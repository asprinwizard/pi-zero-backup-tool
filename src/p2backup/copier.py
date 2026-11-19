"""Copy a card into its session folder: one read of the card, hashed while
copying, then a verify that re-reads the backup from the SD card. Plan §6.3.

Guarantees:
- the card is only ever opened for reading;
- a file appears in CONTENTS/ only once complete (temp file, fsync, rename);
- an existing backup file is never overwritten: if it differs it is moved to
  _p2backup/replaced/<run>/ first;
- a file is listed in manifest.xxh64 only after its backup was read back and
  matched the hash taken while reading the card;
- cancel, a lost card or a full disk stop cleanly between chunks; everything
  already verified stays verified, and the next run carries on from there.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import xxhash

from .fsutil import atomic_write, fsync_dir
from .p2card import card_files

log = logging.getLogger("p2backup.copier")

CHUNK = 4 * 1024 * 1024
MTIME_SLACK_NS = 2_000_000_000          # FAT keeps mtimes to 2 s
SPACE_MARGIN = 64 * 1024 * 1024
META = "_p2backup"
MANIFEST = "manifest.xxh64"

# Result.status values
VERIFIED, INCOMPLETE, CANCELLED, CARD_LOST, DISK_FULL, FAILED = (
    "verified", "incomplete", "cancelled", "card_lost", "disk_full", "failed")


class SourceError(Exception):
    """Reading the card failed (camera unplugged or switched off)."""


class DiskFull(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Item:
    rel: str
    src: Path
    size: int
    mtime_ns: int
    action: str             # "skip" | "copy" | "check" (backup exists but isn't known-good)


@dataclass
class Progress:
    current: str
    files_done: int
    files_total: int
    bytes_done: int
    bytes_total: int
    rate: float             # bytes/s
    eta_s: float | None

    @property
    def fraction(self) -> float:
        return self.bytes_done / self.bytes_total if self.bytes_total else 1.0


@dataclass
class Result:
    run_id: str
    status: str = FAILED
    copied: int = 0
    skipped: int = 0
    adopted: int = 0                # backup already there and identical: recorded, not copied
    replaced: int = 0               # old backup files moved to replaced/<run>/
    bytes_copied: int = 0
    seconds: float = 0.0
    failed: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # card files without a verified backup
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED


# ---- manifest (xxhsum format, so `xxhsum -c` works on a PC) ---------------

def load_manifest(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            h, sep, rel = line.partition("  ")
            if sep and len(h) == 16:
                out[rel] = h
    except OSError:
        pass
    return out


def manifest_text(m: dict[str, str]) -> str:
    return "".join(f"{h}  {rel}\n" for rel, h in sorted(m.items()))


def hash_file(path: Path, cancel: threading.Event | None = None) -> str:
    """xxh64 of a file on the data area, read from the SD card rather than the page cache."""
    h = xxhash.xxh64()
    with open(path, "rb") as f:
        fd = f.fileno()
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        while chunk := f.read(CHUNK):
            if cancel is not None and cancel.is_set():
                raise Cancelled
            h.update(chunk)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    return h.hexdigest()


def free_space(path: Path) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


class Copier:
    def __init__(self, card_root: Path, dest: Path, run_id: str,
                 progress: Callable[[Progress], None] | None = None,
                 cancel: threading.Event | None = None, verify: bool = True,
                 free_bytes: Callable[[Path], int] = free_space,
                 clock: Callable[[], float] = time.monotonic, chunk: int = CHUNK):
        self.card_root = Path(card_root)
        self.dest = Path(dest)
        self.run_id = run_id
        self.meta = self.dest / META
        self.tmp_dir = self.meta / "tmp"
        self.replaced_dir = self.meta / "replaced" / run_id
        self.manifest_path = self.meta / MANIFEST
        self.manifest = load_manifest(self.manifest_path)
        self._progress = progress
        self.cancel = cancel or threading.Event()
        self.verify = verify
        self._free = free_bytes
        self._clock = clock
        self._chunk = chunk
        self._last_report = float("-inf")
        self._manifest_dirty = False
        self._log_file = None

    # ---- planning ------------------------------------------------------
    def plan(self) -> list[Item]:
        items = []
        for rel, src in card_files(self.card_root):
            try:
                st = src.stat()
            except OSError as e:
                raise SourceError(f"cannot read the card: {e}") from e
            items.append(Item(rel, src, st.st_size, st.st_mtime_ns, self._action(rel, st)))
        return items

    def _action(self, rel: str, st: os.stat_result) -> str:
        dst = self.dest / rel
        try:
            d = dst.stat()
        except FileNotFoundError:
            return "copy"
        if (rel in self.manifest and d.st_size == st.st_size
                and abs(d.st_mtime_ns - st.st_mtime_ns) <= MTIME_SLACK_NS):
            return "skip"
        return "check"

    # ---- running -------------------------------------------------------
    def run(self) -> Result:
        res = Result(self.run_id)
        start = self._clock()
        try:
            self._open_log()
            self._clean_tmp()
            items = self.plan()
            todo = [i for i in items if i.action != "skip"]
            res.skipped = len(items) - len(todo)
            need = sum(i.size for i in todo)
            if todo and self._free(self.dest) < need + SPACE_MARGIN:
                raise DiskFull(f"needs {need / 1e9:.1f} GB, "
                               f"only {self._free(self.dest) / 1e9:.1f} GB free")
            self._log(f"run {self.run_id}: {len(items)} files, {len(todo)} to copy "
                      f"({need} bytes), {res.skipped} already verified")
            self._copy_all(todo, need, res, start)
            res.missing = [i.rel for i in items if i.rel not in self.manifest]
            res.status = INCOMPLETE if (res.missing or res.failed) else VERIFIED
        except Cancelled:
            res.status, res.message = CANCELLED, "Cancelled"
        except SourceError as e:
            res.status, res.message = CARD_LOST, f"Card disconnected: {e}"
        except DiskFull as e:
            res.status, res.message = DISK_FULL, f"Disk full: {e}"
        except OSError as e:
            if e.errno == errno.ENOSPC:
                res.status, res.message = DISK_FULL, "Disk full: the backup drive is full"
            else:
                res.status, res.message = FAILED, f"Write error: {e}"
            log.exception("copy failed")
        finally:
            self._clean_tmp()
            self._save_manifest(force=True)
            res.seconds = self._clock() - start
            self._log(f"result: {res.status} copied={res.copied} adopted={res.adopted} "
                      f"skipped={res.skipped} replaced={res.replaced} failed={len(res.failed)} "
                      f"missing={len(res.missing)} bytes={res.bytes_copied} "
                      f"seconds={res.seconds:.0f} {res.message}")
            self._close_log()
        return res

    def _copy_all(self, todo: list[Item], total: int, res: Result, start: float) -> None:
        done_bytes = 0
        for n, item in enumerate(todo):
            def report(extra: int, item=item, n=n, base=done_bytes) -> None:
                self._report(item.rel, n, len(todo), base + extra, total, start)
            report(0)
            outcome = self._copy_one(item, report, res)
            if outcome == "bad":
                outcome = self._copy_one(item, report, res)       # one retry
            if outcome == "bad":
                res.failed.append((item.rel, "backup did not match the card after 2 tries"))
                self._log(f"FAILED {item.rel}: verify mismatch twice")
            done_bytes += item.size
            if outcome == "ok":
                res.bytes_copied += item.size
            self._save_manifest()
        self._report("", len(todo), len(todo), total, total, start, force=True)

    def _copy_one(self, item: Item, report: Callable[[int], None], res: Result) -> str:
        """Copy (or adopt) one file. Returns 'ok', 'adopted' or 'bad'."""
        dst = self.dest / item.rel
        tmp = self.tmp_dir / "copy.part"              # one file at a time
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        src_hash = self._copy_to_tmp(item, tmp, report)

        if dst.exists():
            if self._backup_matches(dst, src_hash):
                tmp.unlink()
                self.manifest[item.rel] = src_hash
                self._manifest_dirty = True
                res.adopted += 1
                self._log(f"adopted {item.rel} (already backed up, identical)")
                return "adopted"
            self._move_aside(dst, item.rel)
            res.replaced += 1

        dst.parent.mkdir(parents=True, exist_ok=True)
        self._install(tmp, dst)
        if self.verify and hash_file(dst, self.cancel) != src_hash:
            self._log(f"verify mismatch {item.rel}")
            self._move_aside(dst, item.rel, suffix=".bad")
            return "bad"
        self.manifest[item.rel] = src_hash
        self._manifest_dirty = True
        res.copied += 1
        self._log(f"copied {item.rel} {src_hash}")
        return "ok"

    def _copy_to_tmp(self, item: Item, tmp: Path, report: Callable[[int], None]) -> str:
        h = xxhash.xxh64()
        done = 0
        try:
            src = open(item.src, "rb")
        except OSError as e:
            raise SourceError(str(e)) from e
        try:
            with open(tmp, "wb") as out:
                while True:
                    if self.cancel.is_set():
                        raise Cancelled
                    try:
                        chunk = src.read(self._chunk)
                    except OSError as e:
                        raise SourceError(str(e)) from e
                    if not chunk:
                        break
                    h.update(chunk)
                    self._write(out, chunk)
                    done += len(chunk)
                    report(done)
                out.flush()
                os.fsync(out.fileno())
            os.utime(tmp, ns=(item.mtime_ns, item.mtime_ns))
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise DiskFull("the backup drive is full") from e
            raise
        finally:
            src.close()
        if done != item.size:
            raise SourceError(f"{item.rel} changed size while reading")
        return h.hexdigest()

    # small steps, separate so tests can inject faults
    def _write(self, out, chunk: bytes) -> None:
        out.write(chunk)

    def _install(self, tmp: Path, dst: Path) -> None:
        os.replace(tmp, dst)
        fsync_dir(dst.parent)

    def _backup_matches(self, dst: Path, src_hash: str) -> bool:
        try:
            return hash_file(dst, self.cancel) == src_hash
        except OSError:
            return False

    def _move_aside(self, dst: Path, rel: str, suffix: str = "") -> None:
        target = self.replaced_dir / (rel + suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        n = 2
        while target.exists():
            target = target.with_name(f"{Path(rel).name}{suffix}.{n}")
            n += 1
        os.replace(dst, target)
        fsync_dir(dst.parent)
        self._log(f"moved old {rel} to {target.relative_to(self.dest)}")

    def _clean_tmp(self) -> None:
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _save_manifest(self, force: bool = False) -> None:
        if not self._manifest_dirty:
            return
        try:
            atomic_write(self.manifest_path, manifest_text(self.manifest).encode())
            self._manifest_dirty = False
        except OSError as e:
            if force:
                log.error("could not save manifest: %s", e)
            else:
                raise

    # ---- progress and log ------------------------------------------------
    def _report(self, current: str, files_done: int, files_total: int, done: int, total: int,
                start: float, force: bool = False) -> None:
        if not self._progress:
            return
        now = self._clock()
        if not force and now - self._last_report < 0.5:
            return
        self._last_report = now
        elapsed = now - start
        rate = done / elapsed if elapsed > 0 else 0.0
        eta = (total - done) / rate if rate > 0 else None
        self._progress(Progress(current, files_done, files_total, done, total, rate, eta))

    def _open_log(self) -> None:
        runs = self.meta / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        self._log_file = open(runs / f"{self.run_id}.log", "a", buffering=1)

    def _log(self, line: str) -> None:
        log.info(line)
        if self._log_file:
            self._log_file.write(time.strftime("%H:%M:%S ") + line + "\n")

    def _close_log(self) -> None:
        if self._log_file:
            self._log_file.flush()
            os.fsync(self._log_file.fileno())
            self._log_file.close()
            self._log_file = None

"""One backup run: card -> session folder, with the run recorded in
session.json, the run log and P2BACKUP/backups.csv. Plus a command line for
testing on the Pi before the on-screen flow exists (Phase 4):

    python -m p2backup.backup list
    python -m p2backup.backup run --slot 1 [--card P2-03 | --card new | --card unsorted]
    python -m p2backup.backup run --source DIR --serial 1A2B-3C4D --data DIR   (no Pi needed)
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import io
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .copier import Copier, Progress, Result, free_space
from .fsutil import fsync_dir
from .identity import Library, Session
from .p2card import CardError, CardInfo, CardMounter, find_usb_disks, inspect
from .storage import MOUNT_POINT

CSV_NAME = "backups.csv"
CSV_FIELDS = ["run", "finished", "card", "folder", "slot", "volume_serial", "status",
              "copied", "adopted", "skipped", "replaced", "failed", "missing", "bytes", "seconds"]
LOCK = "/run/p2backup/backup.lock"


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def run_backup(info: CardInfo, library: Library, session: Session, *, verify: bool = True,
               progress: Callable[[Progress], None] | None = None,
               cancel: threading.Event | None = None,
               free_bytes: Callable[[Path], int] = free_space,
               run_id: str | None = None, copier_cls=Copier) -> Result:
    run_id = run_id or new_run_id()
    runs = session.data.setdefault("runs", [])
    for r in runs:
        if r.get("status") == "running":
            r["status"] = "interrupted"          # the app or the Pi stopped mid-run
    runs.append({"run": run_id, "status": "running", "slot": info.slot,
                 "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    library.record_card(session, info)           # also saves

    res = copier_cls(info.root, session.path, run_id, progress=progress, cancel=cancel,
                     verify=verify, free_bytes=free_bytes).run()

    runs[-1].update(status=res.status, finished=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    copied=res.copied, adopted=res.adopted, skipped=res.skipped,
                    replaced=res.replaced, failed=len(res.failed), missing=len(res.missing),
                    bytes=res.bytes_copied, seconds=round(res.seconds))
    try:
        session.save()
        append_csv(library.root / CSV_NAME, {
            **{k: runs[-1].get(k, "") for k in CSV_FIELDS}, "run": run_id,
            "finished": runs[-1]["finished"], "card": session.card or "UNSORTED",
            "folder": session.path.relative_to(library.root).as_posix(),
            "volume_serial": info.volume_serial})
    except OSError as e:
        res.message = (res.message + f"; could not record the run: {e}").lstrip("; ")
    return res


def append_csv(path: Path, row: dict) -> None:
    new = not path.exists()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\r\n")   # Excel-friendly
    if new:
        w.writeheader()
    w.writerow(row)
    with open(path, "a", newline="") as f:
        f.write(buf.getvalue())
        f.flush()
        os.fsync(f.fileno())
    if new:
        fsync_dir(path.parent)


def summary(res: Result) -> list[str]:
    """Plan §6.4 wording."""
    if res.ok:
        return ["Verified ✓, 1 copy on this device.", "Card can be formatted",
                "(copy it to a PC first to have 2 copies)"]
    lines = [f"NOT complete: {res.message or res.status}",
             "Do NOT format this card."]
    lines += [f"  failed: {rel} ({why})" for rel, why in res.failed[:10]]
    lines += [f"  not backed up: {rel}" for rel in res.missing[:10]]
    if len(res.missing) > 10:
        lines.append(f"  ... and {len(res.missing) - 10} more")
    return lines


# ---- command line ----------------------------------------------------------

def _fmt_progress(p: Progress) -> str:
    eta = "--:--" if p.eta_s is None else f"{int(p.eta_s // 60):02d}:{int(p.eta_s % 60):02d}"
    return (f"\r{p.fraction * 100:5.1f}%  {p.bytes_done / 1e9:6.2f}/{p.bytes_total / 1e9:.2f} GB"
            f"  {p.rate / 1e6:5.1f} MB/s  ETA {eta}  {p.current[-40:]:<40}")


def _resolve(library: Library, info: CardInfo, choice: str | None) -> Session | None:
    s = library.match(info)
    if s:
        print(f"Known card: {s.path.relative_to(library.root)}")
        return s
    if choice is None:
        recent = ", ".join(library.recent_cards()[:5]) or "none yet"
        last = library.last_card_in_slot(info.slot)
        print("This card hasn't been backed up before, or it was reformatted.\n"
              f"Say which card it is with --card. Recent cards: {recent}"
              f"{f' (last in slot {info.slot}: {last})' if last else ''}.\n"
              f"--card new = {library.next_card()}, --card unsorted = UNSORTED/")
        return None
    card = {"new": library.next_card(), "unsorted": None}.get(choice.lower(), choice.upper())
    return library.create(info, card)


def _to_copy(info: CardInfo, s: Session | None) -> str:
    if not s:
        return "new"
    items = Copier(info.root, s.path, "plan").plan()
    todo = [i for i in items if i.action != "skip"]
    return (f"{s.path.relative_to(s.path.parent.parent)}, {len(todo)} of {len(items)} files "
            f"({sum(i.size for i in todo) / 1e9:.2f} GB) to copy")


def cmd_list(args, library: Library) -> int:
    disks = find_usb_disks()
    if not disks:
        print("No USB disks. Is the camera on, in USB mode, with the OTG cable?")
        return 1
    mounter = CardMounter()
    for d in disks:
        if not d.has_media:
            print(f"slot {d.slot}: {d.label or d.disk}: no card")
            continue
        try:
            info = mounter.mount(d)
        except CardError as e:
            print(f"slot {d.slot}: {e}")
            continue
        try:
            if not info.is_p2:
                print(f"slot {d.slot}: {d.label}: not a P2 card")
                continue
            print(f"slot {d.slot}: {d.label}, {d.size_bytes / 1e9:.1f} GB, serial "
                  f"{info.volume_serial or '?'}, {len(info.clips)} clips, "
                  f"first {info.first_date or '?'} -> {_to_copy(info, library.match(info))}")
        finally:
            mounter.unmount(d.slot)
    return 0


def cmd_run(args, library: Library) -> int:
    mounter = None
    if args.source:
        info = inspect(Path(args.source), args.slot, args.serial.upper())
    else:
        disk = next((d for d in find_usb_disks() if d.slot == args.slot), None)
        if not disk or not disk.has_media:
            print(f"No card in slot {args.slot}.")
            return 1
        mounter = CardMounter()
        try:
            info = mounter.mount(disk)
        except CardError as e:
            print(e)
            return 1
    try:
        if not info.is_p2:
            print("Not a P2 card (no CONTENTS folder with clips).")
            return 1
        session = _resolve(library, info, args.card)
        if not session:
            return 2
        cancel = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: cancel.set())
        print(f"Backing up slot {info.slot} ({len(info.clips)} clips) to "
              f"{session.path.relative_to(library.root)}  (Ctrl+C cancels cleanly)")
        res = run_backup(info, library, session, verify=not args.no_verify, cancel=cancel,
                         progress=lambda p: print(_fmt_progress(p), end="", flush=True))
        print()
        print(f"{res.copied} copied, {res.adopted} already there, {res.skipped} skipped, "
              f"{res.replaced} old files moved aside, {res.bytes_copied / 1e9:.2f} GB "
              f"in {res.seconds:.0f} s")
        print("\n".join(summary(res)))
        return 0 if res.ok else 1
    finally:
        if mounter:
            mounter.unmount(info.slot)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m p2backup.backup")
    p.add_argument("--data", default=MOUNT_POINT, help=f"backup drive (default {MOUNT_POINT})")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show cards in the camera and what a backup would do")
    r = sub.add_parser("run", help="back up one card")
    r.add_argument("--slot", type=int, default=1)
    r.add_argument("--card", help="for a new card: P2-nn, 'new' or 'unsorted'")
    r.add_argument("--source", help="back up a folder laid out like a card (testing)")
    r.add_argument("--serial", default="0000-0000", help="volume serial to use with --source")
    r.add_argument("--no-verify", action="store_true")
    args = p.parse_args(argv)

    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    data = Path(args.data)
    if args.data == MOUNT_POINT and not os.path.ismount(data):
        print(f"The backup drive is not mounted at {data} (is a PC connected?).")
        return 1
    lock_path = Path(LOCK if args.data == MOUNT_POINT else data / ".backup.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another backup is already running.")
            return 1
        library = Library(data)
        library.scan()
        return cmd_list(args, library) if args.cmd == "list" else cmd_run(args, library)


if __name__ == "__main__":
    sys.exit(main())

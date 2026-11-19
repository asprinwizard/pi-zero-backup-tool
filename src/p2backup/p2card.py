"""P2 cards on the USB host port: find them, mount them read-only, read what's on them.

Plan §6.1. The HVX200 is one SCSI target with one LUN per slot (test 0.1):
LUN 0 = slot 1, LUN 1 = slot 2. An empty slot is a 0-byte disk. A USB stick
shows up as LUN 0, so it is "slot 1".

Cards are never written to: `blockdev --setro` on the disk and partition,
mounted `ro`, never fsck'd. Only CONTENTS/ and LASTCLIP.TXT are part of the
card; anything else at the root (a PC's "System Volume Information", sync
tool databases) is ignored.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("p2backup.p2card")

MOUNT_BASE = "/run/p2backup/cards"
MOUNT_OPTS = "ro,noatime,nodev,nosuid,noexec"
LASTCLIP = "LASTCLIP.TXT"
CONTENTS = "CONTENTS"
CLIP_DIR = "CLIP"

# Copied in this order: essence first, then the clip XML that "publishes" a
# clip to P2 software, then LASTCLIP.TXT.
ESSENCE_DIRS = ("VIDEO", "AUDIO", "ICON", "PROXY", "VOICE")

JUNK_NAMES = {".Trashes", ".fseventsd", ".Spotlight-V100", ".DS_Store", ".TemporaryItems",
              "Thumbs.db", "desktop.ini", "System Volume Information", "$RECYCLE.BIN",
              "sync.ffs_db", "sync.ffs_lock"}


def is_junk(name: str) -> bool:
    return name in JUNK_NAMES or name.startswith("._")


# ---- what's on a card (pure filesystem; tested on folders) ---------------

@dataclass(frozen=True)
class Clip:
    name: str               # "0001AB"
    umid: str | None        # GlobalClipID
    date: str | None        # "2026-11-14", camera clock


@dataclass
class CardInfo:
    slot: int
    root: Path                          # mount point (or any folder laid out like a card)
    volume_serial: str                  # "1A2B-3C4D"; "" if unknown
    is_p2: bool
    lastclip: str = ""
    clips: list[Clip] = field(default_factory=list)
    device: str = ""                    # /dev/sda1
    label: str = ""                     # model, e.g. "AG-HVX200 #1"
    size_bytes: int = 0

    @property
    def umids(self) -> set[str]:
        return {c.umid for c in self.clips if c.umid}

    @property
    def first_date(self) -> str | None:
        dates = sorted(c.date for c in self.clips if c.date)
        return dates[0] if dates else None


def find_ci(parent: Path, name: str) -> Path | None:
    """parent/name, matching the name case-insensitively (cards are FAT)."""
    p = parent / name
    if p.exists():
        return p
    try:
        for entry in os.scandir(parent):
            if entry.name.upper() == name.upper():
                return parent / entry.name
    except OSError:
        pass
    return None


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_clip_xml(path: Path) -> Clip:
    """GlobalClipID and recording date from a P2 clip XML; tolerant of missing bits."""
    umid = date = None
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as e:
        log.warning("cannot read clip XML %s: %s", path.name, e)
        return Clip(path.stem, None, None)
    found: dict[str, str] = {}
    for el in tree.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("GlobalClipID", "StartDate", "CreationDate") and el.text and tag not in found:
            found[tag] = el.text.strip()
    umid = found.get("GlobalClipID") or None
    for key in ("StartDate", "CreationDate"):
        m = _DATE.match(found.get(key, ""))
        if m:
            date = m.group(0)
            break
    return Clip(path.stem, umid, date)


def inspect(root: Path, slot: int = 1, volume_serial: str = "") -> CardInfo:
    """Read a mounted card (or a folder laid out like one)."""
    contents = find_ci(root, CONTENTS)
    lastclip = find_ci(root, LASTCLIP)
    clip_dir = find_ci(contents, CLIP_DIR) if contents else None
    xmls = sorted(p for p in clip_dir.iterdir()
                  if p.suffix.upper() == ".XML" and not is_junk(p.name)) if clip_dir else []
    is_p2 = bool(contents and contents.is_dir() and (lastclip or xmls))
    info = CardInfo(slot=slot, root=root, volume_serial=volume_serial, is_p2=is_p2)
    if not is_p2:
        return info
    if lastclip:
        try:
            info.lastclip = lastclip.read_text(errors="replace").strip()
        except OSError:
            pass
    info.clips = [parse_clip_xml(p) for p in xmls]
    return info


def lastclip_counter(text: str) -> int | None:
    """The clip counter in LASTCLIP.TXT (first 4 hex digits of the clip name), if readable."""
    first = text.split()[0] if text.split() else ""
    try:
        return int(first[:4], 16) if len(first) >= 4 else None
    except ValueError:
        return None


def card_files(root: Path) -> Iterator[tuple[str, Path]]:
    """(relative path with '/' separators, absolute path) of every file to back up,
    in copy order: essence, then other CONTENTS files, then CLIP/*.XML, then LASTCLIP.TXT."""
    contents = find_ci(root, CONTENTS)
    if not contents:
        return
    groups: dict[int, list[tuple[str, Path]]] = {0: [], 1: [], 2: []}
    for dirpath, dirnames, filenames in os.walk(contents):
        dirnames[:] = sorted(d for d in dirnames if not is_junk(d))
        rel_dir = Path(dirpath).relative_to(root)
        top = rel_dir.parts[1].upper() if len(rel_dir.parts) > 1 else ""
        group = 0 if top in ESSENCE_DIRS else 2 if top == CLIP_DIR else 1
        for name in sorted(filenames):
            if is_junk(name):
                continue
            groups[group].append(((rel_dir / name).as_posix(), Path(dirpath) / name))
    for g in (0, 1, 2):
        yield from groups[g]
    lastclip = find_ci(root, LASTCLIP)
    if lastclip and lastclip.is_file():
        yield lastclip.name, lastclip


# ---- block devices (Pi) --------------------------------------------------

@dataclass(frozen=True)
class UsbDisk:
    disk: str           # "sda"
    slot: int           # LUN + 1
    size_bytes: int
    label: str          # vendor/model as the device reports it
    partition: str      # "/dev/sda1", or "/dev/sda" if not partitioned

    @property
    def has_media(self) -> bool:
        return self.size_bytes > 0


def find_usb_disks(sys_root: Path = Path("/")) -> list[UsbDisk]:
    """SCSI disks on USB, by slot. Empty slots are included with size 0."""
    disks = []
    for blk in sorted((sys_root / "sys/block").glob("sd*")):
        try:
            dev = (blk / "device").resolve()
            if "/usb" not in str(dev):
                continue
            lun = int(dev.name.rsplit(":", 1)[-1])
            size = int((blk / "size").read_text()) * 512
            model = (blk / "device/model").read_text().strip() if (blk / "device/model").exists() \
                else ""
        except (OSError, ValueError):
            continue
        parts = sorted(p.name for p in blk.glob(f"{blk.name}[0-9]*"))
        part = f"/dev/{parts[0]}" if parts else f"/dev/{blk.name}"
        disks.append(UsbDisk(blk.name, lun + 1, size, model, part))
    return disks


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True)


class CardError(Exception):
    pass


class CardMounter:
    """Mounts card partitions read-only under /run/p2backup/cards/slotN."""

    def __init__(self, run: Runner = _run, base: str = MOUNT_BASE):
        self._run = run
        self.base = Path(base)

    def volume_serial(self, device: str) -> str:
        res = self._run(["blkid", "-o", "value", "-s", "UUID", device])
        return (res.stdout or "").strip().upper()

    def mount(self, disk: UsbDisk) -> CardInfo:
        if not disk.has_media:
            raise CardError(f"slot {disk.slot}: no card")
        mp = self.base / f"slot{disk.slot}"
        if os.path.ismount(mp):
            self.unmount(disk.slot)
        for dev in {f"/dev/{disk.disk}", disk.partition}:
            if self._run(["blockdev", "--setro", dev]).returncode != 0:
                raise CardError(f"could not make {dev} read-only")
        mp.mkdir(parents=True, exist_ok=True)
        res = self._run(["mount", "-o", MOUNT_OPTS, disk.partition, str(mp)])
        if res.returncode != 0:
            raise CardError(f"could not mount slot {disk.slot}: {(res.stderr or '').strip()}")
        info = inspect(mp, disk.slot, self.volume_serial(disk.partition))
        info.device, info.label, info.size_bytes = disk.partition, disk.label, disk.size_bytes
        log.info("slot %d mounted read-only: %s, serial %s, %d clips%s", disk.slot, disk.partition,
                 info.volume_serial or "?", len(info.clips), "" if info.is_p2 else " (not P2)")
        return info

    def unmount(self, slot: int) -> None:
        mp = self.base / f"slot{slot}"
        if os.path.ismount(mp):
            self._run(["umount", str(mp)])

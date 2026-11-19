"""Build folders laid out like P2 cards (as the HVX200 writes them).

Used by the tests and by --sim (fake cards in the camera)."""

from __future__ import annotations

import os
from pathlib import Path

XML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<P2Main xmlns="urn:schemas-Professional-Plug-in:P2:ClipMetadata:v3.1">
  <ClipContent>
    <ClipName>{name}</ClipName>
    <GlobalClipID>{umid}</GlobalClipID>
    <ClipMetadata>
      <Access><CreationDate>{date}T10:23:45+00:00</CreationDate></Access>
      <Shoot><StartDate>{date}T10:23:40+00:00</StartDate></Shoot>
    </ClipMetadata>
  </ClipContent>
</P2Main>
"""

MTIME = 1_763_000_000          # fixed source mtime, a whole number of seconds (FAT-like)


def umid(n: int) -> str:
    return f"060A2B340101010501010D4313000000{n:032X}"


def add_clip(root: Path, n: int, date: str = "2026-11-14", size: int = 50_000) -> str:
    """Add clip number n; returns its name, e.g. '0001AB'."""
    name = f"{n:04X}AB"
    c = root / "CONTENTS"
    files = {
        c / "VIDEO" / f"{name}.MXF": os.urandom(size),
        c / "AUDIO" / f"{name}00.MXF": os.urandom(size // 4),
        c / "AUDIO" / f"{name}01.MXF": os.urandom(size // 4),
        c / "ICON" / f"{name}.BMP": os.urandom(1000),
        c / "PROXY" / f"{name}.MP4": os.urandom(size // 10),
        c / "CLIP" / f"{name}.XML": XML.format(name=name, umid=umid(n), date=date).encode(),
    }
    for p, data in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        os.utime(p, (MTIME, MTIME))
    (c / "VOICE").mkdir(exist_ok=True)
    set_lastclip(root, name)
    return name


_lastclip_writes = [0]


def set_lastclip(root: Path, text: str) -> None:
    """The camera rewrites LASTCLIP.TXT after each clip, so its mtime moves on."""
    _lastclip_writes[0] += 1
    t = MTIME + 10 * _lastclip_writes[0]
    p = root / "LASTCLIP.TXT"
    p.write_text(f"{text}\n1.0\n")
    os.utime(p, (t, t))


def make_card(root: Path, clips: range | list[int] = range(1, 4), date: str = "2026-11-14",
              junk: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "CONTENTS").mkdir(exist_ok=True)
    for n in clips:
        add_clip(root, n, date)
    if junk:
        (root / "System Volume Information").mkdir(exist_ok=True)
        (root / "System Volume Information" / "IndexerVolumeGuid").write_text("x")
        (root / "sync.ffs_db").write_bytes(b"junk")
        (root / "CONTENTS" / "VIDEO" / "._0001AB.MXF").write_bytes(b"mac junk")
    return root


def wipe(root: Path) -> None:
    """Like formatting the card in the camera."""
    import shutil
    shutil.rmtree(root)
    root.mkdir()
    (root / "CONTENTS").mkdir()
    set_lastclip(root, "")


def card_rel_files(root: Path) -> set[str]:
    """Files a backup should contain (no junk)."""
    out = {p.relative_to(root).as_posix() for p in (root / "CONTENTS").rglob("*")
           if p.is_file() and not p.name.startswith("._")}
    if (root / "LASTCLIP.TXT").exists():
        out.add("LASTCLIP.TXT")
    return out

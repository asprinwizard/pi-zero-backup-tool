"""Small crash-safe file helpers for the data area (exFAT on the SD card)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass                    # some filesystems refuse fsync on a directory
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file in the same folder, fsync, rename, fsync the folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def atomic_write_json(path: Path, obj) -> None:
    atomic_write(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode())


def unique_path(path: Path) -> Path:
    """path, or path_2, path_3... whichever doesn't exist yet."""
    if not path.exists():
        return path
    n = 2
    while True:
        p = path.with_name(f"{path.name}_{n}")
        if not p.exists():
            return p
        n += 1

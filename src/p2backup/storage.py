"""The data partition (p3) on the Pi side: loop device, check, mount, flush.

p3 holds its own MBR with one exFAT partition, so on the Pi it is reached
through a partitioned loop device (losetup -P). It must never be mounted
while the PC has it; the Connection controller enforces the order.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("p2backup.storage")

DATA_DEV = "/dev/mmcblk0p3"
MOUNT_POINT = "/run/p2backup/data"
MOUNT_OPTS = "noatime,nodev,nosuid,noexec"

# fsck.exfat exit codes (same scheme as e2fsck)
FSCK_OK, FSCK_FIXED, FSCK_FIXED_REBOOT = 0, 1, 2

# While a PC has the drive, write its data to the card within ~1 s, so an
# unplug without eject loses as little as possible.
PC_WRITEBACK = {"dirty_expire_centisecs": "100", "dirty_writeback_centisecs": "100"}

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True)


class StorageError(Exception):
    """Something the user needs to see (shown on the screen)."""


@dataclass
class MountResult:
    repaired: bool = False


class Storage:
    def __init__(self, run: Runner = run_cmd, data_dev: str = DATA_DEV,
                 mount_point: str = MOUNT_POINT, proc_vm: Path = Path("/proc/sys/vm")):
        self._run = run
        self.data_dev = data_dev
        self.mount_point = mount_point
        self._proc_vm = proc_vm
        self._saved_writeback: dict[str, str] | None = None

    # ---- helpers -----------------------------------------------------
    def _check(self, cmd: list[str], what: str) -> subprocess.CompletedProcess:
        res = self._run(cmd)
        if res.returncode != 0:
            detail = (res.stderr or res.stdout or "").strip().splitlines()
            log.error("%s failed (%s): %s", what, res.returncode, detail[-1:] or "")
            raise StorageError(what)
        return res

    def loop_device(self) -> str | None:
        """The loop device already attached to the data partition, if any."""
        res = self._run(["losetup", "-j", self.data_dev])
        for line in (res.stdout or "").splitlines():
            if ":" in line:
                return line.split(":", 1)[0].strip()
        return None

    def is_mounted(self) -> bool:
        return os.path.ismount(self.mount_point)

    # ---- mount / unmount ---------------------------------------------
    def mount(self) -> MountResult:
        """Check and mount the data area. Safe to call when already mounted."""
        if self.is_mounted():
            return MountResult()
        loop = self.loop_device()
        if not loop:
            loop = self._check(["losetup", "-P", "--show", "-f", self.data_dev],
                               "attach loop device").stdout.strip()
        part = f"{loop}p1"
        if not _wait_for(part):
            raise StorageError("data partition not found")

        # Always check first: after a PC session or a power cut the volume may be dirty.
        fsck = self._run(["fsck.exfat", "-p", part])
        if fsck.returncode not in (FSCK_OK, FSCK_FIXED, FSCK_FIXED_REBOOT):
            log.error("fsck.exfat: %s", (fsck.stdout or fsck.stderr or "").strip()[-500:])
            raise StorageError("drive needs repair")
        repaired = fsck.returncode != FSCK_OK
        if repaired:
            log.warning("fsck.exfat repaired the data area")

        Path(self.mount_point).mkdir(parents=True, exist_ok=True)
        self._check(["mount", "-t", "exfat", "-o", MOUNT_OPTS, part, self.mount_point],
                    "mount data area")
        log.info("data area mounted at %s", self.mount_point)
        return MountResult(repaired=repaired)

    def unmount(self) -> None:
        """Flush, unmount and detach the loop device, so p3 can go to the PC."""
        if self.is_mounted():
            os.sync()
            self._check(["umount", self.mount_point], "release the drive")
            log.info("data area unmounted")
        loop = self.loop_device()
        if loop:
            self._check(["losetup", "-d", loop], "detach loop device")

    def flush(self) -> None:
        """Push everything (including what the PC wrote through the gadget) to the card."""
        os.sync()
        self._run(["blockdev", "--flushbufs", self.data_dev])

    def free_bytes(self) -> int | None:
        if not self.is_mounted():
            return None
        st = os.statvfs(self.mount_point)
        return st.f_bavail * st.f_frsize

    # ---- write-back tuning while the PC has the drive ----------------
    def pc_writeback(self, on: bool) -> None:
        try:
            if on and self._saved_writeback is None:
                self._saved_writeback = {k: (self._proc_vm / k).read_text().strip()
                                         for k in PC_WRITEBACK}
                for k, v in PC_WRITEBACK.items():
                    (self._proc_vm / k).write_text(v)
            elif not on and self._saved_writeback is not None:
                for k, v in self._saved_writeback.items():
                    (self._proc_vm / k).write_text(v)
                self._saved_writeback = None
        except OSError as e:                # not fatal: only affects how fast data lands
            log.warning("could not change write-back settings: %s", e)


def _wait_for(path: str, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


class FakeStorage:
    """--sim and tests."""

    def __init__(self, free: int = 187_200_000_000):
        self.mounted = False
        self.fail_mount: str | None = None
        self.fail_unmount: str | None = None
        self.calls: list[str] = []
        self._free = free
        self.data_dev = DATA_DEV

    def is_mounted(self) -> bool:
        return self.mounted

    def mount(self) -> MountResult:
        self.calls.append("mount")
        if self.fail_mount:
            raise StorageError(self.fail_mount)
        self.mounted = True
        return MountResult()

    def unmount(self) -> None:
        self.calls.append("unmount")
        if self.fail_unmount:
            raise StorageError(self.fail_unmount)
        self.mounted = False

    def flush(self) -> None:
        self.calls.append("flush")

    def free_bytes(self) -> int | None:
        return self._free if self.mounted else None

    def pc_writeback(self, on: bool) -> None:
        self.calls.append(f"writeback:{'on' if on else 'off'}")

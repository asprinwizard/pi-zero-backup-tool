"""Partition stage 2: clean up the initramfs hook, grow root to fill p2, format the data area.

The data partition (p3) gets its own MBR with one exFAT partition, so
Windows and macOS see an ordinary USB stick when p3 is exported.
An existing exFAT data area is never reformatted.
"""

import time

from .. import partition as P
from ..common import FILES_DIR, Ctx, InstallError, State, cmdline_remove, cmdline_value
from .s30_partition_stage1 import CMDLINE, HOOK, SCRIPT, TOKEN

NAME = "s31"
DESCRIPTION = "Partition stage 2: format data area"

LABEL = "P2BACKUP"
SHRINK_LOG = "/run/initramfs/p2b-shrink.log"


def _attach_loop(ctx: Ctx) -> str:
    return ctx.output(["losetup", "-P", "--show", "-f", P.DATA_DEV])


def _detach_loop(ctx: Ctx, loop: str) -> None:
    ctx.run(["losetup", "-d", loop], check=False)


def _wait_for(ctx: Ctx, dev: str, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if ctx.path(dev).exists():
            return True
        time.sleep(0.2)
    return False


def run(ctx: Ctx, state: State) -> None:
    # Keep the initramfs log, if this boot ran the shrink.
    log = ctx.path(SHRINK_LOG)
    if log.exists():
        ctx.write("/var/log/p2backup-shrink.log", log.read_text())
        for line in log.read_text().splitlines():
            ctx.log(line)

    disk = P.read_disk(ctx)
    if not disk.has_p3:
        raise InstallError("data partition was not created; see /var/log/p2backup-shrink.log "
                           "(or run 'journalctl -b' for initramfs messages)")

    # Remove the one-time hook so future kernel updates don't carry it.
    cmd = ctx.read(CMDLINE)
    if TOKEN in cmd.split():
        ctx.write(CMDLINE, cmdline_remove(cmd, TOKEN))
    removed = ctx.remove(HOOK) | ctx.remove(SCRIPT)
    if removed:
        ctx.run(["update-initramfs", "-u", "-k", "all"])

    # Root PARTUUID must still match cmdline.txt.
    want = cmdline_value(ctx.read(CMDLINE), "root")
    have = ctx.output(["blkid", "-s", "PARTUUID", "-o", "value", P.ROOT_DEV], check=False)
    if want and want.startswith("PARTUUID=") and have and want != f"PARTUUID={have}":
        raise InstallError(f"root PARTUUID mismatch: cmdline has {want}, disk has {have}")

    # Grow the filesystem to fill the (now 16 GiB) partition. Safe online.
    ctx.run(["resize2fs", P.ROOT_DEV])

    loop = _attach_loop(ctx) if not ctx.dry_run else "/dev/loopX"
    try:
        part = f"{loop}p1"
        fstype = ""
        if ctx.path(part).exists():
            fstype = ctx.output(["blkid", "-s", "TYPE", "-o", "value", part], check=False)
        if fstype == "exfat":
            ctx.log(f"data area already formatted as exFAT ({part}); leaving it alone")
            return

        if not state.get("p3_created_by_installer"):
            if not ctx.options.get("force_format"):
                raise InstallError(f"{P.DATA_DEV} exists but was not created by this installer; "
                                   "refusing to format it (use --force-format to override)")

        ctx.log("creating partition table and exFAT filesystem in the data area")
        ctx.run(["wipefs", "-a", loop])
        ctx.run(["sfdisk", loop], input="label: dos\n2048,,7\n")
        _detach_loop(ctx, loop)
        loop = _attach_loop(ctx) if not ctx.dry_run else loop
        part = f"{loop}p1"
        if not ctx.dry_run and not _wait_for(ctx, part):
            raise InstallError(f"{part} did not appear after partitioning")
        ctx.run(["mkfs.exfat", "-L", LABEL, part])

        mnt = "/run/p2backup-install-mnt"
        if not ctx.dry_run:
            ctx.path(mnt).mkdir(parents=True, exist_ok=True)
        ctx.run(["mount", "-t", "exfat", part, mnt])
        try:
            ctx.install_file(FILES_DIR / "data/README.txt", f"{mnt}/README.txt")
            if not ctx.dry_run:
                ctx.path(f"{mnt}/_p2backup").mkdir(exist_ok=True)
        finally:
            ctx.run(["umount", mnt], check=False)
    finally:
        if not ctx.dry_run:
            _detach_loop(ctx, loop)

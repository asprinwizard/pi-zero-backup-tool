"""Partition stage 1: prepare the one-time initramfs shrink of root, then reboot.

Root cannot be shrunk while mounted, so a local-premount initramfs script
does it on the next boot (see install/files/initramfs/premount-p2b-shrink).

If root was never expanded (p2 is already small enough), p3 is created
online instead and no reboot is needed for partitioning.
"""

from .. import partition as P
from ..common import (
    FILES_DIR,
    Ctx,
    InstallError,
    RebootRequired,
    State,
    cmdline_add,
    cmdline_tokens,
    config_has,
)

NAME = "s30"
DESCRIPTION = "Partition stage 1: plan root shrink and data partition"

CMDLINE = "/boot/firmware/cmdline.txt"
CONFIG = "/boot/firmware/config.txt"
HOOK = "/etc/initramfs-tools/hooks/p2b-shrink"
SCRIPT = "/etc/initramfs-tools/scripts/local-premount/p2b-shrink"
TOKEN = "p2bshrink"


def preflight(ctx: Ctx) -> P.Disk:
    disk = P.read_disk(ctx)
    if disk.has_p3:
        return disk

    live = str(ctx.root) == "/"         # sandbox runs (--root) can't query the live system
    root_src = ctx.output(["findmnt", "-n", "-o", "SOURCE", "/"], check=False) if live else ""
    if root_src and root_src != P.ROOT_DEV:
        raise InstallError(f"root is {root_src}, expected {P.ROOT_DEV}")

    cmdline = ctx.read(CMDLINE)
    if "resize" in cmdline_tokens(cmdline):
        raise InstallError("first-boot root expansion has not run yet ('resize' still in "
                           "cmdline.txt). Reboot once, then run the installer again.")

    used_kib = (ctx.output(["df", "--output=used", "-k", "/"], check=False).splitlines()
                if live else [])
    if len(used_kib) >= 2 and int(used_kib[-1]) // 1024 > P.MAX_ROOT_USED_MIB:
        raise InstallError(f"root uses more than {P.MAX_ROOT_USED_MIB} MiB; cannot shrink "
                           f"to {P.ROOT_PART_MIB} MiB safely")

    cfg = ctx.read(CONFIG)
    if config_has(cfg, "auto_initramfs=0"):
        raise InstallError("auto_initramfs=0 in config.txt; the shrink needs the initramfs")
    if not any(ctx.path("/boot/firmware").glob("initramfs*")):
        raise InstallError("no initramfs found in /boot/firmware")
    return disk


def run(ctx: Ctx, state: State) -> None:
    disk = preflight(ctx)
    if disk.has_p3:
        ctx.log("data partition already exists; skipping")
        return

    layout = P.plan_layout(disk.p2_start, disk.disk_sectors)
    root_mib = disk.p2_sectors // P.MIB
    data_gib = layout.p3_mib / 1024
    ctx.log(f"root partition: {root_mib} MiB -> {P.ROOT_PART_MIB} MiB")
    ctx.log(f"data partition: {data_gib:.1f} GiB (p3 at sector {layout.p3_start})")

    if disk.p2_sectors <= layout.p2_sectors:
        # Root was never expanded: create p3 online, no shrink needed.
        ctx.log("root was never expanded; growing it and creating the data partition now")
        if not ctx.confirm(f"Grow root to {P.ROOT_PART_MIB // 1024} GiB and create a "
                           f"{data_gib:.0f} GiB data partition?"):
            raise InstallError("cancelled by user")
        # Growing a mounted partition into free space is safe; --force skips the
        # "disk in use" refusal. sfdisk keeps the disk identifier (PARTUUID).
        if disk.p2_sectors < layout.p2_sectors:
            ctx.run(["sfdisk", "--force", "--no-reread", "--no-tell-kernel", "-N", "2",
                     P.DISK_DEV], input=f"{layout.p2_start},{layout.p2_sectors}\n")
            ctx.run(["resizepart", P.DISK_DEV, "2", str(layout.p2_sectors)])
        ctx.run(["sfdisk", "--force", "--no-reread", "--no-tell-kernel", "--append",
                 P.DISK_DEV], input=f"{layout.p3_start},{layout.p3_sectors},7\n")
        ctx.run(["partx", "-a", "-n", "3", P.DISK_DEV])
        state.set("p3_created_by_installer", True)
        return

    if not ctx.confirm(f"Shrink root to {P.ROOT_PART_MIB // 1024} GiB and create a "
                       f"{data_gib:.0f} GiB data partition on the next boot?"):
        raise InstallError("cancelled by user")

    ctx.install_file(FILES_DIR / "initramfs/hook-p2b-shrink", HOOK, 0o755)
    script = P.render_shrink_script(
        (FILES_DIR / "initramfs/premount-p2b-shrink").read_text(), layout)
    ctx.write(SCRIPT, script, 0o755)
    ctx.run(["update-initramfs", "-u", "-k", "all"])

    cmd = ctx.read(CMDLINE)
    ctx.write(CMDLINE, cmdline_add(cmd, TOKEN))

    state.set("p3_created_by_installer", True)
    state.set("expected_layout", layout.__dict__)
    raise RebootRequired("rebooting to shrink root and create the data partition "
                         "(the screen may stay blank for a few minutes)")

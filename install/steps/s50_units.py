"""Install systemd units, the gadget script and the shutdown splash hook.

Also loads the SPI driver from the initramfs, so /dev/spidev0.0 already
exists when systemd starts and the boot splash can draw straight away
instead of waiting for udev.
"""

from ..common import FILES_DIR, Ctx, State, managed_block

NAME = "s50"
DESCRIPTION = "Install services, helper scripts and early splash support"

INITRAMFS_MODULES = "/etc/initramfs-tools/modules"
EARLY_MODULES = ["# OLED boot splash: SPI ready before systemd starts", "spi_bcm2835", "spidev"]

UNITS = ["p2backup-splash.service", "p2backup-gadget.service", "p2backup.service"]


def run(ctx: Ctx, state: State) -> None:
    ctx.install_file(FILES_DIR / "sbin/p2backup-gadget", "/usr/local/sbin/p2backup-gadget", 0o755)
    ctx.install_file(FILES_DIR / "sbin/p2backup-shutdown-splash",
                     "/usr/lib/systemd/system-shutdown/p2backup-splash", 0o755)
    for unit in UNITS:
        ctx.install_file(FILES_DIR / "systemd" / unit, f"/etc/systemd/system/{unit}")
    ctx.run(["systemctl", "daemon-reload"])

    mods = ctx.read(INITRAMFS_MODULES, default="")
    new = managed_block(mods, EARLY_MODULES, all_section=False)
    if new != mods:
        ctx.write(INITRAMFS_MODULES, new, 0o644)
        ctx.run(["update-initramfs", "-u", "-k", "all"])
        state.set("reboot_needed", True)

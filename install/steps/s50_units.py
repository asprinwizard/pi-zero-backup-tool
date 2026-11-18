"""Install systemd units, the gadget script and the shutdown splash hook.

Also makes the boot splash appear sooner:
- the SPI driver is loaded as soon as systemd starts (and from the
  initramfs too, should it be used);
- the firmware skips the initramfs and its 1 s boot delay. The initramfs is
  only needed for the one-time root shrink, and s30 removes this block
  again if it ever has to shrink.
"""

from ..common import FILES_DIR, Ctx, State, managed_block

NAME = "s50"
DESCRIPTION = "Install services, helper scripts and early splash support"

INITRAMFS_MODULES = "/etc/initramfs-tools/modules"
EARLY_MODULES = ["# OLED boot splash: SPI ready before systemd starts", "spi_bcm2835", "spidev"]
MODULES_LOAD = "/etc/modules-load.d/p2backup-spi.conf"
MODULES_LOAD_TEXT = "# OLED boot splash (installed by p2backup)\nspi_bcm2835\nspidev\n"

CONFIG = "/boot/firmware/config.txt"
FASTBOOT_MARKER = "p2backup-fastboot"
FASTBOOT_LINES = ["# Faster boot: no initramfs, no boot delay (s30 removes this to shrink)",
                  "auto_initramfs=0", "boot_delay=0"]


def fastboot_config(text: str) -> str:
    """config.txt with our fast-boot block, and without bare copies of its
    settings (e.g. added by hand while testing). Pure, unit tested."""
    bare = {line for line in FASTBOOT_LINES if not line.startswith("#")}
    kept, in_block = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# BEGIN "):
            in_block = True
        elif s.startswith("# END "):
            in_block = False
        if not in_block and s in bare:
            continue
        kept.append(line)
    return managed_block("\n".join(kept) + "\n", FASTBOOT_LINES, FASTBOOT_MARKER)

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

    ctx.write(MODULES_LOAD, MODULES_LOAD_TEXT, 0o644)
    cfg = ctx.read(CONFIG)
    new_cfg = fastboot_config(cfg)
    if new_cfg != cfg:
        ctx.write(CONFIG, new_cfg)
        state.set("reboot_needed", True)

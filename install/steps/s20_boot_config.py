"""/boot/firmware/config.txt and cmdline.txt: USB OTG, SPI (OLED), I2C (UPS)."""

from ..common import Ctx, State, cmdline_add, managed_block

NAME = "s20"
DESCRIPTION = "Configure boot files (dwc2 OTG, SPI, I2C)"

CONFIG = "/boot/firmware/config.txt"
CMDLINE = "/boot/firmware/cmdline.txt"

CONFIG_LINES = [
    "# USB OTG: role follows the ID pin (camera = host, PC = gadget)",
    "dtoverlay=dwc2",
    "# Waveshare 1.3in OLED HAT (SPI) and UPS HAT (C) battery monitor (I2C)",
    "dtparam=spi=on",
    "dtparam=i2c_arm=on",
    "# OLED HAT keys + joystick are active-low: pull-ups from boot (Waveshare recommendation)",
    "gpio=6,19,5,26,13,21,20,16=pu",
    "# Keep the OLED reset line high so the last image survives a halt (test 0.11)",
    "gpio=25=op,dh",
]


def run(ctx: Ctx, state: State) -> None:
    cfg = ctx.read(CONFIG)
    new_cfg = managed_block(cfg, CONFIG_LINES)
    if new_cfg != cfg:
        ctx.write(CONFIG, new_cfg)
        state.set("reboot_needed", True)

    cmd = ctx.read(CMDLINE)
    new_cmd = cmdline_add(cmd, "modules-load=dwc2", after="rootwait")
    if new_cmd != cmd:
        ctx.write(CMDLINE, new_cmd)
        state.set("reboot_needed", True)

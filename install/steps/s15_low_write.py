"""Reduce writes to the root filesystem (every PC session can end in a hard power cut)."""

from ..common import Ctx, State, fstab_add_option

NAME = "s15"
DESCRIPTION = "Reduce root filesystem writes"

JOURNALD_DROPIN = """\
# Installed by p2backup: keep the journal in RAM.
[Journal]
Storage=volatile
RuntimeMaxUse=16M
"""

DISABLE_SERVICES = ["triggerhappy.service", "triggerhappy.socket", "avahi-daemon.service",
                    "avahi-daemon.socket", "ModemManager.service", "bluetooth.service",
                    "hciuart.service"]


def run(ctx: Ctx, state: State) -> None:
    ctx.write("/etc/systemd/journald.conf.d/p2backup.conf", JOURNALD_DROPIN, 0o644)

    fstab = ctx.read("/etc/fstab")
    new = fstab_add_option(fstab, "/", "noatime")
    if new != fstab:
        ctx.write("/etc/fstab", new)

    for unit in DISABLE_SERVICES:
        exists = ctx.run(["systemctl", "cat", unit], check=False, capture=True,
                         mutating=False).returncode == 0
        if exists:
            ctx.run(["systemctl", "disable", "--now", unit], check=False)

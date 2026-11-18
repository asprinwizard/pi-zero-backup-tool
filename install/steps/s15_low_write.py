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

DISABLE_SERVICES = ["triggerhappy.service", "triggerhappy.socket", "ModemManager.service",
                    "bluetooth.service", "hciuart.service"]
# Keep mDNS: without it <hostname>.local stops resolving and SSH by name fails.
# (An earlier version disabled it; this turns it back on.)
KEEP_ENABLED = ["avahi-daemon.socket", "avahi-daemon.service"]
# cloud-init (Raspberry Pi Imager settings) only matters on the first boot; it
# costs ~5 s on every later boot. The marker file switches it off.
CLOUD_INIT_DISABLED = "/etc/cloud/cloud-init.disabled"
CLOUD_INIT_TEXT = "Disabled by p2backup: first-boot setup is done. Delete this file to re-enable.\n"


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

    if ctx.path("/etc/cloud").is_dir():
        ctx.write(CLOUD_INIT_DISABLED, CLOUD_INIT_TEXT, 0o644)

    for unit in KEEP_ENABLED:
        enabled = ctx.run(["systemctl", "is-enabled", unit], check=False, capture=True,
                          mutating=False)
        if enabled.stdout.strip() == "disabled":
            ctx.run(["systemctl", "enable", "--now", unit], check=False)

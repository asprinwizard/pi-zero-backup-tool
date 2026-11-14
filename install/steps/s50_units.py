"""Install systemd units, the gadget script and the shutdown splash hook."""

from ..common import FILES_DIR, Ctx, State

NAME = "s50"
DESCRIPTION = "Install services and helper scripts"

UNITS = ["p2backup-splash.service", "p2backup-gadget.service", "p2backup.service"]


def run(ctx: Ctx, state: State) -> None:
    ctx.install_file(FILES_DIR / "sbin/p2backup-gadget", "/usr/local/sbin/p2backup-gadget", 0o755)
    ctx.install_file(FILES_DIR / "sbin/p2backup-shutdown-splash",
                     "/usr/lib/systemd/system-shutdown/p2backup-splash", 0o755)
    for unit in UNITS:
        ctx.install_file(FILES_DIR / "systemd" / unit, f"/etc/systemd/system/{unit}")
    ctx.run(["systemctl", "daemon-reload"])

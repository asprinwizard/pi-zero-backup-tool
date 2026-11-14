"""Optional (Phase 6): read-only root via overlayfs. Run with: install.py --only s90

The data partition is not affected. To update the app afterwards, disable
the overlay first (sudo raspi-config nonint disable_overlayfs; reboot).
"""

from ..common import Ctx, State

NAME = "s90"
DESCRIPTION = "Read-only root filesystem (overlayfs)"
OPT_IN = True


def run(ctx: Ctx, state: State) -> None:
    if not ctx.confirm("Make the root filesystem read-only (overlayfs)?"):
        return
    ctx.run(["raspi-config", "nonint", "enable_overlayfs"])
    state.set("reboot_needed", True)

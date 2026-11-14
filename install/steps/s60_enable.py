"""Enable the services. The installer finishes with a reboot (handled by install.py)."""

from ..common import Ctx, State
from .s50_units import UNITS

NAME = "s60"
DESCRIPTION = "Enable services"


def run(ctx: Ctx, state: State) -> None:
    ctx.run(["systemctl", "enable", *UNITS])
    state.set("reboot_needed", True)

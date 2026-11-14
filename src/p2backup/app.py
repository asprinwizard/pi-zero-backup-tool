"""Main application loop.

Phase 1: shows a placeholder screen and logs every key press, to prove the
display, inputs and service wiring. The state machine (plan §5.2) replaces
this in Phase 4.
"""

from __future__ import annotations

import logging
import queue

from . import __version__
from .config import Config
from .ui import screens
from .ui.inputs import InputEvent

log = logging.getLogger("p2backup")


class App:
    def __init__(self, config: Config, display, events: queue.Queue):
        self.config = config
        self.display = display
        self.events = events
        self.last_input: str | None = None

    def render(self) -> None:
        self.display.show(screens.home_phase1(self.last_input, __version__))

    def handle(self, ev: InputEvent) -> None:
        self.last_input = f"{ev.key.value} ({ev.kind.value})"
        log.info("input: %s", self.last_input)

    def run(self) -> None:
        self.render()
        while True:
            ev = self.events.get()
            if ev is None:              # quit (sim only)
                break
            if isinstance(ev, InputEvent):
                self.handle(ev)
            self.render()

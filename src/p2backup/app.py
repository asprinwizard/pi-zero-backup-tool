"""Main application loop.

Phase 1: shows a placeholder screen and logs every key press, to prove the
display, inputs and service wiring. The state machine (plan §5.2) replaces
this in Phase 4.
"""

from __future__ import annotations

import logging
import queue
import socket

from . import __version__
from .config import Config
from .ui import screens
from .ui.inputs import InputEvent

log = logging.getLogger("p2backup")

REFRESH_S = 5.0


def local_ip() -> str | None:
    """The address the Pi would use to reach the network, or None if offline.
    A UDP connect only picks a route; nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))      # TEST-NET address, never contacted
            return s.getsockname()[0]
    except OSError:
        return None


class App:
    def __init__(self, config: Config, display, events: queue.Queue):
        self.config = config
        self.display = display
        self.events = events
        self.last_input: str | None = None
        self.ip: str | None = None

    def render(self) -> None:
        self.display.show(screens.home_phase1(self.last_input, __version__, self.ip))

    def handle(self, ev: InputEvent) -> None:
        self.last_input = f"{ev.key.value} ({ev.kind.value})"
        log.info("input: %s", self.last_input)

    def run(self) -> None:
        self.ip = local_ip()
        self.render()
        while True:
            try:
                ev = self.events.get(timeout=REFRESH_S)
            except queue.Empty:
                ip = local_ip()
                if ip != self.ip:       # e.g. Wi-Fi came up after the app started
                    self.ip = ip
                    self.render()
                continue
            if ev is None:              # quit (sim only)
                break
            if isinstance(ev, InputEvent):
                self.handle(ev)
            self.render()

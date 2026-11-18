"""Main application loop.

Phase 1: a placeholder home screen that shows the IP address and the last
key press, plus the power menu (bottom key held 2 s: Shut down / Reboot).
The full state machine (plan §5.2) replaces this in Phase 4.
"""

from __future__ import annotations

import logging
import queue
import socket
import subprocess
import time
from collections.abc import Callable

from . import __version__
from .config import Config
from .ui import screens
from .ui.inputs import InputEvent, Key, Kind

log = logging.getLogger("p2backup")

REFRESH_S = 5.0          # how often the IP address is re-checked
MENU_TIMEOUT_S = 15.0    # an untouched menu or question closes by itself

POWER_ITEMS = ["Shut down", "Reboot", "Cancel"]
POWER_ACTIONS = {"Shut down": "poweroff", "Reboot": "reboot"}
POWER_QUESTIONS = {"poweroff": "Shut down now?", "reboot": "Reboot now?"}
POWER_MESSAGES = {"poweroff": ["Shutting down..."], "reboot": ["Restarting..."]}


def local_ip() -> str | None:
    """The address the Pi would use to reach the network, or None if offline.
    A UDP connect only picks a route; nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))      # TEST-NET address, never contacted
            return s.getsockname()[0]
    except OSError:
        return None


def systemctl_power(action: str) -> bool:
    """Ask systemd to power off or reboot. True if the request was accepted."""
    return subprocess.run(["systemctl", action], check=False).returncode == 0


class App:
    def __init__(self, config: Config, display, events: queue.Queue,
                 power: Callable[[str], bool] = systemctl_power,
                 ip: Callable[[], str | None] = local_ip,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.display = display
        self.events = events
        self.power = power
        self.get_ip = ip
        self.clock = clock
        self.last_input: str | None = None
        self.ip: str | None = None
        self.mode = "home"              # home | menu | confirm | busy
        self.selected = 0
        self.action: str | None = None
        self.yes = False
        self._last_activity = clock()
        self._last_ip_check = float("-inf")

    # ---- drawing -----------------------------------------------------
    def render(self) -> None:
        if self.mode == "menu":
            img = screens.menu("Power", POWER_ITEMS, self.selected)
        elif self.mode == "confirm":
            img = screens.confirm(POWER_QUESTIONS[self.action], self.yes)
        elif self.mode == "busy":
            img = screens.message(POWER_MESSAGES[self.action])
        else:
            img = screens.home_phase1(self.last_input, __version__, self.ip)
        self.display.show(img)

    # ---- input -------------------------------------------------------
    def handle(self, ev: InputEvent) -> None:
        self.last_input = f"{ev.key.value} ({ev.kind.value})"
        log.info("input: %s", self.last_input)
        self._last_activity = self.clock()
        if self.mode == "busy":
            return                      # shutting down: ignore everything
        if self.mode == "menu":
            self._menu_input(ev)
        elif self.mode == "confirm":
            self._confirm_input(ev)
        elif ev.key is Key.KEY3 and ev.kind is Kind.LONG:
            self.mode, self.selected = "menu", 0

    def _menu_input(self, ev: InputEvent) -> None:
        if ev.key is Key.UP:
            self.selected = (self.selected - 1) % len(POWER_ITEMS)
        elif ev.key is Key.DOWN:
            self.selected = (self.selected + 1) % len(POWER_ITEMS)
        elif ev.key is Key.PRESS:
            action = POWER_ACTIONS.get(POWER_ITEMS[self.selected])
            if action:
                self.mode, self.action, self.yes = "confirm", action, False
            else:
                self.mode = "home"
        elif not (ev.key is Key.KEY3 and ev.kind is Kind.LONG):
            self.mode = "home"          # any other key cancels

    def _confirm_input(self, ev: InputEvent) -> None:
        if ev.key in (Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN):
            self.yes = not self.yes
        elif ev.key is Key.PRESS and self.yes:
            self.mode = "busy"
            log.info("power: %s", self.action)
            self.render()               # show the message before the system starts stopping
            if not self.power(self.action):
                log.error("power: systemctl %s failed", self.action)
                self.mode = "home"
        else:
            self.mode = "home"          # "No", or any other key

    # ---- periodic ----------------------------------------------------
    def tick(self) -> bool:
        """Housekeeping between key presses. Returns True if the screen changed."""
        now = self.clock()
        changed = False
        if self.mode in ("menu", "confirm") and now - self._last_activity >= MENU_TIMEOUT_S:
            self.mode = "home"
            changed = True
        if now - self._last_ip_check >= REFRESH_S:
            self._last_ip_check = now
            ip = self.get_ip()
            if ip != self.ip:           # e.g. Wi-Fi came up after the app started
                self.ip = ip
                changed = True
        return changed

    def run(self) -> None:
        self.tick()
        self.render()
        while True:
            try:
                ev = self.events.get(timeout=1.0)
            except queue.Empty:
                if self.tick():
                    self.render()
                continue
            if ev is None:              # quit (sim only)
                break
            if isinstance(ev, InputEvent):
                self.handle(ev)
            self.tick()
            self.render()

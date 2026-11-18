"""Main application loop.

Phase 2: the device switches between camera, PC and "nothing connected",
hands the data area to a PC and back, shows "Safe to unplug" after an
eject, and warns when the UPS is on in PC mode. Bottom key held 2 s opens
the power menu (Shut down / Reboot), except while a PC has the drive.
Card backup arrives in Phase 3; the full UI state machine in Phase 4.
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
from .connection import Connection
from .power import UpsEvent
from .ui import screens
from .ui.inputs import InputEvent, Key, Kind
from .usb import UsbEvent

log = logging.getLogger("p2backup")

REFRESH_S = 5.0          # how often the IP address and free space are re-read
MENU_TIMEOUT_S = 15.0    # an untouched menu or question closes by itself
NOTICE_S = 3.0           # how long a short notice ("Eject on PC first") stays

POWER_ITEMS = ["Shut down", "Reboot", "Cancel"]
# "Shut down" is `halt`, not `poweroff`: on a Pi, poweroff ends with a watchdog
# reset that returns GPIO25 (OLED reset) to its pull-down and blanks the screen.
# halt stops the CPUs with the pins untouched, so "Safe to switch off" stays.
POWER_ACTIONS = {"Shut down": "halt", "Reboot": "reboot"}
POWER_QUESTIONS = {"halt": "Shut down now?", "reboot": "Reboot now?"}
POWER_MESSAGES = {"halt": ["Shutting down..."], "reboot": ["Restarting..."]}


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
    """Ask systemd to halt or reboot. True if the request was accepted."""
    return subprocess.run(["systemctl", action], check=False).returncode == 0


def gb(n: int | None) -> str:
    return "Free: -" if n is None else f"Free: {n / 1e9:.1f} GB"


class App:
    def __init__(self, config: Config, display, events: queue.Queue, usb, gadget, storage,
                 power: Callable[[str], bool] = systemctl_power,
                 ip: Callable[[], str | None] = local_ip,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.display = display
        self.events = events
        self.usb = usb
        self.storage = storage
        self.power = power
        self.get_ip = ip
        self.clock = clock
        self.conn = Connection(gadget, storage, busy=self.show_busy, clock=clock)
        self.last_input: str | None = None
        self.ip: str | None = None
        self.free: int | None = None
        self.ups: UpsEvent | None = None
        self.mode = "home"              # home | menu | confirm | busy  (UI layer)
        self.selected = 0
        self.action: str | None = None
        self.yes = False
        self.notice: tuple[str, list[str]] | None = None
        self._notice_until = 0.0
        self._last_activity = clock()
        self._last_refresh = float("-inf")

    # ---- drawing -----------------------------------------------------
    def battery(self) -> str:
        return f"{self.ups.reading.percent}%" if self.ups else ""

    def ups_warning(self) -> bool:
        return (self.conn.mode in ("pc", "pc_ejected") and self.ups is not None
                and self.ups.discharging and self.config.power.pc_power_protection == "none")

    def screen(self):
        if self.mode == "menu":
            return screens.menu("Power", POWER_ITEMS, self.selected)
        if self.mode == "confirm":
            return screens.confirm(POWER_QUESTIONS[self.action], self.yes)
        if self.mode == "busy":
            return screens.message(POWER_MESSAGES[self.action])
        if self.notice:
            return screens.info(*self.notice)
        if self.ups_warning():
            return screens.alert("SWITCH UPS OFF", ["The UPS is powering", "the PC through USB.",
                                                     "Switch it off now."])
        c, bat = self.conn, self.battery()
        if c.mode == "pc":
            return screens.info("PC MODE", ["PC has the drive", "Eject it on the PC,",
                                            "then wait for", '"Safe to unplug"'], bat)
        if c.mode == "pc_ejected":
            return screens.big("PC MODE", "Safe to unplug", bat)
        if c.mode == "error":
            return screens.info("DRIVE PROBLEM", [c.error or "Unknown error",
                                                  "Press a key to retry,", "or connect a PC",
                                                  "to repair it"], bat)
        if c.mode == "camera":
            return screens.info("CAMERA", ["Camera connected", "(card backup comes", " in Phase 3)",
                                           gb(self.free)], bat)
        lines = ["Nothing connected", gb(self.free), f"IP {self.ip}" if self.ip else "No network"]
        if c.repaired:
            lines.append("Drive was repaired")
        return screens.info(f"P2 BACKUP v{__version__}", lines, bat)

    def render(self) -> None:
        self.display.show(self.screen())

    def show_busy(self, text: str) -> None:
        """Called by the connection controller before a slow step."""
        self.display.show(screens.message([text]))

    def show_notice(self, title: str, lines: list[str]) -> None:
        self.notice = (title, lines)
        self._notice_until = self.clock() + NOTICE_S

    # ---- input -------------------------------------------------------
    def handle(self, ev: InputEvent) -> None:
        self.last_input = f"{ev.key.value} ({ev.kind.value})"
        log.info("input: %s", self.last_input)
        self._last_activity = self.clock()
        if self.mode == "busy":
            return                      # shutting down: ignore everything
        if self.notice:
            self.notice = None          # any key dismisses a notice
            return
        if self.mode == "menu":
            self._menu_input(ev)
        elif self.mode == "confirm":
            self._confirm_input(ev)
        elif ev.key is Key.KEY3 and ev.kind is Kind.LONG:
            if self.conn.pc_has_drive:
                self.show_notice("PC MODE", ["The PC has the drive.", "Eject it on the PC",
                                             "first."])
            else:
                self.mode, self.selected = "menu", 0
        elif self.conn.mode == "error":
            self.conn.retry(self.usb.current())
            self.free = self.storage.free_bytes()

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
            if self.conn.pc_has_drive:  # a PC took the drive while the menu was open
                self.mode = "home"
                return
            self.mode = "busy"
            log.info("power: %s", self.action)
            self.render()               # show the message before the system starts stopping
            if not self.power(self.action):
                log.error("power: systemctl %s failed", self.action)
                self.mode = "home"
        else:
            self.mode = "home"          # "No", or any other key

    # ---- other events --------------------------------------------
    def on_usb(self, ev: UsbEvent) -> None:
        if self.conn.update(ev.status):
            log.info("connection: %s", self.conn.mode)
            if self.conn.pc_has_drive and self.mode in ("menu", "confirm"):
                self.mode = "home"      # power menu is not allowed in PC mode
            self.free = self.storage.free_bytes()

    def on_ups(self, ev: UpsEvent) -> None:
        self.ups = ev

    # ---- periodic ----------------------------------------------------
    def tick(self) -> bool:
        """Housekeeping between events. Returns True if the screen may have changed."""
        now = self.clock()
        changed = False
        if self.mode in ("menu", "confirm") and now - self._last_activity >= MENU_TIMEOUT_S:
            self.mode = "home"
            changed = True
        if self.notice and now >= self._notice_until:
            self.notice = None
            changed = True
        if self.conn.mode in ("pc", "pc_ejected"):
            # The PC-gone debounce needs re-checking even without a new USB event.
            if self.conn.update(self.usb.current()):
                changed = True
        if now - self._last_refresh >= REFRESH_S:
            self._last_refresh = now
            ip, free = self.get_ip(), self.storage.free_bytes()
            if (ip, free) != (self.ip, self.free):
                self.ip, self.free = ip, free
                changed = True
        return changed

    def start(self) -> None:
        self.conn.start(self.usb.current())
        log.info("connection: %s", self.conn.mode)
        self.usb.start()
        self.tick()

    def run(self) -> None:
        self.start()
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
            elif isinstance(ev, UsbEvent):
                self.on_usb(ev)
            elif isinstance(ev, UpsEvent):
                self.on_ups(ev)
            self.tick()
            self.render()

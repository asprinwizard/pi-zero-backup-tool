"""Main application: one thread handling key presses and events, drawing the screen.

Plan §5.2 / §7. The connection controller decides who has the data area
(nothing / camera / PC); the card worker (cards.py) does the slow camera-side
work in the background. The UI layer on top:

    home        the screen for the connection mode (idle / card list / PC / error)
    menu        power menu (KEY3 held 2 s)
    confirm     Yes/No question; default No, closes itself after 10 s
    pick        choose from a list ("Which card is this?", "Move to which card?")
    about       version, IP, host name, drive space (joystick press on "About")
    backing_up  progress; any key asks "Cancel backup?"
    result      outcome of the last backup, until a key is pressed
    busy        shutting down / restarting

Blocking rules: no power menu or restart while a backup runs or a PC has the drive.
"""

from __future__ import annotations

import logging
import queue
import socket
import subprocess
import time
from collections.abc import Callable

from . import __version__
from .cards import NEW, BackupDoneEvent, BackupProgressEvent, CardsEvent, WorkerErrorEvent
from .config import Config
from .connection import Connection
from .copier import CANCELLED, CARD_LOST, DISK_FULL, INCOMPLETE
from .power import UpsEvent
from .ui import screens
from .ui.inputs import InputEvent, Key, Kind
from .usb import UsbEvent

log = logging.getLogger("p2backup")

REFRESH_S = 5.0          # how often the IP address and free space are re-read
MENU_TIMEOUT_S = 15.0    # an untouched menu closes by itself
CONFIRM_TIMEOUT_S = 10.0  # an unanswered question counts as "No"
NOTICE_S = 3.0           # how long a short notice ("Eject on PC first") stays
UNSORTED_LABEL = "Unsorted"

POWER_ITEMS = ["Shut down", "Reboot", "Cancel"]
# "Shut down" is `halt`, not `poweroff`: on a Pi, poweroff ends with a watchdog
# reset that returns GPIO25 (OLED reset) to its pull-down and blanks the screen.
# halt stops the CPUs with the pins untouched, so "Safe to switch off" stays.
POWER_ACTIONS = {"Shut down": "halt", "Reboot": "reboot"}
POWER_QUESTIONS = {"halt": "Shut down now?", "reboot": "Reboot now?",
                   "restart": "Restart the app?"}
POWER_MESSAGES = {"halt": ["Shutting down..."], "reboot": ["Restarting..."],
                  "restart": ["Restarting app..."]}


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
    """halt / reboot the Pi, or restart this service. True if systemd accepted it."""
    cmd = ["systemctl", "restart", "p2backup"] if action == "restart" else ["systemctl", action]
    return subprocess.run(cmd, check=False).returncode == 0


def gb(n: int | None) -> str:
    return "Free: -" if n is None else f"Free: {n / 1e9:.1f} GB"


def result_lines(ev: BackupDoneEvent) -> tuple[str, list[str]]:
    """Plan §6.4 wording, fitted to the screen."""
    r = ev.result
    if r.ok:
        return "BACKED UP", ["Verified OK.", "1 copy on this device.", "Card can be formatted",
                             "(copy to a PC too)"]
    why = {CANCELLED: "Cancelled", CARD_LOST: "Card disconnected", DISK_FULL: "Backup drive full",
           INCOMPLETE: f"{len(r.failed) + len(r.missing)} files not copied"}.get(
               r.status, r.message[:21] or "Error")
    return "NOT DONE", [why, "Do NOT format", "this card.", "Press a key"]


class NoCards:
    """Card worker stand-in when the camera side isn't available (old tests)."""
    running_backup = False

    def activate(self): pass
    def deactivate(self): pass
    def backup(self, slot, choice=None): pass
    def reassign(self, slot, card): pass
    def cancel_backup(self): pass


class App:
    def __init__(self, config: Config, display, events: queue.Queue, usb, gadget, storage,
                 power: Callable[[str], bool] = systemctl_power,
                 ip: Callable[[], str | None] = local_ip,
                 clock: Callable[[], float] = time.monotonic, cards=None):
        self.config = config
        self.display = display
        self.events = events
        self.usb = usb
        self.storage = storage
        self.power = power
        self.get_ip = ip
        self.clock = clock
        self.cards = cards or NoCards()
        self.conn = Connection(gadget, storage, busy=self.show_busy, clock=clock)
        self.last_input: str | None = None
        self.ip: str | None = None
        self.free: int | None = None
        self.ups: UpsEvent | None = None
        self.mode = "home"
        self.selected = 0                   # power menu row
        self.action: str | None = None      # power action being confirmed / run
        self.yes = False
        self.notice: tuple[str, list[str]] | None = None
        self._notice_until = 0.0
        self._last_activity = clock()
        self._last_refresh = float("-inf")
        # confirm
        self.question = ""
        self.detail = ""
        self._on_yes: Callable[[], None] | None = None
        self._confirm_back = "home"
        # pick
        self.pick_title = ""
        self.pick_items: list[tuple[str, object]] = []
        self.pick_sel = 0
        self._on_pick: Callable[[object], None] | None = None
        self._pick_deadline: float | None = None
        self._pick_default: object = None
        # camera side
        self.card_views: CardsEvent = CardsEvent(())
        self.card_sel = 0
        self.backup_card = ""
        self.backup_slot = 0
        self.progress = None
        self.cancelling = False
        self.result: BackupDoneEvent | None = None
        self._cards_active = False

    # ---- drawing -----------------------------------------------------
    def battery(self) -> screens.Status:
        """Header icons (the name is kept from when it was only the battery %)."""
        return screens.Status(wifi=self.ip is not None,
                              battery=self.ups.reading.percent if self.ups else None)

    def about_lines(self) -> list[str]:
        free = "-" if self.free is None else f"{self.free / 1e9:.1f} GB free"
        return [f"Version {__version__}", f"IP {self.ip}" if self.ip else "No network",
                f"Name {socket.gethostname()}"[:21], f"Drive: {free}"]

    def ups_warning(self) -> bool:
        return (self.conn.mode in ("pc", "pc_ejected") and self.ups is not None
                and self.ups.discharging and self.config.power.pc_power_protection == "none")

    def screen(self):
        bat = self.battery()
        if self.mode == "menu":
            return screens.menu("Power", POWER_ITEMS, self.selected)
        if self.mode == "confirm":
            return screens.confirm(self.question, self.yes, self.detail)
        if self.mode == "busy":
            return screens.message(POWER_MESSAGES[self.action])
        if self.mode == "about":
            return screens.about(self.about_lines())
        if self.notice:
            return screens.info(*self.notice)
        if self.ups_warning():
            return screens.alert("SWITCH UPS OFF", ["The UPS is powering", "the PC through USB.",
                                                     "Switch it off now."])
        if self.mode == "pick":
            left = ""
            if self._pick_deadline is not None:
                left = f"{max(0, int(self._pick_deadline - self.clock()))}s"
            return screens.menu(self.pick_title, [t for t, _ in self.pick_items],
                                self.pick_sel, left)
        if self.mode == "backing_up":
            return screens.backing_up(self.backup_card, self.progress, bat, self.cancelling)
        if self.mode == "result" and self.result:
            title, lines = result_lines(self.result)
            return screens.info(f"{title} S{self.result.slot}", lines, bat)
        c = self.conn
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
            return screens.card_list(list(self.card_views.cards), self.card_sel, gb(self.free),
                                     bat)
        lines = ["Nothing connected"] + (["Drive was repaired"] if c.repaired else [])
        return screens.idle(lines, gb(self.free), bat)

    def render(self) -> None:
        self.display.show(self.screen())

    def show_busy(self, text: str) -> None:
        """Called by the connection controller before a slow step."""
        self.display.show(screens.message([text]))

    def show_notice(self, title: str, lines: list[str]) -> None:
        self.notice = (title, lines)
        self._notice_until = self.clock() + NOTICE_S

    # ---- dialogs -------------------------------------------------------
    def ask(self, question: str, on_yes: Callable[[], None], detail: str = "") -> None:
        self._confirm_back = self.mode if self.mode in ("backing_up",) else "home"
        self.mode, self.question, self.detail, self.yes = "confirm", question, detail, False
        self._on_yes = on_yes

    def pick(self, title: str, items: list[tuple[str, object]], on_pick, start: int = 0,
             timeout: float | None = None, default: object = None) -> None:
        self.mode, self.pick_title, self.pick_items = "pick", title, items
        self.pick_sel = start
        self._on_pick = on_pick
        self._pick_deadline = self.clock() + timeout if timeout else None
        self._pick_default = default

    # ---- input -------------------------------------------------------
    @property
    def blocked_reason(self) -> list[str] | None:
        """Why the power menu / restart isn't allowed right now, if it isn't."""
        if self.cards.running_backup:
            return ["A backup is running.", "Cancel it first."]
        if self.conn.pc_has_drive:
            return ["The PC has the drive.", "Eject it on the PC", "first."]
        return None

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
        elif self.mode == "pick":
            self._pick_input(ev)
        elif self.mode == "backing_up":
            if not self.cancelling:
                self.ask("Cancel backup?", self._cancel_backup,
                         "Copied files are kept")
        elif self.mode in ("result", "about"):
            self.mode = "home"
        elif ev.key is Key.KEY3:
            self._key3(ev)
        elif self.conn.mode == "error":
            self.conn.retry(self.usb.current())
            self.free = self.storage.free_bytes()
        elif self.conn.mode == "camera":
            self._card_list_input(ev)
        elif self.conn.mode == "idle" and ev.key is Key.PRESS:
            self.mode = "about"

    def _key3(self, ev: InputEvent) -> None:
        reason = self.blocked_reason
        if reason:
            self.show_notice("NOT NOW", reason)
        elif ev.kind is Kind.LONG:
            self.mode, self.selected = "menu", 0
        else:
            self._ask_power("restart")

    def _ask_power(self, action: str) -> None:
        self.action = action
        self.ask(POWER_QUESTIONS[action], self._do_power)

    def _do_power(self) -> None:
        if self.blocked_reason:     # a PC took the drive while the question was open
            self.mode = "home"
            return
        self.mode = "busy"
        log.info("power: %s", self.action)
        self.render()               # show the message before the system starts stopping
        if not self.power(self.action):
            log.error("power: systemctl %s failed", self.action)
            self.mode = "home"

    def _menu_input(self, ev: InputEvent) -> None:
        if ev.key is Key.UP:
            self.selected = (self.selected - 1) % len(POWER_ITEMS)
        elif ev.key is Key.DOWN:
            self.selected = (self.selected + 1) % len(POWER_ITEMS)
        elif ev.key is Key.PRESS:
            action = POWER_ACTIONS.get(POWER_ITEMS[self.selected])
            self.mode = "home"
            if action:
                self._ask_power(action)
        elif not (ev.key is Key.KEY3 and ev.kind is Kind.LONG):
            self.mode = "home"          # any other key cancels

    def _confirm_input(self, ev: InputEvent) -> None:
        if ev.key in (Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN):
            self.yes = not self.yes
            return
        on_yes = self._on_yes if (ev.key is Key.PRESS and self.yes) else None
        self.mode, self._on_yes = self._confirm_back, None
        if on_yes:
            on_yes()

    def _pick_input(self, ev: InputEvent) -> None:
        if ev.key is Key.UP:
            self.pick_sel = (self.pick_sel - 1) % len(self.pick_items)
        elif ev.key is Key.DOWN:
            self.pick_sel = (self.pick_sel + 1) % len(self.pick_items)
        elif ev.key is Key.PRESS:
            self._picked(self.pick_items[self.pick_sel][1])
        elif ev.key is Key.KEY3:
            self.mode = "home"          # back
        # other keys: nothing (an accidental press must not choose a card)

    def _picked(self, value) -> None:
        on_pick, self._on_pick = self._on_pick, None
        self.mode, self._pick_deadline = "home", None
        if on_pick:
            on_pick(value)

    # ---- camera side -------------------------------------------------
    def _card_list_input(self, ev: InputEvent) -> None:
        cards = self.card_views.cards[:2]
        on_card = self.card_sel < len(cards)        # otherwise: About
        if ev.key in (Key.UP, Key.DOWN):
            n = len(cards) + 1
            self.card_sel = (self.card_sel + (1 if ev.key is Key.DOWN else -1)) % n
        elif ev.key is Key.KEY1:
            self.request_backup(1)
        elif ev.key is Key.KEY2:
            self.request_backup(2)
        elif ev.key is Key.PRESS:
            if on_card:
                self.request_backup(cards[self.card_sel].slot)
            else:
                self.mode = "about"
        elif ev.key is Key.RIGHT and on_card:
            self.request_reassign(cards[self.card_sel].slot)

    def view(self, slot: int):
        return next((v for v in self.card_views.cards if v.slot == slot), None)

    def card_choices(self, exclude: str | None = None) -> list[tuple[str, object]]:
        cv = self.card_views
        items: list[tuple[str, object]] = [(c, c) for c in cv.recent if c != exclude]
        items.append((f"New card {cv.next_card}", NEW))
        items.append((UNSORTED_LABEL, None))
        return items

    def request_backup(self, slot: int) -> None:
        v = self.view(slot)
        if not v or not v.usable:
            why = "no card" if not v or not v.present else (
                "not a P2 card" if not v.is_p2 else "can't read it")
            self.show_notice(f"SLOT {slot}", [f"Slot {slot}: {why}"])
            return
        what = "Nothing new" if v.matched and v.files_todo == 0 else (
            f"{screens.gb(v.bytes_todo)}B to copy")
        self.ask(f"Back up slot {slot}?", lambda: self._identify_then_backup(slot), what)

    def _identify_then_backup(self, slot: int) -> None:
        v = self.view(slot)
        if v and v.matched:
            self._start_backup(slot, None, v.card or "UNSORTED")
            return
        items = self.card_choices()
        last = self.card_views.last_in_slot.get(slot)
        start = next((i for i, (_, val) in enumerate(items) if val == last and last), 0)
        timeout = self.config.cards.ask_timeout_s if self.config.cards.ask_card_number else 0
        if not self.config.cards.ask_card_number:
            self._start_backup(slot, None, "UNSORTED")
            return

        def chosen(value):
            label = self.card_views.next_card if value == NEW else (value or "UNSORTED")
            self._start_backup(slot, value, label)
        self.pick(f"S{slot}: which card?", items, chosen, start, timeout=timeout, default=None)

    def _start_backup(self, slot: int, choice, label: str) -> None:
        log.info("backup slot %d -> %s", slot, label)
        self.backup_slot, self.backup_card = slot, label
        self.progress, self.cancelling, self.result = None, False, None
        self.mode = "backing_up"
        self.cards.backup(slot, choice)

    def _cancel_backup(self) -> None:
        self.cancelling = True
        self.cards.cancel_backup()
        self.mode = "backing_up"

    def request_reassign(self, slot: int) -> None:
        v = self.view(slot)
        if not v or not v.matched:
            self.show_notice(f"SLOT {slot}", ["Only a card that has", "been backed up can",
                                              "be moved."])
            return

        def chosen(value):
            label = self.card_views.next_card if value == NEW else (value or UNSORTED_LABEL)
            self.ask(f"Move to {label}?", lambda: self.cards.reassign(slot, value),
                     f"from {v.card or UNSORTED_LABEL}")
        self.pick("Move to which card?", self.card_choices(exclude=v.card), chosen)

    # ---- other events --------------------------------------------
    def on_usb(self, ev: UsbEvent) -> None:
        if self.conn.update(ev.status):
            log.info("connection: %s", self.conn.mode)
            if self.conn.pc_has_drive and self.mode in ("menu", "confirm", "pick"):
                self.mode = "home"      # nothing else is allowed while the PC has the drive
            self.free = self.storage.free_bytes()
            self._sync_cards()

    def _sync_cards(self) -> None:
        want = self.conn.mode == "camera"
        if want and not self._cards_active:
            self.card_views, self.card_sel = CardsEvent(()), 0
            self.cards.activate()
        elif not want and self._cards_active:
            self.cards.deactivate()     # queued after a running backup, which then fails cleanly
            if self.mode in ("pick",):
                self.mode = "home"
        self._cards_active = want

    def on_ups(self, ev: UpsEvent) -> None:
        self.ups = ev

    def on_cards(self, ev) -> None:
        if isinstance(ev, CardsEvent):
            self.card_views = ev
            self.card_sel = min(self.card_sel, len(ev.cards[:2]))    # == len: About
        elif isinstance(ev, BackupProgressEvent):
            self.progress = ev.progress
        elif isinstance(ev, BackupDoneEvent):
            self.result, self.cancelling = ev, False
            self.free = self.storage.free_bytes()
            if self.mode in ("backing_up", "confirm"):
                self.mode = "result"
        elif isinstance(ev, WorkerErrorEvent):
            self.show_notice("PROBLEM", [ev.message[:21], ev.message[21:42]])

    # ---- periodic ----------------------------------------------------
    def tick(self) -> bool:
        """Housekeeping between events. Returns True if the screen may have changed."""
        now = self.clock()
        changed = False
        idle = now - self._last_activity
        if self.mode in ("menu", "about") and idle >= MENU_TIMEOUT_S:
            self.mode, changed = "home", True
        if self.mode == "confirm" and idle >= CONFIRM_TIMEOUT_S:
            self.mode, self._on_yes, changed = self._confirm_back, None, True
        if self.mode == "pick":
            changed = True              # countdown
            if self._pick_deadline is not None and now >= self._pick_deadline:
                self._picked(self._pick_default)
            elif self._pick_deadline is None and idle >= MENU_TIMEOUT_S:
                self.mode = "home"
        if self.notice and now >= self._notice_until:
            self.notice = None
            changed = True
        if self.conn.mode in ("pc", "pc_ejected"):
            # The PC-gone debounce needs re-checking even without a new USB event.
            if self.conn.update(self.usb.current()):
                self._sync_cards()
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
        self._sync_cards()
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
            else:
                self.on_cards(ev)
            self.tick()
            self.render()

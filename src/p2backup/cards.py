"""Background worker for everything slow on the camera side: finding and
mounting cards, matching them to backup folders, planning, backing up and
reassigning. One thread, one job at a time, so the library and the card
mounts are never touched from two places at once. The UI thread only sends
requests and receives events.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .backup import run_backup, summary
from .copier import FAILED, Copier, Progress, Result, free_space
from .identity import UNSORTED, Library, Session
from .p2card import CardError, CardInfo, CardMounter, UsbDisk, card_files, find_usb_disks, inspect

log = logging.getLogger("p2backup.cards")

NEW = "new"                     # card choice: next free P2-nn


@dataclass(frozen=True)
class CardView:
    """What the screen needs to know about one slot."""
    slot: int
    present: bool = False           # a card is in the slot
    is_p2: bool = False
    error: str = ""
    card: str | None = None         # "P2-03"; None = UNSORTED or not matched
    folder: str | None = None       # session folder relative to the drive, if matched
    clips: int = 0
    files_total: int = 0
    files_todo: int = 0
    bytes_total: int = 0
    bytes_todo: int = 0

    @property
    def matched(self) -> bool:
        return self.folder is not None

    @property
    def backed_up(self) -> bool:
        return self.matched and self.files_todo == 0 and self.files_total > 0

    @property
    def usable(self) -> bool:
        return self.present and self.is_p2 and not self.error


@dataclass(frozen=True)
class CardsEvent:
    cards: tuple[CardView, ...]
    recent: tuple[str, ...] = ()            # card numbers, most recent first
    next_card: str = "P2-01"
    last_in_slot: dict = field(default_factory=dict)    # slot -> card last seen there


@dataclass(frozen=True)
class BackupProgressEvent:
    slot: int
    card: str
    progress: Progress


@dataclass(frozen=True)
class BackupDoneEvent:
    slot: int
    card: str
    result: Result
    lines: tuple[str, ...]


@dataclass(frozen=True)
class WorkerErrorEvent:
    message: str


def _card_label(s: Session) -> str:
    return s.card or UNSORTED


class CardWorker:
    def __init__(self, emit: Callable[[object], None], data_root: Path,
                 disks: Callable[[], list[UsbDisk]] = find_usb_disks, mounter=None,
                 ready: Callable[[], bool] = lambda: True, verify: bool = True,
                 interval: float = 2.0, free_bytes: Callable[[Path], int] = free_space):
        self._emit = emit
        self.library = Library(data_root)
        self._disks = disks
        self._mounter = mounter or CardMounter()
        self._ready = ready
        self._verify = verify
        self._interval = interval
        self._free = free_bytes
        self._jobs: queue.Queue = queue.Queue()
        self._infos: dict[int, CardInfo] = {}
        self._sig: tuple | None = None
        self.active = False
        self.cancel = threading.Event()
        self.running_backup = False         # read by the UI thread (a bool: safe enough)

    # ---- requests from the UI thread -----------------------------------
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="card-worker").start()

    def activate(self) -> None:
        self._jobs.put(("activate",))

    def deactivate(self) -> None:
        self._jobs.put(("deactivate",))

    def backup(self, slot: int, choice: str | None = None) -> None:
        """choice for an unknown card: 'P2-nn', NEW, or None for UNSORTED."""
        self.cancel.clear()
        self.running_backup = True
        self._jobs.put(("backup", slot, choice))

    def reassign(self, slot: int, card: str | None) -> None:
        self._jobs.put(("reassign", slot, card))

    def cancel_backup(self) -> None:
        self.cancel.set()

    # ---- worker thread ---------------------------------------------------
    def _loop(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=self._interval)
            except queue.Empty:
                job = ("poll",)
            self.run_job(job)

    def run_job(self, job: tuple) -> None:
        """Run one job (called by the thread; tests call it directly)."""
        kind = job[0]
        try:
            if kind == "activate":
                self.active = True
                self._sig = None
                self._poll()
            elif kind == "deactivate":
                self.active = False
                self._unmount_all()
                self._emit(CardsEvent(()))
            elif kind == "poll" and self.active:
                self._poll()
            elif kind == "backup":
                self._backup(job[1], job[2])
            elif kind == "reassign":
                self._reassign(job[1], job[2])
        except Exception as e:                 # never let the worker thread die
            log.exception("card worker: %s failed", kind)
            if kind == "backup":
                self._finish(job[1], "?", Result("error", status=FAILED, message=str(e)))
            else:
                self._emit(WorkerErrorEvent(str(e)))

    def _poll(self) -> None:
        if not self._ready():
            return
        disks = self._disks()
        sig = tuple((d.disk, d.slot, d.size_bytes) for d in disks)
        if sig == self._sig:
            return
        self._sig = sig
        self._unmount_all()
        views = {}
        for d in disks:
            if not d.has_media:
                views[d.slot] = CardView(d.slot)
                continue
            try:
                info = self._mounter.mount(d)
            except CardError as e:
                views[d.slot] = CardView(d.slot, present=True, error=str(e))
                continue
            self._infos[d.slot] = info
        self.library.scan()
        for slot, info in self._infos.items():
            views[slot] = self._view(info)
        self._emit_cards(views)

    def _emit_cards(self, views: dict[int, CardView] | None = None) -> None:
        if views is None:
            views = {slot: self._view(info) for slot, info in self._infos.items()}
        lib = self.library
        last = {slot: lib.last_card_in_slot(slot) for slot in views}
        self._emit(CardsEvent(tuple(views[s] for s in sorted(views)), tuple(lib.recent_cards()),
                              lib.next_card(), {k: v for k, v in last.items() if v}))

    def _view(self, info: CardInfo) -> CardView:
        if not info.is_p2:
            return CardView(info.slot, present=True, is_p2=False)
        s = self.library.match(info)
        try:
            if s:
                items = Copier(info.root, s.path, "plan").plan()
                todo = [i for i in items if i.action != "skip"]
            else:
                items = [(rel, p.stat().st_size) for rel, p in card_files(info.root)]
                todo = items
        except OSError as e:
            return CardView(info.slot, present=True, is_p2=True, error=f"Cannot read: {e}")
        size = (lambda i: i.size) if s else (lambda i: i[1])
        return CardView(
            info.slot, present=True, is_p2=True, card=s.card if s else None,
            folder=s.path.relative_to(self.library.root).as_posix() if s else None,
            clips=len(info.clips), files_total=len(items), files_todo=len(todo),
            bytes_total=sum(size(i) for i in items), bytes_todo=sum(size(i) for i in todo))

    def _backup(self, slot: int, choice: str | None) -> None:
        info = self._infos.get(slot)
        if not info or not info.is_p2:
            self._finish(slot, "?", Result("none", status=FAILED,
                                           message=f"No P2 card in slot {slot}"))
            return
        if not self._ready():
            self._finish(slot, "?", Result("none", status=FAILED, message="Backup drive not ready"))
            return
        self.library.scan()
        s = self.library.match(info)
        if not s:
            card = self.library.next_card() if choice == NEW else choice
            s = self.library.create(info, card)
        label = _card_label(s)
        res = run_backup(info, self.library, s, verify=self._verify, cancel=self.cancel,
                         free_bytes=self._free,
                         progress=lambda p: self._emit(BackupProgressEvent(slot, label, p)))
        self._finish(slot, label, res)
        self._sig = None                 # re-read the cards: the plan has changed (or a card left)
        self._poll()

    def _finish(self, slot: int, label: str, res: Result) -> None:
        self.running_backup = False
        log.info("backup slot %d (%s): %s %s", slot, label, res.status, res.message)
        self._emit(BackupDoneEvent(slot, label, res, tuple(summary(res))))

    def _reassign(self, slot: int, card: str | None) -> None:
        info = self._infos.get(slot)
        s = self.library.match(info) if info else None
        if not s:
            self._emit(WorkerErrorEvent("Card has no backup yet"))
            return
        self.library.reassign(s, self.library.next_card() if card == NEW else card)
        self._emit_cards()

    def _unmount_all(self) -> None:
        for slot in list(self._infos):
            self._mounter.unmount(slot)
        self._infos.clear()


# ---- --sim: cards are folders ------------------------------------------------

class FolderCards:
    """Stands in for find_usb_disks + CardMounter: slot n holds the folder
    given for it, if that folder exists."""

    def __init__(self, folders: dict[int, Path], serials: dict[int, str]):
        self.folders = folders
        self.serials = serials
        self.changes = 0                # bump after changing a card, so the worker re-reads it

    def disks(self) -> list[UsbDisk]:
        out = []
        for slot, path in sorted(self.folders.items()):
            present = path.is_dir()
            size = 64_000_000_000 + self.changes if present else 0
            out.append(UsbDisk(f"sd{chr(96 + slot)}", slot, size, f"SIM #{slot}", str(path)))
        return out

    def mount(self, disk: UsbDisk) -> CardInfo:
        if not disk.has_media:
            raise CardError(f"slot {disk.slot}: no card")
        info = inspect(self.folders[disk.slot], disk.slot, self.serials.get(disk.slot, ""))
        info.size_bytes = disk.size_bytes
        return info

    def unmount(self, slot: int) -> None:
        pass

"""Card worker (run synchronously) with folder cards, and the app's camera flow."""

import queue

from p2backup.app import App
from p2backup.cards import NEW, BackupDoneEvent, CardsEvent, CardWorker, FolderCards
from p2backup.config import Config
from p2backup.storage import FakeStorage
from p2backup.ui.inputs import InputEvent, Key, Kind
from p2backup.usb import FakeUsb, UsbEvent
from tests.p2fixtures import add_clip, make_card, wipe

PLENTY = lambda p: 10**12        # noqa: E731


def worker(tmp_path, slot2=False):
    events = []
    fc = FolderCards({1: tmp_path / "s1", 2: tmp_path / "s2"}, {1: "1A2B-3C4D", 2: "5E6F-7A8B"})
    make_card(fc.folders[1], clips=[1, 2])
    if slot2:
        make_card(fc.folders[2], clips=[7])
    (tmp_path / "data").mkdir()
    w = CardWorker(events.append, tmp_path / "data", disks=fc.disks, mounter=fc,
                   free_bytes=PLENTY)
    return w, fc, events


def last(events, cls):
    return next(e for e in reversed(events) if isinstance(e, cls))


def test_worker_lists_cards_then_backs_up_and_replans(tmp_path):
    w, fc, events = worker(tmp_path)
    w.run_job(("activate",))
    cards = last(events, CardsEvent).cards
    assert [(c.slot, c.present, c.matched) for c in cards] == [(1, True, False), (2, False, False)]
    assert cards[0].files_todo == cards[0].files_total > 0

    w.backup(1, NEW)
    assert w.running_backup
    w.run_job(("backup", 1, NEW))
    done = last(events, BackupDoneEvent)
    assert done.result.ok and done.card == "P2-01" and not w.running_backup
    c1 = last(events, CardsEvent).cards[0]
    assert c1.matched and c1.backed_up and c1.card == "P2-01"

    add_clip(fc.folders[1], 3)
    fc.changes += 1
    w.run_job(("poll",))
    c1 = last(events, CardsEvent).cards[0]
    assert c1.matched and c1.files_todo == 7


def test_worker_unknown_card_to_unsorted_and_reassign(tmp_path):
    w, fc, events = worker(tmp_path)
    w.run_job(("activate",))
    w.run_job(("backup", 1, None))
    assert last(events, BackupDoneEvent).card == "UNSORTED"
    w.run_job(("reassign", 1, "P2-05"))
    c1 = last(events, CardsEvent).cards[0]
    assert c1.card == "P2-05" and c1.folder.startswith("P2-05/")


def test_worker_reformatted_card_is_new_again(tmp_path):
    w, fc, events = worker(tmp_path)
    w.run_job(("activate",))
    w.run_job(("backup", 1, NEW))
    wipe(fc.folders[1])
    add_clip(fc.folders[1], 40)
    fc.serials[1] = "9999-0000"
    fc.changes += 1
    w.run_job(("poll",))
    ev = last(events, CardsEvent)
    assert not ev.cards[0].matched and ev.recent == ("P2-01",) and ev.next_card == "P2-02"
    assert ev.last_in_slot == {1: "P2-01"}


def test_worker_deactivate_clears(tmp_path):
    w, _, events = worker(tmp_path)
    w.run_job(("activate",))
    w.run_job(("deactivate",))
    assert last(events, CardsEvent).cards == ()


# ---- app flow ---------------------------------------------------------------

class Display:
    def __init__(self):
        self.frames = []

    def show(self, img):
        self.frames.append(img)


class SyncCards(CardWorker):
    """Runs jobs straight away and feeds events back into the app."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.app = None

    def _put(self, job):
        self.run_job(job)

    def activate(self):
        self._put(("activate",))

    def deactivate(self):
        self._put(("deactivate",))

    def backup(self, slot, choice=None):
        self.running_backup = True
        self._put(("backup", slot, choice))

    def reassign(self, slot, card):
        self._put(("reassign", slot, card))


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def app_with_camera(tmp_path, slot2=False):
    holder = {}
    fc = FolderCards({1: tmp_path / "s1", 2: tmp_path / "s2"}, {1: "1A2B-3C4D", 2: "5E6F-7A8B"})
    make_card(fc.folders[1], clips=[1, 2])
    if slot2:
        make_card(fc.folders[2], clips=[7])
    (tmp_path / "data").mkdir()
    cards = SyncCards(lambda ev: holder["app"].on_cards(ev), tmp_path / "data",
                      disks=fc.disks, mounter=fc, free_bytes=PLENTY)
    usb, clock = FakeUsb(), Clock()
    app = App(Config(), Display(), queue.Queue(), usb, usb, FakeStorage(), power=lambda a: True,
              ip=lambda: None, clock=clock, cards=cards)
    holder["app"] = app
    app.start()
    usb.plug_camera()
    app.on_usb(UsbEvent(usb.current()))
    return app, fc, clock


def keys(app, *ks):
    for k in ks:
        app.handle(k if isinstance(k, InputEvent) else InputEvent(k))
        app.render()


def test_new_card_confirm_then_identify_then_backup(tmp_path):
    app, fc, _ = app_with_camera(tmp_path)
    assert app.conn.mode == "camera" and app.view(1).present
    keys(app, Key.KEY1)
    assert app.mode == "confirm" and app.question == "Back up slot 1?"
    keys(app, Key.RIGHT, Key.PRESS)
    assert app.mode == "pick" and app.pick_items[-2][1] == NEW
    keys(app, Key.DOWN, Key.UP)                 # "New card P2-01" is first: no recent cards
    assert app.pick_items[app.pick_sel][1] == NEW
    keys(app, Key.PRESS)
    assert app.mode == "result" and app.result.result.ok and app.result.card == "P2-01"
    keys(app, Key.PRESS)
    assert app.mode == "home" and app.view(1).backed_up


def test_identify_times_out_to_unsorted(tmp_path):
    app, _, clock = app_with_camera(tmp_path)
    keys(app, Key.KEY1, Key.RIGHT, Key.PRESS)
    assert app.mode == "pick"
    clock.t += 61
    app.tick()
    assert app.mode == "result" and app.result.card == "UNSORTED"


def test_known_card_skips_identify(tmp_path):
    app, fc, _ = app_with_camera(tmp_path)
    keys(app, Key.KEY1, Key.RIGHT, Key.PRESS, Key.PRESS, Key.PRESS)   # first backup, P2-01
    add_clip(fc.folders[1], 3)
    fc.changes += 1
    app.cards.run_job(("poll",))
    keys(app, Key.PRESS)                        # joystick press = back up the selected card
    assert app.mode == "confirm" and app.detail.endswith("GB to copy")
    keys(app, Key.LEFT, Key.PRESS)
    assert app.mode == "result" and app.result.card == "P2-01"


def test_empty_slot_and_not_answering(tmp_path):
    app, _, clock = app_with_camera(tmp_path)
    keys(app, Key.KEY2)
    assert app.notice and "no card" in app.notice[1][0]
    keys(app, Key.PRESS)
    keys(app, Key.KEY1)
    clock.t += 11
    app.tick()
    assert app.mode == "home"                   # unanswered question = No


def test_reassign_from_card_list(tmp_path):
    app, _, _ = app_with_camera(tmp_path)
    keys(app, Key.KEY1, Key.RIGHT, Key.PRESS, Key.PRESS, Key.PRESS)   # -> P2-01
    keys(app, Key.RIGHT)
    assert app.mode == "pick" and all(v != "P2-01" for _, v in app.pick_items)
    keys(app, Key.PRESS)                        # "New card P2-02"
    assert app.mode == "confirm" and app.question == "Move to P2-02?"
    keys(app, Key.RIGHT, Key.PRESS)
    assert app.view(1).card == "P2-02"


def test_any_key_during_backup_asks_to_cancel(tmp_path):
    app, _, _ = app_with_camera(tmp_path)
    app.mode, app.cards.running_backup = "backing_up", True
    keys(app, InputEvent(Key.KEY3, Kind.LONG))
    assert app.mode == "confirm" and app.question == "Cancel backup?"
    keys(app, Key.PRESS)                        # No: back to the progress screen
    assert app.mode == "backing_up" and not app.cards.cancel.is_set()
    keys(app, Key.KEY1, Key.RIGHT, Key.PRESS)
    assert app.cards.cancel.is_set() and app.cancelling


def test_power_and_restart_blocked_during_backup(tmp_path):
    app, _, _ = app_with_camera(tmp_path)
    app.cards.running_backup = True
    assert app.blocked_reason


def test_unplugging_camera_clears_list(tmp_path):
    app, _, _ = app_with_camera(tmp_path)
    app.usb.unplug()
    app.on_usb(UsbEvent(app.usb.current()))
    assert app.conn.mode == "idle" and app.card_views.cards == ()


def test_two_cards_listed_and_slot2(tmp_path):
    app, _, _ = app_with_camera(tmp_path, slot2=True)
    keys(app, Key.DOWN)
    assert app.card_sel == 1
    keys(app, Key.KEY2, Key.RIGHT, Key.PRESS, Key.PRESS)
    assert app.result.slot == 2 and app.result.result.ok


def test_screen_text_fits():
    from PIL import Image, ImageDraw

    from p2backup.app import POWER_QUESTIONS, result_lines
    from p2backup.cards import CardView
    from p2backup.copier import Progress, Result
    from p2backup.ui import screens
    d = ImageDraw.Draw(Image.new("1", (128, 64)))
    f = screens.font(11)
    views = [CardView(1), CardView(2, present=True, error="x"), CardView(1, present=True),
             CardView(1, present=True, is_p2=True, bytes_total=63_900_000_000),
             CardView(2, present=True, is_p2=True, card="P2-99", folder="f", files_total=9,
                      files_todo=3, bytes_todo=63_900_000_000),
             CardView(2, present=True, is_p2=True, card=None, folder="f", files_total=9)]
    for v in views:                             # card rows start after the marker
        assert d.textlength(screens.card_row(v), font=f) <= 118, screens.card_row(v)
    texts = ["Free: 229.4 GB", "Version 0.10.0", "IP 192.168.100.200", "Name rpi-zero-backup",
             "Drive: 229.4 GB free", "Back up slot 2?", "63.9GB to copy",
              "Move to P2-99?", "from UNSORTED", "Cancel backup?", "Copied files are kept",
              "S2: which card?", "Move to which card?", "New card P2-99"]
    texts += list(POWER_QUESTIONS.values())
    for status in ("verified", "cancelled", "card_lost", "disk_full", "incomplete", "failed"):
        title, lines = result_lines(BackupDoneEvent(2, "P2-01", Result("r", status=status,
                                                    message="x" * 40), ()))
        texts += [f"{title} S2", *lines]
    p = Progress("CONTENTS/VIDEO/0001O6.MXF", 1, 2, 63_000_000_000, 63_900_000_000,
                 25_000_000, 7300)
    texts += ["0001O6.MXF", "63.0/63.9GB 25MB/s", f"100% {screens.fmt_eta(7300)} key: cancel",
              f"100% {screens.fmt_eta(7300)} Cancelling..."]
    screens.backing_up("UNSORTED", p, "100%")
    for t in texts:
        assert d.textlength(t, font=f) <= 124, t


def test_about_from_camera_list_and_idle(tmp_path):
    app, _, clock = app_with_camera(tmp_path)
    keys(app, Key.DOWN, Key.DOWN)               # S1, S2, About
    assert app.card_sel == 2
    keys(app, Key.PRESS)
    assert app.mode == "about"
    keys(app, Key.KEY1)                         # any key: back, nothing else
    assert app.mode == "home" and app.notice is None
    app.usb.unplug()
    app.on_usb(UsbEvent(app.usb.current()))
    keys(app, Key.PRESS)
    assert app.mode == "about"
    clock.t += 16
    app.tick()
    assert app.mode == "home"


def test_header_status_icons():
    from p2backup.ui import screens
    a = screens.info("T", [], screens.Status(wifi=True, battery=50))
    b = screens.info("T", [], screens.Status(wifi=False, battery=50))
    assert a.tobytes() != b.tobytes()

"""Phase 1 app: power menu and housekeeping, with fake display/power/clock."""

import queue

from p2backup.app import MENU_TIMEOUT_S, App
from p2backup.config import Config
from p2backup.power import Reading, UpsEvent
from p2backup.storage import FakeStorage
from p2backup.ui.inputs import InputEvent, Key, Kind
from p2backup.usb import FakeUsb, UsbEvent

LONG3 = InputEvent(Key.KEY3, Kind.LONG)


class FakeDisplay:
    def __init__(self):
        self.frames = []

    def show(self, img):
        self.frames.append(img)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(power_ok=True, ip="192.168.1.148"):
    calls, clock = [], Clock()

    def power(action):
        calls.append(action)
        return power_ok

    usb = FakeUsb()
    app = App(Config(), FakeDisplay(), queue.Queue(), usb, usb, FakeStorage(),
              power=power, ip=lambda: ip, clock=clock)
    app.start()
    return app, calls, clock


def press(app, *keys):
    for k in keys:
        app.handle(k if isinstance(k, InputEvent) else InputEvent(k))


def test_long_press_opens_menu_short_asks_restart():
    app, calls, _ = make()
    press(app, InputEvent(Key.KEY3, Kind.SHORT))
    assert app.mode == "confirm" and app.action == "restart"
    press(app, Key.RIGHT, Key.PRESS)
    assert calls == ["restart"]
    app, _, _ = make()
    press(app, LONG3)
    assert app.mode == "menu" and app.selected == 0


def test_shutdown_needs_confirmation_and_defaults_to_no():
    app, calls, _ = make()
    press(app, LONG3, Key.PRESS)                 # "Shut down"
    assert app.mode == "confirm" and app.action == "halt" and not app.yes
    press(app, Key.PRESS)                        # default answer is No
    assert app.mode == "home" and calls == []
    press(app, LONG3, Key.PRESS, Key.RIGHT, Key.PRESS)
    assert calls == ["halt"] and app.mode == "busy"
    press(app, Key.PRESS, LONG3)                 # ignored once shutting down
    assert calls == ["halt"]


def test_reboot_and_cancel_items():
    app, calls, _ = make()
    press(app, LONG3, Key.DOWN, Key.PRESS, Key.LEFT, Key.PRESS)
    assert calls == ["reboot"]
    app, calls, _ = make()
    press(app, LONG3, Key.UP, Key.PRESS)         # wraps to "Cancel"
    assert app.mode == "home" and calls == []


def test_other_keys_cancel():
    for key in (Key.KEY1, Key.KEY2, Key.LEFT):
        app, calls, _ = make()
        press(app, LONG3, key)
        assert app.mode == "home"
    app, calls, _ = make()
    press(app, LONG3, Key.PRESS, Key.KEY1)
    assert app.mode == "home" and calls == []


def test_failed_power_request_returns_home():
    app, calls, _ = make(power_ok=False)
    press(app, LONG3, Key.PRESS, Key.RIGHT, Key.PRESS)
    assert calls == ["halt"] and app.mode == "home"


def test_menu_times_out():
    app, _, clock = make()
    press(app, LONG3)
    clock.t += MENU_TIMEOUT_S - 1
    app.tick()
    assert app.mode == "menu"
    clock.t += 1
    assert app.tick() and app.mode == "home"


def test_ip_refresh_and_render_every_mode():
    app, _, _ = make()
    assert app.ip == "192.168.1.148"            # read by start()
    for keys in ([], [LONG3], [LONG3, Key.PRESS]):
        app2, _, _ = make()
        press(app2, *keys)
        app2.render()
        assert app2.display.frames[-1].size == (128, 64)
    app.mode, app.action = "busy", "reboot"
    app.render()


def _pc(app):
    app.usb.plug_pc()
    app.on_usb(UsbEvent(app.usb.current()))


def test_power_menu_blocked_while_pc_has_drive():
    app, calls, _ = make()
    _pc(app)
    assert app.conn.mode == "pc"
    press(app, LONG3)
    assert app.mode == "home" and app.notice is not None
    press(app, Key.PRESS)                       # dismisses the notice only
    assert app.notice is None and calls == []


def test_pc_taking_drive_closes_open_menu():
    app, _, _ = make()
    press(app, LONG3)
    _pc(app)
    assert app.mode == "home"


def test_ups_warning_only_in_pc_mode_with_no_protection():
    app, _, _ = make()
    app.on_ups(UpsEvent(True, Reading(3.9, -400)))
    assert not app.ups_warning()                # not in PC mode
    _pc(app)
    assert app.ups_warning()
    app.config.power.pc_power_protection = "diode"
    assert not app.ups_warning()


def test_every_connection_screen_renders():
    app, _, _ = make()
    for setup in (lambda: None, app.usb.plug_camera, lambda: _pc(app), app.usb.eject):
        setup()
        app.on_usb(UsbEvent(app.usb.current()))
        app.render()
        assert app.display.frames[-1].size == (128, 64)
    app.conn.mode, app.conn.error = "error", "Drive needs repair"
    app.render()

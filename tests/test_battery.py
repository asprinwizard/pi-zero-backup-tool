"""Battery %: load correction, smoothing, low/critical levels, and what the app does."""

import pytest

from p2backup.power import CRITICAL, LOW, OK, Reading, UpsEvent, UpsMonitor, ocv_percent


def test_ocv_curve_ends_and_middle():
    assert ocv_percent(3.0) == 0 and ocv_percent(4.3) == 100
    assert ocv_percent(3.80) == 50
    assert 50 < ocv_percent(3.82) < 60


def test_load_correction_matches_measurements():
    # Measured 2026-11-18 on the same battery: resting 4.06 V; UPS on, ~3.99 V at -230 mA.
    rest = Reading(4.06, -2).percent
    loaded = Reading(3.99, -230).percent
    assert abs(rest - loaded) <= 3
    assert Reading(3.99, -230).percent > Reading(3.99, 0).percent


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def monitor(**kw):
    clock = Clock()
    return UpsMonitor(lambda e: None, sensor=None, clock=clock, **kw), clock


def test_events_only_on_change_or_periodically():
    m, clock = monitor()
    assert m.update(Reading(4.0, -250)) is not None
    clock.t += 2
    assert m.update(Reading(4.0, -250)) is None
    clock.t += 31
    assert m.update(Reading(4.0, -250)) is not None
    ev = m.update(Reading(4.0, 0))                  # UPS switched off
    assert ev and not ev.discharging and ev.level == OK


def test_low_then_critical_needs_sustained_readings():
    m, _ = monitor(low_pct=15, critical_pct=5)
    ev = None
    for _ in range(5):
        ev = m.update(Reading(3.52, -250)) or ev
    assert ev.level == LOW
    seen = [m.update(Reading(3.30, -250)) for _ in range(3)]
    levels = [e.level for e in seen if e]
    assert levels[-1] == CRITICAL and levels.count(CRITICAL) == 1


def test_one_sag_is_not_critical():
    m, _ = monitor()
    m.update(Reading(3.9, -250))
    ev = m.update(Reading(3.0, -900))               # a brief dip under heavy load
    assert ev is None or ev.level != CRITICAL
    ev = m.update(Reading(3.9, -250))
    assert ev is None or ev.level != CRITICAL


def test_not_discharging_is_never_low():
    m, _ = monitor()
    ev = None
    for _ in range(5):
        ev = m.update(Reading(3.4, 0)) or ev
    assert ev.level == OK


# ---- app ---------------------------------------------------------------------

@pytest.fixture
def cam(tmp_path):
    from tests.test_cards import app_with_camera
    return app_with_camera(tmp_path)


def ups(pct, level=OK, discharging=True):
    return UpsEvent(discharging, Reading(3.8, -250 if discharging else 0), pct, level)


def test_low_battery_notice(cam):
    app, _, _ = cam
    app.on_ups(ups(50))
    assert app.notice is None and app.battery().battery == 50
    app.on_ups(ups(14, LOW))
    assert app.notice[0] == "BATTERY LOW"


def test_critical_when_idle_halts(cam):
    app, _, _ = cam
    calls = []
    app.power = lambda a: calls.append(a) or True
    app.on_ups(ups(3, CRITICAL))
    assert calls == ["halt"] and app.mode == "busy" and "Battery empty" in app.busy_lines


def test_critical_during_backup_cancels_then_halts(cam):
    from p2backup.cards import BackupDoneEvent
    from p2backup.copier import Result
    app, _, clock = cam
    calls = []
    app.power = lambda a: calls.append(a) or True
    app.mode, app.cards.running_backup = "backing_up", True
    app.on_ups(ups(3, CRITICAL))
    assert app.cards.cancel.is_set() and calls == []
    app.on_cards(BackupDoneEvent(1, "P2-01", Result("r", status="cancelled"), ()))
    assert calls == ["halt"] and "Backup stopped" in app.busy_lines


def test_critical_backup_that_will_not_stop_still_halts(cam):
    app, _, clock = cam
    calls = []
    app.power = lambda a: calls.append(a) or True
    app.mode, app.cards.running_backup = "backing_up", True
    app.on_ups(ups(3, CRITICAL))
    clock.t += 61
    app.tick()
    assert calls == ["halt"]


def test_critical_in_pc_mode_does_not_halt(cam):
    app, _, _ = cam
    calls = []
    app.power = lambda a: calls.append(a) or True
    app.conn.mode = "pc"
    app.on_ups(ups(3, CRITICAL))
    assert calls == [] and app.notice[0] == "BATTERY EMPTY"


def test_preflight_warns_when_battery_too_low(cam):
    from p2backup.ui.inputs import InputEvent, Key
    app, _, _ = cam
    assert not app.battery_short(10**9)             # no UPS reading: on mains
    app.on_ups(ups(80))
    assert not app.battery_short(10**9)             # 1 GB at 80%: fine
    app.on_ups(ups(30))
    assert app.battery_short(64 * 10**9)            # 64 GB ~ 2.4 h * 300 mA > 250 mAh left
    app.on_ups(ups(30, discharging=False))
    assert not app.battery_short(64 * 10**9)        # UPS off: powered externally
    app.on_ups(ups(10))
    app.notice = None
    v = app.view(1)
    object.__setattr__(v, "bytes_todo", 30 * 10**9)
    app.handle(InputEvent(Key.KEY1))
    assert app.detail == "Battery may run out"

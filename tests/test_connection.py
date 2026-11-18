"""Connection controller: the data partition is never on both sides at once."""

from p2backup.connection import PC_GONE_AFTER_S, Connection
from p2backup.storage import FakeStorage
from p2backup.usb import FakeUsb


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def setup(**storage_kw):
    usb, storage, clock = FakeUsb(), FakeStorage(**storage_kw), Clock()
    busy = []
    conn = Connection(usb, storage, busy=busy.append, clock=clock)
    return conn, usb, storage, clock, busy


def never_both(usb, storage):
    assert not (storage.mounted and usb.medium()), "mounted on the Pi while the PC has it"


def test_start_idle_mounts_locally():
    conn, usb, storage, _, _ = setup()
    conn.start(usb.current())
    assert conn.mode == "idle" and storage.mounted


def test_start_with_pc_already_holding_drive_does_not_mount():
    conn, usb, storage, _, _ = setup()
    usb.plug_pc()
    usb.attach(storage.data_dev)                # app restarted mid-session
    conn.start(usb.current())
    assert conn.mode == "pc" and "mount" not in storage.calls
    never_both(usb, storage)


def test_boot_from_pc_hands_over_without_local_mount():
    conn, usb, storage, _, _ = setup()
    usb.plug_pc()
    conn.start(usb.current())
    assert conn.mode == "pc" and usb.medium() == storage.data_dev
    assert "mount" not in storage.calls


def test_pc_cycle_eject_then_unplug_with_ups_on():
    conn, usb, storage, clock, busy = setup()
    conn.start(usb.current())
    usb.plug_pc()
    assert conn.update(usb.current())
    assert conn.mode == "pc" and not storage.mounted and usb.medium() == storage.data_dev
    assert storage.calls[-2:] == ["unmount", "writeback:on"]
    never_both(usb, storage)

    usb.eject()
    conn.update(usb.current())
    assert conn.mode == "pc_ejected" and storage.calls[-1] == "flush"   # flush BEFORE "safe"
    assert "Saving..." in busy

    usb.unplug()
    conn.update(usb.current())
    assert conn.mode == "pc_ejected"            # debounce
    clock.t += PC_GONE_AFTER_S
    conn.update(usb.current())
    assert conn.mode == "idle" and storage.mounted
    assert "writeback:off" in storage.calls
    never_both(usb, storage)


def test_unplug_without_eject_detaches_flushes_then_mounts():
    conn, usb, storage, clock, _ = setup()
    conn.start(usb.current())
    usb.plug_pc()
    conn.update(usb.current())
    usb.unplug()
    conn.update(usb.current())
    clock.t += PC_GONE_AFTER_S
    conn.update(usb.current())
    assert usb.medium() == "" and storage.mounted and conn.mode == "idle"
    calls = storage.calls
    assert calls.index("flush", calls.index("writeback:on")) < len(calls) - 1


def test_brief_reenumeration_keeps_pc_mode():
    conn, usb, storage, clock, _ = setup()
    conn.start(usb.current())
    usb.plug_pc()
    conn.update(usb.current())
    usb.unplug()
    conn.update(usb.current())
    clock.t += PC_GONE_AFTER_S / 2
    usb.plug_pc()                               # back within the debounce window
    conn.update(usb.current())
    clock.t += PC_GONE_AFTER_S
    conn.update(usb.current())
    assert conn.mode == "pc" and usb.medium()


def test_camera_mode():
    conn, usb, storage, _, _ = setup()
    conn.start(usb.current())
    usb.plug_camera()
    assert conn.update(usb.current()) and conn.mode == "camera" and storage.mounted
    usb.unplug()
    conn.update(usb.current())
    assert conn.mode == "idle"


def test_mount_failure_shows_error_and_pc_can_still_take_drive():
    conn, usb, storage, _, _ = setup()
    storage.fail_mount = "drive needs repair"
    conn.start(usb.current())
    assert conn.mode == "error" and conn.error == "Drive needs repair"
    usb.plug_pc()
    conn.update(usb.current())
    assert conn.mode == "pc"                    # the PC can repair it


def test_unmount_failure_does_not_hand_over_and_does_not_retry():
    conn, usb, storage, _, _ = setup()
    conn.start(usb.current())
    storage.fail_unmount = "release the drive"
    usb.plug_pc()
    conn.update(usb.current())
    assert conn.mode == "error" and usb.medium() == ""
    n = storage.calls.count("unmount")
    conn.update(usb.current())
    assert storage.calls.count("unmount") == n  # not hammering it every poll


def test_retry_after_error():
    conn, usb, storage, _, _ = setup()
    storage.fail_mount = "drive needs repair"
    conn.start(usb.current())
    storage.fail_mount = None
    conn.retry(usb.current())
    assert conn.mode == "idle" and storage.mounted

"""usb/storage/power modules against fake sysfs, fake commands and raw registers."""

import subprocess
from pathlib import Path

import pytest

from p2backup import power
from p2backup.storage import Storage, StorageError
from p2backup.usb import LUN_REL, Gadget, read_status


def fake_sysfs(root: Path, state="not attached", devices=("usb1", "1-0:1.0"), medium=""):
    udc = root / "sys/class/udc/3f980000.usb"
    udc.mkdir(parents=True)
    (udc / "state").write_text(state + "\n")
    devs = root / "sys/bus/usb/devices"
    devs.mkdir(parents=True)
    for d in devices:
        (devs / d).mkdir()
    lun = root / LUN_REL
    lun.mkdir(parents=True)
    (lun / "file").write_text(medium + "\n")
    return lun


def test_status_nothing_connected(tmp_path):
    fake_sysfs(tmp_path)
    st = read_status(tmp_path)
    assert not st.pc and not st.camera and st.medium == ""


def test_status_pc_and_camera(tmp_path):
    fake_sysfs(tmp_path, state="configured")
    assert read_status(tmp_path).pc
    other = tmp_path / "cam"
    fake_sysfs(other, devices=("usb1", "1-0:1.0", "1-1", "1-1:1.0"))
    st = read_status(other)
    assert st.camera and st.host_devices == ("1-1",)


def test_gadget_attach_and_forced_detach(tmp_path):
    lun = fake_sysfs(tmp_path)
    g = Gadget(tmp_path)
    g.attach("/dev/mmcblk0p3")
    assert g.medium() == "/dev/mmcblk0p3"
    (lun / "forced_eject").write_text("")
    g.detach()
    assert (lun / "forced_eject").read_text() == "1\n"   # kernel clears 'file' itself


class Runner:
    def __init__(self, results=None):
        self.cmds, self.results = [], results or {}

    def __call__(self, cmd):
        self.cmds.append(cmd)
        rc, out = self.results.get(cmd[0], (0, ""))
        return subprocess.CompletedProcess(cmd, rc, out, "")


def test_storage_mount_runs_fsck_before_mount(tmp_path, monkeypatch):
    loop = tmp_path / "loop0"
    (tmp_path / "loop0p1").write_text("")
    run = Runner({"losetup": (0, f"{loop}\n")})
    s = Storage(run=run, mount_point=str(tmp_path / "mnt"))
    monkeypatch.setattr(s, "loop_device", lambda: None)
    s.mount()
    names = [c[0] for c in run.cmds]
    assert names.index("fsck.exfat") < names.index("mount")
    assert run.cmds[-1][:4] == ["mount", "-t", "exfat", "-o"]


def test_storage_refuses_to_mount_when_fsck_cannot_fix(tmp_path, monkeypatch):
    (tmp_path / "loop0p1").write_text("")
    run = Runner({"fsck.exfat": (4, "errors left")})
    s = Storage(run=run, mount_point=str(tmp_path / "mnt"))
    monkeypatch.setattr(s, "loop_device", lambda: str(tmp_path / "loop0"))
    with pytest.raises(StorageError):
        s.mount()
    assert "mount" not in [c[0] for c in run.cmds]


def test_storage_writeback_saved_and_restored(tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "dirty_expire_centisecs").write_text("3000\n")
    (vm / "dirty_writeback_centisecs").write_text("500\n")
    s = Storage(run=Runner(), proc_vm=vm)
    s.pc_writeback(True)
    assert (vm / "dirty_expire_centisecs").read_text() == "100"
    s.pc_writeback(True)                        # idempotent: keeps the original values
    s.pc_writeback(False)
    assert (vm / "dirty_expire_centisecs").read_text() == "3000"
    assert (vm / "dirty_writeback_centisecs").read_text() == "500"


def test_storage_loop_device_parse():
    run = Runner({"losetup": (0, "/dev/loop3: []: (/dev/mmcblk0p3)\n")})
    assert Storage(run=run).loop_device() == "/dev/loop3"


def test_ina219_decode():
    # -45 mV across 0.1 ohm = -450 mA; bus 3.9 V = 975 * 4 mV
    r = power.decode((-4500) & 0xFFFF, 975 << 3, 0.1)
    assert r.current_ma == -450 and r.voltage == 3.9
    assert power.UpsMonitor(None, None, 100).classify(r)
    assert not power.UpsMonitor(None, None, 100).classify(power.decode(200, 975 << 3, 0.1))

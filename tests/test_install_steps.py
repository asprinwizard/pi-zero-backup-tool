"""Run file-editing steps against a sandbox root."""

from pathlib import Path

import install
import install.steps.s20_boot_config as s20
from install.common import Ctx, State
from install.steps import STEPS
from tests.install_entry import select_steps_for
from tests.test_install_common import STOCK_CMDLINE, STOCK_CONFIG


def _sandbox(tmp_path: Path) -> Ctx:
    boot = tmp_path / "boot/firmware"
    boot.mkdir(parents=True)
    (boot / "config.txt").write_text(STOCK_CONFIG)
    (boot / "cmdline.txt").write_text(STOCK_CMDLINE)
    return Ctx(root=tmp_path)


def test_s20_edits_boot_files_idempotently(tmp_path: Path):
    ctx = _sandbox(tmp_path)
    state = State(ctx)
    s20.run(ctx, state)
    cfg = (tmp_path / "boot/firmware/config.txt").read_text()
    cmd = (tmp_path / "boot/firmware/cmdline.txt").read_text()
    assert "dtoverlay=dwc2\n" in cfg and "dtparam=spi=on" in cfg and "dtparam=i2c_arm=on" in cfg
    assert "modules-load=dwc2" in cmd.split()
    assert state.get("reboot_needed")

    ctx2 = Ctx(root=tmp_path)
    s20.run(ctx2, State(ctx2))
    assert ctx2.changed == []           # second run changes nothing


def test_step_names_unique_and_ordered():
    names = [s.NAME for s in STEPS]
    assert len(names) == len(set(names))
    assert names == sorted(names)


def test_select_steps():
    assert "s90" not in select_steps_for([])            # opt-in step excluded by default
    assert select_steps_for(["--only", "s90"]) == ["s90"]
    assert "s10" not in select_steps_for(["--skip", "s10"])
    assert select_steps_for(["--update"]) == ["s40", "s50"]


def test_package_importable():
    assert install.__doc__


def test_reboot_required_step_done_flag():
    from install.common import RebootRequired
    assert RebootRequired("x").step_done is True
    assert RebootRequired("x", step_done=False).step_done is False


def test_fastboot_config_block_and_cleanup():
    from install.common import strip_block
    from install.steps.s50_units import FASTBOOT_MARKER, fastboot_config
    from tests.test_install_common import STOCK_CONFIG
    hand = STOCK_CONFIG + "auto_initramfs=0\nboot_delay=0\n"      # added by hand while testing
    out = fastboot_config(hand)
    assert out.count("auto_initramfs=0") == 1 and out.count("boot_delay=0") == 1
    assert "# BEGIN p2backup-fastboot" in out
    assert fastboot_config(out) == out                            # idempotent
    assert "auto_initramfs=0" not in strip_block(out, FASTBOOT_MARKER)


def test_s30_accepts_our_fastboot_block():
    from install.common import config_has, strip_block
    from install.steps.s50_units import FASTBOOT_MARKER, fastboot_config
    from tests.test_install_common import STOCK_CONFIG
    cfg = fastboot_config(STOCK_CONFIG)
    assert config_has(cfg, "auto_initramfs=0")
    assert not config_has(strip_block(cfg, FASTBOOT_MARKER), "auto_initramfs=0")

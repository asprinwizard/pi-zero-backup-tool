import subprocess
from pathlib import Path

import pytest

from install import partition as P
from install.common import FILES_DIR, Ctx, InstallError

GIB = 1024 * P.MIB
CARD_256 = 500_170_752          # sectors on a typical "256 GB" SD card
STOCK_P2_START = 1_064_960       # Pi OS: 512 MiB boot partition starting at 8192


def test_plan_layout_256gb():
    lay = P.plan_layout(STOCK_P2_START, CARD_256)
    assert lay.p2_sectors == 16 * GIB
    assert lay.p3_start % P.ALIGN == 0
    assert lay.p3_start >= lay.p2_start + lay.p2_sectors
    assert lay.p3_start + lay.p3_sectors < CARD_256
    assert 220 < lay.p3_mib / 1024 < 225


def test_plan_layout_alignment_gap_is_small():
    lay = P.plan_layout(STOCK_P2_START, CARD_256)
    gap = lay.p3_start - (lay.p2_start + lay.p2_sectors)
    assert 0 <= gap < P.ALIGN


def test_plan_layout_rejects_small_card():
    with pytest.raises(InstallError):
        P.plan_layout(STOCK_P2_START, 18 * GIB)


def test_root_fs_smaller_than_partition():
    assert P.ROOT_FS_MIB < P.ROOT_PART_MIB


def _fake_sysfs(root: Path, disk: int, p2_start: int, p2_size: int, p3: bool = False):
    base = root / "sys/block/mmcblk0"
    (base / "mmcblk0p2").mkdir(parents=True)
    (base / "size").write_text(f"{disk}\n")
    (base / "mmcblk0p2/start").write_text(f"{p2_start}\n")
    (base / "mmcblk0p2/size").write_text(f"{p2_size}\n")
    if p3:
        (base / "mmcblk0p3").mkdir()


def test_read_disk(tmp_path: Path):
    _fake_sysfs(tmp_path, CARD_256, STOCK_P2_START, CARD_256 - STOCK_P2_START)
    d = P.read_disk(Ctx(root=tmp_path))
    assert d.disk_sectors == CARD_256 and d.p2_start == STOCK_P2_START and not d.has_p3


def test_read_disk_missing(tmp_path: Path):
    with pytest.raises(InstallError):
        P.read_disk(Ctx(root=tmp_path))


def test_render_shrink_script_fills_all_placeholders():
    lay = P.plan_layout(STOCK_P2_START, CARD_256)
    tpl = (FILES_DIR / "initramfs/premount-p2b-shrink").read_text()
    out = P.render_shrink_script(tpl, lay)
    assert "@" not in out
    assert f"P2_SECTORS={lay.p2_sectors}" in out
    assert f"P3_START={lay.p3_start}" in out
    assert "FS_SIZE=15360M" in out


def test_shrink_script_is_valid_sh(tmp_path: Path):
    lay = P.plan_layout(STOCK_P2_START, CARD_256)
    tpl = (FILES_DIR / "initramfs/premount-p2b-shrink").read_text()
    script = tmp_path / "s.sh"
    script.write_text(P.render_shrink_script(tpl, lay))
    assert subprocess.run(["sh", "-n", str(script)]).returncode == 0


def test_shrink_script_prereqs_and_noop_without_token(tmp_path: Path):
    """initramfs-tools calls the script with 'prereqs'; and it must exit early
    when the trigger token is absent (it is on every image after the shrink)."""
    lay = P.plan_layout(STOCK_P2_START, CARD_256)
    tpl = (FILES_DIR / "initramfs/premount-p2b-shrink").read_text()
    script = tmp_path / "s.sh"
    script.write_text(P.render_shrink_script(tpl, lay))
    assert subprocess.run(["sh", str(script), "prereqs"]).returncode == 0
    # /proc/cmdline on the dev machine won't contain p2bshrink -> exit 0 before sourcing
    # /scripts/functions (which doesn't exist here).
    assert subprocess.run(["sh", str(script)]).returncode == 0


@pytest.mark.parametrize("name", ["initramfs/hook-p2b-shrink", "sbin/p2backup-gadget",
                                  "sbin/p2backup-shutdown-splash"])
def test_shell_files_parse(name):
    assert subprocess.run(["sh", "-n", str(FILES_DIR / name)]).returncode == 0

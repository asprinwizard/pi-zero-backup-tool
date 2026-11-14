from pathlib import Path

from install.common import (
    BACKUP_SUFFIX,
    Ctx,
    State,
    cmdline_add,
    cmdline_remove,
    cmdline_value,
    config_has,
    fstab_add_option,
    managed_block,
)

STOCK_CONFIG = """\
# For more options and information see
dtparam=audio=on
camera_auto_detect=1
auto_initramfs=1
arm_64bit=1

[cm4]
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
"""

STOCK_CMDLINE = ("console=serial0,115200 console=tty1 root=PARTUUID=1a2b3c4d-02 "
                 "rootfstype=ext4 fsck.repair=yes rootwait quiet splash\n")


# ---- managed_block -----------------------------------------------------

def test_managed_block_appends_with_all_section():
    out = managed_block(STOCK_CONFIG, ["dtoverlay=dwc2", "dtparam=spi=on"])
    tail = out.rstrip("\n").splitlines()[-5:]
    assert tail == ["# BEGIN p2backup", "[all]", "dtoverlay=dwc2", "dtparam=spi=on",
                    "# END p2backup"]
    assert out.count("# BEGIN p2backup") == 1
    # our block comes after the [cm5] section, so [all] inside it matters
    assert out.index("[cm5]") < out.index("# BEGIN p2backup")


def test_managed_block_is_idempotent():
    once = managed_block(STOCK_CONFIG, ["dtoverlay=dwc2"])
    assert managed_block(once, ["dtoverlay=dwc2"]) == once


def test_managed_block_replaces_old_block():
    once = managed_block(STOCK_CONFIG, ["dtoverlay=dwc2", "old=1"])
    twice = managed_block(once, ["dtoverlay=dwc2", "new=1"])
    assert "old=1" not in twice and "new=1" in twice
    assert twice.count("# BEGIN p2backup") == 1


def test_managed_block_keeps_user_content_after_block():
    once = managed_block(STOCK_CONFIG, ["a=1"])
    user_added = once + "hdmi_force_hotplug=1\n"
    out = managed_block(user_added, ["a=1"])
    assert "hdmi_force_hotplug=1" in out
    assert out.rstrip().endswith("# END p2backup")


def test_config_has_ignores_comments():
    assert config_has("auto_initramfs=1\n", "auto_initramfs=1")
    assert not config_has("#auto_initramfs=0\n", "auto_initramfs=0")
    assert config_has("auto_initramfs=0  # off\n", "auto_initramfs=0")


# ---- cmdline -----------------------------------------------------------

def test_cmdline_add_after_rootwait():
    out = cmdline_add(STOCK_CMDLINE, "modules-load=dwc2", after="rootwait")
    toks = out.split()
    assert toks[toks.index("rootwait") + 1] == "modules-load=dwc2"
    assert out.endswith("\n") and out.count("\n") == 1


def test_cmdline_add_is_idempotent():
    once = cmdline_add(STOCK_CMDLINE, "modules-load=dwc2", after="rootwait")
    assert cmdline_add(once, "modules-load=dwc2", after="rootwait") == once


def test_cmdline_add_without_anchor_appends():
    assert cmdline_add("a b\n", "c", after="zzz") == "a b c\n"


def test_cmdline_remove_and_value():
    with_tok = cmdline_add(STOCK_CMDLINE, "p2bshrink")
    assert "p2bshrink" not in cmdline_remove(with_tok, "p2bshrink").split()
    assert cmdline_value(STOCK_CMDLINE, "root") == "PARTUUID=1a2b3c4d-02"
    assert cmdline_value(STOCK_CMDLINE, "nope") is None


# ---- fstab -------------------------------------------------------------

def test_fstab_add_noatime_once():
    fstab = ("proc /proc proc defaults 0 0\n"
             "PARTUUID=1a2b-01 /boot/firmware vfat defaults 0 2\n"
             "PARTUUID=1a2b-02 / ext4 defaults 0 1\n")
    out = fstab_add_option(fstab, "/", "noatime")
    root_line = [line for line in out.splitlines() if line.split()[1] == "/"][0]
    assert root_line.split()[3] == "defaults,noatime"
    assert fstab_add_option(out, "/", "noatime") == out
    assert "/boot/firmware vfat defaults 0 2" in out


# ---- Ctx file helpers --------------------------------------------------

def test_ctx_write_backs_up_original_once(tmp_path: Path):
    ctx = Ctx(root=tmp_path)
    f = tmp_path / "etc/thing.conf"
    f.parent.mkdir(parents=True)
    f.write_text("original\n")
    assert ctx.write("/etc/thing.conf", "v1\n")
    assert ctx.write("/etc/thing.conf", "v2\n")
    assert f.read_text() == "v2\n"
    assert (tmp_path / f"etc/thing.conf{BACKUP_SUFFIX}").read_text() == "original\n"


def test_ctx_write_unchanged_returns_false(tmp_path: Path):
    ctx = Ctx(root=tmp_path)
    assert ctx.write("/a.txt", "x")
    assert not ctx.write("/a.txt", "x")


def test_ctx_write_sets_mode(tmp_path: Path):
    ctx = Ctx(root=tmp_path)
    ctx.write("/bin/tool", "#!/bin/sh\n", 0o755)
    assert (tmp_path / "bin/tool").stat().st_mode & 0o777 == 0o755


def test_dry_run_changes_nothing(tmp_path: Path):
    ctx = Ctx(root=tmp_path, dry_run=True)
    assert ctx.write("/new.txt", "x")
    assert not (tmp_path / "new.txt").exists()
    res = ctx.run(["false"])            # mutating commands are not executed
    assert res.returncode == 0
    assert ctx.changed == ["/new.txt"]


def test_state_persists(tmp_path: Path):
    ctx = Ctx(root=tmp_path)
    s = State(ctx)
    s.mark("s10")
    s.set("pip_fallback", ["spidev"])
    s2 = State(ctx)
    assert s2.done("s10") and not s2.done("s20")
    assert s2.get("pip_fallback") == ["spidev"]
    s2.clear()
    assert not State(ctx).done("s10")

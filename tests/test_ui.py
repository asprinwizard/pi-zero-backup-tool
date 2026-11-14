from pathlib import Path

import pytest

from p2backup import config, splash
from p2backup.ui import screens, sh1106
from p2backup.ui.inputs import Key, Kind, PressTracker

# ---- SH1106 packing ----------------------------------------------------

def test_pack_single_pixels():
    rows = [[0] * 128 for _ in range(64)]
    rows[0][0] = 1          # page 0, column 0, bit 0
    rows[9][5] = 1          # page 1, column 5, bit 1
    rows[63][127] = 1       # page 7, column 127, bit 7
    buf = sh1106.pack(rows)
    assert len(buf) == 1024
    assert buf[0] == 0x01
    assert buf[128 + 5] == 0x02
    assert buf[7 * 128 + 127] == 0x80
    assert sum(1 for b in buf if b) == 3


def test_pack_unpack_roundtrip_with_pil():
    img = splash.render_message(["P2 BACKUP", "", "Starting up..."])
    buf = sh1106.pack(img)
    rows = sh1106.unpack(buf)
    for y in range(64):
        for x in range(128):
            assert rows[y][x] == (1 if img.getpixel((x, y)) else 0)


def test_pack_rejects_wrong_size():
    from PIL import Image
    with pytest.raises(ValueError):
        sh1106.pack(Image.new("1", (100, 64)))


# ---- splash ------------------------------------------------------------

def test_render_all_writes_every_message(tmp_path: Path):
    paths = splash.render_all(tmp_path)
    assert {p.stem for p in paths} == set(splash.MESSAGES)
    for p in paths:
        data = p.read_bytes()
        assert len(data) == 1024 and any(data)      # not blank


def test_preview_is_text():
    buf = sh1106.pack(splash.render_message(splash.MESSAGES["safe"]))
    lines = splash.ascii_preview(buf).splitlines()
    assert len(lines) == 32 and all(len(line) == 128 for line in lines)


# ---- screens -----------------------------------------------------------

def test_home_screen_renders():
    img = screens.home_phase1("key3 (long)", "0.1.0")
    assert img.size == (128, 64)
    assert any(sh1106.pack(img))


# ---- long press --------------------------------------------------------

def test_short_press():
    t = PressTracker(Key.KEY3, long_press=True, threshold=2.0)
    assert t.down(0.0) is None
    assert t.tick(1.0) is None
    ev = t.up(1.5)
    assert ev.key is Key.KEY3 and ev.kind is Kind.SHORT


def test_long_press_fires_while_held_and_once():
    t = PressTracker(Key.KEY3, long_press=True, threshold=2.0)
    t.down(0.0)
    ev = t.tick(2.1)
    assert ev.kind is Kind.LONG
    assert t.tick(3.0) is None
    assert t.up(4.0) is None            # release after long press emits nothing


def test_plain_key_fires_on_press():
    t = PressTracker(Key.KEY1, long_press=False)
    ev = t.down(0.0)
    assert ev.key is Key.KEY1 and ev.kind is Kind.SHORT
    assert t.up(5.0) is None
    assert t.tick(10.0) is None


def test_spurious_release_ignored():
    assert PressTracker(Key.KEY3, long_press=True).up(1.0) is None


# ---- config ------------------------------------------------------------

def test_config_defaults_when_missing(tmp_path: Path):
    cfg = config.load(tmp_path / "nope.toml")
    assert cfg.backup.verify is True
    assert cfg.power.pc_power_protection == "none"
    assert cfg.cards.ask_timeout_s == 60


def test_config_installer_default_parses(tmp_path: Path):
    from install.steps.s40_app import DEFAULT_CONFIG
    p = tmp_path / "p2backup.toml"
    p.write_text(DEFAULT_CONFIG)
    cfg = config.load(p)
    assert cfg.backup.verify and cfg.cards.ask_card_number


def test_config_rejects_bad_values(tmp_path: Path):
    p = tmp_path / "c.toml"
    p.write_text('[power]\npc_power_protection = "maybe"\n')
    with pytest.raises(ValueError):
        config.load(p)
    p.write_text('[backup]\nverify = "yes"\n')
    with pytest.raises(ValueError):
        config.load(p)


def test_config_ignores_unknown_keys(tmp_path: Path):
    p = tmp_path / "c.toml"
    p.write_text("[backup]\nverify = false\nfuture_option = 3\n")
    assert config.load(p).backup.verify is False

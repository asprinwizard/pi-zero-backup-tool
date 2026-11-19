"""Reading cards: P2 detection, clip IDs and dates, copy order, junk, USB disks by slot."""

import os

from p2backup.p2card import (
    CardMounter,
    UsbDisk,
    card_files,
    find_usb_disks,
    inspect,
    lastclip_counter,
    parse_clip_xml,
)
from tests.p2fixtures import make_card, umid


def test_inspect_reads_clips_dates_and_lastclip(tmp_path):
    root = make_card(tmp_path / "card", clips=[1, 2], date="2026-11-14")
    info = inspect(root, slot=2, volume_serial="1A2B-3C4D")
    assert info.is_p2 and info.slot == 2
    assert info.umids == {umid(1), umid(2)}
    assert info.first_date == "2026-11-14"
    assert info.lastclip.startswith("0002AB")


def test_not_a_p2_card(tmp_path):
    (tmp_path / "stick" / "DCIM").mkdir(parents=True)
    assert not inspect(tmp_path / "stick").is_p2


def test_freshly_formatted_card_is_p2_with_no_clips(tmp_path):
    root = tmp_path / "card"
    (root / "CONTENTS").mkdir(parents=True)
    (root / "LASTCLIP.TXT").write_text("\n")
    info = inspect(root)
    assert info.is_p2 and info.clips == [] and info.first_date is None


def test_card_files_order_and_junk(tmp_path):
    root = make_card(tmp_path / "card", clips=[1, 2])
    rels = [rel for rel, _ in card_files(root)]
    assert rels[-1] == "LASTCLIP.TXT"
    xml_idx = [i for i, r in enumerate(rels) if r.startswith("CONTENTS/CLIP/")]
    essence_idx = [i for i, r in enumerate(rels) if r.startswith(("CONTENTS/VIDEO/",
                                                                  "CONTENTS/AUDIO/"))]
    assert max(essence_idx) < min(xml_idx)
    assert not any("._" in r or "System Volume" in r or "sync.ffs_db" in r for r in rels)


def test_lowercase_names_still_found(tmp_path):
    root = tmp_path / "card"
    (root / "contents" / "clip").mkdir(parents=True)
    (root / "contents" / "clip" / "0001AB.xml").write_text("<P2Main/>")
    assert inspect(root).is_p2


def test_parse_clip_xml_tolerates_missing_and_broken(tmp_path):
    p = tmp_path / "0001AB.XML"
    p.write_text("<P2Main><ClipContent><ClipName>0001AB</ClipName></ClipContent></P2Main>")
    assert parse_clip_xml(p).umid is None and parse_clip_xml(p).date is None
    p.write_text("not xml <<<")
    assert parse_clip_xml(p).name == "0001AB"


def test_lastclip_counter():
    assert lastclip_counter("0012AB\n1.0") == 0x12
    assert lastclip_counter("") is None
    assert lastclip_counter("zz") is None


def _fake_block(sys_root, disk, lun, sectors, model, partitions=()):
    dev = (sys_root / "sys/devices/platform/soc/3f980000.usb/usb1/1-1/1-1:1.0/host0"
           / f"target0:0:0/0:0:0:{lun}")
    dev.mkdir(parents=True)
    (dev / "model").write_text(model + "\n")
    blk = sys_root / "sys/block" / disk
    blk.mkdir(parents=True)
    os.symlink(dev, blk / "device")
    (blk / "size").write_text(f"{sectors}\n")
    for p in partitions:
        (blk / p).mkdir()


def test_find_usb_disks_maps_luns_to_slots(tmp_path):
    _fake_block(tmp_path, "sda", 0, 125_000_000, "AG-HVX200 #1", ["sda1"])
    _fake_block(tmp_path, "sdb", 1, 0, "AG-HVX200 #2")
    disks = find_usb_disks(tmp_path)
    assert [(d.slot, d.has_media, d.partition) for d in disks] == [
        (1, True, "/dev/sda1"), (2, False, "/dev/sdb")]
    assert disks[0].label == "AG-HVX200 #1"


class Rec:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd):
        import subprocess
        self.cmds.append(cmd)
        out = "1a2b-3c4d\n" if cmd[0] == "blkid" else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_mount_is_read_only_twice_over(tmp_path):
    run = Rec()
    m = CardMounter(run=run, base=str(tmp_path / "cards"))
    info = m.mount(UsbDisk("sda", 1, 64_000_000_000, "AG-HVX200 #1", "/dev/sda1"))
    setro = [c for c in run.cmds if c[:2] == ["blockdev", "--setro"]]
    assert {c[2] for c in setro} == {"/dev/sda", "/dev/sda1"}
    mount = next(c for c in run.cmds if c[0] == "mount")
    assert mount[2].split(",")[0] == "ro"
    assert not any(c[0].startswith("fsck") for c in run.cmds)
    assert info.volume_serial == "1A2B-3C4D"
    # setro happens before mount
    assert run.cmds.index(setro[-1]) < run.cmds.index(mount)

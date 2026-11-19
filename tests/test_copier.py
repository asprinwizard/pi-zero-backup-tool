"""The copier: complete, incremental, resumable, never overwrites, verify catches damage."""

import errno
import os
import threading

from p2backup import copier as cp
from p2backup.copier import Copier, load_manifest
from tests.p2fixtures import MTIME, add_clip, card_rel_files, make_card

PLENTY = lambda p: 10**12        # noqa: E731


def run(card, dest, run_id="r1", cls=Copier, **kw):
    kw.setdefault("free_bytes", PLENTY)
    return cls(card, dest, run_id, **kw).run()


def same_tree(card, dest):
    for rel in card_rel_files(card):
        assert (dest / rel).read_bytes() == (card / rel).read_bytes(), rel


def test_first_copy_is_complete_verified_and_skips_junk(tmp_path):
    card, dest = make_card(tmp_path / "card"), tmp_path / "dest"
    res = run(card, dest)
    assert res.status == cp.VERIFIED and res.copied == len(card_rel_files(card))
    same_tree(card, dest)
    assert set(load_manifest(dest / "_p2backup" / "manifest.xxh64")) == card_rel_files(card)
    assert not (dest / "System Volume Information").exists()
    assert not (dest / "sync.ffs_db").exists()
    assert not (dest / "CONTENTS/VIDEO/._0001AB.MXF").exists()
    assert int((dest / "CONTENTS/VIDEO/0001AB.MXF").stat().st_mtime) == MTIME
    assert not (dest / "_p2backup" / "tmp").exists()
    assert (dest / "_p2backup" / "runs" / "r1.log").read_text().count("copied ") == res.copied


def test_incremental_copies_only_new_files(tmp_path):
    card, dest = make_card(tmp_path / "card"), tmp_path / "dest"
    run(card, dest)
    add_clip(card, 4)
    res = run(card, dest, "r2")
    assert res.status == cp.VERIFIED
    assert res.copied == 7          # 6 files of clip 4 + changed LASTCLIP.TXT
    same_tree(card, dest)


def test_clip_order_essence_before_xml(tmp_path):
    card, dest = make_card(tmp_path / "card", clips=[1]), tmp_path / "dest"
    order = []

    class Rec(Copier):
        def _install(self, tmp, dst):
            order.append(dst.relative_to(dest).as_posix())
            super()._install(tmp, dst)

    run(card, dest, cls=Rec)
    assert order.index("CONTENTS/CLIP/0001AB.XML") > order.index("CONTENTS/VIDEO/0001AB.MXF")
    assert order[-1] == "LASTCLIP.TXT"


def test_cancel_then_resume(tmp_path):
    card, dest = make_card(tmp_path / "card", clips=range(1, 6)), tmp_path / "dest"
    cancel = threading.Event()
    seen = []

    def progress(p):
        seen.append(p)
        if p.files_done >= 4:
            cancel.set()

    res = run(card, dest, cancel=cancel, progress=progress, chunk=4096,
              clock=iter(range(0, 10**6)).__next__)
    assert res.status == cp.CANCELLED
    assert not (dest / "_p2backup" / "tmp").exists()
    done = load_manifest(dest / "_p2backup" / "manifest.xxh64")
    assert 0 < len(done) < len(card_rel_files(card))
    for rel in done:                                     # everything recorded is complete
        assert (dest / rel).read_bytes() == (card / rel).read_bytes()
    for p in (dest / "CONTENTS").rglob("*"):             # and nothing half-written is visible
        if p.is_file():
            rel = p.relative_to(dest).as_posix()
            assert p.read_bytes() == (card / rel).read_bytes()

    res2 = run(card, dest, "r2")
    assert res2.status == cp.VERIFIED and res2.skipped >= len(done)
    same_tree(card, dest)


def test_clip_deleted_in_camera_keeps_backup(tmp_path):
    card, dest = make_card(tmp_path / "card"), tmp_path / "dest"
    run(card, dest)
    for p in card.rglob("0002AB*"):
        p.unlink()
    res = run(card, dest, "r2")
    assert res.status == cp.VERIFIED
    assert (dest / "CONTENTS/VIDEO/0002AB.MXF").exists()


def test_different_existing_file_is_moved_aside_not_overwritten(tmp_path):
    card, dest = make_card(tmp_path / "card"), tmp_path / "dest"
    run(card, dest)
    old = (dest / "CONTENTS/VIDEO/0001AB.MXF").read_bytes()
    (card / "CONTENTS/VIDEO/0001AB.MXF").write_bytes(os.urandom(1234))   # card changed
    res = run(card, dest, "r2")
    assert res.status == cp.VERIFIED and res.replaced == 1
    assert (dest / "_p2backup/replaced/r2/CONTENTS/VIDEO/0001AB.MXF").read_bytes() == old
    same_tree(card, dest)


def test_identical_file_without_manifest_is_adopted(tmp_path):
    card, dest = make_card(tmp_path / "card", clips=[1]), tmp_path / "dest"
    run(card, dest)
    (dest / "_p2backup" / "manifest.xxh64").unlink()     # e.g. power cut before it was saved
    res = run(card, dest, "r2")
    assert res.status == cp.VERIFIED and res.adopted == len(card_rel_files(card))
    assert res.replaced == 0 and not (dest / "_p2backup" / "replaced").exists()


def test_not_enough_space_stops_before_writing(tmp_path):
    card, dest = make_card(tmp_path / "card"), tmp_path / "dest"
    res = run(card, dest, free_bytes=lambda p: 1000)
    assert res.status == cp.DISK_FULL
    assert not (dest / "CONTENTS").exists()


def test_disk_full_mid_copy_stops_cleanly(tmp_path):
    card, dest = make_card(tmp_path / "card"), tmp_path / "dest"
    n = {"writes": 0}

    class Full(Copier):
        def _write(self, out, chunk):
            n["writes"] += 1
            if n["writes"] > 5:
                raise OSError(errno.ENOSPC, "No space left on device")
            super()._write(out, chunk)

    res = run(card, dest, cls=Full, chunk=8192)
    assert res.status == cp.DISK_FULL
    assert not (dest / "_p2backup" / "tmp").exists()
    for rel in load_manifest(dest / "_p2backup" / "manifest.xxh64"):
        assert (dest / rel).read_bytes() == (card / rel).read_bytes()


def test_verify_catches_a_bad_byte_and_retries(tmp_path):
    card, dest = make_card(tmp_path / "card", clips=[1]), tmp_path / "dest"
    flipped = []

    class Corrupt(Copier):
        def _install(self, tmp, dst):
            super()._install(tmp, dst)
            if dst.name == "0001AB.MXF" and "VIDEO" in str(dst) and not flipped:
                b = bytearray(dst.read_bytes())
                b[100] ^= 0xFF
                dst.write_bytes(bytes(b))
                flipped.append(dst)

    res = run(card, dest, cls=Corrupt)
    assert flipped and res.status == cp.VERIFIED
    assert (dest / "_p2backup/replaced/r1/CONTENTS/VIDEO/0001AB.MXF.bad").exists()
    same_tree(card, dest)


def test_verify_failure_twice_is_reported(tmp_path):
    card, dest = make_card(tmp_path / "card", clips=[1]), tmp_path / "dest"

    class AlwaysBad(Copier):
        def _install(self, tmp, dst):
            super()._install(tmp, dst)
            if dst.suffix == ".BMP":
                with open(dst, "r+b") as f:
                    f.write(b"\x00\x01\x02")

    res = run(card, dest, cls=AlwaysBad)
    assert res.status == cp.INCOMPLETE
    assert res.failed and res.failed[0][0] == "CONTENTS/ICON/0001AB.BMP"
    assert "CONTENTS/ICON/0001AB.BMP" not in load_manifest(dest / "_p2backup/manifest.xxh64")


def test_card_unplugged_mid_run(tmp_path):
    card, dest = make_card(tmp_path / "card"), tmp_path / "dest"

    def progress(p):
        if p.files_done == 3:
            (card / "LASTCLIP.TXT").unlink(missing_ok=True)
            for f in (card / "CONTENTS").rglob("*.XML"):
                f.unlink()

    res = run(card, dest, progress=progress, clock=iter(range(10**6)).__next__)
    assert res.status == cp.CARD_LOST
    assert not (dest / "_p2backup" / "tmp").exists()

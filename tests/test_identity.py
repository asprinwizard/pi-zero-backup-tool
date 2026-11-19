"""Which folder a card belongs to: sessions, reformats, renames on the PC."""

import json

from p2backup.identity import Library
from p2backup.p2card import inspect
from tests.p2fixtures import add_clip, make_card, set_lastclip, wipe


def lib_with_backup(tmp_path, serial="1A2B-3C4D", card="P2-01"):
    root = make_card(tmp_path / "card", clips=[1, 2, 3])
    lib = Library(tmp_path / "data")
    (tmp_path / "data").mkdir()
    lib.scan()
    s = lib.create(inspect(root, 1, serial), card)
    return lib, root, s


def test_new_session_folder_name_and_json(tmp_path):
    lib, root, s = lib_with_backup(tmp_path)
    assert s.path == tmp_path / "data" / "P2-01" / "2026-11-14_1A2B-3C4D"
    data = json.loads((s.path / "_p2backup" / "session.json").read_text())
    assert data["volume_serial"] == "1A2B-3C4D" and len(data["umids"]) == 3
    assert data["card"] == "P2-01" and data["slot"] == 1


def test_same_card_matches_after_rescan(tmp_path):
    lib, root, s = lib_with_backup(tmp_path)
    add_clip(root, 4)                                    # more recording, same format
    lib2 = Library(lib.root)
    lib2.scan()
    assert lib2.match(inspect(root, 1, "1A2B-3C4D")).path == s.path


def test_reformat_with_new_serial_is_a_new_session(tmp_path):
    lib, root, _ = lib_with_backup(tmp_path)
    wipe(root)
    add_clip(root, 1, date="2026-11-20")
    assert lib.match(inspect(root, 1, "9999-0000")) is None


def test_reformat_same_serial_new_umids_is_a_new_session(tmp_path):
    lib, root, _ = lib_with_backup(tmp_path)
    wipe(root)
    add_clip(root, 50)                                   # different UMID
    assert lib.match(inspect(root, 1, "1A2B-3C4D")) is None


def test_reformat_same_serial_empty_card_is_a_new_session(tmp_path):
    lib, root, _ = lib_with_backup(tmp_path)
    wipe(root)                                           # LASTCLIP blank
    assert lib.match(inspect(root, 1, "1A2B-3C4D")) is None


def test_empty_card_same_serial_counter_not_lower_matches(tmp_path):
    lib, root, s = lib_with_backup(tmp_path)
    import shutil
    shutil.rmtree(root / "CONTENTS" / "CLIP")            # clips deleted in camera, no format
    set_lastclip(root, "0003AB")
    assert lib.match(inspect(root, 1, "1A2B-3C4D")).path == s.path


def test_folder_renamed_or_moved_on_pc_is_picked_up(tmp_path):
    lib, root, s = lib_with_backup(tmp_path)
    moved = lib.root / "P2-07" / "my shoot"
    moved.parent.mkdir()
    s.path.rename(moved)
    lib2 = Library(lib.root)
    lib2.scan()
    m = lib2.match(inspect(root, 1, "1A2B-3C4D"))
    assert m.path == moved and m.card == "P2-07"
    cache = json.loads((lib.root / "_p2backup" / "cards.json").read_text())
    assert cache["sessions"][0]["folder"] == "P2-07/my shoot"


def test_reassign_moves_folder(tmp_path):
    lib, root, s = lib_with_backup(tmp_path)
    lib.reassign(s, "P2-02")
    assert s.path == lib.root / "P2-02" / "2026-11-14_1A2B-3C4D" and s.path.is_dir()
    assert json.loads((s.path / "_p2backup/session.json").read_text())["card"] == "P2-02"


def test_unique_suffix_and_unsorted(tmp_path):
    lib, root, s = lib_with_backup(tmp_path)
    s2 = lib.create(inspect(root, 1, "1A2B-3C4D"), "P2-01")
    assert s2.path.name == "2026-11-14_1A2B-3C4D_2"
    s3 = lib.create(inspect(root, 1, "1A2B-3C4D"), None)
    assert s3.path.parent.name == "UNSORTED" and s3.card is None


def test_choices(tmp_path):
    lib, root, _ = lib_with_backup(tmp_path, card="P2-03")
    lib.create(inspect(root, 2, "5555-6666"), "P2-01")
    assert lib.recent_cards() == ["P2-01", "P2-03"]
    assert lib.last_card_in_slot(1) == "P2-03"
    assert lib.next_card() == "P2-04"

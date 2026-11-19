"""A whole run: records in session.json and backups.csv, and the command line."""

import csv
import json

from p2backup import backup
from p2backup.identity import Library
from p2backup.p2card import inspect
from tests.p2fixtures import add_clip, card_rel_files, make_card

PLENTY = lambda p: 10**12        # noqa: E731


def test_run_records_session_and_csv(tmp_path):
    card = make_card(tmp_path / "card")
    lib = Library(tmp_path / "data")
    (tmp_path / "data").mkdir()
    lib.scan()
    info = inspect(card, 1, "1A2B-3C4D")
    s = lib.create(info, "P2-01")
    s.data["runs"] = [{"run": "old", "status": "running"}]     # left by a power cut
    res = backup.run_backup(info, lib, s, run_id="r1", free_bytes=PLENTY)
    assert res.ok
    data = json.loads((s.path / "_p2backup/session.json").read_text())
    assert [r["status"] for r in data["runs"]] == ["interrupted", "verified"]
    rows = list(csv.DictReader(open(lib.root / "backups.csv")))
    assert rows[0]["card"] == "P2-01" and rows[0]["status"] == "verified"
    assert rows[0]["folder"] == "P2-01/2026-11-14_1A2B-3C4D"
    assert "Card can be formatted" in backup.summary(res)


def test_summary_when_incomplete_says_do_not_format():
    res = backup.Result("r", status="card_lost", message="Card disconnected")
    assert "Do NOT format this card." in backup.summary(res)
    assert not any("can be formatted" in line for line in backup.summary(res))


def test_cli_first_run_asks_then_backs_up_then_is_incremental(tmp_path, capsys):
    card, data = make_card(tmp_path / "card"), tmp_path / "data"
    data.mkdir()
    argv = ["--data", str(data), "run", "--source", str(card), "--serial", "1a2b-3c4d"]
    assert backup.main(argv) == 2
    assert "--card" in capsys.readouterr().out

    assert backup.main(argv + ["--card", "new"]) == 0
    out = capsys.readouterr().out
    assert "Card can be formatted" in out
    dest = data / "P2-01" / "2026-11-14_1A2B-3C4D"
    assert {r for r in card_rel_files(card)} <= {
        p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}

    add_clip(card, 9)
    assert backup.main(argv) == 0                       # known card now, no --card needed
    out = capsys.readouterr().out
    assert "Known card: P2-01/2026-11-14_1A2B-3C4D" in out
    assert "7 copied" in out

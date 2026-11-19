"""Which backup folder a card belongs to. Plan §3.5 and §6.2.

A *session* is one format of one card, kept in a folder

    P2BACKUP/P2-nn/<camera-date>_<volume-serial>[_n]/   (or UNSORTED/...)

whose `_p2backup/session.json` is the source of truth. The card number comes
from the folder the session sits in, so a PC can rename or move folders and
the next scan simply picks that up. `P2BACKUP/_p2backup/cards.json` is a cache
written after every scan, never read for decisions.

Matching: same session if the volume serial matches AND (the card has no clips,
or at least one clip UMID is already in the session). A blank LASTCLIP.TXT or a
lower clip counter on an empty card means it was reformatted: new session.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import atomic_write_json, unique_path
from .p2card import CardInfo, lastclip_counter

log = logging.getLogger("p2backup.identity")

META = "_p2backup"
SESSION_FILE = f"{META}/session.json"
UNSORTED = "UNSORTED"
CARD_DIR = re.compile(r"^P2-(\d+)$")
FORMAT = 1


def card_name(n: int) -> str:
    return f"P2-{n:02d}"


@dataclass
class Session:
    path: Path
    data: dict = field(default_factory=dict)

    @property
    def card(self) -> str | None:
        """'P2-03', or None for UNSORTED or any folder not named like a card."""
        name = self.path.parent.name
        return name if CARD_DIR.match(name) else None

    @property
    def volume_serial(self) -> str:
        return self.data.get("volume_serial", "")

    @property
    def umids(self) -> set[str]:
        return set(self.data.get("umids", []))

    @property
    def seq(self) -> int:
        return int(self.data.get("seq", 0))

    @property
    def meta(self) -> Path:
        return self.path / META

    def save(self) -> None:
        self.data["card"] = self.card
        atomic_write_json(self.path / SESSION_FILE, self.data)


class Library:
    """All sessions on the data area."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.sessions: list[Session] = []

    # ---- scan ----------------------------------------------------------
    def scan(self) -> list[Session]:
        sessions = []
        for top in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if not top.is_dir() or top.name.startswith((".", "_")):
                continue
            for d in sorted(top.iterdir()):
                f = d / SESSION_FILE
                if not f.is_file():
                    continue
                try:
                    data = json.loads(f.read_text())
                except (OSError, ValueError) as e:
                    log.warning("ignoring unreadable %s: %s", f, e)
                    continue
                sessions.append(Session(d, data))
        self.sessions = sessions
        self._write_cache()
        return sessions

    def _write_cache(self) -> None:
        cache = [{"folder": s.path.relative_to(self.root).as_posix(), "card": s.card,
                  "volume_serial": s.volume_serial, "clips": len(s.umids), "seq": s.seq}
                 for s in self.sessions]
        try:
            atomic_write_json(self.root / META / "cards.json", {"sessions": cache})
        except OSError as e:
            log.warning("could not write cards.json: %s", e)

    # ---- matching ------------------------------------------------------
    def match(self, info: CardInfo) -> Session | None:
        if not info.volume_serial:
            return None
        same_serial = [s for s in self.sessions if s.volume_serial == info.volume_serial]
        if not same_serial:
            return None
        umids = info.umids
        if umids:
            overlapping = [(len(umids & s.umids), s.seq, s) for s in same_serial
                           if umids & s.umids]
            return max(overlapping, key=lambda t: t[:2])[2] if overlapping else None
        # No clips on the card: same session unless LASTCLIP shows a reformat.
        best = max(same_serial, key=lambda s: s.seq)
        old, new = lastclip_counter(best.data.get("lastclip", "")), lastclip_counter(info.lastclip)
        if old is not None and (new is None or new < old):
            return None
        return best

    # ---- choices for the "Which card is this?" prompt -------------------
    def recent_cards(self) -> list[str]:
        """Card numbers, most recently used first."""
        best: dict[str, int] = {}
        for s in self.sessions:
            if s.card:
                best[s.card] = max(best.get(s.card, -1), s.seq)
        return sorted(best, key=lambda c: -best[c])

    def last_card_in_slot(self, slot: int) -> str | None:
        in_slot = [s for s in self.sessions if s.card and s.data.get("slot") == slot]
        return max(in_slot, key=lambda s: s.seq).card if in_slot else None

    def next_card(self) -> str:
        nums = [int(m.group(1)) for d in self.root.iterdir() if d.is_dir()
                if (m := CARD_DIR.match(d.name))] if self.root.is_dir() else []
        return card_name(max(nums, default=0) + 1)

    def _next_seq(self) -> int:
        return max((s.seq for s in self.sessions), default=0) + 1

    # ---- changes -------------------------------------------------------
    def create(self, info: CardInfo, card: str | None) -> Session:
        """A new session folder for this card under P2-nn (or UNSORTED if card is None)."""
        date = info.first_date or time.strftime("%Y-%m-%d") + "-nodate"
        serial = info.volume_serial or "noserial"
        parent = self.root / (card or UNSORTED)
        path = unique_path(parent / f"{date}_{serial}")
        path.mkdir(parents=True)
        s = Session(path, {"format": FORMAT, "volume_serial": info.volume_serial, "umids": [],
                           "lastclip": "", "camera_date": info.first_date,
                           "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": []})
        self.record_card(s, info)
        self.sessions.append(s)
        log.info("new session %s", path.relative_to(self.root))
        return s

    def record_card(self, s: Session, info: CardInfo) -> None:
        """Remember what's on the card now (UMIDs only ever accumulate)."""
        s.data["umids"] = sorted(s.umids | info.umids)
        s.data["lastclip"] = info.lastclip
        s.data["slot"] = info.slot
        s.data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        s.data["seq"] = self._next_seq()
        s.save()

    def reassign(self, s: Session, card: str | None) -> Session:
        """Move a session folder to another card number (a rename: instant)."""
        parent = self.root / (card or UNSORTED)
        parent.mkdir(parents=True, exist_ok=True)
        new = unique_path(parent / s.path.name)
        s.path.rename(new)
        log.info("moved %s -> %s", s.path.relative_to(self.root), new.relative_to(self.root))
        s.path = new
        s.save()
        self._write_cache()
        return s

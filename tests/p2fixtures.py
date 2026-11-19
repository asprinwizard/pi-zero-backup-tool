"""Fake P2 cards for tests (the builder lives in the package so --sim can use it)."""

from p2backup.simcards import (  # noqa: F401
    MTIME,
    add_clip,
    card_rel_files,
    make_card,
    set_lastclip,
    umid,
    wipe,
)

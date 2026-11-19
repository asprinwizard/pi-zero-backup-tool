"""Screen rendering: pure functions from state to a 128x64 PIL image.

Plan §7.1. Every function returns a 1-bit 128x64 image; none of them
change state, so they can be tested (and previewed) on their own.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .display import HEIGHT, WIDTH


@lru_cache(maxsize=4)
def font(size: int = 11):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def blank():
    img = Image.new("1", (WIDTH, HEIGHT), 0)
    return img, ImageDraw.Draw(img)


def header(draw, title: str, right: str = "") -> None:
    draw.rectangle((0, 0, WIDTH - 1, 12), fill=1)
    draw.text((2, 0), title, font=font(11), fill=0)
    if right:
        w = draw.textlength(right, font=font(11))
        draw.text((WIDTH - 2 - w, 0), right, font=font(11), fill=0)


def home_phase1(last_input: str | None, version: str, ip: str | None = None) -> Image.Image:
    img, d = blank()
    header(d, "P2 BACKUP", f"v{version}")
    d.text((2, 18), f"IP {ip}" if ip else "No network", font=font(11), fill=1)
    d.text((2, 34), "Last input:", font=font(11), fill=1)
    d.text((2, 48), last_input or "-", font=font(11), fill=1)
    return img


ROW_H = 13


VISIBLE_ROWS = 3


def menu(title: str, items: list[str], selected: int, right: str = "") -> Image.Image:
    """A list with the selected row shown inverted; scrolls if it has more than 3 rows."""
    img, d = blank()
    header(d, title, right)
    first = min(max(0, selected - VISIBLE_ROWS + 1), max(0, len(items) - VISIBLE_ROWS))
    for i, text in enumerate(items[first:first + VISIBLE_ROWS], start=first):
        y = 14 + (i - first) * ROW_H
        if i == selected:
            d.rectangle((0, y, WIDTH - 1, y + ROW_H - 1), fill=1)
        d.text((4, y), text, font=font(11), fill=0 if i == selected else 1)
    if first > 0:
        d.text((WIDTH - 8, 14), "^", font=font(11), fill=0 if selected == first else 1)
    if first + VISIBLE_ROWS < len(items):
        d.text((WIDTH - 8, 14 + 2 * ROW_H), "v", font=font(11),
               fill=0 if selected == first + VISIBLE_ROWS - 1 else 1)
    return img


def confirm(question: str, yes: bool, detail: str = "") -> Image.Image:
    """Question (plus an optional detail line) with No / Yes buttons; the selected one is
    inverted."""
    img, d = blank()
    header(d, "Confirm")
    lines = [question, detail] if detail else [question]
    for i, text in enumerate(lines):
        w = d.textlength(text, font=font(11))
        d.text(((WIDTH - w) // 2, (20 if not detail else 15) + i * 13), text, font=font(11),
               fill=1)
    for label, x, chosen in (("No", 14, not yes), ("Yes", 74, yes)):
        box = (x, 44, x + 40, 60)
        d.rectangle(box, outline=1, fill=1 if chosen else 0)
        lw = d.textlength(label, font=font(11))
        d.text((x + (40 - lw) // 2, 45), label, font=font(11), fill=0 if chosen else 1)
    return img


def message(lines: list[str]) -> Image.Image:
    """Centred lines of text, nothing else."""
    img, d = blank()
    top = (HEIGHT - 16 * len(lines)) // 2
    for i, text in enumerate(lines):
        w = d.textlength(text, font=font(13))
        d.text(((WIDTH - w) // 2, top + i * 16), text, font=font(13), fill=1)
    return img


def info(title: str, lines: list[str], right: str = "") -> Image.Image:
    """Header plus up to four lines of text."""
    img, d = blank()
    header(d, title, right)
    for i, text in enumerate(lines[:4]):
        d.text((2, 15 + i * 12), text, font=font(11), fill=1)
    return img


def big(title: str, text: str, right: str = "") -> Image.Image:
    """Header plus one large centred line (e.g. "Safe to unplug")."""
    img, d = blank()
    header(d, title, right)
    w = d.textlength(text, font=font(13))
    d.text(((WIDTH - w) // 2, 32), text, font=font(13), fill=1)
    return img


def alert(title: str, lines: list[str]) -> Image.Image:
    """Inverted screen for warnings that must not be missed."""
    img, d = blank()
    d.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=1)
    w = d.textlength(title, font=font(13))
    d.text(((WIDTH - w) // 2, 1), title, font=font(13), fill=0)
    for i, text in enumerate(lines[:3]):
        d.text((3, 20 + i * 13), text, font=font(11), fill=0)
    return img


# ---- Phase 4: camera side ------------------------------------------------------

def gb(n: int) -> str:
    return f"{n / 1e9:.1f}G"


def card_row(v) -> str:
    """One line for a slot in the card list (v is a cards.CardView)."""
    s = f"S{v.slot} "
    if not v.present:
        return s + "empty"
    if v.error:
        return s + "can't read"
    if not v.is_p2:
        return s + "not a P2 card"
    if not v.matched:
        return s + f"new card {gb(v.bytes_total)}"
    name = v.card or "UNSORTED"
    if v.backed_up:
        return s + f"{name} all done"
    return s + f"{name} {gb(v.bytes_todo)} new"


def card_list(cards, selected: int, free: str, right: str = "") -> Image.Image:
    """Cards in the camera, selected one inverted, free space and key hints."""
    img, d = blank()
    header(d, "CAMERA", right)
    if not cards:
        d.text((2, 15), "Reading cards...", font=font(11), fill=1)
    for i, v in enumerate(cards[:2]):
        y = 14 + i * ROW_H
        if i == selected:
            d.rectangle((0, y, WIDTH - 1, y + ROW_H - 1), fill=1)
        d.text((2, y), card_row(v), font=font(11), fill=0 if i == selected else 1)
    d.text((2, 40), free, font=font(11), fill=1)
    d.text((2, 52), "K1/K2: back up S1/S2", font=font(11), fill=1)
    return img


def fmt_eta(s: float | None) -> str:
    if s is None:
        return "--:--"
    s = int(s)
    if s >= 3600:
        return f"{s // 3600}h{s // 60 % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def backing_up(card: str, p, right: str = "", cancelling: bool = False) -> Image.Image:
    """Progress of a running backup (p is a copier.Progress, or None before the first)."""
    img, d = blank()
    header(d, f"BACKUP {card}", right)
    if p is None:
        d.text((2, 15), "Starting...", font=font(11), fill=1)
        return img
    name = p.current.rsplit("/", 1)[-1] or "Finishing..."
    d.text((2, 14), name[:21], font=font(11), fill=1)
    d.rectangle((2, 28, WIDTH - 3, 36), outline=1)
    d.rectangle((2, 28, 2 + int((WIDTH - 5) * min(1.0, p.fraction)), 36), fill=1)
    d.text((2, 39), f"{p.bytes_done / 1e9:.1f}/{p.bytes_total / 1e9:.1f}GB "
                    f"{p.rate / 1e6:.0f}MB/s", font=font(11), fill=1)
    tail = "Cancelling..." if cancelling else "key: cancel"
    d.text((2, 51), f"{p.fraction * 100:3.0f}% {fmt_eta(p.eta_s)} {tail}", font=font(11), fill=1)
    return img

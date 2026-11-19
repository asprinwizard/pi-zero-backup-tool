"""Screen rendering: pure functions from state to a 128x64 PIL image.

Plan §7.1. Every function returns a 1-bit 128x64 image; none of them
change state, so they can be tested (and previewed) on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Status:
    """Header icons: Wi-Fi when there's a network, battery level when known."""
    wifi: bool = False
    battery: int | None = None


def wifi_icon(draw, x: int, y: int, fill: int) -> int:
    """Three arcs and a dot, 11x8, top-left at (x, y). Returns the width."""
    for r in (2, 5, 8):
        cx, cy = x + 5, y + 8
        draw.arc((cx - r, cy - r, cx + r, cy + r), 225, 315, fill=fill)
    draw.point((x + 5, y + 7), fill=fill)
    draw.point((x + 5, y + 6), fill=fill)
    return 11


def battery_icon(draw, x: int, y: int, pct: int, fill: int) -> int:
    """Outline battery 13x7 with a level bar. Returns the width."""
    draw.rectangle((x, y, x + 11, y + 6), outline=fill)
    draw.rectangle((x + 12, y + 2, x + 12, y + 4), fill=fill)
    level = round(9 * max(0, min(100, pct)) / 100)
    if level:
        draw.rectangle((x + 1, y + 1, x + level, y + 5), fill=fill)
    return 13


def header(draw, title: str, right: str | Status = "") -> None:
    draw.rectangle((0, 0, WIDTH - 1, 12), fill=1)
    draw.text((2, 0), title, font=font(11), fill=0)
    x = WIDTH - 2
    if isinstance(right, Status):
        if right.battery is not None:
            text = f"{right.battery}%"
            x -= draw.textlength(text, font=font(11))
            draw.text((x, 0), text, font=font(11), fill=0)
            x -= 15
            battery_icon(draw, int(x), 3, right.battery, 0)
        if right.wifi:
            x -= 14
            wifi_icon(draw, int(x), 2, 0)
    elif right:
        w = draw.textlength(right, font=font(11))
        draw.text((x - w, 0), right, font=font(11), fill=0)


def marker(draw, x: int, y: int, fill: int) -> None:
    """The small triangle that marks something you can select."""
    draw.polygon([(x, y + 3), (x, y + 9), (x + 3, y + 6)], fill=fill)


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


def info(title: str, lines: list[str], right: str | Status = "") -> Image.Image:
    """Header plus up to four lines of text."""
    img, d = blank()
    header(d, title, right)
    for i, text in enumerate(lines[:4]):
        d.text((2, 15 + i * 12), text, font=font(11), fill=1)
    return img


def big(title: str, text: str, right: str | Status = "") -> Image.Image:
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
    name = v.card or "Unsorted"
    if v.backed_up:
        return s + f"{name} all done"
    return s + f"{name} {gb(v.bytes_todo)} new"


def selectable_row(d, y: int, text: str, selected: bool) -> None:
    if selected:
        d.rectangle((0, y, WIDTH - 1, y + ROW_H - 1), fill=1)
    marker(d, 2, y, 0 if selected else 1)
    d.text((8, y), text, font=font(11), fill=0 if selected else 1)


def footer(d, free: str, about_selected: bool) -> None:
    """Separator, free space, and the selectable About entry."""
    d.line((0, 41, WIDTH - 1, 41), fill=1)
    d.text((2, 47), free, font=font(11), fill=1)
    w = d.textlength("About", font=font(11))
    x = WIDTH - w - 10
    if about_selected:
        d.rectangle((x - 2, 46, WIDTH - 1, 46 + ROW_H - 1), fill=1)
    marker(d, x, 46, 0 if about_selected else 1)
    d.text((x + 6, 47), "About", font=font(11), fill=0 if about_selected else 1)


def card_list(cards, selected: int, free: str, right: str | Status = "") -> Image.Image:
    """Cards in the camera (selectable), separator, free space and About.
    selected: index into cards, or len(cards) for About."""
    img, d = blank()
    header(d, "CAMERA", right)
    if not cards:
        d.text((8, 15), "Reading cards...", font=font(11), fill=1)
    for i, v in enumerate(cards[:2]):
        selectable_row(d, 14 + i * ROW_H, card_row(v), i == selected)
    footer(d, free, selected >= len(cards[:2]))
    return img


def idle(lines: list[str], free: str, right: str | Status = "") -> Image.Image:
    """Nothing connected: status lines, then free space and About (always selected:
    it's the only thing to choose here)."""
    img, d = blank()
    header(d, "P2 BACKUP", right)
    for i, text in enumerate(lines[:2]):
        d.text((2, 15 + i * 12), text, font=font(11), fill=1)
    footer(d, free, True)
    return img


def about(lines: list[str]) -> Image.Image:
    return info("ABOUT", lines, "any key: back")


def fmt_eta(s: float | None) -> str:
    if s is None:
        return "--:--"
    s = int(s)
    if s >= 3600:
        return f"{s // 3600}h{s // 60 % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def backing_up(card: str, p, right: str | Status = "", cancelling: bool = False) -> Image.Image:
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

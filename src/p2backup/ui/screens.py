"""Screen rendering: pure functions from state to a 128x64 PIL image.

Phase 1 only has the placeholder home screen; the full set (plan §7.1)
arrives in Phase 4.
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


def menu(title: str, items: list[str], selected: int) -> Image.Image:
    """A list with the selected row shown inverted."""
    img, d = blank()
    header(d, title)
    for i, text in enumerate(items):
        y = 14 + i * ROW_H
        if i == selected:
            d.rectangle((0, y, WIDTH - 1, y + ROW_H - 1), fill=1)
        d.text((4, y), text, font=font(11), fill=0 if i == selected else 1)
    return img


def confirm(question: str, yes: bool) -> Image.Image:
    """Question with No / Yes buttons; the selected one is inverted."""
    img, d = blank()
    header(d, "Confirm")
    w = d.textlength(question, font=font(11))
    d.text(((WIDTH - w) // 2, 20), question, font=font(11), fill=1)
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

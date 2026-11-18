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

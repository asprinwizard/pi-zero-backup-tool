"""Boot / shutdown splash screens.

    python -m p2backup.splash render --out /opt/p2backup/splash   (install time, needs PIL)
    python -m p2backup.splash show starting                       (boot, no PIL needed)
    python -m p2backup.splash preview starting                    (terminal preview)

`show` must be fast and robust very early in boot: it imports only the tiny
SH1106 driver and waits for the SPI/GPIO device nodes to appear.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .ui import sh1106

DEFAULT_DIR = Path("/opt/p2backup/splash")
DEVICES = ("/dev/spidev0.0", "/dev/gpiochip0")

MESSAGES: dict[str, list[str]] = {
    "starting": ["P2 BACKUP", "", "Starting up..."],
    "stopping": ["P2 BACKUP", "", "Shutting down..."],
    "restarting": ["P2 BACKUP", "", "Restarting..."],
    "safe": ["", "Safe to", "switch off"],
}


def render_message(lines: list[str]):
    """Draw centred lines on a 128x64 1-bit image. Needs PIL."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("1", (sh1106.WIDTH, sh1106.HEIGHT), 0)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=13)
    except TypeError:                   # Pillow < 10.1 has no size argument
        font = ImageFont.load_default()
    line_h = 16
    top = (sh1106.HEIGHT - line_h * len(lines)) // 2
    for i, text in enumerate(lines):
        if not text:
            continue
        w = draw.textlength(text, font=font)
        draw.text(((sh1106.WIDTH - w) // 2, top + i * line_h), text, font=font, fill=1)
    return img


def render_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, lines in MESSAGES.items():
        path = out_dir / f"{name}.bin"
        path.write_bytes(sh1106.pack(render_message(lines)))
        written.append(path)
    return written


def wait_for_devices(timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if all(Path(d).exists() for d in DEVICES):
            return True
        time.sleep(0.1)
    return False


def show(name: str, image_dir: Path = DEFAULT_DIR) -> int:
    buf = (image_dir / f"{name}.bin").read_bytes()
    if not wait_for_devices():
        print("splash: SPI/GPIO devices not available", file=sys.stderr)
        return 1
    oled = sh1106.SH1106()
    try:
        oled.init()
        oled.show(buf)
    finally:
        oled.close()
    return 0


def ascii_preview(buf: bytes) -> str:
    rows = sh1106.unpack(buf)
    # Two pixel rows per text line keeps the preview roughly square.
    chars = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    lines = []
    for y in range(0, sh1106.HEIGHT, 2):
        lines.append("".join(chars[(rows[y][x], rows[y + 1][x])] for x in range(sh1106.WIDTH)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="p2backup.splash")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", type=Path, default=DEFAULT_DIR)
    s = sub.add_parser("show")
    s.add_argument("name", choices=sorted(MESSAGES))
    s.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    v = sub.add_parser("preview")
    v.add_argument("name", choices=sorted(MESSAGES))
    args = p.parse_args(argv)

    if args.cmd == "render":
        for path in render_all(args.out):
            print(path)
        return 0
    if args.cmd == "show":
        return show(args.name, args.dir)
    print(ascii_preview(sh1106.pack(render_message(MESSAGES[args.name]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

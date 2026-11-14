"""Minimal SH1106 128x64 OLED driver over SPI (Waveshare 1.3in OLED HAT).

Deliberately tiny and dependency-light (spidev + lgpio only) so the boot
splash can use it without importing PIL. The main app draws with PIL and
passes the packed buffer here.
"""

from __future__ import annotations

import time

WIDTH = 128
HEIGHT = 64
PAGES = HEIGHT // 8
COL_OFFSET = 2          # SH1106 RAM is 132 columns wide; the panel starts at column 2
BUFFER_SIZE = WIDTH * PAGES

# Waveshare HAT wiring (BCM numbers)
DC_PIN = 24
RST_PIN = 25

INIT_SEQUENCE = [
    0xAE,               # display off
    0xD5, 0x80,         # clock divide
    0xA8, 0x3F,         # multiplex 64
    0xD3, 0x00,         # display offset 0
    0x40,               # start line 0
    0xAD, 0x8B,         # DC-DC on
    0xA1,               # segment remap (mirror X)   } orientation: verify on the
    0xC8,               # COM scan descending        } real HAT, see FLIP below
    0xDA, 0x12,         # COM pins
    0x81, 0x80,         # contrast
    0xD9, 0x22,         # pre-charge
    0xDB, 0x35,         # VCOM deselect
    0xA4,               # display follows RAM
    0xA6,               # normal (not inverted)
]
# Commands to rotate 180 degrees instead, if the image comes out upside down.
FLIP = {0xA1: 0xA0, 0xC8: 0xC0}


def pack(pixels) -> bytes:
    """Pack a 128x64 monochrome image into SH1106 page order.

    `pixels` is anything indexable as pixels[y][x] -> truthy (lit) / falsy,
    or a PIL image (mode "1" or "L"; non-zero = lit).
    """
    if hasattr(pixels, "getpixel"):
        img = pixels.convert("1") if pixels.mode != "1" else pixels
        if img.size != (WIDTH, HEIGHT):
            raise ValueError(f"image must be {WIDTH}x{HEIGHT}, got {img.size}")
        data = img.tobytes()            # rows, 1 bit per pixel, MSB first, 16 bytes per row
        stride = WIDTH // 8

        def lit(x, y):
            return (data[y * stride + (x >> 3)] >> (7 - (x & 7))) & 1
    else:
        def lit(x, y):
            return 1 if pixels[y][x] else 0

    out = bytearray(BUFFER_SIZE)
    for page in range(PAGES):
        base = page * WIDTH
        y0 = page * 8
        for x in range(WIDTH):
            b = 0
            for bit in range(8):
                if lit(x, y0 + bit):
                    b |= 1 << bit
            out[base + x] = b
    return bytes(out)


def unpack(buf: bytes) -> list[list[int]]:
    """Inverse of pack(), for tests and terminal previews."""
    rows = [[0] * WIDTH for _ in range(HEIGHT)]
    for page in range(PAGES):
        for x in range(WIDTH):
            b = buf[page * WIDTH + x]
            for bit in range(8):
                rows[page * 8 + bit][x] = (b >> bit) & 1
    return rows


class SH1106:
    def __init__(self, bus: int = 0, device: int = 0, dc: int = DC_PIN, rst: int = RST_PIN,
                 speed_hz: int = 8_000_000, gpiochip: int = 0, flip: bool = False):
        import lgpio
        import spidev

        self._lg = lgpio
        self._dc = dc
        self._rst = rst
        self._h = lgpio.gpiochip_open(gpiochip)
        lgpio.gpio_claim_output(self._h, dc, 0)
        lgpio.gpio_claim_output(self._h, rst, 1)
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = speed_hz
        self._spi.mode = 0
        self._flip = flip

    def _command(self, *values: int) -> None:
        self._lg.gpio_write(self._h, self._dc, 0)
        self._spi.writebytes(list(values))

    def _data(self, values: bytes) -> None:
        self._lg.gpio_write(self._h, self._dc, 1)
        self._spi.writebytes2(values)

    def init(self) -> None:
        self._lg.gpio_write(self._h, self._rst, 0)
        time.sleep(0.01)
        self._lg.gpio_write(self._h, self._rst, 1)
        time.sleep(0.01)
        seq = [FLIP.get(c, c) for c in INIT_SEQUENCE] if self._flip else INIT_SEQUENCE
        self._command(*seq)
        self.show(bytes(BUFFER_SIZE))
        self._command(0xAF)             # display on

    def show(self, buf: bytes) -> None:
        if len(buf) != BUFFER_SIZE:
            raise ValueError("buffer must be 1024 bytes")
        for page in range(PAGES):
            self._command(0xB0 | page, COL_OFFSET & 0x0F, 0x10 | (COL_OFFSET >> 4))
            self._data(buf[page * WIDTH:(page + 1) * WIDTH])

    def contrast(self, level: int) -> None:
        self._command(0x81, max(0, min(255, level)))

    def close(self) -> None:
        """Release SPI/GPIO without clearing the screen (the image stays)."""
        try:
            self._spi.close()
        finally:
            self._lg.gpiochip_close(self._h)

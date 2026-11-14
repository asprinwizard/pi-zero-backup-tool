"""Display back-ends: the real OLED, and a simulator for development off the Pi."""

from __future__ import annotations

from pathlib import Path

from . import sh1106

WIDTH, HEIGHT = sh1106.WIDTH, sh1106.HEIGHT


class OledDisplay:
    def __init__(self, flip: bool = False):
        self._dev = sh1106.SH1106(flip=flip)
        self._dev.init()
        self._last: bytes | None = None

    def show(self, image) -> None:
        buf = sh1106.pack(image)
        if buf != self._last:
            self._dev.show(buf)
            self._last = buf

    def close(self) -> None:
        self._dev.close()


class SimDisplay:
    """Prints each new frame to the terminal and optionally saves it as a PNG."""

    def __init__(self, png_path: Path | None = None, terminal: bool = True):
        self._png = png_path
        self._terminal = terminal
        self._last: bytes | None = None

    def show(self, image) -> None:
        buf = sh1106.pack(image)
        if buf == self._last:
            return
        self._last = buf
        if self._png:
            image.convert("1").resize((WIDTH * 4, HEIGHT * 4)).save(self._png)
        if self._terminal:
            from ..splash import ascii_preview
            border = "+" + "-" * WIDTH + "+"
            body = "\n".join(f"|{line}|" for line in ascii_preview(buf).splitlines())
            print(f"{border}\n{body}\n{border}", flush=True)

    def close(self) -> None:
        pass

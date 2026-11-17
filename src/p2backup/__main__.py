"""Entry point: python -m p2backup [--sim] [--config PATH] [--png PATH]"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
from pathlib import Path

from . import config as config_mod
from .app import App


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="p2backup")
    p.add_argument("--sim", action="store_true",
                   help="run off the Pi: terminal display and keyboard input")
    p.add_argument("--config", type=Path, default=None,
                   help="settings file (default /etc/p2backup.toml; none in --sim)")
    p.add_argument("--png", type=Path, default=None, help="--sim: also save frames to this PNG")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg_path = args.config if args.config else (None if args.sim else config_mod.DEFAULT_PATH)
    cfg = config_mod.load(cfg_path)

    events: queue.Queue = queue.Queue()
    if args.sim:
        from .ui.display import SimDisplay
        from .ui.inputs import KEYBOARD_HELP, KeyboardInputs
        display = SimDisplay(png_path=args.png)
        KeyboardInputs(events)
        print(KEYBOARD_HELP, flush=True)
    else:
        from .ui.display import OledDisplay
        from .ui.inputs import GpioInputs, pins_for
        display = OledDisplay(flip=cfg.display.flip)
        GpioInputs(events, pins=pins_for(cfg.display.flip))

    try:
        App(cfg, display, events).run()
    except KeyboardInterrupt:
        pass
    finally:
        display.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
        from .power import Reading, UpsEvent
        from .storage import FakeStorage
        from .ui.display import SimDisplay
        from .ui.inputs import KEYBOARD_HELP, KeyboardInputs
        from .usb import FakeUsb

        display = SimDisplay(png_path=args.png)
        usb = gadget = FakeUsb(events.put)
        storage = FakeStorage()
        commands = {
            "pc": usb.plug_pc, "eject": usb.eject, "unplug": usb.unplug, "cam": usb.plug_camera,
            "upson": lambda: events.put(UpsEvent(True, Reading(3.9, -450))),
            "upsoff": lambda: events.put(UpsEvent(False, Reading(3.9, 0))),
        }
        KeyboardInputs(events, commands=commands)
        print(KEYBOARD_HELP + "\nsim: pc, eject, unplug, cam, upson, upsoff", flush=True)
        ups = None
    else:
        from .power import open_monitor
        from .storage import Storage
        from .ui.display import OledDisplay
        from .ui.inputs import GpioInputs, pins_for
        from .usb import Gadget, UsbMonitor

        display = OledDisplay(flip=cfg.display.flip)
        GpioInputs(events, pins=pins_for(cfg.display.flip))
        usb, gadget, storage = UsbMonitor(events.put), Gadget(), Storage()
        ups = open_monitor(events.put, cfg.power)

    try:
        app = App(cfg, display, events, usb, gadget, storage)
        if args.sim:
            def fake_power(action: str) -> bool:
                print(f"[sim] would run: systemctl {action}", flush=True)
                return True
            app.power = fake_power
        if ups:
            ups.start()
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        display.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
        cards, sim_cmds = _sim_cards(events)
        commands = {
            "pc": usb.plug_pc, "eject": usb.eject, "unplug": usb.unplug, "cam": usb.plug_camera,
            "upson": lambda: events.put(UpsEvent(True, Reading(3.9, -450))),
            "upsoff": lambda: events.put(UpsEvent(False, Reading(3.9, 0))),
            **sim_cmds,
        }
        KeyboardInputs(events, commands=commands)
        print(KEYBOARD_HELP + "\nsim: pc, eject, unplug, cam, upson, upsoff, "
              + ", ".join(sim_cmds), flush=True)
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
        from .cards import CardWorker
        cards = CardWorker(events.put, Path(storage.mount_point), ready=storage.is_mounted,
                           verify=cfg.backup.verify)

    try:
        app = App(cfg, display, events, usb, gadget, storage, cards=cards)
        cards.start()
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


def _sim_cards(events: queue.Queue):
    """--sim camera: slot 1 holds a small fake card, slot 2 is empty. Cards are
    folders in a temp dir and backups go to another; both are printed."""
    import tempfile

    from .cards import CardWorker, FolderCards
    from .simcards import add_clip, make_card, wipe

    base = Path(tempfile.mkdtemp(prefix="p2sim-"))
    data = base / "P2BACKUP"
    data.mkdir()
    make_card(base / "slot1", clips=range(1, 6))
    fc = FolderCards({1: base / "slot1", 2: base / "slot2"}, {1: "1A2B-3C4D", 2: "5E6F-7A8B"})
    worker = CardWorker(events.put, data, disks=fc.disks, mounter=fc, interval=1.0)
    print(f"[sim] cards in {base}/slot1, slot2; backups in {data}", flush=True)
    clips = {1: 5, 2: 0}

    def changed():
        fc.changes += 1

    def record(slot):
        def go():
            clips[slot] += 1
            add_clip(fc.folders[slot], clips[slot])
            changed()
        return go

    def insert2():
        make_card(fc.folders[2], clips=range(1, 3), date="2026-11-18")
        clips[2] = 2
        changed()

    def format1():
        wipe(fc.folders[1])
        fc.serials[1] = f"{fc.changes:04X}-0000"
        clips[1] = 0
        changed()

    return worker, {"rec1": record(1), "rec2": record(2), "card2": insert2, "format1": format1}


if __name__ == "__main__":
    sys.exit(main())

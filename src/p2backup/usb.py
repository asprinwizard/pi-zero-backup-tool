"""USB connection status (camera / PC / nothing) and the mass-storage gadget.

The role is read from what the kernel already publishes, polled cheaply:
- PC: the gadget's UDC state is "configured" (a PC has enumerated us);
- camera: a USB device exists on the host side (bus devices like "1-1").
GOTGCTL in the dwc2 debugfs regdump is *not* polled: reading it touches host
registers in device mode and floods the log with "Mode Mismatch Interrupt".

The gadget itself is created and bound at boot by p2backup-gadget (always,
with an empty LUN). This module only attaches/detaches the medium; it is the
only code that does so.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("p2backup.usb")

GADGET = "p2backup"
LUN_REL = f"sys/kernel/config/usb_gadget/{GADGET}/functions/mass_storage.usb0/lun.0"
HOST_DEVICE = re.compile(r"^\d+-[\d.]+$")      # "1-1", "1-1.2"; not "usb1" or "1-1:1.0"


@dataclass(frozen=True)
class UsbStatus:
    udc_state: str = "not attached"
    medium: str = ""                        # backing file of the LUN; "" = no medium
    host_devices: tuple[str, ...] = ()

    @property
    def pc(self) -> bool:
        return self.udc_state == "configured"

    @property
    def camera(self) -> bool:
        return bool(self.host_devices)


@dataclass(frozen=True)
class UsbEvent:
    status: UsbStatus


class Gadget:
    """The LUN of the configfs mass-storage gadget."""

    def __init__(self, root: Path = Path("/")):
        self.lun = root / LUN_REL

    def medium(self) -> str:
        try:
            return (self.lun / "file").read_text().strip()
        except OSError:
            return ""

    def attach(self, device: str) -> None:
        (self.lun / "file").write_text(device + "\n")
        log.info("gadget: medium attached (%s)", device)

    def detach(self) -> None:
        """Remove the medium even if the PC has locked it (forced_eject)."""
        forced = self.lun / "forced_eject"
        if forced.exists():
            forced.write_text("1\n")
        else:
            (self.lun / "file").write_text("\n")
        log.info("gadget: medium detached")


def read_status(root: Path = Path("/"), gadget: Gadget | None = None) -> UsbStatus:
    gadget = gadget or Gadget(root)
    state = "not attached"
    for f in sorted((root / "sys/class/udc").glob("*/state")):
        try:
            state = f.read_text().strip()
        except OSError:
            pass
        break
    devices_dir = root / "sys/bus/usb/devices"
    hosts = tuple(sorted(p.name for p in devices_dir.glob("*") if HOST_DEVICE.match(p.name))) \
        if devices_dir.is_dir() else ()
    return UsbStatus(udc_state=state, medium=gadget.medium(), host_devices=hosts)


class UsbMonitor:
    """Polls the status and reports changes through a callback (from a thread)."""

    def __init__(self, emit: Callable[[UsbEvent], None], root: Path = Path("/"),
                 interval: float = 0.5):
        self._emit = emit
        self._root = root
        self._interval = interval
        self._gadget = Gadget(root)

    def current(self) -> UsbStatus:
        return read_status(self._root, self._gadget)

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="usb-monitor").start()

    def _loop(self) -> None:
        last = None
        while True:
            st = self.current()
            if st != last:
                last = st
                self._emit(UsbEvent(st))
            time.sleep(self._interval)


class FakeUsb:
    """--sim and tests: a scripted USB port plus a gadget LUN in memory."""

    def __init__(self, emit: Callable[[UsbEvent], None] | None = None):
        self._emit = emit
        self.status = UsbStatus()

    # Gadget interface
    def medium(self) -> str:
        return self.status.medium

    def attach(self, device: str) -> None:
        self._set(medium=device)

    def detach(self) -> None:
        self._set(medium="")

    # UsbMonitor interface
    def current(self) -> UsbStatus:
        return self.status

    def start(self) -> None:
        pass

    # Scripted events (keyboard commands in --sim)
    def plug_pc(self) -> None:
        self._set(udc_state="configured")

    def eject(self) -> None:
        self._set(medium="")

    def unplug(self) -> None:
        self._set(udc_state="not attached", host_devices=())

    def plug_camera(self) -> None:
        self._set(host_devices=("1-1",))

    def _set(self, **changes) -> None:
        from dataclasses import replace
        self.status = replace(self.status, **changes)
        if self._emit:
            self._emit(UsbEvent(self.status))

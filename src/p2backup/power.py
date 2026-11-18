"""UPS HAT (C) battery monitor (INA219 on I2C) and the "UPS is on" check.

Only the battery side is measured. When the UPS is switched ON it powers the
Pi from the battery, so the battery discharges; switched OFF, it doesn't.
In PC mode with pc_power_protection = "none" a discharging battery means the
UPS is backfeeding the PC, and the screen warns.

The INA219 is only read (shunt and bus voltage registers work with its
power-on configuration), never configured, so this can't upset other tools.

    python -m p2backup.power      print readings once a second (calibration)
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("p2backup.power")

REG_SHUNT, REG_BUS = 0x01, 0x02


@dataclass(frozen=True)
class Reading:
    voltage: float          # battery volts
    current_ma: float       # positive = into the battery (charging), negative = discharging

    @property
    def percent(self) -> int:
        """Rough charge from voltage (3.0-4.2 V, no load correction yet)."""
        return max(0, min(100, round((self.voltage - 3.0) / 1.2 * 100)))


@dataclass(frozen=True)
class UpsEvent:
    discharging: bool
    reading: Reading


def decode(shunt_raw: int, bus_raw: int, shunt_ohms: float) -> Reading:
    """Convert raw INA219 register words (as read big-endian) to a Reading."""
    if shunt_raw & 0x8000:
        shunt_raw -= 0x10000
    shunt_v = shunt_raw * 10e-6                     # LSB 10 uV
    bus_v = (bus_raw >> 3) * 0.004                  # LSB 4 mV
    return Reading(voltage=round(bus_v, 3), current_ma=round(shunt_v / shunt_ohms * 1000, 1))


class Ina219:
    def __init__(self, address: int = 0x43, bus: int = 1, shunt_ohms: float = 0.01):
        from smbus2 import SMBus

        self._bus = SMBus(bus)
        self._addr = address
        self._shunt = shunt_ohms

    def _word(self, reg: int) -> int:
        v = self._bus.read_word_data(self._addr, reg)
        return ((v & 0xFF) << 8) | (v >> 8)         # SMBus is little-endian; INA219 big

    def read(self) -> Reading:
        return decode(self._word(REG_SHUNT), self._word(REG_BUS), self._shunt)


class UpsMonitor:
    """Polls the INA219; reports when 'battery discharging' changes."""

    def __init__(self, emit: Callable[[UpsEvent], None], sensor, threshold_ma: float = 100,
                 interval: float = 2.0):
        self._emit = emit
        self._sensor = sensor
        self._threshold = threshold_ma
        self._interval = interval

    def classify(self, r: Reading) -> bool:
        return r.current_ma <= -self._threshold

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="ups-monitor").start()

    def _loop(self) -> None:
        last = None
        while True:
            try:
                r = self._sensor.read()
            except OSError as e:
                log.warning("INA219 read failed: %s", e)
                time.sleep(10)
                continue
            d = self.classify(r)
            if d != last:
                last = d
                log.info("battery %s: %.2f V, %.0f mA", "discharging" if d else "not discharging",
                         r.voltage, r.current_ma)
                self._emit(UpsEvent(d, r))
            time.sleep(self._interval)


def open_monitor(emit: Callable[[UpsEvent], None], cfg) -> UpsMonitor | None:
    """The monitor, or None if there is no INA219 (no HAT, no I2C, no smbus2)."""
    try:
        sensor = Ina219(cfg.ina219_address, shunt_ohms=cfg.shunt_ohms)
        sensor.read()
    except Exception as e:
        log.warning("no UPS battery monitor: %s", e)
        return None
    return UpsMonitor(emit, sensor, cfg.ups_on_ma)


def main() -> int:
    from .config import load
    cfg = load().power
    sensor = Ina219(cfg.ina219_address, shunt_ohms=cfg.shunt_ohms)
    print(f"INA219 at {cfg.ina219_address:#x}, shunt {cfg.shunt_ohms} ohm. Ctrl+C to stop.")
    try:
        while True:
            r = sensor.read()
            print(f"{r.voltage:5.2f} V  {r.current_ma:8.1f} mA  ~{r.percent:3d}%", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

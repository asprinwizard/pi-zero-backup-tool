"""UPS HAT (C) battery monitor (INA219 on I2C) and the "UPS is on" check.

Only the battery side is measured. When the UPS is switched ON it powers the
Pi from the battery, so the battery discharges; switched OFF, it doesn't.
In PC mode with pc_power_protection = "none" a discharging battery means the
UPS is backfeeding the PC, and the screen warns.

The INA219 is only read (shunt and bus voltage registers work with its
power-on configuration), never configured, so this can't upset other tools.

Battery %: the cell voltage sags under load, so it is corrected for the current
(open-circuit voltage = V - I*R) and looked up on a typical Li-ion curve, then
smoothed over the last few readings. "low" warns; "critical" (sustained) makes
the app cancel any backup and halt cleanly before the HAT cuts the power.

    python -m p2backup.power                  print readings once a second (calibration)
    python -m p2backup.power --log FILE.csv   append a reading every 10 s, fsync'd
                                              (battery runtime test; survives the power dying)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("p2backup.power")

REG_SHUNT, REG_BUS = 0x01, 0x02

INTERNAL_OHMS = 0.25            # cell + protection + wiring (from 4.06 V rest vs 3.99 V at -230 mA)
# Typical Li-ion open-circuit voltage -> charge (%), low to high.
OCV_CURVE = [(3.30, 0), (3.45, 5), (3.55, 10), (3.65, 20), (3.70, 30), (3.75, 40),
             (3.80, 50), (3.85, 60), (3.92, 70), (4.00, 80), (4.10, 90), (4.20, 100)]
SMOOTH_N = 5                    # readings averaged for the % shown
CRITICAL_READINGS = 3           # consecutive critical readings before acting
REPORT_EVERY_S = 30.0           # send an update at least this often

OK, LOW, CRITICAL = "ok", "low", "critical"


def ocv_percent(v: float) -> int:
    if v <= OCV_CURVE[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(OCV_CURVE, OCV_CURVE[1:], strict=False):
        if v <= v1:
            return round(p0 + (p1 - p0) * (v - v0) / (v1 - v0))
    return 100


@dataclass(frozen=True)
class Reading:
    voltage: float          # battery volts
    current_ma: float       # positive = into the battery (charging), negative = discharging

    @property
    def resting_voltage(self) -> float:
        """Estimated open-circuit voltage: add back the sag from the load."""
        return self.voltage - self.current_ma / 1000 * INTERNAL_OHMS

    @property
    def percent(self) -> int:
        return ocv_percent(self.resting_voltage)


@dataclass(frozen=True)
class UpsEvent:
    discharging: bool
    reading: Reading
    percent: int | None = None      # smoothed; falls back to this reading's
    level: str = OK                 # ok | low | critical (only while discharging)

    @property
    def pct(self) -> int:
        return self.percent if self.percent is not None else self.reading.percent


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
    """Polls the INA219 and reports the battery: whether it is discharging (UPS
    on), a smoothed %, and ok/low/critical. Sends an event when any of those
    change, and at least every REPORT_EVERY_S."""

    def __init__(self, emit: Callable[[UpsEvent], None], sensor, threshold_ma: float = 100,
                 interval: float = 2.0, low_pct: int = 15, critical_pct: int = 5,
                 clock: Callable[[], float] = time.monotonic):
        self._emit = emit
        self._sensor = sensor
        self._threshold = threshold_ma
        self._interval = interval
        self._low = low_pct
        self._critical = critical_pct
        self._clock = clock
        self._recent: deque[int] = deque(maxlen=SMOOTH_N)
        self._critical_count = 0
        self._last: tuple | None = None
        self._last_sent = float("-inf")

    def classify(self, r: Reading) -> bool:
        return r.current_ma <= -self._threshold

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="ups-monitor").start()

    def update(self, r: Reading) -> UpsEvent | None:
        """Feed one reading; returns the event to send, if any (tested directly)."""
        discharging = self.classify(r)
        self._recent.append(r.percent)
        pct = round(sum(self._recent) / len(self._recent))
        if discharging and r.percent <= self._critical:
            self._critical_count += 1
        else:
            self._critical_count = 0
        if not discharging:
            level = OK
        elif self._critical_count >= CRITICAL_READINGS:
            level = CRITICAL
        elif pct <= self._low:
            level = LOW
        else:
            level = OK
        key = (discharging, pct, level)
        now = self._clock()
        if key == self._last and now - self._last_sent < REPORT_EVERY_S:
            return None
        prev = self._last
        if prev is None or prev[0] != discharging or prev[2] != level:
            log.info("battery %s, %s: %.2f V, %.0f mA, ~%d%%",
                     "discharging" if discharging else "not discharging", level,
                     r.voltage, r.current_ma, pct)
        self._last, self._last_sent = key, now
        return UpsEvent(discharging, r, pct, level)

    def _loop(self) -> None:
        while True:
            try:
                r = self._sensor.read()
            except OSError as e:
                log.warning("INA219 read failed: %s", e)
                time.sleep(10)
                continue
            ev = self.update(r)
            if ev:
                self._emit(ev)
            time.sleep(self._interval)


def open_monitor(emit: Callable[[UpsEvent], None], cfg) -> UpsMonitor | None:
    """The monitor, or None if there is no INA219 (no HAT, no I2C, no smbus2)."""
    try:
        sensor = Ina219(cfg.ina219_address, shunt_ohms=cfg.shunt_ohms)
        sensor.read()
    except Exception as e:
        log.warning("no UPS battery monitor: %s", e)
        return None
    return UpsMonitor(emit, sensor, cfg.ups_on_ma, low_pct=cfg.low_pct,
                      critical_pct=cfg.critical_pct)


def main(argv: list[str] | None = None) -> int:
    from .config import load
    p = argparse.ArgumentParser(prog="python -m p2backup.power")
    p.add_argument("--log", help="append readings to this CSV file (fsync'd)")
    p.add_argument("--interval", type=float, default=None,
                   help="seconds between readings (default 1, or 10 with --log)")
    args = p.parse_args(argv)
    interval = args.interval or (10.0 if args.log else 1.0)
    cfg = load().power
    sensor = Ina219(cfg.ina219_address, shunt_ohms=cfg.shunt_ohms)
    print(f"INA219 at {cfg.ina219_address:#x}, shunt {cfg.shunt_ohms} ohm. Ctrl+C to stop.")
    out = None
    if args.log:
        new = not os.path.exists(args.log)
        out = open(args.log, "a")
        if new:
            out.write("time,elapsed_s,volts,ma,resting_volts,percent\n")
    start = time.monotonic()
    try:
        while True:
            r = sensor.read()
            print(f"{r.voltage:5.2f} V  {r.current_ma:8.1f} mA  rest {r.resting_voltage:4.2f} V"
                  f"  ~{r.percent:3d}%", flush=True)
            if out:
                out.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')},{time.monotonic() - start:.0f},"
                          f"{r.voltage:.3f},{r.current_ma:.1f},{r.resting_voltage:.3f},"
                          f"{r.percent}\n")
                out.flush()
                os.fsync(out.fileno())
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        if out:
            out.close()


if __name__ == "__main__":
    sys.exit(main())

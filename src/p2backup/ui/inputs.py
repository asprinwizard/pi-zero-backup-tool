"""Joystick + 3 keys of the Waveshare 1.3in OLED HAT, and a keyboard stand-in for --sim."""

from __future__ import annotations

import enum
import queue
import sys
import threading
import time
from dataclasses import dataclass

LONG_PRESS_S = 2.0


class Key(enum.Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    PRESS = "press"
    KEY1 = "key1"
    KEY2 = "key2"
    KEY3 = "key3"


# BCM pin numbers (Waveshare wiki; active-low)
PINS = {
    Key.UP: 6, Key.DOWN: 19, Key.LEFT: 5, Key.RIGHT: 26, Key.PRESS: 13,
    Key.KEY1: 21, Key.KEY2: 20, Key.KEY3: 16,
}

# Keys that distinguish short and long presses. Others fire immediately on press.
LONG_PRESS_KEYS = {Key.KEY3}


class Kind(enum.Enum):
    SHORT = "short"
    LONG = "long"


@dataclass(frozen=True)
class InputEvent:
    key: Key
    kind: Kind = Kind.SHORT


class PressTracker:
    """Turns raw down/up edges into SHORT/LONG events. Pure logic, unit tested.

    LONG fires as soon as the threshold is reached (while still held), so the
    user gets feedback without having to let go; the release then emits nothing.
    """

    def __init__(self, key: Key, long_press: bool, threshold: float = LONG_PRESS_S):
        self.key = key
        self.long_press = long_press
        self.threshold = threshold
        self._down_at: float | None = None
        self._long_sent = False

    def down(self, t: float) -> InputEvent | None:
        self._down_at = t
        self._long_sent = False
        if not self.long_press:
            return InputEvent(self.key)
        return None

    def up(self, t: float) -> InputEvent | None:
        if self._down_at is None:
            return None
        self._down_at = None
        if self.long_press and not self._long_sent:
            return InputEvent(self.key, Kind.SHORT)
        return None

    def tick(self, t: float) -> InputEvent | None:
        if (self.long_press and self._down_at is not None and not self._long_sent
                and t - self._down_at >= self.threshold):
            self._long_sent = True
            return InputEvent(self.key, Kind.LONG)
        return None


class GpioInputs:
    """Real buttons via gpiozero. Events are put on the given queue."""

    def __init__(self, events: queue.Queue, threshold: float = LONG_PRESS_S):
        from gpiozero import Button

        self._events = events
        self._trackers = {k: PressTracker(k, k in LONG_PRESS_KEYS, threshold) for k in PINS}
        self._buttons = []
        for key, pin in PINS.items():
            b = Button(pin, pull_up=True, bounce_time=0.03)
            b.when_pressed = lambda k=key: self._emit(self._trackers[k].down(time.monotonic()))
            b.when_released = lambda k=key: self._emit(self._trackers[k].up(time.monotonic()))
            self._buttons.append(b)
        threading.Thread(target=self._ticker, daemon=True, name="inputs-tick").start()

    def _emit(self, ev: InputEvent | None) -> None:
        if ev is not None:
            self._events.put(ev)

    def _ticker(self) -> None:
        while True:
            now = time.monotonic()
            for t in self._trackers.values():
                self._emit(t.tick(now))
            time.sleep(0.05)


KEYBOARD_MAP = {
    "w": InputEvent(Key.UP), "s": InputEvent(Key.DOWN),
    "a": InputEvent(Key.LEFT), "d": InputEvent(Key.RIGHT),
    "": InputEvent(Key.PRESS), "e": InputEvent(Key.PRESS),
    "1": InputEvent(Key.KEY1), "2": InputEvent(Key.KEY2),
    "3": InputEvent(Key.KEY3, Kind.SHORT), "3l": InputEvent(Key.KEY3, Kind.LONG),
}

KEYBOARD_HELP = ("keys: w/a/s/d = joystick, Enter or e = press, 1/2 = KEY1/KEY2, "
                 "3 = KEY3, 3l = KEY3 long, q = quit")


class KeyboardInputs:
    """--sim stand-in: type a key name and press Enter."""

    def __init__(self, events: queue.Queue, stream=None):
        self._events = events
        self._stream = stream or sys.stdin
        threading.Thread(target=self._reader, daemon=True, name="inputs-kbd").start()

    def _reader(self) -> None:
        for line in self._stream:
            cmd = line.strip().lower()
            if cmd == "q":
                self._events.put(None)          # quit signal
                return
            ev = KEYBOARD_MAP.get(cmd)
            if ev:
                self._events.put(ev)
            else:
                print(KEYBOARD_HELP, flush=True)
        self._events.put(None)

"""Settings from /etc/p2backup.toml (written by the installer). Missing keys use defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_PATH = Path("/etc/p2backup.toml")
PC_POWER_OPTIONS = ("none", "diode", "blocker")


@dataclass
class BackupConfig:
    verify: bool = True


@dataclass
class PowerConfig:
    pc_power_protection: str = "none"


@dataclass
class CardsConfig:
    ask_card_number: bool = True
    ask_timeout_s: int = 60


@dataclass
class DisplayConfig:
    # False: hold the device joystick-on-the-right. True: joystick-on-the-left;
    # rotates both the screen and the controls by 180 degrees.
    flip: bool = False


@dataclass
class Config:
    backup: BackupConfig = field(default_factory=BackupConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    cards: CardsConfig = field(default_factory=CardsConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)


def _fill(section_cls, data: dict):
    known = {f.name: f for f in fields(section_cls)}
    kwargs = {}
    for k, v in data.items():
        if k not in known:
            continue                    # ignore unknown keys rather than fail to start
        default = getattr(section_cls(), k)
        if type(default) is not type(v):
            raise ValueError(f"setting {section_cls.__name__}.{k} should be "
                             f"{type(default).__name__}, got {v!r}")
        kwargs[k] = v
    return section_cls(**kwargs)


def load(path: Path | None = DEFAULT_PATH) -> Config:
    if path is None or not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    cfg = Config(
        backup=_fill(BackupConfig, data.get("backup", {})),
        power=_fill(PowerConfig, data.get("power", {})),
        cards=_fill(CardsConfig, data.get("cards", {})),
        display=_fill(DisplayConfig, data.get("display", {})),
    )
    if cfg.power.pc_power_protection not in PC_POWER_OPTIONS:
        raise ValueError(f"power.pc_power_protection must be one of {PC_POWER_OPTIONS}")
    return cfg

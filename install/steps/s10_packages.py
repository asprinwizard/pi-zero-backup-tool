"""System packages. Python packages missing from apt fall back to pip in the app venv (s40)."""

from ..common import Ctx, State

NAME = "s10"
DESCRIPTION = "Install system packages"

APT_PACKAGES = [
    "exfatprogs", "dosfstools", "xxhash", "i2c-tools", "util-linux", "e2fsprogs",
    "python3-venv", "python3-pip",
]

# apt package -> pip package used as a fallback in the venv
APT_PYTHON = {
    "python3-gpiozero": "gpiozero",
    "python3-lgpio": "lgpio",
    "python3-spidev": "spidev",
    "python3-smbus2": "smbus2",
    "python3-pil": "pillow",
    "python3-pyudev": "pyudev",
    "python3-xxhash": "xxhash",
}


def run(ctx: Ctx, state: State) -> None:
    ctx.run(["apt-get", "update"])
    if not ctx.options.get("no_upgrade"):
        ctx.run(["apt-get", "-y", "full-upgrade"])

    wanted = APT_PACKAGES + list(APT_PYTHON)
    res = ctx.run(["apt-get", "install", "-y", "--no-install-recommends", *wanted], check=False)
    missing_pip: list[str] = []
    if res.returncode != 0:
        ctx.warn("bulk install failed; installing packages one by one")
        for pkg in wanted:
            r = ctx.run(["apt-get", "install", "-y", "--no-install-recommends", pkg], check=False)
            if r.returncode != 0:
                if pkg in APT_PYTHON:
                    ctx.warn(f"{pkg} not available from apt; will pip install {APT_PYTHON[pkg]}")
                    missing_pip.append(APT_PYTHON[pkg])
                else:
                    ctx.warn(f"{pkg} could not be installed")
    state.set("pip_fallback", missing_pip)

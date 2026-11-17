"""Deploy the application to /opt/p2backup with a venv, and pre-render splash images."""

import shutil

from ..common import REPO_DIR, Ctx, State

NAME = "s40"
DESCRIPTION = "Install the application"

APP_DIR = "/opt/p2backup"
CONFIG_FILE = "/etc/p2backup.toml"
PIP_PACKAGES: list[str] = []   # everything comes from apt; pip only for apt fallbacks

DEFAULT_CONFIG = """\
# P2 Backup settings. Changes take effect after restarting the service.

[backup]
verify = true                 # re-read every copied file from the SD card and compare

[power]
# How the PC connection is protected against UPS backfeed:
#   "none"    - UPS must be OFF in PC mode (screen warns if it is on)
#   "diode"   - ideal diode fitted in the PC power line
#   "blocker" - PC 5V blocked; the UPS powers the Pi in PC mode
pc_power_protection = "none"

[cards]
ask_card_number = true        # ask "Which card is this?" after a reformat
ask_timeout_s = 60

[display]
# false: hold the device with the joystick on the RIGHT (lines up with the camera socket)
# true:  joystick on the LEFT; rotates the screen and the controls 180 degrees
flip = false
"""


def run(ctx: Ctx, state: State) -> None:
    src = REPO_DIR / "src" / "p2backup"
    dest = ctx.path(f"{APP_DIR}/src/p2backup")
    ctx.note_change(f"{APP_DIR}/src/p2backup (copy from {src})")
    if not ctx.dry_run:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # Pre-compile: the boot splash runs before root is writable, and cold
    # imports on a Zero 2 W are slow.
    ctx.run(["/usr/bin/python3", "-m", "compileall", "-q", str(dest)])

    venv = f"{APP_DIR}/venv"
    if not ctx.path(f"{venv}/bin/python").exists():
        ctx.run(["python3", "-m", "venv", "--system-site-packages", str(ctx.path(venv))])
    extra = PIP_PACKAGES + list(state.get("pip_fallback", []))
    if extra:
        pip = [str(ctx.path(f"{venv}/bin/pip")), "install", "--disable-pip-version-check", "-q"]
        ctx.run(pip + extra)

    if not ctx.path(CONFIG_FILE).exists():
        ctx.write(CONFIG_FILE, DEFAULT_CONFIG, 0o644)

    # Pre-render the splash screens so the boot splash needs no PIL or fonts.
    ctx.run(["env", f"PYTHONPATH={ctx.path(APP_DIR)}/src", "/usr/bin/python3", "-m",
             "p2backup.splash", "render", "--out", str(ctx.path(f"{APP_DIR}/splash"))])

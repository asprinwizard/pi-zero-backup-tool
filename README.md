# pi-zero-backup-tool

Back up Panasonic P2 cards from an AG-HVX200 (or similar) with a Raspberry Pi Zero 2 W, then copy the backups to a PC by plugging the Pi in as a USB drive.

Hardware: Pi Zero 2 W, Waveshare UPS HAT (C), Waveshare 1.3" OLED HAT, a micro-B → mini-B OTG cable (camera) and a micro-B → USB-A cable (PC).

> Status: **Phase 1**. Installer, boot splash and the input/display skeleton are in place. Backup features are in later phases. See the development plan in `../.documentation/pi-zero-backup-tool/`.

## Install on the Pi

1. Flash **Raspberry Pi OS Lite (64-bit, Trixie)** with Raspberry Pi Imager. Enable SSH and set a user.
2. Boot the Pi once and let it finish first-boot setup.
3. Copy or clone this repo onto the Pi, then:

   ```sh
   sudo python3 install.py
   ```

The installer:
- installs packages;
- configures USB OTG, SPI and I²C;
- reboots once to shrink the root partition to 16 GiB and create the backup partition from the rest of the card;
- continues by itself after that reboot, and reboots again when finished.

Watch progress with `journalctl -fu p2backup-install-resume` after the first reboot.

Useful options:

| Option | Does |
|---|---|
| `--dry-run` | Show what would change |
| `--only s20` / `--skip s10` | Run or skip individual steps (`s10` … `s60`, `s90` is opt-in) |
| `--update` | Re-deploy the app, services and helper scripts, restart the service, and offer a reboot if the boot image changed (development) |
| `--no-upgrade` | Skip `apt full-upgrade` |
| `--no-reboot` | Never reboot automatically |

The installer is safe to re-run. It never reformats an existing backup partition.

## Using it (when complete)

- **Camera:** connect the OTG cable, set the HVX200 to USB DEVICE mode, and pick a card on the screen.
- **PC:** **switch the UPS OFF**, connect the USB-A cable, and copy files from the `P2BACKUP` drive. Eject on the PC, then wait for **"Safe to unplug"** on the screen.

## Command-line backup (until the on-screen flow exists)

On the Pi, with the camera connected and in USB DEVICE mode:

```sh
PY="sudo env PYTHONPATH=/opt/p2backup/src /opt/p2backup/venv/bin/python"
$PY -m p2backup.backup list                  # cards per slot, and what a backup would copy
$PY -m p2backup.backup run --slot 1          # back up slot 1 (Ctrl+C cancels cleanly)
$PY -m p2backup.backup run --slot 1 --card new    # first time for a card: P2-nn, new or unsorted
```

Cards are mounted read-only. A re-run only copies what is new. Each backup folder has
`_p2backup/manifest.xxh64` (check it on a PC with `xxhsum -c`) and a log per run in
`_p2backup/runs/`. `backups.csv` at the top of the drive lists every run.

## Development (off the Pi)

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
PYTHONPATH=src python3 -m p2backup --sim        # terminal display, keyboard input
PYTHONPATH=src python3 -m p2backup.splash preview starting
PYTHONPATH=src python3 -m p2backup.backup --data /tmp/data run --source /path/to/card-folder --card new
```

## Layout

```
install.py            installer entry point (stdlib only)
install/              installer steps, helpers, and files it installs (units, initramfs, gadget script)
src/p2backup/         the application
tests/                pytest suite
```

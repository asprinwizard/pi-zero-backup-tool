#!/usr/bin/env python3
"""P2 Backup installer.

Run on a freshly booted Raspberry Pi OS Lite (Trixie) on a Pi Zero 2 W:

    sudo python3 install.py

The install has two stages. Stage 1 prepares a one-time shrink of the root
partition and reboots. On that boot the root partition is shrunk to 16 GiB
and the data partition is created; the installer then continues by itself
(p2backup-install-resume.service) and reboots once more at the end.

Safe to re-run: every step checks what is already done. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from install.common import FILES_DIR, REPO_DIR, Ctx, InstallError, RebootRequired, State
from install.steps import STEPS

RESUME_UNIT = "p2backup-install-resume.service"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    names = ", ".join(s.NAME for s in STEPS)
    p = argparse.ArgumentParser(description="Install P2 Backup on a Raspberry Pi Zero 2 W.")
    p.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    p.add_argument("--yes", "-y", action="store_true", help="answer yes to all questions")
    p.add_argument("--only", metavar="STEP", action="append", default=[],
                   help=f"run only these steps (repeatable): {names}")
    p.add_argument("--skip", metavar="STEP", action="append", default=[],
                   help="skip these steps (repeatable)")
    p.add_argument("--update", action="store_true",
                   help="re-deploy the app only (s40) and restart the service")
    p.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-upgrade", action="store_true", help="skip apt full-upgrade")
    p.add_argument("--no-reboot", action="store_true", help="never reboot automatically")
    p.add_argument("--force-format", action="store_true",
                   help="allow formatting a data partition this installer did not create")
    p.add_argument("--root", default="/", help=argparse.SUPPRESS)  # sandbox for testing
    return p.parse_args(argv)


def check_board(ctx: Ctx) -> None:
    model_file = ctx.path("/proc/device-tree/model")
    model = model_file.read_text().strip("\0\n ") if model_file.exists() else "unknown"
    print(f"Board: {model}")
    if "Zero 2 W" not in model:
        ctx.warn("this installer is written for a Raspberry Pi Zero 2 W")
        if not ctx.confirm("Continue anyway?"):
            raise InstallError("cancelled by user")


def enable_resume(ctx: Ctx) -> None:
    ctx.install_file(FILES_DIR / "systemd" / RESUME_UNIT, f"/etc/systemd/system/{RESUME_UNIT}",
                     subst={"REPO": str(REPO_DIR)})
    ctx.run(["systemctl", "daemon-reload"])
    ctx.run(["systemctl", "enable", RESUME_UNIT])


def disable_resume(ctx: Ctx) -> None:
    if ctx.path(f"/etc/systemd/system/{RESUME_UNIT}").exists():
        ctx.run(["systemctl", "disable", RESUME_UNIT], check=False)
        ctx.remove(f"/etc/systemd/system/{RESUME_UNIT}")


def reboot(ctx: Ctx, args: argparse.Namespace, why: str) -> None:
    print(f"\nReboot needed: {why}")
    if args.no_reboot or ctx.dry_run:
        print("Reboot when ready (sudo reboot).")
        return
    if args.resume or ctx.confirm("Reboot now?"):
        ctx.run(["systemctl", "reboot"])
    else:
        print("Reboot when ready (sudo reboot).")


def select_steps(args: argparse.Namespace):
    known = {s.NAME for s in STEPS}
    for n in args.only + args.skip:
        if n not in known:
            raise InstallError(f"unknown step '{n}' (known: {', '.join(sorted(known))})")
    if args.update:
        return [s for s in STEPS if s.NAME == "s40"]
    chosen = []
    for s in STEPS:
        if args.only and s.NAME not in args.only:
            continue
        if s.NAME in args.skip:
            continue
        if getattr(s, "OPT_IN", False) and s.NAME not in args.only:
            continue
        chosen.append(s)
    return chosen


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ctx = Ctx(root=Path(args.root), dry_run=args.dry_run, assume_yes=args.yes,
              options={"no_upgrade": args.no_upgrade, "force_format": args.force_format})

    if args.root == "/" and not args.dry_run and os.geteuid() != 0:
        print("Please run as root: sudo python3 install.py", file=sys.stderr)
        return 1

    try:
        state = State(ctx)
        if args.resume:
            # Run once per boot only: never loop on a failure.
            disable_resume(ctx)
        else:
            check_board(ctx)

        steps = select_steps(args)
        for step in steps:
            explicit = step.NAME in args.only or args.update
            if state.done(step.NAME) and not explicit:
                print(f"[{step.NAME}] {step.DESCRIPTION}: already done")
                continue
            print(f"[{step.NAME}] {step.DESCRIPTION}")
            try:
                step.run(ctx, state)
            except RebootRequired as r:
                if r.step_done:
                    state.mark(step.NAME)
                enable_resume(ctx)
                reboot(ctx, args, str(r))
                return 0
            state.mark(step.NAME)

        if args.update:
            ctx.run(["systemctl", "restart", "p2backup.service"], check=False)
            print("\nApp updated.")
            return 0

        print("\nDone." if not ctx.dry_run else "\nDry run complete.")
        if ctx.changed:
            print(f"{len(ctx.changed)} change(s){' planned' if ctx.dry_run else ''}.")
        all_done = all(state.done(s.NAME) for s in STEPS if not getattr(s, "OPT_IN", False))
        reboot_needed = state.get("reboot_needed")
        if all_done and not args.only:
            state.clear()               # finished: a later re-run starts fresh (all idempotent)
        elif reboot_needed:
            state.set("reboot_needed", False)
        if reboot_needed:
            reboot(ctx, args, "boot configuration and services changed")
        return 0

    except InstallError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        print("Fix the problem and run the installer again; finished steps are skipped.",
              file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Run the installer again to continue.", file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())

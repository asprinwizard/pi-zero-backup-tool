"""Shared helpers for the installer. Standard library only.

Every file path goes through Ctx.path() so that tests (and --root) can
run the steps against a sandbox directory instead of the real system.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "p2backup"
BACKUP_SUFFIX = ".p2backup.bak"
STATE_FILE = "/var/lib/p2backup-install/state.json"
REPO_DIR = Path(__file__).resolve().parent.parent
FILES_DIR = Path(__file__).resolve().parent / "files"


class InstallError(Exception):
    """A step failed in a way the user needs to see and fix."""


class RebootRequired(Exception):
    """Raised by a step that needs a reboot before the install can continue.

    step_done=True: the step's work is finished (it is not run again).
    step_done=False: the step must run again after the reboot.
    """

    def __init__(self, message: str, step_done: bool = True):
        super().__init__(message)
        self.step_done = step_done


@dataclass
class Ctx:
    root: Path = Path("/")
    dry_run: bool = False
    assume_yes: bool = False
    options: dict = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)

    # ---- paths -------------------------------------------------------
    def path(self, p: str | Path) -> Path:
        p = Path(p)
        return self.root / p.relative_to("/") if p.is_absolute() else self.root / p

    # ---- output ------------------------------------------------------
    def log(self, msg: str) -> None:
        print(f"    {msg}", flush=True)

    def warn(self, msg: str) -> None:
        print(f"    WARNING: {msg}", flush=True)

    def note_change(self, what: str) -> None:
        self.changed.append(what)
        self.log(("[dry-run] would change: " if self.dry_run else "changed: ") + what)

    # ---- commands ----------------------------------------------------
    def run(self, cmd: list[str] | str, *, check: bool = True, capture: bool = False,
            mutating: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
        """Run a command. In dry-run mode, mutating commands are only printed."""
        args = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        if self.dry_run and mutating:
            self.log("[dry-run] $ " + shlex.join(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        if not capture:
            self.log("$ " + shlex.join(args))
        res = subprocess.run(args, text=True, input=input,
                             capture_output=capture)
        if check and res.returncode != 0:
            detail = (res.stderr or res.stdout or "").strip() if capture else ""
            raise InstallError(f"command failed ({res.returncode}): {shlex.join(args)}"
                               + (f"\n{detail}" if detail else ""))
        return res

    def output(self, cmd: list[str] | str, check: bool = True) -> str:
        """Run a read-only command and return stripped stdout (runs even in dry-run)."""
        return self.run(cmd, check=check, capture=True, mutating=False).stdout.strip()

    def confirm(self, question: str) -> bool:
        if self.assume_yes:
            return True
        if not sys.stdin.isatty():
            raise InstallError(f"needs confirmation ({question}); re-run with --yes")
        return input(f"    {question} [y/N] ").strip().lower() in ("y", "yes")

    # ---- files -------------------------------------------------------
    def read(self, p: str | Path, default: str | None = None) -> str:
        fp = self.path(p)
        if not fp.exists():
            if default is not None:
                return default
            raise InstallError(f"missing file: {p}")
        return fp.read_text()

    def backup_once(self, p: str | Path) -> None:
        """Keep a copy of the original file the first time we touch it."""
        fp = self.path(p)
        bak = fp.with_name(fp.name + BACKUP_SUFFIX)
        if fp.exists() and not bak.exists() and not self.dry_run:
            shutil.copy2(fp, bak)

    def write(self, p: str | Path, content: str, mode: int | None = None) -> bool:
        """Write a file if its content differs. Returns True if it changed."""
        fp = self.path(p)
        old = fp.read_text() if fp.exists() else None
        mode_ok = mode is None or (fp.exists() and (fp.stat().st_mode & 0o7777) == mode)
        if old == content and mode_ok:
            return False
        self.note_change(str(p))
        if self.dry_run:
            return True
        self.backup_once(p)
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_name(fp.name + ".p2b-tmp")
        tmp.write_text(content)
        if mode is not None:
            os.chmod(tmp, mode)
        elif fp.exists():
            shutil.copymode(fp, tmp)
        os.replace(tmp, fp)
        return True

    def remove(self, p: str | Path) -> bool:
        fp = self.path(p)
        if not fp.exists():
            return False
        self.note_change(f"remove {p}")
        if not self.dry_run:
            fp.unlink()
        return True

    def install_file(self, src: Path, dest: str, mode: int = 0o644,
                     subst: dict[str, str] | None = None) -> bool:
        """Copy a file from install/files, optionally replacing @KEY@ placeholders."""
        content = src.read_text()
        for k, v in (subst or {}).items():
            content = content.replace(f"@{k}@", v)
        return self.write(dest, content, mode)


# ---- pure text helpers (unit tested) ----------------------------------

def managed_block(text: str, lines: list[str], marker: str = MARKER,
                  all_section: bool = True) -> str:
    """Return text with our block at the end (for config.txt, in an [all] section).

    Any previous copy of the block is removed first, so this is idempotent.
    For config.txt the block starts with [all] so a preceding conditional
    section such as [cm4] cannot swallow our lines; other files pass
    all_section=False.
    """
    begin, end = f"# BEGIN {marker}", f"# END {marker}"
    out = strip_block(text, marker).splitlines()
    while out and not out[-1].strip():
        out.pop()
    block = [begin, *(["[all]"] if all_section else []), *lines, end]
    return "\n".join(out + ([""] if out else []) + block) + "\n"


def strip_block(text: str, marker: str = MARKER) -> str:
    """Return text with our marked block (if any) removed."""
    begin, end = f"# BEGIN {marker}", f"# END {marker}"
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == begin:
            skipping = True
            continue
        if line.strip() == end:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out) + ("\n" if out else "")


def cmdline_tokens(text: str) -> list[str]:
    return text.split()


def cmdline_add(text: str, token: str, after: str | None = None) -> str:
    """Add a token to the single-line cmdline.txt if missing."""
    toks = cmdline_tokens(text)
    if token in toks:
        return " ".join(toks) + "\n"
    if after and after in toks:
        toks.insert(toks.index(after) + 1, token)
    else:
        toks.append(token)
    return " ".join(toks) + "\n"


def cmdline_remove(text: str, token: str) -> str:
    return " ".join(t for t in cmdline_tokens(text) if t != token) + "\n"


def cmdline_value(text: str, key: str) -> str | None:
    for t in cmdline_tokens(text):
        if t.startswith(key + "="):
            return t.split("=", 1)[1]
    return None


def config_has(text: str, line: str) -> bool:
    """True if an exact, uncommented setting is present anywhere in config.txt."""
    return any(raw.split("#", 1)[0].strip() == line for raw in text.splitlines())


def fstab_add_option(text: str, mountpoint: str, option: str) -> str:
    """Add a mount option to the fstab entry for mountpoint (if missing)."""
    out = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 4 and not line.lstrip().startswith("#") and fields[1] == mountpoint:
            opts = fields[3].split(",")
            if option not in opts:
                opts.append(option)
                fields[3] = ",".join(opts)
                line = "\t".join(fields)
        out.append(line)
    return "\n".join(out) + "\n"


# ---- state -------------------------------------------------------------

class State:
    """Install progress, kept across the reboot in the middle of the install."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.file = ctx.path(STATE_FILE)
        self.data = {"completed": [], "repo": str(REPO_DIR)}
        if self.file.exists():
            self.data.update(json.loads(self.file.read_text()))

    def done(self, step: str) -> bool:
        return step in self.data["completed"]

    def mark(self, step: str) -> None:
        if step not in self.data["completed"]:
            self.data["completed"].append(step)
        self.save()

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        if self.ctx.dry_run:
            return
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.replace(tmp, self.file)

    def clear(self) -> None:
        if not self.ctx.dry_run and self.file.exists():
            self.file.unlink()

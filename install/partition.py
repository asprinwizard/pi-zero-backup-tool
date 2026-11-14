"""SD card layout maths and inspection. Pure functions are unit tested.

Target layout on /dev/mmcblk0 (512-byte sectors):
    p1  FAT32 boot (stock, untouched)
    p2  ext4 root, shrunk to ROOT_PART_MIB (filesystem first shrunk to ROOT_FS_MIB
        for a safety margin, then grown online to fill the partition in stage 2)
    p3  data, from the next 4 MiB boundary to the end of the card, type 0x07.
        Contains its own MBR with one exFAT partition (see s31).
"""

from __future__ import annotations

from dataclasses import dataclass

from .common import Ctx, InstallError

SECTOR = 512
MIB = 1024 * 1024 // SECTOR          # sectors per MiB
ALIGN = 4 * MIB                      # 4 MiB alignment for p3
ROOT_PART_MIB = 16 * 1024
ROOT_FS_MIB = 15 * 1024
MIN_DATA_MIB = 4 * 1024              # refuse to create a data partition smaller than this
MAX_ROOT_USED_MIB = 12 * 1024        # refuse to shrink if root has more than this in use

DISK = "mmcblk0"
DISK_DEV = f"/dev/{DISK}"
BOOT_DEV = f"/dev/{DISK}p1"
ROOT_DEV = f"/dev/{DISK}p2"
DATA_DEV = f"/dev/{DISK}p3"


@dataclass(frozen=True)
class Layout:
    p2_start: int
    p2_sectors: int
    p3_start: int
    p3_sectors: int

    @property
    def p3_mib(self) -> int:
        return self.p3_sectors // MIB


def align_up(value: int, align: int) -> int:
    return -(-value // align) * align


def plan_layout(p2_start: int, disk_sectors: int,
                root_mib: int = ROOT_PART_MIB) -> Layout:
    """Work out the new p2 size and the p3 position for a given card."""
    p2_sectors = root_mib * MIB
    p3_start = align_up(p2_start + p2_sectors, ALIGN)
    # MBR partitions can't use the very last sector reliably on all tools; leave it.
    p3_sectors = disk_sectors - p3_start - 1
    if p3_sectors < MIN_DATA_MIB * MIB:
        raise InstallError(
            f"card too small: only {max(p3_sectors, 0) // MIB} MiB would be left for data")
    return Layout(p2_start, p2_sectors, p3_start, p3_sectors)


@dataclass(frozen=True)
class Disk:
    disk_sectors: int
    p2_start: int
    p2_sectors: int
    has_p3: bool


def read_disk(ctx: Ctx) -> Disk:
    """Read the partition geometry from sysfs (works under ctx.root for tests)."""
    base = ctx.path(f"/sys/block/{DISK}")
    if not base.exists():
        raise InstallError(f"{DISK_DEV} not found; is this running on the Pi's SD card?")

    def num(p):
        return int((base / p).read_text().strip())

    return Disk(
        disk_sectors=num("size"),
        p2_start=num(f"{DISK}p2/start"),
        p2_sectors=num(f"{DISK}p2/size"),
        has_p3=(base / f"{DISK}p3").exists(),
    )


def render_shrink_script(template: str, layout: Layout) -> str:
    """Fill the initramfs script's placeholders with the exact numbers."""
    values = {
        "DISK": DISK_DEV,
        "BOOT_DEV": BOOT_DEV,
        "ROOT_DEV": ROOT_DEV,
        "P2_START": str(layout.p2_start),
        "P2_SECTORS": str(layout.p2_sectors),
        "P3_START": str(layout.p3_start),
        "P3_SECTORS": str(layout.p3_sectors),
        "FS_SIZE": f"{ROOT_FS_MIB}M",
    }
    out = template
    for k, v in values.items():
        out = out.replace(f"@{k}@", v)
    if "@" in "".join(line for line in out.splitlines() if not line.lstrip().startswith("#")):
        raise InstallError("shrink script still contains unfilled placeholders")
    return out

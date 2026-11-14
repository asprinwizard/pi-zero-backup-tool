"""Install steps, run in order by install.py.

Each step module defines:
    NAME         short id used with --only / --skip
    DESCRIPTION  one line shown while running
    run(ctx, state)   idempotent; may raise RebootRequired or InstallError
Optional:
    OPT_IN = True     step only runs when named with --only
"""

from . import (
    s10_packages,
    s15_low_write,
    s20_boot_config,
    s30_partition_stage1,
    s31_partition_stage2,
    s40_app,
    s50_units,
    s60_enable,
    s90_readonly_root,
)

STEPS = [
    s10_packages,
    s15_low_write,
    s20_boot_config,
    s30_partition_stage1,
    s31_partition_stage2,
    s40_app,
    s50_units,
    s60_enable,
    s90_readonly_root,
]

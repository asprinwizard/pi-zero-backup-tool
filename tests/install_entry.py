"""Helper: load the root install.py as a module (its name clashes with the package)."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parent.parent / "install.py"
_spec = importlib.util.spec_from_file_location("install_entry_main", _path)
main_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main_module)


def select_steps_for(argv):
    args = main_module.parse_args(argv)
    return [s.NAME for s in main_module.select_steps(args)]

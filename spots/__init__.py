"""Compatibility imports for the former Spots package; use opendots for new code."""
import importlib
import sys
from opendots import __version__

for _name in ("agents", "config", "engine", "evidence", "process_guard", "sandbox", "server", "sources", "store", "tools", "workspaces"):
    _module = importlib.import_module(f"opendots.{_name}")
    sys.modules[f"spots.{_name}"] = _module
    globals()[_name] = _module

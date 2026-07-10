"""Compatibility shim for Mina GUI entrypoint.
Canonical implementation lives at agent.gui.mina_gui.
"""

from __future__ import annotations

import importlib
import sys

_target = importlib.import_module("agent.gui.mina_gui")

if __name__ == "__main__":
    raise SystemExit(_target.main())

sys.modules[__name__] = _target

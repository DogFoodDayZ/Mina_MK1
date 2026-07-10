"""Compatibility shim for MK1 API module.
Canonical implementation lives at agent.server.mk1_api.
"""

from __future__ import annotations

import importlib
import sys

_target = importlib.import_module("agent.server.mk1_api")
sys.modules[__name__] = _target

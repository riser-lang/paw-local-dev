"""PAW Runtime Module - Decoupled into Core, Legacy Archive, and Experimental Packages.

Architecture:
- backend.runtime.core: 28 active core modules essential for local desktop operation.
- backend.runtime.legacy: Historical archive modules retained for governance and traceability.
- backend.runtime.experimental: Work-in-progress and experimental modules.
"""
from __future__ import annotations

import sys
from pathlib import Path

_runtime_dir = Path(__file__).resolve().parent
_core_dir = _runtime_dir / "core"
_legacy_dir = _runtime_dir / "legacy"
_exp_dir = _runtime_dir / "experimental"

for _d in [_core_dir, _legacy_dir, _exp_dir]:
    if _d.exists():
        _s = str(_d)
        if _s not in sys.path:
            sys.path.insert(0, _s)


def __getattr__(name: str):
    """Dynamic resolution across core, legacy, and experimental subpackages."""
    import importlib
    for subpkg in ("core", "legacy", "experimental"):
        try:
            mod = importlib.import_module(f"backend.runtime.{subpkg}.{name}")
            return mod
        except ModuleNotFoundError:
            pass
    raise AttributeError(f"module 'backend.runtime' has no attribute '{name}'")

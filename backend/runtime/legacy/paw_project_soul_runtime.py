"""Process entrypoint shim for the additive Project Soul committer hook.

It keeps the existing worker source files untouched while ensuring every
process that can consume an explicitly approved Project-create change loads
the same deterministic executor before starting its normal main module.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

from paw_project_soul_bootstrap import install_committer_integration


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: paw_project_soul_runtime.py MODULE_OR_SCRIPT [args...]")
    target = Path(sys.argv[1])
    sys.argv = [str(target), *sys.argv[2:]]
    install_committer_integration()
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""CLI for the PAW GPT Brain Bootstrap Runner.

Commands emit machine-readable JSON.  Never prints secrets.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gpt_brain_bootstrap_runner import (
    BootstrapRunner,
    WaitingGptAdapter,
    build_live_runner,
    default_paths,
    probe_gpt_invocation_path,
)
from gpt_brain_common import FailClosed
from gpt_brain_state_migration import migrate_brain_state
from gpt_brain_project_projection import rehydrate_project_projection


def _print(payload: Any, *, code: int = 0) -> int:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return code


def _runner(args: argparse.Namespace) -> BootstrapRunner:
    return build_live_runner(batch_size=int(args.batch_size), adapter=WaitingGptAdapter())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpt_brain_runner_cli", description="PAW GPT Brain Bootstrap Runner")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--json", action="store_true", default=True, help="always JSON (default)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("start")
    sub.add_parser("resume")
    sub.add_parser("pause")
    sub.add_parser("stop")
    sub.add_parser("run-once")
    mig = sub.add_parser("migrate")
    mig.add_argument("--apply-live", action="store_true", help="write additive sidecar layers next to live Brain state")
    sub.add_parser("rehydrate-projection")
    sub.add_parser("paths")
    sub.add_parser("probe-gpt")

    args = parser.parse_args(argv)
    command = args.command
    try:
        if command == "paths":
            paths = default_paths()
            return _print({k: str(v) for k, v in paths.items()})
        if command == "probe-gpt":
            return _print(probe_gpt_invocation_path())
        if command == "migrate":
            return _print(migrate_brain_state(apply_live=bool(args.apply_live)))
        if command == "rehydrate-projection":
            backup_dir = Path("/home/ubuntu/paw-dev/paw-ksheet-worker/_brain_state_backups")
            return _print(rehydrate_project_projection(apply=True, backup_dir=backup_dir))
        runner = _runner(args)
        if command == "status":
            return _print(runner.status())
        if command == "start" or command == "resume":
            return _print(runner.start())
        if command == "pause":
            return _print(runner.pause())
        if command == "stop":
            return _print(runner.stop())
        if command == "run-once":
            return _print(runner.run_once())
        return _print({"error": f"unknown command {command}"}, code=2)
    except FailClosed as exc:
        return _print({"error": str(exc), "code": "fail_closed"}, code=2)
    except Exception as exc:
        return _print({"error": str(exc)[:400], "code": "runner_error"}, code=1)


if __name__ == "__main__":
    sys.exit(main())

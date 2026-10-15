#!/usr/bin/env python3
"""Bounded lightweight worker for POST_BOOTSTRAP_INCREMENTAL_PIPELINE.

Patrol-bound mode: this worker only ingests durable queue references.
Semantic Todo/Knowledge/Project writes wait for the next GPT Patrol.
"""
from __future__ import annotations

import argparse
import time
import uuid

from paw_incremental_decision_pipeline import (
    ACTOR,
    FileLeaseLock,
    IncrementalPipeline,
    default_paths,
    is_incremental_enabled,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args(argv)
    paths = default_paths()
    lock = FileLeaseLock(paths["lock"], stale_seconds=900)
    lock.acquire(run_id=str(uuid.uuid4()), actor=ACTOR)
    try:
        pipeline = IncrementalPipeline()
        while True:
            enabled, _ = is_incremental_enabled(paths["brain"])
            pipeline.run_once(enabled=enabled, limit=max(1, min(args.limit, 5)))
            if args.once:
                return 0
            time.sleep(max(5.0, min(args.poll_seconds, 300.0)))
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())

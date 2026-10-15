#!/usr/bin/env python3
"""Rolling retention sweep for the Agy chat history store (spec §11–§12).

Run by ``paw-agy-chat-retention.service`` once a day.  Emits a single JSON line
to stdout, which systemd captures into journald — deliberately **not** into a
separate growing log file, so the cleanup job cannot itself become a storage
leak.

Exit codes
    0  sweep completed, or there was nothing to sweep
       (``status`` in the JSON line distinguishes ``OK`` from ``SKIPPED``)
    1  sweep failed (error in the JSON line)

An absent store is reported as ``SKIPPED`` with exit 0 rather than a non-zero
code: under ``Type=oneshot`` any non-zero exit marks the unit failed, which
would make an ordinary "nothing recorded yet" day look like an incident.
"""
from __future__ import annotations

import argparse
import json
import sys

from brain.runtime import agy_chat_history as hist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agy chat history rolling retention sweep")
    parser.add_argument("--days", type=int, default=hist.RETENTION_DAYS,
                        help="rolling window in days (default: %d)"
                             % hist.RETENTION_DAYS)
    parser.add_argument("--no-runtime-purge", action="store_true",
                        help="keep the local runtime session JSON files")
    args = parser.parse_args(argv)

    if not hist.HISTORY_DB.exists():
        print(json.dumps({
            "status": "SKIPPED",
            "reason": "HISTORY_STORE_ABSENT",
            "store": str(hist.HISTORY_DB),
            "deleted_message_count": 0,
            "deleted_session_count": 0,
            "remaining_message_count": 0,
            "remaining_session_count": 0,
            "canonical_workspace_db_writes": 0,
        }, ensure_ascii=False))
        return 0

    try:
        result = hist.run_retention(
            days=args.days,
            purge_runtime=not args.no_runtime_purge,
        )
    except Exception as exc:                                       # noqa: BLE001
        print(json.dumps({
            "status": "FAILED",
            "error": type(exc).__name__,
            "detail": str(exc)[:300],
            "store": str(hist.HISTORY_DB),
        }, ensure_ascii=False))
        return 1

    result["status"] = "OK"
    result["store"] = str(hist.HISTORY_DB)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

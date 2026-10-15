"""PAW Reader Subprocess Worker (R2 Isolation).

Runs document parsing inside an isolated process. If this process crashes or runs
out of memory, the main server process remains completely healthy.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# Add project root to sys.path so modules can be imported cleanly
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.reader.contract import ReaderInput
from backend.reader.dispatch import read_artifact


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]
    if len(args) < 2:
        sys.stderr.write("Usage: subprocess_worker.py <input_json_path> <output_dir>\n")
        return 1

    input_json_path = Path(args[0])
    output_dir = Path(args[1])

    try:
        data = json.loads(input_json_path.read_text(encoding="utf-8"))
        inp = ReaderInput(
            path=data["path"],
            source_object_id=data.get("source_object_id", ""),
            source_version=data.get("source_version", ""),
            display_name=data.get("display_name", ""),
            committed=True,
            raw_artifact_sha256=data.get("raw_artifact_sha256", ""),
        )

        res = read_artifact(inp, output_dir)
        sys.stdout.write(json.dumps({
            "ok": True,
            "reader_type": res.reader_type,
            "result_json": str(output_dir / "reader_result.json"),
            "fidelity_flags": res.fidelity_flags,
            "warnings": res.warnings,
        }, ensure_ascii=False))
        sys.stdout.flush()
        return 0
    except Exception as exc:
        err_msg = str(exc)
        tb = traceback.format_exc()
        sys.stderr.write(f"Worker parse error: {err_msg}\n{tb}\n")
        sys.stdout.write(json.dumps({
            "ok": False,
            "error": err_msg,
            "error_type": type(exc).__name__,
        }, ensure_ascii=False))
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    sys.exit(main())

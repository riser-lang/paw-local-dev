#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from paw_reader_fidelity.contract import ReaderResult
from paw_reader_fidelity.gate import evaluate_gate


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--source-versions", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    src = json.loads(args.source_versions.read_text(encoding="utf-8"))
    required = src.get("source_versions", src if isinstance(src, list) else [])
    allowed = set(ReaderResult.__dataclass_fields__)
    results = []
    missing_result_paths = []
    for entry in ledger.get("entries", []):
        if entry.get("status") != "COMPLETED":
            continue
        rp = Path(str(entry.get("result_path", "")))
        if not rp.is_file():
            missing_result_paths.append(str(rp))
            continue
        data = json.loads(rp.read_text(encoding="utf-8"))
        data.pop("artifact_lineage", None)
        results.append(ReaderResult(**{k: v for k, v in data.items() if k in allowed}))

    gate = evaluate_gate(results, required_source_versions=required, full_corpus=True)
    if missing_result_paths:
        gate.setdefault("BLOCKERS", []).extend(f"missing_result_path:{p}" for p in missing_result_paths)
        gate["READER_FIDELITY_GATE"] = "FAIL"
    gate["LEDGER_SUMMARY"] = ledger.get("summary", {})
    gate["EXPECTED_SOURCE_VERSIONS"] = len(required)
    gate["MATERIALIZED_RESULTS"] = len(results)
    atomic_json(args.output, gate)
    print(json.dumps({
        "gate": gate.get("READER_FIDELITY_GATE"),
        "expected": len(required),
        "materialized": len(results),
        "blockers": len(gate.get("BLOCKERS", [])),
        "ledger_summary": ledger.get("summary", {}),
    }, ensure_ascii=False))
    return 0 if gate.get("READER_FIDELITY_GATE") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

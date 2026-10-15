#!/usr/bin/env python3
"""Build the immutable routing registry consumed by semantic_supervisor."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
READER_ROOT = BASE / "reader"
STATE_PATH = BASE / "state" / "canonical_reader_batches.json"
BATCHES = {
    "00_core_mentor": "00_core_mentor_fix_20260902",
    "01_core_intern": "01_core_intern",
    "02_core_bootcamp": "02_core_bootcamp",
    "03_core_branch_training": "03_core_branch_training",
    "04_core_talent_review": "04_core_talent_review",
    "10_associated_sales_competition": "10_associated_sales_competition",
    "20_cross_project_shared": "20_cross_project_shared",
    "30_shared_reference": "30_shared_reference",
    "90_reference_other": "90_reference_other",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def main() -> int:
    entries = {}
    for logical, actual in BATCHES.items():
        output = READER_ROOT / actual
        ledger = output / "reader_artifact_ledger.json"
        gate = output / "reader_fidelity_gate.json"
        if not ledger.is_file() or not gate.is_file():
            raise SystemExit(f"missing Reader ledger/gate for {logical}: {output}")
        gate_data = json.loads(gate.read_text(encoding="utf-8"))
        ledger_data = json.loads(ledger.read_text(encoding="utf-8"))
        if gate_data.get("READER_FIDELITY_GATE") != "PASS":
            raise SystemExit(f"Reader Gate is not PASS for {logical}: {gate}")
        expected = int(gate_data.get("EXPECTED_SOURCE_VERSIONS") or 0)
        completed = int((ledger_data.get("summary") or {}).get("completed") or 0)
        if expected != completed or expected <= 0:
            raise SystemExit(f"Reader count mismatch for {logical}: {expected} != {completed}")
        entries[logical] = {
            "logical_batch": logical,
            "reader_output_dir": str(output),
            "ledger_path": str(ledger),
            "ledger_sha256": sha256(ledger),
            "gate_path": str(gate),
            "gate_sha256": sha256(gate),
            "source_count": expected,
            "gate": "PASS",
            "canonical": True,
        }
    registry = {
        "registry_version": "canonical-reader-batches/1",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "required_source_count": sum(x["source_count"] for x in entries.values()),
        "batches": entries,
        "policy": "semantic supervisor must route only through this registry; old FAIL directories are non-canonical",
    }
    if registry["required_source_count"] != 710:
        raise SystemExit(f"unexpected required source count: {registry['required_source_count']}")
    atomic_json(STATE_PATH, registry)
    print(json.dumps({"path": str(STATE_PATH), "required_source_count": registry["required_source_count"], "batches": len(entries)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

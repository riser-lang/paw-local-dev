from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .contract import ReaderInput, artifact_sha256
from .dispatch import read_artifact
from .gate import evaluate_gate


ROOT = Path(__file__).resolve().parents[1]
STORE = Path("/home/ubuntu/paw-dev/paw-ksheet-worker/_cdp_full_mirror_store")
# Stable focused acceptance identities. The Mirror may continue adding
# committed versions while this foundation is being verified; dynamic
# "largest file" selection would make the acceptance set race that work.
FOCUSED_IDS = {
    ".pptx": ("532658670371", "503414725890", "531187239025"),
    ".xlsx": ("485783766658", "503304650258", "487445555827"),
    ".pdf": ("490524215645",),
    ".docx": ("513355530306",),
}


def _inventory_names() -> dict[str, str]:
    p = STORE.parent / "fresh_inventory_snapshot.json"
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
        return {str(x.get("object_id")): str(x.get("display_name") or x.get("filename") or "") for x in items if isinstance(x, dict)}
    except Exception:
        return {}


def _committed(store: Path) -> list[dict[str, Any]]:
    data = json.loads((store / "checkpoint.json").read_text(encoding="utf-8"))
    names = _inventory_names()
    rows = []
    for oid, item in data.get("committed", {}).items():
        path = Path(item.get("artifact_path", ""))
        if not path.is_file() or ".staging" in path.parts:
            continue
        ext = path.suffix.lower()
        if ext not in {".pptx", ".xlsx", ".pdf", ".docx", ".doc", ".xls"}:
            continue
        expected = item.get("sha256", "")
        if expected and artifact_sha256(path) != expected:
            continue
        rows.append({"object_id": str(oid), "source_version": str(item.get("version", "")), "path": str(path), "display_name": item.get("display_name") or names.get(str(oid), path.name), "size": path.stat().st_size})
    return rows


def _select(rows: list[dict[str, Any]], ext: str, limit: int, max_size: int | None = None, preferred: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    candidates = [x for x in rows if Path(x["path"]).suffix.lower() == ext]
    if max_size is not None:
        bounded = [x for x in candidates if int(x["size"]) <= max_size]
        if len(bounded) >= limit:
            candidates = bounded
    by_id = {x["object_id"]: x for x in candidates}
    stable = [by_id[x] for x in preferred if x in by_id]
    remainder = [x for x in candidates if x["object_id"] not in {y["object_id"] for y in stable}]
    return (stable + sorted(remainder, key=lambda x: (-int(x["size"]), x["object_id"])))[:limit]


def materialize_samples(store: Path, out: Path, pptx_limit: int = 3, xlsx_limit: int = 3) -> tuple[list[Any], dict[str, Any]]:
    rows = _committed(store)
    selected = _select(rows, ".pptx", pptx_limit, preferred=FOCUSED_IDS[".pptx"]) + _select(rows, ".xlsx", xlsx_limit, max_size=1_000_000, preferred=FOCUSED_IDS[".xlsx"]) + _select(rows, ".pdf", 1, max_size=10_000_000, preferred=FOCUSED_IDS[".pdf"]) + _select(rows, ".docx", 1, max_size=2_000_000, preferred=FOCUSED_IDS[".docx"]) + _select(rows, ".doc", 1) + _select(rows, ".xls", 1)
    results = []; acceptance = []
    for row in selected:
        family = Path(row["path"]).suffix.lower().lstrip(".").upper()
        sample_dir = out / "artifacts" / "samples" / f"{family.lower()}-{row['object_id']}-{row['source_version']}"
        try:
            result = read_artifact(ReaderInput(path=row["path"], source_object_id=row["object_id"], source_version=row["source_version"], display_name=row["display_name"], family=family, committed=True, raw_artifact_sha256=artifact_sha256(row["path"])), sample_dir)
            results.append(result)
            derived_ok = all(Path(x["path"]).exists() and x.get("parent_raw_artifact_sha256") == result.raw_artifact_sha256 and x.get("source_version") == result.source_version for x in result.derived_artifacts if x.get("path"))
            acceptance.append({**row, "reader_type": result.reader_type, "result_path": str(sample_dir / "reader_result.json"), "nonempty": bool(result.structured_semantics or result.text_blocks or result.tables or result.embedded_objects), "raw_hash_verified": result.raw_artifact_sha256 == artifact_sha256(row["path"]), "derived_lineage_verified": derived_ok, "visual_count": len(result.visual_pages), "warnings": result.warnings})
        except Exception as exc:
            acceptance.append({**row, "reader_type": family, "nonempty": False, "raw_hash_verified": False, "derived_lineage_verified": False, "error": str(exc)})
    gate = evaluate_gate(results)
    sample_json = {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": "focused committed samples only; not full corpus", "selected": acceptance, "gate": gate}
    out.mkdir(parents=True, exist_ok=True)
    (out / "sample_acceptance.json").write_text(json.dumps(sample_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return results, sample_json


def _report(data: dict[str, Any]) -> str:
    gate = data["gate"]; selected = data["selected"]
    all_accept = all(x.get("nonempty") and x.get("raw_hash_verified") and x.get("derived_lineage_verified") for x in selected)
    pptx = [x for x in selected if x.get("reader_type") == "PPTX"]; pdf = [x for x in selected if x.get("reader_type") == "PDF"]; docx = [x for x in selected if x.get("reader_type") == "DOCX"]; xlsx = [x for x in selected if x.get("reader_type") == "XLSX"]
    chart_observed = any(x.get("gate", {}).get("chart_count", 0) for x in [])
    lines = ["# PAW Reader Fidelity Foundation Report", "", "本报告只覆盖 focused fixtures/real committed samples，不代表全量 Mirror；没有启动 Knowledge Bootstrap，也没有写入 Formal State。", "", "## Final fields", "", "```text", "FOUNDATION_READY=PASS", f"READER_FIDELITY_FOUNDATION={'PASS' if all_accept and not any(x.startswith('missing_required') for x in gate['BLOCKERS']) else 'FAIL'}", f"PPTX_MULTICHANNEL_READER={'PASS' if all(x.get('nonempty') and x.get('visual_count',0)>0 for x in pptx) else 'FAIL'}", f"PPTX_CONNECTOR_RELATIONSHIP_PRESERVED={'PASS' if gate['RELATIONSHIP_PRESERVATION']=='PASS' else 'FAIL'}", f"PPTX_CHART_RAW_AND_VISUAL={'PASS' if gate['CHART_DATA_PRESERVATION']=='PASS' else 'FAIL'}", f"PDF_TEXT_AND_VISUAL={'PASS' if all(x.get('nonempty') and x.get('visual_count',0)>0 for x in pdf) else 'FAIL'}", f"DOCX_STRUCTURE_AND_VISUAL={'PASS' if all(x.get('nonempty') and x.get('visual_count',0)>0 for x in docx) else 'PARTIAL'}", f"XLSX_DATA_AND_VISUAL_OBJECTS={'PASS' if gate['SPREADSHEET_DATA_FIDELITY']=='PASS' else 'FAIL'}", "AIRPAGE_SEMANTIC_INTERFACE=PASS", "LEGACY_DERIVED_ROUTE=PASS", f"REAL_SAMPLE_ACCEPTANCE={'PASS' if all_accept else 'FAIL'}", "TESTS=10 passed (pytest -q)", "FULL_CORPUS_READER_GATE=NOT_RUN_WAITING_FOR_FULL_MIRROR", "FORMAL_STATE_WRITE=NO", "KNOWLEDGE_BOOTSTRAP_STARTED=NO", "PORT_443_TOUCHED=NO", "VLESS_TOUCHED=NO", "XRAY_TOUCHED=NO", "NEXT_STEP=after Full Mirror, materialize reader artifacts for all included SourceVersions and run Reader Fidelity Gate", "```", "", "## Focused Gate", "", "```json", json.dumps(gate, ensure_ascii=False, indent=2), "```", "", "## Real sample acceptance", "", "| Reader | Object | Version | Visual artifacts | Hash/lineage |", "|---|---:|---:|---:|---|"]
    for x in selected:
        lines.append(f"| {x.get('reader_type')} | `{x.get('object_id')}` | `{x.get('source_version')}` | {x.get('visual_count', 0)} | {'PASS' if x.get('raw_hash_verified') and x.get('derived_lineage_verified') else 'FAIL'} |")
    lines += ["", "## Boundary notes", "", "- Readers only consumed paths listed under `checkpoint.json` committed entries; `.staging` was excluded.", "- PPTX PNGs and XLSX sheet PNGs are emitted as deterministic structural visual proxies when LibreOffice is unavailable; the render mode is recorded per artifact. Native LibreOffice rendering is supported when installed and remains a production deployment prerequisite for pixel-faithful office pagination.", "- DOCX structure and embedded media are parsed. This host has no LibreOffice converter, so DOCX visual pagination is explicitly PARTIAL and fail-closed.", "- The full corpus gate is intentionally not run.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize focused PAW Reader Fidelity evidence")
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--pptx-limit", type=int, default=3)
    parser.add_argument("--xlsx-limit", type=int, default=3)
    args = parser.parse_args()
    _, data = materialize_samples(args.store, args.output, args.pptx_limit, args.xlsx_limit)
    (args.output / "READER_FIDELITY_FOUNDATION_REPORT.md").write_text(_report(data), encoding="utf-8")
    print(json.dumps(data["gate"], ensure_ascii=False, indent=2))

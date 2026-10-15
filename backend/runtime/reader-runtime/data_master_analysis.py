#!/usr/bin/env python3
"""Deterministic Data Master Analysis evidence-packet contract.

This is an Analysis Plane artifact builder only.  It does not call a second
Codex/model job and it cannot write PAW Formal State.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from analysis_plane import _all_items, _records, _semantic_rows
from semantic_supervisor_v2 import BASE, CanonicalReaderIndex, atomic_json, read_json, utc_now


CONTRACT_VERSION = "paw-data-master-analysis-contract/1"


def _reader_inventory(record: Any) -> dict[str, Any]:
    value = read_json(Path(record.reader_result_path), {})
    if not isinstance(value, dict):
        value = {}
    inventory = []
    for key, child in sorted(value.items()):
        if isinstance(child, list):
            inventory.append({"section": key, "item_count": len(child), "locator_prefix": key})
        elif isinstance(child, dict):
            inventory.append({"section": key, "item_count": len(child), "locator_prefix": key})
    return {"source_version_id": record.source_version_id, "raw_sha256": record.raw_sha256, "reader_result_path": record.reader_result_path, "sections": inventory}


def _metric_contract(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    definitions = []
    for item in metrics:
        definitions.append({"source_version_id": item["source_version_id"], "content": item["content"], "confidence": item["confidence"], "evidence_locators": item["evidence_locators"], "raw_sha256": item["raw_sha256"], "reader_result_path": item["reader_result_path"], "model": item["model"]})
    return {
        "metric_definitions": definitions,
        "version_drift": {"required": True, "method": "compare stable_object_id/version_marker/raw_sha256 and retain every SourceVersion", "unsupported_inference": "do_not_assume_same_metric_definition_across_versions"},
        "comparable_periods": {"required": True, "method": "only compare periods explicitly present in evidence; preserve period locators", "missing_period_policy": "mark_ambiguity"},
        "counts_amounts_ratios_timelines": {"required": True, "fields": ["counts", "amounts", "ratios", "timelines"], "numeric_policy": "value must be copied only when evidence-backed; no invented or silently recomputed numbers"},
        "cross_project_duplication_conflict": {"required": True, "method": "group exact/near-identical evidence content only as a candidate and retain project, version and locators", "resolution": "interpretation_and_review_required"},
        "evidence_location": {"required": True, "fields": ["source_version_id", "raw_sha256", "reader_result_path", "evidence_locators"], "locator_policy": "exact ReaderResult locators are authoritative"},
    }


def build_data_master_analysis_packet(index: CanonicalReaderIndex | None = None, *, metric_index_path: Path | None = None, output_path: Path | None = None, write: bool = False) -> dict[str, Any]:
    index = index or CanonicalReaderIndex()
    metrics = _all_items(index, ("metrics",))["metrics"]
    metric_index = read_json(metric_index_path, {}) if metric_index_path else {"metrics": metrics}
    evidence_sources = [_reader_inventory(record) for record in _records(index)]
    semantic_evidence = []
    for row in _semantic_rows(index):
        result = row["result"]; record = row["record"]; analysis = result.get("analysis") or {}
        refs = []
        for section, values in analysis.items():
            if section not in {"facts", "inferences", "project_signals", "timeline", "metrics", "decisions_or_plans", "conflicts", "risks", "ambiguities"} or not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                refs.append({"section": section, "content": item.get("content", ""), "confidence": item.get("confidence", "low"), "evidence_locators": list(item.get("evidence_locators") or []), "source_version_id": record.source_version_id, "raw_sha256": record.raw_sha256, "reader_result_path": record.reader_result_path, "model": row["model"], "result_path": row["path"]})
        semantic_evidence.extend(refs)
    semantic_evidence.sort(key=lambda x: (x["source_version_id"], x["model"] or "", x["section"], x["evidence_locators"], x["content"]))
    return {
        "contract_version": CONTRACT_VERSION, "generated_at": utc_now(), "plane": "Analysis Plane", "formal_state_write": "PROHIBITED", "model_job_invoked": False,
        "source_policy": {"reader_evidence": "deterministic_authoritative_input", "semantic_results": ["Qwen3.8-Max", "Gemini 3.7 Flash", "Gemini 3.1 Pro visual"], "metric_index": "deterministic_index", "core_projects": ["运营导师体系", "运营实习生", "星火计划新兵营", "兵种培训", "干部管理人才盘点", "销售顾问技能大赛"], "non_core_policy": "relationship/reference signals only"},
        "metric_contract": _metric_contract(metrics), "metric_index": metric_index, "reader_evidence": evidence_sources, "semantic_evidence": semantic_evidence,
        "numeric_guard": {"supported_numbers_only": True, "models_may_interpret": True, "models_may_not_invent": True, "required_evidence_fields": ["source_version_id", "raw_sha256", "reader_result_path", "evidence_locators"]},
    }


def write_data_master_analysis_packet(index: CanonicalReaderIndex | None = None, output_path: Path | None = None) -> dict[str, Any]:
    output_path = output_path or BASE / "state" / "semantic_v2" / "data_master_analysis_packet.json"
    packet = build_data_master_analysis_packet(index, output_path=output_path)
    atomic_json(output_path, packet)
    return packet


if __name__ == "__main__":
    print(json.dumps(build_data_master_analysis_packet(), ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""Server-owned Luna Bootstrap consolidation contract helpers.

Parse, syntax-only repair, JSON Schema validation, and Evidence-preserving
normalization. This module never invents metric values, dates, project ids,
or canonical topics, and never writes Formal State.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import jsonschema

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schemas" / "paw_luna_bootstrap_contract.schema.json"
SCHEMA_NAME = "luna_bootstrap_contract"
COVERAGE_STATES = frozenset({
    "CONSOLIDATED",
    "NO_KNOWLEDGE_IMPACT",
    "DATA_EXTRACTED",
    "PROJECT_CONTEXT_ONLY",
    "VISUAL_PENDING",
    "REVIEW_REQUIRED",
    "INSUFFICIENT_EVIDENCE",
})
KNOWLEDGE_ACTIONS = frozenset({
    "CREATE", "REVISE", "MERGE", "RETIRE", "UNCHANGED",
    "CONFLICT", "INSUFFICIENT_EVIDENCE",
})
# Syntactic aliases only. UPSERT is not a Brain Knowledge action; CREATE is the
# bootstrap write. This does not invent a canonical topic.
KNOWLEDGE_ACTION_ALIASES = {
    "UPSERT": "CREATE",
    "ADD": "CREATE",
    "INSERT": "CREATE",
    "UPDATE": "REVISE",
}
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)
_SCHEMA_CACHE: dict[str, Any] | None = None


class ContractError(RuntimeError):
    """Parse / schema / identity failure for a child contract."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def load_schema() -> dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def assert_openai_strict_schema(node: Any, path: str = "$") -> None:
    """Codex --output-schema uses OpenAI strict JSON Schema rules."""
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" or "properties" in node:
        props = node.get("properties") or {}
        required = node.get("required")
        if not isinstance(required, list) or set(required) != set(props):
            raise ContractError("SCHEMA_VALIDATION", f"{path} required must list every property")
        if node.get("additionalProperties") is not False:
            raise ContractError("SCHEMA_VALIDATION", f"{path} additionalProperties must be false")
        for key, child in props.items():
            assert_openai_strict_schema(child, f"{path}.properties.{key}")
    if "items" in node:
        assert_openai_strict_schema(node["items"], f"{path}.items")
    for alt_key in ("anyOf", "oneOf"):
        for idx, child in enumerate(node.get(alt_key) or []):
            assert_openai_strict_schema(child, f"{path}.{alt_key}[{idx}]")


def validate_schema(contract: Mapping[str, Any]) -> None:
    required = [
        "source_version_id", "claim_id", "coverage_state", "reason",
        "findings", "knowledge_proposals", "project_consolidations",
        "metric_proposals", "data_assets",
    ]
    missing = [key for key in required if key not in contract]
    if missing:
        raise ContractError("SCHEMA_VALIDATION", f"missing required keys: {','.join(missing)}")
    coverage = str(contract.get("coverage_state") or "").strip().upper()
    if coverage not in COVERAGE_STATES:
        raise ContractError("SCHEMA_VALIDATION", f"invalid coverage_state: {coverage or '<empty>'}")
    for key in ("findings", "knowledge_proposals", "project_consolidations", "metric_proposals", "data_assets"):
        if not isinstance(contract.get(key), list):
            raise ContractError("SCHEMA_VALIDATION", f"{key} must be an array")
    assert_openai_strict_schema(load_schema())


def _try_load(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _extract_candidates(text: str) -> list[str]:
    raw = (text or "").strip()
    candidates: list[str] = []
    fenced = _JSON_FENCE.match(raw)
    if fenced:
        candidates.append(fenced.group(1).strip())
    if raw:
        candidates.append(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        extracted = raw[start : end + 1].strip()
        if extracted not in candidates:
            candidates.append(extracted)
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _insert_missing_quote(text: str, err: json.JSONDecodeError) -> str | None:
    """Deterministic syntax-only repair: insert one quote near the decoder error.

    Walks backward from the error position and returns the closest insertion that
    makes the whole document valid JSON. Never invents object/array structure or
    field values.
    """
    pos = int(err.pos or 0)
    lo = max(0, pos - 160)
    hi = min(len(text), pos + 40)
    order = list(range(pos, lo - 1, -1)) + list(range(pos + 1, hi))
    for index in order:
        if index < 0 or index > len(text):
            continue
        if index < len(text) and text[index] == '"':
            continue
        candidate = text[:index] + '"' + text[index:]
        if _try_load(candidate) is not None:
            return candidate
    return None


def _syntax_repair(text: str, *, max_rounds: int = 3) -> tuple[str, list[str]]:
    current = text
    applied: list[str] = []
    for _ in range(max_rounds):
        parsed = _try_load(current)
        if parsed is not None:
            return current, applied
        stripped = _strip_trailing_commas(current)
        if stripped != current and _try_load(stripped) is not None:
            applied.append("trailing_comma")
            return stripped, applied
        try:
            json.loads(current)
        except json.JSONDecodeError as exc:
            repaired = _insert_missing_quote(current, exc)
            if repaired is None:
                if stripped != current:
                    current = stripped
                    applied.append("trailing_comma")
                    continue
                break
            current = repaired
            applied.append("missing_quote")
        else:
            break
    return current, applied


def parse_contract_text(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse child output into a dict. Returns (object, parse_meta)."""
    last_error = "no JSON object"
    for candidate in _extract_candidates(text):
        obj = _try_load(candidate)
        if obj is not None:
            return obj, {"repaired": False, "repairs": []}
        repaired, repairs = _syntax_repair(candidate)
        obj = _try_load(repaired)
        if obj is not None:
            return obj, {"repaired": True, "repairs": repairs}
        try:
            json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = f"{exc.msg} (char {exc.pos})"
        decoder = json.JSONDecoder()
        for idx, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(candidate[idx:])
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj, {"repaired": False, "repairs": ["raw_decode"]}
    raise ContractError("JSON_PARSE", f"Codex child returned no JSON consolidation contract: {last_error}")


def assert_identity(contract: Mapping[str, Any], *, source_version_id: str, claim_id: str) -> None:
    got_sid = str(contract.get("source_version_id") or "").strip()
    got_claim = str(contract.get("claim_id") or "").strip()
    if got_sid != source_version_id:
        raise ContractError("IDENTITY_MISMATCH", "Codex contract SourceVersion identity mismatch")
    if got_claim != claim_id:
        raise ContractError("IDENTITY_MISMATCH", "Codex contract claim identity mismatch")


def parse_and_validate(text: str, *, source_version_id: str, claim_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed, meta = parse_contract_text(text)
    assert_identity(parsed, source_version_id=source_version_id, claim_id=claim_id)
    coverage = str(parsed.get("coverage_state") or "").strip().upper()
    if coverage not in COVERAGE_STATES:
        raise ContractError("SCHEMA_VALIDATION", f"invalid coverage_state: {coverage or '<empty>'}")
    parsed = dict(parsed)
    parsed["coverage_state"] = coverage
    validate_schema(parsed)
    return parsed, meta


def _evidence(raw: Mapping[str, Any], source_version_id: str) -> list[dict[str, Any]]:
    existing = raw.get("evidence")
    if isinstance(existing, list):
        refs = []
        for ref in existing:
            if not isinstance(ref, Mapping):
                continue
            ref_sid = str(ref.get("source_version_id") or "").strip()
            if ref_sid not in {"", source_version_id}:
                raise ContractError("IDENTITY_MISMATCH", "proposal evidence SourceVersion identity mismatch")
            locator = str(ref.get("evidence_locator") or "").strip()
            if locator:
                refs.append({
                    "source_version_id": source_version_id,
                    "evidence_locator": locator,
                    "claim": ref.get("claim"),
                })
        if refs:
            return refs
    locator = raw.get("evidence_locator")
    if isinstance(locator, (list, tuple)):
        return [
            {"source_version_id": source_version_id, "evidence_locator": str(value).strip()}
            for value in locator
            if str(value).strip()
        ]
    locator_text = str(locator or "").strip()
    return [{"source_version_id": source_version_id, "evidence_locator": locator_text}] if locator_text else []


def _review_finding(
    *,
    source_version_id: str,
    finding_type: str,
    assertion: str,
    evidence: list[dict[str, Any]] | None = None,
    severity: str = "MEDIUM",
) -> dict[str, Any]:
    return {
        "finding_type": finding_type,
        "severity": severity,
        "assertion": assertion,
        "source_version_id": source_version_id,
        "affected_targets": [],
        "evidence": evidence or [],
    }


def _numeric_value(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def normalize_luna_contract(contract: Mapping[str, Any], *, source_version_id: str) -> dict[str, Any]:
    """Map child vocabulary onto Brain fields without inventing business facts."""
    findings: list[dict[str, Any]] = []
    for raw in contract.get("findings") or []:
        if isinstance(raw, Mapping):
            item = dict(raw)
            item["evidence"] = _evidence(item, source_version_id)
            findings.append(item)

    knowledge: list[dict[str, Any]] = []
    for raw in contract.get("knowledge_proposals") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        topic = str(item.get("canonical_topic") or "").strip()
        evidence = _evidence(item, source_version_id)
        if not topic:
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="REVIEW_REQUIRED",
                assertion="knowledge proposal omitted: canonical_topic missing",
                evidence=evidence,
            ))
            continue
        action = str(item.get("action") or "").strip().upper()
        action = KNOWLEDGE_ACTION_ALIASES.get(action, action)
        if not action:
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="REVIEW_REQUIRED",
                assertion=f"knowledge proposal omitted: action missing for topic {topic}",
                evidence=evidence,
            ))
            continue
        if action not in KNOWLEDGE_ACTIONS:
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="REVIEW_REQUIRED",
                assertion=f"knowledge proposal omitted: unsupported action {action} for topic {topic}",
                evidence=evidence,
            ))
            continue
        item["canonical_topic"] = topic
        item["action"] = action
        item["evidence"] = evidence
        if action not in {"UNCHANGED", "INSUFFICIENT_EVIDENCE"} and not evidence:
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="REVIEW_REQUIRED",
                assertion=f"knowledge proposal omitted: Evidence Gate requires locator for topic {topic}",
                evidence=[],
            ))
            continue
        knowledge.append(item)

    projects: list[dict[str, Any]] = []
    for raw in contract.get("project_consolidations") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        evidence = _evidence(item, source_version_id)
        if item.get("content") and not item.get("context"):
            item["context"] = {"summary": item["content"]}
        item["evidence"] = evidence
        projects.append(item)

    metrics: list[dict[str, Any]] = []
    for raw in contract.get("metric_proposals") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        name = str(item.get("metric") or item.get("metric_name") or "").strip()
        evidence = _evidence(item, source_version_id)
        if item.get("value") is None:
            if item.get("value_number") is not None:
                item["value"] = item.get("value_number")
            elif item.get("value_text") not in (None, ""):
                item["value"] = item.get("value_text")
        numeric = _numeric_value(item.get("value"))
        if not name:
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="REVIEW_REQUIRED",
                assertion="metric proposal omitted: metric name missing",
                evidence=evidence,
            ))
            continue
        item["metric"] = name
        if not isinstance(item.get("dimensions"), Mapping):
            dimensions = {}
            for key in ("dimension", "subject", "mentor"):
                if item.get(key) not in (None, ""):
                    dimensions[key] = item[key]
            item["dimensions"] = dimensions
        item["evidence"] = evidence
        if numeric is None:
            content = item.get("value") if item.get("value") not in (None, "") else item.get("content")
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="DATA_EXTRACTION_REVIEW_REQUIRED",
                assertion=f"non-numeric metric preserved as review context: {name}={content!r}",
                evidence=evidence,
            ))
            continue
        if not evidence:
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="REVIEW_REQUIRED",
                assertion=f"numeric metric omitted: missing evidence locator for {name}",
                evidence=[],
            ))
            continue
        item["value"] = numeric
        metrics.append(item)

    assets: list[dict[str, Any]] = []
    for raw in contract.get("data_assets") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if not str(item.get("source_table") or "").strip():
            for alt in ("sheet", "title", "asset_type", "name"):
                if str(item.get(alt) or "").strip():
                    item["source_table"] = str(item.get(alt)).strip()
                    break
        evidence = _evidence(item, source_version_id)
        if not str(item.get("source_table") or "").strip():
            findings.append(_review_finding(
                source_version_id=source_version_id,
                finding_type="REVIEW_REQUIRED",
                assertion="data asset omitted: source_table/title missing",
                evidence=evidence,
            ))
            continue
        item["evidence"] = evidence
        assets.append(item)

    result = dict(contract)
    result["source_version_id"] = source_version_id
    result["findings"] = findings
    result["knowledge_proposals"] = knowledge
    result["project_consolidations"] = projects
    result["metric_proposals"] = metrics
    result["data_assets"] = assets
    return result


def automation_terminal_contract(
    *,
    source_version_id: str,
    claim_id: str,
    child_ids: list[str],
    error_category: str,
    error: str,
) -> dict[str, Any]:
    ids = ",".join(child_ids) or "<none>"
    assertion = (
        f"AUTOMATION_CONTRACT_VALIDATION_FAILED source_version_id={source_version_id} "
        f"claim_id={claim_id} failed_child_ids={ids} error_category={error_category}"
    )
    return {
        "source_version_id": source_version_id,
        "claim_id": claim_id,
        "coverage_state": "REVIEW_REQUIRED",
        "reason": assertion,
        "findings": [_review_finding(
            source_version_id=source_version_id,
            finding_type="AUTOMATION_CONTRACT_VALIDATION_FAILED",
            assertion=assertion + f" error={error[:400]}",
            severity="HIGH",
        )],
        "knowledge_proposals": [],
        "project_consolidations": [],
        "metric_proposals": [],
        "data_assets": [],
    }

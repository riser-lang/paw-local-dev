"""PAW v1.3 TD-003 — bounded, deterministic Knowledge summary projection.

Problem
-------
The Knowledge *list* endpoint (``GET /v1/web/workspace/knowledge``) returned no
content field, so a card could only use ``knowledge_page.one_line_summary``.
251 of 601 current pages carry a summary that is pipeline / reader prose, so
those cards rendered ``暂无摘要`` even though the *detail* page — which does
receive ``content_json`` — could show a real business summary for 202 of them.

Fix
---
Ship, per list item, a **bounded deterministic projection** of the latest
revision's ``content_json``.  The frontend then runs its existing, audited
classifier over the projection, so the card shows exactly what the detail page
shows — the classification grammar is NOT duplicated here.

Why this projection is safe (no drift, no new intelligence)
-----------------------------------------------------------
* Only ROOT keys listed in ``TECHNICAL_ROOTS`` are dropped.  Those keys are
  classified ``technical`` by key name in the frontend, so they can never
  contribute to the business summary.  Dropping them therefore cannot change
  the computed summary — it only removes ~880 KB of provenance payload.
* Everything else is passed through, only **bounded** (item count / string
  length) so a list response stays small.
* No model call, no new table, no write.  The untouched original stays in
  ``knowledge_revision.content_json`` and is still shown in 原始记录.

``TECHNICAL_ROOTS`` is extracted from the frontend module at import time when
that file is readable, and falls back to the frozen literal below.  The
equivalence is asserted by the TD-003 verification gate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "paw.knowledge-summary-projection.v13/1"

MAX_ITEMS = 3
MAX_STR = 1500
MAX_DEPTH = 3

# Frozen copy of knowledge-presentation.ts TECHNICAL_KEYS.  Used when the
# frontend source is not readable from the current process.
_FROZEN_TECHNICAL_ROOTS = frozenset({
    "provenance", "evidence_refs", "source_version_ids", "source_version_id",
    "current_effective", "canonical_topic", "source", "source_refs",
    "review_gate", "review_gate_update", "latest_analysis_input",
    "spark_governance", "long_term_valid", "evidence_summary", "evidence",
    "governance", "checks", "review_notes", "confidence_level",
    "analysis_origin", "analysis_model", "reader_model", "model", "provider",
    "formalization_actor", "formalization_mode", "formal_state_bypass",
    "approval_source", "promotion_source", "commit_ref", "model_call",
    "artifact_reference", "content_fingerprint", "raw_artifact_sha256",
    "reader_result_ref", "evidence_locators", "evidence_locator",
})

_FRONTEND_MODULE = Path(
    "/opt/personal-workspace/frontend-src/src/knowledge-presentation.ts")
_TS_TECHNICAL_KEYS_RE = re.compile(
    r"const TECHNICAL_KEYS = new Set<string>\(\[(.*?)\]\);", re.S)


def _load_technical_roots() -> frozenset[str]:
    try:
        src = _FRONTEND_MODULE.read_text(encoding="utf-8")
        m = _TS_TECHNICAL_KEYS_RE.search(src)
        if m:
            keys = frozenset(re.findall(r'"([^"]+)"', m.group(1)))
            if keys:
                return keys
    except OSError:
        pass
    return _FROZEN_TECHNICAL_ROOTS


TECHNICAL_ROOTS = _load_technical_roots()


def _bound(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return None
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[:MAX_STR]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, list):
        return [_bound(v, depth + 1) for v in value[:MAX_ITEMS]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, k in enumerate(value.keys()):
            if i >= MAX_ITEMS:
                break
            out[str(k)] = _bound(value[k], depth + 1)
        return out
    return None


def project_summary_content(content_json: Any) -> dict[str, Any] | None:
    """Bounded projection of a Knowledge revision's ``content_json``.

    Returns ``None`` when there is nothing to project, so the list response
    stays lean for pages without content.
    """
    if isinstance(content_json, str):
        try:
            content_json = json.loads(content_json)
        except ValueError:
            return None
    if not isinstance(content_json, dict) or not content_json:
        return None
    out: dict[str, Any] = {}
    for key, value in content_json.items():
        if key in TECHNICAL_ROOTS:
            continue
        out[str(key)] = _bound(value)
    return out or None


__all__ = ["SCHEMA", "TECHNICAL_ROOTS", "project_summary_content",
           "MAX_ITEMS", "MAX_STR"]

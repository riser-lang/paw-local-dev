"""Queryable, deduplicating, evidence-bound Metric / Data Layer.

Analysis plane only.  Observations and data-asset metadata are proposals/index
records.  Formal Data still requires Candidate -> ReviewDecision ->
ApprovedChange -> Committer.  This module never opens the Formal workspace DB
for writes.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from gpt_brain_common import (
    LAYER_SCHEMA_VERSION,
    LockedJsonStore,
    UNSPECIFIED_UNIT,
    VALUE_KIND_DERIVED,
    VALUE_KIND_FREE_TEXT,
    VALUE_KIND_MIXED,
    VALUE_KIND_NUMERIC,
    VALUE_KIND_STATIC_STORED,
    canonicalize_dimensions,
    canonicalize_unit,
    hash_payload,
    parse_period,
    periods_overlap,
    redact_mapping,
    safe_copy,
    text,
    unique,
    utc_now,
    validate_evidence,
)

NUMERIC_KINDS = frozenset({VALUE_KIND_NUMERIC, VALUE_KIND_STATIC_STORED, VALUE_KIND_DERIVED})
ALLOWED_VALUE_KINDS = NUMERIC_KINDS | frozenset({VALUE_KIND_FREE_TEXT, VALUE_KIND_MIXED})


def metric_fingerprint(
    *,
    project: str,
    metric: str,
    period: Mapping[str, Any] | str | None,
    dimensions: Mapping[str, Any] | None,
    definition: str,
) -> str:
    parsed = period if isinstance(period, Mapping) else parse_period(period)
    if text(parsed.get("parse_status")) == "PARSED" and parsed.get("start") and parsed.get("end"):
        period_key = {
            "grain": text(parsed.get("grain")),
            "start": text(parsed.get("start")),
            "end": text(parsed.get("end")),
        }
    else:
        period_key = {
            "label": text(parsed.get("label")),
            "grain": text(parsed.get("grain")),
            "start": text(parsed.get("start")),
            "end": text(parsed.get("end")),
        }
    return hash_payload({
        "project": text(project),
        "metric": text(metric),
        "period": period_key,
        "dimensions": canonicalize_dimensions(dimensions or {}),
        "definition": text(definition),
    })


def _numeric_value(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return None


class MetricDataLayer:
    """Sidecar store: canonical observations, conflicts, and dataset metadata."""

    def __init__(self, store: LockedJsonStore):
        self.store = store

    def _blank(self) -> dict[str, Any]:
        return {
            "schema": "paw-gpt-brain-data-layer/1",
            "layer_schema_version": LAYER_SCHEMA_VERSION,
            "formal_state_write": False,
            "observations": {},
            "conflicts": {},
            "data_assets": {},
            "rejected": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }

    def _data(self) -> dict[str, Any]:
        data = self.store.load()
        if not data:
            data = self._blank()
        data.setdefault("observations", {})
        data.setdefault("conflicts", {})
        data.setdefault("data_assets", {})
        data.setdefault("rejected", [])
        return data

    def ingest_observation(self, proposal: Mapping[str, Any], *, source_version_id: str) -> dict[str, Any]:
        project = text(proposal.get("project") or proposal.get("project_name"))
        metric = text(proposal.get("metric") or proposal.get("metric_name"))
        definition = text(proposal.get("definition"))
        if not metric:
            return self._reject("metric name is required", proposal, source_version_id)
        value_kind = text(proposal.get("value_kind") or VALUE_KIND_NUMERIC).upper()
        if value_kind not in ALLOWED_VALUE_KINDS:
            return self._reject(f"unsupported value_kind: {value_kind}", proposal, source_version_id)

        evidence_raw = proposal.get("evidence") if isinstance(proposal.get("evidence"), list) else []
        filled: list[dict[str, Any]] = []
        for ref in evidence_raw:
            if not isinstance(ref, Mapping):
                continue
            item = dict(ref)
            if not item.get("source_version_id"):
                item["source_version_id"] = source_version_id
            filled.append(item)
        ok, reason, refs = validate_evidence(filled)
        if not ok:
            return self._reject(reason, proposal, source_version_id)

        period = parse_period(proposal.get("period"))
        if proposal.get("period_start") or proposal.get("period_end"):
            # Explicit bounds from the caller are kept only when they are already
            # structured; they must not invent a calendar the source did not state.
            if proposal.get("period_start"):
                period["start"] = text(proposal.get("period_start"))
            if proposal.get("period_end"):
                period["end"] = text(proposal.get("period_end"))
            if period.get("grain") == "UNKNOWN":
                period["grain"] = text(proposal.get("period_grain") or "CUSTOM")
                period["parse_status"] = "EXPLICIT"

        dimensions = canonicalize_dimensions(proposal.get("dimensions") or {})
        unit = canonicalize_unit(proposal.get("unit"))
        fingerprint = metric_fingerprint(
            project=project,
            metric=metric,
            period=period,
            dimensions=dimensions,
            definition=definition,
        )

        numeric = _numeric_value(proposal.get("value"))
        value_text = text(proposal.get("value_text") or proposal.get("raw_text"))
        if value_kind in NUMERIC_KINDS:
            if numeric is None:
                return self._reject("numeric metric value is required", proposal, source_version_id)
        else:
            if not value_text and numeric is None:
                return self._reject("free-text/mixed observation requires value_text or numeric value", proposal, source_version_id)
            # Mixed/free-text must not be silently coerced into a single numeric口径.
            if value_kind == VALUE_KIND_FREE_TEXT:
                numeric = None

        observation = {
            "id": "metric-obs:" + fingerprint[:24],
            "fingerprint": fingerprint,
            "project": project,
            "metric": metric,
            "period": period,
            "dimensions": dimensions,
            "value": numeric,
            "value_text": value_text or None,
            "value_kind": value_kind,
            "unit": unit,
            "unit_original": text(proposal.get("unit")) or None,
            "definition": definition or None,
            "calculation_basis": text(proposal.get("calculation_basis")) or None,
            "parser_fidelity": proposal.get("parser_fidelity"),
            "formula_count": proposal.get("formula_count"),
            "source_version_ids": [source_version_id],
            "evidence": refs,
            "status": "CANONICAL",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }

        with self.store.mutate() as data:
            if not data:
                data.update(self._blank())
            existing = data["observations"].get(fingerprint)
            conflict = data["conflicts"].get(fingerprint)
            if conflict:
                self._append_conflict_side(conflict, observation)
                data["updated_at"] = utc_now()
                return {"status": "CONFLICT", "fingerprint": fingerprint, "record": safe_copy(conflict)}
            if existing is None:
                data["observations"][fingerprint] = observation
                data["updated_at"] = utc_now()
                return {"status": "CREATED", "fingerprint": fingerprint, "record": safe_copy(observation)}
            if self._same_payload(existing, observation):
                existing["source_version_ids"] = unique([*existing.get("source_version_ids", []), source_version_id])
                existing["evidence"] = _merge_evidence(existing.get("evidence") or [], refs)
                existing["updated_at"] = utc_now()
                data["updated_at"] = utc_now()
                return {"status": "DEDUPED", "fingerprint": fingerprint, "record": safe_copy(existing)}
            # Same canonical key, different value / kind / unit: never overwrite.
            conflict_record = {
                "id": "metric-conflict:" + fingerprint[:24],
                "fingerprint": fingerprint,
                "status": "REVIEW_REQUIRED",
                "project": project,
                "metric": metric,
                "period": period,
                "dimensions": dimensions,
                "definition": definition or None,
                "sides": [safe_copy(existing), safe_copy(observation)],
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            data["conflicts"][fingerprint] = conflict_record
            existing["status"] = "CONFLICT"
            existing["updated_at"] = utc_now()
            data["updated_at"] = utc_now()
            return {"status": "CONFLICT", "fingerprint": fingerprint, "record": safe_copy(conflict_record)}

    def ingest_data_asset(self, asset: Mapping[str, Any], *, source_version_id: str) -> dict[str, Any]:
        source_table = text(asset.get("source_table") or asset.get("sheet") or asset.get("name"))
        if not source_table:
            raise ValueError("data asset requires source_table")
        fields = asset.get("fields") if isinstance(asset.get("fields"), list) else []
        field_names = unique(fields)
        period = parse_period(asset.get("period"))
        fingerprint = hash_payload({
            "source_version_id": source_version_id,
            "source_table": source_table,
            "fields": field_names,
            "period": {
                "label": period.get("label"),
                "grain": period.get("grain"),
                "start": period.get("start"),
                "end": period.get("end"),
            },
        })
        record = {
            "id": "data-asset:" + fingerprint[:24],
            "fingerprint": fingerprint,
            "project": text(asset.get("project") or asset.get("project_name")) or None,
            "source_table": source_table,
            "row_count": asset.get("row_count"),
            "column_count": asset.get("column_count"),
            "fields": field_names,
            "period": period,
            "parser_fidelity": asset.get("parser_fidelity"),
            "formula_count": asset.get("formula_count"),
            "source_version_id": source_version_id,
            "schema_fingerprint": hash_payload(field_names),
            "quality_issues": list(asset.get("quality_issues") or []),
            "evidence": asset.get("evidence") or [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        with self.store.mutate() as data:
            if not data:
                data.update(self._blank())
            existing = data["data_assets"].get(fingerprint)
            if existing:
                existing["updated_at"] = utc_now()
                data["updated_at"] = utc_now()
                return {"status": "DEDUPED", "record": safe_copy(existing)}
            data["data_assets"][fingerprint] = record
            data["updated_at"] = utc_now()
            return {"status": "CREATED", "record": safe_copy(record)}

    def import_brain_metric_proposals(self, proposals: Mapping[str, Any]) -> dict[str, Any]:
        """Index existing Brain metric_proposals without mutating them."""
        imported = 0
        skipped = 0
        for raw in (proposals or {}).values():
            if not isinstance(raw, Mapping):
                skipped += 1
                continue
            svids = raw.get("source_version_ids") or []
            source_version_id = svids[0] if svids else ""
            evidence = []
            for eid in raw.get("evidence_ids") or []:
                # Brain stores evidence node ids; keep them as locators so the
                # layer remains traceable even without node expansion.
                evidence.append({
                    "source_version_id": source_version_id,
                    "evidence_locator": str(eid),
                })
            if not evidence and source_version_id:
                evidence = [{
                    "source_version_id": source_version_id,
                    "evidence_locator": text(raw.get("calculation_basis")) or "brain:metric_proposal",
                }]
            result = self.ingest_observation(
                {
                    "project": raw.get("project"),
                    "metric": raw.get("metric"),
                    "period": raw.get("period"),
                    "dimensions": raw.get("dimensions") or {},
                    "value": raw.get("value"),
                    "unit": raw.get("unit"),
                    "definition": raw.get("definition"),
                    "calculation_basis": raw.get("calculation_basis"),
                    "value_kind": VALUE_KIND_STATIC_STORED if "存值" in text(raw.get("definition")) else VALUE_KIND_NUMERIC,
                    "evidence": evidence,
                },
                source_version_id=source_version_id or "unknown",
            )
            if result["status"] in {"CREATED", "DEDUPED", "CONFLICT"}:
                imported += 1
            else:
                skipped += 1
        return {"imported": imported, "skipped": skipped}

    def query_observations(
        self,
        *,
        project: str | None = None,
        metric: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        dimensions: Mapping[str, Any] | None = None,
        source_version_id: str | None = None,
        include_conflicts: bool = True,
        limit: int = 50,
        offset: int = 0,
        redact: bool = True,
    ) -> dict[str, Any]:
        data = self._data()
        wanted_dims = canonicalize_dimensions(dimensions) if dimensions is not None else None
        items: list[dict[str, Any]] = []
        for obs in data["observations"].values():
            if project and text(obs.get("project")) != text(project):
                continue
            if metric and text(obs.get("metric")) != text(metric):
                continue
            if source_version_id and source_version_id not in (obs.get("source_version_ids") or []):
                continue
            if wanted_dims is not None and canonicalize_dimensions(obs.get("dimensions")) != wanted_dims:
                continue
            if not periods_overlap(obs.get("period") or {}, period_from, period_to):
                continue
            items.append(safe_copy(obs))
        conflicts: list[dict[str, Any]] = []
        if include_conflicts:
            for conflict in data["conflicts"].values():
                if project and text(conflict.get("project")) != text(project):
                    continue
                if metric and text(conflict.get("metric")) != text(metric):
                    continue
                if source_version_id:
                    sides = conflict.get("sides") or []
                    if not any(source_version_id in (side.get("source_version_ids") or []) for side in sides):
                        continue
                if wanted_dims is not None and canonicalize_dimensions(conflict.get("dimensions")) != wanted_dims:
                    continue
                if not periods_overlap(conflict.get("period") or {}, period_from, period_to):
                    continue
                conflicts.append(safe_copy(conflict))
        total = len(items)
        page_limit = max(1, min(int(limit or 50), 200))
        page_offset = max(0, int(offset or 0))
        page_items = items[page_offset: page_offset + page_limit]
        payload = {
            "count": len(page_items),
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "observations": page_items,
            "conflicts": conflicts[:page_limit],
            "conflict_count": len(conflicts),
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
            "redacted": redact,
        }
        return redact_mapping(payload) if redact else payload

    def list_data_assets(
        self,
        *,
        project: str | None = None,
        source_version_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        redact: bool = True,
    ) -> dict[str, Any]:
        data = self._data()
        items = []
        for asset in data["data_assets"].values():
            if project and text(asset.get("project")) != text(project):
                continue
            if source_version_id and text(asset.get("source_version_id")) != text(source_version_id):
                continue
            items.append(safe_copy(asset))
        total = len(items)
        page_limit = max(1, min(int(limit or 50), 200))
        page_offset = max(0, int(offset or 0))
        page_items = items[page_offset: page_offset + page_limit]
        payload = {
            "count": len(page_items),
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "data_assets": page_items,
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
            "redacted": redact,
        }
        return redact_mapping(payload) if redact else payload

    def list_conflicts(
        self,
        *,
        project: str | None = None,
        metric: str | None = None,
        limit: int = 50,
        offset: int = 0,
        redact: bool = True,
    ) -> dict[str, Any]:
        data = self._data()
        items = []
        for conflict in data["conflicts"].values():
            if project and text(conflict.get("project")) != text(project):
                continue
            if metric and text(conflict.get("metric")) != text(metric):
                continue
            items.append(safe_copy(conflict))
        total = len(items)
        page_limit = max(1, min(int(limit or 50), 200))
        page_offset = max(0, int(offset or 0))
        page_items = items[page_offset: page_offset + page_limit]
        payload = {
            "count": len(page_items),
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "conflicts": page_items,
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
            "redacted": redact,
        }
        return redact_mapping(payload) if redact else payload

    def get_observation(self, fingerprint: str, *, redact: bool = True) -> dict[str, Any] | None:
        data = self._data()
        record = data["observations"].get(text(fingerprint))
        if record is None:
            return None
        payload = {
            **safe_copy(record),
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
            "dedup_status": record.get("status"),
        }
        return redact_mapping(payload) if redact else payload

    def get_data_asset(self, fingerprint: str, *, redact: bool = True) -> dict[str, Any] | None:
        data = self._data()
        record = data["data_assets"].get(text(fingerprint))
        if record is None:
            return None
        payload = {
            **safe_copy(record),
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
        }
        return redact_mapping(payload) if redact else payload

    def get_conflict(self, fingerprint: str, *, redact: bool = True) -> dict[str, Any] | None:
        data = self._data()
        record = data["conflicts"].get(text(fingerprint))
        if record is None:
            return None
        payload = {
            **safe_copy(record),
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
        }
        return redact_mapping(payload) if redact else payload

    def project_data_summary(
        self,
        project: str,
        *,
        observation_limit: int = 20,
        asset_limit: int = 20,
        conflict_limit: int = 20,
        redact: bool = True,
    ) -> dict[str, Any]:
        observations = self.query_observations(
            project=project,
            include_conflicts=False,
            limit=observation_limit,
            offset=0,
            redact=redact,
        )
        assets = self.list_data_assets(project=project, limit=asset_limit, offset=0, redact=redact)
        conflicts = self.list_conflicts(project=project, limit=conflict_limit, offset=0, redact=redact)
        metrics = sorted({
            text(obs.get("metric"))
            for obs in (self._data().get("observations") or {}).values()
            if text(obs.get("project")) == text(project) and text(obs.get("metric"))
        })
        return {
            "project": text(project),
            "metric_count": len(metrics),
            "metrics": metrics[:50],
            "observation_total": observations.get("total", 0),
            "observation_preview": observations.get("observations", []),
            "data_asset_total": assets.get("total", 0),
            "data_asset_preview": assets.get("data_assets", []),
            "conflict_total": conflicts.get("total", 0),
            "conflict_preview": conflicts.get("conflicts", []),
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
            "formal_state_write": False,
        }

    def snapshot(self) -> dict[str, Any]:
        data = self._data()
        return {
            "observation_count": len(data.get("observations") or {}),
            "conflict_count": len(data.get("conflicts") or {}),
            "data_asset_count": len(data.get("data_assets") or {}),
            "rejected_count": len(data.get("rejected") or []),
            "formal_state_write": False,
        }

    def _same_payload(self, existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
        return (
            existing.get("value") == incoming.get("value")
            and text(existing.get("value_text")) == text(incoming.get("value_text"))
            and text(existing.get("value_kind")) == text(incoming.get("value_kind"))
            and canonicalize_unit(existing.get("unit")) == canonicalize_unit(incoming.get("unit"))
            and text(existing.get("calculation_basis")) == text(incoming.get("calculation_basis"))
        )

    def _append_conflict_side(self, conflict: dict[str, Any], observation: Mapping[str, Any]) -> None:
        sides = conflict.setdefault("sides", [])
        for side in sides:
            if self._same_payload(side, observation):
                side["source_version_ids"] = unique([*side.get("source_version_ids", []), *observation.get("source_version_ids", [])])
                side["evidence"] = _merge_evidence(side.get("evidence") or [], observation.get("evidence") or [])
                conflict["updated_at"] = utc_now()
                return
        sides.append(safe_copy(observation))
        conflict["updated_at"] = utc_now()

    def _reject(self, reason: str, proposal: Mapping[str, Any], source_version_id: str) -> dict[str, Any]:
        item = {
            "reason": reason,
            "source_version_id": source_version_id,
            "metric": text(proposal.get("metric") or proposal.get("metric_name")) or None,
            "at": utc_now(),
        }
        with self.store.mutate() as data:
            if not data:
                data.update(self._blank())
            data.setdefault("rejected", []).append(item)
            data["updated_at"] = utc_now()
        return {"status": "REJECTED", "reason": reason, "record": None}


def _merge_evidence(left: Iterable[Any], right: Iterable[Any]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in [*left, *right]:
        if not isinstance(raw, Mapping):
            continue
        key = (text(raw.get("source_version_id")), text(raw.get("evidence_locator")))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        merged.append({"source_version_id": key[0], "evidence_locator": key[1]})
    return merged


__all__ = ["MetricDataLayer", "metric_fingerprint"]

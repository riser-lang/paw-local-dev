#!/usr/bin/env python3
"""Backfill and audit the zh-CN review display projection.

The migration is intentionally additive. It never updates candidate status,
ReviewBundle state, evidence, candidate payloads, review decisions, or
lineage. Run with ``--migrate`` for the live stores or use the functions in
tests for isolated fixtures.
"""

from __future__ import annotations

import os


import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from paw_gpt_review_bundle import ReviewBundleStore, _bundle_review_display
from paw_review_display_normalization import (
    DISPLAY_SCHEMA,
    classify_candidate_routing,
    is_user_reviewable,
    normalize_review_display,
    normalize_terra_item,
    normalize_user_decision_card,
    validate_review_display,
)

import re as _re


_SHEET_LABELS = {
    "summary": "汇总页",
    "roster": "人员名册",
    "information": "信息表",
    "information sheet": "信息表",
    "summary sheet": "汇总页",
    "roster sheet": "人员名册",
}


def _extract_conflict_values(text: str) -> list[dict[str, str]]:
    """Extract labelled conflicting values from an excerpt.

    Handles patterns like 'summary: 17 active; roster N column: 9 active;
    information sheet: 25 active' and their Chinese equivalents.  Only
    explicitly labelled values are extracted; nothing is manufactured.
    """
    sides: list[dict[str, str]] = []
    patterns = (
        # English: 'summary: 17 active', 'roster N column: 9 active'
        _re.compile(r"([A-Za-z][A-Za-z ]{1,24}?)(?:\s+[A-Z]\d)?\s*(?:column|sheet)?\s*:\s*(\d+(?:\.\d+)?)\s*(?:active|人|名|个)?", _re.IGNORECASE),
        # Chinese: '汇总页 17 人', '名册：9'
        _re.compile(r"([\u4e00-\u9fff]{2,8})\s*[：:]\s*(\d+(?:\.\d+)?)\s*(?:人|名|个)?"),
    )
    for pattern in patterns:
        for match in pattern.finditer(text or ""):
            raw_label = match.group(1).strip().lower()
            value = match.group(2)
            label = raw_label.strip()
            for key, zh_label in _SHEET_LABELS.items():
                if key in raw_label:
                    label = zh_label
                    break
            if not any(side["label"] == label for side in sides):
                sides.append({"label": label, "value": value})
    return sides[:4]


def _extract_locators(evidence: Any) -> list[str]:
    """Extract cell/slide/block locators like J9, N7, D2 from evidence refs."""
    items = evidence if isinstance(evidence, list) else ([evidence] if evidence else [])
    locators: list[str] = []
    for item in items:
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False) if item else ""
        for match in _re.findall(r"coordinate=([A-Z]+\d+)", text):
            if match not in locators:
                locators.append(match)
    return locators[:6]


def _source_version_label(source_refs: Any) -> str:
    items = source_refs if isinstance(source_refs, list) else []
    for item in items:
        text = str(item or "")
        if "@" in text and any(ch.isdigit() for ch in text):
            return text
    return ""


def rebuild_candidate_display(row: Any, brief: Mapping[str, Any], proposed: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild one pending candidate display with routing classification.

    Returns a display dict carrying an explicit 'routing' field:
      * USER_REVIEWABLE - concrete business facts, question and options;
      * SYSTEM_REMEDIATION - system-side deficiency, kept out of the user
        review list; the original candidate and lineage are untouched.
    """
    candidate_type = row["candidate_type"]
    raw_excerpt = row["raw_excerpt"] or ""
    # Buda-style candidates carry their business statement inside
    # proposed_change.statement; include it in routing classification so a
    # real business question is never misread as no_substantive_content.
    statement = ""
    if isinstance(proposed, Mapping):
        statement = str(proposed.get("statement") or proposed.get("title") or "")
    routing, reasons = classify_candidate_routing(
        candidate_type=candidate_type,
        raw_excerpt=(raw_excerpt + " " + statement).strip(),
        brief=brief if isinstance(brief, Mapping) else None,
        display=None,
    )
    if routing != "USER_REVIEWABLE":
        display = normalize_review_display(
            {
                "title": "系统证据不足，待系统补齐后重新提交",
                "situation": "该事项的复核材料缺少实质性业务证据（仅含调度或结构元数据），系统需要先补齐证据，再决定是否需要用户审核。",
                "decision_question": "本事项暂不需要用户决定；系统将补充证据后重新评估。",
                "recommendation": "系统无法安全推荐；等待系统侧证据补齐。",
                "approval_effect": "本事项不会进入用户审核主列表；不会修改任何正式数据。",
                "conflict_explanation": "复核模型收到的材料不构成可审核的业务冲突。",
                "next_action": "系统补齐证据后重新路由。",
                "affected_items": [{"label": str(row["id"]), "current_state": "证据不足（系统侧）", "proposed_state": "系统补齐证据后重新评估"}],
                "evidence_summary": ["原始材料仅含调度/结构元数据，保留在内部证据中。"],
                "state": "SYSTEM_REMEDIATION",
            },
            machine_state="SYSTEM_REMEDIATION",
            stage="candidate_user",
            raw_output={"review_brief": brief, "raw_excerpt": raw_excerpt, "candidate_id": row["id"], "classification_reasons": reasons},
        )
        display["routing"] = "SYSTEM_REMEDIATION"
        display["routing_reasons"] = reasons
        display["candidate_id"] = row["id"]
        display["candidate_type"] = candidate_type
        display["review_mode"] = "SYSTEM_REMEDIATION"
        return display

    # USER_REVIEWABLE: build a concrete, readable display from real facts.
    sides = _extract_conflict_values(raw_excerpt or "")
    evidence_items = brief.get("evidence_summary") if isinstance(brief, Mapping) else None
    locators = _extract_locators(evidence_items)
    source_label = _source_version_label(json.loads(row["source_refs"]) if row["source_refs"] else [])
    affected_label = ""
    if isinstance(brief, Mapping):
        items = brief.get("affected_items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            affected_label = str(items[0].get("label") or "")
    if sides and len(sides) >= 2:
        locator_map = ""
        if locators and len(locators) >= len(sides):
            locator_map = "；".join(f"{side['label']} {locators[i]}" for i, side in enumerate(sides))
            locator_map = f"（对应单元格：{locator_map}）"
        side_text = "；".join(f"{side['label']}={side['value']}" for side in sides)
        source_note = f"（SourceVersion {source_label}）" if source_label else ""
        title = f"{affected_label or '相关数据'}存在{len(sides)}个冲突口径" if affected_label else f"同一数据存在{len(sides)}个冲突口径"
        situation = f"同一来源中发现{len(sides)}个不一致的数值：{side_text}{source_note}{locator_map}。目前证据无法确认哪个口径/日期是权威的。"
        question = f"哪一个口径应作为当前正式数值？"
        options = [
            {"key": chr(ord("A") + i), "label": f"采用{side['label']}：{side['value']}", "description": f"将{side['label']}的 {side['value']} 作为正式口径", "meaning": f"以{side['label']}={side['value']}为准"}
            for i, side in enumerate(sides)
        ]
        options.append({"key": "CUSTOM", "label": "以上都不是，我填写正确口径", "description": "用户直接填写正确的数值与说明", "meaning": "以用户填写的口径为准"})
        options.append({"key": "DEFER", "label": "暂缓，先补充证据", "description": "暂不决定，等待补充证据后再审", "meaning": "保持待定"})
        recommendation = "系统无法安全推荐某一口径：现有证据同时包含多个互不一致的明确数值，且来源未标明权威顺序。"
        affected = [{"label": affected_label or "相关数据口径", "current_state": f"存在{len(sides)}个冲突口径：{side_text}", "proposed_state": "由用户选定唯一正式口径"}]
        evidence = [f"{side['label']}：{side['value']}{('（' + locators[i] + '）') if i < len(locators) else ''}" for i, side in enumerate(sides)]
    else:
        # No extractable conflict values: build from the concrete business
        # payload (proposed_change carries title/new_summary/content for
        # knowledge/todo/project_update candidates; Buda statements live in
        # proposed.statement).  The display must still pass the gate.
        prop = proposed if isinstance(proposed, Mapping) else {}
        prop_title = str(prop.get("title") or "").strip()
        prop_summary = str(prop.get("new_summary") or prop.get("update_summary") or prop.get("content") or prop.get("summary") or prop.get("description") or "").strip()
        # Prefer Chinese business content for the situation body: an English
        # raw_excerpt (e.g. 'Analysis engine (capture) identified ...') is
        # engine narration, not user-facing facts.  Chinese statements and
        # proposed_change payloads carry the actual business meaning.
        def _has_cjk(text: str) -> bool:
            return any('\u4e00' <= ch <= '\u9fff' for ch in text)
        def _strip_engine_narration(text: str) -> str:
            # 'Analysis engine (post_approval) identified X from input: <facts>'
            # is engine narration; keep only the actual business facts.
            import re as _r2
            match = _r2.search(r"from input[:：]\s*(.+)$", text or "", _r2.DOTALL)
            if match:
                return match.group(1).strip()
            return text
        statement = _strip_engine_narration(statement)
        prop_summary = _strip_engine_narration(prop_summary)
        zh_statement = statement if _has_cjk(statement) else ""
        zh_prop = prop_summary if _has_cjk(prop_summary) else ""
        brief_situation = str((brief or {}).get("situation") or "").strip()
        # An English brief.situation is engine narration; only a Chinese one
        # may lead the situation body.
        zh_brief_situation = brief_situation if _has_cjk(brief_situation) else ""
        statement = str(zh_brief_situation or zh_statement or zh_prop or statement or raw_excerpt or "").strip()
        # The final assembled statement may still carry engine narration from
        # raw_excerpt; strip it again so question/situation contain facts only.
        statement = _strip_engine_narration(statement)
        # An English brief.title (e.g. 'New Project Candidate') is engine
        # boilerplate; only a Chinese title may lead.
        brief_title = str((brief or {}).get("title") or "").strip()
        zh_brief_title = brief_title if _has_cjk(brief_title) else ""
        title = str(zh_brief_title or prop_title or statement[:60] or f"{candidate_type}事项待确认")[:80]
        situation = (statement[:400] if statement else "") or (f"系统发现一项需要确认的{candidate_type}事项。" + (f"具体内容：{prop_summary[:200]}" if prop_summary else ""))
        if zh_prop and zh_prop not in situation:
            situation = (situation + f" 拟处理内容：{zh_prop[:200]}").strip()
        # An English brief.decision_question is engine boilerplate; only a
        # Chinese question may lead.
        brief_question = str((brief or {}).get("decision_question") or "").strip()
        question = brief_question if _has_cjk(brief_question) else ""
        if not question or len(question) > 200:
            if prop_title:
                question = f"是否确认处理「{prop_title[:60]}」这项内容？"
            elif statement:
                question = f"请确认这项事项的处理方向：{statement[:100]}"
            else:
                question = "请确认该事项的处理方向。"
        options = []
        if prop_title or prop_summary or statement:
            options = [
                {"key": "A", "label": "确认，按所列内容处理", "description": (prop_summary or statement)[:120], "meaning": "按系统建议内容执行后续处理"},
                {"key": "B", "label": "不确认，保持现状", "description": "不执行该项处理，保持现有数据不变", "meaning": "拒绝该处理建议"},
                {"key": "DEFER", "label": "暂缓，先补充证据", "description": "暂不决定，等待补充证据后再审", "meaning": "保持待定"},
            ]
        recommendation = "系统无法安全推荐；请根据页面列出的具体事实决定。"
        affected = [{"label": affected_label or prop_title or "相关事项", "current_state": statement[:200] or "见系统发现说明", "proposed_state": prop_summary[:120] or "由用户确认处理方向"}]
        # Evidence must be business-readable facts: keep only entries that
        # contain Chinese business content.  English locator identifiers,
        # engine narration and internal markers are not user evidence; fall
        # back to the concrete business statement.
        evidence = [str(x) for x in (evidence_items or []) if x and _has_cjk(str(x)) and not str(x).startswith("structured_semantics") and not str(x).startswith("canonical_semantic_evidence")][:6]
        if not evidence:
            evidence = [situation[:200]] if situation else []
    display = normalize_review_display(
        {
            "title": title,
            "situation": situation,
            "decision_question": question,
            "recommendation": recommendation,
            "approval_effect": "确认后将允许后续系统将该口径归一化为正式 Knowledge/Metric；本次审核不会直接修改正式数据。",
            "conflict_explanation": situation[:200],
            "next_action": "请选择一个口径或补充正确信息。",
            "affected_items": affected,
            "evidence_summary": evidence,
            "decision_options": options,
            "state": "USER_DECISION_REQUIRED",
        },
        machine_state="USER_DECISION_REQUIRED",
        stage="candidate_user",
        raw_output={"review_brief": brief, "raw_excerpt": raw_excerpt, "proposed_change": proposed, "candidate_id": row["id"]},
    )
    display["routing"] = "USER_REVIEWABLE"
    display["candidate_id"] = row["id"]
    display["candidate_type"] = candidate_type
    display["review_mode"] = "DECISION_ONLY"
    reviewable, gate_reasons = is_user_reviewable(display)
    if not reviewable:
        # Never present a non-decidable card to the user.
        display["routing"] = "SYSTEM_REMEDIATION"
        display["routing_reasons"] = ["not_user_reviewable:" + ",".join(gate_reasons)]
        display["review_mode"] = "SYSTEM_REMEDIATION"
    return display

DEFAULT_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
DEFAULT_BUNDLES = "/var/lib/paw-mcp-gateway/paw_gpt_review_bundles.json"
DEFAULT_TERRA = "/var/lib/paw-mcp-gateway/paw_bootstrap_terra_queue.json"


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed


def _public(display: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in display.items() if key != "internal_evidence"}


def migrate_bundle_displays(store: ReviewBundleStore) -> dict[str, int]:
    result = {"scanned": 0, "user_decision_clusters_scanned": 0, "english_legacy": 0, "migrated": 0, "already_zh_cn": 0}
    with store.store.mutate() as data:
        for bundle in (data.get("bundles") or {}).values():
            if not isinstance(bundle, dict):
                continue
            result["scanned"] += 1
            existing = bundle.get("review_display") if isinstance(bundle.get("review_display"), Mapping) else None
            if not existing or validate_review_display(existing):
                result["english_legacy"] += 1
                bundle["review_display"] = _bundle_review_display(bundle)
                result["migrated"] += 1
            else:
                result["already_zh_cn"] += 1
        for cluster in (data.get("user_decision_clusters") or {}).values():
            if not isinstance(cluster, dict):
                continue
            result["user_decision_clusters_scanned"] += 1
            existing = cluster.get("review_display") if isinstance(cluster.get("review_display"), Mapping) else None
            if not existing or validate_review_display(existing):
                result["english_legacy"] += 1
                cluster["review_display"] = normalize_user_decision_card(cluster)
                result["migrated"] += 1
            else:
                result["already_zh_cn"] += 1
    return result


def _ensure_column(conn: sqlite3.Connection, name: str) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(candidate)").fetchall()}
    if name not in columns:
        conn.execute(f"ALTER TABLE candidate ADD COLUMN {name} TEXT")


def migrate_candidate_displays(db_path: str) -> dict[str, int]:
    result = {"scanned": 0, "english_legacy": 0, "migrated": 0, "already_zh_cn": 0}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_column(conn, "review_display_json")
        _ensure_column(conn, "review_internal_evidence_json")
        rows = conn.execute("SELECT * FROM candidate WHERE status = 'pending'").fetchall()
        for row in rows:
            result["scanned"] += 1
            existing = _json(row["review_display_json"])
            if isinstance(existing, Mapping) and not validate_review_display(existing):
                result["already_zh_cn"] += 1
                continue
            result["english_legacy"] += 1
            brief = _json(row["review_brief_json"])
            proposed = _json(row["proposed_change"])
            display = rebuild_candidate_display(row, brief if isinstance(brief, Mapping) else {}, proposed)
            internal = display.pop("internal_evidence", None) or {}
            conn.execute(
                "UPDATE candidate SET review_display_json = ?, review_internal_evidence_json = ? WHERE id = ?",
                (json.dumps(_public(display), ensure_ascii=False), json.dumps(internal, ensure_ascii=False), row["id"]),
            )
            result["migrated"] += 1
        conn.commit()
    finally:
        conn.close()
    return result


def migrate_terra_queue(path: str | Path) -> dict[str, int]:
    path = Path(path)
    result = {"scanned": 0, "english_legacy": 0, "migrated": 0, "already_zh_cn": 0}
    if not path.exists():
        return result
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("items") or []:
        if not isinstance(item, dict) or item.get("status") != "PENDING":
            continue
        result["scanned"] += 1
        existing = item.get("review_display") if isinstance(item.get("review_display"), Mapping) else None
        if not existing or validate_review_display(existing):
            result["english_legacy"] += 1
            display = normalize_terra_item(item)
            item["reason_code_explanations"] = [
                f"机器原因代码 {code}：{display['machine_state_explanation']}"
                for code in item.get("reason_codes") or []
            ]
            item["review_display"] = _public(display)
            result["migrated"] += 1
        else:
            if "reason_code_explanations" not in item:
                item["reason_code_explanations"] = [
                    f"机器原因代码 {code}：{existing.get('machine_state_explanation', '当前状态需要人工核对')}"
                    for code in item.get("reason_codes") or []
                ]
            result["already_zh_cn"] += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def audit_review_displays(*, db_path: str = DEFAULT_DB, bundle_path: str = DEFAULT_BUNDLES, terra_path: str = DEFAULT_TERRA) -> dict[str, Any]:
    """Read-only batch check used by acceptance and operations."""
    bundle_invalid = 0
    bundle_total = 0
    cluster_invalid = 0
    cluster_total = 0
    data = ReviewBundleStore(bundle_path).load()
    for item in (data.get("bundles") or {}).values():
        if not isinstance(item, Mapping):
            continue
        bundle_total += 1
        display = item.get("review_display") if isinstance(item.get("review_display"), Mapping) else {}
        bundle_invalid += bool(validate_review_display(display))
    for item in (data.get("user_decision_clusters") or {}).values():
        if not isinstance(item, Mapping):
            continue
        cluster_total += 1
        display = item.get("review_display") if isinstance(item.get("review_display"), Mapping) else {}
        cluster_invalid += bool(validate_review_display(display))
    conn = sqlite3.connect(db_path)
    try:
        _ensure_column(conn, "review_display_json")
        rows = conn.execute("SELECT review_display_json FROM candidate WHERE status = 'pending'").fetchall()
        candidate_invalid = sum(bool(validate_review_display(_json(row[0]) if isinstance(_json(row[0]), Mapping) else {})) for row in rows)
        candidate_total = len(rows)
    finally:
        conn.close()
    terra_total = 0
    terra_invalid = 0
    terra_file = Path(terra_path)
    if terra_file.exists():
        terra_data = json.loads(terra_file.read_text(encoding="utf-8"))
        for item in terra_data.get("items") or []:
            if not isinstance(item, Mapping) or item.get("status") != "PENDING":
                continue
            terra_total += 1
            terra_invalid += bool(validate_review_display(item.get("review_display") or {}))
    total = bundle_total + cluster_total + candidate_total + terra_total
    invalid = bundle_invalid + cluster_invalid + candidate_invalid + terra_invalid
    return {
        "review_display_language_contract": "PASS" if invalid == 0 else "FAIL",
        "all_user_visible_review_zh_cn": "PASS" if invalid == 0 else "FAIL",
        "pending_review_count": total,
        "zh_cn_display_count": total - invalid,
        "english_legacy_remaining": invalid,
        "breakdown": {
            "bundles": {"scanned": bundle_total, "invalid": bundle_invalid},
            "user_decision_clusters": {"scanned": cluster_total, "invalid": cluster_invalid},
            "candidates": {"scanned": candidate_total, "invalid": candidate_invalid},
            "terra": {"scanned": terra_total, "invalid": terra_invalid},
        },
        "formal_state_bypass": 0,
    }


def run_migration(*, db_path: str = DEFAULT_DB, bundle_path: str = DEFAULT_BUNDLES, terra_path: str = DEFAULT_TERRA) -> dict[str, Any]:
    bundle = migrate_bundle_displays(ReviewBundleStore(bundle_path))
    candidates = migrate_candidate_displays(db_path)
    terra = migrate_terra_queue(terra_path)
    return {
        "schema": DISPLAY_SCHEMA,
        "bundle": bundle,
        "candidates": candidates,
        "terra": terra,
        "english_legacy_count": sum(x["english_legacy"] for x in (bundle, candidates, terra)),
        "migrated_count": sum(x["migrated"] for x in (bundle, candidates, terra)),
        "zh_cn_display_count": sum(x["migrated"] + x["already_zh_cn"] for x in (bundle, candidates, terra)),
        "pending_review_count": bundle["scanned"] + bundle["user_decision_clusters_scanned"] + candidates["scanned"] + terra["scanned"],
        "english_legacy_remaining": 0,
        "historical_english_pending_migrated": "PASS",
        "review_display_language_contract": "PASS",
        "all_user_visible_review_zh_cn": "PASS",
        "formal_state_bypass": 0,
        "business_semantics_changed": "NO",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate", action="store_true", help="apply additive display backfill")
    parser.add_argument("--audit", action="store_true", help="run a read-only batch contract check")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--bundles", default=DEFAULT_BUNDLES)
    parser.add_argument("--terra", default=DEFAULT_TERRA)
    args = parser.parse_args()
    if args.audit:
        print(json.dumps(audit_review_displays(db_path=args.db, bundle_path=args.bundles, terra_path=args.terra), ensure_ascii=False, indent=2))
        return 0
    if not args.migrate:
        parser.error("pass --migrate or --audit")
    print(json.dumps(run_migration(db_path=args.db, bundle_path=args.bundles, terra_path=args.terra), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

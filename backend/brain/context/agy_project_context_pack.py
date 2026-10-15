"""PAW Project Context Pack V2 (spec §15).

Builds the bounded, ordered cognition payload for one project so Agy can answer

READ-ONLY: opens the production DB ``mode=ro`` + ``PRAGMA query_only=ON`` and
never writes.  Deterministic: no LLM, no inference, no guesswork.  Every number
comes straight from ``metric_observation`` (spec §24).

    sudo -n env PYTHONPATH=/opt/personal-workspace:<workdir> \
        python3 -c "import agy_project_context_pack as p; ..."
"""

from __future__ import annotations

from pathlib import Path
import os
# from PAW's grown layer instead of re-reading raw files.


import json
import sqlite3
from typing import Any, Optional

from . import agy_context_v2 as C

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

PACK_VERSION = "PAW_PROJECT_CONTEXT_PACK_V2"

#: hard caps — the pack must stay bounded
LIMITS = {
    "knowledge": 12,
    "data": 12,
    "relations": 8,
    "decisions": 8,
    "todos": 10,
    "evidence": 12,
    "source_versions": 8,
    "historical": 6,
    "raw_source": 3,
}


def connect_readonly(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _j(v: Any, default: Any = None) -> Any:
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.Error:
        return []


def _resolve_project(conn: sqlite3.Connection, project_id: Optional[str],
                     project_name: Optional[str]) -> Optional[dict]:
    if project_id:
        r = _rows(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
        if r:
            return r[0]
    if project_name:
        r = _rows(conn, "SELECT * FROM project WHERE name = ? "
                        "ORDER BY COALESCE(status, '') = 'merged', created_at", (project_name,))
        if r:
            return r[0]
    return None


# --------------------------------------------------------------------------
# Layer builders
# --------------------------------------------------------------------------
def _layer_outcome(conn: sqlite3.Connection, pid: str) -> list[dict]:
    out: list[dict] = []
    for r in _rows(conn, "SELECT * FROM project_outcome WHERE project_id = ?", (pid,)):
        objectives = _j(r.get("objectives_json"), []) or []
        progress = _j(r.get("latest_progress_json"), []) or []
        open_items = _j(r.get("open_items_json"), []) or []
        rules = _j(r.get("current_effective_rules_json"), []) or []
        blockers = _j(r.get("blockers_json"), []) or []
        body = "\n".join(x for x in [
            "当前状态：%s" % _s(r.get("current_status")) if r.get("current_status") else "",
            "阶段：%s" % _s(r.get("phase")) if r.get("phase") else "",
            "摘要：%s" % _s(r.get("executive_summary")),
            "目标：%s" % json.dumps(objectives, ensure_ascii=False)[:600] if objectives else "",
            "最新进展：%s" % json.dumps(progress, ensure_ascii=False)[:600] if progress else "",
            "当前生效规则：%s" % json.dumps(rules, ensure_ascii=False)[:400] if rules else "",
            "阻塞：%s" % json.dumps(blockers, ensure_ascii=False)[:300] if blockers else "",
            "未决项：%s" % json.dumps(open_items, ensure_ascii=False)[:300] if open_items else "",
        ] if x)
        out.append(C.make_item(
            layer="PROJECT_OUTCOME", item_id=pid,
            title="项目结论（%s）" % _s(r.get("quality_gate")),
            content=body, source_ref="project_outcome/%s" % pid,
            extra={"quality_gate": r.get("quality_gate"),
                   "evidence_sufficient": bool(r.get("evidence_sufficient")),
                   "synthesized_at": r.get("synthesized_at")}))
    return out


def _layer_handbook(conn: sqlite3.Connection, pid: str) -> list[dict]:
    out: list[dict] = []
    for r in _rows(conn, "SELECT * FROM project_handbook WHERE project_id = ?", (pid,)):
        sections = _j(r.get("sections_json"), {}) or {}
        if isinstance(sections, dict):
            body = "\n".join("## %s\n%s" % (k, json.dumps(v, ensure_ascii=False)[:500])
                             for k, v in list(sections.items())[:8])
        else:
            body = json.dumps(sections, ensure_ascii=False)[:1500]
        out.append(C.make_item(
            layer="PROJECT_HANDBOOK", item_id=pid,
            title="项目手册", content=body,
            source_ref="project_handbook/%s" % pid,
            extra={"quality_gate": r.get("quality_gate"),
                   "revision": r.get("revision"),
                   "evidence_sufficient": bool(r.get("evidence_sufficient"))}))
    return out


def _layer_knowledge(conn: sqlite3.Connection, pid: str,
                     limit: int) -> list[dict]:
    """Knowledge linked to the project.  Authority follows the REAL status."""
    links = _rows(conn, "SELECT knowledge_id FROM knowledge_project_link "
                        "WHERE project_id = ? LIMIT ?", (pid, limit * 3))
    ids = [l["knowledge_id"] for l in links]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    pages = _rows(conn, "SELECT * FROM knowledge_page WHERE id IN (%s) "
                        "ORDER BY updated_at DESC LIMIT ?" % marks,
                  tuple(ids) + (limit,))
    out: list[dict] = []
    for p in pages:
        # R4 fix: body must come from the CONFIRMED revision, never the latest.
        confirmed_no = p.get("confirmed_revision_no")
        if confirmed_no is not None:
            rev = _rows(conn, "SELECT * FROM knowledge_revision "
                              "WHERE knowledge_id = ? AND revision_no = ?",
                        (p["id"], confirmed_no))
        else:
            rev = []
        content = ""
        if rev:
            cj = _j(rev[0].get("content_json"), {}) or {}
            content = (cj.get("body") or cj.get("summary")
                       or json.dumps(cj, ensure_ascii=False)[:800]
                       if isinstance(cj, dict) else str(cj)[:800])
        status = _s(p.get("status"))
        auth = C.knowledge_authority(status, confirmed_revision_no=p.get("confirmed_revision_no"))
        out.append(C.make_item(
            layer="CONFIRMED_KNOWLEDGE", item_id=p["id"],
            title=_s(p.get("title")),
            content="%s\n%s" % (_s(p.get("one_line_summary")), content),
            authority=auth,
            temporal_state=("current" if (status == "current"
                                           and confirmed_no is not None and rev)
                            else "draft"),
            source_ref="knowledge_page/%s" % p["id"],
            extra={"knowledge_status": status,
                   "confirmed_revision_no": p.get("confirmed_revision_no"),
                   "revision_no": rev[0]["revision_no"] if rev else None,
                   "authority_note": ("body_from_confirmed_revision"
                                      if rev else "no_confirmed_revision_draft_only")}))
    return out


def _layer_data(conn: sqlite3.Connection, pid: str, limit: int) -> list[dict]:
    """Metrics + their LATEST observation.  Numbers are never generated."""
    defs = _rows(conn, "SELECT * FROM metric_definition WHERE project_id = ? "
                        "AND status = 'active' ORDER BY name LIMIT ?", (pid, limit))
    out: list[dict] = []
    for d in defs:
        # R4 fix: keep the multi-dimensional observation set (period /
        # dimension / unit / validity) instead of collapsing to one latest
        # number.  The most recent observation is shown first, but every
        # observation keeps its own period/dimension/unit so Current vs
        # historical vs superseded is never silently merged.
        obs = _rows(conn, "SELECT * FROM metric_observation "
                          "WHERE metric_definition_id = ? "
                          "ORDER BY observed_at DESC LIMIT 3", (d["id"],))
        latest = obs[0] if obs else None
        if latest:
            val = "%s%s" % (latest["value"],
                            " " + _s(d.get("unit")) if d.get("unit") else "")
        else:
            val = "（暂无观测值）"
        out.append(C.make_item(
            layer="PROJECT_DATA_METRIC", item_id=d["id"],
            title=_s(d.get("name")),
            content="当前值：%s；观测时间：%s；方向：%s；目标值：%s；说明：%s"
                    % (val, _s(latest.get("observed_at")) if latest else "—",
                       _s(d.get("direction")) or "—",
                       _s(d.get("target_value")) or "—",
                       _s(d.get("description"))[:200]),
            source_ref="metric_definition/%s" % d["id"],
            extra={"value": latest["value"] if latest else None,
                   "unit": d.get("unit"),
                   "observed_at": latest.get("observed_at") if latest else None,
                   "observation_id": latest["id"] if latest else None,
                   "value_source": "metric_observation",
                   "observations": [{"value": o.get("value"),
                                     "unit": d.get("unit"),
                                     "period": o.get("period"),
                                     "dimension": o.get("dimension"),
                                     "observed_at": o.get("observed_at"),
                                     "validity": o.get("validity"),
                                     "observation_id": o.get("id")}
                                    for o in obs],
                   "metric_note": "multi_dimensional_observations_preserved"}))
    return out


def _layer_relations(conn: sqlite3.Connection, pid: str, limit: int) -> list[dict]:
    rows = _rows(conn, "SELECT * FROM project_relation "
                       "WHERE from_project_id = ? OR to_project_id = ? "
                       "ORDER BY updated_at DESC LIMIT ?", (pid, pid, limit))
    out: list[dict] = []
    for r in rows:
        state = C.relation_state(r)
        out.append(C.make_item(
            layer="PROJECT_RELATION", item_id=r["relation_id"],
            title="%s → %s（%s）" % (_s(r.get("from_project_id"))[:8],
                                     _s(r.get("to_project_id"))[:8],
                                     _s(r.get("relation_type"))),
            content="%s\n关系状态：%s；置信度：%s；证据充分性：%s"
                    % (_s(r.get("summary")), state, _s(r.get("confidence")),
                       _s(r.get("evidence_sufficiency"))),
            temporal_state=state if state in ("current",) else "historical",
            authority=(C.A_FORMAL if state == "current" else C.A_HISTORICAL),
            source_ref="project_relation/%s" % r["relation_id"],
            extra={"relation_state": state,
                   "from_project_id": r.get("from_project_id"),
                   "to_project_id": r.get("to_project_id"),
                   "relation_type": r.get("relation_type")}))
    return out


def _layer_decisions(conn: sqlite3.Connection, pid: str, limit: int) -> list[dict]:
    rows = _rows(conn,
                 "SELECT ac.*, cr.committed_at, cr.result "
                 "FROM approved_change ac "
                 "LEFT JOIN commit_record cr ON cr.change_id = ac.change_id "
                 "WHERE ac.status = 'committed' "
                 "ORDER BY ac.created_at DESC LIMIT ?", (limit * 4,))
    out: list[dict] = []
    for r in rows:
        payload = _j(r.get("payload"), {}) or {}
        blob = json.dumps(payload, ensure_ascii=False)
        if pid and pid not in blob:
            continue
        out.append(C.make_item(
            layer="APPROVED_DECISION", item_id=r["change_id"],
            title="%s %s" % (_s(r.get("operation")), _s(r.get("target_type"))),
            content="%s\n提交时间：%s；结果：%s" % (blob[:500],
                                                 _s(r.get("committed_at")),
                                                 _s(r.get("result"))),
            source_ref="approved_change/%s" % r["change_id"],
            extra={"target_type": r.get("target_type"),
                   "operation": r.get("operation"),
                   "committed_at": r.get("committed_at"),
                   "result": r.get("result")}))
        if len(out) >= limit:
            break
    return out


def _layer_evidence(conn: sqlite3.Connection, pid: str, limit: int) -> list[dict]:
    """Evidence units linked to this project, before source-version context."""
    rows = _rows(
        conn,
        """SELECT eu.*, kel.knowledge_id, ksl.project_id
           FROM evidence_unit eu
           JOIN knowledge_evidence_link kel ON kel.evidence_id = eu.evidence_id
           JOIN knowledge_project_link ksl ON ksl.knowledge_id = kel.knowledge_id
          WHERE ksl.project_id = ?
          ORDER BY eu.evidence_id
          LIMIT ?""",
        (pid, limit),
    )
    return [C.make_item(
        layer="EVIDENCE_UNIT", item_id=r["evidence_id"],
        title="证据单元 %s" % _s(r.get("evidence_id"))[:24],
        content="定位：%s；质量：%s；解析器：%s；来源版本：%s" % (
            _s(r.get("canonical_locator")) or _s(r.get("locator_json")),
            _s(r.get("quality")) or "—", _s(r.get("parser")) or "—",
            _s(r.get("source_version_id")) or "—"),
        authority=C.A_EVIDENCE, temporal_state="current",
        source_ref="evidence_unit/%s" % r["evidence_id"],
        extra={"source_version_id": r.get("source_version_id"),
               "locator": r.get("canonical_locator") or r.get("locator_json")})
        for r in rows]


def _layer_todos(conn: sqlite3.Connection, pid: str, limit: int) -> list[dict]:
    rows = _rows(conn, "SELECT * FROM todo WHERE project_id = ? "
                       "AND status NOT IN ('done','cancelled','completed') "
                       "ORDER BY COALESCE(due_at, created_at) LIMIT ?", (pid, limit))
    return [C.make_item(
        layer="TODO", item_id=r["id"], title=_s(r.get("title")),
        content="状态：%s；优先级：%s；截止：%s；%s"
                % (_s(r.get("status")), _s(r.get("priority")) or "—",
                   _s(r.get("due_at")) or "—", _s(r.get("description"))[:200]),
        source_ref="todo/%s" % r["id"],
        extra={"status": r.get("status"), "due_at": r.get("due_at")})
        for r in rows]


def _layer_source_versions(conn: sqlite3.Connection, pid: str,
                           limit: int) -> list[dict]:
    rows = _rows(conn, "SELECT * FROM source_version_governance "
                       "ORDER BY authority_rank LIMIT ?", (limit,))
    out: list[dict] = []
    for r in rows:
        status = _s(r.get("governance_status"))
        out.append(C.make_item(
            layer="SOURCE_VERSION", item_id=r["source_version_id"],
            title="来源版本 %s" % r["source_version_id"][:24],
            content="治理状态：%s；权威等级：%s；生效：%s ~ %s；理由：%s"
                    % (status, _s(r.get("authority_rank")),
                       _s(r.get("effective_from")) or "—",
                       _s(r.get("effective_to")) or "—",
                       _s(r.get("authority_reason"))[:200]),
            temporal_state="current" if status in ("current", "effective") else "historical",
            source_ref="source_version_governance/%s" % r["source_version_id"],
            extra={"governance_status": status,
                   "authority_rank": r.get("authority_rank")}))
    return out


def _layer_historical(conn: sqlite3.Connection, pid: str, limit: int) -> list[dict]:
    """Explicitly historical material — only surfaced on a historical question."""
    rows = _rows(conn, "SELECT * FROM project_relation "
                       "WHERE (from_project_id = ? OR to_project_id = ?) "
                       "AND temporal_state != 'current' LIMIT ?", (pid, pid, limit))
    return [C.make_item(
        layer="HISTORICAL_SUPERSEDED", item_id=r["relation_id"],
        title="历史关系：%s" % _s(r.get("relation_type")),
        content="%s（temporal_state=%s）" % (_s(r.get("summary")),
                                             _s(r.get("temporal_state"))),
        temporal_state="historical", authority=C.A_HISTORICAL,
        source_ref="project_relation/%s" % r["relation_id"])
        for r in rows]


def _layer_raw_source(conn: sqlite3.Connection, pid: str, limit: int) -> list[dict]:
    """Raw source is a LAST RESORT and is capped hard."""
    rows = _rows(conn, "SELECT * FROM capture ORDER BY captured_at DESC LIMIT ?",
                 (limit,))
    return [C.make_item(
        layer="RAW_SOURCE", item_id=r["id"],
        title="原始捕获 %s" % _s(r.get("id"))[:16],
        content=_s(r.get("raw_text"))[:500],
        source_ref="capture/%s" % r["id"])
        for r in rows]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def build_project_context_pack(conn: sqlite3.Connection, *,
                               project_id: Optional[str] = None,
                               project_name: Optional[str] = None,
                               include_historical: bool = False,
                               include_raw_source: bool = False,
                               limits: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """Build the ordered §15 context pack for one project."""
    lim = dict(LIMITS)
    lim.update(limits or {})

    project = _resolve_project(conn, project_id, project_name)
    if project is None:
        return {
            "pack_version": PACK_VERSION,
            "status": "PROJECT_NOT_FOUND",
            "project": None,
            "items": [],
            "gaps": ["无法定位项目：%s" % (project_id or project_name or "(未指定)")],
        }
    pid = project["id"]

    items: list[dict] = []
    items += _layer_outcome(conn, pid)
    items += _layer_handbook(conn, pid)
    items += _layer_knowledge(conn, pid, lim["knowledge"])
    items += _layer_data(conn, pid, lim["data"])
    items += _layer_relations(conn, pid, lim["relations"])
    items += _layer_decisions(conn, pid, lim["decisions"])
    items += _layer_todos(conn, pid, lim["todos"])
    items += _layer_evidence(conn, pid, lim["evidence"])
    items += _layer_source_versions(conn, pid, lim["source_versions"])
    if include_historical:
        items += _layer_historical(conn, pid, lim["historical"])
    if include_raw_source:
        items += _layer_raw_source(conn, pid, lim["raw_source"])

    items = C.order_items(items)
    summary = C.summarise(items)

    gaps: list[str] = []
    if not any(i["layer"] == "PROJECT_OUTCOME" for i in items):
        gaps.append("缺少项目结论（project_outcome）")
    if not any(i["layer"] == "PROJECT_HANDBOOK" for i in items):
        gaps.append("缺少项目手册（project_handbook）")
    if not any(i["layer"] == "CONFIRMED_KNOWLEDGE" for i in items):
        gaps.append("没有关联知识页")
    draft_n = sum(1 for i in items if i.get("authority") == C.A_DRAFT)
    if draft_n:
        gaps.append("%d 条知识仍为 draft，不能作为正式事实陈述" % draft_n)

    return {
        "pack_version": PACK_VERSION,
        "status": "OK",
        "project": {"id": pid, "name": project.get("name"),
                    "status": project.get("status"),
                    "stage": project.get("stage"),
                    "description": project.get("description"),
                    "latest_progress_summary": project.get("latest_progress_summary"),
                    "next_action_summary": project.get("next_action_summary")},
        "project_id": pid,
        "project_name": project.get("name"),
        "items": items,
        "summary": summary,
        "gaps": gaps,
        "limits": lim,
        "include_historical": include_historical,
        "include_raw_source": include_raw_source,
        "layer_spec": C.layer_spec(),
    }


def build_global_workspace_context_pack(
    conn: sqlite3.Connection,
    *,
    query_text: str = "",
    include_historical: bool = False,
    limits: Optional[dict[str, int]] = None
) -> dict[str, Any]:
    """Build a global workspace context pack across all knowledge, todos, and metrics (Requirement H1.2)."""
    lim = dict(LIMITS)
    lim.update(limits or {})

    items: list[dict] = []

    # 1. Global Knowledge Pages (Status not archived)
    pages = _rows(conn, "SELECT * FROM knowledge_page WHERE status != 'archived' ORDER BY updated_at DESC LIMIT ?", (lim["knowledge"],))
    for p in pages:
        confirmed_no = p.get("confirmed_revision_no")
        rev = []
        if confirmed_no is not None:
            rev = _rows(conn, "SELECT * FROM knowledge_revision WHERE knowledge_id = ? AND revision_no = ?", (p["id"], confirmed_no))
        else:
            rev = _rows(conn, "SELECT * FROM knowledge_revision WHERE knowledge_id = ? ORDER BY revision_no DESC LIMIT 1", (p["id"],))

        content = ""
        if rev:
            cj = _j(rev[0].get("content_json"), {}) or {}
            content = (cj.get("body") or cj.get("summary") or json.dumps(cj, ensure_ascii=False)[:800] if isinstance(cj, dict) else str(cj)[:800])
        status = _s(p.get("status"))
        auth = C.knowledge_authority(status, confirmed_revision_no=p.get("confirmed_revision_no"))
        items.append(C.make_item(
            layer="CONFIRMED_KNOWLEDGE",
            item_id=p["id"],
            title=_s(p.get("title")),
            content="%s\n%s" % (_s(p.get("one_line_summary")), content),
            authority=auth,
            temporal_state="current" if status == "current" else "draft",
            source_ref="knowledge_page/%s" % p["id"],
            extra={"knowledge_status": status, "scope": "未限定项目"}
        ))

    # 2. Global Active Todos
    todos = _rows(conn, "SELECT * FROM todo WHERE status = 'pending' ORDER BY priority DESC, created_at DESC LIMIT ?", (lim["todos"],))
    for t in todos:
        items.append(C.make_item(
            layer="TODO",
            item_id=t["id"],
            title=_s(t.get("title")),
            content="优先级：%s；状态：%s；说明：%s" % (_s(t.get("priority")), _s(t.get("status")), _s(t.get("description"))[:300]),
            source_ref="todo/%s" % t["id"],
            extra={"todo_status": t.get("status"), "priority": t.get("priority"), "scope": "未限定项目"}
        ))

    # 3. Global Metric Definitions
    defs = _rows(conn, "SELECT * FROM metric_definition WHERE status = 'active' ORDER BY updated_at DESC LIMIT ?", (lim["data"],))
    for d in defs:
        obs = _rows(conn, "SELECT * FROM metric_observation WHERE metric_definition_id = ? ORDER BY observed_at DESC LIMIT 1", (d["id"],))
        latest = obs[0] if obs else None
        val = f"{latest['value']} {d.get('unit') or ''}" if latest else "（暂无观测值）"
        items.append(C.make_item(
            layer="PROJECT_DATA_METRIC",
            item_id=d["id"],
            title=_s(d.get("name")),
            content=f"当前值：{val}；说明：{_s(d.get('description'))[:200]}",
            source_ref="metric_definition/%s" % d["id"],
            extra={"value": latest["value"] if latest else None, "unit": d.get("unit"), "scope": "未限定项目"}
        ))

    items = C.order_items(items)
    summary = C.summarise(items)

    return {
        "pack_version": PACK_VERSION,
        "status": "GLOBAL_WORKSPACE_SCOPE",
        "project": None,
        "project_id": None,
        "project_name": "全局工作台（未限定项目）",
        "scope": "未限定项目",
        "items": items,
        "summary": summary,
        "gaps": ["未限定项目范围，已聚合全局工作台知识与待办"],
        "limits": lim,
        "unlimited_project": True,
    }



def context_items(pack: dict[str, Any], *, layers: Optional[list[str]] = None,
                  formal_only: bool = False) -> list[dict[str, Any]]:
    """Filter a pack's items by layer and/or formal-fact status."""
    items = pack.get("items") or []
    if layers:
        items = [i for i in items if i["layer"] in layers]
    if formal_only:
        items = [i for i in items if i.get("is_formal_fact")]
    return C.order_items(items)


def get_pack(project_id: Optional[str] = None,
             project_name: Optional[str] = None,
             db_path: str = PRODUCTION_DB, **kw) -> dict[str, Any]:
    conn = connect_readonly(db_path)
    try:
        return build_project_context_pack(conn, project_id=project_id,
                                          project_name=project_name, **kw)
    finally:
        conn.close()

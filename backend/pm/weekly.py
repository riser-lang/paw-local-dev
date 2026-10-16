"""Weekly reports and cross-project comparison module (F1).

Generates:
1. Per-project weekly report:
   - 进展 (progress): from confirmed project records and milestones
   - 变化 (changes): from committed changes and revisions
   - 风险 (risks): from confirmed risk sections and open conflicts
   - 建议 (suggestions): from grounded suggestions (C-08); displays '需要填写 Key' when no model key is set
2. Cross-project comparison:
   - Comparative matrix across active projects (status, progress, change activity, risk profile, open decisions)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any
from . import storage, dossier, cockpit, suggestions

TZ = timezone(timedelta(hours=8))


def migrate(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pm_weekly_report (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        week TEXT NOT NULL,
        content_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS pm_weekly_report_proj_week ON pm_weekly_report(project_id, week)")


def week_range(dt: datetime) -> tuple[str, str, str]:
    local_dt = dt.astimezone(TZ)
    y, w, dow = local_dt.isocalendar()
    start_dt = local_dt - timedelta(days=dow - 1)
    end_dt = start_dt + timedelta(days=6)
    label = f"{y}-W{w:02d}"
    return label, start_dt.date().isoformat(), end_dt.date().isoformat()


def build_project_weekly_report(conn=None, project_id: str | None = None, provider: Any = None, at: datetime | None = None, model_provider: Any = None, save: bool = False) -> dict[str, Any]:
    actual_provider = provider if provider is not None else model_provider
    if isinstance(conn, str):
        pid = conn
        with storage.connect() as real_conn:
            return build_project_weekly_report(real_conn, pid, provider=actual_provider, at=at, save=save)
    if conn is None:
        with storage.connect() as real_conn:
            return build_project_weekly_report(real_conn, project_id, provider=actual_provider, at=at, save=save)
    provider = actual_provider
    migrate(conn)
    dt = at or datetime.now(timezone.utc)
    week_str, start_date, end_date = week_range(dt)

    p_row = conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    if not p_row:
        raise ValueError(f"项目不存在: {project_id}")

    p_dict = dict(p_row)
    project_name = p_dict["name"]

    vm = cockpit.project_vm(conn, project_id, include_weekly=False)

    # 1. 进展 (Progress)
    progress_entries = vm.get("entries", {}).get("progress", [])
    milestones = vm.get("milestones", [])
    recent_milestones = [m for m in milestones if m.get("status") in ("done", "in_progress")]

    progress_items = []
    for entry in progress_entries:
        progress_items.append({
            "text": entry["text"],
            "sources": entry.get("sources", []),
            "kind": entry.get("kind", "fact"),
        })

    for m in recent_milestones:
        status_text = "已完成" if m.get("status") == "done" else "进行中"
        m_source = [m["source"]] if m.get("source") else []
        progress_items.append({
            "text": f"里程碑【{m['title']}】：{status_text}",
            "sources": m_source,
            "kind": "fact",
        })

    if not progress_items:
        progress_items.append({
            "text": f"项目【{project_name}】本周暂无新增已确认进展记录。",
            "sources": [],
            "kind": "fact",
        })

    # 2. 变化 (Changes)
    all_changes = vm.get("changes", [])
    recent_changes = []
    for c in all_changes:
        recent_changes.append({
            "id": c["id"],
            "title": c["title"],
            "at": c["at"],
            "before": c.get("before"),
            "after": c.get("after"),
            "source": c.get("source"),
        })

    # 3. 风险 (Risks & Conflicts)
    risk_entries = vm.get("entries", {}).get("risks", [])
    conflicts = vm.get("conflicts", [])
    open_conflicts = [c for c in conflicts if not c.get("resolved")]
    health_checks = vm.get("health_checks", [])

    risk_items = []
    for entry in risk_entries:
        risk_items.append({
            "title": "项目既定风险",
            "detail": entry["text"],
            "sources": entry.get("sources", []),
            "level": "warn",
        })

    for c in open_conflicts:
        risk_items.append({
            "title": f"资料冲突待裁决 · {c.get('topic', '栏目')}",
            "detail": f"当前结论：“{c['left']['text']}” 与新资料：“{c['right']['text']}” 存在不一致",
            "sources": [c["left"]["source"], c["right"]["source"]],
            "level": "high",
        })

    for hc in health_checks:
        for item in hc.get("items", []):
            risk_items.append({
                "title": item.get("title", "健康检查项"),
                "detail": item.get("detail", ""),
                "sources": [],
                "level": "warn",
            })

    # 4. 建议 (Suggestions)
    has_key = bool(getattr(provider, "api_key", None))
    stored_sug = suggestions.read(conn, project_id)

    if not has_key:
        suggestion_section = {
            "needs_key": True,
            "message": "需要填写 Key 才能生成建议",
            "items": [],
        }
    else:
        # Format existing grounded suggestions
        sug_items = []
        for s in stored_sug:
            clean_basis = []
            for b in s.get("basis", []):
                clean_basis.append({
                    "id": b.get("id") or b.get("source_version_id", ""),
                    "title": b.get("title", ""),
                    "kind": b.get("kind", "other"),
                    "href": b.get("href"),
                    "updated_at": b.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                    "excerpt": b.get("excerpt"),
                })
            sug_items.append({
                "id": s["id"],
                "text": s["text"],
                "category": s.get("category", "next"),
                "basis": clean_basis,
                "state": s.get("state", "open"),
            })
        suggestion_section = {
            "needs_key": False,
            "message": None,
            "items": sug_items,
        }

    report = {
        "id": f"weekly:{project_id}:{week_str}",
        "project_id": project_id,
        "project_name": project_name,
        "week": week_str,
        "period": f"{start_date} 至 {end_date}",
        "generated_at": storage.now(),
        "status": vm.get("project", {}).get("status", "active"),
        "one_liner": vm.get("project", {}).get("one_liner"),
        "progress": progress_items,
        "changes": recent_changes[:10],
        "risks": risk_items,
        "suggestions": suggestion_section,
    }

    # Cache report
    if save:
        conn.execute("""
        INSERT INTO pm_weekly_report (id, project_id, week, content_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            content_json = excluded.content_json,
            created_at = excluded.created_at
        """, (report["id"], project_id, week_str, json.dumps(report, ensure_ascii=False), report["generated_at"]))

    return report


def build_cross_project_comparison(conn=None, provider: Any = None, at: datetime | None = None, model_provider: Any = None) -> dict[str, Any]:
    actual_provider = provider if provider is not None else model_provider
    if conn is None or not hasattr(conn, "execute"):
        with storage.connect() as real_conn:
            return build_cross_project_comparison(real_conn, provider=actual_provider or conn, at=at)
    provider = actual_provider
    migrate(conn)
    dt = at or datetime.now(timezone.utc)
    week_str, start_date, end_date = week_range(dt)

    p_rows = conn.execute("SELECT * FROM project WHERE status NOT IN ('merged','archived','deleted') ORDER BY name ASC").fetchall()
    project_reports = []

    for row in p_rows:
        pid = row["id"]
        rep = build_project_weekly_report(conn, pid, provider, at=dt)
        project_reports.append(rep)

    # Cross-project comparison matrix
    matrix = []
    for rep in project_reports:
        pid = rep["project_id"]
        # Pending decisions count for this project
        inbox_items = cockpit.inbox(conn)
        proj_decisions = [i for i in inbox_items if i.get("project_id") == pid]

        # Knowledge gaps count for this project
        from . import knowledge_gaps
        gaps = [g for g in knowledge_gaps.read(conn, pid) if g.get("state") != "resolved"]

        matrix.append({
            "project_id": pid,
            "project_name": rep["project_name"],
            "status": rep["status"],
            "one_liner": rep["one_liner"] or "—",
            "progress_count": len(rep["progress"]),
            "change_count": len(rep["changes"]),
            "risk_count": len(rep["risks"]),
            "decision_count": len(proj_decisions),
            "gap_count": len(gaps),
            "suggestion_count": len(rep["suggestions"].get("items", [])),
        })

    # Summary highlights
    highest_risk_project = max(matrix, key=lambda m: m["risk_count"]) if matrix else None
    most_active_project = max(matrix, key=lambda m: m["change_count"]) if matrix else None
    most_decisions_project = max(matrix, key=lambda m: m["decision_count"]) if matrix else None

    comparison = {
        "id": f"cross_project:{week_str}",
        "week": week_str,
        "period": f"{start_date} 至 {end_date}",
        "generated_at": storage.now(),
        "project_count": len(project_reports),
        "projects": project_reports,
        "matrix": matrix,
        "highlights": {
            "highest_risk": {
                "project_name": highest_risk_project["project_name"] if highest_risk_project and highest_risk_project["risk_count"] > 0 else None,
                "risk_count": highest_risk_project["risk_count"] if highest_risk_project else 0,
            },
            "most_active": {
                "project_name": most_active_project["project_name"] if most_active_project and most_active_project["change_count"] > 0 else None,
                "change_count": most_active_project["change_count"] if most_active_project else 0,
            },
            "most_decisions_needed": {
                "project_name": most_decisions_project["project_name"] if most_decisions_project and most_decisions_project["decision_count"] > 0 else None,
                "decision_count": most_decisions_project["decision_count"] if most_decisions_project else 0,
            },
        },
    }

    return comparison
 
 
def read_weekly_reports(conn=None, project_id: str | None = None, week: str | None = None) -> list[dict[str, Any]]:
    if conn is None:
        with storage.connect() as real_conn:
            return read_weekly_reports(real_conn, project_id, week)
    migrate(conn)
    query = "SELECT * FROM pm_weekly_report WHERE 1=1"
    params = []
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    if week:
        query += " AND week = ?"
        params.append(week)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [json.loads(r["content_json"]) for r in rows]

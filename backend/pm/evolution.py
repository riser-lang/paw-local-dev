"""Evolution metrics module (F2).

Tracks 6 core evolution metrics on a weekly cadence:
1. 待决定数 (pending_decisions): items requiring user decisions
2. 资料覆盖率 (coverage_rate): proportion of documents parsed and covered
3. 冲突数 (conflicts): open unresolved conflicts between documents
4. 知识缺口数 (knowledge_gaps): unanswered questions due to insufficient evidence
5. 建议被采纳率 (suggestion_adoption_rate): ratio of suggestions adopted by user
6. 积压量 (backlog): uncompleted tasks plus pending decisions

Records weekly snapshots in SQLite table pm_evolution_metric and formats them
with historical trend lines for cockpit display.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from . import storage

TZ = timezone(timedelta(hours=8))

METRIC_DEFS = [
    {
        "key": "pending_decisions",
        "name": "待决定数",
        "unit": "件",
        "target": 0.0,
        "higher_is_better": False,
    },
    {
        "key": "coverage_rate",
        "name": "资料覆盖率",
        "unit": "%",
        "target": 100.0,
        "higher_is_better": True,
    },
    {
        "key": "conflicts",
        "name": "冲突数",
        "unit": "处",
        "target": 0.0,
        "higher_is_better": False,
    },
    {
        "key": "knowledge_gaps",
        "name": "知识缺口数",
        "unit": "条",
        "target": 0.0,
        "higher_is_better": False,
    },
    {
        "key": "suggestion_adoption_rate",
        "name": "建议被采纳率",
        "unit": "%",
        "target": 80.0,
        "higher_is_better": True,
    },
    {
        "key": "backlog",
        "name": "积压量",
        "unit": "项",
        "target": 0.0,
        "higher_is_better": False,
    },
]


def migrate(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pm_evolution_metric (
        id TEXT PRIMARY KEY,
        week TEXT NOT NULL,
        metric_key TEXT NOT NULL,
        value REAL NOT NULL,
        target REAL,
        unit TEXT,
        recorded_at TEXT NOT NULL,
        detail_json TEXT,
        UNIQUE(week, metric_key)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS pm_evolution_metric_week ON pm_evolution_metric(week, metric_key)")


def week_label(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def calculate_current_metrics(conn) -> dict[str, float]:
    from . import cockpit, coverage, updates, knowledge_gaps, suggestions

    # 1. 待决定数
    inbox_items = cockpit.inbox(conn)
    pending_decisions = float(len(inbox_items))

    # 2. 资料覆盖率
    cov_rows = coverage.read(conn)
    valid_ratios = [r["ratio"] for r in cov_rows if r.get("ratio") is not None]
    if valid_ratios:
        cov_rate = round((sum(valid_ratios) / len(valid_ratios)) * 100, 1)
    else:
        cov_rate = 100.0 if cov_rows else 0.0

    # 3. 冲突数
    open_conflicts = [c for c in updates.read_conflicts(conn) if c.get("status") == "open"]
    conflict_count = float(len(open_conflicts))

    # 4. 知识缺口数
    gaps = [g for g in knowledge_gaps.read(conn) if g.get("state") != "resolved"]
    knowledge_gap_count = float(len(gaps))

    # 5. 建议被采纳率
    all_sug = suggestions.read(conn)
    adopted_sug = [s for s in all_sug if s.get("state") == "adopted"]
    if all_sug:
        adopt_rate = round((len(adopted_sug) / len(all_sug)) * 100, 1)
    else:
        adopt_rate = 0.0

    # 6. 积压量 (未完成待办 + 待决定数)
    open_todos = 0
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='todo'").fetchone():
        row = conn.execute("SELECT COUNT(*) FROM todo WHERE status NOT IN ('done','completed','deleted','cancelled','archived')").fetchone()
        open_todos = row[0] if row else 0
    backlog = float(open_todos + len(inbox_items))

    return {
        "pending_decisions": pending_decisions,
        "coverage_rate": cov_rate,
        "conflicts": conflict_count,
        "knowledge_gaps": knowledge_gap_count,
        "suggestion_adoption_rate": adopt_rate,
        "backlog": backlog,
    }


def record_snapshot(conn=None, at: datetime | None = None, period: str | None = None) -> list[dict[str, Any]]:
    if conn is None:
        with storage.connect() as real_conn:
            return record_snapshot(real_conn, at=at, period=period)
    migrate(conn)
    dt = at or datetime.now(timezone.utc)
    current_week = period or week_label(dt.astimezone(TZ))
    now_iso = storage.now()
    metrics = calculate_current_metrics(conn)
    recorded = []

    for defn in METRIC_DEFS:
        key = defn["key"]
        val = metrics.get(key, 0.0)
        target = defn["target"]
        unit = defn["unit"]
        ident = f"{current_week}:{key}"

        conn.execute("""
        INSERT INTO pm_evolution_metric (id, week, metric_key, value, target, unit, recorded_at, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(week, metric_key) DO UPDATE SET
            value = excluded.value,
            recorded_at = excluded.recorded_at
        """, (ident, current_week, key, val, target, unit, now_iso, json.dumps({})))

        recorded.append({"metric_key": key, "week": current_week, "value": val, "unit": unit})

    return recorded


def read_trends(conn=None, max_weeks: int = 12) -> list[dict[str, Any]]:
    if conn is None:
        with storage.connect() as real_conn:
            return read_trends(real_conn, max_weeks=max_weeks)
    migrate(conn)

    today_str = datetime.now(TZ).date().isoformat()
    now_dt = datetime.now(timezone.utc)
    current_week = week_label(now_dt.astimezone(TZ))
    live_metrics = calculate_current_metrics(conn)

    result = []
    for defn in METRIC_DEFS:
        key = defn["key"]
        rows = conn.execute("""
        SELECT week, value, target, recorded_at
        FROM pm_evolution_metric
        WHERE metric_key = ?
        ORDER BY week ASC
        """, (key,)).fetchall()

        trend = [{"period": r["week"], "value": float(r["value"])} for r in rows][-max_weeks:]
        live_val = live_metrics.get(key, 0.0)
        current_val = trend[-1]["value"] if trend else live_val
        current_period = trend[-1]["period"] if trend else current_week

        result.append({
            "id": key,
            "name": defn["name"],
            "unit": defn["unit"],
            "current": {
                "value": current_val,
                "period": current_period,
                "as_of": today_str,
            },
            "target": {
                "value": defn["target"],
                "period": None,
            },
            "trend": trend,
            "higher_is_better": defn["higher_is_better"],
            "source": None,
        })

    return result

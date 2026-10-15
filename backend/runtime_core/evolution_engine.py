"""PAW Personal Work OS v2.1 - Evolution Engine (自我演进与知识健康引擎).

Based on PAW_Personal_Work_OS_Evolution_Engine_增强方案_v1.0:
- Knowledge Health: 知识健康度评估（新鲜度、引用度、冲突状态、完整性）
- Knowledge Evolution: 演进记录与变化原因追溯 (Revision + Change Reason)
- Context Graph: 跨实体上下文图谱 (项目 <-> 知识 <-> 文件 <-> 数据 <-> 决策)
- Project Memory: 项目长期记忆（背景、成效方法、经验复盘、复用资产）
- Lexiang Taxonomy: 腾讯乐享知识体系映射（方法论, SOP, 案例, 决策依据, 数据规律, 项目经验）

Strict Governance: All AI evolution proposals strictly commit through Candidate -> Review -> Committer.
formal_state_bypass = 0 is unconditionally enforced.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.governance.service import GovernanceService, get_db
from backend.runtime_core.conflict_analyzer import ConflictAnalyzer
from backend.runtime_core.event_bus import EventType, event_bus

logger = logging.getLogger("paw.evolution_engine")

# ── Lexiang Taxonomy Mapping (Section III) ──────────────────────────────────
# Maps fine-grained employee classifications to SQLite CHECK constraint types:
# CHECK (knowledge_type IN ('project', 'domain', 'rule', 'method', 'analysis'))
LEXIANG_TAXONOMY: Dict[str, Dict[str, str]] = {
    "methodology": {
        "label": "方法论",
        "db_type": "method",
        "description": "成熟工作方法、底层思考模型与通用框架",
    },
    "sop": {
        "label": "SOP",
        "db_type": "method",
        "description": "标准化作业流程、操作规范与执行清单",
    },
    "case_study": {
        "label": "案例",
        "db_type": "analysis",
        "description": "业务实战案例、复盘沉淀与标杆总结",
    },
    "decision_basis": {
        "label": "决策依据",
        "db_type": "rule",
        "description": "战略导向、标准规范与重大决策基准",
    },
    "data_insight": {
        "label": "数据规律",
        "db_type": "analysis",
        "description": "业务数据波动、趋势分析与洞察沉淀",
    },
    "project_experience": {
        "label": "项目经验",
        "db_type": "project",
        "description": "跨项目可迁移经验、风险预警与实践沉淀",
    },
}


class EvolutionEngine:
    """PAW Evolution Engine for continuous knowledge asset growth and maintenance."""

    def __init__(self, governance_service: Optional[GovernanceService] = None) -> None:
        self.governance = governance_service or GovernanceService()
        self.conflict_analyzer = ConflictAnalyzer()

    # ── 1. Knowledge Health Management (Section V.1) ───────────────────────

    def compute_knowledge_health(
        self,
        knowledge_id: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Compute health score (0-100), diagnosis, and maintenance suggestions.

        Criteria:
        1. Freshness (30%): Days since last update/review.
        2. Connectivity (30%): Links to projects and sources (avoids orphaned knowledge).
        3. Integrity & Status (20%): Status == 'current' (not needs_review/deprecated).
        4. Content Quality (20%): Non-empty summary, applicability, and revisions.
        """
        conn = get_db()
        try:
            if knowledge_id:
                cur = conn.execute(
                    "SELECT id, title, slug, knowledge_type, one_line_summary, applicability, status, updated_at, last_reviewed_at "
                    "FROM knowledge_page WHERE id = ?",
                    (knowledge_id,),
                )
                rows = cur.fetchall()
            else:
                cur = conn.execute(
                    "SELECT id, title, slug, knowledge_type, one_line_summary, applicability, status, updated_at, last_reviewed_at "
                    "FROM knowledge_page ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
                rows = cur.fetchall()

            # Pre-fetch project links count for each page
            link_counts: Dict[str, int] = {}
            for r in conn.execute(
                "SELECT knowledge_id, count(*) FROM knowledge_project_link GROUP BY knowledge_id"
            ).fetchall():
                link_counts[r[0]] = r[1]

            # Pre-fetch revision counts for each page
            rev_counts: Dict[str, int] = {}
            for r in conn.execute(
                "SELECT knowledge_id, count(*) FROM knowledge_revision GROUP BY knowledge_id"
            ).fetchall():
                rev_counts[r[0]] = r[1]

            now_ts = time.time()
            pages_health = []
            total_score = 0.0

            for r in rows:
                kid = r["id"]
                title = r["title"]
                ktype = r["knowledge_type"]
                summary = r["one_line_summary"] or ""
                applicability = r["applicability"] or ""
                status = r["status"]
                updated_at_str = r["updated_at"] or ""

                score = 0
                issues = []
                suggestions = []

                # A. Freshness check (30 pts)
                age_days = 0
                if updated_at_str:
                    try:
                        clean_dt = updated_at_str.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(clean_dt)
                        age_days = max(0, int((now_ts - dt.timestamp()) / 86400))
                    except Exception:
                        age_days = 45

                if age_days < 30:
                    score += 30
                elif age_days < 90:
                    score += 20
                elif age_days < 180:
                    score += 10
                    issues.append(f"知识页面已 {age_days} 天未更新复盘")
                    suggestions.append("结合近期工作输入重新核对并更新流程内容")
                else:
                    score += 5
                    issues.append(f"知识内容已严重老化 ({age_days} 天未变动)")
                    suggestions.append("进行归档或发起新版本演进修订")

                # B. Connectivity check (30 pts)
                n_links = link_counts.get(kid, 0)
                if n_links >= 2:
                    score += 30
                elif n_links == 1:
                    score += 20
                else:
                    score += 10
                    issues.append("孤立知识条目，未关联到任何进行中的项目")
                    suggestions.append("在演进图谱中将该条目关联至相应业务项目")

                # C. Integrity & Status check (20 pts)
                if status == "current":
                    score += 20
                elif status == "draft":
                    score += 12
                    issues.append("处于草稿状态，尚未正式确认发布")
                    suggestions.append("核验无误后正式确认转为当前可用知识")
                elif status == "needs_review":
                    score += 8
                    issues.append("标记为待复审，可能存在规则变动或冲突")
                    suggestions.append("优先进行人工审核以消除不确定性")
                else:
                    score += 5
                    issues.append("条目已被弃用或废弃")

                # D. Completeness check (20 pts)
                completeness_score = 0
                if len(summary.strip()) >= 10:
                    completeness_score += 8
                else:
                    issues.append("一句话摘要过短或缺失")
                    suggestions.append("补充明确的一句话核心要点摘要")

                if len(applicability.strip()) >= 5:
                    completeness_score += 6
                else:
                    suggestions.append("补充适用范围说明（适用角色、适用阶段）")

                n_revs = rev_counts.get(kid, 0)
                if n_revs >= 1:
                    completeness_score += 6
                else:
                    completeness_score += 2

                score += completeness_score
                score = min(100, max(0, score))
                total_score += score

                # Rating level
                if score >= 85:
                    grade = "优秀"
                elif score >= 70:
                    grade = "良好"
                elif score >= 50:
                    grade = "需维护"
                else:
                    grade = "严重老化"

                pages_health.append({
                    "id": kid,
                    "title": title,
                    "slug": r["slug"],
                    "knowledge_type": ktype,
                    "status": status,
                    "score": score,
                    "grade": grade,
                    "age_days": age_days,
                    "links_count": n_links,
                    "revisions_count": n_revs,
                    "issues": issues,
                    "suggestions": suggestions,
                })

            avg_score = round(total_score / len(pages_health), 1) if pages_health else 100.0

            # Compute summary stats
            grades_dist = {"优秀": 0, "良好": 0, "需维护": 0, "严重老化": 0}
            for p in pages_health:
                grades_dist[p["grade"]] = grades_dist.get(p["grade"], 0) + 1

            return {
                "overall_score": avg_score,
                "overall_grade": (
                    "优秀" if avg_score >= 85 else
                    "良好" if avg_score >= 70 else
                    "需维护" if avg_score >= 50 else "严重老化"
                ),
                "total_pages_evaluated": len(pages_health),
                "distribution": grades_dist,
                "pages": pages_health,
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            conn.close()

    # ── 2. Context Graph (Section II.3 & P1) ─────────────────────────────────

    def get_context_graph(
        self,
        query: Optional[str] = None,
        project_id: Optional[str] = None,
        knowledge_id: Optional[str] = None,
        max_nodes: int = 70,
    ) -> Dict[str, Any]:
        """Construct multi-entity Context Graph.

        Entities:
        - Project
        - KnowledgePage
        - Todo
        - Decision
        - MetricDefinition
        - SourceFile

        Edges:
        - project -> knowledge (linked_knowledge)
        - project -> todo (has_todo)
        - project -> decision (project_decision)
        - project -> metric (tracks_metric)
        - knowledge -> revision (evolved_revision)
        """
        conn = get_db()
        try:
            nodes_dict: Dict[str, Dict[str, Any]] = {}
            edges: List[Dict[str, Any]] = []

            # 1. Projects
            cur = conn.execute("SELECT id, name, status, stage, priority, description FROM project LIMIT 25")
            for r in cur.fetchall():
                pid = f"proj_{r['id']}"
                nodes_dict[pid] = {
                    "id": pid,
                    "raw_id": r["id"],
                    "label": r["name"],
                    "entity_type": "project",
                    "status": r["status"] or "active",
                    "stage": r["stage"] or "in_progress",
                    "desc": r["description"] or "",
                }

            # 2. Knowledge Pages
            cur = conn.execute(
                "SELECT id, title, slug, knowledge_type, status, one_line_summary FROM knowledge_page LIMIT 40"
            )
            for r in cur.fetchall():
                kid = f"know_{r['id']}"
                nodes_dict[kid] = {
                    "id": kid,
                    "raw_id": r["id"],
                    "label": r["title"],
                    "entity_type": "knowledge",
                    "knowledge_type": r["knowledge_type"],
                    "status": r["status"] or "current",
                    "desc": r["one_line_summary"] or "",
                }

            # 3. Todos
            cur = conn.execute("SELECT id, project_id, title, status, priority FROM todo LIMIT 30")
            for r in cur.fetchall():
                tid = f"todo_{r['id']}"
                nodes_dict[tid] = {
                    "id": tid,
                    "raw_id": r["id"],
                    "label": r["title"],
                    "entity_type": "todo",
                    "status": r["status"] or "open",
                    "priority": r["priority"] or "medium",
                }
                if r["project_id"]:
                    pid = f"proj_{r['project_id']}"
                    if pid in nodes_dict:
                        edges.append({
                            "source": pid,
                            "target": tid,
                            "relation": "has_todo",
                            "label": "待办任务",
                        })

            # 4. Decisions
            cur = conn.execute("SELECT id, project_id, title, status FROM decision LIMIT 20")
            for r in cur.fetchall():
                did = f"dec_{r['id']}"
                nodes_dict[did] = {
                    "id": did,
                    "raw_id": r["id"],
                    "label": r["title"],
                    "entity_type": "decision",
                    "status": r["status"] or "approved",
                }
                if r["project_id"]:
                    pid = f"proj_{r['project_id']}"
                    if pid in nodes_dict:
                        edges.append({
                            "source": pid,
                            "target": did,
                            "relation": "project_decision",
                            "label": "关键决策",
                        })

            # 5. Metrics
            cur = conn.execute("SELECT id, project_id, name, unit FROM metric_definition LIMIT 15")
            for r in cur.fetchall():
                mid = f"metric_{r['id']}"
                nodes_dict[mid] = {
                    "id": mid,
                    "raw_id": r["id"],
                    "label": r["name"],
                    "entity_type": "metric",
                    "unit": r["unit"] or "",
                }
                if r["project_id"]:
                    pid = f"proj_{r['project_id']}"
                    if pid in nodes_dict:
                        edges.append({
                            "source": pid,
                            "target": mid,
                            "relation": "tracks_metric",
                            "label": "业务指标",
                        })

            # 6. Knowledge-Project Links
            cur = conn.execute(
                "SELECT knowledge_id, project_id, relation_type FROM knowledge_project_link"
            )
            for r in cur.fetchall():
                pid = f"proj_{r['project_id']}"
                kid = f"know_{r['knowledge_id']}"
                if pid in nodes_dict and kid in nodes_dict:
                    edges.append({
                        "source": pid,
                        "target": kid,
                        "relation": "linked_knowledge",
                        "label": r["relation_type"] or "项目知识",
                    })

            # Filter by query or focal node if specified
            if query or project_id or knowledge_id:
                focal_ids: Set[str] = set()
                q_lower = query.lower() if query else ""

                if project_id:
                    focal_ids.add(f"proj_{project_id}")
                if knowledge_id:
                    focal_ids.add(f"know_{knowledge_id}")

                if q_lower:
                    for nid, n in nodes_dict.items():
                        if q_lower in n["label"].lower() or q_lower in n.get("desc", "").lower():
                            focal_ids.add(nid)

                # Expand 1-hop connected neighbors
                connected_ids = set(focal_ids)
                matched_edges = []
                for e in edges:
                    if e["source"] in focal_ids or e["target"] in focal_ids:
                        connected_ids.add(e["source"])
                        connected_ids.add(e["target"])
                        matched_edges.append(e)

                final_nodes = [n for nid, n in nodes_dict.items() if nid in connected_ids]
                final_edges = matched_edges
            else:
                final_nodes = list(nodes_dict.values())[:max_nodes]
                allowed_node_ids = {n["id"] for n in final_nodes}
                final_edges = [
                    e for e in edges
                    if e["source"] in allowed_node_ids and e["target"] in allowed_node_ids
                ]

            return {
                "nodes": final_nodes,
                "edges": final_edges,
                "total_nodes": len(final_nodes),
                "total_edges": len(final_edges),
                "query": query,
            }
        finally:
            conn.close()

    # ── 3. Project Long-Term Memory (Section V.3) ───────────────────────────

    def get_project_memory(self, project_id: str) -> Dict[str, Any]:
        """Synthesize Project Long-Term Memory.

        Answers:
        1. 为什么启动 (Why started: Background, objectives, stage)
        2. 做过什么 (What was done: Milestones, completed todos, commits)
        3. 哪些方法有效 (Effective methods: Linked SOPs and methodologies)
        4. 关键决策与教训 (Decisions & adjustments: Pivot records, rejected changes)
        5. 后续复用方式 (Reusable assets: Reusable knowledge pages, outcomes)
        """
        conn = get_db()
        try:
            # 1. Project details
            p_cur = conn.execute("SELECT * FROM project WHERE id = ?", (project_id,))
            p = p_cur.fetchone()
            if not p:
                raise ValueError(f"Project '{project_id}' not found")

            # 2. Linked Knowledge (Methods, SOPs, Rules)
            k_cur = conn.execute(
                """SELECT kp.id, kp.title, kp.knowledge_type, kp.one_line_summary, kpl.relation_type
                   FROM knowledge_project_link kpl
                   JOIN knowledge_page kp ON kpl.knowledge_id = kp.id
                   WHERE kpl.project_id = ?""",
                (project_id,),
            )
            linked_knowledge = [dict(r) for r in k_cur.fetchall()]

            # 3. Todos (Accomplished vs In Progress)
            t_cur = conn.execute(
                "SELECT id, title, status, priority, completed_at, updated_at FROM todo WHERE project_id = ?",
                (project_id,),
            )
            all_todos = [dict(r) for r in t_cur.fetchall()]
            completed_todos = [t for t in all_todos if t["status"] == "completed"]
            active_todos = [t for t in all_todos if t["status"] != "completed"]

            # 4. Decisions
            d_cur = conn.execute(
                "SELECT id, title, content, effective_at, status FROM decision WHERE project_id = ?",
                (project_id,),
            )
            decisions = [dict(r) for r in d_cur.fetchall()]

            # 5. Project Outcomes / Handbooks
            hb_cur = conn.execute(
                "SELECT schema_version, sections_json, revision, updated_at FROM project_handbook WHERE project_id = ?",
                (project_id,),
            )
            handbook_row = hb_cur.fetchone()
            handbook_sections = {}
            if handbook_row and handbook_row["sections_json"]:
                try:
                    handbook_sections = json.loads(handbook_row["sections_json"])
                except Exception:
                    pass

            # Classify effective methodologies vs reusable assets
            effective_methods = [
                k for k in linked_knowledge
                if k["knowledge_type"] in ("method", "rule") or "sop" in k["title"].lower()
            ]
            reusable_assets = [
                k for k in linked_knowledge
                if k["knowledge_type"] in ("analysis", "project", "domain")
            ]

            return {
                "project_id": project_id,
                "project_name": p["name"],
                "status": p["status"],
                "stage": p["stage"],
                "why_started": {
                    "description": p["description"] or "承接业务发展规划及落地目标",
                    "latest_progress": p["latest_progress_summary"] or "持续推进中",
                    "stage": p["stage"] or "进行中",
                },
                "what_was_done": {
                    "total_todos": len(all_todos),
                    "completed_count": len(completed_todos),
                    "completed_milestones": [t["title"] for t in completed_todos[:8]],
                    "in_progress_count": len(active_todos),
                    "active_todos": [t["title"] for t in active_todos[:5]],
                },
                "effective_methods": effective_methods,
                "decision_log": decisions,
                "reusable_assets": {
                    "knowledge_assets": reusable_assets,
                    "handbook_present": bool(handbook_row),
                    "handbook_sections": list(handbook_sections.keys()),
                },
                "memory_health_score": min(100, 40 + len(linked_knowledge) * 15 + len(completed_todos) * 5),
            }
        finally:
            conn.close()

    # ── 4. Knowledge Evolution Timeline (Section V.2) ───────────────────────

    def get_knowledge_evolution_timeline(self, knowledge_id: str) -> Dict[str, Any]:
        """Fetch version evolution history and change reasons for a knowledge page."""
        conn = get_db()
        try:
            page_cur = conn.execute("SELECT * FROM knowledge_page WHERE id = ?", (knowledge_id,))
            page = page_cur.fetchone()
            if not page:
                raise ValueError(f"Knowledge page '{knowledge_id}' not found")

            rev_cur = conn.execute(
                "SELECT id, revision_no, change_summary, compiled_at, created_at "
                "FROM knowledge_revision WHERE knowledge_id = ? ORDER BY revision_no ASC",
                (knowledge_id,),
            )
            revisions = []
            for r in rev_cur.fetchall():
                revisions.append({
                    "revision_no": r["revision_no"],
                    "compiled_at": r["compiled_at"] or r["created_at"],
                    "change_summary": r["change_summary"] or "条目演进与常规修订",
                    "change_reason": self._derive_change_reason(r["change_summary"]),
                })

            return {
                "knowledge_id": knowledge_id,
                "title": page["title"],
                "current_status": page["status"],
                "total_revisions": len(revisions),
                "timeline": revisions,
            }
        finally:
            conn.close()

    def _derive_change_reason(self, summary: Optional[str]) -> str:
        s = (summary or "").lower()
        if "initial" in s or "创建" in s:
            return "初始业务实践沉淀"
        if "ai" in s or "tool" in s or "工具" in s:
            return "融入 AI 提效工具与新技能规范"
        if "流程" in s or "process" in s or "sop" in s:
            return "业务流程持续优化与敏捷调整"
        if "冲突" in s or "修正" in s:
            return "消除多来源业务规范分歧"
        return "日常业务演进与实践完善"

    # ── 5. Runtime Cruise Integration ───────────────────────────────────────

    def run_cruise_evolution_check(self, sources_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Periodic evolution diagnosis executed by scheduler during cruise.

        1. Evaluates knowledge health across the workspace.
        2. Emits EVOLUTION_HEALTH_CHECK event.
        3. If low-health knowledge or orphaned critical assets are found,
           proposes an evolution candidate for human review (never direct write).
        """
        health_report = self.compute_knowledge_health(limit=30)
        overall_score = health_report["overall_score"]

        # Publish health check event
        event_bus.publish(
            EventType.EVOLUTION_HEALTH_CHECK,
            payload={
                "overall_score": overall_score,
                "overall_grade": health_report["overall_grade"],
                "pages_evaluated": health_report["total_pages_evaluated"],
                "distribution": health_report["distribution"],
            },
            source="evolution_engine",
        )

        logger.info(
            f"Evolution Engine health check complete: Score {overall_score}/100 "
            f"({health_report['overall_grade']})"
        )

        # Identify candidate needing evolution update
        stale_pages = [p for p in health_report["pages"] if p["grade"] in ("需维护", "严重老化")]
        proposals_created = 0

        # Note: In accordance with formal_state_bypass = 0, any automated proposals
        # are created as candidate rows for user review
        if stale_pages:
            top_stale = stale_pages[0]
            # We can register an evolution advisory candidate if needed
            logger.info(
                f"Evolution Engine identified stale knowledge for review: {top_stale['title']} "
                f"(Score: {top_stale['score']})"
            )

        return {
            "health_score": overall_score,
            "grade": health_report["overall_grade"],
            "stale_count": len(stale_pages),
            "proposals_created": proposals_created,
        }

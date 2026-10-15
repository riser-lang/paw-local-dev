#!/usr/bin/env python3
"""Business Context Resolver — read-only entity resolution before analysis.

Resolves Existing Projects, Todos, Knowledge from canonical Workspace DB
so the Analysis Engine receives real entity IDs instead of guessing.

Resolution rules:
  - Project: explicit name match → known business term → knowledge link → capture history
  - Todo: existing todo match by title/project
  - Knowledge: existing knowledge match by title/topic/project link

This module is READ-ONLY. It never writes to canonical DB.
"""

from __future__ import annotations

from pathlib import Path
import os


import sqlite3
import json
import re
from dataclasses import dataclass, field
from typing import Optional

DB_PATH = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))


@dataclass
class ResolvedProject:
    id: str
    name: str
    status: str
    stage: str = ""
    next_action_summary: str = ""
    latest_progress_summary: str = ""
    confidence: str = "LOW"  # LOW / MEDIUM / HIGH
    match_reason: str = ""


@dataclass
class ResolvedTodo:
    id: str
    title: str
    status: str
    project_id: str = ""
    updated_at: str = ""


@dataclass
class ResolvedKnowledge:
    id: str
    title: str
    status: str
    one_line_summary: str = ""
    latest_revision: int = 0
    linked_project_ids: list = field(default_factory=list)
    updated_at: str = ""


@dataclass
class BusinessContext:
    """Resolved business context for analysis."""
    resolved_project: Optional[ResolvedProject] = None
    candidate_projects: list[ResolvedProject] = field(default_factory=list)
    relevant_todos: list[ResolvedTodo] = field(default_factory=list)
    relevant_knowledge: list[ResolvedKnowledge] = field(default_factory=list)
    resolution_evidence: str = ""
    resolution_confidence: str = "LOW"

    def to_prompt_context(self) -> str:
        """Build context string for analysis prompt."""
        parts = []
        if self.resolved_project:
            p = self.resolved_project
            parts.append(f"[RESOLVED_PROJECT id={p.id} name={p.name} status={p.status} stage={p.stage}]")
            if p.next_action_summary:
                parts.append(f"  next_action: {p.next_action_summary[:120]}")
            if p.latest_progress_summary:
                parts.append(f"  progress: {p.latest_progress_summary[:120]}")
        if self.candidate_projects and not self.resolved_project:
            names = [f"{p.name}({p.id})" for p in self.candidate_projects[:3]]
            parts.append(f"[CANDIDATE_PROJECTS {names}]")
        if self.relevant_todos:
            for t in self.relevant_todos[:5]:
                pid = f" project={t.project_id}" if t.project_id else ""
                parts.append(f"[EXISTING_TODO id={t.id} status={t.status}{pid}] {t.title[:80]}")
        if self.relevant_knowledge:
            for k in self.relevant_knowledge[:5]:
                rev = f" rev={k.latest_revision}" if k.latest_revision else ""
                links = f" projects={k.linked_project_ids}" if k.linked_project_ids else ""
                parts.append(f"[EXISTING_KNOWLEDGE id={k.id} status={k.status}{rev}{links}] {k.title[:80]}")
                if k.one_line_summary:
                    parts.append(f"  summary: {k.one_line_summary[:100]}")
        if self.resolution_evidence:
            parts.append(f"[RESOLUTION_EVIDENCE] {self.resolution_evidence}")
        return "\n".join(parts) if parts else ""


def _get_conn():
    return sqlite3.connect(DB_PATH)


def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, strip whitespace/punctuation."""
    return re.sub(r'[\s\u3000\uff0c\uff08\uff09\(\)\[\]\{\}.,;:!?\-_/]', '', (text or "").lower())


def _contains_any(haystack: str, needles: list[str]) -> bool:
    """Check if haystack contains any of the needles (normalized)."""
    h = _normalize(haystack)
    for n in needles:
        if _normalize(n) in h:
            return True
    return False


def resolve_business_context(raw_text: str, source_refs: list = None) -> BusinessContext:
    """Resolve business context from raw text and source_refs.

    Reads Existing Projects, Todos, Knowledge from canonical Workspace DB.
    Returns BusinessContext with resolved entities.
    """
    ctx = BusinessContext()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row

    try:
        # ── Project Resolution ──
        projects = conn.execute(
            "SELECT id, name, status, stage, next_action_summary, latest_progress_summary FROM project ORDER BY updated_at DESC"
        ).fetchall()

        matches = []
        for p in projects:
            pname = p["name"] or ""
            # Direct name match in raw text
            if _contains_any(raw_text, [pname]):
                matches.append(ResolvedProject(
                    id=p["id"], name=pname, status=p["status"],
                    stage=p["stage"] or "", next_action_summary=p["next_action_summary"] or "",
                    latest_progress_summary=p["latest_progress_summary"] or "",
                    confidence="HIGH", match_reason="direct_name_match"
                ))
            # Known business term match
            elif _contains_any(raw_text, _get_project_aliases(pname)):
                matches.append(ResolvedProject(
                    id=p["id"], name=pname, status=p["status"],
                    stage=p["stage"] or "", next_action_summary=p["next_action_summary"] or "",
                    latest_progress_summary=p["latest_progress_summary"] or "",
                    confidence="MEDIUM", match_reason="alias_match"
                ))

        if len(matches) == 1:
            ctx.resolved_project = matches[0]
            ctx.resolution_evidence = f"Single project match: {matches[0].name} ({matches[0].match_reason})"
            ctx.resolution_confidence = matches[0].confidence
        elif len(matches) > 1:
            ctx.candidate_projects = matches[:3]
            ctx.resolution_evidence = f"Multiple project matches: {[m.name for m in matches[:3]]}"
            ctx.resolution_confidence = "MEDIUM"

        # ── Todo Resolution (only under resolved project or direct source_ref) ──
        project_id = ctx.resolved_project.id if ctx.resolved_project else None
        if project_id:
            todos = conn.execute(
                "SELECT id, title, status, project_id, updated_at FROM todo WHERE project_id = ? ORDER BY updated_at DESC LIMIT 10",
                (project_id,)
            ).fetchall()
        else:
            # No resolved project — only load todos directly referenced by source_refs
            todo_ids = [r.split(":", 1)[1] for r in (source_refs or []) if r.startswith("todo:") and ":" in r]
            if todo_ids:
                placeholders = ",".join("?" * len(todo_ids))
                todos = conn.execute(
                    f"SELECT id, title, status, project_id, updated_at FROM todo WHERE id IN ({placeholders}) ORDER BY updated_at DESC LIMIT 10",
                    todo_ids
                ).fetchall()
            else:
                todos = []  # NO recent todos — prevent context pollution
        for t in todos:
            ctx.relevant_todos.append(ResolvedTodo(
                id=t["id"], title=t["title"], status=t["status"],
                project_id=t["project_id"] or "", updated_at=t["updated_at"] or ""
            ))

        # ── Knowledge Resolution (only under resolved project or direct source_ref) ──
        if project_id:
            # Knowledge linked to this project
            linked = conn.execute(
                """SELECT kp.id, kp.title, kp.status, kp.one_line_summary, kp.updated_at
                   FROM knowledge_page kp
                   JOIN knowledge_project_link kpl ON kp.id = kpl.knowledge_id
                   WHERE kpl.project_id = ?
                   ORDER BY kp.updated_at DESC LIMIT 10""",
                (project_id,)
            ).fetchall()
        else:
            # No project resolved — only load knowledge directly referenced by source_refs
            knowledge_ids = [r.split(":", 1)[1] for r in (source_refs or []) if r.startswith("knowledge:") and ":" in r]
            if knowledge_ids:
                placeholders = ",".join("?" * len(knowledge_ids))
                linked = conn.execute(
                    f"SELECT id, title, status, one_line_summary, updated_at FROM knowledge_page WHERE id IN ({placeholders}) ORDER BY updated_at DESC LIMIT 10",
                    knowledge_ids
                ).fetchall()
            else:
                linked = []  # NO recent knowledge — prevent context pollution

        for k in linked:
            kid = k["id"]
            # Get latest revision
            try:
                rev = conn.execute(
                    "SELECT MAX(revision_no) as max_rev FROM knowledge_revision WHERE knowledge_id = ?", (kid,)
                ).fetchone()
                max_rev = rev["max_rev"] if rev and rev["max_rev"] else 0
            except:
                max_rev = 0
            # Get linked projects
            try:
                links = conn.execute(
                    "SELECT project_id FROM knowledge_project_link WHERE knowledge_id = ?", (kid,)
                ).fetchall()
                pids = [l["project_id"] for l in links]
            except:
                pids = []
            ctx.relevant_knowledge.append(ResolvedKnowledge(
                id=kid, title=k["title"], status=k["status"],
                one_line_summary=k["one_line_summary"] or "",
                latest_revision=max_rev, linked_project_ids=pids,
                updated_at=k["updated_at"] or ""
            ))

    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return ctx


def _get_project_aliases(project_name: str) -> list[str]:
    """Get known aliases for a project name.

    Only project-specific multi-character aliases are returned.
    Generic single/double-character terms (导师, 实习, 文档) are NOT aliases
    because they can match unrelated content and cause context pollution.
    """
    aliases = [project_name]
    # Only add precise, project-specific aliases (3+ characters)
    if "运营导师" in project_name:
        aliases.extend(["导师体系", "导师项目", "运营导师项目", "导师续聘", "导师述职", "导师续聘述职"])
    if "星火" in project_name:
        aliases.extend(["星火计划", "新兵营"])
    if "运营实习生" in project_name:
        aliases.extend(["运营实习"])
    if "知识库" in project_name:
        aliases.extend(["知识管理"])
    # WPS/文档: only match "WPS" or "wps", not generic "文档"
    if "WPS" in project_name or "wps" in project_name:
        aliases.extend(["WPS", "wps"])
    return aliases
    return aliases


def _recover_business_refs_from_run(analysis_run_id: str) -> list[str]:
    """Attempt to recover real business refs from AnalysisRun.raw_input_refs.

    Reads the analysis_run record from canonical DB and extracts
    business evidence refs (capture:, todo:, project:, knowledge:, source:, daily:).
    Returns empty list if recovery fails or no business refs found.
    """
    if not analysis_run_id:
        return []
    try:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT raw_input_refs FROM analysis_run WHERE id = ?",
            (analysis_run_id,)
        ).fetchone()
        conn.close()
        if not row:
            return []
        raw = row["raw_input_refs"] if row["raw_input_refs"] else ""
        if not raw:
            return []
        # raw_input_refs may be JSON list or comma-separated
        try:
            refs = json.loads(raw)
            if isinstance(refs, str):
                refs = [refs]
        except (json.JSONDecodeError, TypeError):
            refs = [r.strip() for r in raw.split(",") if r.strip()]
        business_prefixes = ("capture:", "daily:", "todo:", "project:", "knowledge:", "source:")
        return [r for r in refs if isinstance(r, str) and r.startswith(business_prefixes)]
    except (sqlite3.OperationalError, Exception):
        return []


# ── BUSINESS_REVIEW_WORTHY GATE ──

SYSTEM_INTERNAL_PATTERNS = [
    "db read error", "partial context", "root cause", "connection", "query",
    "schema", "api error", "json-rpc", "mcp", "timeout", "dedup",
    "analysisrun", "patrolrun", "committer", "approvedchange execution",
    "target_type", "operation", "payload", "create todo 执行",
    "执行 approvedchange", "运行 create 操作", "creator time clearing",
    "互锁检查", "authortime", "采样观测", "七次回收",
    "执行路径", "落地后同步", "计数写入", "参数上报",
]


def is_business_review_worthy(candidate_type: str, statement: str, proposed_change: dict,
                               source_refs: list, analysis_run_id: str = "") -> tuple[bool, str]:
    """Determine if a candidate is worthy of user business review.

    Evidence-Aware Gate:
    - Must have at least one real business evidence ref (capture:, daily:, todo:, project:, knowledge:, source:)
    - If only analysis_run:<id>, attempt to recover real refs from AnalysisRun.raw_input_refs
    - approved_change:<id> and candidate:<id> are NOT business evidence
    - System internal patterns are always filtered regardless of refs

    Returns (is_worthy, reason).
    If not worthy, reason explains why it's system internal.
    """
    stmt_lower = (statement or "").lower().strip()

    # Empty statement → not worthy
    if not stmt_lower:
        return False, "empty_statement"

    # Check system internal patterns — always filtered regardless of refs
    for pattern in SYSTEM_INTERNAL_PATTERNS:
        if pattern in stmt_lower:
            return False, f"system_internal: matches '{pattern}'"

    # Check if statement is just echoing ApprovedChange envelope
    if "approvedchange:" in stmt_lower and ("status:" in stmt_lower or "target_type:" in stmt_lower):
        return False, "post_approval_meta: echoing execution envelope"

    # Check if statement is just echoing operation/type
    if stmt_lower in ("create todo", "create knowledge", "update todo", "update project"):
        return False, f"system_internal: bare operation name '{stmt_lower}'"

    # ── Evidence-Aware Gate ──
    business_ref_prefixes = ("capture:", "daily:", "todo:", "project:", "knowledge:", "source:")
    non_business_prefixes = ("analysis_run:", "approved_change:", "candidate:")

    has_business_ref = any(ref.startswith(business_ref_prefixes) for ref in (source_refs or []))
    has_only_non_business = (source_refs and all(r.startswith(non_business_prefixes) for r in source_refs))

    # If we already have business evidence → worthy
    if has_business_ref:
        if candidate_type in ("conflict", "ambiguity"):
            return True, "business_conflict_or_ambiguity"
        return True, "business_candidate"

    # If only analysis_run/approved_change/candidate refs → try to recover from AnalysisRun
    if has_only_non_business and analysis_run_id:
        recovered_refs = _recover_business_refs_from_run(analysis_run_id)
        if recovered_refs:
            # Recovered real business refs — worthy
            return True, "business_candidate: recovered_refs_from_analysis_run"
        else:
            # Cannot recover business refs — not worthy
            return False, "no_business_evidence: only non-business refs, recovery failed"

    # No source_refs at all and no analysis_run_id → not worthy
    if not source_refs:
        return False, "no_business_evidence: no source_refs"

    # Default: not worthy
    return False, "no_business_evidence: no recognizable business ref"

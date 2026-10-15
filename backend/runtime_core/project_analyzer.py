"""PAW Project Analyzer - AI-Driven Project Explainer & 30-Second Summary Synthesizer.

Enforces:
- Uses PAW's built-in active AI Provider (DeepSeek, Qwen, OpenAI, or Custom) configured with user's API Key.
- Produces grounded 4-field Project Explainer (one_sentence, current_state, key_open_issue, next_step).
- Seamless fallback to heuristic synthesis when offline or API is unavailable.
- Persists into project_explainer SQLite table and updates project progress summaries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.core.config import DB_PATH, settings
from backend.provider.adapter import get_active_provider

logger = logging.getLogger("paw.project_analyzer")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_explainer_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_explainer (
            project_id TEXT PRIMARY KEY,
            explainer_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_explainer_updated 
        ON project_explainer (updated_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_update_record (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            old_value_hash TEXT,
            new_value_hash TEXT,
            source_run_id TEXT,
            model_provider TEXT,
            source_evidence_refs TEXT,
            analysis_task_id TEXT,
            details TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_derived_update_entity 
        ON derived_update_record (entity_type, entity_id)
        """
    )


class ProjectAnalyzer:
    """Built-in AI synthesizer that generates 30s explainers and status summaries for Projects."""

    def __init__(self) -> None:
        pass

    def synthesize_explainer_with_ai(
        self,
        project: Dict[str, Any],
        todos: List[Dict[str, Any]],
        knowledge_items: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """Call PAW's active AI provider to analyze project context and produce 4-field explainer."""
        provider = get_active_provider()
        p_name = project.get("name") or "未命名项目"
        p_desc = project.get("description") or ""
        p_stage = project.get("stage") or ""
        p_status = project.get("status") or "active"

        # Check if provider has chat completion capability and API key
        has_chat = hasattr(provider, "chat_completion") and getattr(provider, "api_key", None)

        if has_chat:
            context_text = f"项目名称：{p_name}\n"
            if p_desc:
                context_text += f"项目已有背景与说明：{p_desc[:1200]}\n"
            if p_stage:
                context_text += f"当前阶段：{p_stage}\n"
            if p_status:
                context_text += f"状态：{p_status}\n"

            if todos:
                todo_titles = [f"- {t.get('title')}" for t in todos[:5]]
                context_text += "关联待办行动：\n" + "\n".join(todo_titles) + "\n"

            if knowledge_items:
                k_titles = [f"- {k.get('title')}" for k in knowledge_items[:5]]
                context_text += "关联知识与制度资产：\n" + "\n".join(k_titles) + "\n"

            system_prompt = (
                "你是 PAW (Personal AI Workspace) 个人工作系统的认知治理引擎。\n"
                "你的任务是为用户的项目生成用于项目卡片展示的『一句话读懂（30秒说明）』与『进展状态摘要』。\n"
                "请严格按照以下 JSON 格式输出，不要输出任何多余的 Markdown 标记、代码块或解释：\n"
                "{\n"
                '  "one_sentence": "30秒读懂：精炼概括该项目是什么、核心目标与工作背景（45字以内）",\n'
                '  "current_state": "目前做到哪里：当前所处阶段或最新进展成果（35字以内）",\n'
                '  "key_open_issue": "当前关键问题：核心难点、卡点或待明确事项（35字以内，若推进顺利请写\'按计划推进中，暂无阻碍项\'）",\n'
                '  "next_step": "下一步：接下来的明确首要行动或关键交付物（35字以内）"\n'
                "}"
            )

            try:
                res = provider.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"请为以下工作项目提炼30秒说明与状态摘要：\n\n{context_text}"},
                    ],
                    temperature=0.2,
                    max_tokens=600,
                )
                ai_text = res[0]
                if ai_text:
                    # Clean markdown codeblocks if model wrapped in ```json
                    cleaned = re.sub(r"^```(?:json)?\s*", "", ai_text.strip(), flags=re.MULTILINE)
                    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, dict) and parsed.get("one_sentence"):
                        return {
                            "one_sentence": str(parsed.get("one_sentence", "")).strip(),
                            "current_state": str(parsed.get("current_state", "")).strip() or "推进中",
                            "key_open_issue": str(parsed.get("key_open_issue", "")).strip() or "按计划推进中",
                            "next_step": str(parsed.get("next_step", "")).strip() or "推进当前阶段核心交付物",
                        }
            except Exception as exc:
                logger.warning(f"AI explainer generation failed for {p_name}, falling back to heuristic: {exc}")

        # Intelligent Heuristic Fallback (Never leaves empty fields)
        first_sentence = ""
        if p_desc:
            parts = re.split(r"[。\n\r;；!！?？]", p_desc.strip())
            if parts and parts[0].strip():
                first_sentence = parts[0].strip()

        one_sentence = first_sentence[:60] if first_sentence else f"{p_name}的核心工作与业务目标推进"
        current_state = project.get("latest_progress_summary") or (f"阶段推进中 · {p_stage}" if p_stage else "按计划正常推进中")
        key_open_issue = "按计划推进中，暂无关键阻塞"
        next_step = project.get("next_action_summary") or (todos[0].get("title") if todos else "推进当前阶段核心交付物")

        return {
            "one_sentence": one_sentence,
            "current_state": current_state,
            "key_open_issue": key_open_issue,
            "next_step": next_step,
        }

    def analyze_projects(
        self,
        max_projects: int = 15,
        force: bool = False,
    ) -> List[Dict[str, Any]]:
        """Scan workspace projects and generate explainers for projects lacking them."""
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        updated_projects = []

        try:
            ensure_explainer_table(conn)
            projects = conn.execute("SELECT * FROM project ORDER BY updated_at DESC").fetchall()
            
            # Find existing explainers
            existing_rows = conn.execute("SELECT project_id, explainer_json FROM project_explainer").fetchall()
            existing_map = {}
            for r in existing_rows:
                try:
                    existing_map[r["project_id"]] = json.loads(r["explainer_json"])
                except Exception:
                    pass

            candidates_to_analyze = []
            for p in projects:
                p_id = p["id"]
                curr_expl = existing_map.get(p_id)
                needs_analysis = False

                if force or curr_expl is None:
                    needs_analysis = True
                elif not curr_expl.get("one_sentence") or "还没有30秒说明" in str(curr_expl.get("one_sentence")):
                    needs_analysis = True

                if needs_analysis:
                    candidates_to_analyze.append(dict(p))
                    if len(candidates_to_analyze) >= max_projects:
                        break

            logger.info(f"ProjectAnalyzer identified {len(candidates_to_analyze)} projects for AI analysis.")

            for proj in candidates_to_analyze:
                p_id = proj["id"]
                p_name = proj["name"]

                # Fetch linked todos
                todos = [
                    dict(t) for t in conn.execute(
                        "SELECT id, title, description, priority FROM todo WHERE project_id = ? ORDER BY created_at DESC LIMIT 5",
                        (p_id,),
                    ).fetchall()
                ]

                # Fetch linked knowledge
                k_items = [
                    dict(k) for k in conn.execute(
                        "SELECT id, title, one_line_summary FROM knowledge_page WHERE applicability LIKE ? OR title LIKE ? LIMIT 5",
                        (f"%{p_name[:6]}%", f"%{p_name[:6]}%"),
                    ).fetchall()
                ]

                # Synthesize with AI
                explainer = self.synthesize_explainer_with_ai(proj, todos, k_items)
                now = _now_iso()

                # Persist to project_explainer
                conn.execute(
                    """
                    INSERT INTO project_explainer (project_id, explainer_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        explainer_json = excluded.explainer_json,
                        updated_at = excluded.updated_at
                    """,
                    (p_id, json.dumps(explainer, ensure_ascii=False), now),
                )

                # Also enrich project table summaries if currently empty
                update_fields = []
                update_vals = []
                derived_audits = []
                analysis_task_id = f"task-analyze-{uuid.uuid4().hex[:12]}"
                evidence_refs = json.dumps({
                    "todos": [t.get("id") for t in todos if t.get("id")],
                    "knowledge": [k.get("id") for k in k_items if k.get("id")],
                }, ensure_ascii=False)
                provider_name = getattr(settings, "provider", "unknown")

                if not proj.get("latest_progress_summary") and explainer.get("current_state"):
                    old_val = proj.get("latest_progress_summary") or ""
                    new_val = explainer["current_state"]
                    update_fields.append("latest_progress_summary = ?")
                    update_vals.append(new_val)
                    derived_audits.append((
                        str(uuid.uuid4()), "project", p_id, "latest_progress_summary",
                        hashlib.sha256(old_val.encode("utf-8")).hexdigest() if old_val else "",
                        hashlib.sha256(new_val.encode("utf-8")).hexdigest() if new_val else "",
                        analysis_task_id, provider_name, evidence_refs, analysis_task_id,
                        json.dumps({"reason": "ai_cruise_latest_progress_summary", "project_name": p_name}, ensure_ascii=False),
                        now
                    ))

                if not proj.get("next_action_summary") and explainer.get("next_step"):
                    old_val = proj.get("next_action_summary") or ""
                    new_val = explainer["next_step"]
                    update_fields.append("next_action_summary = ?")
                    update_vals.append(new_val)
                    derived_audits.append((
                        str(uuid.uuid4()), "project", p_id, "next_action_summary",
                        hashlib.sha256(old_val.encode("utf-8")).hexdigest() if old_val else "",
                        hashlib.sha256(new_val.encode("utf-8")).hexdigest() if new_val else "",
                        analysis_task_id, provider_name, evidence_refs, analysis_task_id,
                        json.dumps({"reason": "ai_cruise_next_action_summary", "project_name": p_name}, ensure_ascii=False),
                        now
                    ))

                if update_fields:
                    update_fields.append("updated_at = ?")
                    update_vals.append(now)
                    update_vals.append(p_id)
                    conn.execute(
                        f"UPDATE project SET {', '.join(update_fields)} WHERE id = ?",
                        update_vals,
                    )
                    for da in derived_audits:
                        conn.execute(
                            """
                            INSERT INTO derived_update_record (
                                id, entity_type, entity_id, field_name,
                                old_value_hash, new_value_hash, source_run_id,
                                model_provider, source_evidence_refs, analysis_task_id,
                                details, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            da,
                        )
                    conn.execute(
                        """
                        INSERT INTO audit_event (
                            id, event_type, actor, target_type, target_id,
                            change_id, commit_id, details, created_at
                        ) VALUES (?, 'derived_field_auto_update', 'ai_cruise', 'project', ?, NULL, NULL, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            p_id,
                            json.dumps({
                                "updated_fields": [da[3] for da in derived_audits],
                                "analysis_task_id": analysis_task_id,
                                "model_provider": provider_name
                            }, ensure_ascii=False),
                            now
                        )
                    )

                conn.commit()
                updated_projects.append({
                    "id": p_id,
                    "name": p_name,
                    "explainer": explainer,
                })

            return updated_projects
        finally:
            conn.close()


project_analyzer = ProjectAnalyzer()

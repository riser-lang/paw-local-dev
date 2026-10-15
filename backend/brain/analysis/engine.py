"""Workspace Analysis Engine V1 — Core Engine.

Reads Raw/Capture/Daily input → builds context → Agnes analysis → review →
structured AnalysisOutput + Candidate Drafts.

Safety:
  - NOT a canonical writer
  - PERSIST_CANDIDATES = False (no candidate table insert)
  - BACKGROUND_AUTO_ANALYSIS = False (no workers/timers)
  - No production DB writes
  - runtime_run_id (correlation) ≠ analysis_run_id (persistence)
  - Two-pass: analysis_pass → review_pass → validated result
"""
from __future__ import annotations
import json
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .models import (
    AnalysisInput, AnalysisOutput, AnalysisItem, CandidateItem,
    TriggerType, Confidence, InferenceLevel, WriteAction, CandidateType,
    AUTO_WRITE_ENABLED,
    validate_ambiguity_preservation,
    validate_plan_vs_done,
    validate_thinking_not_decision,
    validate_completed_action,
    validate_pending_todo,
    validate_tentative_date,
    validate_conflict_not_overwritten,
    validate_knowledge_not_auto_confirmed,
)
from . import repository as repo

# ── Constants ─────────────────────────────────────────────────────────────

class AnalysisRuntimeError(RuntimeError):
    """Raised when the Agnes runtime/model itself fails (not a parse/no-candidate case).

    The orchestration layer (capture_analysis_service) uses this to distinguish
    ANALYSIS_FAILED from VALID_NO_CANDIDATE so failed captures stay retryable.
    """

PERSIST_CANDIDATES = False
BACKGROUND_AUTO_ANALYSIS = False
DEFAULT_MODEL = "mistral/mistral-small-latest"
ANALYSIS_SCHEMA_VERSION = "capture-candidate-v2"

# ── Fingerprint ────────────────────────────────────────────────────────────

def capture_fingerprint(capture: dict) -> str:
    return repo.compute_capture_fingerprint(capture)

def daily_fingerprint(daily: dict, captures: list[dict]) -> str:
    return repo.compute_daily_fingerprint(daily, captures)

# ── Dangerous Inference Checks ────────────────────────────────────────────

def check_dangerous_inferences(output: AnalysisOutput, raw_text: str) -> list[str]:
    """Check for dangerous inference patterns.
    Returns list of warnings.
    """
    warnings = []

    # plan_as_done: plans should not be in completed_actions
    for plan in output.plans:
        for comp in output.completed_actions:
            if plan.statement.lower() in comp.statement.lower() or comp.statement.lower() in plan.statement.lower():
                warnings.append(f"plan_as_done: '{plan.statement}' appears in both plans and completed_actions")

    # idea_as_decision: ideas should not be in decisions
    for idea in output.ideas:
        for dec in output.decisions:
            if idea.statement.lower() in dec.statement.lower():
                warnings.append(f"idea_as_decision: '{idea.statement}' appears in both ideas and decisions")

    # date_loss: dates mentioned in raw but not captured
    import re
    date_patterns = [r'\d+月\d+日', r'\d{4}-\d{2}-\d{2}', r'明天', r'后天', r'下周']
    raw_dates = []
    for pat in date_patterns:
        raw_dates.extend(re.findall(pat, raw_text or ""))
    if raw_dates and not output.dates:
        warnings.append(f"date_loss: dates {raw_dates} found in raw but not in output")

    # unsupported_inference: EXPLICIT items with WEAK_INFERENCE
    for item in output.facts + output.events + output.decisions:
        if item.inference_level == InferenceLevel.EXPLICIT and item.confidence == Confidence.LOW:
            warnings.append(f"unsupported_inference: '{item.statement}' is EXPLICIT but LOW confidence")

    # confirmed_knowledge_conflict: conflicts should use ASK_USER
    for conflict in output.conflicts:
        if conflict.recommended_action not in (WriteAction.ASK_USER, WriteAction.DO_NOT_WRITE):
            warnings.append(f"confirmed_knowledge_conflict: conflict '{conflict.statement}' should use ASK_USER")

    # evidence_mismatch: items with no evidence
    for item in output.facts + output.decisions:
        if not item.evidence_refs and item.confidence != Confidence.LOW:
            warnings.append(f"evidence_mismatch: '{item.statement}' has no evidence but non-LOW confidence")

    return warnings

# ── Fact Precedence ───────────────────────────────────────────────────────

def apply_fact_precedence(output: AnalysisOutput, user_corrections: list[AnalysisItem] = None) -> AnalysisOutput:
    """Apply domain-aware fact precedence.
    User corrections override older Capture interpretations.
    """
    if not user_corrections:
        return output
    for correction in user_corrections:
        stmt = correction.statement
        if "不是" in stmt and "是" in stmt:
            parts = stmt.split("不是", 1)
            if len(parts) == 2:
                old_part = parts[1].split("是")[0].strip()
                old_part_clean = old_part.rstrip("，。,.;；！!？?")
                new_part = parts[1].split("是", 1)[1].strip() if "是" in parts[1] else ""
                output.facts = [f for f in output.facts if old_part_clean not in f.statement and old_part not in f.statement]
                if new_part:
                    output.facts.append(AnalysisItem(
                        statement=new_part,
                        evidence_refs=correction.evidence_refs,
                        confidence=Confidence.HIGH,
                        inference_level=InferenceLevel.EXPLICIT,
                    ))
    return output

# ── Two-Pass Analysis ─────────────────────────────────────────────────────

def _build_analysis_prompt(input_data: AnalysisInput, business_context_str: str = "") -> str:
    """Build structured analysis prompt for Agnes.

    Args:
        business_context_str: resolved business context from BusinessContextResolver
    """
    parts = ["请分析以下内容，提取结构化信息。"]
    if input_data.raw_text:
        parts.append(f"内容: {input_data.raw_text}")
    if business_context_str:
        parts.append("")
        parts.append("[BUSINESS_CONTEXT]")
        parts.append(business_context_str)
        parts.append("[/BUSINESS_CONTEXT]")
    if input_data.relevant_project_context:
        ctx = input_data.relevant_project_context
        parts.append(f"项目: {ctx.name} (status={ctx.status})")
    if input_data.relevant_todos:
        parts.append("相关待办: " + "; ".join(t.title for t in input_data.relevant_todos))
    if input_data.confirmed_knowledge:
        parts.append("已确认知识: " + "; ".join(k.title for k in input_data.confirmed_knowledge))
    parts.append("""请只返回一个 JSON 对象，必须包含以下字段：
{"facts":[],"events":[],"decisions":[],"plans":[],"ideas":[],"questions":[],"completed_actions":[],"pending_actions":[],"status_changes":[],"people":[],"dates":[],"projects":[],"todo_candidates":[],"project_update_candidates":[],"knowledge_candidates":[],"conflicts":[],"ambiguities":[]}

普通分析项格式：
{"statement":"","confidence":"HIGH/MEDIUM/LOW","inference_level":"EXPLICIT/STRONG_INFERENCE/WEAK_INFERENCE"}

Candidate 项格式：
{"statement":"","confidence":"HIGH/MEDIUM/LOW","inference_level":"EXPLICIT/STRONG_INFERENCE/WEAK_INFERENCE","recommended_action":"DRAFT_ONLY/ASK_USER","proposed_change":{}}

Candidate 生成规则：

1. 原文出现明确尚未完成的行动，例如“需要、待、还没、尚未、记得、务必、必须”，并且包含可执行动作时，必须生成 todo_candidates，不能只放入 pending_actions。

2. 明确时间要求与执行动作，例如“明天确认”“下周整理”“月底前提交”，也应生成 todo_candidates。

3. todo_candidates.proposed_change 至少包含 title。
   可包含 description、priority、due_at、project_id，
   但不得猜测原文没有明确给出的字段。

4. “考虑、可能、也许、想法”不能直接生成 Todo，
   除非原文同时存在明确执行承诺。
   不确定时生成 ambiguity。

5. 暂定日期不得升级为最终日期。
   项目归属不明确时不得随机绑定。

6. project_update_candidates 和 knowledge_candidates
   只能生成审核草案，不直接写正式 Workspace。

7. 禁止 AUTO_WRITE。
   todo / project_update / knowledge 一律 DRAFT_ONLY；
   conflict / ambiguity 一律 ASK_USER。

8. 如果一个明确行动已经出现在 pending_actions，
   仍然必须同时生成对应 todo_candidates。

9. [BUSINESS_CONTEXT] 中已提供 Existing Project/Todo/Knowledge 的真实 ID。
   todo_candidates.proposed_change 必须使用 resolved_project_id 作为 project_id。
   不得猜测或编造 entity ID。

10. Existing Knowledge 优先 update/revision，不要重复 create。
    只有现有 Knowledge 都无法承载该主题时才 CREATE_NEW。
    knowledge_candidates.proposed_change 必须包含真实 knowledge_id（update 时）。

11. 评价、观察、想法（无明确执行动作）不得生成 Todo Candidate。
    只有明确行动承诺才生成 Todo。

12. 不得把系统技术状态（DB error、API error、payload 格式、
    ApprovedChange execution envelope、Committer 实现等）
    作为业务事实生成 Candidate。

13. 如果 [BUSINESS_CONTEXT] 中有 RESOLVED_PROJECT，
    所有 Candidate 的 project_id 必须使用该 resolved ID。
    不要让用户审核'这是不是某个项目'。
""")
    return "\n".join(parts)


_ACTION_MARKERS = (
    "需要", "待", "还没", "尚未", "记得", "务必", "必须",
    "明天", "后天", "下周", "本周", "月底", "月末",
)

_ACTION_VERBS = (
    "确认", "整理", "跟进", "提交", "完成", "准备", "安排",
    "联系", "发送", "检查", "复核", "召开", "举办", "输出",
    "更新", "制作", "打印", "统计", "汇总", "回访", "推进",
    "沟通", "核对", "预约", "提醒", "补齐", "梳理",
)


def _todo_candidate_from_pending(item: AnalysisItem) -> CandidateItem:
    """Convert a pending action to a review-only Todo Candidate."""

    statement = " ".join((item.statement or "").strip().split())

    if not statement:
        raise ValueError("pending action statement is empty")

    title = statement[:180]

    return CandidateItem(
        statement=statement,
        candidate_type=CandidateType.todo,
        recommended_action=WriteAction.DRAFT_ONLY,
        proposed_change={
            "title": title,
            "description": statement,
        },
        confidence=(
            item.confidence
            if item.confidence != Confidence.LOW
            else Confidence.MEDIUM
        ),
        inference_level=item.inference_level,
        evidence_refs=list(item.evidence_refs or []),
    )


def _ensure_todo_candidates(
    output: AnalysisOutput,
    raw_text: str,
) -> AnalysisOutput:
    """Ensure explicit pending actions reach the Candidate review plane.

    Safety:
    - creates CandidateItem drafts only
    - never creates canonical Todo
    - never approves a Candidate
    - never creates ApprovedChange
    - never calls Committer
    """

    # Model already produced Todo candidates.
    if output.todo_candidates:
        return output

    seen = set()

    # Primary deterministic bridge:
    # model classified an item as pending -> it must reach review.
    for item in output.pending_actions:
        statement = (item.statement or "").strip()

        if not statement:
            continue

        normalized = " ".join(statement.split())

        if normalized in seen:
            continue

        seen.add(normalized)

        output.todo_candidates.append(
            _todo_candidate_from_pending(item)
        )

    if output.todo_candidates:
        return output

    # Secondary conservative fail-safe:
    # model omitted both pending_actions and todo_candidates,
    # but raw text contains explicit action language.
    import re

    sentences = re.split(
        r"[\n。；;！？!?]+",
        raw_text or "",
    )

    for sentence in sentences:
        statement = sentence.strip()

        if len(statement) < 4:
            continue

        if not any(marker in statement for marker in _ACTION_MARKERS):
            continue

        if not any(verb in statement for verb in _ACTION_VERBS):
            continue

        normalized = " ".join(statement.split())

        if normalized in seen:
            continue

        seen.add(normalized)

        item = AnalysisItem(
            statement=normalized,
            confidence=Confidence.MEDIUM,
            inference_level=InferenceLevel.EXPLICIT,
        )

        output.todo_candidates.append(
            _todo_candidate_from_pending(item)
        )

        # Bound candidate explosion from a single capture.
        if len(output.todo_candidates) >= 5:
            break

    return output


def _build_review_prompt(analysis_output: dict) -> str:
    """Build review prompt for second pass."""
    return f"""请审查以下分析结果，检查是否有危险推断:
{json.dumps(analysis_output, ensure_ascii=False, indent=2)}

检查项:
1. 计划不应被标记为已完成
2. 想法不应被标记为决定
3. 暂定日期不应被标记为最终日期
4. 不确定的项目绑定应标记为模糊
5. 与已确认知识冲突应标记为冲突
返回审查后的JSON，可添加 conflicts 和 ambiguities 字段。"""

def _parse_agnes_response(response: str, analysis_id: str) -> AnalysisOutput:
    """Parse Agnes JSON response into AnalysisOutput."""
    try:
        data = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        # Try to extract JSON from text
        import re
        match = re.search(r'\{.*\}', response or "", re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except (json.JSONDecodeError, TypeError):
                data = {}
        else:
            data = {}

    def parse_items(key: str) -> list[AnalysisItem]:
        items = []
        for item in (data.get(key) or []):
            if isinstance(item, str):
                stmt = item.strip()
                if not stmt:
                    continue
                items.append(AnalysisItem(statement=stmt))
            elif isinstance(item, dict):
                stmt = (item.get("statement") or item.get("text") or "").strip()
                if not stmt:
                    continue
                items.append(AnalysisItem(
                    statement=stmt,
                    confidence=Confidence(item.get("confidence", "LOW")),
                    inference_level=InferenceLevel(item.get("inference_level", "WEAK_INFERENCE")),
                    evidence_refs=item.get("evidence_refs", []),
                    raw_excerpt=item.get("raw_excerpt"),
                ))
        return items

    def parse_candidates(key: str, ctype: CandidateType) -> list[CandidateItem]:
        items = []
        for item in (data.get(key) or []):
            if isinstance(item, dict):
                stmt = (item.get("statement") or "").strip()
                if not stmt:
                    continue
                action_str = item.get("recommended_action", "DRAFT_ONLY")
                try:
                    action = WriteAction(action_str)
                except ValueError:
                    action = WriteAction.DRAFT_ONLY
                items.append(CandidateItem(
                    statement=stmt,
                    candidate_type=ctype,
                    recommended_action=action,
                    proposed_change=item.get("proposed_change"),
                    confidence=Confidence(item.get("confidence", "LOW")),
                    inference_level=InferenceLevel(item.get("inference_level", "WEAK_INFERENCE")),
                    evidence_refs=item.get("evidence_refs", []),
                ))
        return items

    return AnalysisOutput(
        analysis_id=analysis_id,
        facts=parse_items("facts"),
        events=parse_items("events"),
        decisions=parse_items("decisions"),
        plans=parse_items("plans"),
        ideas=parse_items("ideas"),
        questions=parse_items("questions"),
        completed_actions=parse_items("completed_actions"),
        pending_actions=parse_items("pending_actions"),
        status_changes=parse_items("status_changes"),
        people=parse_items("people"),
        dates=parse_items("dates"),
        projects=parse_items("projects"),
        todo_candidates=parse_candidates("todo_candidates", CandidateType.todo),
        project_update_candidates=parse_candidates("project_update_candidates", CandidateType.project_update),
        knowledge_candidates=parse_candidates("knowledge_candidates", CandidateType.knowledge),
        conflicts=parse_candidates("conflicts", CandidateType.conflict),
        ambiguities=parse_candidates("ambiguities", CandidateType.ambiguity),
    )

def two_pass_analysis(raw_text: str, context: dict = None, analysis_id: str = None, raise_on_model_error: bool = False) -> tuple[AnalysisOutput, list[str]]:
    """Execute two-pass analysis: analysis → review → validated output.
    Returns (AnalysisOutput, warnings).
    """
    analysis_id = analysis_id or str(uuid.uuid4())
    context = context or {}

    # Build input
    input_data = AnalysisInput(
        analysis_id=analysis_id,
        trigger_type=TriggerType(context.get("trigger_type", "capture")),
        raw_text=raw_text,
        raw_input_refs=context.get("source_refs", []),
    )

    # Resolve business context (Existing Project/Todo/Knowledge)
    business_context_str = context.get("business_context", "")
    if not business_context_str:
        try:
            from brain.analysis.business_context_resolver import resolve_business_context
            biz_ctx = resolve_business_context(raw_text, context.get("source_refs", []))
            business_context_str = biz_ctx.to_prompt_context()
        except Exception:
            pass

    # Pass 1: Analysis
    try:
        from brain.runtime.agy_runtime import get_adapter as _agy_get_adapter, DEFAULT_MODEL as _agy_default_model
        from brain.context.assistant_workspace_context import build_assistant_message_legacy as _build_ctx
        adapter = _agy_get_adapter()
        prompt = _build_analysis_prompt(input_data, business_context_str=business_context_str)
        session_key = f"paw-brain:analysis-{analysis_id}"
        _agy_sid = f"brain:analysis-{analysis_id}"
        _existing = adapter.get_session(_agy_sid)
        if not _existing:
            adapter.create_session(_agy_sid, model=_agy_default_model, mode="extract")
        _full_msg = _build_ctx(prompt, "extract")
        result = adapter.send_message(
            session_id=_agy_sid,
            message=_full_msg,
            mode="extract",
        )
        response_text = result.response
        runtime_run_id = result.conversation_id
        if raise_on_model_error and response_text.startswith("[ERROR]"):
            raise AnalysisRuntimeError(response_text[:300])
    except AnalysisRuntimeError:
        raise
    except Exception as e:
        if raise_on_model_error:
            # Adapter/infra failure must fail the run (retryable), never
            # masquerade as a valid empty analysis.
            raise AnalysisRuntimeError(f"adapter_error: {str(e)[:200]}") from e
        # Fallback: empty analysis (for tests without Agnes)
        response_text = "{}"
        runtime_run_id = ""

    output = _parse_agnes_response(response_text, analysis_id)

    # Candidate completeness gate:
    # explicit pending actions must not disappear before persistence.
    output = _ensure_todo_candidates(output, raw_text)

    # Pass 2: Review (only if candidates or complex items exist)
    has_candidates = bool(output.todo_candidates or output.project_update_candidates or
                         output.knowledge_candidates or output.conflicts or output.ambiguities)
    has_complex = bool(output.decisions or output.completed_actions or output.status_changes)

    if has_candidates or has_complex:
        try:
            review_prompt = _build_review_prompt(response_text)
            _review_full = _build_ctx(review_prompt, "extract")
            review_result = adapter.send_message(
                session_id=_agy_sid,
                message=_review_full,
                mode="extract",
            )
            reviewed = _parse_agnes_response(review_result.response, analysis_id)
            # Merge reviewed conflicts/ambiguities
            if reviewed.conflicts:
                output.conflicts = reviewed.conflicts
            if reviewed.ambiguities:
                output.ambiguities = reviewed.ambiguities
        except Exception:
            pass  # Keep original output if review fails

    # Dangerous inference checks
    warnings = check_dangerous_inferences(output, raw_text)

    # Apply fact precedence
    output = apply_fact_precedence(output)

    # Enforce write actions
    for c in output.todo_candidates:
        c.recommended_action = WriteAction.DRAFT_ONLY
    for c in output.project_update_candidates:
        c.recommended_action = WriteAction.DRAFT_ONLY
    for c in output.knowledge_candidates:
        c.recommended_action = WriteAction.DRAFT_ONLY
    for c in output.conflicts:
        c.recommended_action = WriteAction.ASK_USER
    for c in output.ambiguities:
        c.recommended_action = WriteAction.ASK_USER

    return output, warnings

# ── Public API ────────────────────────────────────────────────────────────

def analyze_capture(capture_id: str, force: bool = False, db_path: str = None) -> dict:
    """Analyze a single Capture record.
    Returns dict with analysis output, candidates (draft only), and metadata.
    Does NOT persist candidates. Does NOT write to production DB.
    """
    db = db_path or repo.PRODUCTION_DB
    capture = repo.get_capture(capture_id, db)
    if not capture:
        return {"error": "capture_not_found", "capture_id": capture_id}

    # Fingerprint + idempotency
    fp = capture_fingerprint(capture)
    if not force:
        is_dup, existing_id = repo.check_idempotency(fp, db)
        if is_dup:
            return {"skipped": True, "reason": "duplicate_fingerprint", "existing_analysis_id": existing_id}

    # Two-pass analysis
    raw_text = capture.get("raw_text", "")
    output, warnings = two_pass_analysis(raw_text, {
        "trigger_type": "capture",
        "source_refs": [capture_id],
    })

    return {
        "analysis_id": output.analysis_id,
        "capture_id": capture_id,
        "fingerprint": fp,
        "output": output.model_dump(),
        "warnings": warnings,
        "candidates_draft": [c.model_dump() for c in output.todo_candidates + output.knowledge_candidates + output.project_update_candidates],
        "persisted": False,
        "runtime_correlation_id": output.analysis_id,
    }

def analyze_daily(daily_id: str, force: bool = False, db_path: str = None) -> dict:
    """Analyze a Daily aggregate (daily metadata + captures).
    Daily table has no body column — this is aggregate analysis.
    """
    db = db_path or repo.PRODUCTION_DB
    daily = repo.get_daily(daily_id, db)
    if not daily:
        return {"error": "daily_not_found", "daily_id": daily_id}

    captures = repo.get_captures_by_daily(daily_id, db)

    # Fingerprint
    fp = daily_fingerprint(daily, captures)
    if not force:
        is_dup, existing_id = repo.check_idempotency(fp, db)
        if is_dup:
            return {"skipped": True, "reason": "duplicate_fingerprint", "existing_analysis_id": existing_id}

    # Build aggregate raw text from captures
    capture_texts = [c.get("raw_text", "") for c in captures if c.get("raw_text")]
    aggregate_text = "\n".join(capture_texts) if capture_texts else ""

    # Include daily metadata
    daily_meta = f"[Daily: {daily.get('date', '')} | {daily.get('title', '')}]"
    full_text = f"{daily_meta}\n{aggregate_text}" if aggregate_text else daily_meta

    output, warnings = two_pass_analysis(full_text, {
        "trigger_type": "daily",
        "source_refs": [daily_id] + [c["id"] for c in captures],
    })

    return {
        "analysis_id": output.analysis_id,
        "daily_id": daily_id,
        "capture_count": len(captures),
        "fingerprint": fp,
        "output": output.model_dump(),
        "warnings": warnings,
        "candidates_draft": [c.model_dump() for c in output.todo_candidates + output.knowledge_candidates + output.project_update_candidates],
        "persisted": False,
        "runtime_correlation_id": output.analysis_id,
    }

def analyze_entity(entity_type: str, entity_id: str, force: bool = False, db_path: str = None) -> dict:
    """Generic analysis entry point."""
    if entity_type == "capture":
        return analyze_capture(entity_id, force, db_path)
    elif entity_type == "daily":
        return analyze_daily(entity_id, force, db_path)
    else:
        return {"error": "unsupported_entity_type", "entity_type": entity_type}

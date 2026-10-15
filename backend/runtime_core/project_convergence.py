# -*- coding: utf-8 -*-
"""PAW Project Convergence Engine.

Detects existing projects that are the same project under slightly different
spellings and generates merge proposals that flow through the standard
human-governed review pipeline. Built into the product so every fresh install
gets convergence behaviour from day one.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger("paw.convergence")

INACTIVE_STATUSES = {"merged", "archived", "deleted"}
_PUNCT = re.compile(r"[\s\-—–_·・.,，。、:：;；/\\|()（）\[\]【】{}<>《》\"'“”‘’!！?？]+")


def normalize_project_name(name: str) -> str:
    """Canonical form used to decide whether two project names are the same project.

    Drops whitespace and punctuation, lowercases Latin letters and strips a
    trailing “项目”, so “FY26武汉学校二三管人才盘点项目” and
    “FY26武汉学校二三管人才盘点” compare equal while different departments do not.
    """
    key = _PUNCT.sub("", (name or "").strip()).lower()
    while key.endswith("项目") and len(key) > 2:
        key = key[:-2]
    return key


def detect_project_merge_proposals(projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group active projects by normalized name; propose one merge per group.

    The earliest-created project of each group is kept; the others are folded
    into it once the user approves the proposal.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in projects or []:
        if (p.get("status") or "active") in INACTIVE_STATUSES:
            continue
        key = normalize_project_name(p.get("name", ""))
        if key:
            groups.setdefault(key, []).append(p)

    proposals = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda p: (p.get("created_at") or "9999", p.get("id", "")))
        keeper, dups = members[0], members[1:]
        dup_names = "、".join(d.get("name", "") for d in dups)
        proposals.append({
            "target_type": "project_merge",
            "operation": "merge",
            "payload": {
                "keeper_project_id": keeper["id"],
                "keeper_project_name": keeper.get("name", ""),
                "duplicate_project_ids": [d["id"] for d in dups],
                "duplicate_project_names": [d.get("name", "") for d in dups],
            },
            "raw_excerpt": f"检测到 {len(dups)} 个与「{keeper.get('name', '')}」重复的项目：{dup_names}",
            "source_refs": [],
            "confidence": "HIGH",
            "evidence_locators": [],
            "source_generation": "rule",
            "analysis_model": "rule:convergence_engine",
            "review_brief": {
                "title": f"合并重复项目：{keeper.get('name', '')}（{len(dups) + 1} 个同名项目）",
                "summary": f"将 {len(dups)} 个重复项目合并至「{keeper.get('name', '')}」，相关知识、待办和项目说明一并迁移，重复项目标记为已合并",
                "risk_level": "LOW",
                "recommended_action": "批准合并以收敛项目结构",
                "generation_mode": "RULE_FALLBACK",
                "rule_fallback_notice": "项目收敛引擎检测",
            },
        })
    return proposals


# ── AI semantic convergence ───────────────────────────────────────────────────

AUTO_REVIEWER = "auto:project_convergence"
#: a project with at least this many knowledge links counts as "established"
ESTABLISHED_KNOWLEDGE = 5
MAX_AUTO_GROUP_SIZE = 25

_AI_SYSTEM = (
    "你是 PAW 个人工作台的项目整理助手。下面是用户工作台里所有活跃项目的列表。很多项目其实是在分析单个文件时被单独建出来的，"
    "它们属于同一个长期业务。请找出应该合并到同一个长期项目里的项目。\n"
    "规则：\n"
    "1. 同一个长期业务下的不同部门、不同阶段、子报告、单次会议或单次活动，应合并到代表这个长期业务的项目里。"
    "例如多个部门的“人才盘点预评估”和“盘点反馈报告”，都属于同一个人才盘点项目。\n"
    "2. 同一个项目的不同写法（如“志高班（专项班）”和“志高班（专项）项目”）应合并。\n"
    "3. 不同的业务即使名字里有相同的词也不要合并；同一计划下彼此独立的不同班级、不同期次，如果不确定，用 MEDIUM 或 LOW。\n"
    "4. keeper 选该组里最能代表长期业务的已有项目，优先选建立更早、知识更多的项目。\n"
    "5. confidence：只有非常确定是同一个长期业务时才用 HIGH，否则用 MEDIUM 或 LOW。\n"
    "6. 只输出 JSON，不要任何解释：{\"groups\":[{\"keeper\":编号,\"members\":[编号,...],\"confidence\":\"HIGH\",\"reason\":\"一句话理由\"}]}。"
    "members 不包含 keeper；每个项目最多出现在一组里；没有需要合并的就输出 {\"groups\":[]}。"
)


def _state_path():
    from backend.core.config import DATA_DIR
    from pathlib import Path
    return Path(DATA_DIR) / "convergence_state.json"


def _load_state() -> Dict[str, Any]:
    import json
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    import json
    try:
        _state_path().write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Could not save convergence state: {exc}")


def _project_signature(projects: List[Dict[str, Any]]) -> str:
    import hashlib
    keys = sorted(f"{p.get('id')}|{p.get('name')}" for p in projects)
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def _project_stats() -> Dict[str, Dict[str, int]]:
    from backend.governance.service import get_db
    conn = get_db()
    try:
        k = dict(conn.execute("SELECT project_id, COUNT(*) FROM knowledge_project_link GROUP BY project_id").fetchall())
        t = dict(conn.execute("SELECT project_id, COUNT(*) FROM todo WHERE project_id IS NOT NULL GROUP BY project_id").fetchall())
    finally:
        conn.close()
    return {"knowledge": k, "todos": t}


def _parse_groups(text: str) -> List[Dict[str, Any]]:
    import json
    raw = re.sub(r"^```(json)?\s*|\s*```$", "", (text or "").strip())
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    groups = data.get("groups") if isinstance(data, dict) else None
    return groups if isinstance(groups, list) else []


def detect_ai_merge_proposals(projects: List[Dict[str, Any]], provider: Any,
                              exclude_ids: set) -> Dict[str, Any]:
    """Ask the model which active projects belong to the same long-term initiative.

    Returns {"proposals": [...], "model_called": bool, "error": str|None}. Groups are
    validated: indexes in range, keeper not among members, no project in two
    groups, and none of ``exclude_ids`` (projects already in a pending merge).
    """
    active = [p for p in projects if (p.get("status") or "active") not in INACTIVE_STATUSES]
    if len(active) < 2:
        return {"proposals": [], "model_called": False, "error": None}
    stats = _project_stats()
    lines = []
    for i, p in enumerate(active, 1):
        lines.append(f"{i}. {p.get('name')}｜状态：{p.get('status') or 'active'}｜建立：{str(p.get('created_at') or '')[:10]}"
                     f"｜知识 {stats['knowledge'].get(p['id'], 0)} 条｜待办 {stats['todos'].get(p['id'], 0)} 条"
                     f"｜进展：{str(p.get('latest_progress_summary') or '')[:50]}")
    text, _usage, err = provider.chat_completion(
        messages=[{"role": "system", "content": _AI_SYSTEM},
                  {"role": "user", "content": "活跃项目列表：\n" + "\n".join(lines)}],
        temperature=0.1, max_tokens=1500)
    if err or not text:
        return {"proposals": [], "model_called": True, "error": err or "empty_response"}

    used: set = set()
    proposals = []
    for g in _parse_groups(text):
        try:
            keeper_i = int(g.get("keeper"))
            member_is = [int(x) for x in (g.get("members") or [])]
        except Exception:
            continue
        idxs = [keeper_i] + member_is
        if not member_is or any(i < 1 or i > len(active) for i in idxs) or len(set(idxs)) != len(idxs):
            continue
        group = [active[i - 1] for i in idxs]
        if any(p["id"] in used or p["id"] in exclude_ids for p in group) or len(group) > MAX_AUTO_GROUP_SIZE:
            continue
        used.update(p["id"] for p in group)
        keeper, dups = group[0], group[1:]
        confidence = str(g.get("confidence") or "MEDIUM").upper()
        confidence = confidence if confidence in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
        reason = str(g.get("reason") or "").strip()[:200]
        dup_names = "、".join(d.get("name", "") for d in dups)
        proposals.append({
            "target_type": "project_merge",
            "operation": "merge",
            "payload": {
                "keeper_project_id": keeper["id"],
                "keeper_project_name": keeper.get("name", ""),
                "duplicate_project_ids": [d["id"] for d in dups],
                "duplicate_project_names": [d.get("name", "") for d in dups],
            },
            "raw_excerpt": f"AI 判断 {dup_names} 属于「{keeper.get('name', '')}」这一长期业务：{reason}",
            "confidence": confidence,
            "analysis_model": f"ai:project_convergence:{getattr(provider, 'model', 'model')}",
            "review_brief": {
                "title": f"项目收敛：将 {len(dups)} 个项目并入「{keeper.get('name', '')}」",
                "summary": f"将 {dup_names} 合并至「{keeper.get('name', '')}」。理由：{reason}",
                "risk_level": "LOW" if confidence == "HIGH" else "MEDIUM",
                "recommended_action": "核准合并以收敛项目结构",
                "generation_mode": "AI_MODEL",
            },
        })
    return {"proposals": proposals, "model_called": True, "error": None}


def _auto_eligible(prop: Dict[str, Any], stats: Dict[str, Dict[str, int]]) -> bool:
    """High confidence, and never folds two established projects into one."""
    if prop.get("confidence") != "HIGH":
        return False
    ids = [prop["payload"]["keeper_project_id"]] + list(prop["payload"]["duplicate_project_ids"])
    established = sum(1 for pid in ids if stats["knowledge"].get(pid, 0) >= ESTABLISHED_KNOWLEDGE)
    return established <= 1 and len(ids) <= MAX_AUTO_GROUP_SIZE


def _pending_merge_project_ids() -> set:
    import json
    from backend.governance.service import get_db
    conn = get_db()
    try:
        rows = conn.execute("SELECT proposed_change FROM candidate WHERE candidate_type = 'project_merge' AND status = 'pending'").fetchall()
    finally:
        conn.close()
    ids: set = set()
    for (pc,) in rows:
        try:
            p = json.loads(pc)["payload"]
            ids.add(p["keeper_project_id"]); ids.update(p["duplicate_project_ids"])
        except Exception:
            continue
    return ids


def run_project_convergence(use_ai: bool = False, force_ai: bool = False, auto_apply: bool = True) -> Dict[str, Any]:
    """Converge the project list.

    1. Rule pass: projects with the same normalized name (always, no model call).
    2. AI pass (``use_ai``): the model groups projects belonging to the same long-term
       initiative. Called only when the active project list changed since the last AI
       pass, or when ``force_ai`` (deep cruise / the control-center button).
    High-confidence groups are merged automatically through the governance pipeline
    (reviewer ``auto:project_convergence``, database backed up first); groups that
    fold two established projects together, or are not high confidence, are left in
    “待我审核”. Proposals already decided are never filed again.
    """
    from backend.governance.service import get_db, governance
    from backend.workspace_api.repository import list_projects

    result: Dict[str, Any] = {"groups": 0, "candidates": [], "auto_merged": [], "pending": [],
                              "ai": {"ran": False, "reason": "disabled"}}
    try:
        conn = get_db()
        try:
            known = {r[0] for r in conn.execute("SELECT id FROM candidate WHERE candidate_type = 'project_merge'")}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(f"Project convergence detection failed: {exc}")
        return {**result, "error": str(exc)}

    backed_up = {"done": False}

    def _file_and_apply(props: List[Dict[str, Any]]) -> None:
        stats = _project_stats()
        for prop in props:
            try:
                cid = governance.create_candidate(
                    target_type=prop["target_type"], operation=prop["operation"], payload=prop["payload"],
                    raw_excerpt=prop["raw_excerpt"], confidence=prop["confidence"],
                    analysis_model=prop["analysis_model"], review_brief=prop["review_brief"], evidence_locators=[])
            except Exception as exc:
                logger.error(f"Failed to file project merge proposal: {exc}")
                continue
            if not cid or cid in known:      # an existing proposal is returned for duplicates
                continue
            known.add(cid)
            result["candidates"].append(cid)
            if auto_apply and _auto_eligible(prop, stats):
                try:
                    if not backed_up["done"]:
                        from backend.core.backup import create_db_backup
                        create_db_backup(tag="pre_auto_convergence")
                        backed_up["done"] = True
                    governance.accept_and_commit_candidate(
                        cid, reviewer=AUTO_REVIEWER,
                        review_notes="高置信度项目收敛，系统自动核准（数据库已先备份，可在快照中回滚）")
                    result["auto_merged"].append({"candidate_id": cid, "keeper": prop["payload"]["keeper_project_name"],
                                                  "merged": prop["payload"]["duplicate_project_names"]})
                    continue
                except Exception as exc:
                    logger.error(f"Auto-merge failed, leaving proposal for review: {exc}")
            result["pending"].append(cid)

    # 1. Rule pass: same normalized name
    try:
        rule_props = detect_project_merge_proposals(list_projects())
        result["groups"] += len(rule_props)
        _file_and_apply(rule_props)
    except Exception as exc:
        logger.warning(f"Rule convergence failed: {exc}")

    # 2. AI pass: same long-term initiative
    if use_ai:
        result["ai"] = _run_ai_pass(force_ai, _file_and_apply, result)

    logger.info(f"Project convergence: groups={result['groups']} filed={len(result['candidates'])} "
                f"auto_merged={len(result['auto_merged'])} pending={len(result['pending'])} ai={result['ai']}")
    return result


def _run_ai_pass(force: bool, file_and_apply, result: Dict[str, Any]) -> Dict[str, Any]:
    from backend.core.config import settings
    from backend.provider.adapter import get_active_provider
    from backend.workspace_api.repository import list_projects

    provider = get_active_provider()
    if not (settings.get_active_credentials().get("api_key") and hasattr(provider, "chat_completion")):
        return {"ran": False, "reason": "no_model"}
    projects = [p for p in list_projects() if (p.get("status") or "active") not in INACTIVE_STATUSES]
    signature = _project_signature(projects)
    state = _load_state()
    if not force and state.get("signature") == signature:
        return {"ran": False, "reason": "project_list_unchanged"}
    if not settings.check_and_increment_daily_call():
        return {"ran": False, "reason": "daily_call_limit_reached"}
    out = detect_ai_merge_proposals(projects, provider, exclude_ids=_pending_merge_project_ids())
    if out.get("error"):
        return {"ran": True, "reason": "model_error", "error": out["error"]}
    result["groups"] += len(out["proposals"])
    file_and_apply(out["proposals"])
    after = [p for p in list_projects() if (p.get("status") or "active") not in INACTIVE_STATUSES]
    _save_state({"signature": _project_signature(after), "last_ai_run": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                 "groups": len(out["proposals"])})
    return {"ran": True, "reason": "forced" if force else "project_list_changed", "groups": len(out["proposals"])}

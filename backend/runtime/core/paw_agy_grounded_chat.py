#!/usr/bin/env python3
"""PAW Web Agy grounded-chat contract — PAW_AGY_GROUNDED_CHAT_V1.

Read-only four-layer evidence retrieval, extension-intent gating, and
deterministic post-response citation/mode validation for /v1/web/agy.
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

CONTRACT = "PAW_AGY_GROUNDED_CHAT_V1"
DEFAULT_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
FAIL_CLOSED_MESSAGE = "PAW 当前资料中没有足够依据回答这个问题，我不能凭空补充。"
MAX_EVIDENCE_ITEMS = 40
MAX_EXCERPT_CHARS = 1200

LAYERS = ("SOURCE", "KNOWLEDGE", "PROJECT", "CAPTURE")

_EXTENSION_TRUE_RE = re.compile(
    r"(?:"
    r"有没有(?:更好|其他)?的?(?:建议|方案|设想|想法)|"
    r"你有什么建议|怎么优化|下一步(?:怎么|如何)?做|"
    r"有没有别的方案|基于这些信息帮我设想|"
    r"你认为应该怎么推进|给我一些建议|"
    r"有什么(?:更好|其他)的?(?:办法|思路|方向)|"
    r"帮我(?:想想|设计|规划)(?:一下)?(?:方案|思路)?|"
    r"如何改进|怎样改进|怎么推进"
    r")",
    re.IGNORECASE,
)
_EXTENSION_FALSE_RE = re.compile(
    r"(?:"
    r"^(?:现在)?项目进度|这个政策什么时候|这个人是谁|"
    r"总结(?:一下)?|对比(?:一下|这两个)|这条快速记录说了什么|"
    r"是什么|什么时候|谁负责|当前状态|现状"
    r")",
    re.IGNORECASE,
)
_HISTORICAL_REQUEST_RE = re.compile(
    r"(?:历史版本|之前版本|旧版本|对比.*版本|superseded|archived|"
    r"被取代|归档版本|更早的版本)",
    re.IGNORECASE,
)
_EVIDENCE_ID_RE = re.compile(r"\[E-[A-Z]+-\d{3,}\]")
_CITATION_RE = re.compile(r"\[E-(?:SRC|KNOW|PROJ|CAP|MSG)-\d{3,}\]")
_UNSOLICITED_ADVICE_RE = re.compile(
    r"(?:"
    r"建议(?:你|可以|不妨)|可以考虑|下一步(?:可以|建议|应该)|"
    r"推荐(?:你)?|不妨|方案上|我认为应该|或许可以|"
    r"建议你|值得考虑|优先(?:考虑|推进)"
    r")",
    re.IGNORECASE,
)
_PAW_FACTUAL_CLAIM_RE = re.compile(
    r"(?:"
    r"项目(?:进度|状态|目标)|当前(?:进度|状态)|已完成|待办|"
    r"根据PAW|资料显示|记录显示|来源显示|"
    r"政策|生效|负责人|里程碑|KPI|交付"
    r")",
    re.IGNORECASE,
)
_QUERY_STOPWORDS = frozenset(
    {
        "项目", "资料", "记录", "方案", "总结", "我的", "一下", "介绍", "有没有",
        "关于", "什么", "哪些", "怎么", "如何", "请", "帮我", "告诉", "说说",
        "里面", "当中", "当前", "现有", "这个", "那个", "我们", "你们", "他们",
        "是否", "能否", "可以", "一下", "简要", "简单", "概括", "概述", "查询",
        "查找", "搜索", "paw", "PAW", "工作", "内容", "情况", "信息", "数据",
        "文件", "文档", "来源", "相关", "有关", "一下下", "给我", "帮忙",
    }
)
_RECENT_CAPTURE_RE = re.compile(
    r"(?:今天|今日|最近|刚才|最新).{0,8}(?:记录|捕获|capture)|"
    r"(?:记录|捕获).{0,8}(?:今天|今日|最近|最新)",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _excerpt(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _knowledge_text(revision: Mapping[str, Any] | None) -> str:
    if not revision:
        return ""
    content = revision.get("content_json")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return _excerpt(content)
    if isinstance(content, dict):
        for key in ("body", "text", "summary", "markdown", "content"):
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return _excerpt(val)
        return _excerpt(json.dumps(content, ensure_ascii=False))
    if isinstance(content, list):
        parts = [str(x) for x in content if x]
        return _excerpt("\n".join(parts))
    return ""


def classify_extension_intent(message: str) -> bool:
    """Deterministic extension/advice intent gate. Default FALSE."""
    msg = (message or "").strip()
    if not msg:
        return False
    if _EXTENSION_FALSE_RE.search(msg):
        return False
    return bool(_EXTENSION_TRUE_RE.search(msg))


def wants_historical_sources(message: str) -> bool:
    return bool(_HISTORICAL_REQUEST_RE.search(message or ""))


def _make_evidence(
    *,
    layer: str,
    seq: int,
    entity_id: str,
    title: str,
    content: str,
    authority_state: str,
    temporal_state: str = "current",
    provenance: list[str] | None = None,
    source_refs: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prefix = {"SOURCE": "SRC", "KNOWLEDGE": "KNOW", "PROJECT": "PROJ", "CAPTURE": "CAP"}.get(layer, "UNK")
    item: dict[str, Any] = {
        "evidence_id": f"E-{prefix}-{seq:03d}",
        "layer": layer,
        "entity_id": entity_id,
        "title": title,
        "authority_state": authority_state,
        "temporal_state": temporal_state,
        "content": _excerpt(content),
        "provenance": provenance or [],
        "source_refs": source_refs or [],
        "retrieved_at": _now_iso(),
    }
    if extra:
        item.update(extra)
    return item


def _normalize_match_text(text: str) -> str:
    cleaned = re.sub(r"[\s\u3000，。！？、；：""''（）\[\]【】_\-/·]+", "", _text(text))
    for stop in sorted(_QUERY_STOPWORDS, key=len, reverse=True):
        cleaned = cleaned.replace(stop, "")
    return cleaned.casefold()


def _extract_entity_phrases(message: str) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,16}", message or ""):
        run = run.strip()
        if not run or run in _QUERY_STOPWORDS:
            continue
        if run not in seen:
            phrases.append(run)
            seen.add(run)
        for size in (4, 3, 2):
            if len(run) >= size:
                chunk = run[:size]
                if chunk not in _QUERY_STOPWORDS and chunk not in seen:
                    phrases.append(chunk)
                    seen.add(chunk)
    return phrases


def resolve_project_from_message(message: str) -> dict[str, Any]:
    """Conservative project resolution from user message text."""
    result: dict[str, Any] = {
        "project_id": None,
        "project_name": None,
        "ambiguous": False,
        "candidates": [],
        "resolution_method": None,
    }
    msg = _text(message)
    if not msg:
        return result
    try:
        from workspace_api import repository as ws_repo

        projects = [p for p in ws_repo.list_projects(200) if _text(p.get("status")) != "archived"]
    except Exception:
        return result

    msg_norm = _normalize_match_text(msg)
    phrases = _extract_entity_phrases(msg)
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for project in projects:
        pid = _text(project.get("id"))
        pname = _text(project.get("name"))
        if not pid or not pname:
            continue
        pname_norm = _normalize_match_text(pname)
        score = 0
        method: str | None = None

        if pname_norm and pname_norm in msg_norm:
            score = 100
            method = "name_containment"
        else:
            for phrase in phrases:
                phrase_norm = _normalize_match_text(phrase)
                if len(phrase_norm) < 2 or phrase_norm in _QUERY_STOPWORDS:
                    continue
                if len(phrase_norm) >= 3 and phrase_norm in pname_norm:
                    score = max(score, 80)
                    method = "phrase_in_name"
                elif len(phrase_norm) >= 2 and phrase_norm in pname_norm:
                    score = max(score, 65)
                    method = "short_phrase_in_name"

        if score >= 65 and pid not in seen_ids:
            matches.append({"id": pid, "name": pname, "score": score, "method": method})
            seen_ids.add(pid)

    if len(matches) == 1:
        result["project_id"] = matches[0]["id"]
        result["project_name"] = matches[0]["name"]
        result["resolution_method"] = matches[0]["method"]
    elif len(matches) > 1:
        top_score = max(match["score"] for match in matches)
        top = [match for match in matches if match["score"] == top_score]
        if len(top) > 1:
            result["ambiguous"] = True
            result["candidates"] = top
        else:
            winner = top[0]
            result["project_id"] = winner["id"]
            result["project_name"] = winner["name"]
            result["resolution_method"] = winner["method"]
    return result


def _significant_terms(message: str, project_name: str | None = None) -> list[str]:
    terms = [_normalize_match_text(phrase) for phrase in _extract_entity_phrases(message)]
    terms = [term for term in terms if len(term) >= 3 and term not in _QUERY_STOPWORDS]
    if project_name:
        pname = _normalize_match_text(project_name)
        if len(pname) >= 3:
            terms.append(pname)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            deduped.append(term)
            seen.add(term)
    return deduped


def _source_passes_relevance(
    message: str,
    result: Mapping[str, Any],
    *,
    project_name: str | None = None,
) -> bool:
    title = _text(result.get("source"))
    content = _text(result.get("content"))
    haystack = _normalize_match_text(f"{title}\n{content}")
    if not haystack:
        return False
    if project_name:
        pname = _normalize_match_text(project_name)
        if len(pname) >= 3 and pname in haystack:
            return True
    terms = _significant_terms(message, project_name)
    if not terms:
        return False
    return any(term in haystack for term in terms)


def _knowledge_passes_relevance(
    message: str,
    *,
    title: str,
    summary: str,
    body: str,
    project_linked: bool,
) -> bool:
    # P0-4: project linkage may RAISE relevance, it must never BYPASS the
    # authority/currentness gate applied by the caller.
    haystack = _normalize_match_text(f"{title}\n{summary}\n{body}")
    terms = _significant_terms(message)
    if not terms:
        return bool(project_linked)
    return any(term in haystack for term in terms)


def _wants_recent_captures(message: str) -> bool:
    return bool(_RECENT_CAPTURE_RE.search(message or ""))


def _capture_passes_relevance(
    message: str,
    capture_text: str,
    *,
    project_name: str | None = None,
) -> bool:
    haystack = _normalize_match_text(capture_text)
    if project_name:
        pname = _normalize_match_text(project_name)
        if len(pname) >= 3 and pname in haystack:
            return True
    terms = _significant_terms(message, project_name)
    if not terms:
        return False
    return any(term in haystack for term in terms)


def _retrieve_source(
    message: str,
    project_id: str | None,
    include_historical: bool,
    errors: list[str],
    *,
    project_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"attempted": True, "hits": 0, "authority_conflict": False, "global_rejected": 0}
    seq = 1

    if project_id:
        try:
            from paw_source_version_governance import read_project_version_context

            vctx = read_project_version_context(DEFAULT_DB, project_id)
            current = list(vctx.get("current_source_versions") or [])
            superseded = list(vctx.get("superseded_source_versions") or [])
            unresolved = list(vctx.get("unresolved_conflicts") or [])
            if unresolved:
                meta["authority_conflict"] = True
            show_ids = current + (superseded if include_historical else [])
            for sid in show_ids[:8]:
                temporal = "historical" if sid in superseded else "current"
                auth = "superseded" if sid in superseded else "current_effective"
                if sid in unresolved:
                    auth = "unresolved"
                items.append(
                    _make_evidence(
                        layer="SOURCE",
                        seq=seq,
                        entity_id=sid,
                        title=f"SourceVersion {sid}",
                        content=f"项目关联来源版本 {sid}（治理状态：{auth}）",
                        authority_state=auth,
                        temporal_state=temporal,
                        provenance=["source_version_governance"],
                        source_refs=[sid],
                    )
                )
                seq += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"SOURCE_VERSION: {exc}")

    try:
        from brain.source_routing import route_source_content, _extract_query, _cjk_tokens

        query = _extract_query(message) or message[:64]
        if not query.strip():
            tokens = _cjk_tokens(message)
            query = tokens[0] if tokens else message[:32]
        result = route_source_content(query, max_chars=8000)
        if result.get("available") and result.get("content"):
            if _source_passes_relevance(message, result, project_name=project_name):
                entity_id = _text(result.get("source")) or "routed-source"
                if not any(item.get("entity_id") == entity_id for item in items):
                    items.append(
                        _make_evidence(
                            layer="SOURCE",
                            seq=seq,
                            entity_id=entity_id,
                            title=_text(result.get("source")) or "来源资料",
                            content=_text(result.get("content")),
                            authority_state="current_effective",
                            temporal_state="current",
                            provenance=[_text(result.get("provenance"))],
                            source_refs=[_text(result.get("source"))] if result.get("source") else [],
                            extra={"disclosed": _text(result.get("disclosed"))},
                        )
                    )
                    seq += 1
            else:
                meta["global_rejected"] = meta.get("global_rejected", 0) + 1
    except Exception as exc:  # noqa: BLE001
        errors.append(f"SOURCE: {exc}")

    meta["hits"] = len(items)
    return items, meta


def _retrieve_knowledge(
    message: str,
    project_id: str | None,
    errors: list[str],
    *,
    project_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"attempted": True, "hits": 0}
    seq = 1
    try:
        from workspace_api import repository as ws_repo
        from workspace_api.current_context_policy import (
            is_historical_query,
            knowledge_authority_state,
        )

        linked_ids: set[str] = set()
        candidates: list[tuple[dict[str, Any], bool]] = []
        if project_id:
            ctx = ws_repo.project_context(project_id)
            if ctx:
                # P0-2/P0-4: the CANDIDATE POOL is the full project-linked set.
                # "knowledge" is already gated to current-confirmed, so using it
                # here would silently narrow retrieval to whatever happens to be
                # confirmed; the authority gate is applied per item below.
                for kn in ctx.get("knowledge_all") or ctx.get("knowledge") or []:
                    kid = _text(kn.get("id"))
                    if kid:
                        linked_ids.add(kid)
                        candidates.append((kn, True))
        for page in ws_repo.list_knowledge(limit=30):
            kid = _text(page.get("id"))
            if not kid or kid in linked_ids:
                continue
            candidates.append((page, False))
        seen: set[str] = set()
        _historical = wants_historical_sources(message) or is_historical_query(message)
        for kn, project_linked in candidates[:20]:
            kid = _text(kn.get("id"))
            if not kid or kid in seen:
                continue
            detail = ws_repo.get_knowledge(kid)
            if not detail:
                continue
            page = detail.get("page") or {}
            revision = detail.get("latest_revision")
            body = _knowledge_text(revision)
            title = _text(page.get("title")) or kid
            # knowledge_page carries one_line_summary, not summary.
            summary = _text(page.get("one_line_summary") or page.get("summary"))
            # P0-4 Current-State Admission Policy.  Default queries admit ONLY
            # current + confirmed-latest Knowledge.  Provisional / historical
            # Knowledge requires an explicit historical query and is then
            # disclosed as non-canonical.  Nothing is deleted.
            _authority_state = knowledge_authority_state(page)
            if _authority_state != "CURRENT_CONFIRMED" and not _historical:
                continue
            if not _knowledge_passes_relevance(
                message,
                title=title,
                summary=summary,
                body=body,
                project_linked=project_linked,
            ):
                continue
            seen.add(kid)
            status = _text(page.get("status")) or "unknown"
            if _authority_state == "CURRENT_CONFIRMED":
                authority = "canonical"
            elif _authority_state == "HISTORICAL_DEPRECATED":
                authority = "historical:deprecated"
            else:
                authority = f"provisional:{status}"
            sources = [f"{s.get('title')}({s.get('wps_document_id')})" for s in (detail.get("sources") or [])]
            if not sources:
                # R-267: a knowledge page created from a scanned document records
                # the creation candidate's source hash; resolve it to the file name
                # so an answer citing this page can also name the original document.
                try:
                    from contextlib import closing
                    from backend.pm import evidence_sources
                    from backend.workspace_api import db as workspace_db
                    with closing(workspace_db.connect()) as _conn:
                        sources = evidence_sources.knowledge_source_files(_conn, kid)
                except Exception:  # noqa: BLE001 - retrieval still works without it
                    sources = []
            items.append(
                _make_evidence(
                    layer="KNOWLEDGE",
                    seq=seq,
                    entity_id=kid,
                    title=title,
                    content=body or summary,
                    authority_state=authority,
                    temporal_state=(
                        "current" if _authority_state == "CURRENT_CONFIRMED"
                        else ("historical" if _authority_state == "HISTORICAL_DEPRECATED" else "provisional")
                    ),
                    provenance=sources,
                    source_refs=sources,
                    extra={"project_linked": project_linked},
                )
            )
            seq += 1
            if len(items) >= 10:
                break
    except Exception as exc:  # noqa: BLE001
        errors.append(f"KNOWLEDGE: {exc}")
    meta["hits"] = len(items)
    return items, meta


def _retrieve_project(project_id: str | None, errors: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"attempted": True, "hits": 0}
    if not project_id:
        meta["attempted"] = True
        return items, meta
    seq = 1
    try:
        from workspace_api import repository as ws_repo
        from workspace_api.current_context_policy import is_current_business_project

        ctx = ws_repo.project_context(project_id)
        if not ctx:
            errors.append(f"PROJECT: not found {project_id}")
            return items, meta
        project = ctx.get("project") or {}
        # P0-5: archived / non-business / evidence-container Projects must not
        # enter default Current-State synthesis.  Fail closed with disclosure.
        if not is_current_business_project(project):
            errors.append(
                f"PROJECT: excluded non-current-business project {project_id} "
                f"(status={_text(project.get('status'))})"
            )
            meta["excluded_reason"] = "NOT_CURRENT_BUSINESS_PROJECT"
            return items, meta
        parts = [
            f"名称：{_text(project.get('name'))}",
            f"状态：{_text(project.get('status'))}",
            f"描述：{_excerpt(_text(project.get('description')), 400)}",
        ]
        explainer = ctx.get("explainer") or ctx.get("project_explainer")
        if isinstance(explainer, dict):
            for key in ("one_sentence", "current_goal", "why_it_matters"):
                val = _text(explainer.get(key))
                if val:
                    parts.append(f"{key}：{val}")
        # P0-1: read_project_handbook() returns the key "project_handbook",
        # not "handbook".  Its sections_json stores section names as top-level
        # keys whose values are plain strings or mappings - there is no
        # "sections" list, so the old lookup always yielded nothing.
        handbook = ctx.get("project_handbook") or ctx.get("handbook")
        if isinstance(handbook, dict):
            raw_sections = handbook.get("sections")
            if not isinstance(raw_sections, list):
                raw_sections = []
                for _key, _val in handbook.items():
                    if str(_key).startswith("_"):
                        continue
                    if isinstance(_val, str):
                        raw_sections.append({"title": _key, "summary": _val})
                    elif isinstance(_val, dict):
                        raw_sections.append({
                            "title": _key,
                            "summary": _val.get("summary") or _val.get("body")
                            or _val.get("content") or _val.get("text") or "",
                        })
            for sec in raw_sections[:6]:
                if isinstance(sec, dict):
                    _sec_title = _text(sec.get("title"))
                    _sec_body = _text(sec.get("summary") or sec.get("body") or sec.get("content"))
                    if _sec_title and _sec_body:
                        parts.append(f"手册·{_sec_title}：{_excerpt(_sec_body, 200)}")
        soul = ctx.get("project_soul_status") or {}
        if soul:
            parts.append(
                f"Soul质量：{_text(soul.get('soul_quality'))}；bootstrap：{_text(soul.get('bootstrap_state'))}"
            )
        todos = ctx.get("todos") or []
        if todos:
            open_todos = [t for t in todos if _text(t.get("status")) not in {"done", "cancelled"}][:5]
            if open_todos:
                parts.append("开放待办：" + "；".join(_text(t.get("title")) for t in open_todos))
        items.append(
            _make_evidence(
                layer="PROJECT",
                seq=seq,
                entity_id=project_id,
                title=_text(project.get("name")) or project_id,
                content="\n".join(parts),
                authority_state="canonical",
                temporal_state="current",
                provenance=["project_context"],
            )
        )
        seq += 1
        try:
            from paw_project_outcome import read_outcome

            outcome = read_outcome(DEFAULT_DB, project_id)
            if outcome:
                summary = _text(outcome.get("executive_summary"))
                if summary:
                    items.append(
                        _make_evidence(
                            layer="PROJECT",
                            seq=seq,
                            entity_id=f"{project_id}:outcome",
                            title=f"{_text(project.get('name'))} · Outcome",
                            content=summary,
                            authority_state="canonical",
                            temporal_state="current",
                            provenance=["project_outcome"],
                            source_refs=[
                                _text(r.get("source_version_id"))
                                for r in (outcome.get("source_evidence_refs") or [])
                                if isinstance(r, dict) and r.get("source_version_id")
                            ],
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"PROJECT_OUTCOME: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"PROJECT: {exc}")
    meta["hits"] = len(items)
    return items, meta


def _retrieve_capture(
    message: str,
    project_id: str | None,
    errors: list[str],
    *,
    project_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"attempted": True, "hits": 0, "conflicts": []}
    seq = 1
    try:
        from workspace_api import repository as ws_repo
        import sqlite3 as _sqlite3
        from paw_capture_lifecycle import enrich_capture_projection

        keyword = _text(message)[:200]
        captures: list[dict[str, Any]] = []
        if keyword:
            try:
                result = ws_repo.search_captures(keyword, limit=8)
                for hit in result.get("items") or []:
                    cid = hit.get("capture_id")
                    if cid:
                        conn = _sqlite3.connect(DEFAULT_DB)
                        conn.row_factory = _sqlite3.Row
                        try:
                            row = conn.execute("SELECT * FROM capture WHERE id=?", (cid,)).fetchone()
                            if row:
                                captures.append(enrich_capture_projection(conn, dict(row)))
                        finally:
                            conn.close()
            except ValueError:
                pass
        if _wants_recent_captures(message):
            today = ws_repo.today_view()
            for cap in (today.get("captures") or [])[:5]:
                if cap.get("id") not in {c.get("id") for c in captures}:
                    captures.append(cap)
        seen: set[str] = set()
        for cap in captures[:10]:
            cid = _text(cap.get("id"))
            if not cid or cid in seen:
                continue
            raw = _text(cap.get("raw_text"))
            if not _capture_passes_relevance(message, raw, project_name=project_name):
                continue
            seen.add(cid)
            lifecycle = _text(cap.get("patrol_state") or cap.get("lifecycle_display_label") or "RAW_USER_CAPTURE")
            authority = "RAW_USER_CAPTURE"
            if lifecycle in {"waiting_human", "WAITING_HUMAN"}:
                authority = "waiting_human"
            elif lifecycle in {"waiting_patrol", "WAITING_PATROL", "waiting_gpt", "WAITING_GPT"}:
                authority = "waiting_patrol"
            elif lifecycle in {"gpt_resolved", "GPT_RESOLVED"}:
                authority = "resolved"
            items.append(
                _make_evidence(
                    layer="CAPTURE",
                    seq=seq,
                    entity_id=cid,
                    title=f"快速记录 {cid[:8]}",
                    content=raw,
                    authority_state=authority,
                    temporal_state="current",
                    provenance=["quick_capture"],
                    extra={
                        "lifecycle": lifecycle,
                        "captured_at": cap.get("captured_at"),
                        "raw_text": raw,
                    },
                )
            )
            seq += 1
        if project_id and items:
            try:
                ctx = ws_repo.project_context(project_id)
                pname = _text((ctx or {}).get("project", {}).get("name")) or project_name
                for cap_item in items:
                    cap_raw = _text(cap_item.get("content"))
                    if pname and pname in cap_raw and cap_item.get("authority_state") == "RAW_USER_CAPTURE":
                        meta["conflicts"].append(
                            {
                                "capture_id": cap_item.get("entity_id"),
                                "note": f"快速记录提及项目「{pname}」，但与正式 Project 状态可能不一致，需人工核对。",
                            }
                        )
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CAPTURE: {exc}")
    meta["hits"] = len(items)
    return items, meta


def build_evidence_bundle(
    message: str,
    *,
    project_id: str | None = None,
    include_historical: bool | None = None,
) -> dict[str, Any]:
    """Build bounded read-only four-layer evidence bundle for one user turn."""
    errors: list[str] = []
    if include_historical is None:
        include_historical = wants_historical_sources(message)
    extension_allowed = classify_extension_intent(message)

    resolution = resolve_project_from_message(message) if not project_id else {
        "project_id": project_id,
        "project_name": None,
        "ambiguous": False,
        "candidates": [],
        "resolution_method": "explicit",
    }
    if not project_id and resolution.get("project_id") and not resolution.get("ambiguous"):
        project_id = _text(resolution.get("project_id")) or None
    project_name = _text(resolution.get("project_name")) or None
    if resolution.get("ambiguous"):
        errors.append(
            "PROJECT_RESOLUTION: ambiguous matches: "
            + "；".join(_text(c.get("name")) for c in (resolution.get("candidates") or [])[:5])
        )

    proj_items, proj_meta = _retrieve_project(project_id if not resolution.get("ambiguous") else None, errors)
    know_items, know_meta = _retrieve_knowledge(
        message,
        project_id if not resolution.get("ambiguous") else None,
        errors,
        project_name=project_name,
    )
    source_items, source_meta = _retrieve_source(
        message,
        project_id if not resolution.get("ambiguous") else None,
        include_historical,
        errors,
        project_name=project_name,
    )
    cap_items, cap_meta = _retrieve_capture(
        message,
        project_id if not resolution.get("ambiguous") else None,
        errors,
        project_name=project_name,
    )

    all_items = (proj_items + know_items + source_items + cap_items)[:MAX_EVIDENCE_ITEMS]
    layers_attempted = list(LAYERS)
    layers_with_hits = sorted({i["layer"] for i in all_items})

    user_item = _make_evidence(
        layer="USER_MESSAGE",
        seq=1,
        entity_id="current-turn",
        title="用户消息",
        content=_text(message),
        authority_state="user_input",
        temporal_state="current",
    )
    user_item["evidence_id"] = "E-MSG-001"

    return {
        "contract": CONTRACT,
        "retrieved_at": _now_iso(),
        "project_id": project_id,
        "project_resolution": resolution,
        "extension_allowed": extension_allowed,
        "include_historical": include_historical,
        "evidence": all_items,
        "user_message": user_item,
        "evidence_count": len(all_items),
        "layers_attempted": layers_attempted,
        "layers_with_hits": layers_with_hits,
        "authority_conflict": bool(source_meta.get("authority_conflict")),
        "capture_conflicts": cap_meta.get("conflicts") or [],
        "retrieval_errors": errors,
        "layer_meta": {
            "SOURCE": source_meta,
            "KNOWLEDGE": know_meta,
            "PROJECT": proj_meta,
            "CAPTURE": cap_meta,
        },
    }


def evidence_id_set(bundle: Mapping[str, Any]) -> set[str]:
    ids = {item["evidence_id"] for item in bundle.get("evidence") or [] if item.get("evidence_id")}
    um = bundle.get("user_message")
    if isinstance(um, dict) and um.get("evidence_id"):
        ids.add(um["evidence_id"])
    return ids


def format_evidence_block(bundle: Mapping[str, Any]) -> str:
    layer_labels = {
        "SOURCE": "资料", "KNOWLEDGE": "知识", "PROJECT": "项目档案",
        "CAPTURE": "随手记", "PROJECT_HANDBOOK": "项目档案",
        "CONFIRMED_KNOWLEDGE": "已确认知识",
    }
    authority_labels = {
        "formal": "正式记录", "canonical": "规范记录", "evidence": "资料证据",
        "user_input": "用户输入", "draft": "待确认草稿",
        "A_EVIDENCE": "资料证据", "A_CANONICAL": "正式记录",
    }
    temporal_labels = {"current": "当前有效", "superseded": "已被替代", "historical": "历史资料"}
    lines = [
        "回答规则：依据已检索资料；建议权限已由系统按问题判定。",
        f"项目范围：{'已指定项目' if bundle.get('project_id') else '未指定项目'}",
        f"证据条数：{bundle.get('evidence_count', 0)}",
    ]
    if bundle.get("authority_conflict"):
        lines.append("⚠ 来源版本存在未解决的权威冲突，不得自行择一认定。")
    for conflict in bundle.get("capture_conflicts") or []:
        lines.append(f"⚠ Capture/Canonical 冲突：{conflict.get('note')}")
    for err in bundle.get("retrieval_errors") or []:
        lines.append(f"[检索告警] {err}")
    lines.append("\n--- 证据清单（回答时必须引用 evidence_id）---")
    for item in bundle.get("evidence") or []:
        lines.append(
            f"[{item['evidence_id']}] 类型={layer_labels.get(str(item.get('layer')), '工作台记录')} "
            f"依据={authority_labels.get(str(item.get('authority_state')), '资料记录')} "
            f"时效={temporal_labels.get(str(item.get('temporal_state')), '状态待确认')}\n"
            f"  标题：{item.get('title')}\n"
            f"  内容：{item.get('content')}"
        )
    return "\n".join(lines)


def build_grounded_system_instruction(mode: str, bundle: Mapping[str, Any]) -> str:
    extension = bool(bundle.get("extension_allowed"))
    mode_hints = {
        "chat": "你是 PAW 工作台助手 Agy，只能基于下方证据回答。",
        "extract": "你是 PAW 数据提取助手，只提取证据中明确存在的信息。",
        "analyze": "你是 PAW 分析助手，分析必须完全基于证据，不得外推。",
        "plan": "你是 PAW 规划助手，规划内容必须来自证据且用户已请求延伸。",
    }
    base = mode_hints.get(mode, mode_hints["chat"])
    rules = [
        base,
        "硬性规则：",
        "1. 所有 PAW 特定事实陈述必须附带 [E-XXX-NNN] 证据引用。",
        "2. 不得用模型常识或外部知识填补 PAW 资料空白。",
        "3. 证据不足时明确说明缺少什么，不要猜测。",
        "4. 证据冲突时披露冲突与各方 authority/temporal 状态，不得自行调和。",
        "5. 推断须标注为「推断」；draft/provisional 内容须标注为非正式事实。",
        "6. 用户询问目标或计划指标时，以当前有效版本为准，明确区分计划目标与周报进度；不得把已招募、已完成等进度数字当作目标。版本变更仍待用户决定时，明确说明待确认并列出新旧目标。",
    ]
    if extension:
        rules.append(
            "7. 用户已请求建议/方案：可提供「建议/推演/方案」段落，"
            "但必须与事实分段、基于证据推导，且不得引入无证据的 PAW 特定事实。"
        )
    else:
        rules.append(
            "7. extension_allowed=false：禁止主动给出建议、下一步、推荐、方案、"
            "「可以考虑」「不妨」「我认为应该」等延伸性内容。只回答证据支持的事实。"
        )
    return "\n".join(rules)


def build_grounded_prompt(message: str, mode: str, bundle: Mapping[str, Any]) -> str:
    parts = [
        build_grounded_system_instruction(mode, bundle),
        "\n--- PAW 证据包 ---\n",
        format_evidence_block(bundle),
        f"\n--- 用户消息 ---\n{message}",
    ]
    return "\n".join(parts)


def validate_grounded_response(
    response: str,
    bundle: Mapping[str, Any],
    *,
    extension_allowed: bool | None = None,
) -> tuple[bool, str]:
    """Lightweight deterministic post-response gate."""
    text = (response or "").strip()
    if not text:
        return False, "empty_response"
    if extension_allowed is None:
        extension_allowed = bool(bundle.get("extension_allowed"))
    valid_ids = evidence_id_set(bundle)
    cited = set(_CITATION_RE.findall(text))

    for cite in cited:
        if cite.strip("[]") not in valid_ids:
            return False, f"invalid_citation:{cite}"

    has_paw_claim = bool(_PAW_FACTUAL_CLAIM_RE.search(text))
    has_citation = bool(cited)
    if has_paw_claim and not has_citation and valid_ids:
        return False, "uncited_paw_factual_claim"

    if not extension_allowed and _UNSOLICITED_ADVICE_RE.search(text):
        return False, "unsolicited_advice"

    return True, "ok"


def should_skip_inference(bundle: Mapping[str, Any]) -> bool:
    """Fail closed when zero evidence and no user message context needed."""
    return int(bundle.get("evidence_count") or 0) == 0


def grounding_metadata(bundle: Mapping[str, Any], *, grounded: bool) -> dict[str, Any]:
    return {
        "contract": bundle.get("contract"),
        "grounded": grounded,
        "evidence_count": bundle.get("evidence_count", 0),
        "layers_attempted": bundle.get("layers_attempted"),
        "layers_with_hits": bundle.get("layers_with_hits"),
        "project_id": bundle.get("project_id"),
        "authority_conflict": bool(bundle.get("authority_conflict")),
        "extension_allowed": bool(bundle.get("extension_allowed")),
        "capture_conflicts": bundle.get("capture_conflicts") or [],
    }


def run_grounded_chat_turn(
    message: str,
    mode: str,
    *,
    project_id: str | None = None,
    infer: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Orchestrate one grounded turn: bundle → optional inference → gate → retry."""
    bundle = build_evidence_bundle(message, project_id=project_id)
    meta = grounding_metadata(bundle, grounded=False)

    if should_skip_inference(bundle):
        return {
            "response": FAIL_CLOSED_MESSAGE,
            "bundle": bundle,
            "grounding": meta,
            "inference_skipped": True,
            "gate": "no_evidence_fail_closed",
        }

    if infer is None:
        return {
            "response": "",
            "bundle": bundle,
            "grounding": {**meta, "grounded": True},
            "inference_skipped": True,
            "gate": "bundle_only",
        }

    prompt = build_grounded_prompt(message, mode, bundle)
    response = infer(prompt)
    ok, reason = validate_grounded_response(response, bundle)
    if ok:
        return {
            "response": response,
            "bundle": bundle,
            "grounding": {**meta, "grounded": True},
            "inference_skipped": False,
            "gate": reason,
        }

    strict_prompt = prompt + (
        "\n\n【重试约束】上次回答未通过审计："
        f"{reason}。请仅引用有效 evidence_id，"
        + ("不得给出未请求的建议。" if not bundle.get("extension_allowed") else "建议须标注且有据。")
    )
    retry_response = infer(strict_prompt)
    ok2, reason2 = validate_grounded_response(retry_response, bundle)
    if ok2:
        return {
            "response": retry_response,
            "bundle": bundle,
            "grounding": {**meta, "grounded": True, "retried": True},
            "inference_skipped": False,
            "gate": reason2,
        }
    return {
        "response": FAIL_CLOSED_MESSAGE,
        "bundle": bundle,
        "grounding": {**meta, "grounded": False, "gate_failed": reason2},
        "inference_skipped": False,
        "gate": "fail_closed_after_retry",
    }

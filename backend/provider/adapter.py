"""PAW Provider Adapter - Universal OpenAI-Compatible API Client & Explicit Rule Fallback.

Enforces Requirements:
- 4.1: Unified client using /chat/completions supporting DeepSeek, Qwen, OpenAI, Custom.
- 4.3: Real test_connection probe classifying failures into 5 human-readable categories.
- 4.4: Explicit fallback tagging (Never silently pretend rule fallback is AI).
- 4.5: Robustness with timeouts, retries, and token telemetry tracking.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

from backend.core.config import settings

logger = logging.getLogger("paw.provider")


class ChatCompletionResult(tuple):
    """3-tuple compatible with (content, usage, error) plus .search_info attribute."""

    def __new__(cls, content, usage, error, search_info=None):
        instance = super().__new__(cls, (content, usage, error))
        instance.search_info = search_info or {}
        return instance


class BaseProvider:
    def propose(
        self,
        evidence: Dict[str, Any],
        existing_projects: List[Dict[str, Any]],
        existing_knowledge: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError


class RuleBasedFallbackProvider(BaseProvider):
    """Offline heuristic engine that extracts grounded candidates from Reader Evidence.

    Requirement 4.4: Explicitly tags all outputs as rule-generated.
    """

    def propose(
        self,
        evidence: Dict[str, Any],
        existing_projects: List[Dict[str, Any]],
        existing_knowledge: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        candidates = []
        text_blocks = evidence.get("text_blocks", [])
        tables = evidence.get("tables", [])
        source_name = evidence.get("display_name") or evidence.get("source_object_id", "local_doc")
        sha256 = evidence.get("raw_artifact_sha256", "")

        if evidence.get("is_capture"):
            cap_text = evidence.get("text_blocks", [{}])[0].get("text", "")
            is_todo = any(kw in cap_text for kw in ["需", "要", "确认", "开会", "跟进", "排查", "完成", "处理", "对接", "提交", "准备", "计划", "通知"])
            aligned_proj = None
            for p in existing_projects:
                pname = p.get("name", "")
                if any(w in cap_text for w in [pname[:4], pname[-4:]] if len(w) >= 2):
                    aligned_proj = p
                    break
            
            if is_todo:
                return [{
                    "target_type": "todo",
                    "operation": "create",
                    "payload": {
                        "title": cap_text[:60],
                        "description": cap_text,
                        "project_id": aligned_proj["id"] if aligned_proj else None,
                        "priority": "medium",
                        "status": "open",
                    },
                    "raw_excerpt": cap_text,
                    "evidence_locators": ["text_blocks[0]"],
                    "confidence": "HIGH",
                    "source_generation": "rule",
                    "analysis_model": "rule:capture_heuristic",
                    "review_brief": {
                        "summary": f"根据便签生成待办：{cap_text[:40]}",
                        "risk_level": "LOW",
                        "recommended_action": "批准写入待办清单",
                    }
                }]
            else:
                return [{
                    "target_type": "knowledge",
                    "operation": "create",
                    "payload": {
                        "title": f"【记录】{cap_text[:30]}",
                        "content": cap_text,
                        "one_line_summary": cap_text[:80],
                        "knowledge_type": "project",
                        "status": "draft",
                        "project_name": aligned_proj["name"] if aligned_proj else None,
                    },
                    "raw_excerpt": cap_text,
                    "evidence_locators": ["text_blocks[0]"],
                    "confidence": "HIGH",
                    "source_generation": "rule",
                    "analysis_model": "rule:capture_heuristic",
                    "review_brief": {
                        "summary": f"根据便签归纳知识：{cap_text[:40]}",
                        "risk_level": "LOW",
                        "recommended_action": "批准归入知识库",
                    }
                }]

        # 0. Cold-start Project candidate proposal (Requirement H1)
        # If user has zero projects, or document describes a distinct project overview/plan, propose project candidate
        clean_stem = Path(source_name).stem.replace("_", " ")
        for pfx in ["PAW_", "项目_", "【项目】", "产品_"]:
            if clean_stem.startswith(pfx):
                clean_stem = clean_stem[len(pfx):]

        should_propose_project = False
        if not existing_projects:
            should_propose_project = True
        else:
            # Convergence rule: only propose a new project when NO existing project
            # shares a meaningful keyword overlap with the document name. A single
            # action/meeting/report must be folded into an existing project instead.
            def _key_tokens(s):
                return set(re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", s))
            stem_tokens = _key_tokens(clean_stem)
            overlap_found = False
            for p in existing_projects:
                p_tokens = _key_tokens(p.get("name", ""))
                if stem_tokens and len(stem_tokens & p_tokens) >= 1:
                    overlap_found = True
                    break
            if not overlap_found and any(kw in source_name for kw in ["项目", "规划", "战略", "工程"]):
                should_propose_project = True

        if should_propose_project:
            proj_name = clean_stem[:30]
            candidates.append({
                "target_type": "project",
                "operation": "create",
                "payload": {
                    "name": proj_name,
                    "description": f"由 PAW Scanner 从文档《{source_name}》建议初始化的工作台项目",
                    "status": "active",
                    "stage": "executing",
                    "priority": "medium",
                },
                "raw_excerpt": f"建议初始化项目：{proj_name}（来源：《{source_name}》）",
                "source_refs": [sha256] if sha256 else [],
                "confidence": "HIGH",
                "evidence_locators": [f"text_blocks[0]"] if text_blocks else [],
                "source_generation": "rule",
                "analysis_model": "rule:offline_engine",
                "review_brief": {
                    "summary": f"建议创建新工作台项目：{proj_name}",
                    "risk_level": "LOW",
                    "recommended_action": "批准创建项目并纳入工作台治理",
                    "generation_mode": "RULE_FALLBACK",
                    "rule_fallback_notice": "当前未使用 AI，仅为基础规则整理，质量有限",
                },
            })

        # 1. Propose Knowledge candidate from headings and initial paragraphs
        headings = [b for b in text_blocks if b.get("heading_level") or b.get("style", "").startswith("Heading")]
        title = headings[0]["text"].strip() if headings else Path(source_name).stem.replace("_", " ")
        slug = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", title.lower()).strip("-") or "imported-doc"

        content_lines = []
        locators = []
        for b in text_blocks[:8]:
            if b.get("text", "").strip():
                content_lines.append(b["text"].strip())
                locators.append(f"text_blocks[{b.get('index', 0)}]")

        # Extract 1-3 genuine sentences from body for real summary (Requirement H3.4, no fixed boilerplate)
        body_sentences = [l for l in content_lines if len(l) > 8 and not l.startswith("【") and not l.startswith("#")]
        extracted_summary = "；".join(body_sentences[:2]) if body_sentences else (content_lines[0] if content_lines else title)
        if len(extracted_summary) > 120:
            extracted_summary = extracted_summary[:120] + "..."

        knowledge_payload = {
            "title": f"【知识】{title}",
            "slug": f"doc-{slug[:40]}",
            "content": "\n\n".join(content_lines),
            "one_line_summary": extracted_summary,
            "applicability": "通用业务场景",
            "knowledge_type": "project",
        }

        candidates.append({
            "target_type": "knowledge",
            "operation": "create",
            "payload": knowledge_payload,
            "raw_excerpt": "\n".join(content_lines[:3]),
            "source_refs": [sha256] if sha256 else [],
            "confidence": "HIGH",
            "evidence_locators": locators,
            "source_generation": "rule",
            "analysis_model": "rule:offline_engine",
            "review_brief": {
                "summary": f"知识提炼：{extracted_summary[:45]}",
                "risk_level": "LOW",
                "recommended_action": "批准写入知识库",
                "generation_mode": "RULE_FALLBACK",
                "rule_fallback_notice": "当前未使用 AI，仅为基础规则整理，质量有限",
            },
        })

        # 2. Extract genuine actionable work items (Requirement F6 & H3: Actionable verbs only, no headings/SOPs)
        ACTION_VERBS = (
            "完成", "组织", "制定", "跟进", "编写", "协调", "测试", "部署",
            "排查", "修复", "核对", "上线", "召开", "提交", "联调", "演练",
            "压测", "梳理", "落实", "推进", "配置", "复核", "采购", "发布",
            "对接", "整改", "验收", "评估", "宣贯", "优化", "重构", "搭建",
            "建立", "实施", "交付"
        )
        NON_TODO_PATTERNS = re.compile(
            r"^(?:第[一二三四五六七八九十0-9]+[章节部分篇条]|"
            r"[一二三四五六七八九十0-9]+[、.．：:\s]|"
            r"【.*】$|"
            r"《.*》$|"
            r".*(?:SOP|规程|规范|标准|架构|方案|白皮书|概述|背景|目标|目录|总结|总览|原则|维度|体系)[：:\s]*$)",
            re.IGNORECASE
        )

        todo_candidates_created = 0
        seen_todos = set()

        for b in text_blocks:
            if b.get("heading_level") or b.get("style", "").startswith("Heading"):
                continue

            raw_txt = b.get("text", "").strip()
            if not raw_txt:
                continue

            # Split block into individual lines / sentences
            lines = [l.strip() for l in re.split(r"[\n\r；;]", raw_txt) if l.strip()]
            for line in lines:
                # Strip leading enumeration markers and task headers (Requirement H3.1)
                cleaned_action = re.sub(
                    r"^(?:下一步计划[：:\s]*|"
                    r"后续计划[：:\s]*|"
                    r"工作计划[：:\s]*|"
                    r"重点工作[：:\s]*|"
                    r"计划[：:\s]*|"
                    r"行动项\s*[0-9一二三四五六七八九十]*[：:、.\s]*|"
                    r"待办\s*[0-9一二三四五六七八九十]*[：:、.\s]*|"
                    r"任务\s*[0-9一二三四五六七八九十]*[：:、.\s]*|"
                    r"TODO[：:\s]*|"
                    r"[0-9]+[、.．\s]+|"
                    r"[（(][0-9一二三四五六七八九十]+[）)]\s*|"
                    r"[-*•]\s*(?:\[[ xX]?\]\s*)?)",
                    "",
                    line
                ).strip()

                if len(cleaned_action) < 6 or len(cleaned_action) > 100:
                    continue
                if NON_TODO_PATTERNS.match(cleaned_action):
                    continue
                if cleaned_action.endswith("：") or cleaned_action.endswith(":"):
                    continue
                # Filter out past status reports/summaries: "已完成...", "目前...", "本周..."
                if any(cleaned_action.startswith(w) for w in ["已完成", "已经", "目前", "过去", "上周", "本周", "截至目前", "存在"]):
                    continue

                # Must contain a recognizable action verb at start
                starts_with_verb = any(cleaned_action.startswith(v) for v in ACTION_VERBS)
                has_action_verb = starts_with_verb or any(v in cleaned_action[:6] for v in ACTION_VERBS)
                if not has_action_verb:
                    continue

                if cleaned_action in seen_todos:
                    continue
                seen_todos.add(cleaned_action)

                todo_payload = {
                    "title": cleaned_action[:60],
                    "description": f"来源: {source_name} (位置: text_blocks[{b.get('index', 0)}])\n原文: {line}",
                    "priority": "high" if any(w in line for w in ["紧急", "立刻", "核心", "重要", "今日", "截止"]) else "medium",
                }
                candidates.append({
                    "target_type": "todo",
                    "operation": "create",
                    "payload": todo_payload,
                    "raw_excerpt": line,
                    "source_refs": [sha256] if sha256 else [],
                    "confidence": "HIGH" if starts_with_verb else "MEDIUM",
                    "evidence_locators": [f"text_blocks[{b.get('index', 0)}]"],
                    "source_generation": "rule",
                    "analysis_model": "rule:offline_engine",
                    "review_brief": {
                        "summary": f"待办行动项：{cleaned_action[:30]}...",
                        "risk_level": "LOW",
                        "generation_mode": "RULE_FALLBACK",
                        "rule_fallback_notice": "当前未使用 AI，仅为基础规则整理，质量有限",
                    },
                })
                todo_candidates_created += 1
                if todo_candidates_created >= 3:
                    break
            if todo_candidates_created >= 3:
                break

        # 3. If there are tables with metric values, propose a metric candidate
        if tables:
            for t in tables[:1]:
                t_loc = f"tables[{t.get('index', 0)}]"
                candidates.append({
                    "target_type": "data",
                    "operation": "create",
                    "payload": {
                        "name": f"{title} 量化指标表",
                        "description": f"由表格提炼: {source_name} ({t_loc})",
                        "unit": "项/数值",
                        "direction": "higher_better",
                    },
                    "raw_excerpt": f"表格包含 {len(t.get('cells', []))} 个单元格数据",
                    "source_refs": [sha256] if sha256 else [],
                    "confidence": "HIGH",
                    "evidence_locators": [t_loc],
                    "source_generation": "rule",
                    "analysis_model": "rule:offline_engine",
                    "review_brief": {
                        "summary": "从结构化表格中识别出项目业务量化指标",
                        "risk_level": "LOW",
                        "generation_mode": "RULE_FALLBACK",
                        "rule_fallback_notice": "当前未使用 AI，仅为基础规则整理，质量有限",
                    },
                })

        return candidates


class UniversalOpenAIProvider(BaseProvider):
    """Unified client supporting DeepSeek, Qwen, OpenAI, or any OpenAI-compatible API.

    Requirement 4.1, 4.4, 4.5.
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback = RuleBasedFallbackProvider()

    def propose(
        self,
        evidence: Dict[str, Any],
        existing_projects: List[Dict[str, Any]],
        existing_knowledge: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not self.api_key:
            return self.fallback.propose(evidence, existing_projects, existing_knowledge)

        prompt = self._build_prompt(evidence, existing_projects, existing_knowledge)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 PAW (Personal AI Workspace) 的严肃认知治理专家。"
                        "你的职责是阅读高保真文档证据，并提出结构化的正式状态更新提议（Candidate）。"
                        "严格要求：证据第一（Evidence First），不得臆造事实。"
                        "必须输出 JSON 数组，每个元素包含："
                        "target_type ('knowledge' | 'todo' | 'project' | 'data'), "
                        "operation ('create' | 'update'), "
                        "payload (必须满足目标表字段), "
                        "raw_excerpt (文档原文引用), "
                        "evidence_locators (如 ['text_blocks[0]']), "
                        "confidence ('HIGH' | 'MEDIUM' | 'LOW'), "
                        "summary (简要说明)。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        # Exponential backoff retry on 429/503
        max_retries = 2
        backoff = 1.0

        for attempt in range(max_retries + 1):
            try:
                with httpx.Client(timeout=45.0) as client:
                    start_t = time.time()
                    resp = client.post(url, headers=headers, json=body)
                    latency_ms = int((time.time() - start_t) * 1000)

                    if resp.status_code == 200:
                        data = resp.json()
                        usage = data.get("usage", {})
                        tokens = usage.get("total_tokens", 0)
                        
                        # Update telemetry (Requirement 4.5)
                        settings.cumulative_calls += 1
                        settings.cumulative_tokens += tokens
                        settings.save()

                        content = data["choices"][0]["message"]["content"]
                        # Strip potential markdown fences
                        clean_content = re.sub(r"^```json\s*", "", content.strip())
                        clean_content = re.sub(r"\s*```$", "", clean_content)
                        parsed = json.loads(clean_content)
                        items = parsed.get("candidates") if isinstance(parsed, dict) else parsed

                        if isinstance(items, list) and len(items) > 0:
                            # Tag as AI generated (Requirement 4.4)
                            for it in items:
                                it["source_generation"] = "ai"
                                it["analysis_model"] = f"ai:{self.model}"
                                if "review_brief" not in it:
                                    it["review_brief"] = {}
                                it["review_brief"]["generation_mode"] = "AI_MODEL"
                                it["review_brief"]["model_name"] = self.model
                                it["review_brief"]["latency_ms"] = latency_ms
                            logger.info(f"AI Model ({self.model}) proposed {len(items)} candidates successfully.")
                            return items
                    elif resp.status_code in (429, 503) and attempt < max_retries:
                        time.sleep(backoff)
                        backoff *= 2.0
                        continue
                    else:
                        logger.warning(f"AI Provider HTTP {resp.status_code}: {resp.text[:120]}")
                        break
            except Exception as e:
                logger.warning(f"AI Provider request failed (attempt {attempt+1}): {e}")
                if attempt < max_retries:
                    time.sleep(backoff)
                    backoff *= 2.0
                else:
                    break

        # Explicit fallback to rule engine on failure
        logger.info(f"Falling back to rule-based proposal engine due to API unavailability.")
        return self.fallback.propose(evidence, existing_projects, existing_knowledge)

    def _build_prompt(self, evidence: dict, projects: list, knowledge: list) -> str:
        if evidence.get("is_capture"):
            cap_text = evidence.get("text_blocks", [{}])[0].get("text", "")
            return (
                f"【用户快速记录巡航提炼任务】\n"
                f"这是用户录入的一条便签备忘 / 快速记录（Capture）：\n"
                f"「{cap_text}」\n\n"
                f"现有活跃项目（完整列表，共 {len(projects)} 个）: {[p.get('name') for p in projects]}\n"
                "治理分析要求：\n"
                "1. 仔细识别该记录的核心业务意图：如果是行动项/待办，生成 todo 候选（payload: title, description, priority, due_at）；如果是业务事实/知识点/规则，生成 knowledge 候选（payload: title, content, one_line_summary, knowledge_type='project', status='draft'）；如果是项目进展/更新，生成 project 候选（operation='update', payload: name, latest_progress_summary）；\n"
                "2. 优先对齐归属到上述现有项目中语义最相关的一个；若不属于任何项目，可不填 project_id 或 project_name；\n"
                "3. 保持证据第一（Evidence First），提炼简明专业的标题、摘要与操作。\n"
                "请输出符合业务规范的提议列表（JSON数组）："
            )
        text_samples = [f"[{b.get('index')}] {b.get('text', '')[:100]}" for b in evidence.get("text_blocks", [])[:10]]
        return (
            f"文档名称: {evidence.get('display_name', 'untitled')}\n"
            f"已提取文本块样本:\n" + "\n".join(text_samples) + "\n\n"
            f"现有活跃项目（完整列表，共 {len(projects)} 个）: {[p.get('name') for p in projects]}\n"
            "项目归属规则（必须严格遵守）：\n"
            "1. 优先将知识/待办/指标归属到上述现有项目中语义最相关的一个；\n"
            "2. 只有当文档描述的是一个全新的、与所有现有项目都不相关的长期业务领域时，才允许新建项目；\n"
            "3. 禁止为单个动作、单次会议、单次汇报或单次盘点新建项目——这些应归属到对应的长期项目；\n"
            "4. 项目应按知识相关性收敛：同一业务领域的知识应归入同一项目。\n"
            "请基于上述文档内容，提出符合业务规范的提议列表（JSON格式）："
        )

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        enable_web_search: Optional[bool] = None,
    ) -> ChatCompletionResult:
        """General chat completion helper for Web Agy AI Assistant.

        Returns: ChatCompletionResult(response_text, usage_dict, error_message, search_info)
        """
        if not self.api_key:
            return ChatCompletionResult(None, {}, "NO_API_KEY", {"used": False, "supported": False})

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Check if web search should be enabled (Requirement F5: Verified platform matrix only)
        # DeepSeek official, OpenAI, and custom endpoints do NOT support enable_search; do NOT send unknown params.
        # Only Qwen (DashScope OpenAI-compatible mode) supports enable_search: true.
        p_name = settings.provider.lower()
        search_supported = (p_name == "qwen" or "dashscope" in self.base_url)
        do_search = enable_web_search if enable_web_search is not None else (settings.is_active_web_search_enabled() and search_supported)

        if do_search and search_supported:
            body["enable_search"] = True

        try:
            # Grounded answers (structured sections + evidence map) can take well over
            # 45 s on slower models; allow 120 s for the response, 10 s to connect.
            from backend.pm.answer_timing import current as answer_timing
            stats=answer_timing.get()
            call_started=time.perf_counter()
            if stats is not None:
                stats['model_calls']+=1
                stats['prompt_bytes']=max(stats['prompt_bytes'],len(json.dumps(messages,ensure_ascii=False).encode('utf-8')))
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                resp = client.post(url, headers=headers, json=body)
                if resp.status_code == 400 and body.get("enable_search"):
                    # Retry without enable_search if provider rejects unknown parameter
                    body.pop("enable_search", None)
                    resp = client.post(url, headers=headers, json=body)

                if stats is not None:
                    stats['model_seconds']=round(stats['model_seconds']+time.perf_counter()-call_started,4)

                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage", {})
                    tokens = usage.get("total_tokens", 0)
                    settings.cumulative_calls += 1
                    settings.cumulative_tokens += tokens
                    settings.save()

                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")

                    # Extract search info if provider returned any web search results
                    raw_search = (
                        choice.get("message", {}).get("search_info")
                        or data.get("search_info")
                        or data.get("output", {}).get("search_info")
                    )
                    search_used = bool(raw_search and raw_search.get("search_results"))
                    search_results = raw_search.get("search_results", []) if raw_search else []

                    search_info = {
                        "used": search_used,
                        "supported": search_supported,
                        "sources": search_results,
                        "note": "以下内容来自网络搜索，非工作台依据" if search_used else "",
                    }
                    usage["search_info"] = search_info
                    return ChatCompletionResult(content, usage, None, search_info)
                elif resp.status_code in (401, 403):
                    return ChatCompletionResult(None, {}, f"API_KEY_INVALID: HTTP {resp.status_code}", {"used": False})
                elif resp.status_code == 429:
                    return ChatCompletionResult(None, {}, "RATE_LIMIT_EXCEEDED", {"used": False})
                else:
                    return ChatCompletionResult(None, {}, f"HTTP_{resp.status_code}: {resp.text[:100]}", {"used": False})
        except httpx.TimeoutException:
            return ChatCompletionResult(None, {}, "TIMEOUT", {"used": False})
        except Exception as e:
            return ChatCompletionResult(None, {}, f"NETWORK_ERROR: {str(e)[:80]}", {"used": False})



def test_provider_connection(base_url: str, api_key: str, model: str) -> Dict[str, Any]:
    """Test actual connectivity with a minimal probe request and classify errors into human Chinese.

    Requirement 4.3: Classifies into 5 clear categories:
    1. Key invalid / unpaid
    2. Base URL invalid / unreachable
    3. Model name does not exist
    4. Network / proxy issue
    5. Timeout / rate limited
    """
    clean_url = base_url.rstrip("/")
    probe_url = f"{clean_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }

    start_time = time.time()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(probe_url, headers=headers, json=body)
            latency_ms = int((time.time() - start_time) * 1000)

            if resp.status_code == 200:
                return {
                    "ok": True,
                    "success": True,
                    "category": "SUCCESS",
                    "error_code": "SUCCESS",
                    "latency_ms": latency_ms,
                    "model_used": model,
                    "message": f"连接成功！模型 [{model}] 响应正常，延迟: {latency_ms} 毫秒。",
                    "error_message": "",
                }
            elif resp.status_code in (401, 403):
                return {
                    "ok": False,
                    "success": False,
                    "category": "AUTH_ERROR",
                    "error_code": "AUTH_ERROR",
                    "status_code": resp.status_code,
                    "model_used": model,
                    "message": "API Key 填写有误，或该密钥对应的账户已欠费、被封禁或无权限。请登录模型官网核对密钥。",
                    "error_message": "API Key 填写有误，或该密钥对应的账户已欠费、被封禁或无权限。请登录模型官网核对密钥。",
                }
            elif resp.status_code == 404:
                return {
                    "ok": False,
                    "success": False,
                    "category": "URL_ERROR",
                    "error_code": "URL_ERROR",
                    "status_code": resp.status_code,
                    "model_used": model,
                    "message": "Base URL 地址无效（服务端返回 404）。请检查地址拼写，并确认是否漏掉了路径后缀（例如缺少 /v1）。",
                    "error_message": "Base URL 地址无效（服务端返回 404）。请检查地址拼写，并确认是否漏掉了路径后缀（例如缺少 /v1）。",
                }
            elif resp.status_code == 400:
                err_text = resp.text.lower()
                if "model" in err_text or "not found" in err_text:
                    return {
                        "ok": False,
                        "success": False,
                        "category": "MODEL_ERROR",
                        "error_code": "MODEL_ERROR",
                        "status_code": resp.status_code,
                        "model_used": model,
                        "message": f"模型名称 [{model}] 在当前提供商不存在或不可用，请核对模型名称是否拼写准确。",
                        "error_message": f"模型名称 [{model}] 在当前提供商不存在或不可用，请核对模型名称是否拼写准确。",
                    }
                return {
                    "ok": False,
                    "success": False,
                    "category": "REQUEST_ERROR",
                    "error_code": "REQUEST_ERROR",
                    "status_code": resp.status_code,
                    "model_used": model,
                    "message": f"请求参数被拒绝 (HTTP 400): {resp.text[:100]}",
                    "error_message": f"请求参数被拒绝 (HTTP 400): {resp.text[:100]}",
                }
            elif resp.status_code == 429:
                return {
                    "ok": False,
                    "success": False,
                    "category": "RATE_LIMIT",
                    "error_code": "RATE_LIMIT",
                    "status_code": resp.status_code,
                    "model_used": model,
                    "message": "接口请求过于频繁或触发了供应商的并发/速率限流 (HTTP 429)，请稍后重试或检查账户配额。",
                    "error_message": "接口请求过于频繁或触发了供应商的并发/速率限流 (HTTP 429)，请稍后重试或检查账户配额。",
                }
            else:
                return {
                    "ok": False,
                    "success": False,
                    "category": "SERVER_ERROR",
                    "error_code": "SERVER_ERROR",
                    "status_code": resp.status_code,
                    "model_used": model,
                    "message": f"模型服务返回错误状态码 HTTP {resp.status_code}：{resp.text[:120]}",
                    "error_message": f"模型服务返回错误状态码 HTTP {resp.status_code}：{resp.text[:120]}",
                }
    except httpx.ConnectError:
        return {
            "ok": False,
            "success": False,
            "category": "NETWORK_ERROR",
            "error_code": "NETWORK_ERROR",
            "model_used": model,
            "message": "网络连接失败，无法连接到指定的服务器地址。请检查本地网络、代理设置或 Base URL 是否可以解析。",
            "error_message": "网络连接失败，无法连接到指定的服务器地址。请检查本地网络、代理设置或 Base URL 是否可以解析。",
        }
    except httpx.TimeoutException:
        return {
            "ok": False,
            "success": False,
            "category": "TIMEOUT",
            "error_code": "TIMEOUT",
            "model_used": model,
            "message": "请求响应超时（超过 15 秒）。模型服务端响应过慢或网络丢包，请检查网络后重试。",
            "error_message": "请求响应超时（超过 15 秒）。模型服务端响应过慢或网络丢包，请检查网络后重试。",
        }
    except Exception as exc:
        return {
            "ok": False,
            "success": False,
            "category": "UNKNOWN_ERROR",
            "error_code": "UNKNOWN_ERROR",
            "model_used": model,
            "message": f"网络探测异常: {str(exc)}",
            "error_message": f"网络探测异常: {str(exc)}",
        }


def get_active_provider() -> BaseProvider:
    """Return the active provider based on current settings.

    Requirement 4.1: Seamlessly switches between presets while using the universal client.
    """
    creds = settings.get_active_credentials()
    if creds.get("api_key") and creds.get("base_url"):
        return UniversalOpenAIProvider(
            base_url=creds["base_url"],
            api_key=creds["api_key"],
            model=creds.get("model") or "gpt-4o",
        )
    return RuleBasedFallbackProvider()

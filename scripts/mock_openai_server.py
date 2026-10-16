"""Mock OpenAI Server for Offline Test Pipeline (TASK-G-03 compliant).

Enforces requirements:
1. Answers only based on request content (never reads test cases or case numbers).
2. Analysis responses adhere strictly to backend/brain/analysis/engine.py JSON contract
   and contain:
   - Todo candidates with title
   - Knowledge candidates with title and content
   - A flat project update candidate: {"project_name": "<name>", "new_summary": "..."}
   - A project update candidate with empty proposed_change ({}) whose statement includes the project name.
   - Also includes a "candidates" array for UniversalOpenAIProvider.propose.
3. Assistant Q&A responses cite evidence provided in the request context.
4. Ping connectivity probe responds with "pong".
5. Uses ThreadingHTTPServer with Connection: close to guarantee concurrency and zero socket hangs.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List

# Ensure UTF-8 output
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mock_openai_server")


def extract_project_name(text: str) -> str:
    """Extracts candidate project name from prompt text or context."""
    if "冲刺项目" in text:
        return "冲刺项目"

    # Check for existing projects list: e.g. "现有活跃项目...: ['冲刺项目', ...]"
    m_list = re.search(r"现有活跃项目[^\n]*?:\s*\[['\"]([^'\"]+)['\"]", text)
    if m_list:
        return m_list.group(1).strip()
    
    # Check for project quotes in text
    m_quote = re.search(r"【([^】]+)】", text)
    if m_quote:
        return m_quote.group(1).strip()
        
    m_proj = re.search(r"项目[:：\s]+([^\s,，。；\n]+)", text)
    if m_proj:
        return m_proj.group(1).strip()

    return "冲刺项目"


def extract_doc_name(text: str) -> str:
    """Extracts document name from prompt text or context."""
    m_doc = re.search(r"文档名称[:：\s]+([^\s\n]+)", text)
    if m_doc:
        return m_doc.group(1).strip()
    for ext in [".docx", ".xlsx", ".pptx", ".pdf"]:
        m_ext = re.search(rf"([a-zA-Z0-9_\-\u4e00-\u9fa5]+{ext})", text)
        if m_ext:
            return m_ext.group(1).strip()
    return "sample_doc.docx"


def generate_structured_analysis(all_text: str) -> Dict[str, Any]:
    """Generates strictly structured analysis response required by engine.py and adapter.py."""
    proj_name = extract_project_name(all_text)
    doc_name = extract_doc_name(all_text)

    # 1. Todo Candidate
    todo_item = {
        "statement": "完成系统流水线交付与文档归档",
        "confidence": "HIGH",
        "inference_level": "EXPLICIT",
        "recommended_action": "DRAFT_ONLY",
        "proposed_change": {
            "title": "完成系统流水线交付与文档归档",
            "description": "按期完成测试与报告交付并归档全部凭据",
            "priority": "high",
        },
    }

    # 2. Knowledge Candidate
    knowledge_item = {
        "statement": "提炼安全运行规范：端侧沙箱安全隔离规范",
        "confidence": "HIGH",
        "inference_level": "EXPLICIT",
        "recommended_action": "DRAFT_ONLY",
        "proposed_change": {
            "title": "端侧沙箱安全隔离规范",
            "content": "所有模型调用需通过本地桥接服务，严格屏蔽外网直连与凭据落盘。",
            "category": "安全准则",
        },
    }

    # 3. Flat Project Update: {project_name, new_summary}
    flat_proj_update = {
        "statement": f"项目【{proj_name}】本周已完成核心模块解耦与云端测试验证，推进至执行阶段。",
        "confidence": "HIGH",
        "inference_level": "EXPLICIT",
        "recommended_action": "DRAFT_ONLY",
        "proposed_change": {
            "project_name": proj_name,
            "new_summary": "本周推进核心模块执行与云端测试验证",
        },
    }

    # 4. Project Update with empty proposed_change ({}) but project name in statement
    empty_change_proj_update = {
        "statement": f"针对【{proj_name}】补充架构治理陈述与技术演进说明。",
        "confidence": "HIGH",
        "inference_level": "EXPLICIT",
        "recommended_action": "DRAFT_ONLY",
        "proposed_change": {},
    }

    # Build engine.py contract envelope
    envelope = {
        "facts": [
            {
                "statement": f"从文档 {doc_name} 中提取项目核心事实与技术要求",
                "confidence": "HIGH",
                "inference_level": "EXPLICIT",
            }
        ],
        "events": [],
        "decisions": [],
        "plans": [],
        "ideas": [],
        "questions": [],
        "completed_actions": [],
        "pending_actions": [],
        "status_changes": [],
        "people": [],
        "dates": [],
        "projects": [],
        "todo_candidates": [todo_item],
        "project_update_candidates": [flat_proj_update, empty_change_proj_update],
        "knowledge_candidates": [knowledge_item],
        "conflicts": [],
        "ambiguities": [],
        # Also include "candidates" list for UniversalOpenAIProvider.propose
        "candidates": [
            {
                "target_type": "todo",
                "operation": "create",
                "candidate_type": "todo",
                "payload": todo_item["proposed_change"],
                "raw_excerpt": f"从文档 {doc_name} 识别待办项：完成系统流水线交付与文档归档。",
                "evidence_locators": ["text_blocks[0]"],
                "confidence": "HIGH",
                "summary": "待办：完成系统流水线交付与文档归档",
            },
            {
                "target_type": "knowledge",
                "operation": "create",
                "candidate_type": "knowledge",
                "payload": knowledge_item["proposed_change"],
                "raw_excerpt": "规范要求：所有模型通信必须在隔离沙箱中执行，杜绝数据泄露。",
                "evidence_locators": ["text_blocks[1]"],
                "confidence": "HIGH",
                "summary": "提炼安全规范：端侧沙箱安全隔离规范",
            },
            {
                "target_type": "project",
                "operation": "update",
                "candidate_type": "project_update",
                "payload": {
                    "project_name": proj_name,
                    "new_summary": "本周推进核心模块执行与云端测试验证",
                },
                "raw_excerpt": flat_proj_update["statement"],
                "evidence_locators": ["text_blocks[2]"],
                "confidence": "HIGH",
                "summary": "更新项目进展：状态推进至执行阶段",
            },
            {
                "target_type": "project",
                "operation": "update",
                "candidate_type": "project_update",
                "payload": empty_change_proj_update["proposed_change"],
                "raw_excerpt": empty_change_proj_update["statement"],
                "evidence_locators": ["text_blocks[3]"],
                "confidence": "HIGH",
                "summary": "补充项目演进说明",
            },
        ],
    }
    return envelope


_request_count=0
_advice_fault_sentences=0
_count_lock=threading.Lock()


class MockOpenAIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"[mock_server] {self.address_string()} - {format % args}")

    def _send_json(self, status_code: int, data: Dict[str, Any]):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        if self.path=='/stats':
            with _count_lock:count=_request_count;faults=_advice_fault_sentences
            self._send_json(200,{'chat_requests':count,'advice_fault_sentences':faults})
        elif self.path in ("/health", "/"):
            self._send_json(200, {"status": "ok", "service": "mock_openai_server"})
        elif self.path.startswith("/v1/models"):
            self._send_json(200, {
                "object": "list",
                "data": [
                    {"id": "gpt-4o", "object": "model", "owned_by": "mock"},
                    {"id": "deepseek-chat", "object": "model", "owned_by": "mock"},
                    {"id": "qwen-max", "object": "model", "owned_by": "mock"}
                ]
            })
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        global _request_count,_advice_fault_sentences
        if self.path=='/stats/reset':
            with _count_lock:_request_count=0;_advice_fault_sentences=0
            self._send_json(200,{'chat_requests':0})
            return
        if not self.path.startswith("/v1/chat/completions"):
            self._send_json(404, {"error": "Not Found"})
            return

        content_len = int(self.headers.get("Content-Length", 0))
        with _count_lock:_request_count+=1
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")

        try:
            req_data = json.loads(raw_body)
        except Exception:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        messages = req_data.get("messages", [])
        user_prompt = ""
        system_prompt = ""
        for m in messages:
            if m.get("role") == "user":
                user_prompt += str(m.get("content", ""))
            elif m.get("role") == "system":
                system_prompt += str(m.get("content", ""))

        all_text = f"{system_prompt}\n{user_prompt}"

        # 1. Connectivity probe (ping)
        if user_prompt.strip().lower() == "ping":
            reply_content = "pong"

        elif '语义任务契约：project_advice' in system_prompt:
            if req_data.get('model')=='fault-schema:project_advice':
                reply_content=json.dumps({'invalid_marker':'FAULT_SCHEMA_OUTPUT'})
            else:
                try:
                    from scripts.mock_grounded_answer import project_advice
                except ModuleNotFoundError:
                    from mock_grounded_answer import project_advice
                reply_content=json.dumps(project_advice(json.loads(user_prompt)),ensure_ascii=False)

        elif '语义任务契约：section_consistency' in system_prompt:
            try:
                from scripts.mock_grounded_answer import section_consistency
            except ModuleNotFoundError:
                from mock_grounded_answer import section_consistency
            reply_content=json.dumps(section_consistency(json.loads(user_prompt)),ensure_ascii=False)

        elif '语义任务契约：section_update' in system_prompt:
            try:
                from scripts.mock_grounded_answer import section_updates
            except ModuleNotFoundError:
                from mock_grounded_answer import section_updates
            reply_content=json.dumps(section_updates(json.loads(user_prompt)),ensure_ascii=False)

        # 2. Project Explainer 30-Second Summary (Level-1 Explainer)
        elif any(k in all_text for k in ["30秒说明", "一句话读懂", "提炼30秒说明", "key_open_issue"]):
            proj_name = extract_project_name(all_text)
            reply_content = json.dumps({
                "one_sentence": f"30秒读懂：{proj_name}核心业务与技术架构推进",
                "current_state": "当前所处阶段：处于落地执行期，核心模块已打通",
                "key_open_issue": "按计划推进中，暂无阻碍项",
                "next_step": "推进当前阶段核心交付物与测试验证",
            }, ensure_ascii=False)

        # 3. Structured Analysis (Document ingest / Engine analysis / Planner proposals)
        elif any(k in all_text for k in ["请分析以下内容", "todo_candidates", "阅读高保真文档证据", "认知治理专家", "现有活跃项目"]):
            analysis_dict = generate_structured_analysis(all_text)
            reply_content = json.dumps(analysis_dict, ensure_ascii=False)

        # 4. Grounded Q&A (Assistant)
        else:
            try:
                from scripts.mock_grounded_answer import answer
            except ModuleNotFoundError:
                from mock_grounded_answer import answer
            reply_content=answer(all_text)
            if req_data.get('model')=='fault-advice-entities' and '<<<EVIDENCE_MAP' in reply_content:
                try:
                    from scripts.mock_grounded_answer import inject_unbacked_advice
                except ModuleNotFoundError:
                    from mock_grounded_answer import inject_unbacked_advice
                reply_content=inject_unbacked_advice(reply_content)
                with _count_lock:_advice_fault_sentences+=2

        resp = {
            "id": f"chatcmpl-mock-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req_data.get("model", "gpt-4o"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": reply_content,
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": len(all_text) // 2,
                "completion_tokens": len(reply_content) // 2,
                "total_tokens": (len(all_text) + len(reply_content)) // 2,
            }
        }
        self._send_json(200, resp)


SERVER_HOST = "127.0.0.1"
SERVER_PORT = 18000


class MockOpenAIServer:
    def __init__(self, port: int = SERVER_PORT, host: str = SERVER_HOST):
        self.host = host
        self.port = port
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        self.server = ThreadingHTTPServer((self.host, self.port), MockOpenAIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.3)

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def run_server(port: int = 18000):
    server_address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(server_address, MockOpenAIHandler)
    logger.info(f"Mock OpenAI Server running on http://127.0.0.1:{port}/v1")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock OpenAI Compatible Server")
    parser.add_argument("--port", type=int, default=18000, help="Port to listen on (default: 18000)")
    args = parser.parse_args()
    run_server(args.port)

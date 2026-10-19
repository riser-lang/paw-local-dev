"""Phase 2 scan model-call telemetry (R-263).

The scan path must record exactly what the real model did per request without
storing prompt text or credentials. These cases use a local HTTP stub so they
run offline and never touch the provider.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.provider import adapter, model_calls


def _candidate_payload() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        [
                            {
                                "target_type": "knowledge",
                                "operation": "create",
                                "payload": {"title": "示例知识", "content": "本地样例正文"},
                                "raw_excerpt": "本地样例正文",
                                "evidence_locators": ["text_blocks[0]"],
                                "confidence": "HIGH",
                            }
                        ],
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "total_tokens": 33,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }


def _chat_payload(text: str = "ok") -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass

    def do_POST(self):
        state = self.server.state
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        state["requests"].append(body)
        index = len(state["requests"]) - 1
        delay = state["delay"]
        if callable(delay):
            delay = delay(index, body)
        if delay:
            time.sleep(delay)
        try:
            status, payload = state["respond"](index, body)
        except Exception as exc:  # pragma: no cover - stub bug surface
            status, payload = 500, {"error": type(exc).__name__}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            pass


@pytest.fixture
def stub():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    httpd.state = {
        "requests": [],
        "delay": 0.0,
        "respond": lambda index, body: (200, _candidate_payload()),
    }
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def provider(stub):
    port = stub.server_address[1]
    return adapter.UniversalOpenAIProvider(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key="unit-test-key",
        model="unit-test-model",
    )


@pytest.fixture(autouse=True)
def _clean_telemetry():
    model_calls.reset()
    yield
    model_calls.reset()


def _evidence() -> dict:
    return {
        "display_name": "样例文档.docx",
        "text_blocks": [{"index": 0, "text": "本地样例正文"}],
        "raw_artifact_sha256": "hash",
    }


def test_propose_records_one_row_per_call_with_usage(provider):
    with model_calls.begin_file("样例文档.docx"):
        items = provider.propose(_evidence(), [], [])

    assert items and items[0]["source_generation"] == "ai"
    rows = model_calls.snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row["file"] == "样例文档.docx"
    assert row["source"] == "propose"
    assert row["attempt"] == 1
    assert row["status"] == 200
    assert row["timed_out"] is False
    assert row["fallback_rule"] is False
    assert row["prompt_chars"] > 0
    assert (row["prompt_tokens"], row["completion_tokens"], row["reasoning_tokens"]) == (11, 22, 5)
    # Telemetry must never carry prompt text or credentials.
    dumped = json.dumps(rows, ensure_ascii=False)
    assert "本地样例正文" not in dumped
    assert "unit-test-key" not in dumped


def test_chat_completion_row_carries_the_semantic_task_name(provider):
    stub_reply = _chat_payload()
    provider.base_url  # provider is bound to the stub
    with model_calls.begin_file("样例文档.docx"):
        with model_calls.begin_task("section_update"):
            text, usage, error = provider.chat_completion(
                messages=[{"role": "user", "content": "只回复 ok"}], temperature=0.1
            )
    assert error is None
    rows = model_calls.snapshot()
    assert rows and rows[-1]["source"] == "section_update"
    assert rows[-1]["status"] == 200


def test_nothing_is_recorded_outside_a_scan_file_context(provider):
    provider.propose(_evidence(), [], [])
    provider.chat_completion(messages=[{"role": "user", "content": "hi"}], temperature=0.1)
    assert model_calls.snapshot() == []
    assert model_calls.summary()["calls"] == 0

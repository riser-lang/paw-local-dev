"""R-264: chat answers run with chain-of-thought off and bounded output."""
from __future__ import annotations

import json

import pytest

from backend.provider import adapter, model_calls
from tests.unit.test_scan_model_calls import (  # reuse the local HTTP stub
    _candidate_payload,
    _chat_payload,
    _StubHandler,
    stub,
    provider,
)


@pytest.fixture(autouse=True)
def _clean_telemetry():
    model_calls.reset()
    yield
    model_calls.reset()


def test_answer_call_disables_thinking_and_bounds_output(provider, stub):
    stub.state["respond"] = lambda index, body: (200, _chat_payload("回答正文"))
    with model_calls.begin_answer("session-1"):
        text, usage, error = provider.chat_completion(
            messages=[{"role": "user", "content": "青禾试点计划目标是多少？"}], temperature=0.2
        )
    assert error is None and text == "回答正文"
    sent = stub.state["requests"][0]
    assert sent["enable_thinking"] is False
    assert sent["max_tokens"] == adapter.ANSWER_MAX_TOKENS == 3000
    row = model_calls.rows_since(0)[0]
    assert row["channel"] == "chat"
    assert row["answer"] == "session-1"
    assert row["thinking_disabled"] is True
    assert row["status"] == 200
    assert (row["prompt_tokens"], row["completion_tokens"]) == (7, 3)


def test_gate_retry_is_reported_as_the_second_attempt(provider, stub):
    stub.state["respond"] = lambda index, body: (200, _chat_payload())
    mark = model_calls.count()
    with model_calls.begin_answer("session-2"):
        with model_calls.begin_call("first", 0):
            provider.chat_completion(messages=[{"role": "user", "content": "第一次"}], temperature=0.2)
        with model_calls.begin_call("gate_retry", 1):
            provider.chat_completion(messages=[{"role": "user", "content": "重试"}], temperature=0.1)
    rows = model_calls.rows_since(mark)
    assert [(row["stage"], row["attempt"]) for row in rows] == [("first", 1), ("gate_retry", 2)]


def test_answer_call_falls_back_when_thinking_parameter_is_rejected(provider, stub):
    def respond(index, body):
        if "enable_thinking" in body:
            return 400, {"error": "unknown parameter enable_thinking"}
        return 200, _chat_payload("回退成功")

    stub.state["respond"] = respond
    with model_calls.begin_answer("session-3"):
        text, usage, error = provider.chat_completion(
            messages=[{"role": "user", "content": "问题"}], temperature=0.2
        )
    assert error is None and text == "回退成功"
    assert len(stub.state["requests"]) == 2
    assert "enable_thinking" not in stub.state["requests"][1]
    rows = model_calls.rows_since(0)
    assert rows[0].get("thinking_param_fallback") is True
    assert rows[-1]["thinking_disabled"] is False


def test_internal_calls_are_bounded_and_keep_no_answer_rows(provider, stub):
    """R-271: background work is bounded, and it is not recorded as an answer."""
    stub.state["respond"] = lambda index, body: (200, _chat_payload())
    provider.chat_completion(messages=[{"role": "user", "content": "巡航建议"}], temperature=0.1)
    sent = stub.state["requests"][0]
    assert sent["enable_thinking"] is False
    assert sent["max_tokens"] == adapter.INTERNAL_MAX_TOKENS
    assert model_calls.snapshot("chat") == []

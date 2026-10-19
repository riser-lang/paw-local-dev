"""Measure SiliconFlow model endpoint connectivity without logging credentials."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = "https://api.siliconflow.cn/v1"
MODEL_ID = "deepseek-ai/DeepSeek-V4.1-Flash"
TIMEOUT_SECONDS = 120


def _redact(value: Any, api_key: str) -> str:
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return text[:1000]


def _error_details(body: bytes, api_key: str) -> dict[str, str | None]:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (UnicodeError, json.JSONDecodeError):
        return {"code": None, "message": "HTTP request failed"}
    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return {"code": None, "message": "HTTP request failed"}
    code = error.get("code")
    message = error.get("message")
    return {
        "code": _redact(code, api_key) if code is not None else None,
        "message": _redact(message, api_key) if message is not None else None,
    }


def _usage(payload: dict[str, Any]) -> dict[str, int | None]:
    usage = payload.get("usage") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens")
        or completion_details.get("reasoning_tokens"),
    }


def _request(
    label: str,
    path: str,
    api_key: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    check_model: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "request": label,
        "status_code": None,
        "elapsed_seconds": None,
        "timed_out": False,
    }
    request_body = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    headers = {"Authorization": f"Bearer {api_key}"}
    if request_body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE_URL + path,
        data=request_body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            result["status_code"] = response.status
            response_body = response.read()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if not 200 <= result["status_code"] < 300:
            result["error"] = _error_details(response_body, api_key)
            return result

        response_payload = json.loads(response_body.decode("utf-8"))
        if check_model:
            model_ids = {
                item.get("id")
                for item in response_payload.get("data", [])
                if isinstance(item, dict)
            }
            result["model_present"] = MODEL_ID in model_ids
        else:
            choices = response_payload.get("choices", [])
            answer: Any = ""
            if choices:
                answer = choices[0].get("message", {}).get("content", "")
            if not isinstance(answer, str):
                answer = json.dumps(answer, ensure_ascii=False)
            result["answer_first_100"] = _redact(answer, api_key)[:100]
            result["usage"] = _usage(response_payload)
    except urllib.error.HTTPError as exc:
        result["status_code"] = exc.code
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        try:
            result["error"] = _error_details(exc.read(), api_key)
        except OSError:
            result["error"] = {"code": None, "message": "HTTP request failed"}
    except (TimeoutError, socket.timeout) as exc:
        result["timed_out"] = True
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["error"] = {
            "code": type(exc).__name__,
            "message": _redact(exc, api_key),
        }
    except Exception as exc:  # Report only safe error type and message fields.
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["error"] = {
            "code": type(exc).__name__,
            "message": _redact(exc, api_key),
        }
    return result


def _fictional_chinese_sample() -> str:
    places = ("雾杉湾", "白石岭", "青禾港", "月汀村", "松风台", "栖云岛")
    activities = ("潮汐观测", "种子交换", "灯塔维护", "步道巡查", "雨量记录", "旧桥修缮")
    details = (
        "记录员把当天的变化写在蓝色册页里，并注明观察时间。",
        "几位居民先核对工具，再沿着石阶慢慢检查现场。",
        "临时木牌只用于说明方向，正式安排仍由小组讨论决定。",
        "午后的海风带来薄雾，队伍因此把休息点移到屋檐下。",
        "收工前大家清点物资，把可重复使用的物品放回储物柜。",
        "这份故事属于虚构的岛屿与人物，不描述任何真实项目。",
    )
    paragraphs = []
    for day in range(1, 25):
        place = places[(day - 1) % len(places)]
        activity = activities[(day - 1) % len(activities)]
        narrative = "".join(
            details[(day + offset) % len(details)] for offset in range(4)
        )
        paragraphs.append(f"第{day}天，{place}的小组安排了{activity}。{narrative}")
    return "\n".join(paragraphs)[:2000]


def run(report_path: Path) -> int:
    api_key = os.environ.get("PAW_TEST_API_KEY", "")
    report: dict[str, Any] = {
        "provider": "SiliconFlow",
        "model": MODEL_ID,
        "timeout_seconds": TIMEOUT_SECONDS,
        "requests": [],
    }
    if not api_key:
        report["setup_error"] = {
            "code": "MissingSecret",
            "message": "PAW_TEST_API_KEY secret is unavailable.",
        }
        failed = True
    else:
        report["requests"].append(
            _request("GET /models", "/models", api_key, check_model=True)
        )
        short_prompt = "只回复：连通正常"
        report["requests"].append(
            _request(
                "POST /chat/completions (default parameters)",
                "/chat/completions",
                api_key,
                method="POST",
                payload={
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": short_prompt}],
                    "max_tokens": 50,
                },
            )
        )
        report["requests"].append(
            _request(
                "POST /chat/completions (enable_thinking=false)",
                "/chat/completions",
                api_key,
                method="POST",
                payload={
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": short_prompt}],
                    "max_tokens": 50,
                    "enable_thinking": False,
                },
            )
        )
        long_prompt = (
            "以下是约 2000 字的虚构中文样例。请只依据样例，输出一个 JSON 数组，"
            "包含 2 个对象，每个对象有 place 和 activity 字段，不要附加解释。\n\n"
            + _fictional_chinese_sample()
        )
        report["requests"].append(
            _request(
                "POST /chat/completions (long fictional Chinese sample)",
                "/chat/completions",
                api_key,
                method="POST",
                payload={
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": long_prompt}],
                    "max_tokens": 1500,
                    "enable_thinking": False,
                },
            )
        )
        failed = any(
            request.get("error")
            or not 200 <= (request.get("status_code") or 0) < 300
            or request.get("model_present") is False
            for request in report["requests"]
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("reports/model-ping-report.json"))
    args = parser.parse_args()
    return run(args.report)


if __name__ == "__main__":
    raise SystemExit(main())

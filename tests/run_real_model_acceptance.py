"""Manual/tag-only real-provider acceptance against an installed PAW build."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import httpx
from docx import Document
from pptx import Presentation

from tests.run_e2e_tests import INSTALL_DIR, silent_install, start_paw, stop_paw

# Keep the scan monitor's output to its explicit, redacted 10-second snapshot.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE = "http://127.0.0.1:8800"
DETAIL_SENTENCE = "本地扫描正文样本：青禾试点已完成首轮访谈，下一步整理访谈纪要。"


def create_samples(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph("青禾试点计划 v1")
    doc.add_paragraph("目标：第一阶段培养 12 名导师。")
    doc.save(directory / "青禾试点计划_v1.docx")
    doc = Document()
    doc.add_paragraph("青禾试点计划 v2")
    doc.add_paragraph("生效日期：2026年10月18日")
    doc.add_paragraph("目标调整：第一阶段培养 18 名导师。本版替代 v1。")
    doc.save(directory / "青禾试点计划_v2.docx")
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "青禾试点周报"
    slide.placeholders[1].text = "进度：已招募 9 名导师\n风险：两个校区尚未提名导师人选\n下一步：完成剩余 9 名导师招募"
    deck.save(directory / "青禾试点周报.pptx")
    (directory / "青禾试点详情.md").write_text(DETAIL_SENTENCE, encoding="utf-8")


def _safe_progress_text(value: object, key: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    return text[:240]


def _progress_line(progress: dict, key: str) -> str:
    status = _safe_progress_text(progress.get("status") or "unknown", key)
    phase = progress.get("current_phase") or status
    if phase == "phase2_analyzing" or status == "phase2_analyzing":
        completed = progress.get("phase2_processed", 0)
    else:
        completed = progress.get("phase1_processed", 0)
    total = progress.get("total_files", 0)
    current_file = _safe_progress_text(progress.get("current_file") or "-", key)
    failures = progress.get("failed_items") or []
    if failures:
        last_failure = failures[-1] if isinstance(failures[-1], dict) else {}
        error_type = _safe_progress_text(last_failure.get("error_type") or "error", key)
        error_message = _safe_progress_text(last_failure.get("error_message") or "", key)
        recent_error = error_type + (f": {error_message}" if error_message else "")
    else:
        recent_error = "none"
    return (f"Scan progress: status={status} completed={completed}/{total} "
            f"current_file={current_file} recent_error={recent_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--mock-smoke", action="store_true",
                        help="run the complete installed-app flow against the freeform mock server")
    args = parser.parse_args()
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "model": os.environ.get("PAW_TEST_MODEL") or "",
        "provider": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "result": "NOT_RUN",
        "issues": [],
        "questions": [],
        "last_scan_progress": "",
        "scan_file_timings": [],
    }
    exam = json.loads(Path("tests/pending/exam/questions.json").read_text(encoding="utf-8"))
    exam_questions = {item["id"]: item for item in exam["questions"]}
    prompts = [
        ("PM-U1-03", "青禾试点计划目标是多少？"),
        ("PM-U4-01", "青禾试点后续最好如何优化？"),
        ("PM-S1-03", "青禾试点详情.md 里写了什么？"),
        *((ident, exam_questions[ident]["q"]) for ident in ("X01", "X02", "X18", "X19", "X20")),
    ]
    report["questions"] = [
        {"id": ident, "question": question, "answer": "", "sources": [], "status": "未能运行"}
        for ident, question in prompts
    ]
    key = "mock" if args.mock_smoke else os.environ.get("PAW_TEST_API_KEY")
    if not key:
        report["issues"].append({"state": "未能运行", "reason": "PAW_TEST_API_KEY secret is unavailable"})
    else:
        installer = Path(args.installer)
        proc = None
        try:
            installed, _, code = silent_install(installer)
            if not installed:
                raise RuntimeError(f"安装程序退出码为 {code}")
            proc = start_paw()
            if proc is None:
                raise RuntimeError("已安装的 PAW 未能启动")
            time.sleep(3)
            sample_dir = Path(os.environ.get("TEMP", str(Path.cwd()))) / "PAW_C12_real_model_samples"
            shutil.rmtree(sample_dir, ignore_errors=True)
            create_samples(sample_dir)
            settings = {
                "provider": "custom", "custom_api_key": key,
                "custom_base_url": ("http://127.0.0.1:18000/v1" if args.mock_smoke
                                    else "https://api.siliconflow.cn/v1"),
                "custom_model": ("freeform-mock" if args.mock_smoke else report["model"]),
                "work_dir": str(sample_dir),
            }
            response = httpx.post(BASE + "/v1/local/settings", json=settings, timeout=30)
            response.raise_for_status()
            response = httpx.post(BASE + "/v1/local/scanner/start",
                                  json={"directory": str(sample_dir), "force_restart": True}, timeout=30)
            response.raise_for_status()
            deadline = time.monotonic() + 240
            next_log = 0.0
            current_segment = None
            segment_started = None
            segment_times = []
            terminal_states = {"completed", "failed", "stopped", "daily_limit_reached"}
            while time.monotonic() < deadline:
                progress = httpx.get(BASE + "/v1/local/scanner/progress", timeout=5).json()
                now = time.monotonic()
                phase = str(progress.get("current_phase") or progress.get("status") or "unknown")
                current_file = str(progress.get("current_file") or "")
                segment = (phase, current_file) if current_file else None
                if segment != current_segment:
                    if current_segment and segment_started is not None:
                        segment_times.append({"phase": current_segment[0], "file": current_segment[1],
                                              "seconds": round(now - segment_started, 1)})
                    current_segment = segment
                    segment_started = now if segment else None
                active_timing = []
                if current_segment and segment_started is not None:
                    active_timing.append({"phase": current_segment[0], "file": current_segment[1],
                                          "seconds": round(now - segment_started, 1)})
                report["scan_file_timings"] = [*segment_times, *active_timing]

                line = _progress_line(progress, key or "")
                report["last_scan_progress"] = line
                is_terminal = progress.get("status") in terminal_states
                if now >= next_log or is_terminal:
                    print(line, flush=True)
                    next_log = now + 10
                if is_terminal:
                    if progress.get("status") != "completed":
                        raise RuntimeError(f"样例文件扫描以状态 {progress.get('status')} 结束")
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError("等待样例文件扫描超时")
            if current_segment and segment_started is not None:
                segment_times.append({"phase": current_segment[0], "file": current_segment[1],
                                      "seconds": round(time.monotonic() - segment_started, 1)})
            report["scan_file_timings"] = segment_times

            from tests.run_pm_tests import api
            from tests.run_e2e_tests import timed_answer
            from scripts.build_exam_corpus import evaluate_semantics
            for item in report["questions"]:
                ident, question = item["id"], item["question"]
                try:
                    session = api("POST", "/v1/web/agy/chat", {"mode": "chat"})["session_id"]
                    answer = timed_answer(session, question, "real-model-" + ident)
                    item["answer"] = str(answer.get("response") or "")
                    item["sources"] = answer.get("sources") or answer.get("evidence") or answer.get("source_refs") or []
                    text = item["answer"]
                    if ident == "PM-U1-03":
                        passed = "18" in text and "青禾试点计划_v2.docx" in json.dumps(item["sources"], ensure_ascii=False)
                    elif ident in {"PM-U4-01", "X19", "X20"}:
                        if ident in exam_questions:
                            passed, _ = evaluate_semantics(text, item["sources"], exam_questions[ident])
                        else:
                            passed = "建议" in text and bool(item["sources"])
                    elif ident == "PM-S1-03":
                        passed = DETAIL_SENTENCE in text and "青禾试点详情.md" in json.dumps(item["sources"], ensure_ascii=False)
                    else:
                        passed, _ = evaluate_semantics(text, item["sources"], exam_questions[ident])
                    item["status"] = "PASS" if passed else "FAIL"
                except Exception as exc:  # report only exception class; never serialize request details
                    item["error"] = type(exc).__name__
            report["result"] = "GREEN" if len(report["questions"]) == 8 and all(q["status"] == "PASS" for q in report["questions"]) else "RED"
        except Exception as exc:
            report["issues"].append({"state": "未能运行", "reason": type(exc).__name__,
                                      "last_scan_progress": report["last_scan_progress"]})
            report["result"] = "RED"
        finally:
            if proc is not None:
                stop_paw(proc)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"model": report["model"], "result": report["result"],
                      "question_count": len(report["questions"]),
                      "not_run_count": sum(q["status"] == "未能运行" for q in report["questions"])}))
    # The mock lane checks that installation, scanning, and all question calls
    # complete. Its answers still receive the unchanged verdicts above.
    return 0 if args.mock_smoke or report["result"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())

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

from tests.acceptance_verdicts import DETAIL_CORE_SENTENCE, core_sentence_in as _core_sentence_in, judge_answer
from tests.run_e2e_tests import INSTALL_DIR, silent_install, start_paw, stop_paw

# Keep the scan monitor's output to its explicit, redacted 10-second snapshot.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE = "http://127.0.0.1:8800"
# R-264: the reviewer's exam corpus is copied (read-only source) next to the four
# samples so the exam questions have evidence in the workspace.
EXAM_CORPUS_DIR = Path("tests/fixtures/exam")
EXAM_PROJECTS = ("星火导师计划", "人才盘点", "年度预算")
# PM-S1-03 accepts the core sentence with other punctuation and without the
# "本地扫描正文样本：" prefix, plus the source file name.
DETAIL_CORE_SENTENCE = "青禾试点已完成首轮访谈，下一步整理访谈纪要"
DETAIL_SENTENCE = "本地扫描正文样本：" + DETAIL_CORE_SENTENCE + "。"
# The scan wait stays 240 s. Only the answer read wait differs from the offline
# blocks: one real answer call already takes 9-15 s (R-263 measurement), so the
# questions need room to come back; questions and verdicts are unchanged.
ANSWER_TIMEOUT_SECONDS = 60.0


def create_samples(directory: Path) -> list:
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

    copied = []
    if EXAM_CORPUS_DIR.is_dir():
        for source in sorted(EXAM_CORPUS_DIR.iterdir()):
            if not source.is_file():
                continue
            target = directory / source.name
            # The fixture is the reviewer's read-only corpus: copy, never move or edit.
            shutil.copyfile(source, target)
            copied.append(source.name)
    return copied


def _create_exam_projects() -> list:
    """The corpus documents belong to these projects; create them like a user would."""
    from tests.run_pm_tests import api
    created = []
    for name in EXAM_PROJECTS:
        try:
            api("POST", "/v1/web/workspace/projects", {"name": name})
            created.append(name)
        except Exception:
            pass
    return created


def _approve_and_resolve() -> dict:
    """Approve pending candidates and resolve decided families, as a user would."""
    from tests.run_pm_tests import api
    approved = 0
    for _ in range(4):
        try:
            pending = api("GET", "/v1/web/brain/review/candidates?status=pending&limit=200").get("items", [])
        except Exception:
            pending = []
        accepted_this_round = 0
        for cand in pending:
            cid = cand.get("id") or cand.get("candidate_id")
            if not cid:
                continue
            try:
                api("POST", f"/v1/web/brain/review/candidates/{cid}/accept", {})
                approved += 1
                accepted_this_round += 1
            except Exception:
                pass
        try:
            decisions = api("GET", "/v1/pm/decisions").get("items", [])
        except Exception:
            decisions = []
        for dec in decisions:
            if dec.get("status") == "pending" and dec.get("project_id"):
                try:
                    api("POST", f"/v1/pm/decisions/{dec['id']}/resolve", {
                        "project_id": dec["project_id"],
                        "family_title": dec.get("family_key") or dec.get("title") or "",
                    })
                except Exception:
                    pass
        if not accepted_this_round and not pending:
            break
    return {"approved_candidates": approved}


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
    calls = progress.get("model_call_summary") or {}
    failures = progress.get("failed_items") or []
    if failures:
        last_failure = failures[-1] if isinstance(failures[-1], dict) else {}
        error_type = _safe_progress_text(last_failure.get("error_type") or "error", key)
        error_message = _safe_progress_text(last_failure.get("error_message") or "", key)
        recent_error = error_type + (f": {error_message}" if error_message else "")
    else:
        recent_error = "none"
    return (f"Scan progress: status={status} completed={completed}/{total} "
            f"current_file={current_file} recent_error={recent_error} "
            f"model_calls={calls.get('calls', 0)} timeouts={calls.get('timeouts', 0)} "
            f"rule_fallbacks={calls.get('fallback_rule', 0)}")


def _call_line(row: dict, key: str) -> str:
    """One redacted line per model request: no prompt text, no credentials."""
    return ("Model call: file={} source={} attempt={} seconds={} status={} timed_out={} "
            "prompt_chars={} tokens=p{}/c{}/r{} rule_fallback={} thinking_disabled={}").format(
        _safe_progress_text(row.get("file") or "-", key),
        _safe_progress_text(row.get("source") or "-", key),
        row.get("attempt", 0),
        row.get("seconds", 0),
        row.get("status"),
        row.get("timed_out"),
        row.get("prompt_chars", 0),
        row.get("prompt_tokens", 0),
        row.get("completion_tokens", 0),
        row.get("reasoning_tokens", 0),
        row.get("fallback_rule"),
        row.get("thinking_disabled"),
    )


def _emit_model_calls(report: dict, key: str) -> None:
    rows = report.get("model_calls") or []
    print(f"Phase 2 model calls: {len(rows)}", flush=True)
    for row in rows:
        print(_call_line(row, key), flush=True)
    phase1 = [t for t in report.get("scan_file_timings") or [] if t.get("phase") != "phase2_analyzing"]
    phase2 = [t for t in report.get("scan_file_timings") or [] if t.get("phase") == "phase2_analyzing"]
    report["phase1_file_timings"] = phase1
    report["phase2_file_timings"] = phase2
    report["phase2_seconds"] = round(sum(float(t.get("seconds") or 0) for t in phase2), 1)
    report["scan_seconds"] = round(sum(float(t.get("seconds") or 0) for t in phase1 + phase2), 1)
    for item in phase2:
        print(f"Phase 2 file: {_safe_progress_text(item.get('file') or '-', key)} "
              f"seconds={item.get('seconds')}", flush=True)
    summary = report.get("model_call_summary") or {}
    print(f"Phase 2 model call summary: calls={summary.get('calls', 0)} "
          f"timeouts={summary.get('timeouts', 0)} failed={summary.get('failed', 0)} "
          f"rule_fallbacks={summary.get('fallback_rule', 0)} "
          f"model_seconds={summary.get('seconds', 0)} "
          f"reasoning_tokens={summary.get('reasoning_tokens', 0)}", flush=True)


def ask_questions(items: list, exam_questions: dict) -> list:
    """Ask each question in a fresh session and record answer, sources, seconds and calls."""
    from tests.run_pm_tests import api
    from tests.run_e2e_tests import timed_answer
    print(f"Answer wait per question: {ANSWER_TIMEOUT_SECONDS}s", flush=True)
    for item in items:
        ident, question = item["id"], item["question"]
        started = time.perf_counter()
        try:
            session = api("POST", "/v1/web/agy/chat", {"mode": "chat"})["session_id"]
            answer = timed_answer(session, question, "real-model-" + ident,
                                  timeout=ANSWER_TIMEOUT_SECONDS)
            item["seconds"] = round(time.perf_counter() - started, 2)
            item["answer"] = str(answer.get("response") or "")
            item["sources"] = answer.get("sources") or answer.get("evidence") or answer.get("source_refs") or []
            item["timings"] = answer.get("timings") or {}
            # Diagnostics for a refusal: which gate rejected the answer, if any.
            item["answer_type"] = answer.get("answer_type")
            item["answer_status"] = answer.get("status")
            item["grounding"] = answer.get("grounding") or {}
            item["source_files"] = answer.get("source_files") or []
            item["model_calls"] = [
                {**row, "question_id": ident, "question": question}
                for row in (answer.get("model_calls") or [])
            ]
            passed, reason = judge_answer(ident, item["answer"], item["sources"], exam_questions)
            item["status"] = "PASS" if passed else "FAIL"
            item["reason"] = reason
        except Exception as exc:  # report only exception class; never serialize request details
            item["error"] = type(exc).__name__
            item["seconds"] = round(time.perf_counter() - started, 2)
    for item in items:
        timings = item.get("timings") or {}
        print(f"Question {item['id']}: {item['status']} seconds={item.get('seconds')} "
              f"model_seconds={timings.get('model_seconds')} "
              f"model_calls={len(item.get('model_calls') or [])} "
              f"answer_chars={len(item['answer'])} sources={len(item['sources'])} "
              f"markers={item.get('markers_found') or []} "
              f"reason={item.get('reason') or '-'} error={item.get('error') or '-'}", flush=True)
    return items


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
        "phase1_file_timings": [],
        "phase2_file_timings": [],
        "phase2_seconds": None,
        "scan_seconds": None,
        "model_calls": [],
        "model_call_summary": {},
        "answer_timeout_seconds": ANSWER_TIMEOUT_SECONDS,
        "exam_corpus": [],
        "exam_projects": [],
        "approved_candidates": 0,
        "ingestion_seconds": None,
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
            report["exam_corpus"] = create_samples(sample_dir)
            report["exam_projects"] = _create_exam_projects()
            print(f"Sample directory: {sample_dir} files={4 + len(report['exam_corpus'])} "
                  f"exam_corpus={len(report['exam_corpus'])} "
                  f"projects={len(report['exam_projects'])}", flush=True)
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
            ingestion_started = time.monotonic()
            deadline = ingestion_started + 240
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
                report["model_calls"] = progress.get("model_calls") or []
                report["model_call_summary"] = progress.get("model_call_summary") or {}

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
            report["ingestion_seconds"] = round(time.monotonic() - ingestion_started, 1)
            print(f"Scan finished in {report['ingestion_seconds']}s (limit 240s)", flush=True)

            # Normal user flow after a scan: approve the non-decision candidates and
            # resolve the document-family decisions, exactly like the exam runner.
            approval = _approve_and_resolve()
            report["approved_candidates"] = approval["approved_candidates"]
            print(f"Approved candidates: {report['approved_candidates']}", flush=True)

            report["questions"] = ask_questions(report["questions"], exam_questions)
            report["result"] = "GREEN" if len(report["questions"]) == 8 and all(q["status"] == "PASS" for q in report["questions"]) else "RED"
        except Exception as exc:
            report["issues"].append({"state": "未能运行", "reason": type(exc).__name__,
                                      "last_scan_progress": report["last_scan_progress"]})
            report["result"] = "RED"
        finally:
            if proc is not None:
                stop_paw(proc)
    # Measurement first: every Phase 2 model request and its per-file cost goes
    # into the report and the log, whichever way the scan ended.
    _emit_model_calls(report, key or "")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"model": report["model"], "result": report["result"],
                      "question_count": len(report["questions"]),
                      "not_run_count": sum(q["status"] == "未能运行" for q in report["questions"])}))
    # The mock lane checks that installation, scanning, and all question calls
    # complete. Its answers still receive the unchanged verdicts above.
    return 0 if args.mock_smoke or report["result"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())

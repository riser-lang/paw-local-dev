#!/usr/bin/env python3
"""Acceptance-only Gemini 3.8 Flash High canary.

This module deliberately has no connection to the production supervisor's
queue or state.  It imports the production prompt/parser helpers read-only,
and writes only below semantic_canary/gemini38flash/.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import semantic_supervisor as production


BASE = Path(__file__).resolve().parent
MODEL = "gemini-3.8-flash-high"
AGY = "/usr/local/bin/agy"
SCHEMA_PATH = BASE / "semantic_schema.json"
CANARY_ROOT = BASE / "semantic_canary" / "gemini38flash"
TIMEOUT_SECONDS = 1800

# These are intentionally explicit acceptance fixtures.  They are not read
# from the production queue and are never expanded to other source versions.
CASE_SPECS: tuple[dict[str, str], ...] = (
    {
        "case_id": "pptx_485497734941_98",
        "kind": "PPTX",
        "batch": "00_core_mentor",
        "source_id": "485497734941",
        "source_version_id": "485497734941@98",
        "title": "员工带教项目对接方案（0907修改）.pptx",
        "reader_result": str(BASE / "reader/00_core_mentor/artifacts/485497734941/485497734941_98/9f926b12bd74f34c/reader_result.json"),
        "baseline": str(BASE / "semantic/agy/00_core_mentor/485497734941@98.json"),
    },
    {
        "case_id": "xlsx_485481025226_95",
        "kind": "XLSX",
        "batch": "00_core_mentor",
        "source_id": "485481025226",
        "source_version_id": "485481025226@95",
        "title": "2026春季导师带教统筹表.xlsx",
        "reader_result": str(BASE / "reader/00_core_mentor/artifacts/485481025226/485481025226_95/82cf4d110c4993ab/reader_result.json"),
        "baseline": str(BASE / "semantic/agy/00_core_mentor/485481025226@95.json"),
    },
)

ANALYSIS_SECTIONS = tuple(production.ANALYSIS_SECTIONS)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
LOCATOR_RE = re.compile(r"(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def case_dir(spec: dict[str, str]) -> Path:
    return CANARY_ROOT / spec["case_id"]


def artifact_paths(spec: dict[str, str]) -> dict[str, Path]:
    root = case_dir(spec)
    stem = spec["case_id"]
    return {
        "normalized": root / f"{stem}.normalized.json",
        "stdout": root / f"{stem}.raw.stdout.txt",
        "stderr": root / f"{stem}.raw.stderr.txt",
        "agy_log": root / f"{stem}.agy.log",
    }


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value or "", encoding="utf-8")


def agy_command(log_path: Path) -> list[str]:
    # Keep the same structured stream contract as production.  The prompt
    # explicitly forbids tools, Web and Search; --sandbox remains enabled.
    return [
        AGY,
        "--model", MODEL,
        "--effort", "high",
        "--mode", "plan",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--json-schema", str(SCHEMA_PATH),
        "--print-timeout", "30m",
        "--disable-slash-commands",
        "--sandbox",
        "--log-file", str(log_path),
        "--print", "",
    ]


def build_prompt(meta: dict[str, Any], reader: dict[str, Any]) -> str:
    prompt = production.base_prompt(
        meta,
        model_role=(
            "Gemini 3.8 Flash High canary：独立第二阅读器与项目关系/风险发现器。"
            "只做证据绑定的结构化分析，重点发现事实、指标、冲突、风险、遗漏和视觉复核事项。"
        ),
        reference_mode=False,
    )
    return prompt + (
        "\n\nCANARY HARD CONSTRAINTS：不得调用任何工具；不得使用 Web、Search、浏览器、"
        "外部知识或命令。ReaderResult 是唯一事实输入。只输出最终 JSON。\n"
        "以下是唯一允许使用的 ReaderResult（控制器已读取并内嵌）：\n"
        + json.dumps(reader, ensure_ascii=False)
    )


def read_reader(spec: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    path = Path(spec["reader_result"])
    if not path.is_file():
        return None, f"missing ReaderResult: {path}"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"invalid ReaderResult: {exc}"
    if not isinstance(value, dict):
        return None, "ReaderResult must be a JSON object"
    source_id = str(value.get("source_object_id", ""))
    source_version = str(value.get("source_version", ""))
    if source_id and source_id != spec["source_id"]:
        return None, f"ReaderResult source_id mismatch: {source_id}"
    if source_version and source_version != spec["source_version_id"]:
        return None, f"ReaderResult source_version mismatch: {source_version}"
    return value, None


def usage_from_wrapper(wrapper: Any) -> dict[str, int | float]:
    """Extract Agy usage while preserving the names Agy actually returned."""
    found: dict[str, int | float] = {}
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "thinking_tokens": ("thinking_tokens", "thinkingTokens"),
        "cache_read_tokens": ("cache_read_tokens", "cacheReadTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for canonical, names in aliases.items():
                for name in names:
                    candidate = value.get(name)
                    if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                        found[canonical] = candidate
                        break
            for key in ("usage", "modelUsage", "stats", "metadata"):
                if key in value:
                    visit(value[key])

    visit(wrapper)
    return found


def numeric_tokens(value: Any) -> list[str]:
    return NUMBER_RE.findall(str(value))


def locator_value(reader: Any, locator: str) -> tuple[bool, Any]:
    if not isinstance(locator, str) or not locator:
        return False, None
    matches = list(LOCATOR_RE.finditer(locator))
    if not matches or matches[0].start() != 0:
        return False, None
    position = 0
    value = reader
    for match in matches:
        if match.start() != position:
            return False, None
        key, index = match.groups()
        try:
            if key is not None:
                if not isinstance(value, dict) or key not in value:
                    return False, None
                value = value[key]
            else:
                if not isinstance(value, list) or int(index) >= len(value):
                    return False, None
                value = value[int(index)]
        except (TypeError, ValueError, KeyError, IndexError):
            return False, None
        position = match.end()
    return position == len(locator), value


def all_items(analysis: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for section in ANALYSIS_SECTIONS + ("related_projects",):
        values = analysis.get(section, [])
        if isinstance(values, list):
            result.extend((section, item) for item in values if isinstance(item, dict))
    return result


def locator_stats(analysis: dict[str, Any], reader: dict[str, Any]) -> dict[str, Any]:
    total = valid = 0
    invalid: list[str] = []
    for section, item in all_items(analysis):
        locators = item.get("evidence_locators", [])
        if not isinstance(locators, list) or not locators:
            invalid.append(f"{section}:missing_locator")
            continue
        for locator in locators:
            total += 1
            ok, _ = locator_value(reader, locator)
            if ok:
                valid += 1
            else:
                invalid.append(str(locator))
    return {
        "total": total,
        "valid": valid,
        "invalid": len(invalid),
        "valid_rate": (valid / total if total else 0.0),
        "invalid_locators": invalid[:100],
    }


def schema_ok(analysis: Any) -> tuple[bool, str]:
    if not isinstance(analysis, dict):
        return False, "analysis is not an object"
    if not production.analysis_shape_ok(analysis):
        return False, "analysis does not satisfy production structured shape"
    required = set(production.ANALYSIS_REQUIRED) | {"summary"}
    if not required.issubset(analysis):
        return False, "required schema field missing"
    for section, item in all_items(analysis):
        if section == "related_projects":
            for key in ("project", "relationship", "confidence"):
                if not isinstance(item.get(key), str):
                    return False, f"related_projects.{key} must be a string"
        if "evidence_locators" in item and (
            not isinstance(item["evidence_locators"], list)
            or any(not isinstance(x, str) for x in item["evidence_locators"])
        ):
            return False, f"{section}.evidence_locators must be string[]"
    return True, "PASS"


def numeric_support(analysis: dict[str, Any], reader: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    unsupported: list[str] = []
    # The acceptance gate is intentionally conservative: a numeric claim in
    # any structured section must be recoverable from one of its locators.
    # summary is excluded because it is explicitly a secondary indicator.
    for section in ANALYSIS_SECTIONS + ("related_projects",):
        values = analysis.get(section, [])
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            content = item.get("content", "")
            tokens = numeric_tokens(content)
            if not tokens:
                continue
            locators = item.get("evidence_locators", [])
            evidence = []
            for locator in locators if isinstance(locators, list) else []:
                ok, value = locator_value(reader, locator)
                if ok:
                    evidence.append(json.dumps(value, ensure_ascii=False))
            evidence_text = "\n".join(evidence)
            evidence_numbers = {token.rstrip("%") for token in numeric_tokens(evidence_text)}
            missing = [token for token in tokens if token.rstrip("%") not in evidence_numbers]
            check = {
                "section": section,
                "index": index,
                "tokens": tokens,
                "locators": locators,
                "unsupported_tokens": missing,
            }
            checks.append(check)
            unsupported.extend(f"{section}[{index}]:{token}" for token in missing)
    return {
        "checked_items": len(checks),
        "unsupported": unsupported[:100],
        "unsupported_count": len(unsupported),
        "all_supported": not unsupported,
        "details": checks[:100],
    }


def visual_check(spec: dict[str, str], analysis: dict[str, Any], reader: dict[str, Any]) -> dict[str, Any]:
    structured = reader.get("structured_semantics", {})
    visual_available = bool(reader.get("visual_pages")) or any(
        isinstance(structured, dict) and structured.get(key)
        for key in ("slides", "pages", "drawing_parts", "chart_parts")
    )
    expected = spec["kind"].upper() == "PPTX" and visual_available
    actual = analysis.get("needs_visual_review") is True
    return {
        "expected_for_acceptance": expected,
        "actual": analysis.get("needs_visual_review"),
        "visual_evidence_available": visual_available,
        "reasonable": (actual if expected else isinstance(analysis.get("needs_visual_review"), bool)),
    }


def make_meta(spec: dict[str, str], reader: dict[str, Any]) -> dict[str, Any]:
    lineage = reader.get("artifact_lineage", {})
    raw_sha = str(reader.get("raw_artifact_sha256") or lineage.get("parent_raw_artifact_sha256") or "")
    return {
        "batch": spec["batch"],
        "source_id": spec["source_id"],
        "source_version_id": spec["source_version_id"],
        "stable_object_id": spec["source_id"],
        "version_marker": spec["source_version_id"].rsplit("@", 1)[-1],
        "title": spec["title"],
        "inventory_path": "",
        "family": spec["kind"],
        "project_tier": "CORE",
        "paw_project_name": "运营导师体系",
        "paw_project_id": None,
        "raw_sha256": raw_sha,
        "reader_result_path": spec["reader_result"],
    }


def failed_payload(spec: dict[str, str], error_class: str, detail: str, *, duration: float = 0.0, returncode: int | None = None) -> dict[str, Any]:
    paths = artifact_paths(spec)
    return {
        "status": "FAILED",
        "result_success": False,
        "model": MODEL,
        "error_class": error_class,
        "detail": detail[-12000:],
        "execution": {
            "started_at": None,
            "completed_at": now(),
            "duration_seconds": duration,
            "returncode": returncode,
        },
        "usage": {},
        "model_invocation": False,
        "raw_stdout_path": str(paths["stdout"]),
        "raw_stderr_path": str(paths["stderr"]),
        "agy_log_path": str(paths["agy_log"]),
        "acceptance": {"case_pass": False},
    }


def run_case(spec: dict[str, str], runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    if runner is None:
        runner = subprocess.run
    paths = artifact_paths(spec)
    existing = paths["normalized"]
    if existing.is_file():
        try:
            value = json.loads(existing.read_text(encoding="utf-8"))
            if value.get("status") == "SUCCESS":
                return {"case_id": spec["case_id"], "status": "SKIPPED_EXISTS", "normalized": str(existing)}
        except (OSError, ValueError, TypeError):
            pass

    reader, problem = read_reader(spec)
    if problem:
        payload = failed_payload(spec, "INPUT_MISSING_OR_INVALID", problem)
        atomic_json(existing, payload)
        return {"case_id": spec["case_id"], "status": "FAILED", "error_class": payload["error_class"], "normalized": str(existing)}

    assert reader is not None
    meta = make_meta(spec, reader)
    prompt = build_prompt(meta, reader)
    stream_input = json.dumps({"event": "user", "message": {"role": "user", "content": prompt}}, ensure_ascii=False) + "\n"
    started_at = now()
    started_clock = time.monotonic()
    stdout = stderr = ""
    returncode: int | None = None
    wrapper: Any = None
    error_class = "NONE"
    detail = ""
    usage: dict[str, int | float] = {}
    parsed_analysis: dict[str, Any] | None = None
    try:
        proc = runner(
            agy_command(paths["agy_log"]),
            cwd=str(Path(spec["reader_result"]).parent),
            input=stream_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        returncode = proc.returncode
        if returncode != 0:
            error_class = production.classify_model_failure(stdout, stderr, returncode)
            detail = stdout + "\n[STDERR]\n" + stderr
        else:
            wrapper = production.extract_agy_stream_result(stdout)
            usage = usage_from_wrapper(wrapper)
            parsed_analysis = production.extract_analysis(wrapper)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        error_class = "TIMEOUT"
        detail = stdout + "\n[TIMEOUT]\n" + stderr
    except production.ModelReportedError as exc:
        error_class = exc.kind
        detail = str(exc)
    except production.ToolCallIncomplete as exc:
        error_class = "MODEL_PROTOCOL_INCOMPLETE"
        detail = str(exc)
    except Exception as exc:  # controller/parser errors are recorded, never raised into production
        error_class = "CONTROLLER_EXCEPTION"
        detail = repr(exc)

    write_text(paths["stdout"], stdout)
    write_text(paths["stderr"], stderr)
    duration = time.monotonic() - started_clock
    completed_at = now()
    actual_success = bool(
        isinstance(wrapper, dict)
        and str(wrapper.get("status", "")).upper() == "SUCCESS"
    )
    if parsed_analysis is not None and error_class == "NONE":
        payload: dict[str, Any] = {
            "status": "SUCCESS",
            "result_success": True,
            "model": MODEL,
            "error_class": "NONE",
            "authoritative_lineage": {
                "source_id": meta["source_id"],
                "source_version_id": meta["source_version_id"],
                "stable_object_id": meta["stable_object_id"],
                "version_marker": meta["version_marker"],
                "raw_sha256": meta["raw_sha256"],
                "reader_result_path": meta["reader_result_path"],
            },
            "routing": {"batch": spec["batch"], "title": spec["title"], "family": spec["kind"], "project_tier": "CORE"},
            "execution": {"started_at": started_at, "completed_at": completed_at, "duration_seconds": duration, "returncode": returncode},
            "usage": usage,
            "model_invocation": bool(actual_success or usage.get("input_tokens", 0) > 0 or usage.get("output_tokens", 0) > 0),
            "raw_stdout_path": str(paths["stdout"]),
            "raw_stderr_path": str(paths["stderr"]),
            "agy_log_path": str(paths["agy_log"]),
            "analysis": parsed_analysis,
        }
    else:
        payload = failed_payload(spec, error_class, detail or "structured semantic payload not found", duration=duration, returncode=returncode)
        payload["execution"]["started_at"] = started_at
        payload["execution"]["completed_at"] = completed_at
        payload["usage"] = usage

    atomic_json(existing, payload)
    return {"case_id": spec["case_id"], "status": payload["status"], "error_class": payload["error_class"], "normalized": str(existing)}


def load_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def baseline_stats(spec: dict[str, str], reader: dict[str, Any]) -> dict[str, Any]:
    baseline = load_json(spec["baseline"])
    if not isinstance(baseline, dict):
        return {"available": False, "path": spec["baseline"], "schema_ok": False, "reason": "baseline missing or invalid"}
    analysis = baseline.get("analysis")
    ok, reason = schema_ok(analysis)
    locators = locator_stats(analysis, reader) if isinstance(analysis, dict) else {"total": 0, "valid": 0, "valid_rate": 0.0, "invalid": 0, "invalid_locators": []}
    counts = {key: len(analysis.get(key, [])) for key in ("facts", "metrics", "risks", "ambiguities")} if isinstance(analysis, dict) else {}
    return {
        "available": True,
        "path": spec["baseline"],
        "status": baseline.get("status"),
        "success": baseline.get("status") == "SUCCESS",
        "schema_ok": ok,
        "schema_reason": reason,
        "counts": counts,
        "locator_stats": locators,
    }


def compare_case(spec: dict[str, str]) -> dict[str, Any]:
    normalized_path = artifact_paths(spec)["normalized"]
    result = load_json(normalized_path)
    reader, problem = read_reader(spec)
    if problem or reader is None:
        return {"case_id": spec["case_id"], "status": "INPUT_UNAVAILABLE", "case_pass": False, "reason": problem}
    baseline = baseline_stats(spec, reader)
    if not isinstance(result, dict):
        return {"case_id": spec["case_id"], "status": "RESULT_MISSING", "case_pass": False, "baseline": baseline}
    analysis = result.get("analysis")
    schema_pass, schema_reason = schema_ok(analysis)
    canary_locators = locator_stats(analysis, reader) if isinstance(analysis, dict) else {"total": 0, "valid": 0, "valid_rate": 0.0, "invalid": 0, "invalid_locators": []}
    numeric = numeric_support(analysis, reader) if isinstance(analysis, dict) else {"all_supported": False, "unsupported_count": 0, "unsupported": []}
    lineage = result.get("authoritative_lineage", {}) if isinstance(result.get("authoritative_lineage"), dict) else {}
    lineage_expected = {
        "source_id": spec["source_id"],
        "source_version_id": spec["source_version_id"],
        "raw_sha256": str(reader.get("raw_artifact_sha256") or reader.get("artifact_lineage", {}).get("parent_raw_artifact_sha256") or ""),
        "reader_result_path": spec["reader_result"],
    }
    lineage_pass = all(lineage.get(key) == value for key, value in lineage_expected.items())
    visual = visual_check(spec, analysis if isinstance(analysis, dict) else {}, reader)
    usage = result.get("usage", {}) if isinstance(result.get("usage"), dict) else {}
    invoked = bool(result.get("model_invocation") or usage.get("input_tokens", 0) > 0 or usage.get("output_tokens", 0) > 0)
    baseline_rate = float(baseline.get("locator_stats", {}).get("valid_rate", 0.0))
    rate_pass = baseline.get("success") is True and baseline.get("schema_ok") is True and canary_locators["valid_rate"] >= baseline_rate
    summary_ok = isinstance(analysis, dict) and isinstance(analysis.get("summary"), str) and bool(analysis["summary"].strip())
    checks = {
        "model_invocation": invoked,
        "status_success": result.get("status") == "SUCCESS",
        "schema_parse_success": result.get("status") == "SUCCESS" and schema_pass,
        "authoritative_lineage_exact": lineage_pass,
        "numeric_facts_metrics_have_supported_locators": numeric.get("all_supported", False),
        "no_unsupported_numeric_claims": numeric.get("unsupported_count", 0) == 0,
        "locator_valid_rate_at_least_baseline": rate_pass,
        "pptx_needs_visual_review_reasonable": visual["reasonable"],
    }
    # summary is deliberately reported but is not a hard acceptance gate.
    case_pass = all(checks.values()) and baseline.get("success") is True and baseline.get("schema_ok") is True
    return {
        "case_id": spec["case_id"],
        "kind": spec["kind"],
        "title": spec["title"],
        "status": result.get("status"),
        "model": result.get("model"),
        "error_class": result.get("error_class", "NONE"),
        "case_pass": case_pass,
        "checks": checks,
        "schema": {"pass": schema_pass, "reason": schema_reason},
        "lineage": {"pass": lineage_pass, "expected": lineage_expected, "actual": lineage},
        "counts": {key: len(analysis.get(key, [])) for key in ("facts", "metrics", "risks", "ambiguities")} if isinstance(analysis, dict) else {},
        "baseline": baseline,
        "locator_stats": canary_locators,
        "numeric_support": numeric,
        "visual_review": visual,
        "summary_secondary": {"nonempty": summary_ok, "length": len(analysis.get("summary", "")) if isinstance(analysis, dict) else 0},
        "normalized_path": str(normalized_path),
    }


def generate_report() -> dict[str, Any]:
    cases = [compare_case(spec) for spec in CASE_SPECS]
    report = {
        "generated_at": now(),
        "model": MODEL,
        "schema_path": str(SCHEMA_PATH),
        "output_root": str(CANARY_ROOT),
        "reader_is_only_fact_input": True,
        "web_search": "PROHIBITED",
        "formal_state_write": "PROHIBITED",
        "cases": cases,
    }
    report["CANARY_PASS"] = len(cases) == len(CASE_SPECS) and all(case.get("case_pass") is True for case in cases)
    report["invoked_successfully"] = sum(1 for case in cases if case.get("checks", {}).get("model_invocation") is True)
    report["required_invocations"] = len(CASE_SPECS)
    return report


def run_all() -> dict[str, Any]:
    CANARY_ROOT.mkdir(parents=True, exist_ok=True)
    run_manifest = {
        "started_at": now(),
        "model": MODEL,
        "command": "python3 gemini38_flash_canary.py --run",
        "cases": [
            {**spec, "output": {key: str(value) for key, value in artifact_paths(spec).items()}}
            for spec in CASE_SPECS
        ],
    }
    for spec in CASE_SPECS:
        run_case(spec)
    run_manifest["completed_at"] = now()
    atomic_json(CANARY_ROOT / "run_manifest.json", run_manifest)
    report = generate_report()
    atomic_json(CANARY_ROOT / "report.json", report)
    return report


def print_report(report: dict[str, Any]) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("CANARY_PASS") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini 3.8 Flash High acceptance-only canary")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--run", action="store_true", help="run the two fixed Agy cases")
    action.add_argument("--report", action="store_true", help="read existing canary results and report")
    args = parser.parse_args(argv)
    if args.run:
        return print_report(run_all())
    # No flag and --report both read only; regenerate from normalized results
    # so a manually copied result cannot leave a stale report behind.
    return print_report(generate_report())


if __name__ == "__main__":
    raise SystemExit(main())

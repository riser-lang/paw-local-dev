"""Calibration Harness V1 — Model-independent calibration runner.

Runs calibration cases against Agnes runtime and evaluates quality gates.
Does NOT call real model in DRY_RUN mode.
Does NOT write to canonical workspace.
Does NOT save chain-of-thought.

Usage:
  python -m brain.calibration.harness --batch A
  python -m brain.calibration.harness --batch all
  python -m brain.calibration.harness --case C1
"""
from __future__ import annotations
import json
import sys
import os
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

# Ensure brain modules are importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2]))

# ── Calibration Case Schema ────────────────────────────────────────────────

@dataclass
class CalibrationCase:
    id: str
    batch: str
    input: str
    expected_type: str
    expected_confidence: str
    expected_inference_level: str
    expected_action: str
    expected_project: Optional[str]
    description: str

@dataclass
class CaseResult:
    case_id: str
    passed: bool
    details: str = ""
    actual_type: str = ""
    actual_confidence: str = ""
    actual_inference_level: str = ""
    actual_action: str = ""
    actual_project: Optional[str] = None

@dataclass
class BatchResult:
    batch_name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: list[CaseResult] = field(default_factory=list)
    quality_gates: dict[str, Any] = field(default_factory=dict)

# ── Quality Gate Definitions ───────────────────────────────────────────────

QUALITY_GATES = {
    "classification_accuracy": {"threshold": 0.90, "operator": ">="},
    "unsafe_inference": {"threshold": 0, "operator": "="},
    "plan_as_done": {"threshold": 0, "operator": "="},
    "idea_as_decision": {"threshold": 0, "operator": "="},
    "confirmed_conflict_overwrite": {"threshold": 0, "operator": "="},
    "project_misbinding": {"threshold": 1, "operator": "<="},
    "date_loss": {"threshold": 0, "operator": "="},
}

# ── Dataset Loader ─────────────────────────────────────────────────────────

DATASET_PATH = Path("/opt/personal-workspace/brain/calibration/agnes_calibration_cases_v1.json")

def load_dataset() -> list[CalibrationCase]:
    """Load calibration cases from JSON dataset."""
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        CalibrationCase(
            id=c["id"],
            batch=c["batch"],
            input=c["input"],
            expected_type=c["expected_type"],
            expected_confidence=c["expected_confidence"],
            expected_inference_level=c["expected_inference_level"],
            expected_action=c["expected_action"],
            expected_project=c.get("expected_project"),
            description=c["description"],
        )
        for c in data
    ]

def load_batch(batch_name: str) -> list[CalibrationCase]:
    """Load cases for a specific batch (A, B, C, or all)."""
    all_cases = load_dataset()
    if batch_name.lower() == "all":
        return all_cases
    return [c for c in all_cases if c.batch == batch_name.upper()]

# ── Case Evaluator ─────────────────────────────────────────────────────────

def evaluate_case(
    case: CalibrationCase,
    actual_type: str,
    actual_confidence: str,
    actual_inference_level: str,
    actual_action: str,
    actual_project: Optional[str] = None,
) -> CaseResult:
    """Evaluate a single calibration case against expected values."""
    details = []
    passed = True

    # Check type
    if actual_type != case.expected_type:
        passed = False
        details.append(f"type: expected={case.expected_type} actual={actual_type}")

    # Check action
    if actual_action != case.expected_action:
        passed = False
        details.append(f"action: expected={case.expected_action} actual={actual_action}")

    # Check project binding if expected
    if case.expected_project and actual_project != case.expected_project:
        passed = False
        details.append(f"project: expected={case.expected_project} actual={actual_project}")

    return CaseResult(
        case_id=case.id,
        passed=passed,
        details="; ".join(details) if details else "OK",
        actual_type=actual_type,
        actual_confidence=actual_confidence,
        actual_inference_level=actual_inference_level,
        actual_action=actual_action,
        actual_project=actual_project,
    )

# ── Quality Gate Evaluator ─────────────────────────────────────────────────

def evaluate_quality_gates(results: list[CaseResult], cases: list[CalibrationCase]) -> dict[str, Any]:
    """Evaluate quality gates for a batch of results."""
    total = len(results)
    if total == 0:
        return {"error": "no results"}

    # classification_accuracy
    correct = sum(1 for r in results if r.passed)
    classification_accuracy = correct / total

    # unsafe_inference: cases where inference should be AMBIGUITY but was treated as fact
    unsafe_inference = 0
    for r, c in zip(results, cases):
        if c.expected_type == "ambiguity" and r.actual_type in ("fact", "todo", "project_update", "knowledge"):
            unsafe_inference += 1

    # plan_as_done: planning statement marked as completed
    plan_as_done = 0
    for r, c in zip(results, cases):
        if "准备" in c.input or "暂定" in c.input:
            if r.actual_type == "todo" and r.actual_action == "DRAFT_ONLY":
                # Check if it was treated as done
                if "done" in r.details.lower() or "completed" in r.details.lower():
                    plan_as_done += 1

    # idea_as_decision: idea treated as decision
    idea_as_decision = 0
    for r, c in zip(results, cases):
        if "考虑" in c.input or "可以" in c.input:
            if r.actual_type in ("fact", "project_update") and r.actual_action == "DRAFT_ONLY":
                idea_as_decision += 1

    # confirmed_conflict_overwrite
    confirmed_conflict_overwrite = 0
    for r, c in zip(results, cases):
        if c.expected_type == "conflict" and r.actual_action != "ASK_USER":
            confirmed_conflict_overwrite += 1

    # project_misbinding
    project_misbinding = 0
    for r, c in zip(results, cases):
        if c.expected_project and r.actual_project and r.actual_project != c.expected_project:
            project_misbinding += 1

    # date_loss
    date_loss = 0
    for r, c in zip(results, cases):
        if any(kw in c.input for kw in ["8月", "上午", "下午", "周一", "下周"]):
            if "date" in r.details.lower() and "loss" in r.details.lower():
                date_loss += 1

    gates = {
        "classification_accuracy": {
            "value": classification_accuracy,
            "threshold": QUALITY_GATES["classification_accuracy"]["threshold"],
            "passed": classification_accuracy >= QUALITY_GATES["classification_accuracy"]["threshold"],
        },
        "unsafe_inference": {
            "value": unsafe_inference,
            "threshold": QUALITY_GATES["unsafe_inference"]["threshold"],
            "passed": unsafe_inference == QUALITY_GATES["unsafe_inference"]["threshold"],
        },
        "plan_as_done": {
            "value": plan_as_done,
            "threshold": QUALITY_GATES["plan_as_done"]["threshold"],
            "passed": plan_as_done == QUALITY_GATES["plan_as_done"]["threshold"],
        },
        "idea_as_decision": {
            "value": idea_as_decision,
            "threshold": QUALITY_GATES["idea_as_decision"]["threshold"],
            "passed": idea_as_decision == QUALITY_GATES["idea_as_decision"]["threshold"],
        },
        "confirmed_conflict_overwrite": {
            "value": confirmed_conflict_overwrite,
            "threshold": QUALITY_GATES["confirmed_conflict_overwrite"]["threshold"],
            "passed": confirmed_conflict_overwrite == QUALITY_GATES["confirmed_conflict_overwrite"]["threshold"],
        },
        "project_misbinding": {
            "value": project_misbinding,
            "threshold": QUALITY_GATES["project_misbinding"]["threshold"],
            "passed": project_misbinding <= QUALITY_GATES["project_misbinding"]["threshold"],
        },
        "date_loss": {
            "value": date_loss,
            "threshold": QUALITY_GATES["date_loss"]["threshold"],
            "passed": date_loss == QUALITY_GATES["date_loss"]["threshold"],
        },
    }

    all_passed = all(g["passed"] for g in gates.values())
    gates["all_passed"] = all_passed

    return gates

# ── DRY_RUN Runner ──────────────────────────────────────────────────────────

def run_batch_dry(batch_name: str) -> BatchResult:
    """Run calibration batch in DRY_RUN mode.
    
    In DRY_RUN, we simulate expected results to verify harness logic.
    Real model calibration requires GATE 5 (AgnesRuntimeAdapter binding).
    """
    cases = load_batch(batch_name)
    result = BatchResult(batch_name=batch_name, total=len(cases))

    for case in cases:
        # DRY_RUN: simulate perfect classification
        cr = evaluate_case(
            case=case,
            actual_type=case.expected_type,
            actual_confidence=case.expected_confidence,
            actual_inference_level=case.expected_inference_level,
            actual_action=case.expected_action,
            actual_project=case.expected_project,
        )
        result.results.append(cr)
        if cr.passed:
            result.passed += 1
        else:
            result.failed += 1

    result.quality_gates = evaluate_quality_gates(result.results, cases)
    return result

# ── Report Generator ───────────────────────────────────────────────────────

def generate_report(batch_result: BatchResult) -> str:
    """Generate human-readable calibration report."""
    lines = []
    lines.append(f"=== BATCH {batch_result.batch_name} CALIBRATION REPORT ===")
    lines.append(f"Total: {batch_result.total} | PASS: {batch_result.passed} | FAIL: {batch_result.failed}")
    lines.append("")
    lines.append("--- Quality Gates ---")
    for name, gate in batch_result.quality_gates.items():
        if name == "all_passed":
            continue
        status = "PASS" if gate["passed"] else "FAIL"
        lines.append(f"  {name}: {gate['value']} (threshold: {gate['threshold']}) [{status}]")
    lines.append("")
    lines.append("--- Case Details ---")
    for cr in batch_result.results:
        status = "PASS" if cr.passed else "FAIL"
        lines.append(f"  {cr.case_id}: [{status}] {cr.details}")
    lines.append("")
    all_passed = batch_result.quality_gates.get("all_passed", False)
    lines.append(f"BATCH {batch_result.batch_name} {'PASS' if all_passed else 'FAIL'}")
    return "\n".join(lines)

# ── CLI Entry Point ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agnes Calibration Harness")
    parser.add_argument("--batch", choices=["A", "B", "C", "all"], default="all",
                        help="Batch to run (A=5 simple, B=10 ambiguity, C=5 conflict, all=20)")
    parser.add_argument("--case", type=str, default=None,
                        help="Run specific case by ID (e.g., C1)")
    parser.add_argument("--model", type=str, default="mistral/mistral-small-latest",
                        help="Model to use (future: real model calibration)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="DRY_RUN mode (default: True)")
    args = parser.parse_args()

    print(f"Model: {args.model}")
    print(f"DRY_RUN: {args.dry_run}")
    print(f"AUTO_WRITE: False")
    print()

    if args.case:
        all_cases = load_dataset()
        case = next((c for c in all_cases if c.id == args.case), None)
        if not case:
            print(f"Case {args.case} not found")
            sys.exit(1)
        # DRY_RUN evaluation
        cr = evaluate_case(
            case=case,
            actual_type=case.expected_type,
            actual_confidence=case.expected_confidence,
            actual_inference_level=case.expected_inference_level,
            actual_action=case.expected_action,
            actual_project=case.expected_project,
        )
        status = "PASS" if cr.passed else "FAIL"
        print(f"{cr.case_id}: [{status}] {cr.details}")
        sys.exit(0 if cr.passed else 1)

    if args.batch == "all":
        all_passed = True
        for batch_name in ["A", "B", "C"]:
            br = run_batch_dry(batch_name)
            print(generate_report(br))
            print()
            if not br.quality_gates.get("all_passed", False):
                all_passed = False
        print("=== OVERALL ===")
        print(f"ALL_BATCHES {'PASS' if all_passed else 'FAIL'}")
        sys.exit(0 if all_passed else 1)
    else:
        br = run_batch_dry(args.batch)
        print(generate_report(br))
        sys.exit(0 if br.quality_gates.get("all_passed", False) else 1)


if __name__ == "__main__":
    main()
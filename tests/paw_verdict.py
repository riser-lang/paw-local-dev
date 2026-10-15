"""Overall verdict for one pipeline run (owned by the reviewer).

Usage: python tests/paw_verdict.py <reports_dir> <commit_sha> <summary.md>
Reads every *.json report written by paw_case.Report and exits 0 only if:
- every required case appears exactly once and is PASS,
- every normal report comes from the same commit and the same installer,
- no screenshot is reused anywhere in the run,
- every negative control FAILed on exactly the expected check,
- the test code passes paw_lint.py.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(reports_dir: str, commit: str, summary_path: str) -> int:
    # required_cases.json plus reviewer-approved feature files required_cases_*.json
    spec = {"cases": {}, "negative_controls": {}}
    for path in sorted(ROOT.glob("required_cases*.json")):
        part = json.loads(path.read_text(encoding="utf-8"))
        for key in spec:
            overlap = set(spec[key]) & set(part.get(key, {}))
            if overlap:
                raise SystemExit(f"duplicate case ids in {path.name}: {sorted(overlap)}")
            spec[key].update(part.get(key, {}))
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(reports_dir).rglob("*.json"))]
    problems: list[str] = []
    seen: Counter = Counter()
    status: dict[str, dict] = {}
    screens: Counter = Counter()
    installers = set()
    rows = []

    for rep in reports:
        if rep.get("commit") != commit:
            problems.append(f"{rep.get('block')}: commit {rep.get('commit')} != {commit}")
        if not rep.get("model_mode"):
            problems.append(f"{rep.get('block')}: model_mode not recorded")
        if rep.get("block") != "NEGATIVE-CONTROL":
            installers.add(rep.get("installer_sha256"))
        for case in rep.get("cases", []):
            seen[case["id"]] += 1
            status[case["id"]] = case
            for shot in case.get("screens", []):
                screens[shot["sha256"]] += 1
            rows.append((rep.get("block"), case["id"], case["status"], case.get("failed_check") or "", rep.get("model_mode")))

    if len(installers) != 1:
        problems.append(f"normal blocks used {len(installers)} different installers")
    for cid in spec["cases"]:
        if seen[cid] != 1:
            problems.append(f"{cid}: appears {seen[cid]} times (expected 1)")
        elif status[cid]["status"] != "PASS":
            problems.append(f"{cid}: {status[cid]['status']} ({status[cid].get('failed_check')})")
    for nid, rule in spec["negative_controls"].items():
        case = status.get(nid)
        if case is None or seen[nid] != 1:
            problems.append(f"{nid}: negative control missing or duplicated")
        elif case["status"] != "FAIL" or case.get("failed_check") != rule["expect_failed_check"]:
            problems.append(f"{nid}: expected FAIL on {rule['expect_failed_check']}, got {case['status']} on {case.get('failed_check')}")
    for sha, n in screens.items():
        if n > 1:
            problems.append(f"screenshot {sha[:12]} reused {n} times")
    sys.path.insert(0, str(ROOT))
    from paw_lint import lint_all
    problems += [f"lint {p}" for p in lint_all()]
    known = set(spec["cases"]) | set(spec["negative_controls"])
    for cid in seen:
        if cid not in known:
            problems.append(f"{cid}: unexpected case id (not in required_cases.json)")

    counts = Counter(r[2] for r in rows if r[1] in spec["cases"])
    lines = [f"# Pipeline verdict for {commit}", "",
             f"Required cases: {len(spec['cases'])} | PASS {counts['PASS']} | FAIL {counts['FAIL']} | NOT RUN {len(spec['cases']) - counts['PASS'] - counts['FAIL']}", "",
             "| Block | Case | Status | Failed check | Model |", "|---|---|---|---|---|"]
    lines += [f"| {b} | {c} | {s} | {f} | {m} |" for b, c, s, f, m in rows]
    lines += ["", "## Problems" if problems else "## No problems", *[f"- {p}" for p in problems],
              "", f"VERDICT: {'RED' if problems else 'GREEN'}"]
    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))

"""Static rules for test code (owned by the reviewer; run by paw_verdict.py).

Rejects patterns that let a case pass without testing anything:
  R1 a case block without c.fresh_install(...) (only NC-03 is exempt)
  R2 a check whose actual and expected values are both constants
  R3 a case whose only checks are HTTP status codes / app-started / install
  R4 a ">= 0" comparison (always true)
  R5 test code writing AI review records directly (candidate, approved_change, ...)
  R6 a B4/B5/B7 screenshot of the bare /app page instead of the page under test
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE)\b[^;]*?\b(candidate|approved_change|review_decision|audit_event|"
    r"analysis_run|knowledge_page|knowledge_revision|todo)\b", re.I | re.S)
SETUP_CHECKS = {"installer_exit_code", "installed_during_this_case", "app_alive_at_screenshot", "screenshot_not_trivial"}


def _is_case_with(node):
    return (isinstance(node, ast.With) and node.items and isinstance(node.items[0].context_expr, ast.Call)
            and isinstance(node.items[0].context_expr.func, ast.Attribute)
            and node.items[0].context_expr.func.attr == "case")


def _calls(node, attr):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def _const(node):
    return isinstance(node, ast.Constant)


def lint_file(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = []
    where = lambda n: f"{path.name}:{getattr(n, 'lineno', 0)}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and FORBIDDEN_SQL.search(node.value):
            out.append(f"{where(node)}: R5 test writes AI review records directly")
        if isinstance(node, ast.JoinedStr):
            text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if FORBIDDEN_SQL.search(text):
                out.append(f"{where(node)}: R5 test writes AI review records directly")
        if isinstance(node, ast.Compare) and any(isinstance(op, ast.GtE) for op in node.ops) \
                and any(_const(c) and c.value == 0 for c in node.comparators):
            out.append(f"{where(node)}: R4 '>= 0' is always true")

    for block in (n for n in ast.walk(tree) if _is_case_with(n)):
        arg0 = block.items[0].context_expr.args[0] if block.items[0].context_expr.args else None
        case_id = arg0.value if _const(arg0) else None
        if case_id != "NC-03" and not _calls(block, "fresh_install"):
            out.append(f"{where(block)}: R1 case {case_id or '<variable>'} has no c.fresh_install(...)")
        strong = 0
        for call in _calls(block, "check"):
            args = call.args
            if len(args) >= 3 and _const(args[1]) and _const(args[2]):
                out.append(f"{where(call)}: R2 check compares two constants")
                continue
            if len(args) >= 4 and _const(args[3]) and args[3].value == ">=" and _const(args[2]) and args[2].value == 0:
                out.append(f"{where(call)}: R4 check '>= 0' is always true")
                continue
            name = args[0].value if args and _const(args[0]) else ""
            actual = ast.unparse(args[1]) if len(args) > 1 else ""
            if name in SETUP_CHECKS or name.startswith("app_") or "status_code" in actual:
                continue
            strong += 1
        if case_id != "NC-03" and strong == 0:
            out.append(f"{where(block)}: R3 case {case_id or '<variable>'} has no substantive check")
        if case_id is None or case_id[:2] in ("B4", "B5", "B7"):
            for call in _calls(block, "screenshot"):
                if len(call.args) > 1 and _const(call.args[1]) and str(call.args[1].value).rstrip("/").endswith("/app"):
                    out.append(f"{where(call)}: R6 screenshot of bare /app, not the page under test")
    return out


def lint_all() -> list[str]:
    problems = []
    for path in sorted(ROOT.glob("*.py")):
        if not path.name.startswith("paw_"):
            problems += lint_file(path)
    return problems


if __name__ == "__main__":
    found = lint_all()
    print("\n".join(found) or "lint clean")
    sys.exit(1 if found else 0)

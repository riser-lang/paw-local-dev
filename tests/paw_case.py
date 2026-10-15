"""Verdict recorder for the cloud test pipeline (owned by the reviewer).

Do not change this file without reviewer approval. Rules it enforces:
- A case is PASS only if, inside the case, it wiped the machine, proved the wipe
  (all zero) and freshly installed (fresh_install), then made at least one
  check, and every check held.
- Any exception, failed check, missing check or dirty environment is FAIL.
- A case that never ran is NOT RUN. Nothing else can produce a verdict.
"""
from __future__ import annotations

import hashlib
import json
import time
import traceback
import urllib.request
from pathlib import Path


class CheckFailed(Exception):
    pass


def _short(value, limit=300):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


_OPS = {
    "==": lambda a, e: a == e,
    "!=": lambda a, e: a != e,
    ">=": lambda a, e: a >= e,
    "in": lambda a, e: e in a,
    "truthy": lambda a, e: bool(a),
}


class Case:
    def __init__(self, report: "Report", case_id: str, title: str):
        self.report, self.id, self.title = report, case_id, title
        self.status, self.checks, self.screens = "NOT RUN", [], []
        self.clean = None
        self.installed = False
        self.failed_check = self.error = None
        self.started = self.finished = None

    def fresh_install(self, clean_fn, install_fn, install_dir: str | Path):
        """Wipe, prove the wipe, install. Required in every normal case.

        clean_fn() -> {location: leftover_count}; install_fn() -> installer exit code.
        install_dir must be (re)created by this install, i.e. after the case started.
        """
        self.record_clean(clean_fn())
        self.check("installer_exit_code", install_fn(), 0)
        st = Path(install_dir).stat()
        created = getattr(st, "st_birthtime", st.st_ctime)
        self.check("installed_during_this_case", created >= self.started - 1, True)
        self.installed = True

    def record_clean(self, locations: dict[str, int]):
        """locations: {path_or_key: number_of_items_found}. Every value must be 0."""
        self.clean = dict(locations)
        dirty = {k: v for k, v in locations.items() if v}
        if not locations or dirty:
            self.failed_check = "environment_clean"
            raise CheckFailed(f"environment not clean: {dirty or 'no locations checked'}")

    def check(self, name: str, actual, expected=True, op: str = "=="):
        ok = bool(_OPS[op](actual, expected))
        self.checks.append({"name": name, "op": op, "actual": _short(actual), "expected": _short(expected), "ok": ok})
        if not ok:
            self.failed_check = name
            raise CheckFailed(name)

    def screenshot(self, png_path: str | Path, page_url: str, health_url: str = "http://127.0.0.1:8800/app"):
        """Register a screenshot taken of page_url while the app was alive."""
        with urllib.request.urlopen(health_url, timeout=10) as resp:
            alive = resp.status == 200
        self.check("app_alive_at_screenshot", alive)
        data = Path(png_path).read_bytes()
        self.check("screenshot_not_trivial", len(data), 20_000, ">=")
        self.screens.append({"file": Path(png_path).name, "page_url": page_url,
                             "sha256": hashlib.sha256(data).hexdigest(), "time": time.time()})

    def __enter__(self):
        self.status, self.started = "RUNNING", time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.finished = time.time()
        if exc_type is None:
            if self.clean is None or not self.installed:
                self.status, self.failed_check = "FAIL", "fresh_install_not_done_in_case"
            elif not self.checks:
                self.status, self.failed_check = "FAIL", "no_checks_made"
            else:
                self.status = "PASS"
        else:
            self.status = "FAIL"
            self.failed_check = self.failed_check or "exception"
            self.error = "".join(traceback.format_exception(exc_type, exc, tb))[-2000:]
        self.report.cases.append(self)
        return True  # a failing case must not stop the remaining cases

    def as_dict(self):
        return {k: getattr(self, k) for k in ("id", "title", "status", "failed_check", "error",
                                               "clean", "checks", "screens", "started", "finished")}


class Report:
    def __init__(self, block: str, commit: str, model_mode: str, installer_sha256: str):
        self.block, self.commit, self.model_mode, self.installer_sha256 = block, commit, model_mode, installer_sha256
        self.cases: list[Case] = []

    def case(self, case_id: str, title: str) -> Case:
        return Case(self, case_id, title)

    def save(self, path: str | Path) -> int:
        Path(path).write_text(json.dumps({
            "block": self.block, "commit": self.commit, "model_mode": self.model_mode,
            "installer_sha256": self.installer_sha256, "cases": [c.as_dict() for c in self.cases],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if self.cases and all(c.status == "PASS" for c in self.cases) else 1

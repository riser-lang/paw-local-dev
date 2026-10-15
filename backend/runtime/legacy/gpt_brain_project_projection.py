"""Deterministic Project + Data projection rehydrate.  No model calls."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gpt_brain_common import LockedJsonStore, utc_now
from gpt_brain_data_layer import MetricDataLayer
from gpt_brain_project_builder import ProjectBuilder, default_project_catalog

DEFAULT_BRAIN = Path("/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json")
DEFAULT_LAYER_DIR = Path("/var/lib/paw-mcp-gateway")
DEFAULT_WORKSPACE_DB = Path(
    os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))
)
MIN_FREE_GB = 4.0


def _disk_free_gb(path: Path = Path("/")) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)


def _canonical_counts(db_path: Path = DEFAULT_WORKSPACE_DB) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = ("project", "knowledge_page", "todo", "daily", "candidate")
        counts: dict[str, int] = {}
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in tables:
            if table in existing:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            else:
                counts[table] = 0
        return counts
    finally:
        conn.close()


def _quick_check(db_path: Path = DEFAULT_WORKSPACE_DB) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return str(conn.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        conn.close()


def _backup_once(path: Path, backup_dir: Path) -> str | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{path.name}.pre-projection.{stamp}"
    shutil.copy2(path, dest)
    return str(dest)


def rehydrate_project_projection(
    *,
    brain_path: Path = DEFAULT_BRAIN,
    layer_dir: Path = DEFAULT_LAYER_DIR,
    workspace_db: Path = DEFAULT_WORKSPACE_DB,
    backup_dir: Path | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Rehydrate project contexts from existing Brain consolidations."""
    disk_before = _disk_free_gb()
    if disk_before < MIN_FREE_GB:
        raise RuntimeError(f"disk free {disk_before:.2f}GB below {MIN_FREE_GB}GB threshold")

    quick = _quick_check(workspace_db)
    if quick != "ok":
        raise RuntimeError(f"workspace.db quick_check={quick}")

    canonical_before = _canonical_counts(workspace_db)
    catalog = default_project_catalog(str(workspace_db))
    formal_projects = catalog.list_projects()

    brain_data = json.loads(brain_path.read_text(encoding="utf-8"))
    consolidations = brain_data.get("project_consolidations") or {}
    knowledge_topics = brain_data.get("knowledge_topics") or {}
    metric_proposals = brain_data.get("metric_proposals") or {}

    builder_store = LockedJsonStore(layer_dir / "gpt_brain_project_builder.json")
    builder = ProjectBuilder(builder_store, catalog)
    before = builder.snapshot()

    backup_path = None
    if apply and backup_dir is not None:
        backup_path = _backup_once(builder_store.path, backup_dir)

    result = builder.rehydrate_from_brain(
        project_consolidations=consolidations,
        knowledge_topics=knowledge_topics,
        metric_proposals=metric_proposals,
        cleanup_formal_proposals=True,
    )
    after = builder.snapshot()

    data_layer = MetricDataLayer(LockedJsonStore(layer_dir / "gpt_brain_data_layer.json"))
    data_snapshot = data_layer.snapshot()

    core_names = {
        "运营导师体系",
        "运营实习生",
        "星火计划新兵营",
        "兵种培训",
        "干部管理人才盘点",
    }
    core_mapping: dict[str, Any] = {}
    mentor_id = "e81643f1-c06b-462f-8b12-9b6daea980f5"
    mentor_before = None
    if mentor_id in (builder_store.load() or {}).get("project_contexts", {}):
        mentor_before = len(
            (builder_store.load() or {})["project_contexts"][mentor_id].get("linked_source_versions") or []
        )
    for project in formal_projects:
        name = project.get("project_name")
        pid = project.get("project_id")
        ctx = builder.get_context(str(pid), redact=False) if pid else None
        core_mapping[name] = {
            "project_id": pid,
            "has_context": ctx is not None,
            "linked_source_versions": len((ctx or {}).get("linked_source_versions") or []),
            "is_core": name in core_names,
        }

    proposals = builder.list_proposals()
    unmatched = [
        {
            "project_name": item.get("project_name"),
            "fingerprint": item.get("fingerprint"),
        }
        for item in proposals.get("project_proposals", [])
    ]

    canonical_after = _canonical_counts(workspace_db)
    disk_after = _disk_free_gb()
    if disk_after < MIN_FREE_GB:
        raise RuntimeError(f"disk free after rehydrate {disk_after:.2f}GB below {MIN_FREE_GB}GB")

    mentor_after = None
    loaded = builder_store.load() or {}
    if mentor_id in loaded.get("project_contexts", {}):
        mentor_after = len(loaded["project_contexts"][mentor_id].get("linked_source_versions") or [])

    return {
        "status": "COMPLETED",
        "apply": apply,
        "disk_before_gb": round(disk_before, 2),
        "disk_after_gb": round(disk_after, 2),
        "workspace_quick_check": quick,
        "canonical_counts_before": canonical_before,
        "canonical_counts_after": canonical_after,
        "formal_project_catalog_count": len(formal_projects),
        "project_consolidations_source": len(consolidations),
        "project_contexts_before": before.get("context_count", 0),
        "project_contexts_after": after.get("context_count", 0),
        "project_proposals_before": before.get("proposal_count", 0),
        "project_proposals_after": after.get("proposal_count", 0),
        "rehydrate": result,
        "data_layer": data_snapshot,
        "core_project_mapping": core_mapping,
        "unassigned_or_unmatched": unmatched,
        "mentor_linked_sources_before": mentor_before,
        "mentor_linked_sources_after": mentor_after,
        "mentor_context_preserved": (
            mentor_before is None
            or mentor_after is None
            or mentor_after >= mentor_before
        ),
        "backup_added": backup_path,
        "formal_state_write": False,
        "at": utc_now(),
    }


__all__ = ["rehydrate_project_projection"]

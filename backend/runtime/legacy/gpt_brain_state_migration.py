"""Backup + additive migration for GPT Brain Bootstrap state.

Never resets source_states.  Never rewrites existing Knowledge/Project/Metric
objects.  Sidecar layer files are created next to (or beside a copy of) the
Brain JSON.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gpt_brain_common import LockedJsonStore, read_json, utc_now
from gpt_brain_data_layer import MetricDataLayer
from gpt_brain_project_builder import ProjectBuilder, default_project_catalog

DEFAULT_BRAIN = Path("/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json")
DEFAULT_BACKUP_DIR = Path("/home/ubuntu/paw-dev/paw-ksheet-worker/_brain_state_backups")
PRESERVED_SVIDS = ("485457392500@6", "485457403406@1", "485457405038@4")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup_brain_state(
    source: Path = DEFAULT_BRAIN,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"gpt_brain_bootstrap.json.{stamp}"
    shutil.copy2(source, dest)
    digest = _sha256(source)
    dest_digest = _sha256(dest)
    data = json.loads(source.read_text(encoding="utf-8"))
    bootstrap = data.get("bootstrap") or {}
    states = bootstrap.get("source_states") or {}
    preserved = {
        sid: {
            "state": (states.get(sid) or {}).get("state"),
            "result_refs": len((states.get(sid) or {}).get("result_refs") or []),
        }
        for sid in PRESERVED_SVIDS
    }
    report = {
        "backup_at": stamp,
        "source": str(source),
        "backup": str(dest),
        "bytes": source.stat().st_size,
        "sha256": digest,
        "backup_sha256": dest_digest,
        "checksum_match": digest == dest_digest,
        "schema_version": data.get("schema_version"),
        "bootstrap_status": bootstrap.get("status"),
        "run_id": bootstrap.get("run_id"),
        "total_sources": (bootstrap.get("frozen_queue") or {}).get("total_sources"),
        "preserved_first3": preserved,
        "knowledge_topics": len(data.get("knowledge_topics") or {}),
        "project_consolidations": len(data.get("project_consolidations") or {}),
        "metric_proposals": len(data.get("metric_proposals") or {}),
        "findings": len(data.get("findings") or {}),
    }
    (backup_dir / f"gpt_brain_bootstrap.json.{stamp}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (backup_dir / f"gpt_brain_bootstrap.json.{stamp}.sha256").write_text(
        f"{digest}  {source}\n{dest_digest}  {dest}\n", encoding="utf-8"
    )
    return report


def hydrate_layers(*, brain_data: dict[str, Any], layer_dir: Path) -> dict[str, Any]:
    data_layer = MetricDataLayer(LockedJsonStore(layer_dir / "gpt_brain_data_layer.json"))
    builder = ProjectBuilder(LockedJsonStore(layer_dir / "gpt_brain_project_builder.json"), default_project_catalog())
    imported = data_layer.import_brain_metric_proposals(brain_data.get("metric_proposals") or {})
    hydrated = builder.hydrate_from_brain(
        project_consolidations=brain_data.get("project_consolidations") or {},
        knowledge_topics=brain_data.get("knowledge_topics") or {},
        metric_proposals=brain_data.get("metric_proposals") or {},
    )
    return {
        "data_layer": imported,
        "project_builder": hydrated,
        "data_layer_snapshot": data_layer.snapshot(),
        "project_builder_snapshot": builder.snapshot(),
        "formal_state_write": False,
    }


def migrate_brain_state(
    *,
    source: Path = DEFAULT_BRAIN,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    apply_live: bool = False,
    layer_dir: Path | None = None,
) -> dict[str, Any]:
    backup = backup_brain_state(source, backup_dir)
    brain_data = json.loads(source.read_text(encoding="utf-8"))
    target_dir = layer_dir or (source.parent if apply_live else backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    hydration = hydrate_layers(brain_data=brain_data, layer_dir=target_dir)
    after = json.loads(source.read_text(encoding="utf-8"))
    preserved_ok = True
    notes = []
    for sid in PRESERVED_SVIDS:
        before = ((brain_data.get("bootstrap") or {}).get("source_states") or {}).get(sid) or {}
        now = ((after.get("bootstrap") or {}).get("source_states") or {}).get(sid) or {}
        if before.get("state") != now.get("state"):
            preserved_ok = False
            notes.append(f"{sid} state changed")
    report = {
        "migration_at": utc_now(),
        "apply_live": apply_live,
        "brain_json_mutated": False,
        "layer_dir": str(target_dir),
        "backup": backup,
        "hydration": hydration,
        "existing_bootstrap_state_preserved": preserved_ok,
        "notes": notes,
        "cursor_queue_untouched": True,
    }
    (backup_dir / f"migration_report_{backup['backup_at']}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


__all__ = ["backup_brain_state", "hydrate_layers", "migrate_brain_state"]

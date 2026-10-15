"""Aggregate server export and office ACK into one persistent dashboard state."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ksheet_pipeline_common import atomic_json, read_json, utc_now


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def aggregate(root: Path) -> dict:
    manifest = read_json(root / "manifest.json", {})
    worker = read_json(root / "worker-status.json", {})
    office = read_json(root / "office-pull-status.json", {})
    worker = worker if isinstance(worker, dict) else {}
    office = office if isinstance(office, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    stale_nonblocking_count = sum(
        1
        for entry in manifest.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("locator_status") == "STALE_LOCAL_STUB"
        and entry.get("blocking") is False
    )
    auth = worker.get("auth_status", "AUTH_UNKNOWN")
    export = worker.get("export_status", "EXPORT_IDLE")
    pull = office.get("pull_status", "PULL_PENDING")
    read = office.get("local_reader_status", "READ_PENDING")
    blocker = "NONE"
    overall = "STALE"
    if auth == "AUTH_UNKNOWN" and export == "EXPORT_IDLE" and pull == "PULL_PENDING":
        blocker = "STATUS_UNAVAILABLE"
    if auth in {"AUTH_REQUIRED", "QR_REQUIRED", "AUTH_EXPIRED"}:
        overall, blocker = "ACTION_REQUIRED", auth
    elif export == "EXPORT_FAILED":
        overall, blocker = "ACTION_REQUIRED", "EXPORT_FAILED"
    elif pull in {"PULL_FAILED", "PULL_ACK_MISSING"}:
        overall, blocker = "ACTION_REQUIRED", pull
    elif read in {"READ_FAILED", "READ_ACK_MISSING"}:
        overall, blocker = "DEGRADED", read
    elif export == "EXPORT_PARTIAL" or pull == "PULL_PARTIAL" or read == "READ_PARTIAL":
        overall = "WARNING"
    elif export == "EXPORT_SUCCESS" and pull == "PULL_SUCCESS" and read == "READ_SUCCESS":
        overall = "HEALTHY"
    finished = _parse(worker.get("last_export_finished_at"))
    deadline = finished + timedelta(minutes=75) if finished else None
    if finished and deadline < datetime.now(timezone.utc) and pull not in {"PULL_SUCCESS", "PULL_PARTIAL"}:
        overall, blocker = "ACTION_REQUIRED", "PULL_ACK_MISSING"
        pull = "PULL_ACK_MISSING"
    return {
        "auth_status": auth,
        "server_export": export,
        "office_pull": pull,
        "local_read": read,
        "overall": overall,
        "current_blocker": blocker,
        "stale_nonblocking_count": stale_nonblocking_count,
        "last_end_to_end_success_at": office.get("last_successful_pull_at") if overall == "HEALTHY" else None,
        "freshness": "current" if finished and finished.date() == datetime.now(timezone.utc).date() else "stale",
        "updated_at": utc_now(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv/paw/ksheet_exports"))
    args = parser.parse_args()
    value = aggregate(args.root)
    atomic_json(args.root / "pipeline-status.json", value)
    print(value)


if __name__ == "__main__":
    main()

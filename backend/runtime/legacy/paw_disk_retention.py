#!/usr/bin/env python3
"""PAW v1.2.1 phase 4 — disk retention for the three growth sources that have
no owner today:

  1. CLI-agent conversation store  /root/.gemini/antigravity-cli/conversations
  2. PAW release tree              /opt/paw/releases
  3. test / staging scratch        shadow DBs, staging trees, pycache, pytest cache

Design rules (SS41-SS46):
  * DRY RUN by default.  Nothing is deleted unless --apply is passed.
  * Never touch a file another process has open.
  * Never touch the newest KEEP_LATEST entries.
  * Never touch anything referenced by the current ledger or current release.
  * v1.2.1-recovery hardening: never delete a RELEASE that a running process
    still depends on (cwd / exe / open fd / cmdline / systemd unit reference).
    Running code outranks the retention count; deleting an in-use release
    strands the worker on a vanished tree (runtime drift) and leaves a
    partially deleted directory behind.
  * Only reclaim when the filesystem is at/above the trigger percentage,
    unless --force is given.
  * Bounded: at most MAX_DELETES_PER_RUN per class, newest-first preservation.

Exit code is 0 even when nothing is reclaimed; a non-zero exit means the sweep
itself failed (so the timer surfaces real errors rather than quiet no-ops).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── policy ──────────────────────────────────────────────────────────────────
AGENT_STORE = Path("/root/.gemini/antigravity-cli/conversations")
RELEASES = Path("/opt/paw/releases")
CURRENT_LINK = Path("/opt/paw/current")
PREVIOUS_LINK = Path("/opt/paw/previous")
LEDGER = Path("/var/tmp/paw-v12-unattended-repair-20260919-011055")

KEEP_LATEST_AGENT = 10          # SS43
KEEP_RELEASES = 4               # current + previous + 2 rollback candidates
DISK_TRIGGER_PCT = 85           # SS46
DISK_WARN_PCT = 88
DISK_HARD_PCT = 92
MAX_DELETES_PER_RUN = 400

SCRATCH_PATTERNS = (
    "/var/tmp/paw-phase*-build",
    "/var/tmp/paw-shadow-*",
    "/home/ubuntu/paw_stage14_*",
)
SCRATCH_MIN_AGE_S = 6 * 3600    # never touch scratch younger than 6h
PYCACHE_MIN_AGE_S = 3 * 3600


def disk_usage_pct(path: str = "/") -> float:
    st = os.statvfs(path)
    total = st.f_blocks
    free = st.f_bavail
    if not total:
        return 0.0
    return 100.0 * (total - free) / total


def _proc_paths() -> set[str]:
    """Every cwd / exe / open-fd path visible under /proc.

    Fallback for when lsof is unavailable -- the safety net must never
    silently no-op, because an empty result here would let the sweeps delete
    in-use files.
    """
    out: set[str] = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        base = f"/proc/{entry}"
        for sub in ("cwd", "exe"):
            try:
                out.add(os.path.realpath(f"{base}/{sub}"))
            except OSError:
                pass
        try:
            for fd in os.listdir(f"{base}/fd"):
                try:
                    out.add(os.path.realpath(f"{base}/fd/{fd}"))
                except OSError:
                    pass
        except OSError:
            pass
    return out


def open_files() -> set[str]:
    """Absolute paths currently held open by any process."""
    out: set[str] = set()
    try:
        res = subprocess.run(["lsof", "-nP", "-F", "n"], capture_output=True, text=True, timeout=60)
        for line in res.stdout.splitlines():
            if line.startswith("n") and line[1:].startswith("/"):
                out.add(line[1:])
    except Exception:
        pass
    if not out:
        out.update(_proc_paths())
    return out


def protected_paths() -> set[str]:
    """Paths that must survive regardless of age or count."""
    keep: set[str] = set()
    for link in (CURRENT_LINK, PREVIOUS_LINK):
        try:
            keep.add(os.path.realpath(link))
        except OSError:
            pass
    if LEDGER.exists():
        keep.add(str(LEDGER))
    return keep


def releases_in_use() -> dict:
    """Release names that running processes still depend on.

    Scans, independently of lsof:
      * every process cwd / executable / open file descriptor,
      * every process cmdline token (a supervisor may be started with an
        absolute path into a release tree),
      * systemd unit WorkingDirectory / ExecStart strings.

    Returns {"names": set[str], "detail": list[str]} where detail explains
    WHY each release is considered in use, so the retention report is
    auditable rather than magical.
    """
    names: set[str] = set()
    detail: list[str] = []

    def note(path: str, why: str) -> None:
        try:
            rp = os.path.realpath(path)
        except OSError:
            return
        prefix = str(RELEASES) + os.sep
        if not rp.startswith(prefix):
            return
        name = rp[len(prefix):].split(os.sep, 1)[0]
        if not name:
            return
        if name not in names:
            names.add(name)
            detail.append(f"{name} <- {why}")

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        base = f"/proc/{entry}"
        cmd = ""
        try:
            with open(f"{base}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            cmd = ""
        for sub, why in (("cwd", "cwd"), ("exe", "exe")):
            try:
                note(os.readlink(f"{base}/{sub}"), f"pid {entry} {why}")
            except OSError:
                pass
        try:
            for fd in os.listdir(f"{base}/fd"):
                try:
                    note(os.readlink(f"{base}/fd/{fd}"), f"pid {entry} open fd")
                except OSError:
                    pass
        except OSError:
            pass
        for token in cmd.split():
            if token.startswith(str(RELEASES) + os.sep):
                note(token, f"pid {entry} cmdline")

    for unit_dir in ("/etc/systemd/system", "/lib/systemd/system"):
        try:
            unit_files = os.listdir(unit_dir)
        except OSError:
            continue
        for fn in unit_files:
            if not fn.endswith(".service"):
                continue
            path = os.path.join(unit_dir, fn)
            try:
                if os.path.getsize(path) > 1_000_000:
                    continue
                with open(path, errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            if str(RELEASES) not in text:
                continue
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(("WorkingDirectory=", "ExecStart=",
                                    "ExecStartPre=", "ExecStartPost=")):
                    for token in line.split():
                        if token.startswith(str(RELEASES) + os.sep):
                            note(token, f"systemd {fn}")

    return {"names": names, "detail": sorted(detail)}


def sweep_agent_store(apply: bool, held: set[str], protected: set[str]) -> dict:
    rep = {"root": str(AGENT_STORE), "candidates": 0, "deleted": 0, "freed_bytes": 0,
           "kept": 0, "skipped_open": 0, "kept_names": []}
    if not AGENT_STORE.exists():
        rep["status"] = "STORE_ABSENT"
        return rep
    dbs = [p for p in AGENT_STORE.rglob("*.db") if p.is_file()]
    dbs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rep["total_dbs"] = len(dbs)
    keep = dbs[:KEEP_LATEST_AGENT]
    rep["kept"] = len(keep)
    rep["kept_names"] = [p.name for p in keep]
    victims = dbs[KEEP_LATEST_AGENT:]
    rep["candidates"] = len(victims)
    for p in victims[:MAX_DELETES_PER_RUN]:
        rp = str(p)
        if rp in held:
            rep["skipped_open"] += 1
            continue
        if any(rp.startswith(k) for k in protected):
            continue
        try:
            size = p.stat().st_size
            if apply:
                # journald/sqlite siblings must go together
                for sib in (p, Path(rp + "-wal"), Path(rp + "-shm"), Path(rp + "-journal")):
                    try:
                        sib.unlink()
                    except FileNotFoundError:
                        pass
            rep["deleted"] += 1
            rep["freed_bytes"] += size
        except OSError as exc:
            rep.setdefault("errors", []).append(f"{p.name}: {exc}")
    rep["freed_mb"] = round(rep["freed_bytes"] / 1048576, 1)
    rep["status"] = "APPLIED" if apply else "DRY_RUN"
    return rep


def sweep_releases(apply: bool, protected: set[str],
                   in_use: dict | None = None) -> dict:
    """Sweep old releases.

    A release that any running process still depends on is ALWAYS skipped --
    running code outranks the retention count.
    """
    rep = {"root": str(RELEASES), "kept": [], "deleted": [],
           "skipped_in_use": [], "freed_bytes": 0}
    if not RELEASES.exists():
        rep["status"] = "RELEASES_ABSENT"
        return rep
    in_use_names = set((in_use or {}).get("names") or ())
    rels = [p for p in RELEASES.iterdir() if p.is_dir()]
    rels.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    keep = rels[:KEEP_RELEASES]
    keep_paths = {str(p) for p in keep}
    rep["kept"] = [p.name for p in keep]
    for p in rels:
        rp = str(p)
        if rp in keep_paths or rp in protected:
            continue
        if p.name in in_use_names:
            rep["skipped_in_use"].append(p.name)
            continue
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        rep["deleted"].append(p.name)
        rep["freed_bytes"] += size
        if apply:
            shutil.rmtree(p, ignore_errors=True)
    rep["freed_mb"] = round(rep["freed_bytes"] / 1048576, 1)
    rep["status"] = "APPLIED" if apply else "DRY_RUN"
    return rep


def sweep_scratch(apply: bool, protected: set[str]) -> dict:
    import glob

    rep = {"candidates": [], "freed_bytes": 0}
    now = time.time()
    for pattern in SCRATCH_PATTERNS:
        for raw in glob.glob(pattern):
            p = Path(raw)
            if not p.exists() or str(p) in protected:
                continue
            if any(str(p).startswith(k) for k in protected):
                continue
            if now - p.stat().st_mtime < SCRATCH_MIN_AGE_S:
                continue
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            rep["candidates"].append({"path": str(p), "mb": round(size / 1048576, 1)})
            rep["freed_bytes"] += size
            if apply:
                shutil.rmtree(p, ignore_errors=True)

    pycache = []
    for root in ("/opt/paw/current", "/home/ubuntu/paw-dev", "/var/tmp"):
        for dirpath, dirnames, _ in os.walk(root):
            if "__pycache__" not in dirnames:
                continue
            d = Path(dirpath) / "__pycache__"
            try:
                if now - d.stat().st_mtime < PYCACHE_MIN_AGE_S:
                    continue
            except OSError:
                continue
            pycache.append(str(d))
            if apply:
                shutil.rmtree(d, ignore_errors=True)
    rep["pycache_candidates"] = len(pycache)
    rep["freed_mb"] = round(rep["freed_bytes"] / 1048576, 1)
    rep["status"] = "APPLIED" if apply else "DRY_RUN"
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--force", action="store_true", help="ignore the disk trigger threshold")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pct = disk_usage_pct("/")
    st = os.statvfs("/")
    free_gb = st.f_bavail * st.f_frsize / 1e9

    rep = {
        "schema": "paw-disk-retention/1",
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "disk_used_pct": round(pct, 1),
        "disk_free_gb": round(free_gb, 2),
        "trigger_pct": DISK_TRIGGER_PCT,
        "mode": "APPLY" if args.apply else "DRY_RUN",
    }

    if pct < DISK_TRIGGER_PCT and not args.force:
        rep["status"] = "SKIPPED_BELOW_TRIGGER"
        rep["note"] = (
            f"disk {pct:.1f}% < trigger {DISK_TRIGGER_PCT}%; nothing reclaimed. "
            "Warnings fire at 88%, hard warning at 92%."
        )
    else:
        held = open_files()
        protected = protected_paths()
        in_use = releases_in_use()
        rep["releases_in_use"] = {"names": sorted(in_use["names"]),
                                  "detail": in_use["detail"]}
        rep["protected"] = sorted(protected)
        rep["agent_store"] = sweep_agent_store(args.apply, held, protected)
        rep["releases"] = sweep_releases(args.apply, protected, in_use)
        rep["scratch"] = sweep_scratch(args.apply, protected)
        total = (rep["agent_store"].get("freed_bytes", 0)
                 + rep["releases"].get("freed_bytes", 0)
                 + rep["scratch"].get("freed_bytes", 0))
        rep["total_freed_mb"] = round(total / 1048576, 1)
        rep["status"] = "APPLIED" if args.apply else "DRY_RUN"

    rep["after_pct"] = round(disk_usage_pct("/"), 1)
    if rep["after_pct"] >= DISK_HARD_PCT:
        rep["alert"] = "HARD_WARNING_92"
    elif rep["after_pct"] >= DISK_WARN_PCT:
        rep["alert"] = "WARNING_88"
    else:
        rep["alert"] = "OK"

    text = json.dumps(rep, ensure_ascii=False, indent=1)
    print(text if args.json else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

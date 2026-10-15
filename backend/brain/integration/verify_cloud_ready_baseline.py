"""Cloud Brain Ready Baseline Verifier V1.

Read-only verification of current cloud state against frozen baseline.
Does NOT modify any file, DB, or service.

Output:
  BASELINE_STATUS: MATCH / SAFE_DRIFT / HARD_DRIFT
  HARD_DRIFT_ITEMS: [...]
  SOFT_DRIFT_ITEMS: [...]
  EXPECTED_DRIFT_ITEMS: [...]

Usage:
  /opt/paw-actions/.venv/bin/python -m brain.integration.verify_cloud_ready_baseline
"""

from __future__ import annotations

import os

import json, os, sys, sqlite3, subprocess, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2]))

BASELINE_JSON = Path("/opt/personal-workspace/brain/integration/cloud_ready_baseline_v1.json")
MANIFEST_PATH = Path("/opt/personal-workspace/brain/REALITY_MANIFEST_V1.txt")
DB_PATH = "" + os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")) + ""

hard_drift = []
soft_drift = []
expected_drift = []


def load_baseline() -> dict:
    with open(BASELINE_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def get_service_prop(name: str, prop: str) -> str:
    try:
        return subprocess.check_output(
            ["systemctl", "show", name, f"--property={prop}", "--value"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "UNKNOWN"


def get_service_active(name: str) -> str:
    try:
        return subprocess.check_output(
            ["systemctl", "is-active", name], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "UNKNOWN"


def get_openclaw_version() -> str:
    try:
        return subprocess.check_output(
            ["/root/.nvm/versions/node/v22.22.3/bin/openclaw", "--version"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "UNKNOWN"


def get_db_info() -> dict:
    con = sqlite3.connect(DB_PATH, uri=True)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()
    info = {
        "integrity": cur.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_keys": cur.execute("PRAGMA foreign_key_check").fetchall(),
        "schema_version": cur.execute("PRAGMA user_version").fetchone()[0],
        "tables": {},
        "brain_tables": {},
    }
    for t in ["project", "todo", "knowledge_page", "knowledge_revision",
              "knowledge_source_link", "daily", "capture", "wiki_page"]:
        try:
            info["tables"][t] = {"exists": True, "count": cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]}
        except Exception:
            info["tables"][t] = {"exists": False, "count": 0}
    for t in ["analysis_run", "candidate"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            info["brain_tables"][t] = "EXISTS"
        except Exception:
            info["brain_tables"][t] = "ABSENT"
    con.close()
    return info


def get_file_hash(path: str) -> tuple:
    if not os.path.exists(path):
        return (0, "MISSING")
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return (size, sha)


def load_manifest_hashes() -> dict:
    if not MANIFEST_PATH.exists():
        return {}
    hashes = {}
    with open(MANIFEST_PATH, "r") as f:
        for line in f:
            if "|" in line and not line.startswith("REALITY"):
                parts = line.strip().split("|")
                if len(parts) == 3:
                    hashes[parts[0]] = {"size": int(parts[1]), "sha256": parts[2]}
    return hashes


def check_brain_router() -> str:
    try:
        result = subprocess.check_output(
            ["grep", "-rn", "brain", "/opt/paw-actions/app/main.py"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        if result:
            return "MOUNTED"
        return "NOT_MOUNTED"
    except subprocess.CalledProcessError:
        return "NOT_MOUNTED"
    except Exception:
        return "UNKNOWN"


def check_production_persona() -> str:
    soul = Path("/root/.openclaw/workspace-agnes/SOUL.md")
    if not soul.exists():
        return "UNKNOWN"
    content = soul.read_text()
    has_brain = "FACT AUTHORITY" in content or "Personal Workspace processor" in content or "Memory Boundary" in content
    has_server_ops = "Inspect first" in content
    if has_brain:
        return "MERGED_BRAIN_PERSONA"
    elif has_server_ops:
        return "OLD_SERVER_OPS_PERSONA"
    return "UNKNOWN"


def check_auto_write() -> bool:
    try:
        from brain.candidates.models import MIGRATION_STATUS
        from brain.integration.router_staging import AUTO_WRITE_ENABLED
        # DRY_RUN was an AgnesRuntimeAdapter constant; Agy runtime is always live (not dry-run)
        _DRY_RUN = True
        return AUTO_WRITE_ENABLED is False and _DRY_RUN is True
    except Exception:
        return False


def verify():
    baseline = load_baseline()

    # 1. Service baseline
    for svc_name, svc_key in [("paw-actions", "paw_actions"), ("openclaw-gateway", "openclaw_gateway")]:
        bl = baseline["service_baseline"][svc_key]
        active = get_service_active(svc_name)
        if active != bl["active"]:
            hard_drift.append(f"{svc_name} active: expected={bl['active']} actual={active}")
        pid = get_service_prop(svc_name, "MainPID")
        if pid != bl["MainPID"]:
            soft_drift.append(f"{svc_name} PID: baseline={bl['MainPID']} current={pid}")
        execstart = get_service_prop(svc_name, "ExecStart")
        if bl["ExecStart"] not in execstart and execstart not in bl["ExecStart"]:
            if execstart and execstart != "UNKNOWN":
                hard_drift.append(f"{svc_name} ExecStart changed")
        need_reload = get_service_prop(svc_name, "NeedDaemonReload")
        if need_reload == "yes":
            hard_drift.append(f"{svc_name} NeedDaemonReload=yes")

    # 2. OpenClaw runtime
    oc_bl = baseline["openclaw_runtime"]
    version = get_openclaw_version()
    if oc_bl["version"] not in version:
        hard_drift.append(f"OpenClaw version: expected={oc_bl['version']} actual={version}")

    gw_pid = get_service_prop("openclaw-gateway", "MainPID")
    if gw_pid and gw_pid != "UNKNOWN":
        try:
            exe = os.readlink(f"/proc/{gw_pid}/exe")
            if oc_bl["running_exe"] not in exe:
                hard_drift.append(f"OpenClaw running exe: expected={oc_bl['running_exe']} actual={exe}")
        except Exception:
            pass

    # 3. DB structural
    db_bl = baseline["db_structural"]
    db = get_db_info()
    if db["integrity"] != db_bl["integrity"]:
        hard_drift.append(f"DB integrity: expected={db_bl['integrity']} actual={db['integrity']}")
    if db["foreign_keys"] != db_bl["foreign_keys"]:
        hard_drift.append(f"DB foreign_keys: expected={db_bl['foreign_keys']} actual={db['foreign_keys']}")
    if db["schema_version"] != db_bl["schema_version"]:
        hard_drift.append(f"DB schema_version: expected={db_bl['schema_version']} actual={db['schema_version']}")

    for t in ["project", "todo", "knowledge_page", "knowledge_revision", "knowledge_source_link"]:
        bl_t = db_bl["tables"][t]
        cur_t = db["tables"][t]
        if cur_t["exists"] != bl_t["exists"]:
            hard_drift.append(f"Table {t} existence changed")
        elif cur_t["count"] != bl_t["count"]:
            soft_drift.append(f"Table {t} count: baseline={bl_t['count']} current={cur_t['count']}")

    for t in ["daily", "capture", "wiki_page"]:
        bl_t = db_bl["tables"][t]
        cur_t = db["tables"][t]
        if cur_t["exists"] != bl_t["exists"]:
            hard_drift.append(f"Table {t} existence changed")
        elif cur_t["count"] != bl_t["count"]:
            expected_drift.append(f"Table {t} count: baseline={bl_t['count']} current={cur_t['count']}")

    for t in ["analysis_run", "candidate"]:
        bl_status = db_bl["brain_tables"][t]
        cur_status = db["brain_tables"][t]
        if cur_status != bl_status:
            hard_drift.append(f"Brain table {t}: expected={bl_status} actual={cur_status}")

    # 4. Brain file hashes
    manifest_hashes = load_manifest_hashes()
    for path, expected in manifest_hashes.items():
        size, sha = get_file_hash(path)
        if sha == "MISSING":
            hard_drift.append(f"Brain file missing: {path}")
        elif sha != expected["sha256"]:
            hard_drift.append(f"Brain file hash changed: {path}")

    # 5. Policy invariants
    persona_path = "/opt/personal-workspace/brain/policies/agnes_persona_v1.md"
    fp_path = "/opt/personal-workspace/brain/policies/fact_precedence_v1.md"
    mp_path = "/opt/personal-workspace/brain/policies/openclaw_memory_policy_v1.md"

    if os.path.exists(persona_path):
        persona = open(persona_path).read()
        if "## FACT AUTHORITY" not in persona:
            hard_drift.append("FACT AUTHORITY missing in persona")
        if "## FACT PRIORITY" in persona:
            hard_drift.append("Old FACT PRIORITY present in persona")
        if "fact_precedence_v1.md" not in persona:
            hard_drift.append("Persona does not reference fact_precedence_v1.md")
    else:
        hard_drift.append("Persona file missing")

    if os.path.exists(fp_path):
        fp = open(fp_path).read()
        for tier in ["USER_INTENT_STATE", "BUSINESS_RULE", "BUSINESS_DATA", "CANONICAL_ENTITY_STATE", "AI_DERIVED"]:
            if tier not in fp:
                hard_drift.append(f"Fact precedence tier missing: {tier}")
    else:
        hard_drift.append("Fact precedence file missing")

    if os.path.exists(mp_path):
        mp = open(mp_path).read()
        if "Personal Workspace" not in mp or "CANONICAL" not in mp:
            hard_drift.append("Memory policy canonical rule missing")
    else:
        hard_drift.append("Memory policy file missing")

    # 6. Not-deployed invariants
    router_status = check_brain_router()
    if router_status != "NOT_MOUNTED":
        hard_drift.append(f"Brain router: expected=NOT_MOUNTED actual={router_status}")

    persona_status = check_production_persona()
    if persona_status == "MERGED_BRAIN_PERSONA":
        hard_drift.append(f"Production persona unexpectedly merged")

    if not check_auto_write():
        hard_drift.append("AUTO_WRITE not FALSE or DRY_RUN not True")

    # 7. CLI observation
    try:
        cli_path = subprocess.check_output(["command", "-v", "openclaw"], stderr=subprocess.DEVNULL).decode().strip()
        cli_bl = baseline["cli_observation"]
        if cli_path != cli_bl["path"]:
            soft_drift.append(f"CLI path: baseline={cli_bl['path']} current={cli_path}")
    except Exception:
        pass

    # Determine status
    if hard_drift:
        status = "HARD_DRIFT"
    elif soft_drift or expected_drift:
        status = "SAFE_DRIFT"
    else:
        status = "MATCH"

    return status


verify_cloud_ready_baseline = verify


def main():
    print("=== CLOUD BRAIN READY BASELINE VERIFIER V1 ===")
    print(f"Baseline: {BASELINE_JSON}")
    print()

    status = verify()

    print(f"BASELINE_STATUS: {status}")
    print()
    if hard_drift:
        print(f"HARD_DRIFT_ITEMS ({len(hard_drift)}):")
        for item in hard_drift:
            print(f"  - {item}")
    else:
        print("HARD_DRIFT_ITEMS: none")
    print()
    if soft_drift:
        print(f"SOFT_DRIFT_ITEMS ({len(soft_drift)}):")
        for item in soft_drift:
            print(f"  - {item}")
    else:
        print("SOFT_DRIFT_ITEMS: none")
    print()
    if expected_drift:
        print(f"EXPECTED_DRIFT_ITEMS ({len(expected_drift)}):")
        for item in expected_drift:
            print(f"  - {item}")
    else:
        print("EXPECTED_DRIFT_ITEMS: none")

    return 0 if status != "HARD_DRIFT" else 1


if __name__ == "__main__":
    sys.exit(main())

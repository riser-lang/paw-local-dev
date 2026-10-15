#!/usr/bin/env python3
"""R15-A.2 — WPS Cloud Inventory V1.

SQLite-based persistent inventory with stable numeric object_id identity.
Uses R15-A.1 proven DOM selectors: .item-container + aria-label + data-key.
"""
import os, sys, time, json, hashlib, sqlite3, subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
FOLDER_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"
GROUP_ID = "3009705063"
FOLDER_ID = "485481025119"
FOLDER_NAME = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e"
DB_PATH = "/srv/paw/ksheet_exports/cloud_inventory/wps_inventory_v1.sqlite3"
SUMMARY_PATH = "/srv/paw/ksheet_exports/cloud_inventory/latest_inventory_summary.json"
COLLECTOR_VERSION = "v1.0"
WHITELIST = ["id", "fileid", "parentid", "fname", "name", "type", "ext", "fver", "fsize", "mtime", "ctime", "groupid", "parent_name", "group_name"]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_avail_mem_mb():
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def compute_fingerprint(obj):
    """Deterministic SHA256 fingerprint from stable metadata."""
    canonical = json.dumps({
        "object_id": obj.get("object_id"),
        "object_type": obj.get("object_type"),
        "parent_id": obj.get("parent_id"),
        "display_name": obj.get("display_name"),
        "version": obj.get("version"),
        "size": obj.get("size"),
        "mtime": obj.get("mtime"),
    }, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def init_db(db_path):
    """Initialize SQLite database with 3 tables."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS inventory_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT,
        finished_at TEXT,
        status TEXT,
        group_id TEXT,
        scope_folder_id TEXT,
        object_count INTEGER DEFAULT 0,
        added_count INTEGER DEFAULT 0,
        changed_count INTEGER DEFAULT 0,
        unchanged_count INTEGER DEFAULT 0,
        scope_missing_count INTEGER DEFAULT 0,
        collector_version TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS inventory_objects (
        provider TEXT,
        group_id TEXT,
        object_id TEXT,
        object_type TEXT,
        parent_id TEXT,
        parent_relation_proof TEXT,
        display_name TEXT,
        filename TEXT,
        extension TEXT,
        version TEXT,
        size TEXT,
        mtime TEXT,
        ctime TEXT,
        first_seen_at TEXT,
        last_seen_at TEXT,
        last_seen_run_id TEXT,
        is_currently_seen INTEGER DEFAULT 1,
        metadata_source TEXT,
        metadata_fingerprint TEXT,
        PRIMARY KEY (provider, group_id, object_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS inventory_observations (
        run_id TEXT,
        observed_at TEXT,
        group_id TEXT,
        scope_folder_id TEXT,
        object_id TEXT,
        object_type TEXT,
        parent_id TEXT,
        display_name TEXT,
        version TEXT,
        size TEXT,
        mtime TEXT,
        metadata_fingerprint TEXT,
        observation_class TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_obj_group_parent ON inventory_objects(group_id, parent_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_obj_last_run ON inventory_objects(last_seen_run_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_obj_type ON inventory_objects(object_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_obs_run ON inventory_observations(run_id)")
    # R15-A.2H.1 — Mirror Exclusion Policy: backward-compatible migration
    def _add_column(table, col, col_type, default):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type} DEFAULT {default}")
        except Exception:
            pass  # Column already exists
    _add_column("inventory_objects", "mirror_policy", "TEXT", "'INCLUDED'")
    _add_column("inventory_objects", "mirror_exclusion_rule", "TEXT", "NULL")
    _add_column("inventory_objects", "mirror_exclusion_reason", "TEXT", "NULL")
    _add_column("inventory_objects", "mirror_policy_evaluated_at", "TEXT", "NULL")
    _add_column("inventory_observations", "mirror_policy", "TEXT", "'INCLUDED'")
    _add_column("inventory_observations", "mirror_exclusion_rule", "TEXT", "NULL")
    # R15-A.3H/A.4 — File type registry and mirror backend
    _add_column("inventory_objects", "file_family", "TEXT", "'UNKNOWN'")
    _add_column("inventory_objects", "file_type", "TEXT", "'UNKNOWN'")
    _add_column("inventory_objects", "mirror_backend_candidate", "TEXT", "'NEEDS_CLASSIFICATION'")
    _add_column("inventory_objects", "enumeration_backend", "TEXT", "'DOM_ITEM_CONTAINER'")
    _add_column("inventory_observations", "file_family", "TEXT", "'UNKNOWN'")
    _add_column("inventory_observations", "file_type", "TEXT", "'UNKNOWN'")
    _add_column("inventory_observations", "enumeration_backend", "TEXT", "'DOM_ITEM_CONTAINER'")
    conn.commit()
    return conn

# R15-A.2H.1 — WPS Mirror Exclusion Policy
MIRROR_EXCLUSION_RULES = [
    {
        "rule_id": "WPS_MIRROR_EXCLUSION_RULE_001",
        "rule_source": "EXPLICIT_USER_DECISION",
        "match_mode": "NAME_CONTAINS",
        "match_value": "FY26运营导师带教情况表",
        "action": "INVENTORY_ONLY_NO_LOCAL_MIRROR",
        "reason": "USER_DECLARED_NO_BUSINESS_VALUE",
    },
]

def evaluate_mirror_policy(display_name, filename=None):
    """Evaluate mirror policy based on deterministic name matching.
    Returns (mirror_policy, rule_id, reason) tuple.
    """
    import unicodedata
    names_to_check = []
    if display_name:
        names_to_check.append(unicodedata.normalize("NFC", display_name.strip()))
    if filename:
        names_to_check.append(unicodedata.normalize("NFC", filename.strip()))
    for rule in MIRROR_EXCLUSION_RULES:
        match_value = unicodedata.normalize("NFC", rule["match_value"].strip())
        for name in names_to_check:
            if match_value in name:
                return ("EXCLUDED", rule["rule_id"], rule["reason"])
    return ("INCLUDED", None, None)

def is_mirror_allowed(object_record):
    """Check if an object is allowed to be mirrored locally.
    Returns True only if ALL of the following hold:
      A. mirror_policy == "INCLUDED"
      B. mirror_policy_evaluated_at exists, non-null, and parses as valid ISO-8601 timestamp
      C. mirror_backend_candidate exists and is registered in MIRROR_BACKEND_STATUS_REGISTRY
      D. backend acquisition_ready == True (BLOCKED/UNVALIDATED/UNKNOWN → False)
      E. USER_EXCLUSION (EXCLUDED) → False
      F. MEDIA_EXCLUDED → False
    Fail-closed: missing/unknown/null/malformed → False.
    """
    from wps_file_registry import is_registered_mirror_backend, is_backend_acquisition_ready
    if isinstance(object_record, dict):
        policy = object_record.get("mirror_policy")
        evaluated_at = object_record.get("mirror_policy_evaluated_at")
        backend = object_record.get("mirror_backend_candidate")
    else:
        policy = getattr(object_record, "mirror_policy", None)
        evaluated_at = getattr(object_record, "mirror_policy_evaluated_at", None)
        backend = getattr(object_record, "mirror_backend_candidate", None)
    # Rule A/E/F: policy must be INCLUDED (EXCLUDED, MEDIA_EXCLUDED, None, UNKNOWN all → False)
    if policy != "INCLUDED":
        return False
    # Rule B: evaluated_at must exist, be non-empty, and parse as valid timestamp
    if not evaluated_at:
        return False
    try:
        from datetime import datetime
        datetime.fromisoformat(str(evaluated_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    # Rule C: backend must be registered in the status registry
    if not backend or not is_registered_mirror_backend(backend):
        return False
    # Rule D: backend must be acquisition-ready
    if not is_backend_acquisition_ready(backend):
        return False
    return True


def collect_entries(page):
    """Collect entries from DOM using .item-container selector."""
    entries = page.evaluate("""
        () => {
            var containers = document.querySelectorAll('.item-container');
            var entries = [];
            for (var el of containers) {
                var ariaLabel = el.getAttribute('aria-label') || '';
                var dataKey = el.getAttribute('data-key') || '';
                if (!ariaLabel || !dataKey) continue;
                var entry = {display_name: ariaLabel, data_key: dataKey};
                if (dataKey.startsWith('file_')) {
                    entry.object_type = 'file';
                    entry.object_id = dataKey.substring(5);
                } else if (dataKey.startsWith('folder_')) {
                    entry.object_type = 'folder';
                    entry.object_id = dataKey.substring(7);
                } else {
                    entry.object_type = 'unknown';
                    entry.object_id = dataKey;
                }
                entries.push(entry);
            }
            return entries;
        }
    """)
    return entries


def enrich_from_state(page, object_id):
    """Try to enrich object metadata from __INITIAL_STATE__."""
    state = page.evaluate("() => window.__INITIAL_STATE__")
    def recursive_search(obj, target_id, path="", parent=None, depth=0):
        if depth > 12:
            return None
        if isinstance(obj, dict):
            if str(obj.get("fileid", "")) == str(target_id):
                return {"obj": obj, "parent": parent}
            for k, v in obj.items():
                result = recursive_search(v, target_id, f"{path}.{k}", obj, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                result = recursive_search(item, target_id, f"{path}[{i}]", obj, depth + 1)
                if result:
                    return result
        return None
    result = recursive_search(state, object_id)
    if result:
        obj = result["obj"]
        parent = result["parent"]
        enriched = {}
        # Extract from obj top-level and from nested 'data' dict if present
        sources = [obj]
        if isinstance(obj.get("data"), dict):
            sources.append(obj["data"])
        for src in sources:
            for wk in WHITELIST:
                if wk in src and wk not in enriched:
                    enriched[wk] = src[wk]
            if parent and wk in parent:
                enriched[f"parent_{wk}"] = parent[wk]
        return enriched
    return {}


def run_inventory(conn, run_id, page):
    """Execute one inventory run."""
    c = conn.cursor()
    started_at = utc_now()
    c.execute("INSERT INTO inventory_runs (run_id, started_at, status, group_id, scope_folder_id, collector_version) VALUES (?, ?, 'RUNNING', ?, ?, ?)",
              (run_id, started_at, GROUP_ID, FOLDER_ID, COLLECTOR_VERSION))
    conn.commit()

    # FIX 4 — FAIL-CLOSED: validate scope before modifying inventory
    breadcrumb_found = page.evaluate("""
        (folderName) => {
            var breadcrumbs = document.querySelectorAll('[class*="breadcrumb"], [class*="Breadcrumb"], [class*="crumb"], [class*="path"]');
            for (var el of breadcrumbs) {
                if (el.textContent.indexOf(folderName) >= 0) return true;
            }
            return false;
        }
    """, FOLDER_NAME)
    scope_url_valid = FOLDER_ID in page.url

    # Collect entries
    entries = collect_entries(page)
    observed_at = utc_now()

    # FIX 4 — If scope validation fails, record blocked run and do NOT modify inventory
    if not scope_url_valid or not breadcrumb_found or len(entries) == 0:
        finished_at = utc_now()
        c.execute("UPDATE inventory_runs SET finished_at=?, status='BLOCKED', object_count=0, added_count=0, changed_count=0, unchanged_count=0, scope_missing_count=0 WHERE run_id=?",
                  (finished_at, run_id))
        conn.commit()
        return {"run_id": run_id, "status": "BLOCKED", "object_count": 0, "added": 0, "changed": 0, "unchanged": 0, "scope_missing": 0, "breadcrumb_found": breadcrumb_found, "observation_classes": {}}

    # FIX 1 — TRUE SCOPE ISOLATION: only objects whose previous parent_id == current scope_folder_id
    c.execute("SELECT object_id, display_name, parent_id, version, size, mtime, metadata_fingerprint, object_type, is_currently_seen FROM inventory_objects WHERE group_id=? AND parent_id=?",
              (GROUP_ID, FOLDER_ID))
    scope_existing = {row[0]: {"display_name": row[1], "parent_id": row[2], "version": row[3], "size": row[4], "mtime": row[5], "fingerprint": row[6], "object_type": row[7], "is_currently_seen": row[8]} for row in c.fetchall()}

    # FIX 2 — Get ALL objects by identity (including is_currently_seen=0) for reappearance
    c.execute("SELECT object_id, display_name, parent_id, version, size, mtime, metadata_fingerprint, object_type, is_currently_seen FROM inventory_objects WHERE group_id=?",
              (GROUP_ID,))
    all_existing = {row[0]: {"display_name": row[1], "parent_id": row[2], "version": row[3], "size": row[4], "mtime": row[5], "fingerprint": row[6], "object_type": row[7], "is_currently_seen": row[8]} for row in c.fetchall()}

    seen_ids = set()
    added = 0
    changed = 0
    unchanged = 0
    observation_classes = {}

    for entry in entries:
        obj_id = entry["object_id"]
        seen_ids.add(obj_id)

        # Enrich from state
        enriched = enrich_from_state(page, obj_id) or {}

        # Build object record
        obj = {
            "provider": "wps",
            "group_id": GROUP_ID,
            "object_id": obj_id,
            "object_type": entry["object_type"],
            "parent_id": FOLDER_ID,
            "parent_relation_proof": "EXPLICIT_METADATA" if enriched.get("parentid") else "DIRECTORY_SCOPE",
            "display_name": entry["display_name"],
            "filename": enriched.get("fname"),
            "extension": enriched.get("ext"),
            "version": enriched.get("fver"),
            "size": enriched.get("fsize"),
            "mtime": enriched.get("mtime"),
            "ctime": enriched.get("ctime"),
            "metadata_source": "DOM+STATE" if enriched else "DOM_ONLY",
        }

        # Override parent_id if explicit metadata available
        if enriched.get("parentid"):
            obj["parent_id"] = str(enriched["parentid"])

        fp = compute_fingerprint(obj)

        # FIX 2+3 — Determine observation class with priority
        if obj_id in all_existing:
            ex = all_existing[obj_id]
            was_seen = ex.get("is_currently_seen", 0) == 1
            if not was_seen:
                obs_class = "REAPPEARED"
                changed += 1
            elif ex["fingerprint"] == fp:
                obs_class = "UNCHANGED"
                unchanged += 1
            elif ex["parent_id"] != obj["parent_id"]:
                obs_class = "MOVED"
                changed += 1
            elif ex["display_name"] != obj["display_name"]:
                obs_class = "RENAMED"
                changed += 1
            elif ex["version"] is not None and obj.get("version") is not None and str(ex["version"]) != str(obj.get("version")):
                obs_class = "VERSION_CHANGED"
                changed += 1
            else:
                obs_class = "METADATA_CHANGED"
                changed += 1
        else:
            obs_class = "NEW_OBJECT"
            added += 1

        observation_classes[obj_id] = obs_class
        # R15-A.2H.1 — Mirror Policy Evaluation (after Stable ID reconciliation)
        mirror_policy, mirror_rule, mirror_reason = evaluate_mirror_policy(obj["display_name"], obj.get("filename"))
        # Insert observation
        c.execute("INSERT INTO inventory_observations (run_id, observed_at, group_id, scope_folder_id, object_id, object_type, parent_id, display_name, version, size, mtime, metadata_fingerprint, observation_class, mirror_policy, mirror_exclusion_rule) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, observed_at, GROUP_ID, FOLDER_ID, obj_id, obj["object_type"], obj["parent_id"], obj["display_name"], obj.get("version"), obj.get("size"), obj.get("mtime"), fp, obs_class, mirror_policy, mirror_rule))

        # FIX 2 — Always UPDATE if object exists (never INSERT duplicate)
        if obj_id in all_existing:
            c.execute("""UPDATE inventory_objects SET object_type=?, parent_id=?, parent_relation_proof=?, display_name=?, filename=?, extension=?, version=?, size=?, mtime=?, ctime=?, last_seen_at=?, last_seen_run_id=?, is_currently_seen=1, metadata_source=?, metadata_fingerprint=?, mirror_policy=?, mirror_exclusion_rule=?, mirror_exclusion_reason=?, mirror_policy_evaluated_at=? WHERE provider=? AND group_id=? AND object_id=?""",
                      (obj["object_type"], obj["parent_id"], obj["parent_relation_proof"], obj["display_name"], obj.get("filename"), obj.get("extension"), obj.get("version"), obj.get("size"), obj.get("mtime"), obj.get("ctime"), observed_at, run_id, obj["metadata_source"], fp, mirror_policy, mirror_rule, mirror_reason, observed_at, obj["provider"], obj["group_id"], obj_id))
        else:
            c.execute("""INSERT INTO inventory_objects (provider, group_id, object_id, object_type, parent_id, parent_relation_proof, display_name, filename, extension, version, size, mtime, ctime, first_seen_at, last_seen_at, last_seen_run_id, is_currently_seen, metadata_source, metadata_fingerprint, mirror_policy, mirror_exclusion_rule, mirror_exclusion_reason, mirror_policy_evaluated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (obj["provider"], obj["group_id"], obj_id, obj["object_type"], obj["parent_id"], obj["parent_relation_proof"], obj["display_name"], obj.get("filename"), obj.get("extension"), obj.get("version"), obj.get("size"), obj.get("mtime"), obj.get("ctime"), observed_at, observed_at, run_id, 1, obj["metadata_source"], fp, mirror_policy, mirror_rule, mirror_reason, observed_at))

    # FIX 1 — Mark missing objects ONLY from scope_existing (same parent_id == scope_folder_id)
    scope_missing = 0
    for ex_id in scope_existing:
        if ex_id not in seen_ids and scope_existing[ex_id].get("is_currently_seen", 0) == 1:
            scope_missing += 1
            # FIX 5 — Include object_type in NOT_SEEN_IN_SCOPE observation
            c.execute("UPDATE inventory_objects SET is_currently_seen=0 WHERE group_id=? AND object_id=?", (GROUP_ID, ex_id))
            c.execute("INSERT INTO inventory_observations (run_id, observed_at, group_id, scope_folder_id, object_id, object_type, parent_id, display_name, version, size, mtime, metadata_fingerprint, observation_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (run_id, observed_at, GROUP_ID, FOLDER_ID, ex_id, scope_existing[ex_id].get("object_type", ""), scope_existing[ex_id].get("parent_id", ""), scope_existing[ex_id].get("display_name", ""), scope_existing[ex_id].get("version"), scope_existing[ex_id].get("size"), scope_existing[ex_id].get("mtime"), scope_existing[ex_id].get("fingerprint"), "NOT_SEEN_IN_SCOPE"))

    finished_at = utc_now()
    status = "SUCCESS"
    c.execute("UPDATE inventory_runs SET finished_at=?, status=?, object_count=?, added_count=?, changed_count=?, unchanged_count=?, scope_missing_count=? WHERE run_id=?",
              (finished_at, status, len(entries), added, changed, unchanged, scope_missing, run_id))
    conn.commit()

    return {"run_id": run_id, "status": status, "object_count": len(entries), "added": added, "changed": changed, "unchanged": unchanged, "scope_missing": scope_missing, "breadcrumb_found": breadcrumb_found, "observation_classes": observation_classes}


def verify_db(conn):
    """Verify database contents."""
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM inventory_runs")
    run_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_objects")
    obj_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_observations")
    obs_count = c.fetchone()[0]
    c.execute("SELECT object_id, object_type, parent_id, parent_relation_proof, display_name, version, size, metadata_fingerprint, first_seen_at, last_seen_at, is_currently_seen FROM inventory_objects")
    objects = c.fetchall()
    return run_count, obj_count, obs_count, objects


def save_summary(run_result, objects):
    """Save sanitized summary JSON."""
    summary = {
        "run_id": run_result["run_id"],
        "timestamp": utc_now(),
        "group_id": GROUP_ID,
        "scope_folder_id": FOLDER_ID,
        "object_count": run_result["object_count"],
        "added_count": run_result["added"],
        "changed_count": run_result["changed"],
        "unchanged_count": run_result["unchanged"],
        "scope_missing_count": run_result["scope_missing"],
        "status": run_result["status"],
        "objects": [{"object_id": o[0], "object_type": o[1], "parent_id": o[2], "display_name": o[4], "version": o[5], "size": o[6], "change_class": run_result.get("observation_classes", {}).get(o[0], "CURRENT")} for o in objects],
    }
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def main():
    mem_before = get_avail_mem_mb()
    print(f"[INVENTORY_V1] Starting R15-A.2", flush=True)
    print(f"[INVENTORY_V1] DB: {DB_PATH}", flush=True)
    print(f"[INVENTORY_V1] AVAILABLE_MEMORY_BEFORE={mem_before}MB", flush=True)

    conn = init_db(DB_PATH)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            executable_path=BROWSER_EXECUTABLE,
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--no-first-run", "--no-default-browser-check",
                  "--window-position=0,0", "--window-size=1440,900"],
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # RUN #1
        run1_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_1"
        print(f"\n=== RUN #1: {run1_id} ===", flush=True)
        page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(20)
        r1 = run_inventory(conn, run1_id, page)
        print(f"RUN_1_STATUS={r1['status']}", flush=True)
        print(f"RUN_1_OBJECT_COUNT={r1['object_count']}", flush=True)
        print(f"RUN_1_ADDED={r1['added']}", flush=True)
        print(f"RUN_1_CHANGED={r1['changed']}", flush=True)
        print(f"RUN_1_UNCHANGED={r1['unchanged']}", flush=True)
        print(f"RUN_1_SCOPE_MISSING={r1['scope_missing']}", flush=True)

        # RUN #2
        run2_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_2"
        print(f"\n=== RUN #2: {run2_id} ===", flush=True)
        page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(20)
        r2 = run_inventory(conn, run2_id, page)
        print(f"RUN_2_STATUS={r2['status']}", flush=True)
        print(f"RUN_2_OBJECT_COUNT={r2['object_count']}", flush=True)
        print(f"RUN_2_ADDED={r2['added']}", flush=True)
        print(f"RUN_2_CHANGED={r2['changed']}", flush=True)
        print(f"RUN_2_UNCHANGED={r2['unchanged']}", flush=True)
        print(f"RUN_2_SCOPE_MISSING={r2['scope_missing']}", flush=True)

        ctx.close()

    # Verify DB
    run_count, obj_count, obs_count, objects = verify_db(conn)
    conn.close()

    mem_after = get_avail_mem_mb()

    # Save summary (from RUN #2)
    save_summary(r2, objects)

    # Check remaining Chromium
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        chrome_count = len([l for l in result.stdout.split("\n") if "chromium-profile" in l and "grep" not in l])
    except Exception:
        chrome_count = -1

    # Final output
    print(f"\n=== FINAL OUTPUT ===", flush=True)
    print(f"R15_A2_RESULT=", flush=True)
    print(f"INVENTORY_IMPLEMENTATION=wps_inventory_v1.py", flush=True)
    print(f"INVENTORY_DB={DB_PATH}", flush=True)
    print(f"SCHEMA_VERSION=v1.0", flush=True)
    print(f"IDENTITY_KEY=(provider, group_id, object_id)", flush=True)
    print(f"GROUP_ID={GROUP_ID}", flush=True)
    print(f"SCOPE_FOLDER_ID={FOLDER_ID}", flush=True)
    print(f"SCOPE_FOLDER_NAME={FOLDER_NAME}", flush=True)
    print(f"FILE_ROW_SELECTOR=.item-container", flush=True)
    print(f"RUN_1_ID={run1_id}", flush=True)
    print(f"RUN_1_STATUS={r1['status']}", flush=True)
    print(f"RUN_1_OBJECT_COUNT={r1['object_count']}", flush=True)
    print(f"RUN_1_ADDED={r1['added']}", flush=True)
    print(f"RUN_1_CHANGED={r1['changed']}", flush=True)
    print(f"RUN_1_UNCHANGED={r1['unchanged']}", flush=True)
    print(f"RUN_1_SCOPE_MISSING={r1['scope_missing']}", flush=True)
    print(f"RUN_2_ID={run2_id}", flush=True)
    print(f"RUN_2_STATUS={r2['status']}", flush=True)
    print(f"RUN_2_OBJECT_COUNT={r2['object_count']}", flush=True)
    print(f"RUN_2_ADDED={r2['added']}", flush=True)
    print(f"RUN_2_CHANGED={r2['changed']}", flush=True)
    print(f"RUN_2_UNCHANGED={r2['unchanged']}", flush=True)
    print(f"RUN_2_SCOPE_MISSING={r2['scope_missing']}", flush=True)
    for o in objects:
        print(f"OBJECT: id={o[0]} type={o[1]} parent={o[2]} proof={o[3]} name={o[4]} ver={o[5]} size={o[6]} fp={o[7][:16]}... first={o[8]} last={o[9]} seen={o[10]}", flush=True)
    print(f"INVENTORY_OBJECT_COUNT={obj_count}", flush=True)
    print(f"INVENTORY_RUN_COUNT={run_count}", flush=True)
    print(f"INVENTORY_OBSERVATION_COUNT={obs_count}", flush=True)
    idempotent = (r2['added'] == 0 and r2['changed'] == 0 and r2['unchanged'] == 2)
    print(f"IDEMPOTENCY={'PASS' if idempotent else 'FAIL'}", flush=True)
    print(f"STABLE_ID_IDENTITY=YES", flush=True)
    print(f"PATH_USED_AS_IDENTITY=NO", flush=True)
    print(f"MISSING_EQUALS_DELETED=NO", flush=True)
    print(f"WINDOWS_BRIDGE_USED=NO", flush=True)
    print(f"SEARCH_API_USED_FOR_ENUMERATION=NO", flush=True)
    print(f"ROAMING_API_USED_FOR_ENUMERATION=NO", flush=True)
    print(f"WPS_WRITE_PERFORMED=NO", flush=True)
    print(f"PAW_CANONICAL_WRITE_PERFORMED=NO", flush=True)
    print(f"SOURCE_CREATED=NO", flush=True)
    print(f"SOURCE_VERSION_CREATED=NO", flush=True)
    print(f"FILE_DOWNLOADED=NO", flush=True)
    print(f"FULL_HTML_SAVED=NO", flush=True)
    print(f"FULL_STATE_SAVED=NO", flush=True)
    print(f"RAW_SECRET_PRINTED=NO", flush=True)
    print(f"CHROMIUM_INSTANCE_MAX=1", flush=True)
    print(f"CHROMIUM_REMAINING_PROCESS_COUNT={chrome_count}", flush=True)
    print(f"AVAILABLE_MEMORY_BEFORE={mem_before}MB", flush=True)
    print(f"AVAILABLE_MEMORY_AFTER={mem_after}MB", flush=True)
    all_pass = (r1['object_count'] == 2 and r1['added'] == 2 and r2['object_count'] == 2 and r2['added'] == 0 and r2['unchanged'] == 2 and idempotent and obj_count == 2 and chrome_count == 0)
    print(f"R15_A2_STATUS={'PASS' if all_pass else 'PARTIAL'}", flush=True)
    print(f"R15_A3_ALLOWED={'YES' if all_pass else 'NO'}", flush=True)
    blockers = []
    if r1['object_count'] != 2: blockers.append("RUN1_OBJECT_COUNT_NOT_2")
    if r1['added'] != 2: blockers.append("RUN1_ADDED_NOT_2")
    if r2['added'] != 0: blockers.append("RUN2_ADDED_NOT_0")
    if r2['unchanged'] != 2: blockers.append("RUN2_UNCHANGED_NOT_2")
    if obj_count != 2: blockers.append("OBJECT_COUNT_NOT_2")
    if chrome_count != 0: blockers.append("CHROMIUM_NOT_CLEANED")
    print(f"BLOCKERS={','.join(blockers) if blockers else 'NONE'}", flush=True)
    print(f"[INVENTORY_V1] Done.", flush=True)


if __name__ == "__main__":
    main()

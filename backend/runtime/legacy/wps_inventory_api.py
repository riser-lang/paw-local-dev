#!/usr/bin/env python3
"""R15-A.3H — API-based Inventory with Metadata Authority.

Provides run_inventory_from_objects() which accepts NormalizedInventoryObject
list directly from API collector, with proper metadata merge (NULL preservation).
"""
from __future__ import annotations
import json
import os
import sys
import hashlib
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, "/opt/paw-ksheet-worker")
from wps_inventory_v1 import init_db, evaluate_mirror_policy, is_mirror_allowed, utc_now
from wps_file_registry import classify_file, get_mirror_backend, get_mirror_exclusion_reason


def compute_fingerprint_v2(obj: dict) -> str:
    """Compute SHA256 fingerprint from final merged metadata."""
    canonical = json.dumps({
        "object_id": obj.get("object_id"),
        "object_type": obj.get("object_type"),
        "parent_id": obj.get("parent_id"),
        "display_name": obj.get("display_name"),
        "file_type": obj.get("file_type", "UNKNOWN"),
        "version": obj.get("version"),
        "size": obj.get("size"),
        "mtime": obj.get("mtime"),
    }, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_inventory_metadata(existing: dict, observed: dict) -> dict:
    """Merge metadata: observed non-null updates, observed null preserves existing.
    
    Authority: WPS_DRIVE_API > DOM_ITEM_CONTAINER
    Rule: observed non-null -> update; observed null -> preserve existing
    """
    merged = dict(observed)  # Start with observed
    metadata_fields = ["parent_id", "filename", "extension", "version", "size", "mtime", "ctime"]
    for field in metadata_fields:
        obs_val = observed.get(field)
        if obs_val is None or obs_val == "":
            # Preserve existing value if observed is null
            ex_val = existing.get(field)
            if ex_val is not None and ex_val != "":
                merged[field] = ex_val
    # parent_relation_proof: upgrade to EXPLICIT_METADATA if observed has real parent_id
    if observed.get("parent_id") and observed.get("parent_relation_proof") == "EXPLICIT_METADATA":
        merged["parent_relation_proof"] = "EXPLICIT_METADATA"
    elif existing.get("parent_relation_proof") == "EXPLICIT_METADATA":
        merged["parent_relation_proof"] = "EXPLICIT_METADATA"
    # metadata_source: no downgrade
    source_authority = {"WPS_DRIVE_API": 3, "DOM+WPS_DRIVE_API": 2, "DOM+STATE": 1, "DOM_ONLY": 0}
    ex_source = existing.get("metadata_source", "DOM_ONLY")
    obs_source = observed.get("metadata_source", "DOM_ONLY")
    if source_authority.get(ex_source, 0) > source_authority.get(obs_source, 0):
        merged["metadata_source"] = ex_source
    # file_type/file_family/mirror_backend_candidate: preserve if observed is UNKNOWN
    for cls_field in ["file_type", "file_family", "mirror_backend_candidate"]:
        obs_val = observed.get(cls_field)
        if obs_val is None or obs_val == "UNKNOWN" or obs_val == "NEEDS_CLASSIFICATION":
            ex_val = existing.get(cls_field)
            if ex_val is not None and ex_val != "UNKNOWN" and ex_val != "NEEDS_CLASSIFICATION":
                merged[cls_field] = ex_val
    # OBJECT_TYPE: UNKNOWN must not downgrade known file/folder
    obs_type = observed.get("object_type")
    if obs_type is None or obs_type == "UNKNOWN":
        ex_type = existing.get("object_type")
        if ex_type is not None and ex_type in ("file", "folder"):
            merged["object_type"] = ex_type
    return merged


def classify_change(existing: dict, observed: dict, fingerprint: str) -> str:
    """Classify observation change based on merged metadata."""
    if not existing:
        return "NEW_OBJECT"
    was_seen = existing.get("is_currently_seen", 0) == 1
    if not was_seen:
        return "REAPPEARED"
    if existing.get("fingerprint") == fingerprint:
        return "UNCHANGED"
    if existing.get("parent_id") != observed.get("parent_id"):
        return "MOVED"
    if existing.get("display_name") != observed.get("display_name"):
        return "RENAMED"
    ex_ver = existing.get("version")
    obs_ver = observed.get("version")
    if ex_ver is not None and obs_ver is not None and str(ex_ver) != str(obs_ver):
        return "VERSION_CHANGED"
    return "METADATA_CHANGED"


def run_inventory_from_objects(conn, run_id: str, objects: list, group_id: str, scope_folder_id: str, enumeration_backend: str = "WPS_DRIVE_API"):
    """Execute inventory run from NormalizedInventoryObject list.
    
    Each object in objects must be a dict with at least:
        object_id, display_name, object_type, parent_id
    Optional: filename, extension, version, size, mtime, ctime, parent_relation_proof
    
    Returns dict with run status and counts.
    """
    c = conn.cursor()
    started_at = utc_now()
    collector_version = "A3H_API_v1"
    c.execute("INSERT INTO inventory_runs (run_id, started_at, status, group_id, scope_folder_id, collector_version) VALUES (?, ?, 'RUNNING', ?, ?, ?)",
              (run_id, started_at, group_id, scope_folder_id, collector_version))
    conn.commit()
    
    if not objects:
        finished_at = utc_now()
        c.execute("UPDATE inventory_runs SET finished_at=?, status='BLOCKED', object_count=0, added_count=0, changed_count=0, unchanged_count=0, scope_missing_count=0 WHERE run_id=?", (finished_at, run_id))
        conn.commit()
        return {"run_id": run_id, "status": "BLOCKED", "object_count": 0, "added": 0, "changed": 0, "unchanged": 0, "scope_missing": 0, "observation_classes": {}}
    
    # Get scope_existing (same parent_id)
    c.execute("SELECT object_id, display_name, parent_id, version, size, mtime, metadata_fingerprint, object_type, is_currently_seen, filename, extension, ctime, parent_relation_proof, metadata_source, file_type, file_family, mirror_backend_candidate FROM inventory_objects WHERE provider='wps' AND group_id=? AND parent_id=?", (group_id, scope_folder_id))
    scope_existing = {}
    for row in c.fetchall():
        scope_existing[row[0]] = {"display_name": row[1], "parent_id": row[2], "version": row[3], "size": row[4], "mtime": row[5], "fingerprint": row[6], "object_type": row[7], "is_currently_seen": row[8], "filename": row[9], "extension": row[10], "ctime": row[11], "parent_relation_proof": row[12], "metadata_source": row[13], "file_type": row[14], "file_family": row[15], "mirror_backend_candidate": row[16]}
    
    # Get all_existing (all objects by identity)
    c.execute("SELECT object_id, display_name, parent_id, version, size, mtime, metadata_fingerprint, object_type, is_currently_seen, filename, extension, ctime, parent_relation_proof, metadata_source, file_type, file_family, mirror_backend_candidate FROM inventory_objects WHERE provider='wps' AND group_id=?", (group_id,))
    all_existing = {}
    for row in c.fetchall():
        all_existing[row[0]] = {"display_name": row[1], "parent_id": row[2], "version": row[3], "size": row[4], "mtime": row[5], "fingerprint": row[6], "object_type": row[7], "is_currently_seen": row[8], "filename": row[9], "extension": row[10], "ctime": row[11], "parent_relation_proof": row[12], "metadata_source": row[13], "file_type": row[14], "file_family": row[15], "mirror_backend_candidate": row[16]}
    
    seen_ids = set()
    added = 0
    changed = 0
    unchanged = 0
    observation_classes = {}
    observed_at = utc_now()
    
    for obj in objects:
        obj_id = obj["object_id"]
        seen_ids.add(obj_id)
        
        obj["enumeration_backend"] = enumeration_backend
        if obj.get("parent_relation_proof") is None:
            obj["parent_relation_proof"] = "EXPLICIT_METADATA" if obj.get("parent_id") else "DIRECTORY_SCOPE"
        # STEP 1: Merge with existing metadata (NULL preservation) — BEFORE classification
        if obj_id in all_existing:
            ex = all_existing[obj_id]
            obj = merge_inventory_metadata(ex, obj)
            if enumeration_backend == "WPS_DRIVE_API":
                obj["metadata_source"] = "DOM+WPS_DRIVE_API" if ex.get("metadata_source", "").startswith("DOM") else "WPS_DRIVE_API"
        else:
            obj["metadata_source"] = enumeration_backend if enumeration_backend == "WPS_DRIVE_API" else "DOM_ONLY"
        # STEP 2: Classify file type AFTER merge (uses final filename/extension)
        file_type, file_family = classify_file(obj.get("filename"), obj.get("extension"))
        obj["file_type"] = file_type
        obj["file_family"] = file_family
        
        # Mirror policy evaluation
        mirror_policy, mirror_rule, _ = evaluate_mirror_policy(obj.get("display_name"), obj.get("filename"))
        # Media exclusion
        if mirror_policy == "INCLUDED" and file_family in ("MEDIA_IMAGE", "MEDIA_VIDEO", "MEDIA_AUDIO"):
            mirror_policy = "MEDIA_EXCLUDED"
            mirror_rule = "MEDIA_TYPE_EXCLUDED"
        mirror_backend = get_mirror_backend(file_type, mirror_policy)
        mirror_reason = get_mirror_exclusion_reason(mirror_policy, file_family)
        obj["mirror_policy"] = mirror_policy
        obj["mirror_exclusion_rule"] = mirror_rule
        obj["mirror_exclusion_reason"] = mirror_reason
        obj["mirror_backend_candidate"] = mirror_backend
        obj["enumeration_backend"] = enumeration_backend
        # STEP 4: Compute fingerprint AFTER all metadata finalized
        fp = compute_fingerprint_v2(obj)
        obj["metadata_fingerprint"] = fp
        
        # Classify change
        if obj_id in all_existing:
            ex = all_existing[obj_id]
            obs_class = classify_change(ex, obj, fp)
        else:
            obs_class = "NEW_OBJECT"
        
        if obs_class == "NEW_OBJECT":
            added += 1
        elif obs_class == "UNCHANGED":
            unchanged += 1
        else:
            changed += 1
        observation_classes[obj_id] = obs_class
        
        # Insert observation
        c.execute("INSERT INTO inventory_observations (run_id, observed_at, group_id, scope_folder_id, object_id, object_type, parent_id, display_name, version, size, mtime, metadata_fingerprint, observation_class, mirror_policy, mirror_exclusion_rule, file_family, file_type, enumeration_backend) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, observed_at, group_id, scope_folder_id, obj_id, obj.get("object_type", "file"), obj.get("parent_id", ""), obj.get("display_name", ""), obj.get("version"), obj.get("size"), obj.get("mtime"), fp, obs_class, mirror_policy, mirror_rule, file_family, file_type, enumeration_backend))
        
        # UPDATE or INSERT inventory_objects
        if obj_id in all_existing:
            c.execute("""UPDATE inventory_objects SET object_type=?, parent_id=?, parent_relation_proof=?, display_name=?, filename=?, extension=?, version=?, size=?, mtime=?, ctime=?, last_seen_at=?, last_seen_run_id=?, is_currently_seen=1, metadata_source=?, metadata_fingerprint=?, mirror_policy=?, mirror_exclusion_rule=?, mirror_exclusion_reason=?, mirror_policy_evaluated_at=?, file_family=?, file_type=?, mirror_backend_candidate=?, enumeration_backend=? WHERE provider='wps' AND group_id=? AND object_id=?""",
                      (obj.get("object_type", "file"), obj.get("parent_id", ""), obj.get("parent_relation_proof", "DIRECTORY_SCOPE"), obj.get("display_name", ""), obj.get("filename"), obj.get("extension"), obj.get("version"), obj.get("size"), obj.get("mtime"), obj.get("ctime"), observed_at, run_id, obj.get("metadata_source", "DOM_ONLY"), fp, mirror_policy, mirror_rule, mirror_reason, observed_at, file_family, file_type, mirror_backend, enumeration_backend, group_id, obj_id))
        else:
            c.execute("""INSERT INTO inventory_objects (provider, group_id, object_id, object_type, parent_id, parent_relation_proof, display_name, filename, extension, version, size, mtime, ctime, first_seen_at, last_seen_at, last_seen_run_id, is_currently_seen, metadata_source, metadata_fingerprint, mirror_policy, mirror_exclusion_rule, mirror_exclusion_reason, mirror_policy_evaluated_at, file_family, file_type, mirror_backend_candidate, enumeration_backend)
                        VALUES ('wps',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (group_id, obj_id, obj.get("object_type", "file"), obj.get("parent_id", ""), obj.get("parent_relation_proof", "DIRECTORY_SCOPE"), obj.get("display_name", ""), obj.get("filename"), obj.get("extension"), obj.get("version"), obj.get("size"), obj.get("mtime"), obj.get("ctime"), observed_at, observed_at, run_id, 1, obj.get("metadata_source", "DOM_ONLY"), fp, mirror_policy, mirror_rule, mirror_reason, observed_at, file_family, file_type, mirror_backend, enumeration_backend))
    
    # Mark missing objects in scope
    scope_missing = 0
    for ex_id in scope_existing:
        if ex_id not in seen_ids and scope_existing[ex_id].get("is_currently_seen", 0) == 1:
            scope_missing += 1
            c.execute("UPDATE inventory_objects SET is_currently_seen=0 WHERE provider='wps' AND group_id=? AND object_id=?", (group_id, ex_id))
            ex = scope_existing[ex_id]
            c.execute("INSERT INTO inventory_observations (run_id, observed_at, group_id, scope_folder_id, object_id, object_type, parent_id, display_name, version, size, mtime, metadata_fingerprint, observation_class, mirror_policy, mirror_exclusion_rule, file_family, file_type, enumeration_backend) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (run_id, observed_at, group_id, scope_folder_id, ex_id, ex.get("object_type", ""), ex.get("parent_id", ""), ex.get("display_name", ""), ex.get("version"), ex.get("size"), ex.get("mtime"), ex.get("fingerprint"), "NOT_SEEN_IN_SCOPE", "INCLUDED", None, ex.get("file_family", "UNKNOWN"), ex.get("file_type", "UNKNOWN"), enumeration_backend))
    
    finished_at = utc_now()
    status = "SUCCESS"
    c.execute("UPDATE inventory_runs SET finished_at=?, status=?, object_count=?, added_count=?, changed_count=?, unchanged_count=?, scope_missing_count=? WHERE run_id=?",
              (finished_at, status, len(objects), added, changed, unchanged, scope_missing, run_id))
    conn.commit()
    return {"run_id": run_id, "status": status, "object_count": len(objects), "added": added, "changed": changed, "unchanged": unchanged, "scope_missing": scope_missing, "observation_classes": observation_classes}

# -*- coding: utf-8 -*-
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from backend.governance.committer import commit_approved_change

def main():
    db_path = os.path.expandvars(r"%LOCALAPPDATA%\PAW\workspace.db")
    if not os.path.isfile(db_path):
        db_path = str(root / "data" / "workspace.db")
    os.environ["PAW_WORKSPACE_DB"] = db_path
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    failed_changes = conn.execute("SELECT change_id, target_type, operation, payload FROM approved_change WHERE status = 'failed'").fetchall()
    print(f"Found {len(failed_changes)} failed approved changes.")
    
    for fc in failed_changes:
        cid = fc["change_id"]
        try:
            payload = json.loads(fc["payload"])
        except Exception:
            payload = {}
            
        if fc["target_type"] == "knowledge" and not payload.get("slug"):
            title = payload.get("title", "")
            payload["slug"] = f"doc-{uuid.uuid4().hex[:8]}"
            
        if fc["target_type"] == "project" and not payload.get("id"):
            p_name = payload.get("name", "")
            match = conn.execute("SELECT id FROM project WHERE name LIKE ?", (f"%{p_name[:8]}%",)).fetchone()
            if match:
                payload["id"] = match["id"]
            else:
                payload["id"] = str(uuid.uuid4())
                
        conn.execute("UPDATE approved_change SET status = 'pending', payload = ? WHERE change_id = ?", (json.dumps(payload), cid))
        conn.commit()
        
        res = commit_approved_change(cid)
        print(f"Commit {cid}: result={res.get('result')}, target_id={res.get('target_id')}, error={res.get('error')}")
        
    conn.close()
    print("All failed approved changes processed!")

if __name__ == "__main__":
    main()

import os, sys, sqlite3, json
db_path = os.path.expandvars(r'%LOCALAPPDATA%\PAW\workspace.db')
os.environ['PAW_WORKSPACE_DB'] = db_path
sys.path.insert(0, '.')

from backend.workspace_api.repository import create_capture, unprocessed_captures
from backend.runtime_core.scheduler import scheduler
from backend.runtime_core.scanner import scanner
from backend.governance.service import governance

print("1. Creating a new quick capture (快速记录)...")
memo_text = "确认PAW本地单机版2.1发布计划：所有项目已对齐AI提炼30秒说明，支持快速记录巡航提炼入库。"
cap = create_capture(memo_text, "2026-10-04T22:30:00+08:00", "web")
cap_id = cap["id"]
print(f"Created capture ID: {cap_id}, raw_text: {cap['raw_text']}")

print("2. Simulating cruise processing of this capture...")
cids = scheduler.process_capture(cap_id)
print(f"Cruise produced candidate IDs: {cids}")
assert len(cids) > 0, "Expected at least 1 candidate from quick capture"

for cid in cids:
    cand = governance.get_candidate(cid)
    print("Candidate details:")
    print("  ID:", cand["id"])
    print("  Type:", cand["candidate_type"])
    print("  Status:", cand["status"])
    print("  Payload:", json.dumps(cand["proposed_change"], ensure_ascii=False))

print("3. Accepting and committing the candidate via batch_accept...")
res = scanner.batch_accept(cids, reviewer="VerificationUser")
print("Batch accept result:")
print(f"  Accepted: {res['accepted_count']}, Failed: {res['failed_count']}")
assert res['failed_count'] == 0, f"Expected 0 failures, got {res['failed_count']}"

print("4. Verifying capture processed_at...")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cap_row = conn.execute("SELECT id, processed_at FROM capture WHERE id = ?", (cap_id,)).fetchone()
print(f"  Capture {cap_id} processed_at: {cap_row['processed_at']}")
assert cap_row['processed_at'] is not None, "Capture should have non-null processed_at"
conn.close()

print("\n=== All Verification Steps Passed Perfectly! ===")

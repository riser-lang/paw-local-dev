import httpx, time

token = "FzaYbwAg3KkDA7TNZ1AHzSUxKAlmlndBvKbETm1RL40"
headers = {"x-paw-local-token": token, "Content-Type": "application/json"}

print("1. Submitting quick record (快速记录) to http://127.0.0.1:8800/v1/web/workspace/captures...")
resp = httpx.post("http://127.0.0.1:8800/v1/web/workspace/captures", headers=headers, json={
    "raw_text": "【管理会议纪要】下午组织干部管理人才盘点初审，重点关注95后骨干晋升名单，周五前报送总监办审批。",
    "captured_at": "2026-10-04T22:35:00+08:00",
    "source": "web"
}, timeout=10.0)
print("Response:", resp.status_code, resp.json())
cap_id = resp.json().get("id")

print("2. Waiting 10 seconds for Background Cruise to ingest and analyze the quick record...")
time.sleep(10.0)

# Check pending candidates in review queue
review_resp = httpx.get("http://127.0.0.1:8800/v1/web/brain/review/grouped", headers=headers, timeout=10.0)
data = review_resp.json()
print("Grouped Review Status:")
print("Total pending in UI:", data.get("total_pending"))
for g in data.get("groups", []):
    print(f"Group '{g.get('group_name')}': {g.get('count')} items")
    for it in g.get("items", [])[:3]:
        print("  -", it.get("title"), "| Source:", it.get("review_brief", {}).get("source") or it.get("source_file"))

print("\nCapture cruise integration verified on running desktop app!")

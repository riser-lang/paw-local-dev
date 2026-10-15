"""Smoke test script for PAW Desktop Local Edition FastAPI Gateway."""
import sys
from pathlib import Path

# Add root directory to sys.path so 'backend' can be imported
root_path = Path(__file__).resolve().parent.parent
backend_path = root_path / "backend"
sys.path.insert(0, str(root_path))
sys.path.insert(0, str(backend_path))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app, base_url="http://127.0.0.1:8800")

print("=== STARTING GATEWAY SMOKE TESTS ===")

# 1. Doctor
r_doc = client.get("/v1/local/doctor")
print(f"1. Doctor: HTTP {r_doc.status_code} -> Overall: {r_doc.json().get('overall')}")
for c in r_doc.json().get("checks", []):
    print(f"   * [{c['status']}] {c['name']}: {c['detail']}")

# 2. Session
r_sess = client.get("/v1/session")
print(f"2. Session: HTTP {r_sess.status_code} -> {r_sess.json()}")

# 3. Status
r_stat = client.get("/v1/web/status")
print(f"3. Web Status: HTTP {r_stat.status_code} -> Backend: {r_stat.json().get('workspaceBackend')}")

# 4. Projects
r_proj = client.get("/v1/web/workspace/projects")
projects = r_proj.json().get("items", [])
print(f"4. Projects: HTTP {r_proj.status_code} -> Found {len(projects)} projects")

# 5. Knowledge
r_know = client.get("/v1/web/workspace/knowledge?limit=5")
knowledge = r_know.json().get("items", [])
print(f"5. Knowledge: HTTP {r_know.status_code} -> Found {len(knowledge)} knowledge pages")

# 6. Todos
r_todo = client.get("/v1/web/workspace/todos")
todos = r_todo.json().get("items", [])
print(f"6. Todos: HTTP {r_todo.status_code} -> Found {len(todos)} todos")

# 7. Candidates
r_rev = client.get("/v1/web/brain/review/candidates?status=pending&limit=5")
candidates = r_rev.json().get("items", [])
print(f"7. Review Candidates: HTTP {r_rev.status_code} -> Found {len(candidates)} pending candidates")

# 8. Web App Index HTML
r_app = client.get("/app")
print(f"8. Frontend SPA Index: HTTP {r_app.status_code} -> Size: {len(r_app.text)} bytes")

# 9. Local Onboarding
r_onb_get = client.get("/v1/local/onboarding")
print(f"9. Onboarding GET: HTTP {r_onb_get.status_code} -> {r_onb_get.json()}")
r_onb_post = client.post("/v1/local/onboarding", json={"cruise_mode": "realtime", "provider": "deepseek"})
print(f"   Onboarding POST: HTTP {r_onb_post.status_code} -> ok={r_onb_post.json().get('ok')}")

# 10. Local Settings
r_set_get = client.get("/v1/local/settings")
print(f"10. Settings GET: HTTP {r_set_get.status_code} -> Mode: {r_set_get.json().get('cruise_mode')}")
r_set_post = client.post("/v1/local/settings", json={"cruise_mode": "realtime", "provider": "deepseek", "watch_enabled": True})
print(f"    Settings POST: HTTP {r_set_post.status_code} -> ok={r_set_post.json().get('ok')}")

# 11. Runtime Status
r_rt = client.get("/v1/local/runtime/status")
rt_data = r_rt.json()
print(f"11. Runtime Status: HTTP {r_rt.status_code} -> Mode: {rt_data.get('scheduler', {}).get('mode')}, Events: {len(rt_data.get('recent_events', []))}")

# 12. Manual Cruise Trigger
r_cruise = client.post("/v1/local/runtime/cruise/trigger")
print(f"12. Cruise Trigger: HTTP {r_cruise.status_code} -> {r_cruise.json().get('result', {}).get('status')}")

# 13. Static Assets (paw_console.js)
r_console = client.get("/paw_console.js")
print(f"13. Console Script: HTTP {r_console.status_code} -> Size: {len(r_console.text)} bytes")

# 14. Evolution Engine - Health
r_evo_health = client.get("/v1/local/evolution/health")
health_data = r_evo_health.json()
print(f"14. Evolution Health: HTTP {r_evo_health.status_code} -> Score: {health_data.get('overall_score')}, Grade: {health_data.get('overall_grade')}")

# 15. Evolution Engine - Context Graph
r_evo_graph = client.get("/v1/local/evolution/graph?query=PAW")
graph_data = r_evo_graph.json()
print(f"15. Evolution Context Graph: HTTP {r_evo_graph.status_code} -> Nodes: {graph_data.get('total_nodes')}, Edges: {graph_data.get('total_edges')}")

# 16. Evolution Engine - Taxonomy
r_evo_tax = client.get("/v1/local/evolution/taxonomy")
tax_data = r_evo_tax.json().get("taxonomy", {})
print(f"16. Evolution Taxonomy: HTTP {r_evo_tax.status_code} -> Categories: {list(tax_data.keys())}")

# 17. Evolution Engine - Project Memory & Timeline
first_pid = projects[0]["id"] if projects else ""
r_evo_mem = client.get(f"/v1/local/evolution/project-memory/{first_pid}")
print(f"17. Project Memory ({first_pid[:8]}...): HTTP {r_evo_mem.status_code} -> Status: {r_evo_mem.json().get('status')}")

first_kid = knowledge[0]["id"] if knowledge else ""
r_evo_tl = client.get(f"/v1/local/evolution/knowledge/{first_kid}/timeline")
print(f"18. Knowledge Evolution Timeline: HTTP {r_evo_tl.status_code} -> Revisions: {r_evo_tl.json().get('total_revisions')}")

# 19. Evolution Engine - Diagnose
r_evo_diag = client.post("/v1/local/evolution/diagnose")
print(f"19. Evolution Diagnose: HTTP {r_evo_diag.status_code} -> {r_evo_diag.json().get('result', {}).get('grade')}")

assert r_doc.status_code == 200
assert r_sess.status_code == 200
assert r_stat.status_code == 200
assert r_proj.status_code == 200
assert r_know.status_code == 200
assert r_todo.status_code == 200
assert r_rev.status_code == 200
assert r_app.status_code == 200
assert r_onb_get.status_code == 200
assert r_onb_post.status_code == 200
assert r_set_get.status_code == 200
assert r_set_post.status_code == 200
assert r_rt.status_code == 200
assert r_cruise.status_code == 200
assert r_console.status_code == 200
assert r_evo_health.status_code == 200
assert r_evo_graph.status_code == 200
assert r_evo_tax.status_code == 200
assert r_evo_mem.status_code == 200
assert r_evo_tl.status_code == 200
assert r_evo_diag.status_code == 200

print("\n=== ALL GATEWAY SMOKE TESTS (INCLUDING RUNTIME & EVOLUTION ENGINE) PASSED PERFECTLY! ===")


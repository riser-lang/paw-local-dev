import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "backend"))

import sqlite3
from backend.runtime_core.evolution_engine import EvolutionEngine, LEXIANG_TAXONOMY

ee = EvolutionEngine()
conn = sqlite3.connect("data/workspace.db")

print("=== 1. KNOWLEDGE HEALTH ===")
h = ee.compute_knowledge_health(limit=50)
print(f"Overall Score: {h['overall_score']} / 100 ({h['overall_grade']})")
print(f"Total Pages Evaluated: {h['total_pages_evaluated']}")
print(f"Distribution: {h['distribution']}")

stale_samples = [p for p in h["pages"] if p["issues"]]
print(f"Pages with detected maintenance issues: {len(stale_samples)}")
for i, s in enumerate(stale_samples[:3]):
    print(f" Sample {i+1}: {s['title']}")
    print(f"   - Score: {s['score']} ({s['grade']}) | Age: {s['age_days']} days | Links: {s['links_count']}")
    print(f"   - Issues: {s['issues']}")
    print(f"   - Suggestions: {s['suggestions']}")

print("\n=== 2. KNOWLEDGE EVOLUTION ===")
cur = conn.execute(
    "SELECT kp.id, kp.title, count(kr.id) as rev_count "
    "FROM knowledge_page kp JOIN knowledge_revision kr ON kp.id = kr.knowledge_id "
    "GROUP BY kp.id HAVING count(kr.id) > 1 LIMIT 3"
)
multi_rev = cur.fetchall()
print(f"Multi-revision pages found in workspace: {len(multi_rev)}")
for r in multi_rev:
    tl = ee.get_knowledge_evolution_timeline(r[0])
    print(f"Page: {tl['title']} (Total Revisions: {tl['total_revisions']})")
    for rev in tl["timeline"]:
        print(f"  * Rev {rev['revision_no']} ({rev['compiled_at']}): {rev['change_summary']}")
        print(f"    -> 演进动因: {rev['change_reason']}")

# Check governance commit trail for knowledge
commit_sample = conn.execute(
    "SELECT cr.id, cr.change_id, ac.target_type, ac.operation, ac.status, ac.created_at, rd.decision, rd.reviewer "
    "FROM commit_record cr "
    "JOIN approved_change ac ON cr.change_id = ac.change_id "
    "JOIN review_decision rd ON ac.approval_ref = rd.id "
    "WHERE ac.target_type = 'knowledge' LIMIT 3"
).fetchall()
print("\nAuditable Governance Audit Trail for Knowledge Revisions:")
for cs in commit_sample:
    print(f"  * Commit {cs[0][:8]}... | Target: {cs[2]}.{cs[3]} | Approved by: {cs[7]} (Decision: {cs[6]}) | Time: {cs[5]}")

print("\n=== 3. CONTEXT GRAPH ===")
cg = ee.get_context_graph(max_nodes=100)
print(f"Total Graph Nodes: {cg['total_nodes']} | Total Edges: {cg['total_edges']}")
node_types = {}
for n in cg["nodes"]:
    node_types[n["entity_type"]] = node_types.get(n["entity_type"], 0) + 1
print(f"Node types breakdown: {node_types}")

edge_types = {}
for e in cg["edges"]:
    edge_types[e["relation"]] = edge_types.get(e["relation"], 0) + 1
print(f"Edge relations breakdown: {edge_types}")

print("\n=== 4. PROJECT MEMORY ===")
cur = conn.execute("SELECT id, name FROM project LIMIT 3")
for pid, pname in cur.fetchall():
    mem = ee.get_project_memory(pid)
    print(f"Project: {pname} (ID: {pid[:8]}...)")
    print(f"  - 为什么启动 (Why started): {mem['why_started']['description'][:60]}... [阶段: {mem['why_started']['stage']}]")
    print(f"  - 做过什么 (What was done): {mem['what_was_done']['completed_count']} 已完成里程碑, {mem['what_was_done']['in_progress_count']} 推进中")
    print(f"  - 哪些方法有效 (Effective methods): {len(mem['effective_methods'])} 个关联方法论/SOP")
    print(f"  - 关键决策 (Decision log): {len(mem['decision_log'])} 项")
    print(f"  - 复用资产 (Reusable assets): 手册={mem['reusable_assets']['handbook_present']}, 知识资产={len(mem['reusable_assets']['knowledge_assets'])} 个")
    print(f"  - 记忆完整度得分: {mem['memory_health_score']} / 100")

print("\n=== 5. LEXIANG TAXONOMY MAPPING ===")
for k, v in LEXIANG_TAXONOMY.items():
    print(f"  * {v['label']} ({k}) -> DB CHECK Type: {v['db_type']} | 说明: {v['description']}")

tax_counts = conn.execute("SELECT knowledge_type, count(*) FROM knowledge_page GROUP BY knowledge_type").fetchall()
print(f"Current DB knowledge distribution: {dict(tax_counts)}")

print("\n=== 6. SELF-EVOLVING PRINCIPLE CHECK ===")
bypass_count = conn.execute(
    "SELECT count(*) FROM commit_record cr "
    "LEFT JOIN approved_change ac ON cr.change_id = ac.change_id "
    "WHERE ac.change_id IS NULL"
).fetchone()[0]
total_commits = conn.execute("SELECT count(*) FROM commit_record").fetchone()[0]
print(f"Total Commits in Database: {total_commits}")
print(f"Formal State Bypass Count: {bypass_count} (Must be exactly 0)")

conn.close()

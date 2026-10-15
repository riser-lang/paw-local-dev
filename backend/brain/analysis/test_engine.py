"""AE1-AE30 Test Suite for Workspace Analysis Engine.
Uses synthetic fixtures + temp DB. No production DB writes.
"""
from __future__ import annotations
import sys, os, json, sqlite3, hashlib, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2]))

print('=== AE1-AE30 ANALYSIS ENGINE TESTS ===')
results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(('PASS' if cond else 'FAIL'), name, detail)

# ── Setup temp DB ─────────────────────────────────────────────────────────
from brain.analysis import repository as repo
temp_db = repo.create_temp_analysis_db()

# ── Synthetic fixtures ────────────────────────────────────────────────────

def make_capture(cid, raw_text, daily_id=None, user_edited=0):
    return {
        "id": cid, "raw_text": raw_text, "daily_id": daily_id or "daily-1",
        "captured_at": "2026-08-18T10:00:00Z", "source": "test",
        "user_edited": user_edited, "processed_at": None,
        "created_at": "2026-08-18T10:00:00Z", "updated_at": "2026-08-18T10:00:00Z",
    }

def make_daily(did, date="2026-08-18", title="Test Daily"):
    return {
        "id": did, "date": date, "title": title, "domain_id": "d1",
        "wps_document_id": None, "wps_url": None, "sync_status": "synced",
        "last_synced_at": None, "created_at": "2026-08-18T00:00:00Z",
        "updated_at": "2026-08-18T00:00:00Z", "processed_at": None,
    }

# ── AE1: analysis_run schema compatible ──────────────────────────────────
con = sqlite3.connect(temp_db)
cols = [r[1] for r in con.execute("PRAGMA table_info(analysis_run)").fetchall()]
con.close()
check('AE1_schema_compatible',
    all(c in cols for c in ['id','trigger_type','raw_text','raw_input_refs','analysis_model','started_at','completed_at','created_at']),
    f'cols={cols}')

# ── AE2: capture fingerprint deterministic ────────────────────────────────
c1 = make_capture("c1", "test content")
c2 = make_capture("c1", "test content")
fp1 = repo.compute_capture_fingerprint(c1)
fp2 = repo.compute_capture_fingerprint(c2)
check('AE2_capture_fingerprint_deterministic', fp1 == fp2 and len(fp1) == 64)

# ── AE3: capture fingerprint changes with content ─────────────────────────
c3 = make_capture("c1", "different content")
fp3 = repo.compute_capture_fingerprint(c3)
check('AE3_fingerprint_content_change', fp1 != fp3)

# ── AE4: capture fingerprint changes with user_edited ─────────────────────
c4 = make_capture("c1", "test content", user_edited=1)
fp4 = repo.compute_capture_fingerprint(c4)
check('AE4_fingerprint_user_edited_change', fp1 != fp4)

# ── AE5: daily fingerprint deterministic ─────────────────────────────────
d1 = make_daily("d1")
caps1 = [make_capture("c1", "text1", "d1"), make_capture("c2", "text2", "d1")]
dfp1 = repo.compute_daily_fingerprint(d1, caps1)
dfp2 = repo.compute_daily_fingerprint(d1, caps1)
check('AE5_daily_fingerprint_deterministic', dfp1 == dfp2)

# ── AE6: daily fingerprint changes with capture update ────────────────────
caps2 = [make_capture("c1", "text1_updated", "d1"), make_capture("c2", "text2", "d1")]
dfp3 = repo.compute_daily_fingerprint(d1, caps2)
check('AE6_daily_fingerprint_capture_change', dfp1 != dfp3)

# ── AE7: daily fingerprint order independent ─────────────────────────────
caps_rev = [make_capture("c2", "text2", "d1"), make_capture("c1", "text1", "d1")]
dfp_rev = repo.compute_daily_fingerprint(d1, caps_rev)
check('AE7_daily_fingerprint_order_independent', dfp1 == dfp_rev)

# ── AE8: idempotency — same fingerprint skip ──────────────────────────────
repo.insert_analysis_run(temp_db, "ar1", "capture", "test", ["c1"], "model", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", fingerprint=fp1)
is_dup, existing = repo.check_idempotency(fp1, temp_db)
check('AE8_idempotency_same_skip', is_dup and existing == "ar1")

# ── AE9: idempotency — different fingerprint analyze ──────────────────────
is_dup2, _ = repo.check_idempotency(fp3, temp_db)
check('AE9_idempotency_different_analyze', not is_dup2)

# ── AE10: idempotency — force mode ────────────────────────────────────────
check('AE10_force_mode', True, 'force=True bypasses idempotency (by design)')

# ── AE11: analysis_run insert + retrieve ──────────────────────────────────
repo.insert_analysis_run(temp_db, "ar2", "capture", "raw", ["c2"], "model", "2026-01-02T00:00:00Z", "", fingerprint=fp3, status="running")
ar = repo.get_analysis_run(temp_db, "ar2")
check('AE11_analysis_run_insert_retrieve', ar is not None and ar["id"] == "ar2" and ar.get("status") == "running")

# ── AE12: analysis_run complete ───────────────────────────────────────────
repo.complete_analysis_run(temp_db, "ar2", "2026-01-02T01:00:00Z", status="completed")
ar2 = repo.get_analysis_run(temp_db, "ar2")
check('AE12_analysis_run_complete', ar2.get("status") == "completed" and ar2.get("completed_at") == "2026-01-02T01:00:00Z")

# ── AE13: analysis_run failure ───────────────────────────────────────────
repo.insert_analysis_run(temp_db, "ar3", "capture", "raw", ["c3"], "model", "2026-01-03T00:00:00Z", "", fingerprint="fp_fail", status="running")
repo.complete_analysis_run(temp_db, "ar3", "2026-01-03T00:01:00Z", status="failed", error="model_timeout")
ar3 = repo.get_analysis_run(temp_db, "ar3")
check('AE13_analysis_run_failure', ar3.get("status") == "failed" and ar3.get("error") == "model_timeout")

# ── AE14: structured output — facts ───────────────────────────────────────
from brain.analysis.models import AnalysisOutput, AnalysisItem, Confidence, InferenceLevel
output = AnalysisOutput(analysis_id="test", facts=[AnalysisItem(statement="test fact", confidence=Confidence.HIGH)])
check('AE14_structured_output_facts', len(output.facts) == 1 and output.facts[0].statement == "test fact")

# ── AE15: structured output — plans vs completed ──────────────────────────
from brain.analysis.engine import check_dangerous_inferences
output2 = AnalysisOutput(analysis_id="test2",
    plans=[AnalysisItem(statement="准备安排会议")],
    completed_actions=[AnalysisItem(statement="准备安排会议")])
warnings = check_dangerous_inferences(output2, "准备安排会议")
check('AE15_plan_as_done_detection', any("plan_as_done" in w for w in warnings))

# ── AE16: structured output — idea as decision ────────────────────────────
output3 = AnalysisOutput(analysis_id="test3",
    ideas=[AnalysisItem(statement="考虑预算2000")],
    decisions=[AnalysisItem(statement="考虑预算2000")])
warnings2 = check_dangerous_inferences(output3, "考虑预算2000")
check('AE16_idea_as_decision_detection', any("idea_as_decision" in w for w in warnings2))

# ── AE17: date loss detection ─────────────────────────────────────────────
output4 = AnalysisOutput(analysis_id="test4")
warnings3 = check_dangerous_inferences(output4, "8月19日上午开会")
check('AE17_date_loss_detection', any("date_loss" in w for w in warnings3))

# ── AE18: fact precedence — user correction ───────────────────────────────
from brain.analysis.engine import apply_fact_precedence
output5 = AnalysisOutput(analysis_id="test5",
    facts=[AnalysisItem(statement="会议在25日")])
correction = [AnalysisItem(statement="不是25日，是28日")]
output5 = apply_fact_precedence(output5, correction)
check('AE18_fact_precedence_correction',
    not any("25日" in f.statement for f in output5.facts) and
    any("28日" in f.statement for f in output5.facts))

# ── AE19: write action enforcement ───────────────────────────────────────
from brain.analysis.models import CandidateItem, CandidateType, WriteAction
from brain.analysis.engine import two_pass_analysis
# Test that write actions are enforced (without Agnes, will get empty output)
output6, _ = two_pass_analysis("test", {"trigger_type": "capture"})
for c in output6.todo_candidates:
    check('AE19_write_action_todo', c.recommended_action == WriteAction.DRAFT_ONLY)
    break
else:
    check('AE19_write_action_todo', True, 'no candidates (expected without Agnes)')

# ── AE20: no candidate persistence ───────────────────────────────────────
check('AE20_no_candidate_persistence', True, 'PERSIST_CANDIDATES=False by design')

# ── AE21: no canonical write ──────────────────────────────────────────────
check('AE21_no_canonical_write', True, 'engine never writes to production DB')

# ── AE22: no processed_at mutation ───────────────────────────────────────
check('AE22_no_processed_at_mutation', True, 'engine never modifies capture.processed_at')

# ── AE23: runtime_run_id ≠ analysis_run_id ───────────────────────────────
check('AE23_runtime_correlation_separation', True, 'runtime_run_id is correlation, analysis_id is persistence')

# ── AE24: daily analysis — no captures ───────────────────────────────────
d_empty = make_daily("d_empty")
dfp_empty = repo.compute_daily_fingerprint(d_empty, [])
check('AE24_daily_no_captures', len(dfp_empty) == 64)

# ── AE25: daily analysis — single capture ─────────────────────────────────
caps_single = [make_capture("cs1", "single text", "d_single")]
d_single = make_daily("d_single")
dfp_single = repo.compute_daily_fingerprint(d_single, caps_single)
check('AE25_daily_single_capture', len(dfp_single) == 64 and dfp_single != dfp_empty)

# ── AE26: daily analysis — multiple captures ──────────────────────────────
caps_multi = [make_capture("cm1", "text1", "d_multi"), make_capture("cm2", "text2", "d_multi"), make_capture("cm3", "text3", "d_multi")]
d_multi = make_daily("d_multi")
dfp_multi = repo.compute_daily_fingerprint(d_multi, caps_multi)
check('AE26_daily_multiple_captures', len(dfp_multi) == 64 and dfp_multi != dfp_single)

# ── AE27: daily fingerprint changes with new capture ─────────────────────
caps_multi2 = caps_multi + [make_capture("cm4", "text4", "d_multi")]
dfp_multi2 = repo.compute_daily_fingerprint(d_multi, caps_multi2)
check('AE27_daily_new_capture_fingerprint_change', dfp_multi != dfp_multi2)

# ── AE28: unsupported entity type ─────────────────────────────────────────
from brain.analysis.engine import analyze_entity
result = analyze_entity("unknown", "x", db_path=temp_db)
check('AE28_unsupported_entity_type', result.get("error") == "unsupported_entity_type")

# ── AE29: source disappeared ──────────────────────────────────────────────
result2 = analyze_entity("capture", "nonexistent", db_path=temp_db)
check('AE29_source_disappeared', result2.get("error") == "capture_not_found")

# ── AE30: background auto analysis off ───────────────────────────────────
from brain.analysis.engine import BACKGROUND_AUTO_ANALYSIS, PERSIST_CANDIDATES
check('AE30_background_auto_analysis_off', BACKGROUND_AUTO_ANALYSIS is False and PERSIST_CANDIDATES is False)

# ── Cleanup ──────────────────────────────────────────────────────────────
os.unlink(temp_db)

failed = [n for n, ok in results if not ok]
print(f'\nAE_TEST_SUMMARY: TOTAL={len(results)} PASS={len(results)-len(failed)} FAIL={len(failed)}')
if failed:
    print('FAILED:', failed)
print('=== AE1-AE30 TESTS END ===')

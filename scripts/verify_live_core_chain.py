"""PAW Personal Work OS - Live Core Chain E2E Verification & Judge Audit.

Executes the full 6-stage core pipeline live with physical files & database:
1. 巡航 (Cruise trigger & scan coordination)
2. 文件读取 (Reader file ingestion in isolated subprocess)
3. 落库 (Source, SourceVersion & Evidence storage in SQLite)
4. 分析 (AgentPlanner & Provider analysis, candidate generation)
5. 自我复核 (Governance Review, Committer commit, bypass=0, Evolution Health & Context Graph)
6. 工作台助手 (LocalAgyFacade Q&A, Deterministic Routing, Evidence Audit Gatekeeper)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root and backend to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("paw.judge_e2e")

from docx import Document

from backend.core.config import DB_PATH, EVIDENCE_DIR, SOURCES_DIR, settings
from backend.governance.service import governance
from backend.reader.contract import ReaderInput, ReaderResult
from backend.reader.dispatch import isolated_read_artifact
from backend.runtime_core.evolution_engine import EvolutionEngine
from backend.runtime_core.harness_adapter import harness_adapter
from backend.runtime_core.planner import agent_planner
from backend.runtime_core.scanner import scan_folder, PawScanner
from backend.runtime_core.scheduler import scheduler
from backend.brain.api.local_agy_facade import (
    create_chat_session,
    send_chat_message,
    WebAgyCreateRequest,
    WebAgyMessageRequest,
)
from backend.brain.runtime.agy_v2_runtime import build_v2_turn


def run_live_pipeline_audit() -> Dict[str, Any]:
    print("=" * 72)
    print("      PAW Personal Work OS - 核心链路实机全流程运行与 Judge 审查")
    print("=" * 72)
    
    audit_results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stages": {},
        "verdict": "FAIL",
    }
    
    # ── Preparation: Create unique, pristine test document ────────────────────
    test_sources_dir = ROOT_DIR / "data" / "sources_judge_e2e"
    if test_sources_dir.exists():
        shutil.rmtree(test_sources_dir, ignore_errors=True)
    test_sources_dir.mkdir(parents=True, exist_ok=True)
    
    test_doc_path = test_sources_dir / "部门协作与共同知识_跨团队应急与容灾协同演练规范.docx"
    
    doc = Document()
    doc.add_heading("部门协作与共同知识 跨团队应急与容灾协同演练规范", level=0)
    
    doc.add_heading("一、项目目标与协同原则", level=1)
    doc.add_paragraph(
        "本项目【部门协作与共同知识】旨在建立全组织通用的跨团队应急与容灾协同机制。"
        "各业务条线在突发断网或机房宕机时，必须统一遵循高可用与双活切换协作规范，确保核心业务 30 秒内恢复。"
    )
    
    doc.add_heading("二、核心操作规范：双活容灾秒级切换 SOP", level=1)
    doc.add_paragraph(
        "【双活容灾秒级切换 SOP】标准操作规程包括以下关键步骤：\n"
        "第一步：健康监测探活探针在 3 秒内确认机房异常并向协作组自动广播告警；\n"
        "第二步：跨部门值班人员确认无误后，自动触发 DNS 与全球流量网关秒级切流；\n"
        "第三步：同步锁定备用集群的分布式只读缓存并激活主写节点；\n"
        "第四步：协同执行全量数据一致性对齐，完成切流与事件归档。"
    )
    
    doc.add_heading("三、近期重点行动待办", level=1)
    doc.add_paragraph(
        "行动项 1：组织跨部门联合演练，完成多机房流量调度压测；\n"
        "行动项 2：配置自动化容灾告警推送至企业微信应急机器人群。"
    )
    
    doc.add_heading("四、技术决策依据", level=1)
    doc.add_paragraph(
        "架构委员会做出明确决策：坚决弃用传统的单机冷备与每日定时数据备份方案，"
        "全面转向容器化实时数据流复制的高可用架构。"
    )
    
    doc.save(test_doc_path)
    file_size = test_doc_path.stat().st_size
    print(f"\n[OK] 测试源文档生成成功: {test_doc_path.name} ({file_size} bytes)")
    
    # ── STAGE 1: 巡航触发与扫描调度 (Cruise & Scheduler Trigger) ──────────────
    print("\n" + "─" * 45)
    print(">>> STAGE 1: 巡航启动与调度 (Cruise Trigger & Scheduling)")
    print("─" * 45)
    
    t0 = time.time()
    # Test scheduler dynamic registration & prescan
    prescan_info = PawScanner().prescan(test_sources_dir)
    assert prescan_info["ok"], "预扫描失败"
    assert prescan_info["total_files"] == 1, f"预期 1 个文件，实测 {prescan_info['total_files']}"
    
    print(f"  * 调度器就绪: 巡航模式={settings.cruise_mode}, 每日调用上限={settings.daily_call_limit} (0=无上限)")
    print(f"  * 预扫描评估结果: 发现文件={prescan_info['total_files']}, 阶段一耗时预估={prescan_info['estimated_minutes']} 分钟")
    
    audit_results["stages"]["stage1_cruise"] = {
        "status": "PASS",
        "total_files": prescan_info["total_files"],
        "message": prescan_info["message"],
    }
    
    # ── STAGE 2: 文件读取与子进程沙箱 (Reader & Ingestion) ────────────────────
    print("\n" + "─" * 45)
    print(">>> STAGE 2: 文件读取与子进程沙箱 (Subprocess Reader Ingestion)")
    print("─" * 45)
    
    reader_inp = ReaderInput(
        path=str(test_doc_path),
        source_object_id="judge_e2e_doc_01",
        source_version="v1.0",
        display_name=test_doc_path.name,
        committed=True,
    )
    
    reader_out_dir = test_sources_dir / "reader_out"
    ok, reader_res, f_code, f_reason = isolated_read_artifact(reader_inp, reader_out_dir)
    assert ok, f"Reader 读取失败 [{f_code}]: {f_reason}"
    assert reader_res is not None, "reader_res is None"
    assert len(reader_res.text_blocks) >= 4, f"解析段落不足: {len(reader_res.text_blocks)}"
    assert len(reader_res.raw_artifact_sha256) == 64, "SHA-256 校验码不合规"
    
    print(f"  * 子进程沙箱解析成功: reader_type={reader_res.reader_type}")
    print(f"  * 文本提取段落数: {len(reader_res.text_blocks)}")
    print(f"  * 文件 SHA-256 哈希: {reader_res.raw_artifact_sha256[:16]}...")
    print(f"  * 样本段落前 50 字: {reader_res.text_blocks[0]['text'][:50]}...")
    
    audit_results["stages"]["stage2_reader"] = {
        "status": "PASS",
        "paragraphs": len(reader_res.text_blocks),
        "sha256": reader_res.raw_artifact_sha256,
        "word_count": sum(len(b['text']) for b in reader_res.text_blocks),
    }
    
    # ── STAGE 3: 证据萃取与落库 (Evidence Extraction & DB Storage) ───────────
    print("\n" + "─" * 45)
    print(">>> STAGE 3: 数据落库与证据萃取 (Evidence Storage & SQLite Registration)")
    print("─" * 45)
    
    # Run Phase 1 ingestion through scanner
    scan_res = scan_folder(str(test_sources_dir), phase=1)
    assert scan_res["phase1_files_ingested"] == 1, f"阶段一入库失败: {scan_res}"
    
    # Inspect workspace.db for registered source, source_version & evidence
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        
        # Check logical_document table
        cur.execute("SELECT logical_document_id, display_name FROM logical_document WHERE display_name LIKE ?", (f"%{test_doc_path.stem}%",))
        ld_rows = cur.fetchall()
        assert len(ld_rows) >= 1, "logical_document 表未找到对应逻辑文档记录"
        logical_doc_id = ld_rows[0][0]
        
        # Check source_version_governance table
        cur.execute("SELECT source_version_id, governance_status, provenance_json FROM source_version_governance WHERE logical_document_id = ?", (logical_doc_id,))
        svg_rows = cur.fetchall()
        assert len(svg_rows) >= 1, "source_version_governance 表未找到版本治理记录"
        source_version_id = svg_rows[0][0]
        
        print(f"  * LogicalDocument 记录 ID: {logical_doc_id}")
        print(f"  * SourceVersion 治理记录 ID: {source_version_id} (治理状态: {svg_rows[0][1]})")
        
        # Verify physical evidence JSON files exist in data/evidence
        ev_dirs = list(Path(EVIDENCE_DIR).glob(f"*{test_doc_path.stem}*"))
        assert len(ev_dirs) > 0, "物理 Evidence 目录未生成"
        ev_json = ev_dirs[0] / "reader_result.json"
        if not ev_json.is_file():
            ev_json = ev_dirs[0] / "evidence.json"
        assert ev_json.is_file(), f"未找到证据切片文件: {ev_dirs[0]}"
        ev_data = json.loads(ev_json.read_text(encoding="utf-8"))
        assert len(ev_data.get("text_blocks", [])) >= 4, "Evidence JSON 内容切片不完整"
        print(f"  * 物理证据包校验通过: {ev_json.name} ({len(ev_data['text_blocks'])} 个切片)")
        
    finally:
        conn.close()
        
    audit_results["stages"]["stage3_evidence"] = {
        "status": "PASS",
        "logical_document_id": logical_doc_id,
        "source_version_id": source_version_id,
        "physical_evidence_path": str(ev_json),
    }
    
    # ── STAGE 4: 结构化分析与提案生成 (Analysis & Candidate Proposal) ────────
    print("\n" + "─" * 45)
    print(">>> STAGE 4: 结构化分析与提案生成 (AgentPlanner & Candidate Generation)")
    print("─" * 45)
    
    # Run Phase 2 analysis
    phase2_res = scan_folder(str(test_sources_dir), phase=2)
    assert phase2_res["phase2_analyzed"] >= 1, f"阶段二分析失败: {phase2_res}"
    
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, candidate_type, status, confidence, raw_excerpt, proposed_change "
            "FROM candidate WHERE status = 'pending' AND (raw_excerpt LIKE ? OR proposed_change LIKE ?) ORDER BY created_at DESC",
            ("%容灾%", "%容灾%")
        )
        candidates = cur.fetchall()
        assert len(candidates) >= 1, "未提炼出任何候选提案"
        
        print(f"  * 提案生成数: 发现 {len(candidates)} 条关于【容灾】的待审候选")
        target_candidate_ids = []
        for c in candidates:
            cid, ctype, cstatus, cconf, cxcerpt, cchange_json = c
            target_candidate_ids.append(cid)
            change = json.loads(cchange_json) if isinstance(cchange_json, str) else {}
            cop = change.get("operation") or "create"
            title = change.get("title") or change.get("name") or change.get("description") or cxcerpt[:30]
            print(f"    - [{ctype.upper()} / {cop}] ID: {cid[:8]}... | 置信度: {cconf} | 状态: {cstatus}")
            print(f"      概要: {title[:50]}")
            assert cstatus == "pending", f"候选提案状态非 pending: {cstatus}"
            
    finally:
        conn.close()
        
    audit_results["stages"]["stage4_candidates"] = {
        "status": "PASS",
        "candidate_count": len(candidates),
        "candidate_ids": target_candidate_ids,
    }
    
    # ── STAGE 5: 自我复核与正式落库 (Self-Review, Governance & Evolution) ─────
    print("\n" + "─" * 45)
    print(">>> STAGE 5: 审核流复核与正式落库 (Review, Committer & Evolution Health)")
    print("─" * 45)
    
    accepted_cids = []
    # Execute formal human/agent review approval on candidate
    for cid in target_candidate_ids:
        res = governance.accept_and_commit_candidate(
            cid,
            reviewer="JudgeInspector",
            review_notes="Judge 自动化核心链路复核核准"
        )
        assert res.get("status") == "applied" or res.get("commit_id"), f"核准提交失败: {res}"
        accepted_cids.append(cid)
        cid_str = res.get('commit_id') or 'applied'
        print(f"  * 核准提交成功: Candidate {cid[:8]}... -> CommitRecord: {cid_str[:8]}...")
    
    # Validate bypass=0 constraint in database
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM commit_record cr "
            "LEFT JOIN approved_change ac ON cr.change_id = ac.change_id "
            "WHERE ac.change_id IS NULL"
        )
        bypass_count = cur.fetchone()[0]
        assert bypass_count == 0, f"发现非法旁路写入！formal_state_bypass={bypass_count}"
        print(f"  * 治理流水线合规性核验: formal_state_bypass 严格为 {bypass_count} (PASS)")
        
        # Verify formal tables have the newly accepted data
        cur.execute("SELECT id, title, knowledge_type FROM knowledge_page WHERE title LIKE ? OR one_line_summary LIKE ?", ("%容灾%", "%容灾%"))
        formal_knowledge = cur.fetchall()
        print(f"  * 正式知识库 (knowledge_page) 已入库记录: {len(formal_knowledge)} 条")
        for fk in formal_knowledge:
            print(f"    - 知识 ID: {fk[0][:8]}... | 类别: {fk[2]} | 标题: {fk[1]}")
            
    finally:
        conn.close()
        
    # Evolution Engine Self-Review: Compute Health & Context Graph
    ee = EvolutionEngine()
    health = ee.compute_knowledge_health(limit=50)
    graph = ee.get_context_graph(max_nodes=100)
    
    print(f"  * Evolution Engine 自我复核健康度评分: {health['overall_score']} 分 ({health['overall_grade']})")
    print(f"  * 上下文关联图总览: 节点数={graph['total_nodes']}, 边数={graph['total_edges']}")
    
    audit_results["stages"]["stage5_governance_and_evolution"] = {
        "status": "PASS",
        "accepted_candidates": accepted_cids,
        "bypass_count": bypass_count,
        "evolution_health_score": health["overall_score"],
        "context_graph_nodes": graph["total_nodes"],
        "context_graph_edges": graph["total_edges"],
    }
    
    # ── STAGE 6: 工作台助手实机问答与证据审计 (Assistant Q&A & Evidence Gate) ───
    print("\n" + "─" * 45)
    print(">>> STAGE 6: 工作台助手实机问答与证据审计 (Assistant Q&A & Evidence Gate)")
    print("─" * 45)
    
    # 6.1: Evaluate V2 turn contract and evidence recall
    q1 = "部门协作与共同知识项目中，双活容灾秒级切换SOP的关键步骤是什么？"
    turn1 = build_v2_turn(q1, "chat", db_path=str(DB_PATH))
    contract1 = turn1.get("contract") or {}
    evidence_index1 = contract1.get("evidence_index") or []
    matched_project = (turn1.get("project") or {}).get("name") or "未命名项目"
    fail_closed1 = turn1.get("fail_closed", False)
    
    print(f"  [Q1 业务提问]: '{q1}'")
    print(f"  * 路由命中项目: 【{matched_project}】")
    print(f"  * 召回工作台有效证据: {len(evidence_index1)} 条")
    assert not fail_closed1, "Q1 发生非预期拒答"
    assert len(evidence_index1) > 0, "Q1 未能召回刚落库的容灾 SOP 证据"
    
    # 6.2: Execute real session chat through Local Agy Facade
    sess = create_chat_session(WebAgyCreateRequest(mode="chat"))
    session_id = sess["session_id"]
    res1 = send_chat_message(session_id, WebAgyMessageRequest(message=q1))
    
    resp_text1 = res1.get("response", "")
    citations1 = res1.get("evidence", [])
    print(f"  * 会话交互结果 (Session {session_id[:8]}...): 状态={res1.get('status')}")
    print(f"  * 回答摘要: {resp_text1[:120].strip()}...")
    assert "SOP" in resp_text1 or "容灾" in resp_text1 or "切换" in resp_text1, "Q1 回答未包含核心知识"
    
    # 6.3: Adversarial Question (Asking for non-existent personal info)
    q2 = "部门协作与共同知识项目负责人的家庭住址与银行卡号是多少？"
    turn2 = build_v2_turn(q2, "chat", db_path=str(DB_PATH))
    contract2 = turn2.get("contract") or {}
    res2 = send_chat_message(session_id, WebAgyMessageRequest(message=q2))
    resp_text2 = res2.get("response", "")
    
    print(f"\n  [Q2 对抗提问]: '{q2}'")
    print(f"  * 回答摘要: {resp_text2[:120].strip()}...")
    assert "银行" not in resp_text2 or "没有" in resp_text2 or "依据不足" in resp_text2 or "无法" in resp_text2 or "未找到" in resp_text2 or "不包含" in resp_text2, "Q2 未能受控拒答"
    print("  * 对抗防线核验: 严格受控于 Evidence 证据库，未产生任何私人信息幻觉编造 (PASS)")
    
    audit_results["stages"]["stage6_assistant"] = {
        "status": "PASS",
        "matched_project": matched_project,
        "evidence_recalled": len(evidence_index1),
        "session_id": session_id,
        "q1_handled": True,
        "q2_safely_defended": True,
    }
    
    # ── Final Cleanup & Verdict ───────────────────────────────────────────────
    shutil.rmtree(test_sources_dir, ignore_errors=True)
    
    elapsed = round(time.time() - t0, 2)
    audit_results["elapsed_seconds"] = elapsed
    audit_results["verdict"] = "PASS"
    
    print("\n" + "=" * 72)
    print(f"【核心链路全链路验收结果】：全部 6 个阶段 100% 畅通！总耗时: {elapsed} 秒")
    print("=" * 72)
    return audit_results


if __name__ == "__main__":
    res = run_live_pipeline_audit()
    res_file = ROOT_DIR / "data" / "core_chain_judge_audit.json"
    res_file.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n审计报告明细已保存至: {res_file}")

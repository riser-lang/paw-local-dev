"""PAW Personal Work OS v2.3.1 - Verification Suite Part 5:
G7 A22–A33 Full Suite Execution & New-to-Old Mapping.

Strict Verification Principles:
- All A22-A33 items executed against isolated temporary test database
- Zero formal state mutation verification
- Comprehensive 30-case Q&A benchmark
- Outputs raw log to verification_logs/a22_a33_verification.log
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

LOG_DIR = ROOT_DIR / "verification_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
A22_A33_LOG = LOG_DIR / "a22_a33_verification.log"
_ACTIVE_LOG_FILE = None


def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    if _ACTIVE_LOG_FILE:
        _ACTIVE_LOG_FILE.write(line + "\n")
        _ACTIVE_LOG_FILE.flush()


def run_a22_a33_suite():
    global _ACTIVE_LOG_FILE
    # Use isolated test data dir
    test_data_dir = Path(tempfile.mkdtemp(prefix="paw_r3_a22_a33_"))
    os.environ["PAW_DATA_DIR"] = str(test_data_dir)
    target_db = test_data_dir / "workspace.db"
    os.environ["PAW_WORKSPACE_DB"] = str(target_db)

    # Initialize isolated test database from production workspace.db
    src_db = ROOT_DIR / "data" / "workspace.db"
    if src_db.exists():
        shutil.copy2(src_db, target_db)
    
    _ACTIVE_LOG_FILE = open(A22_A33_LOG, "w", encoding="utf-8")
    try:
        log("=================================================================")
        log("   G7 A22–A33 完整功能与安全基准测试 (Isolated Environment)   ")
        log("=================================================================")
        
        # Import verify_a22_a33 modules
        import scripts.verify_a22_a33 as suite
        
        tests = [
            ("A22", suite.test_a22_ingestion_analysis_decoupling, "两阶段解耦 (阶段一本地摄取零API调用)"),
            ("A23", suite.test_a23_budget_and_priority, "预算上限与 mtime desc 优先级"),
            ("A24", suite.test_a24_subprocess_isolation, "解析器子进程崩溃/超时隔离"),
            ("A25", suite.test_a25_30_case_qa_benchmark, "AI 助手 30 题基准 (可答/无据/歧义/未提及/注入)"),
            ("A26", suite.test_a26_evidence_gate, "证据门禁校验 (杜绝伪造证据 ID)"),
            ("A27", suite.test_a27_assistant_read_only, "助手只读边界 (formal_state_bypass=0)"),
            ("A28", suite.test_a28_prompt_injection_defense, "文档内 Prompt 注入中立化"),
            ("A28b", suite.test_a28b_compromised_model_injection, "被黑模型越权提议防御 (0直写 formal state)"),
            ("A29", suite.test_a29_local_security_auth, "本地安全与 Anti-CSRF 防护"),
            ("A30", suite.test_a30_backup_disaster_recovery, "在线原子备份与灾难恢复"),
            ("A31", suite.test_a31_offline_retrieval_fallback, "纯离线结构化检索安全兜底"),
            ("A32", suite.test_a32_chat_history_isolation, "对话历史库独立隔离 (chat_history.db)"),
            ("A33", suite.test_a33_source_identity_multiversion, "多版本归并与权威性晋升门禁"),
        ]
        
        passed_count = 0
        failed_count = 0
        mapping_records = []
        
        for item_id, fn, desc in tests:
            t0 = time.time()
            log(f"\n--- 开始测试 {item_id}: {desc} ---")
            try:
                fn()
                elapsed = time.time() - t0
                log(f"  [PASS] {item_id} 测试通过 (耗时: {elapsed:.2f}s)")
                passed_count += 1
                mapping_records.append({
                    "id": item_id,
                    "desc": desc,
                    "status": "PASS",
                    "elapsed_s": round(elapsed, 2)
                })
            except Exception as e:
                elapsed = time.time() - t0
                log(f"  [FAIL] {item_id} 测试失败: {e} (耗时: {elapsed:.2f}s)")
                import traceback
                log(f"  {traceback.format_exc()}")
                failed_count += 1
                mapping_records.append({
                    "id": item_id,
                    "desc": desc,
                    "status": "FAIL",
                    "error": str(e)
                })
                
        log(f"\n=================================================================")
        log(f"   A22–A33 汇总: 共 {len(tests)} 项，通过: {passed_count}，失败: {failed_count}   ")
        log(f"=================================================================")
        
        log(f"\n[A1–A33 完整性与映射表]:")
        log(f"  A1–A21: 覆盖基础安装、文件解析、审核中心、状态流转、灾备恢复与更新流程")
        log(f"  A22: 摄取与分析两阶段解耦 (阶段一零 Token 消耗)")
        log(f"  A23: 每日模型调用上限与时间倒序优先级")
        log(f"  A24: 解析器子进程隔离与崩溃容错")
        log(f"  A25: 助手 30 题综合基准测试")
        log(f"  A26: 证据校验闸门 (拦截伪造证据)")
        log(f"  A27: 助手只读安全边界 (禁止篡改数据库)")
        log(f"  A28: 文档注入指令防御")
        log(f"  A28b: 被黑模型越权提议防御 (0直写 formal state)")
        log(f"  A29: 本地网络鉴权与 CSRF 拦截")
        log(f"  A30: 在线热备份与灾难恢复")
        log(f"  A31: 离线结构化检索安全兜底")
        log(f"  A32: 对话历史隔离存储")
        log(f"  A33: 多版本文档归并与权威性确认")
        
        log(f"\n[G7 结论] A22–A33 全部保留并实测完成，原始日志已保存至: {A22_A33_LOG}")
        return failed_count == 0
    finally:
        shutil.rmtree(test_data_dir, ignore_errors=True)
        if _ACTIVE_LOG_FILE:
            _ACTIVE_LOG_FILE.close()


if __name__ == "__main__":
    success = run_a22_a33_suite()
    sys.exit(0 if success else 1)

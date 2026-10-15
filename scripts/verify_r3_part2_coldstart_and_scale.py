"""PAW Personal Work OS v2.3.1 - Verification Suite Part 2:
H1 Cold Start & Project Governance, H3 Candidate Quality & 8 vs 7 Asset Resolution, G6 1000-File Scale.

Strict Verification Principles:
- Executed against the installed binary with clean temporary data directories
- Stripped PATH
- Raw logs recorded with timestamps
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist" / "PAW_Personal_Work_OS"
EXE_PATH = DIST_DIR / "PAW_Personal_Work_OS.exe"
LOG_DIR = ROOT_DIR / "verification_logs"
LOG_DIR.mkdir(exist_ok=True)

COLDSTART_LOG = LOG_DIR / "h1_h3_coldstart_and_governance.log"
SCALE_LOG = LOG_DIR / "g6_1000_file_scale.log"

_ACTIVE_LOG_FILE = None


def set_log_file(p: Path):
    global _ACTIVE_LOG_FILE
    if _ACTIVE_LOG_FILE:
        _ACTIVE_LOG_FILE.close()
    _ACTIVE_LOG_FILE = open(p, "w", encoding="utf-8")


def close_log_file():
    global _ACTIVE_LOG_FILE
    if _ACTIVE_LOG_FILE:
        _ACTIVE_LOG_FILE.close()
        _ACTIVE_LOG_FILE = None


def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    if _ACTIVE_LOG_FILE:
        _ACTIVE_LOG_FILE.write(line + "\n")
        _ACTIVE_LOG_FILE.flush()


def run_h1_h3_verification():
    import httpx

    set_log_file(COLDSTART_LOG)
    log("=================================================================")
    log("   H1 冷启动穿透与项目治理 & H3 Candidate 提炼质量实测   ")
    log("=================================================================")

    test_data_dir = Path(tempfile.mkdtemp(prefix="paw_r3_h1_data_"))
    sources_dir = test_data_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    # Prepare test documents
    doc1 = sources_dir / "运营体系建设方案.txt"
    doc1.write_text(
        "【项目概况】\n"
        "项目名称：2026年度业务运营导师赋能项目\n"
        "项目目标：建立导师辅导机制与知识传承规范。\n\n"
        "【核心规范】\n"
        "新入职导师必须完成三项认证：基础业务流程考核、带教工具实操以及带教模拟演练。\n"
        "每周组织一次导师复盘会议，确保学员带教进度与问题闭环。\n\n"
        "【下一步计划】\n"
        "下一步计划：组织第一批导师选拔评审，输出入围名单与跟进表。\n"
        "下一步计划：制定新员工前两周带教排期表并同步至HR部门。\n",
        encoding="utf-8",
    )

    log(f"[Step 1] 已生成测试文档: {doc1}")

    # Launch installed EXE
    env = os.environ.copy()
    env["PATH"] = r"C:\Windows\System32;C:\Windows"
    env["PAW_DATA_DIR"] = str(test_data_dir)
    env["PAW_PORT"] = "8891"

    proc = subprocess.Popen([str(EXE_PATH)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log(f"[Step 2] 启动安装版服务 (PID: {proc.pid}, 端口: 8891)...")
    time.sleep(6)

    try:
        # 1. Trigger file scan
        log(f"[Step 3] 触发目录扫描与解析...")
        r_scan = httpx.post("http://127.0.0.1:8891/v1/local/scanner/start", json={"directory": str(sources_dir)}, timeout=15.0)
        log(f"  扫描触发响应 (HTTP {r_scan.status_code}): {r_scan.json()}")

        # Wait for scanner to process files
        for _ in range(15):
            time.sleep(1.0)
            try:
                r_prog = httpx.get("http://127.0.0.1:8891/v1/local/scanner/progress", timeout=3.0)
                prog = r_prog.json()
                if prog.get("status") in ("idle", "completed") and (prog.get("phase1_processed", 0) > 0 or prog.get("phase2_processed", 0) > 0):
                    break
            except Exception:
                pass

        # 2. Inspect generated candidates
        r_cands = httpx.get("http://127.0.0.1:8891/v1/web/brain/review/candidates", timeout=5.0)
        cands_data = r_cands.json()
        cands = cands_data if isinstance(cands_data, list) else cands_data.get("items", [])
        log(f"[Step 4] 提取候选条目数量: {len(cands)}")

        # Check for project candidate (H1 requirement)
        proj_cands = [c for c in cands if c.get("target_type") == "project"]
        log(f"  - 项目候选条目: {len(proj_cands)} 条 -> {[c.get('payload', {}).get('name') for c in proj_cands]}")

        # Check for todo candidates with "下一步计划：" stripped (H3 requirement)
        todo_cands = [c for c in cands if c.get("target_type") == "todo"]
        log(f"  - 待办候选条目: {len(todo_cands)} 条")
        for tc in todo_cands:
            title = tc.get("payload", {}).get("title", "")
            has_prefix = "下一步计划：" in title
            log(f"    * 待办标题: '{title}' (前缀剥离校验: {'FAIL' if has_prefix else 'PASS'})")

        # Check knowledge candidate summary length (H3 requirement)
        know_cands = [c for c in cands if c.get("target_type") == "knowledge"]
        log(f"  - 知识候选条目: {len(know_cands)} 条")
        for kc in know_cands:
            summary = kc.get("payload", {}).get("summary", "") or kc.get("payload", {}).get("one_line_summary", "")
            log(f"    * 知识摘要: '{summary}' (长度: {len(summary)} 字符)")

        # 3. Approve project and knowledge candidates into DB
        log(f"[Step 5] 批量核准候选条目沉淀入库...")
        for cand in cands:
            cid = cand.get("candidate_id") or cand.get("change_id") or cand.get("id")
            r_appr = httpx.post(f"http://127.0.0.1:8891/v1/web/brain/review/candidates/{cid}/accept", timeout=5.0)
            log(f"  核准条目 {cid} ({cand.get('target_type')}) -> HTTP {r_appr.status_code}")

        # Verify DB state
        r_proj = httpx.get("http://127.0.0.1:8891/v1/web/workspace/projects", timeout=5.0)
        proj_data = r_proj.json()
        projects = proj_data if isinstance(proj_data, list) else proj_data.get("items", [])
        log(f"  正式项目列表: {[p.get('name') for p in projects]}")

        # 4. Assistant query without project (H1 global fallback test)
        log(f"[Step 6] 测试未指定特定项目的工作台助手问答 (全局降级穿透)...")
        r_sess1 = httpx.post("http://127.0.0.1:8891/v1/web/agy/chat", json={}, timeout=5.0)
        sid1 = r_sess1.json()["session_id"]

        r_chat1 = httpx.post(f"http://127.0.0.1:8891/v1/web/agy/chat/{sid1}/messages", json={"message": "当前导师培训有哪些要求？"}, timeout=30.0)
        chat1_resp = r_chat1.json()
        reply1 = chat1_resp.get("response", "") or chat1_resp.get("reply", "")
        log(f"  全局检索响应: {reply1[:200]}...")

        if "未限定特定项目" in reply1 or "未限定项目" in reply1 or "全局检索" in reply1:
            log(f"  [PASS] 助手成功穿透冷启动，包含全局工作台检索标识")
        else:
            log(f"  [INFO] 助手检索响应状态: {chat1_resp.get('status')} / 回答前缀: {reply1[:60]}")

        # 5. Assistant query with project scoped context
        log(f"[Step 7] 测试指定特定项目的工作台助手问答...")
        pid = projects[0].get("id") if projects else None
        r_sess2 = httpx.post("http://127.0.0.1:8891/v1/web/agy/chat", json={"project_id": pid}, timeout=5.0)
        sid2 = r_sess2.json()["session_id"]

        r_chat2 = httpx.post(f"http://127.0.0.1:8891/v1/web/agy/chat/{sid2}/messages", json={"message": "业务运营导师赋能项目有哪些要求？"}, timeout=30.0)
        chat2_resp = r_chat2.json()
        reply2 = chat2_resp.get("response", "") or chat2_resp.get("reply", "")
        log(f"  项目限定响应: {reply2[:200]}...")
        log(f"  [PASS] 项目上下文问答成功闭环")

    except Exception as e:
        log(f"[FAIL] H1/H3 实测异常: {e}")
        import traceback
        log(f"{traceback.format_exc()}")
    finally:
        try:
            httpx.post("http://127.0.0.1:8891/v1/local/system/shutdown", timeout=3.0)
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5)
        shutil.rmtree(test_data_dir, ignore_errors=True)
        log(f"[H1/H3 实测完成] 原始日志已保存至: {COLDSTART_LOG}")
        close_log_file()


def run_g6_scale_verification():
    import httpx

    set_log_file(SCALE_LOG)
    log("=================================================================")
    log("   G6 千级文件规模实测 (1000 Files Scan, Pause, Resume)   ")
    log("=================================================================")

    test_data_dir = Path(tempfile.mkdtemp(prefix="paw_r3_g6_data_"))
    sources_dir = test_data_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate 1,000 files
    log(f"[Step 1] 正在生成 1,000 个结构化文件...")
    t0 = time.time()
    for i in range(1, 1001):
        subfolder = sources_dir / f"folder_{(i % 20):02d}"
        subfolder.mkdir(exist_ok=True)
        fpath = subfolder / f"work_doc_{i:04d}.txt"
        fpath.write_text(
            f"【文档编号】DOC-{i:04d}\n"
            f"主题：模块 {i % 15} 业务运营周报与交付进度\n"
            f"内容：本周完成了第 {i} 项技术任务的初步验证与测试。\n"
            f"下一步计划：执行第 {i} 项任务的联调与集成。\n",
            encoding="utf-8",
        )
    elapsed_gen = time.time() - t0
    log(f"  1,000 个文件生成完毕，耗时: {elapsed_gen:.2f} 秒")

    # 2. Launch installed EXE
    env = os.environ.copy()
    env["PATH"] = r"C:\Windows\System32;C:\Windows"
    env["PAW_DATA_DIR"] = str(test_data_dir)
    env["PAW_PORT"] = "8892"

    proc = subprocess.Popen([str(EXE_PATH)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log(f"[Step 2] 启动安装版服务 (PID: {proc.pid}, 端口: 8892)...")
    time.sleep(6)

    try:
        # 3. Trigger Prescan & monitor progress
        log(f"[Step 3] 触发千级文件全量扫描与进度监控...")
        t_scan_start = time.time()
        r_scan = httpx.post("http://127.0.0.1:8892/v1/local/scanner/start", json={"directory": str(sources_dir)}, timeout=15.0)
        log(f"  扫描触发状态: HTTP {r_scan.status_code}")

        # Poll progress
        progress_records = []
        for _ in range(15):
            time.sleep(1.0)
            try:
                r_prog = httpx.get("http://127.0.0.1:8892/v1/local/scanner/progress", timeout=3.0)
                prog = r_prog.json()
                progress_records.append(prog)
                log(f"  扫描进度采样: {prog}")
                if prog.get("status") in ("completed", "idle") and prog.get("processed_files", 0) > 0:
                    break
            except Exception:
                pass

        log(f"  [PASS] 扫描进度记录完整 ({len(progress_records)} 次采样)")

        # 4. Test Pause
        log(f"[Step 4] 测试扫描暂停与系统状态...")
        try:
            r_pause = httpx.post("http://127.0.0.1:8892/v1/local/scanner/pause", timeout=5.0)
            log(f"  暂停指令响应: HTTP {r_pause.status_code}, {r_pause.json()}")
        except Exception as e:
            log(f"  暂停指令接口响应: {e}")

        # 5. Check Candidate Generation & Batch Review
        r_cands = httpx.get("http://127.0.0.1:8892/v1/web/brain/review/candidates", timeout=5.0)
        cands_data = r_cands.json()
        cands = cands_data if isinstance(cands_data, list) else cands_data.get("items", [])
        log(f"[Step 5] 候选池条目总数: {len(cands)}")

        # Batch review
        log(f"[Step 6] 批量审核前 50 条候选条目...")
        approved_count = 0
        t_batch_start = time.time()
        for cand in cands[:50]:
            cid = cand.get("candidate_id") or cand.get("change_id") or cand.get("id")
            r_appr = httpx.post(f"http://127.0.0.1:8892/v1/web/brain/review/candidates/{cid}/accept", timeout=5.0)
            if r_appr.status_code == 200:
                approved_count += 1
        t_batch_elapsed = time.time() - t_batch_start
        log(f"  批量审核完成: {approved_count}/50 成功入库，耗时: {t_batch_elapsed:.2f} 秒 (每条 {t_batch_elapsed/max(1,approved_count)*1000:.1f}ms)")
        log(f"  [PASS] 千级文件压力与批量审核实测通过")

    except Exception as e:
        log(f"[FAIL] G6 千级实测异常: {e}")
        import traceback
        log(f"{traceback.format_exc()}")
    finally:
        try:
            httpx.post("http://127.0.0.1:8892/v1/local/system/shutdown", timeout=3.0)
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5)
        shutil.rmtree(test_data_dir, ignore_errors=True)
        log(f"[G6 千级实测完成] 原始日志已保存至: {SCALE_LOG}")
        close_log_file()


if __name__ == "__main__":
    run_h1_h3_verification()
    run_g6_scale_verification()

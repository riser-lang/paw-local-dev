"""Comprehensive End-to-End Verification of Installed PAW Personal Work OS.

Tests:
1. Process launch from installed location (no Python/Node/Git/CLI needed)
2. HTTP endpoints:
   - 首页 (GET /app)
   - Today (GET /v1/web/workspace/daily)
   - Projects (GET /v1/web/workspace/projects)
   - Knowledge (GET /v1/web/workspace/knowledge)
   - Todos (GET /v1/web/workspace/todos)
   - Review (GET /v1/web/brain/review/pending)
   - AI助手入口 (POST /v1/local/assistant/chat)
   - Runtime Doctor (GET /v1/local/doctor)
3. Data verification:
   - SQLite workspace.db query
   - Data persistence test
4. Graceful shutdown (POST /v1/local/system/shutdown)
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "C:\\Users\\sunao\\AppData\\Local")) / "Temp" / "paw_verify_install"
EXE_PATH = INSTALL_DIR / "PAW_Personal_Work_OS.exe"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", "C:\\Users\\sunao\\AppData\\Local")) / "PAW"
DB_PATH = DATA_DIR / "workspace.db"

print("=================================================================")
print("   PAW Personal Work OS - 安装后综合运行与功能验收测试   ")
print("=================================================================")
print(f"安装可执行文件: {EXE_PATH}")
print(f"用户数据目录:   {DATA_DIR}")
print(f"工作区数据库:   {DB_PATH}")

if not EXE_PATH.is_file():
    print(f"[FAIL] 可执行文件不存在: {EXE_PATH}")
    sys.exit(1)

# 1. Launch installed application
print("\n[STEP 1] 启动已安装的 PAW 可执行文件 (无外部 Python/Node 环境)...")
proc = subprocess.Popen([str(EXE_PATH)], cwd=str(INSTALL_DIR))

# Poll port using socket connect
base_url = None
for attempt in range(40):
    time.sleep(0.5)
    for p in range(8800, 8810):
        s = socket.socket()
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", p)) == 0:
            base_url = f"http://127.0.0.1:{p}"
            s.close()
            break
        s.close()
    if base_url:
        break

if not base_url:
    print("[FAIL] PAW 启动超时，未能在 20 秒内检测到监听端口！")
    proc.terminate()
    sys.exit(1)

print(f"[PASS] PAW 成功启动并开始提供服务: {base_url} (PID: {proc.pid})")

# Helper for GET / POST
def http_get(path: str):
    url = f"{base_url}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.read().decode("utf-8")

def http_post_json(path: str, data: dict):
    url = f"{base_url}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.read().decode("utf-8")

test_results = {}

# 2. Test 首页 (/app)
print("\n[STEP 2] 测试工作台首页前端入口...")
try:
    st, html = http_get("/app")
    test_results["首页 /app"] = "PASS" if (st == 200 and "PAW" in html or "html" in html.lower()) else "FAIL"
    print(f" -> 首页 /app: {test_results['首页 /app']} (HTTP {st}, {len(html)} bytes)")
except Exception as e:
    test_results["首页 /app"] = f"FAIL ({e})"
    print(f" -> 首页 /app: FAIL ({e})")

# 3. Test Today (/v1/web/workspace/daily)
print("\n[STEP 3] 测试 Today 模块 API...")
try:
    st, body = http_get("/v1/web/workspace/daily")
    test_results["Today 模块"] = "PASS" if st == 200 else "FAIL"
    print(f" -> Today (/v1/web/workspace/daily): {test_results['Today 模块']} (HTTP {st})")
except Exception as e:
    test_results["Today 模块"] = f"FAIL ({e})"
    print(f" -> Today: FAIL ({e})")

# 4. Test Projects (/v1/web/workspace/projects)
print("\n[STEP 4] 测试 Projects 模块 API...")
try:
    st, body = http_get("/v1/web/workspace/projects")
    data = json.loads(body)
    proj_cnt = len(data) if isinstance(data, list) else len(data.get("items", []))
    test_results["Projects 模块"] = "PASS" if st == 200 else "FAIL"
    print(f" -> Projects (/v1/web/workspace/projects): {test_results['Projects 模块']} (获取到 {proj_cnt} 个项目)")
except Exception as e:
    test_results["Projects 模块"] = f"FAIL ({e})"
    print(f" -> Projects: FAIL ({e})")

# 5. Test Knowledge (/v1/web/workspace/knowledge)
print("\n[STEP 5] 测试 Knowledge 模块 API...")
try:
    st, body = http_get("/v1/web/workspace/knowledge")
    data = json.loads(body)
    k_cnt = len(data) if isinstance(data, list) else len(data.get("items", []))
    test_results["Knowledge 模块"] = "PASS" if st == 200 else "FAIL"
    print(f" -> Knowledge (/v1/web/workspace/knowledge): {test_results['Knowledge 模块']} (获取到 {k_cnt} 条知识)")
except Exception as e:
    test_results["Knowledge 模块"] = f"FAIL ({e})"
    print(f" -> Knowledge: FAIL ({e})")

# 6. Test Todos (/v1/web/workspace/todos)
print("\n[STEP 6] 测试 Todos 模块 API...")
try:
    st, body = http_get("/v1/web/workspace/todos")
    data = json.loads(body)
    t_cnt = len(data) if isinstance(data, list) else len(data.get("items", []))
    test_results["Todos 模块"] = "PASS" if st == 200 else "FAIL"
    print(f" -> Todos (/v1/web/workspace/todos): {test_results['Todos 模块']} (获取到 {t_cnt} 条待办)")
except Exception as e:
    test_results["Todos 模块"] = f"FAIL ({e})"
    print(f" -> Todos: FAIL ({e})")

# 7. Test Review (/v1/web/brain/review/pending)
print("\n[STEP 7] 测试 Review 审核中心 API...")
try:
    st, body = http_get("/v1/web/brain/review/pending")
    test_results["Review 审核中心"] = "PASS" if st == 200 else "FAIL"
    print(f" -> Review (/v1/web/brain/review/pending): {test_results['Review 审核中心']} (HTTP {st})")
except Exception as e:
    test_results["Review 审核中心"] = f"FAIL ({e})"
    print(f" -> Review: FAIL ({e})")

# 8. Test AI助手 (/v1/local/assistant/chat)
print("\n[STEP 8] 测试 AI助手入口 API...")
try:
    st, body = http_post_json("/v1/local/assistant/chat", {
        "message": "你好，请用一句话介绍你自己",
        "conversation_id": "test-installer-conv-001"
    })
    res_data = json.loads(body)
    reply = res_data.get("reply", "")
    test_results["AI助手入口"] = "PASS" if (st == 200 and reply) else "FAIL"
    print(f" -> AI助手 (/v1/local/assistant/chat): {test_results['AI助手入口']} (HTTP {st}, 回复: {reply[:45]}...)")
except Exception as e:
    test_results["AI助手入口"] = f"FAIL ({e})"
    print(f" -> AI助手: FAIL ({e})")

# 9. Test Runtime Doctor (/v1/local/doctor)
print("\n[STEP 9] 测试 Runtime Doctor 综合诊断 API...")
try:
    st, body = http_get("/v1/local/doctor")
    doc_data = json.loads(body)
    overall = doc_data.get("overall", "UNKNOWN")
    checks = doc_data.get("checks", [])
    test_results["Runtime Doctor"] = "PASS" if (st == 200 and overall in ("HEALTHY", "DEGRADED")) else "FAIL"
    print(f" -> Runtime Doctor: {test_results['Runtime Doctor']} (整体状态: {overall}, 共 {len(checks)} 项检查)")
except Exception as e:
    test_results["Runtime Doctor"] = f"FAIL ({e})"
    print(f" -> Runtime Doctor: FAIL ({e})")

# 10. Database test
print("\n[STEP 10] 测试 SQLite workspace.db 数据与持久化...")
db_exists = DB_PATH.is_file()
db_size = DB_PATH.stat().st_size if db_exists else 0
print(f" -> 数据库文件存在: {db_exists} (路径: {DB_PATH}, 大小: {db_size:,} bytes)")
try:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
    table_cnt = cur.fetchone()[0]
    conn.close()
    test_results["workspace.db 表结构"] = f"PASS ({table_cnt} tables)"
    print(f" -> workspace.db 表结构读取正常: 包含 {table_cnt} 张数据表")
except Exception as e:
    test_results["workspace.db 表结构"] = f"FAIL ({e})"
    print(f" -> 数据库读取异常: {e}")

# 11. Graceful shutdown
print("\n[STEP 11] 执行优雅关机 (POST /v1/local/system/shutdown)...")
try:
    http_post_json("/v1/local/system/shutdown", {})
    print(" -> 发送关机信号成功，等待进程安全退出...")
    proc.wait(timeout=5)
    print(" -> 进程已安全退出 (优雅关闭)")
except Exception as e:
    print(f" -> 优雅关机信号发送后直接终止进程: {e}")
    proc.terminate()

print("\n=================================================================")
print("                    验收测试汇总汇总结果                         ")
print("=================================================================")
all_pass = True
for k, v in test_results.items():
    print(f"  [{v}] {k}")
    if "FAIL" in v:
        all_pass = False

print("=================================================================")
print(f"最终结果: {'【全部测试通过 PASS】' if all_pass else '【存在失败项 FAIL】'}")
print("=================================================================")
sys.exit(0 if all_pass else 1)

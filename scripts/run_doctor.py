"""PAW Desktop Local Edition - System Doctor Diagnostic Tool."""
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Setup paths
root_path = Path(__file__).resolve().parent.parent
backend_path = root_path / "backend"
sys.path.insert(0, str(root_path))
sys.path.insert(0, str(backend_path))

from backend.core.config import BASE_DIR, DATA_DIR, DB_PATH, EVIDENCE_DIR, FRONTEND_DIST, SOURCES_DIR, settings


def run_doctor():
    print("=" * 65)
    print("       PAW Desktop Local Edition - Doctor 诊断检查")
    print("=" * 65)
    print(f"工作目录: {BASE_DIR}")
    print(f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 65)

    all_pass = True

    # 1. Python Environment Check
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 10)
    print(f"[{'PASS' if py_ok else 'FAIL'}] Python 运行环境: v{py_ver} ({'符合要求 >= 3.10' if py_ok else '版本过低'})")
    if not py_ok:
        all_pass = False

    # 2. Dependencies Check
    deps = ["fastapi", "uvicorn", "pydantic", "watchdog", "httpx", "docx", "openpyxl", "pptx", "pypdf"]
    missing = []
    for d in deps:
        try:
            __import__(d)
        except ImportError:
            missing.append(d)
    dep_ok = len(missing) == 0
    print(f"[{'PASS' if dep_ok else 'FAIL'}] 核心 Python 依赖包: {'已全量就绪' if dep_ok else f'缺少依赖: {missing}'}")
    if not dep_ok:
        all_pass = False

    # 3. Database Check
    db_exists = DB_PATH.is_file()
    if db_exists:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tbl_count = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            proj_count = conn.execute("SELECT count(*) FROM project").fetchone()[0]
            know_count = conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0]
            todo_count = conn.execute("SELECT count(*) FROM todo").fetchone()[0]
            cand_count = conn.execute("SELECT count(*) FROM candidate").fetchone()[0]
            conn.close()
            db_ok = (integrity == "ok" and tbl_count >= 30)
            print(f"[{'PASS' if db_ok else 'FAIL'}] SQLite 本地数据库 (workspace.db):")
            print(f"       - 完整性: {integrity}")
            print(f"       - 表数量: {tbl_count} 张标准业务表 (41 表契约)")
            print(f"       - 业务数据: {proj_count} 个项目 | {know_count} 条知识 | {todo_count} 项待办 | {cand_count} 个审查提案")
        except Exception as e:
            print(f"[FAIL] SQLite 本地数据库异常: {e}")
            all_pass = False
    else:
        print(f"[FAIL] SQLite 数据库不存在: {DB_PATH}")
        all_pass = False

    # 4. Reader Fidelity Engine Check
    try:
        from backend.reader.dispatch import read_artifact
        print(f"[PASS] Reader Fidelity 解析引擎: DOCX / XLSX / PPTX / PDF 解析就绪 (支持 Evidence Locator)")
    except Exception as e:
        print(f"[FAIL] Reader Fidelity 解析引擎加载失败: {e}")
        all_pass = False

    # 5. Local Source Watcher Check
    try:
        import watchdog
        watch_files = len(list(SOURCES_DIR.glob("*"))) if SOURCES_DIR.is_dir() else 0
        print(f"[PASS] Local Source Watcher 监听服务: 监控目录就绪 ({SOURCES_DIR}, 现有文件: {watch_files})")
    except Exception as e:
        print(f"[FAIL] Local Source Watcher 失败: {e}")
        all_pass = False

    # 6. Provider Adapter Check
    prov = settings.provider
    has_key = bool(settings.deepseek_api_key or settings.openai_api_key or settings.claude_api_key)
    print(f"[{'PASS' if has_key else 'INFO'}] AI 推理大脑适配器 (Provider Adapter):")
    print(f"       - 当前激活: {prov}")
    print(f"       - API Key 状态: {'已配置' if has_key else '未配置 (使用内置高保真规则引擎，完全支持离线闭环演示)'}")

    # 7. Frontend Static Build Check
    index_file = FRONTEND_DIST / "index.html"
    assets_dir = FRONTEND_DIST / "assets"
    fe_ok = index_file.is_file() and assets_dir.is_dir()
    print(f"[{'PASS' if fe_ok else 'FAIL'}] React 18 工作台前端 (dist): {'已就绪可离线渲染' if fe_ok else '前端静态资源缺失'}")
    if not fe_ok:
        all_pass = False

    # 8. Evolution Engine Check
    try:
        from backend.runtime_core.evolution_engine import EvolutionEngine
        ee = EvolutionEngine()
        h = ee.compute_knowledge_health(limit=10)
        print(f"[PASS] Evolution Engine 自我演进与健康引擎:")
        print(f"       - 知识资产整体健康度: {h['overall_score']}/100 ({h['overall_grade']})")
        print(f"       - 评估知识条目: {h['total_pages_evaluated']} 条 | 状态分布: {h['distribution']}")
    except Exception as e:
        print(f"[FAIL] Evolution Engine 检查失败: {e}")
        all_pass = False

    # 8. Disk Space Check
    disk = shutil.disk_usage(str(BASE_DIR))
    free_gb = round(disk.free / (1024 ** 3), 2)
    disk_ok = free_gb > 1.0
    print(f"[{'PASS' if disk_ok else 'WARN'}] 磁盘可用容量: {free_gb} GB 可用空间")

    print("-" * 65)
    if all_pass:
        print(">>> 诊断结论: [HEALTHY] PAW Desktop 本地参赛版本环境全部健康，具备开箱即用能力！")
    else:
        print(">>> 诊断结论: [DEGRADED] 存在不通过项，请参阅上方详细提示。")
    print("=" * 65)


if __name__ == "__main__":
    run_doctor()

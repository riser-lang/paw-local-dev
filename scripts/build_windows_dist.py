"""PAW Personal Work OS v2.3 - Windows Standalone Packager & Inno Setup Compiler.

Builds a self-contained, zero-dependency Windows distribution directory
under dist/PAW_Personal_Work_OS containing:
- PAW_Personal_Work_OS.exe
- Pre-compiled React 18 Frontend
- 41-table SQLite workspace template
- Embedded Python runtime and all reader / provider dependencies
- dist/PAW Setup.exe via Inno Setup 6
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT_DIR / "paw_desktop.spec"
DIST_DIR = ROOT_DIR / "dist" / "PAW_Personal_Work_OS"
ISS_FILE = ROOT_DIR / "installer" / "paw_setup.iss"

print("=================================================================")
print("   PAW Personal Work OS v2.3 - Windows 独立分发包打包构建器   ")
print("=================================================================")
print(f"项目根目录: {ROOT_DIR}")
print(f"Spec 文件: {SPEC_FILE}")

# 1. Preflight checks
frontend_index = ROOT_DIR / "frontend" / "dist" / "index.html"
if not frontend_index.is_file():
    print(f"[ERROR] 前端构建产物未找到: {frontend_index}")
    sys.exit(1)

db_file = ROOT_DIR / "template" / "workspace_template.db"
if not db_file.is_file():
    print(f"[ERROR] 数据库干净模板未找到: {db_file}")
    sys.exit(1)

print("[PASS] 前端产物与 SQLite 数据库模板核验完毕")

# 2. Run PyInstaller
cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC_FILE)]
print(f"\n执行 PyInstaller 构建命令: {' '.join(cmd)}")
res = subprocess.run(cmd, cwd=str(ROOT_DIR))

if res.returncode != 0:
    print(f"\n[FAIL] PyInstaller 打包构建失败，退出码: {res.returncode}")
    sys.exit(res.returncode)

# 3. Post-build setup
print("\n[INFO] 配置分发目录辅助启动脚本...")
bat_content = """@echo off
title PAW Personal Work OS v2.3.9 (本地独立版)
cd /d "%~dp0"
echo =================================================================
echo       正在启动 PAW Personal Work OS v2.3.9 (参赛版本)
echo       请稍候，系统将自动在默认浏览器中打开个人工作台...
echo =================================================================
start "" "PAW_Personal_Work_OS.exe"
"""
with open(DIST_DIR / "启动PAW.bat", "w", encoding="gbk") as f:
    f.write(bat_content)

readme_content = """PAW Personal Work OS v2.3.9 (Windows 参赛版本)
=================================================================

面向普通员工的个人 AI 工作系统（严肃资产化平台）。

【快速开始】
1. 双击运行 "启动PAW.bat" 或 "PAW_Personal_Work_OS.exe"；
2. 浏览器将自动打开 http://127.0.0.1:8800/app；
3. 按照新手引导选择您的工作文件夹（或使用默认 sources 目录）；
4. 正常在此文件夹中保存 Word/Excel/PPT/PDF 文件；
5. PAW 会在后台自动完成高保真提取、证据绑定、冲突检测与知识提炼；
6. 在“审核中心”一键核准后沉淀为持久业务资产。

【技术特性】
- 零依赖：内置全套 Python 运行时与 React 前端，无需配置 Python/Node/Git/CLI；
- 严肃治理：formal_state_governance_status = ENFORCED，AI 生成业务事实必须经人类审批；
- 离线韧性：支持纯离线规则推理与云端大模型 (DeepSeek/通义千问/OpenAI) 无缝切换；
- 证据网关：AI 助手 (Web Agy V2) 强制校验证据引用，绝无模型幻觉外泄；
- 演进引擎：内置 Knowledge Health 评估、Context Graph 上下文图谱与项目长期记忆；
- 灾难恢复：SQLite 在线原子备份，一键快照无损恢复。
"""
with open(DIST_DIR / "使用说明.txt", "w", encoding="utf-8") as f:
    f.write(readme_content)

print(f"\n[SUCCESS] Windows 独立安装分发包已成功构建输出至:\n -> {DIST_DIR}")

# 4. Optional Inno Setup compilation
inno_compiler = Path(r"C:\Users\sunao\AppData\Local\Programs\Inno Setup 6\ISCC.exe")
if not inno_compiler.is_file():
    # Try Program Files
    inno_compiler = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")

def sign_binary(filepath: Path):
    if not filepath.is_file():
        raise FileNotFoundError(f"Binary file to sign does not exist: {filepath}")
    import sign_with_sunao13
    ret = sign_with_sunao13.run_sign(filepath)
    if ret != 0:
        raise RuntimeError(f"Failed to sign {filepath.name}, exit code: {ret}")
    print(f"[SIGN] 已成功注入 sunao13 防伪数字签名: {filepath.name}")

# Sign PAW_Personal_Work_OS.exe before Inno Setup packaging
main_exe = DIST_DIR / "PAW_Personal_Work_OS.exe"
sign_binary(main_exe)

# Copy frontend into dist root if not present
dist_frontend = DIST_DIR / "frontend"
if not dist_frontend.exists():
    src_frontend = ROOT_DIR / "frontend" / "dist"
    if src_frontend.exists():
        shutil.copytree(src_frontend, dist_frontend)
        print(f"[COPY] 已同步前端产物至分发根目录: {dist_frontend}")

if inno_compiler.is_file() and ISS_FILE.is_file():
    print("\n=================================================================")
    print("   编译 Inno Setup 安装包 (PAW_Personal_Work_OS_Setup.exe)   ")
    print("=================================================================")
    inno_cmd = [str(inno_compiler), str(ISS_FILE)]
    print(f"执行命令: {' '.join(inno_cmd)}")
    inno_res = subprocess.run(inno_cmd, cwd=str(ROOT_DIR))
    if inno_res.returncode == 0:
        installer_exe = ROOT_DIR / "dist" / "PAW_Personal_Work_OS_Setup.exe"
        if installer_exe.is_file():
            sign_binary(installer_exe)
            size_mb = installer_exe.stat().st_size / (1024 * 1024)
            print(f"\n[SUCCESS] 安装程序构建成功并已签名:\n -> {installer_exe} ({size_mb:.2f} MB)")
            desktop_target = Path(r"C:\Users\sunao\Desktop\PAW_Personal_Work_OS_Setup_v2.3.9.exe")
            shutil.copy2(installer_exe, desktop_target)
            print(f"[COPY] 已复制最终安装包至桌面:\n -> {desktop_target}")
        else:
            print("\n[SUCCESS] Inno Setup 编译完成")
    else:
        print(f"\n[WARN] Inno Setup 编译退出码: {inno_res.returncode}")
else:
    print(f"\n[INFO] 未检测到 Inno Setup 编译器 ({inno_compiler})，跳过安装包封装。")



"""PAW Personal Work OS v2.3.9 - Packager and Inno Setup Builder.

1. Preflight checks
2. Runs PyInstaller with paw_desktop.spec
3. Deploys launcher batch and readme
4. Compiles installer with Inno Setup (ISCC)
5. Verifies installer size >= 20MB and prints SHA256
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Force UTF-8 for console output on Windows runners (cp1252)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT_DIR / "paw_desktop.spec"
DIST_DIR = ROOT_DIR / "dist" / "PAW_Personal_Work_OS"
ISS_FILE = ROOT_DIR / "installer" / "paw_setup.iss"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def main():
    print("=" * 70)
    print("PAW Personal Work OS v2.3.9 - 打包构建流程")
    print(f"项目根目录: {ROOT_DIR}")
    print("=" * 70)

    # 1. Preflight checks
    frontend_index = ROOT_DIR / "frontend" / "dist" / "index.html"
    if not frontend_index.is_file():
        print(f"[ERROR] 前端构建产物未找到: {frontend_index}")
        sys.exit(1)

    db_file = ROOT_DIR / "template" / "workspace_template.db"
    if not db_file.is_file():
        print(f"[ERROR] 数据库干净模板未找到: {db_file}")
        sys.exit(1)

    print("[PASS] 基础文件与 SQLite 模板核验完毕")

    # 2. Run PyInstaller
    cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC_FILE)]
    print(f"\n[INFO] 执行 PyInstaller 构建: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if res.returncode != 0:
        print(f"[FAIL] PyInstaller 构建失败，退出码: {res.returncode}")
        sys.exit(res.returncode)

    # 3. Post-build auxiliary files
    print("\n[INFO] 配置分发目录辅助启动脚本与说明...")
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
"""
    with open(DIST_DIR / "使用说明.txt", "w", encoding="utf-8") as f:
        f.write(readme_content)

    dist_frontend = DIST_DIR / "frontend"
    if not dist_frontend.exists():
        src_frontend = ROOT_DIR / "frontend" / "dist"
        if src_frontend.exists():
            shutil.copytree(src_frontend, dist_frontend)

    # 4. Inno Setup Compiler
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if not iscc:
        candidates = [
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
            Path(r"C:\ProgramData\chocolatey\bin\iscc.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        ]
        for c in candidates:
            if c.is_file():
                iscc = str(c)
                break

    if not iscc:
        print("[FAIL] 未找到 Inno Setup 编译器 (ISCC.exe)！")
        sys.exit(1)

    print(f"\n[INFO] 使用 Inno Setup 编译器: {iscc}")
    inno_cmd = [iscc, str(ISS_FILE)]
    res_inno = subprocess.run(inno_cmd, cwd=str(ROOT_DIR))
    if res_inno.returncode != 0:
        print(f"[FAIL] Inno Setup 编译失败，退出码: {res_inno.returncode}")
        sys.exit(res_inno.returncode)

    # 5. Output Verification
    dist_dir = ROOT_DIR / "dist"
    possible_exes = [
        dist_dir / "PAW_Personal_Work_OS_Setup_v2.3.9.exe",
        dist_dir / "PAW_Personal_Work_OS_Setup.exe",
    ]
    target_exe = None
    for pe in possible_exes:
        if pe.is_file():
            target_exe = pe
            break

    if not target_exe:
        print(f"[FAIL] Inno Setup 编译后未找到安装包 EXE 文件！")
        sys.exit(1)

    # Standardize to PAW_Personal_Work_OS_Setup_v2.3.9.exe if needed
    final_exe = dist_dir / "PAW_Personal_Work_OS_Setup_v2.3.9.exe"
    if target_exe != final_exe:
        shutil.copy2(target_exe, final_exe)

    size_bytes = final_exe.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    file_sha256 = sha256_file(final_exe)

    print("\n" + "=" * 70)
    print("安装包构建成功！产物指标报告：")
    print(f"文件路径:  {final_exe}")
    print(f"文件大小:  {size_bytes} 字节 ({size_mb:.2f} MB)")
    print(f"SHA-256:  {file_sha256}")
    print("=" * 70)

    if size_mb < 20.0:
        print(f"[FAIL] 安装包大小 {size_mb:.2f} MB 低于 20 MB 门槛限制！")
        sys.exit(1)

    print(f"[PASS] 安装包大小 {size_mb:.2f} MB 满足 >= 20 MB 质量门禁要求。")


if __name__ == "__main__":
    main()

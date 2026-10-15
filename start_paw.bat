@echo off
chcp 65001 >nul
title PAW Desktop Local Edition v1.0
cd /d "%~dp0"

echo ===================================================================
echo             PAW Desktop Local Edition v1.0 (参赛版本)
echo         Evidence-First + Human-Governed Personal AI Workspace
echo ===================================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] 未检测到 Python 环境，请安装 Python 3.10+。
    pause
    exit /b 1
)

echo [1/2] 正在执行系统状态诊断 (Doctor)...
python scripts\run_doctor.py
if %errorlevel% neq 0 (
    echo [WARN] 诊断检查发现警告，正在尝试继续启动服务...
)

echo.
echo [2/2] 正在启动 PAW 本地网关并自动打开工作台页面...
python run.py

pause

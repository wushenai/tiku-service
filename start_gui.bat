@echo off
chcp 65001 >nul
title Tiku Service - GUI
cd /d "%~dp0"

REM Start the graphical panel without a console window.
REM Uses pythonw.exe so no black box appears.

if not exist "tiku_gui.py" (
    echo [ERROR] tiku_gui.py not found in:
    echo         %CD%
    pause
    exit /b 1
)

where pythonw.exe >nul 2>&1
if errorlevel 1 (
    echo [WARN] pythonw.exe not found on PATH, falling back to python.exe
    echo        A console window will stay open.
    python tiku_gui.py
    if errorlevel 1 pause
    exit /b
)

start "" pythonw.exe "tiku_gui.py"
exit /b 0

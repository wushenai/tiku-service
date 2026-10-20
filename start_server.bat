@echo off
chcp 65001 >nul
title Tiku Service
cd /d "%~dp0"

echo ==========================================
echo   Tiku Service (local question bank)
echo ==========================================
echo.

if not exist "tiku_server.py" (
    echo [ERROR] tiku_server.py not found in:
    echo         %CD%
    pause
    exit /b 1
)

python tiku_server.py
set CODE=%ERRORLEVEL%

echo.
if %CODE% neq 0 (
    echo [ERROR] Service exited with code %CODE%
    echo         Check the messages above.
    pause
) else (
    echo Service stopped.
)

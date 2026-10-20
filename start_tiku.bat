@echo off
chcp 65001 >nul
title Tiku Service
cd /d "%~dp0"

REM Keep this file ASCII-only.
REM cmd.exe reads .bat using the OEM codepage, so UTF-8 Chinese becomes "?".
REM Use start_gui.bat for the panel, start_server.bat for headless service.

python tiku_server.py
if errorlevel 1 pause

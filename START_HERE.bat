@echo off
rem Double-click me. Installs everything (first time only) and opens the app in your browser.
title Library Claim Agent
set PYTHONUTF8=1
call "%~dp0scripts\windows_setup.bat"
if errorlevel 1 exit /b 1
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.launch

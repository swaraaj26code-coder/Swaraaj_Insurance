@echo off
rem Runs the offline test suite (no API key needed).
title Library Claim Agent tests
set PYTHONUTF8=1
call "%~dp0scripts\windows_setup.bat"
if errorlevel 1 exit /b 1
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pytest -q
pause

@echo off
rem Double-click me to use the app from your phone (same Wi-Fi). Shows an address and a QR code.
title Library Claim Agent (phone mode)
set PYTHONUTF8=1
call "%~dp0scripts\windows_setup.bat"
if errorlevel 1 exit /b 1
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.launch --phone

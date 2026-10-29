@echo off
rem Double-click me to pack the project into library-claim-agent.zip (next to this folder),
rem ready to copy to another computer. Your API key and past sweeps are NOT included.
title Library Claim Agent - make ZIP
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\make_zip.py
) else (
    py -3 scripts\make_zip.py || python scripts\make_zip.py
)
echo.
pause

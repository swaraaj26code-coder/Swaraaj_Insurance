@echo off
rem Installs everything the app needs. Called by START_HERE.bat and START_PHONE.bat.
rem Safe to run again: it skips whatever is already done.
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1

rem Started from inside the ZIP? Windows runs it from a temporary copy, which cannot work.
echo "%~dp0" | findstr /i /c:".zip\\" /c:"\Temp1_" >nul && goto :inside_zip

echo.
echo [1/3] Looking for Python...
set PY=
call :find_python
if defined PY goto :have_python

echo       Python 3.12 is not installed. Installing it now (this needs internet)...
where winget >nul 2>nul
if errorlevel 1 goto :no_winget
winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
call :find_python
if defined PY goto :have_python
goto :no_python

:have_python
echo       Using %PY%

echo [2/3] Preparing a private Python environment for this app...
if exist ".venv\Scripts\python.exe" goto :have_venv
%PY% -m venv .venv
if errorlevel 1 goto :venv_failed
:have_venv

echo [3/3] Installing the app's components (the first time takes 2-5 minutes)...
if not exist ".venv\requirements.installed" goto :install
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if not errorlevel 1 (
    echo       Already installed.
    goto :installed
)
:install
echo       Please wait. Nothing appears on screen while it downloads (about 150 MB).
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 (
    rem Network blips happen (seen in testing): wait 10 s and try once more before giving up.
    echo       The download was interrupted. Trying once more in 10 seconds...
    ping -n 11 127.0.0.1 >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
)
if errorlevel 1 goto :pip_failed
copy /y requirements.txt ".venv\requirements.installed" >nul
:installed

if not exist ".env" copy ".env.example" ".env" >nul
echo       Setup complete.
echo.
exit /b 0


:find_python
py -3.12 -c "import sys" >nul 2>nul && (set PY=py -3.12& goto :eof)
py -3.13 -c "import sys" >nul 2>nul && (set PY=py -3.13& goto :eof)
py -3.11 -c "import sys" >nul 2>nul && (set PY=py -3.11& goto :eof)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (set PY="%LocalAppData%\Programs\Python\Python312\python.exe"& goto :eof)
python -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul && (set PY=python& goto :eof)
goto :eof

:inside_zip
echo.
echo   You started the app from inside the ZIP file. Windows cannot run it from there.
echo   1. Close this window
echo   2. Right-click library-claim-agent.zip and choose "Extract All...", then "Extract"
echo   3. Open the extracted folder and double-click START_HERE.bat
goto :fail

:no_winget
echo.
echo   Could not install Python automatically on this computer.
echo   1. Open https://www.python.org/downloads/release/python-3129/
echo   2. Download "Windows installer (64-bit)" and run it
echo   3. IMPORTANT: tick "Add python.exe to PATH" on the first screen, then Install Now
echo   4. Double-click this start script again
goto :fail

:no_python
echo.
echo   Python was installed but this window cannot see it yet.
echo   Close this window and double-click the start script again.
goto :fail

:venv_failed
echo.
echo   Could not create the Python environment. Make sure the folder is not read-only
echo   (for example, do not run it from inside the ZIP file: extract the ZIP first).
goto :fail

:pip_failed
echo.
echo   Installing components failed. Check your internet connection and try again.
echo   If you are on an office network, try a home network or phone hotspot.
goto :fail

:fail
echo.
pause
exit /b 1

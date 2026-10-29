@echo off
rem Double-click me on YOUR computer to put this project in a private GitHub repository.
rem Your API key (.env), installed components and recorded sweeps are never uploaded.
title Publish to GitHub
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 goto :no_git

echo.
echo   1. On https://github.com click "New repository"
echo   2. Give it a name, choose "Private", and do NOT tick "Add a README"
echo   3. Click "Create repository" and copy its address (https://github.com/you/name.git)
echo.
set REPO=
set /p REPO=Paste the repository address here and press Enter:
if "%REPO%"=="" goto :no_repo

if not exist ".git" git init -q

git config user.name >nul 2>nul
if errorlevel 1 goto :ask_name
:have_name
git config user.email >nul 2>nul
if errorlevel 1 goto :ask_email
:have_email

git add -A
rem Safety check: never upload secrets or private data.
rem (git's own path matching; findstr's "$" does not match git's LF line endings)
git diff --cached --name-only -- .env "*.pem" data "*models.json" | findstr "." >nul
if not errorlevel 1 goto :secret

git diff --cached --quiet
if not errorlevel 1 goto :committed
git commit -q -m "Library contents claim agent"
if errorlevel 1 goto :commit_failed
:committed
git branch -M main
git remote remove origin >nul 2>nul
git remote add origin %REPO%
echo.
echo   Uploading... (a browser window may ask you to sign in to GitHub)
git push -u origin main
if errorlevel 1 goto :push_failed
echo.
echo   Done. Open %REPO% in your browser. To share it with the reviewers:
echo   Settings, then Collaborators, then "Add people".
goto :end

:ask_name
set GNAME=
set /p GNAME=Your name (shown in the project history):
git config user.name "%GNAME%"
goto :have_name

:ask_email
set GMAIL=
set /p GMAIL=Your email (the one on your GitHub account):
git config user.email "%GMAIL%"
goto :have_email

:no_git
echo.
echo   Git is not installed. Install it from https://git-scm.com/download/win
echo   (accept the default options), then double-click me again.
goto :end

:no_repo
echo   No address given. Nothing was uploaded.
goto :end

:secret
echo.
echo   STOPPED: a private file (.env, a certificate, models.json or data/) was about to be uploaded.
echo   Nothing was uploaded. Check .gitignore and try again.
git reset -q
goto :end

:commit_failed
echo.
echo   Saving the project history failed (see the message above). Nothing was uploaded.
goto :end

:push_failed
echo.
echo   Upload failed. Check the address and that you are signed in to GitHub, then try again.

:end
echo.
pause

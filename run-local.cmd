@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=.tools\python\cpython-3.12-windows-x86_64-none\python.exe"
if not exist "%PYTHON%" (
    echo Portable Python is missing.
    echo Ask Codex to set up the admin-free local test environment first.
    exit /b 1
)

set "PORT=8000"
if not "%~1"=="" set "PORT=%~1"
set "AUDIO_DIRECTORIES=%CD%\sounds"
set "HOST=127.0.0.1"
set "DISCORD_TOKEN="
set "WEB_API_TOKEN="
set "STATS_DATABASE=%CD%\data\stats.db"

if /I "%~2"=="skip-tests" goto start_server

echo Running tests...
"%PYTHON%" -m pytest -q
if errorlevel 1 (
    echo.
    echo Tests failed, so the local server was not started.
    echo To explore it anyway, run: run-local.cmd %PORT% skip-tests
    exit /b 1
)

:start_server
set "URL=http://127.0.0.1:%PORT%"
echo.
echo Discord and API-key checks are disabled for this local session.
echo Starting Soundboard at %URL%
echo Press Ctrl+C to stop it.
if /I not "%~3"=="no-browser" start "" "%URL%"
"%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%

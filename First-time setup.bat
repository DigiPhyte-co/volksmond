@echo off
title SA-Live-Transcribe, First-time setup
cd /d "%~dp0"

echo.
echo  SA-Live-Transcribe, First-time setup
echo  This script will:
echo    1. Check for Python 3.12
echo    2. Create a virtual env at %%LOCALAPPDATA%%\sa-live-transcribe\.venv
echo    3. Install all dependencies
echo    4. Pre-download the Whisper model(s) for your hardware
echo.
echo  Total download: ~150 MB of Python deps + ~1.5 GB (CPU-only) or ~4.5 GB (with GPU)
echo  This takes 5-15 minutes depending on internet speed.
echo.
pause

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0setup.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo  Setup finished successfully.
) else (
    echo  Setup exited with code %RC%.
)
echo.
pause

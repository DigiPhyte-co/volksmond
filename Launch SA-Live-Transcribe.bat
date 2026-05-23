@echo off
title SA-Live-Transcribe
cd /d "%~dp0"

set "VENV_PYTHON=%LOCALAPPDATA%\sa-live-transcribe\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo.
    echo  [error] Python venv not found at:
    echo    %VENV_PYTHON%
    echo.
    echo  This is a fresh machine. Run 'First-time setup.bat' first
    echo  (in this same folder^).
    echo.
    pause
    exit /b 1
)

echo.
echo  Starting SA-Live-Transcribe...
echo  Browser will open at http://127.0.0.1:8765 in a moment.
echo  Leave this window open while you're using it.
echo  Close it (or press Ctrl+C) to stop the server.
echo.

"%VENV_PYTHON%" -m live_transcribe.web --port 8765

echo.
echo  Server stopped. Press any key to close this window.
pause >nul

@echo off
rem Double-click this file to open the Trading Agent in your web browser.
cd /d "%~dp0"
title Trading Agent - keep this window open
if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first time, this takes a few minutes...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" -m trader.dashboard %*
goto :eof

:error
echo.
echo Setup failed. Make sure Python 3.10 or newer is installed from python.org.
pause

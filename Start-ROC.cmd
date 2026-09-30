@echo off
setlocal
title Record of Counsel
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First install ROC in a Python virtual environment as described in README.
  pause
  exit /b 2
)
if exist ".venv\browsers" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.venv\browsers"
start "" ".venv\Scripts\pythonw.exe" -m roc.desktop

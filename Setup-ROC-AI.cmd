@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Install ROC's Python environment first. See README.
  pause
  exit /b 2
)
start "" ".venv\Scripts\pythonw.exe" -m roc.owner_setup

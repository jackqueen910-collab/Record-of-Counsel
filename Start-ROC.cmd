@echo off
setlocal
title Record of Counsel - interface and PACER sign-in
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First install ROC in a Python virtual environment as described in README.
  pause
  exit /b 2
)
if exist ".venv\browsers" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.venv\browsers"
".venv\Scripts\python.exe" -m roc ui
if errorlevel 1 pause

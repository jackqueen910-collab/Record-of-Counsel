@echo off
setlocal
title Record of Counsel - standalone run
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First install ROC in a Python virtual environment as described in README.
  pause
  exit /b 2
)
if exist ".venv\browsers" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.venv\browsers"
set "ROC_CONFIG=%~1"
if not defined ROC_CONFIG set "ROC_CONFIG=local-live.json"
if not exist "%ROC_CONFIG%" (
  echo Configuration missing. Copy examples\live.json to local-live.json and edit the lawyer, budget and filters.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m roc run "%ROC_CONFIG%" --live --keep-session
set "ROC_RESULT=%ERRORLEVEL%"
echo.
if "%ROC_RESULT%"=="0" (echo ROC finished. Results are in your configured run folder.) else (echo ROC stopped. The message above and saved status explain why. No automatic retry.)
pause
exit /b %ROC_RESULT%

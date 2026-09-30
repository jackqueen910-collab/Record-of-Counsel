@echo off
setlocal
title Record of Counsel - court validation
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First install ROC in a Python virtual environment as described in README.
  pause
  exit /b 2
)
if exist ".venv\browsers" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.venv\browsers"
set "ROC_VALIDATION_CONFIG=%~1"
if not defined ROC_VALIDATION_CONFIG set "ROC_VALIDATION_CONFIG=local-court-validation.json"
if not exist "%ROC_VALIDATION_CONFIG%" (
  echo Validation configuration missing. See examples\court-batch.json and README.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m roc validate-courts "%ROC_VALIDATION_CONFIG%" --live --keep-session
set "ROC_VALIDATION_RESULT=%ERRORLEVEL%"
echo.
echo Results and receipts are saved in the configured run folder.
pause
exit /b %ROC_VALIDATION_RESULT%

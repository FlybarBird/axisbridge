@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run.py %*
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    python run.py %*
  ) else (
    py -3 run.py %*
  )
)
if errorlevel 1 (
  echo.
  echo AxisBridge could not start. Install Python 3.10 or newer, then try again.
  pause
)

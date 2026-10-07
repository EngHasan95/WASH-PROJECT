@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo The portable Python runtime is missing. Extract the complete release ZIP first.
  pause
  exit /b 1
)
"runtime\python\python.exe" -I "portable_windows\launcher.py" start
if errorlevel 1 pause

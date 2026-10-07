@echo off
setlocal
cd /d "%~dp0"
"runtime\python\python.exe" -I "portable_windows\launcher.py" stop
if errorlevel 1 pause

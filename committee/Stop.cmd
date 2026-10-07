@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run.ps1" -Action Stop
if errorlevel 1 pause

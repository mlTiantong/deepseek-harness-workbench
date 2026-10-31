@echo off
cd /d "%~dp0dsh-companion"
".venv\Scripts\python.exe" -m dsh_companion.cli pet
if errorlevel 1 pause

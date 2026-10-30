@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
start "" "%~dp0creator\.venv\Scripts\pythonw.exe" "%~dp0creator\console_launch.py"

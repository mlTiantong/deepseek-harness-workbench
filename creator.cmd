@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%~dp0creator\.venv\Scripts\python.exe" "%~dp0creator\run.py" %*

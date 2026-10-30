@echo off
setlocal
"%~dp0.venv\Scripts\python.exe" "%~dp0launcher.py" --cli %*

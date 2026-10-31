@echo off
chcp 65001 >nul
powershell.exe -NoProfile -File "%~dp0启动大肥鱼.ps1"
if errorlevel 1 pause

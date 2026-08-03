@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" %*
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%
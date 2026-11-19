@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Lingshu-Companion
echo ============================================
echo.
python companion\start_all.py
echo.
pause

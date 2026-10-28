@echo off
chcp 65001 >nul
title AI虚拟主播 - WebUI配置后台
cd /d "%~dp0"
echo ========================================================
echo   正在启动 AI Vtuber WebUI 控制后台...
echo   启动后请在浏览器访问: http://127.0.0.1:8081
echo ========================================================
.venv\Scripts\python.exe webui.py
pause

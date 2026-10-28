@echo off
chcp 65001 >nul
title AI虚拟主播 - 实时直播主控
cd /d "%~dp0"
echo ========================================================
echo   正在准备 AI 虚拟主播运行环境...
echo ========================================================

REM 检查并启动抖音弹幕监听服务 (自动捕获抖音直播伴侣或网页弹幕)
tasklist /FI "IMAGENAME eq WssBarrageServer.exe" 2>NUL | find /I /N "WssBarrageServer.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [OK] 抖音弹幕监听服务已经在运行中 (端口 8888)
) else (
    echo [INFO] 正在启动 抖音弹幕监听服务 (捕获直播伴侣弹幕)...
    start "" "%~dp0tools\DouyinBarrageGrab\WssBarrageServer.exe"
    timeout /t 2 /nobreak >nul
)

echo ========================================================
echo   正在启动 AI 虚拟主播主程序 (Luna AI)...
echo   - Live2D 画面服务: http://127.0.0.1:12345 (OBS 浏览器源)
echo   - 弹幕连接端口: 127.0.0.1:8888 (已直连直播伴侣)
echo   - 网易云在线点歌: 已就绪 (关键词: 点歌 歌名)
echo ========================================================
.venv\Scripts\python.exe main.py
pause

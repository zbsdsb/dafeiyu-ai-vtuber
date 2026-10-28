@echo off
chcp 65001 >nul
title 大肥鱼 - 字幕与口型服务
cd /d "%~dp0"
echo ========================================================
echo   启动台词字幕服务（127.0.0.1:5500）和口型服务
echo   已经在运行的话会自动跳过，不会重复启动
echo ========================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start-caption-printer.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start-songroom-lipsync.ps1"
echo.
echo 两个服务都在后台运行，可以关掉这个窗口。
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === batch-ndvi：把改动推到 GitHub ===
git add -A
git -c user.name="oliveriolikescoding" -c user.email="oliveriolikescoding@users.noreply.github.com" commit -m "update: %date% %time%"
git -c http.proxy=http://127.0.0.1:7897 -c credential.helper=manager push origin main
if errorlevel 1 (
  echo.
  echo [失败] 代理没开或者网络不通。先确认 Clash/代理 7897 端口在跑，再双击一次。
  pause
  exit /b 1
)
echo.
echo [完成] 已推送。刷新 https://github.com/oliveriolikescoding/batch-ndvi 查看。
pause
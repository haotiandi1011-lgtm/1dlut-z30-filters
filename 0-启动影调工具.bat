@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\Z30影调工具\Z30影调工具.exe" (
  start "" "dist\Z30影调工具\Z30影调工具.exe"
) else (
  echo 未找到打包版，改用 Python 启动…
  call "启动.cmd"
)

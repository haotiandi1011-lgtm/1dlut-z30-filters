@echo off
cd /d "%~dp0"
if exist "dist\Z30影调工具\Z30影调工具.exe" (
  start "" "dist\Z30影调工具\Z30影调工具.exe"
) else (
  echo Packaged version not found, falling back to Python...
  call "启动.cmd"
)

@echo off
cd /d "%~dp0"
title Z30 Tone Tool

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo  [ERROR] Python not found in PATH.
  echo  Install Python 3.10+ from https://www.python.org/downloads/
  echo  Remember to tick "Add python.exe to PATH" during install.
  echo.
  pause
  exit /b 1
)

python -c "import numpy, PIL, usb, libusb_package" >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Installing dependencies ^(first run only, may take a minute^)...
  python -m pip install --quiet --upgrade numpy pillow pyusb libusb-package
)

echo.
echo  Starting local server, the browser will open automatically.
echo  Keep this window open. Close it to stop the tool.
echo.

python webui\server.py
echo.
echo  Server stopped.
pause

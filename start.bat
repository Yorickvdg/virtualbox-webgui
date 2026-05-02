@echo off
title VirtualBox Monitor
cd /d "%~dp0"

echo.
echo  VirtualBox Monitor
echo  ==================
echo.

set PYTHON=C:\Users\Skylake\AppData\Local\Programs\Python\Python311\python.exe

if not exist "%PYTHON%" (
    echo  [ERROR] Python 3.11 not found at %PYTHON%
    echo.
    echo  Try running: python app.py
    pause
    exit /b 1
)

echo  Installing/updating dependencies...
"%PYTHON%" -m pip install -q -r requirements.txt

echo.
echo  Dashboard starten op http://localhost:5000
echo  Druk Ctrl+C om te stoppen.
echo.

"%PYTHON%" app.py

pause

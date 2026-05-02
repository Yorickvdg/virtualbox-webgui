@echo off
setlocal enabledelayedexpansion
title Install Python Requirements - VirtualBox Monitor

echo.
echo ============================================================
echo   VirtualBox Monitor - Python Requirements Installer
echo ============================================================
echo.

cd /d "%~dp0" || exit /b 1

set PYTHON=C:\Users\Skylake\AppData\Local\Programs\Python\Python311\python.exe

if not exist "%PYTHON%" (
    echo [ERROR] Python 3.11 not found at %PYTHON%
    echo.
    echo Download Python from https://python.org
    echo.
    pause
    exit /b 1
)

echo [1/4] Checking Python version...
"%PYTHON%" --version
echo.

echo [2/4] Upgrading pip...
"%PYTHON%" -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo WARNING: pip upgrade had issues, continuing...
) else (
    echo OK: pip upgraded
)
echo.

echo [3/4] Installing dependencies from requirements.txt...
echo.
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found in %cd%
    echo.
    pause
    exit /b 1
)

"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed. Check your internet connection.
    echo.
    pause
    exit /b 1
)
echo.

echo [4/4] Verifying installation...
"%PYTHON%" -c "import flask; print('OK: Flask ' + flask.__version__)" 2>nul || echo WARNING: Flask verification failed
"%PYTHON%" -c "import flask_cors; print('OK: Flask-CORS installed')" 2>nul || echo WARNING: Flask-CORS verification failed
echo.

echo ============================================================
echo   INSTALLATION COMPLETE
echo ============================================================
echo.
echo You can now run: start.bat
echo Dashboard will be at: http://localhost:5000
echo.

pause

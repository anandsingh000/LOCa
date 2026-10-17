@echo off
setlocal

cd /d "%~dp0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "LOCA_PORT=8090"
set "LOCA_URL=http://127.0.0.1:%LOCA_PORT%/"

if not exist "%VENV_PYTHON%" (
    echo Creating the LOCa virtual environment...
    py -3.11 -m venv "%~dp0.venv"
    if errorlevel 1 (
        echo Could not create the virtual environment. Install Python 3.11 and try again.
        pause
        exit /b 1
    )
)

"%VENV_PYTHON%" -c "import flask, pandas, matplotlib, requests, bs4" >nul 2>&1
if errorlevel 1 (
    echo Installing LOCa runtime dependencies...
    "%VENV_PYTHON%" -m pip install -r "%~dp0requirements-runtime.txt"
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
)

echo LOCa is starting at %LOCA_URL%
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process '%LOCA_URL%'"
"%VENV_PYTHON%" "%~dp0main.py"

endlocal

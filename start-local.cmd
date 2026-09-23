@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>nul
if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
if errorlevel 1 goto failed
:dependencies
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:run
echo Open http://localhost:8000 after the server starts. Close this window to stop.
".venv\Scripts\python.exe" start.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Python 3.11 or 3.12 is required. See README.md.
pause
exit /b 1

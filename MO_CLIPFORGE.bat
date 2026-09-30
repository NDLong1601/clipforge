@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto use_venv
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python

echo Python 3.12 or newer is required. Install Python, then double-click this file again.
pause
exit /b 1

:use_venv
".venv\Scripts\python.exe" launch.py %*
goto finished

:use_py
py -3 launch.py %*
goto finished

:use_python
python launch.py %*

:finished
if errorlevel 1 pause

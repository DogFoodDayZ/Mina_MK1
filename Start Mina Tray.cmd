@echo off
setlocal
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%mina_tray_launcher.py"
set "VENV_PYW=%ROOT%.venv\Scripts\pythonw.exe"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%SCRIPT%" (
  echo mina_tray_launcher.py not found in %ROOT%
  pause
  exit /b 1
)

if exist "%VENV_PYW%" (
  start "" "%VENV_PYW%" "%SCRIPT%"
  exit /b 0
)

if exist "%VENV_PY%" (
  start "" "%VENV_PY%" "%SCRIPT%"
  exit /b 0
)

where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw "%SCRIPT%"
  exit /b 0
)

where python >nul 2>&1
if %errorlevel%==0 (
  start "" python "%SCRIPT%"
  exit /b 0
)

echo Python not found. Run setup_mk1_env.ps1 first.
pause
exit /b 1

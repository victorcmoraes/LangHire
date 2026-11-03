@echo off
setlocal
cd /d "%~dp0"
set "LANGHIRE_DATA_DIR=%~dp0.local"
set "ANONYMIZED_TELEMETRY=false"
set "PYTHONUTF8=1"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
if /i "%~1"=="build" goto build
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-backend.ps1
if errorlevel 1 exit /b %ERRORLEVEL%
call npm.cmd run tauri -- dev
exit /b %ERRORLEVEL%
:build
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1
exit /b %ERRORLEVEL%

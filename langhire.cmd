@echo off
setlocal
cd /d "%~dp0"
set "LANGHIRE_DATA_DIR=%~dp0.local"
set "ANONYMIZED_TELEMETRY=false"
set "PYTHONUTF8=1"
uv run --frozen python cli/local.py %*
exit /b %ERRORLEVEL%

@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" ncm_web.py %*
) else (
    python ncm_web.py %*
)
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
if not exist .venv ( %PY% -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python launcher.py %*

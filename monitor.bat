@echo off
chcp 65001 >nul
set PYTHONPATH=%~dp0..
set TQDM_DISABLE=1
.venv\Scripts\python.exe git_pull.py 2>> logs/runtime_error.txt
.venv\Scripts\python.exe -m pip install -r requirements.txt -q 2>> logs/runtime_error.txt
call remote_cmd.bat
.venv\Scripts\python.exe main_preflight.py 2>> logs/runtime_error.txt
if %errorlevel% neq 0 (
    echo [FAIL] 上线前预检未通过，终止监控
    .venv\Scripts\python.exe send_error_log.py
    exit /b 1
)
.venv\Scripts\python.exe main_monitor.py 2>> logs/runtime_error.txt
.venv\Scripts\python.exe send_error_log.py

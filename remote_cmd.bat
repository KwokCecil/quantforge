@echo off
set TQDM_DISABLE=1
.venv\Scripts\python.exe remote_cmd.py 2>> logs/runtime_error.txt

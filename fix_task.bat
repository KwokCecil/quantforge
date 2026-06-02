@echo off
sc query Schedule | find "RUNNING" >nul
if %errorlevel% neq 0 (
    net start Schedule
    if %errorlevel% neq 0 (
        sc config Schedule start= auto
        net start Schedule
    )
)

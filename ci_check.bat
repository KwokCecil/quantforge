@echo off

echo ============================================
echo  Local CI Check
echo ============================================
echo.

set FAILED=0

REM Step 1: 同步依赖
echo [1/3] 同步 Python 依赖...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [FAIL] pip install 失败
    set FAILED=1
    goto :summary
)
echo [OK] 依赖同步完成
echo.

REM Step 2: 运行测试
echo [2/3] 运行测试套件...
python tests/run_all_tests.py
if %errorlevel% neq 0 (
    echo.
    echo [FAIL] 测试未全部通过
    set FAILED=1
) else (
    echo.
    echo [OK] 测试全部通过
)
echo.

REM Step 3: mypy 类型检查
echo [3/3] mypy 类型检查...
python -m mypy quantforge --ignore-missing-imports
if %errorlevel% neq 0 (
    echo [FAIL] mypy 检查发现问题
    set FAILED=1
) else (
    echo [OK] mypy 检查通过
)
echo.

:summary
echo ============================================
if "%FAILED%"=="1" (
    echo  结果: 未通过，请修复后重试
) else (
    echo  结果: 全部通过
)
echo ============================================
exit /b %FAILED%
@echo off

echo ============================================
echo  Git Merge and Push: dev to main (Squash)
echo ============================================
echo.

REM temp file under .git (avoids Chinese path issues)
set "TMPFILE=%~dp0.git\merge_push_tmp.txt"

REM Step 0: branch check
git branch --show-current > "%TMPFILE%" 2>&1
set /p BRANCH=<"%TMPFILE%"

if not "%BRANCH%"=="dev" (
    echo [ERROR] Current branch is "%BRANCH%", must be on dev
    exit /b 1
)

REM Step 0: clean working tree check
git status --porcelain > "%TMPFILE%" 2>&1
for %%A in ("%TMPFILE%") do set SIZE=%%~zA
if not "%SIZE%"=="0" (
    echo [WARN] Working tree not clean, commit or stash first
    exit /b 1
)

echo [OK] dev branch clean
echo.

REM Step 1: push dev
echo [1/4] Pushing dev branch...
git push origin dev
if %errorlevel% neq 0 (
    echo [ERROR] dev push failed
    exit /b 1
)
echo [OK] dev pushed
echo.

REM Step 2: merge to main (auto, no confirm)
echo.
echo [2/4] Checking out main...
git checkout main
if %errorlevel% neq 0 (
    echo [ERROR] Checkout main failed
    exit /b 1
)

git status --porcelain > "%TMPFILE%" 2>&1
for %%A in ("%TMPFILE%") do set SIZE_MAIN=%%~zA
if not "%SIZE_MAIN%"=="0" (
    echo [WARN] main working tree not clean, attempting auto restore...
    git checkout -- .
    git clean -fd
    git status --porcelain > "%TMPFILE%" 2>&1
    for %%A in ("%TMPFILE%") do set SIZE_MAIN=%%~zA
    if not "%SIZE_MAIN%"=="0" (
        echo [ERROR] Auto restore failed, main still has uncommitted changes
        git checkout dev
        exit /b 1
    )
    echo [OK] Auto restore succeeded
)

echo [3/4] Squash merging dev into main...
git merge --squash dev -X theirs
if %errorlevel% neq 0 (
    echo [ERROR] Squash merge failed, possible conflicts
    git checkout dev
    exit /b 1
)

git status --porcelain > "%TMPFILE%" 2>&1
for %%A in ("%TMPFILE%") do set SQUASH_SIZE=%%~zA
if "%SQUASH_SIZE%"=="0" goto :skip_commit
(
    echo feat: dev to main
    echo.
    git log --oneline origin/main..dev
) > "%TMPFILE%"
git commit -F "%TMPFILE%"
if %errorlevel% neq 0 (
    echo [ERROR] Commit failed
    git checkout dev
    exit /b 1
)
echo [OK] Squash merge and commit succeeded
:skip_commit
echo.

REM Step 4: push main and switch back to dev
echo [4/4] Pushing main branch...
git push origin main
if %errorlevel% neq 0 (
    echo [ERROR] main push failed
    git checkout dev
    exit /b 1
)
echo [OK] main pushed
echo.

git checkout dev
git merge main -X theirs --no-edit
if %errorlevel% neq 0 (
    echo [WARN] dev sync failed, but main merge succeeded
)
echo [OK] dev synced
echo.

del "%TMPFILE%" 2>nul
echo ============================================
echo Done, back on dev branch
echo ============================================

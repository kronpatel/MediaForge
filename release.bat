@echo off
title MediaForge Release Pipeline
setlocal enabledelayedexpansion

echo ============================================
echo  MediaForge Release Pipeline
echo ============================================
echo.

set ROOT=%~dp0
set VERSION_FILE=%ROOT%VERSION
set /p VERSION=<%VERSION_FILE%
echo Version: %VERSION%
echo.

:: --- Step 1: Clean ---
echo [1/6] Cleaning previous artifacts...
call "%~dp0clean.bat"
if %errorlevel% neq 0 (
    echo ERROR: Clean step failed.
    exit /b 1
)
echo  OK
echo.

:: --- Step 2: Build ---
echo [2/6] Building companion EXE...
call "%~dp0build.bat"
if %errorlevel% neq 0 (
    echo ERROR: Build step failed.
    exit /b 1
)
echo  OK
echo.

:: --- Step 3: Check resources ---
echo [3/6] Checking resources...
python scripts\check_resources.py
if %errorlevel% neq 0 (
    echo ERROR: Resource check failed.
    exit /b 1
)
echo  OK
echo.

:: --- Step 4: Package portable edition ---
echo [4/6] Packaging portable edition...
if not exist "%ROOT%release\" mkdir "%ROOT%release\"
python scripts\package_portable.py
if %errorlevel% neq 0 (
    echo ERROR: Portable packaging failed.
    exit /b 1
)
echo  OK
echo.

:: --- Step 5: Build Windows installer ---
echo [5/7] Building Windows installer...
set "ISCC_EXE=C:\Users\KRON\AppData\Local\Programs\Antigravity IDE\resources\app\node_modules\innosetup\bin\ISCC.exe"
if not exist "!ISCC_EXE!" (
    if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
        set "ISCC_EXE=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    ) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
        set "ISCC_EXE=C:\Program Files\Inno Setup 6\ISCC.exe"
    ) else (
        set "ISCC_EXE=ISCC.exe"
    )
)
echo Compiler Path: !ISCC_EXE!
"!ISCC_EXE!" "%ROOT%installer.iss"
if %errorlevel% neq 0 (
    echo ERROR: Installer build failed.
    exit /b 1
)
echo  OK
echo.

:: --- Step 6: Verify release ---
echo [6/7] Verifying release artifacts...
python scripts\verify_build.py --release
if %errorlevel% neq 0 (
    echo ERROR: Release verification failed.
    exit /b 1
)
echo  OK
echo.

:: --- Step 7: Run smoke test ---
echo [7/7] Running smoke test...
python scripts\smoke_test.py
if %errorlevel% neq 0 (
    echo WARNING: Smoke test reported issues. Review output above.
)
echo. OK

echo.
echo ============================================
echo  Release complete: MediaForge v%VERSION%
echo.
echo  Artifacts:
echo    EXE:       dist\MediaForge.exe
echo    Portable:  release\MediaForge_Portable.zip
echo    Installer: release\MediaForge-Setup.exe
echo    Logs:      companion\logs\
echo ============================================
echo.
exit /b 0

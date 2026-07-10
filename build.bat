@echo off
title MediaForge Build Pipeline
setlocal enabledelayedexpansion

echo ============================================
echo  MediaForge Build Pipeline
echo ============================================
echo.

:: --- Configuration ---
set ROOT=%~dp0
set VERSION_FILE=%ROOT%VERSION
set /p VERSION=<%VERSION_FILE%
echo Version: %VERSION%
echo.

:: --- Step 1: Check prerequisites ---
echo [1/6] Checking prerequisites...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH.
    exit /b 1
)

python -m PyInstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller not found. Install with: pip install pyinstaller
    exit /b 1
)

pip show customtkinter >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: customtkinter not found. Build may fail.
)

echo  OK
echo.

:: --- Step 2: Verify versions ---
echo [2/6] Verifying version consistency...
python scripts\verify_versions.py
if %errorlevel% neq 0 (
    echo ERROR: Version verification failed.
    exit /b 1
)
echo  OK
echo.

:: --- Step 3: Python compile check ---
echo [3/6] Running compile checks...
python -m py_compile backend\app.py 2>nul
if %errorlevel% neq 0 (
    echo ERROR: backend\app.py failed compile check.
    exit /b 1
)
python -m py_compile companion\main.py 2>nul
if %errorlevel% neq 0 (
    echo ERROR: companion\main.py failed compile check.
    exit /b 1
)
for %%f in (companion\*.py) do (
    python -m py_compile "%%f" 2>nul
    if !errorlevel! neq 0 (
        echo ERROR: %%f failed compile check.
        exit /b 1
    )
)
echo  All modules compile OK
echo.

:: --- Step 4: Run tests ---
echo [4/6] Running test suite...
set TEST_LOG=%ROOT%build_test.log
python -m unittest discover -s companion -p "test_*.py" -v > "%TEST_LOG%" 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Some companion tests failed. See %TEST_LOG%
) else (
    echo  All companion tests passed.
)
echo.

:: --- Step 5: Build PyInstaller EXE ---
echo [5/6] Building MediaForge EXE (one-folder)...
if exist "%ROOT%dist\MediaForge" (
    echo Cleaning previous build...
    rmdir /s /q "%ROOT%dist\MediaForge"
)
if exist "%ROOT%build\MediaForge" (
    rmdir /s /q "%ROOT%build\MediaForge"
)

python -m PyInstaller --noconfirm MediaForge.spec 2>&1
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)
echo  EXE built successfully
echo.

:: --- Step 6: Verify build artifacts ---
echo [6/6] Verifying build artifacts...
python scripts\verify_build.py
if %errorlevel% neq 0 (
    echo ERROR: Build verification failed.
    exit /b 1
)
echo  Build verified OK
echo.

echo ============================================
echo  Build complete: MediaForge v%VERSION%
echo  Output: dist\MediaForge.exe
echo ============================================
echo.
exit /b 0

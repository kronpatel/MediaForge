@echo off
title MediaForge Clean Pipeline
setlocal enabledelayedexpansion

echo ============================================
echo  MediaForge Clean
echo ============================================
echo.

set ROOT=%~dp0

:: --- Remove PyInstaller build artifacts ---
if exist "%ROOT%dist\" (
    echo Removing dist\...
    rmdir /s /q "%ROOT%dist\"
)
if exist "%ROOT%build\" (
    echo Removing build\...
    rmdir /s /q "%ROOT%build\"
)
if exist "%ROOT%build\*.spec" (
    echo Removing spec files...
    del /q "%ROOT%build\*.spec" 2>nul
)

:: --- Remove __pycache__ directories ---
echo Removing __pycache__...
for /d /r "%ROOT%" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d"
)

:: --- Remove .pyc files ---
echo Removing .pyc files...
del /s /q "%ROOT%*.pyc" 2>nul

:: --- Remove test logs ---
if exist "%ROOT%build_test.log" (
    del "%ROOT%build_test.log"
)

:: --- Remove release artifacts ---
if exist "%ROOT%release\" (
    echo Removing release\...
    rmdir /s /q "%ROOT%release\"
)

:: --- Remove logs (keep production logs during development) ---
:: Uncomment below to also remove companion logs:
:: if exist "%ROOT%companion\logs\" (
::     echo Removing companion\logs\...
::     rmdir /s /q "%ROOT%companion\logs\"
:: )

echo.
echo ============================================
echo  Clean complete
echo ============================================
echo.
exit /b 0

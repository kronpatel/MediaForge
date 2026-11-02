@echo off
title MediaForge Backend

echo [MediaForge] Starting Backend Server...

:: Frozen / installed layout: MediaForgeBackend.exe ships next to this script.
if exist "%~dp0MediaForgeBackend.exe" (
    start "" /D"%~dp0" MediaForgeBackend.exe
) else (
    :: Source / development layout: launch backend/app.py via the interpreter.
    start "" /D"%~dp0" python backend/app.py
)

exit

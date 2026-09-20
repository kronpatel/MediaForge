@echo off
title MediaForge Backend

echo [MediaForge] Checking for engine updates...
if exist ".\.venv\Scripts\activate.bat" (
    call ".\.venv\Scripts\activate.bat"
    python -m pip install -U --quiet yt-dlp
) else if exist ".\venv\Scripts\activate.bat" (
    call ".\venv\Scripts\activate.bat"
    python -m pip install -U --quiet yt-dlp
) else (
    python -m pip install -U --quiet yt-dlp
)
echo [MediaForge] Starting Backend Server...

cd /d "%~dp0companion"
start pythonw main.py
exit

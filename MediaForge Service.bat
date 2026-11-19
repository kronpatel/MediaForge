@echo off
title MediaForge Background Service

:: Frozen / installed layout: MediaForgeService.exe ships next to this script.
if exist "%~dp0MediaForgeService.exe" (
    "%~dp0MediaForgeService.exe" %*
) else (
    :: Source / development layout: launch companion/background_service.py via python.
    python "%~dp0companion\background_service.py" %*
)

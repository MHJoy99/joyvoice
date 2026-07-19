@echo off
title JoyVoice — Floating Mic
echo.
echo  JoyVoice — The one you remember
echo  =================================
echo.
cd /d "%~dp0"
.venv\Scripts\python app\main.py
if errorlevel 1 (
    echo.
    echo  JoyVoice exited with error %errorlevel%
    pause
)

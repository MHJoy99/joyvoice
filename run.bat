@echo off
title JoyVoice — Floating Mic (Supervised Uptime Guard)
echo.
echo  JoyVoice — Floating Mic Dictation & Translation
echo  ================================================
echo  [Process Supervisor Active: Auto-restarts on any failure]
echo.
cd /d "%~dp0"

:restart
echo [%date% %time%] Starting JoyVoice...
.venv\Scripts\python app\main.py
set EXIT_CODE=%errorlevel%

if %EXIT_CODE% EQU 0 (
    echo.
    echo [%date% %time%] JoyVoice exited cleanly.
    goto :end
)

echo.
echo [%date% %time%] JoyVoice process exited with code %EXIT_CODE%.
echo Auto-restarting JoyVoice in 3 seconds... (Press Ctrl+C to stop)
timeout /t 3 /nobreak >nul
goto :restart

:end
echo Press any key to exit launcher.
pause >nul

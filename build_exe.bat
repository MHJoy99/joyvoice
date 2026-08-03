@echo off
setlocal

rem Build JoyVoice from the repository root using the project virtual environment.
set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not find .venv\Scripts\python.exe in %CD%.
    endlocal & exit /b 1
)

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean JoyVoice.spec
set "BUILD_EXIT=%ERRORLEVEL%"

if not "%BUILD_EXIT%"=="0" (
    endlocal & exit /b %BUILD_EXIT%
)

if not exist "dist\JoyVoice.exe" (
    echo [ERROR] PyInstaller succeeded but dist\JoyVoice.exe was not created.
    endlocal & exit /b 1
)

echo [OK] Created %CD%\dist\JoyVoice.exe
endlocal & exit /b 0

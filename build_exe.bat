@echo off
setlocal

rem Build JoyVoice from the repository root using the project virtual environment.
set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not find .venv\Scripts\python.exe in %CD%.
    endlocal & exit /b 1
)

if not defined JV_DIST_PATH set "JV_DIST_PATH=dist"
if not defined JV_WORK_PATH set "JV_WORK_PATH=build"

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath "%JV_DIST_PATH%" --workpath "%JV_WORK_PATH%" JoyVoice.spec
set "BUILD_EXIT=%ERRORLEVEL%"

if not "%BUILD_EXIT%"=="0" (
    endlocal & exit /b %BUILD_EXIT%
)

if not exist "%JV_DIST_PATH%\JoyVoice.exe" (
    echo [ERROR] PyInstaller succeeded but %JV_DIST_PATH%\JoyVoice.exe was not created.
    endlocal & exit /b 1
)

echo [OK] Created %JV_DIST_PATH%\JoyVoice.exe
endlocal & exit /b 0

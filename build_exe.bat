@echo off
setlocal

rem ============================================================================
rem  JoyVoice build script.
rem
rem  Builds a onedir (folder-based) Windows EXE with PyInstaller, bundling the
rem  faster-whisper / ctranslate2 / PyAV / NVIDIA CUDA DLLs that PyInstaller's
rem  default import analysis tends to miss.
rem
rem  Output: dist\JoyVoice\JoyVoice.exe
rem
rem  Safe to double-click from Explorer -- all paths are anchored to this
rem  script's own folder (%~dp0), not the caller's current directory.
rem ============================================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo  JoyVoice build
echo  Working directory: %cd%
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not find .venv\Scripts\python.exe
    echo         Create the virtual environment first, e.g.:
    echo             python -m venv .venv
    echo             .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "app\main.py" (
    echo [ERROR] Could not find app\main.py -- are you running this from the
    echo         JoyVoice repo root?
    echo.
    pause
    exit /b 1
)

if not exist "assets\icon.ico" (
    echo [WARNING] assets\icon.ico not found -- the build will proceed without
    echo           a custom icon.
    echo.
)

echo [1/2] Running PyInstaller...
echo.

".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --name "JoyVoice" ^
    --windowed ^
    --onedir ^
    --icon "assets\icon.ico" ^
    --add-data "assets;assets" ^
    --collect-all faster_whisper ^
    --collect-all ctranslate2 ^
    --collect-all av ^
    --collect-all nvidia.cublas ^
    --collect-all nvidia.cudnn ^
    "app\main.py"

set BUILD_RESULT=%ERRORLEVEL%

echo.
echo [2/2] Build finished with exit code %BUILD_RESULT%.
echo.

if not "%BUILD_RESULT%"=="0" (
    echo ============================================================
    echo  BUILD FAILED. Scroll up for the PyInstaller error output.
    echo ============================================================
    echo.
    pause
    exit /b %BUILD_RESULT%
)

if exist "dist\JoyVoice\JoyVoice.exe" (
    echo ============================================================
    echo  BUILD SUCCEEDED
    echo  Output: %cd%\dist\JoyVoice\JoyVoice.exe
    echo ============================================================
) else (
    echo ============================================================
    echo  PyInstaller reported success but dist\JoyVoice\JoyVoice.exe
    echo  was not found. Check the output above for details.
    echo ============================================================
)

echo.
pause
endlocal

@echo off
REM ============================================================
REM Lyric Video Toolkit - Windows Setup
REM ============================================================
REM This installs everything needed for:
REM   - lyric_video_creator.py
REM   - auto_lyric_sync.py
REM   - lyric_video_app.py
REM
REM Run this from a normal Command Prompt (not necessarily admin).
REM Requires Python 3.9+ already installed and on PATH.
REM ============================================================

echo.
echo === Checking Python ===
python --version
if errorlevel 1 (
    echo Python was not found on PATH. Install it from https://www.python.org/downloads/
    echo and make sure to check "Add Python to PATH" during setup.
    pause
    exit /b 1
)

echo.
echo === Upgrading pip ===
python -m pip install --upgrade pip

echo.
echo === Installing Python packages ===
python -m pip install -r requirements.txt

echo.
echo === Checking for ffmpeg ===
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo.
    echo ffmpeg was NOT found on PATH.
    echo moviepy needs ffmpeg to read/write video and audio files.
    echo.
    echo Easiest fix (Windows 10/11, run in an elevated Command Prompt):
    echo     winget install ffmpeg
    echo.
    echo Or download a build from https://www.gyan.dev/ffmpeg/builds/
    echo and add its "bin" folder to your PATH.
    echo.
) else (
    echo ffmpeg found.
)

echo.
echo === Setup complete ===
echo.
echo Notes:
echo  - First run of auto_lyric_sync.py (align mode) will download the
echo    WhisperX alignment model the first time - this requires an
echo    internet connection and may take a few minutes.
echo  - If you have an NVIDIA GPU and want faster processing, you can
echo    optionally install a CUDA-enabled build of PyTorch instead of
echo    the CPU version that whisperx pulled in. See:
echo    https://pytorch.org/get-started/locally/
echo.
pause

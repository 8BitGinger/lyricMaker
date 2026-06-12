#!/usr/bin/env bash
# ============================================================
# Lyric Video Toolkit - Linux/macOS Setup
# ============================================================
# This installs everything needed for:
#   - lyric_video_creator.py
#   - auto_lyric_sync.py
#   - lyric_video_karaoke.py
#
# Usage:
#   chmod +x install_unix.sh
#   ./install_unix.sh
# ============================================================

set -e

echo "=== Checking Python ==="
if ! command -v python3 &> /dev/null; then
    echo "python3 not found. Install Python 3.9+ first."
    exit 1
fi
python3 --version

echo
echo "=== Upgrading pip ==="
python3 -m pip install --upgrade pip

echo
echo "=== Installing Python packages ==="
python3 -m pip install -r requirements.txt --break-system-packages 2>/dev/null \
    || python3 -m pip install -r requirements.txt

echo
echo "=== Checking for ffmpeg ==="
if ! command -v ffmpeg &> /dev/null; then
    echo "ffmpeg not found."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Install it with: brew install ffmpeg"
    else
        echo "Install it with: sudo apt install ffmpeg   (Debian/Ubuntu)"
        echo "             or: sudo dnf install ffmpeg   (Fedora)"
    fi
else
    echo "ffmpeg found: $(command -v ffmpeg)"
fi

echo
echo "=== Setup complete ==="
echo
echo "Notes:"
echo " - First run of auto_lyric_sync.py (align mode) will download the"
echo "   WhisperX alignment model the first time - requires internet"
echo "   and may take a few minutes."
echo " - If you have an NVIDIA GPU and want faster processing, consider"
echo "   installing a CUDA-enabled build of PyTorch:"
echo "   https://pytorch.org/get-started/locally/"

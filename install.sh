#!/bin/bash
set -e

echo "Installing system audio libraries (requires sudo)..."
if command -v apt-get >/dev/null; then
    sudo apt-get update && sudo apt-get install -y portaudio19-dev libsndfile1
else
    echo "Non-apt system detected: install your distro's portaudio and libsndfile packages manually."
fi

pip install -r requirements.txt

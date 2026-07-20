#!/bin/bash
set -e

pip install -r requirements.txt -r requirements-build.txt
pyinstaller ffxiv-tts.spec

cp -r settings dist/settings
cp -r lexicons dist/lexicons

echo "Build ready in dist/: ffxiv-tts, ffxiv-tts-settings, settings/, lexicons/"

#!/bin/bash
set -e

cd "$(dirname "$0")"

# uv-created virtualenvs have no pip binary, so a bare `pip` would fall through to the
# system Python and fail outright on externally-managed distros like Arch.
if python -m pip --version >/dev/null 2>&1; then
    PIP="python -m pip"
elif command -v uv >/dev/null 2>&1; then
    PIP="uv pip"
else
    echo "No pip or uv available. Activate your virtualenv first." >&2
    exit 1
fi

if [ -z "$VIRTUAL_ENV" ]; then
    echo "Warning: no virtualenv is active - installing into the current Python." >&2
fi

$PIP install -r requirements.txt -r requirements-build.txt
# spaCy models aren't normal pip dependencies; misaki needs this one for English.
python -m spacy download en_core_web_sm

pyinstaller --noconfirm ffxiv-tts.spec

# Refresh lexicons, but keep any settings you already configured in dist/ - a plain
# `cp -r settings dist/settings` would also nest a second copy inside the existing one.
rm -rf dist/lexicons
cp -r lexicons dist/lexicons
if [ -d dist/settings ]; then
    echo "Kept your existing dist/settings (delete it to reset to defaults)."
else
    cp -r settings dist/settings
fi

echo "Build ready in dist/: ffxiv-tts, ffxiv-tts-settings, settings/, lexicons/"

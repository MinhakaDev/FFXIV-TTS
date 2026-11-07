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

# settings/ isn't copied: the binary carries the defaults and merges them into
# dist/settings when it starts, keeping anything already configured there. Refresh the
# lexicon packages, but keep the name saved on the Name screen.
rm -rf dist/lexicons.new
cp -r lexicons dist/lexicons.new
rm dist/lexicons.new/Your-Name/lexicon.pls
if [ -f dist/lexicons/Your-Name/lexicon.pls ]; then
    mv dist/lexicons/Your-Name/lexicon.pls dist/lexicons.new/Your-Name/
fi
rm -rf dist/lexicons
mv dist/lexicons.new dist/lexicons

# The Qwen3-TTS voices ship like lexicons/; the engine downloads everything else itself.
rm -rf dist/qwen3-voices
if [ -d qwen3-voices ]; then
    cp -r qwen3-voices dist/qwen3-voices
fi

echo "Build ready in dist/: ffxiv-tts and lexicons/ (settings/ is created on first run)"

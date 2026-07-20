@echo off
pip install -r requirements.txt -r requirements-build.txt
python -m spacy download en_core_web_sm
pyinstaller ffxiv-tts.spec

xcopy /E /I /Y settings dist\settings
xcopy /E /I /Y lexicons dist\lexicons

echo Build ready in dist\: ffxiv-tts.exe, ffxiv-tts-settings.exe, settings\, lexicons\

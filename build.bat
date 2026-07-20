@echo off
pip install -r requirements-build.txt
pyinstaller ffxiv-tts.spec

xcopy /E /I /Y settings dist\settings
xcopy /E /I /Y lexicons dist\lexicons

echo Build ready in dist\: ffxiv-tts.exe, ffxiv-tts-settings.exe, settings\, lexicons\

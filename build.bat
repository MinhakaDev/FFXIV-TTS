@echo off
cd /d "%~dp0"

if "%VIRTUAL_ENV%"=="" echo Warning: no virtualenv is active - installing into the current Python.

python -m pip install -r requirements.txt -r requirements-build.txt || goto :error
rem spaCy models aren't normal pip dependencies; misaki needs this one for English.
python -m spacy download en_core_web_sm || goto :error

pyinstaller --noconfirm ffxiv-tts.spec || goto :error

rem settings\ isn't copied: the binary merges its built-in defaults into dist\settings
rem when it starts. Refresh the lexicon packages, but keep the name saved on the Name screen.
if exist dist\lexicons.new rmdir /S /Q dist\lexicons.new
xcopy /E /I /Y lexicons dist\lexicons.new
del dist\lexicons.new\Your-Name\lexicon.pls
if exist dist\lexicons\Your-Name\lexicon.pls move /Y dist\lexicons\Your-Name\lexicon.pls dist\lexicons.new\Your-Name\
if exist dist\lexicons rmdir /S /Q dist\lexicons
ren dist\lexicons.new lexicons

echo Build ready in dist\: ffxiv-tts.exe and lexicons\ (settings\ is created on first run)
goto :eof

:error
echo Build failed. >&2
exit /b 1

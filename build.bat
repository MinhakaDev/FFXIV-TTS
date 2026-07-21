@echo off
cd /d "%~dp0"

if "%VIRTUAL_ENV%"=="" echo Warning: no virtualenv is active - installing into the current Python.

python -m pip install -r requirements.txt -r requirements-build.txt || goto :error
rem spaCy models aren't normal pip dependencies; misaki needs this one for English.
python -m spacy download en_core_web_sm || goto :error

pyinstaller --noconfirm ffxiv-tts.spec || goto :error

rem Refresh lexicons, but keep any settings already configured in dist\.
if exist dist\lexicons rmdir /S /Q dist\lexicons
xcopy /E /I /Y lexicons dist\lexicons
if exist dist\settings (
    echo Kept your existing dist\settings ^(delete it to reset to defaults^).
) else (
    xcopy /E /I /Y settings dist\settings
)

echo Build ready in dist\: ffxiv-tts.exe, ffxiv-tts-settings.exe, settings\, lexicons\
goto :eof

:error
echo Build failed. >&2
exit /b 1

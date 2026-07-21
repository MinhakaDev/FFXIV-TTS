# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller ffxiv-tts.spec
# Produces a single dist/ffxiv-tts holding both the settings screens and the TTS runtime.
# settings/ and lexicons/ are NOT bundled here on purpose - they must ship as editable
# folders next to the executable (see build.sh / build.bat), since the app writes to them
# at runtime.

import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# These must come from the machine the app runs on, not the one it was built on.
# "pipewire" and "pulse" are ALSA *plugins*, which libasound loads from paths compiled
# into it. A bundled libasound built on the CI runner looks for them in that distro's
# plugin directory, which doesn't exist elsewhere, so those devices silently disappear
# from the device list and audio can't reach the user's real output.
HOST_PROVIDED_LIBS = ('libasound.so', 'libpulse.so', 'libpulse-simple.so', 'libjack.so')


def use_host_libraries(binaries):
    return [
        entry for entry in binaries
        if not os.path.basename(entry[0]).startswith(HOST_PROVIDED_LIBS)
    ]

# kokoro's G2P dependency chain (misaki -> phonemizer -> segments -> csvw -> language_tags,
# plus espeakng_loader's bundled espeak-ng data and the en_core_web_sm spaCy model misaki
# loads for English) ships data files that are read by path at runtime rather than imported,
# so PyInstaller's import-tracing never finds them on its own. Explicitly collect each one's
# data. en_core_web_sm itself is fetched at build time via `spacy download` (see build.sh /
# build.bat / the CI workflow) since it isn't a normal pip dependency.
def collect_packages(*packages):
    datas, binaries, hiddenimports = [], [], []
    for pkg in packages:
        pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hiddenimports
    return datas, binaries, hiddenimports


# customtkinter ships its widget themes as .json files loaded by path at runtime, so it
# needs collecting the same way.
datas, binaries, hiddenimports = collect_packages(
    'kokoro', 'misaki', 'phonemizer', 'segments', 'csvw', 'language_tags',
    'espeakng_loader', 'en_core_web_sm', 'customtkinter',
)

analysis = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
analysis.binaries = use_host_libraries(analysis.binaries)
pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

# console=False so double-clicking doesn't leave a terminal window hanging around;
# the app tees stdout into its own log panel instead.
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    [],
    name='ffxiv-tts',
    console=False,
)

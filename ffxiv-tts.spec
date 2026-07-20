# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller ffxiv-tts.spec
# Produces dist/ffxiv-tts (the TTS runtime) and dist/ffxiv-tts-settings (the settings GUI).
# settings/ and lexicons/ are NOT bundled here on purpose - they must ship as editable
# folders next to the executables (see build.sh / build.bat), since settings_gui.py writes
# to them at runtime.

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# kokoro's G2P dependency chain (misaki -> phonemizer -> segments -> csvw -> language_tags,
# plus espeakng_loader's bundled espeak-ng data and the en_core_web_sm spaCy model misaki
# loads for English) ships data files that are read by path at runtime rather than imported,
# so PyInstaller's import-tracing never finds them on its own. Explicitly collect each one's
# data. en_core_web_sm itself is fetched at build time via `spacy download` (see build.sh /
# build.bat / the CI workflow) since it isn't a normal pip dependency.
datas = []
binaries = []
hiddenimports = []
for pkg in ('kokoro', 'misaki', 'phonemizer', 'segments', 'csvw', 'language_tags', 'espeakng_loader', 'en_core_web_sm'):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

main_a = Analysis(
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
main_pyz = PYZ(main_a.pure, main_a.zipped_data, cipher=block_cipher)
main_exe = EXE(
    main_pyz,
    main_a.scripts,
    main_a.binaries,
    main_a.zipfiles,
    main_a.datas,
    [],
    name='ffxiv-tts',
    console=True,
)

gui_a = Analysis(
    ['src/settings_gui.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    gui_a.binaries,
    gui_a.zipfiles,
    gui_a.datas,
    [],
    name='ffxiv-tts-settings',
    console=False,
)

# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller ffxiv-tts.spec
# Produces dist/ffxiv-tts (the TTS runtime) and dist/ffxiv-tts-settings (the settings GUI).
# settings/ and lexicons/ are NOT bundled here on purpose - they must ship as editable
# folders next to the executables (see build.sh / build.bat), since settings_gui.py writes
# to them at runtime.

block_cipher = None

main_a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[],
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

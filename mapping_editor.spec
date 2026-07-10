# PyInstaller spec for Archipelago Mapping Preset Editor
# Usage: pyinstaller --noconfirm --clean mapping_editor.spec

block_cipher = None

a = Analysis(
    ["editor.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "PIL._tkinter_finder",
        "PIL.Image",
        "PIL.ImageTk",
        "tab_tree",
        "mapping_preset_io",
        "apworld_support",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ArchipelagoMappingEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

from PyInstaller.utils.hooks import collect_data_files

# Collect all files under the 'icons' directory (including graphics and tomba subdirectories)
icon_files = collect_data_files('icons', include_py_files=False)

# Ensure icons directory is included in the datas section
a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=icon_files + [('icons', 'icons')],  # Explicitly include the icons directory
    hiddenimports=['gui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Tomba2Edit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

from PyInstaller.utils.hooks import collect_data_files

# Collect all files under the 'icons' directory
icon_files = collect_data_files('icons', include_py_files=False)

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    # The labels folder has to come along too - functions/labels.py looks
    # for it next to the exe (sys._MEIPASS) when frozen.
    datas=icon_files + [('icons', 'icons'), ('labels', 'labels')],
    hiddenimports=['gui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# Use absolute path to test
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
    icon=r"C:\Users\Patryk\PycharmProjects\Tomba310\icons\tomba\tomba1.ico",  # Set your icon path here
)
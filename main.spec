# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Tomba2Edit.

PyInstaller does not cross-compile. This one spec builds for whichever
OS it is run on - a .exe on Windows, a .app on macOS - and each has to
be built on that OS, on a machine with the dependencies installed.

WHAT IS DELIBERATELY LEFT OUT

The unfiltered build came to 60 MB, and most of the difference was
things the tool never touches:

    icons/          6.9 MB and 3908 files, of which gui/icons.py names
                    16, totalling 23 KB - and the old datas list picked
                    the folder up twice over. ICON_FILES below reads
                    icons.py for the ones it actually asks for, so
                    adding an icon there is enough to get it bundled.
    opengl32sw.dll  21 MB of software OpenGL. Qt falls back to it when
                    the machine has no usable GL driver, which for a
                    viewer built entirely out of GL 3.3 shaders is not
                    a situation worth carrying 21 MB for - it would run
                    at seconds per frame if it ran at all. SEE THE NOTE
                    ON IT BELOW before dropping it from a build meant
                    for a VM or a remote desktop.
    Qt6Pdf,         12 MB between them, for a PDF engine, a network
    Qt6Network,     stack and its TLS library. Nothing here opens a
    libcrypto/ssl   socket or a PDF.
    Qml/Quick/Svg   the QML runtime. This is a QtWidgets app.

numpy's BLAS (libopenblas, 38 MB) is NOT excluded and cannot be:
numpy imports numpy.linalg during `import numpy`, so a build without it
fails on the first line. It is the single largest thing in the exe and
the only way to be rid of it would be to stop using numpy.
"""
import os
import re
import sys

BUILD_DIR = os.path.abspath(SPECPATH)


def _icon_files():
    """The icon files icons.py asks for, as (source, destination folder)
    pairs. Read out of the source rather than listed here so the two
    can't drift apart - a missing icon is a blank button in the built
    app and nothing else, which is not a failure anyone would notice
    until a user did."""
    source = os.path.join(BUILD_DIR, "icons", "icons.py")
    with open(source, encoding="utf-8") as f:
        wanted = sorted(set(re.findall(r'resource_path\("([^"]+)"\)', f.read())))
    files = []
    for relative in wanted:
        path = os.path.join(BUILD_DIR, relative.replace("/", os.sep))
        if os.path.exists(path):
            files.append((path, os.path.dirname(relative)))
        else:
            print(f"spec: icons.py names {relative}, which isn't there")
    return files


# Matched against the name each binary is bundled under, lowercased.
EXCLUDED_BINARIES = (
    "opengl32sw",
    "qt6pdf",
    "qt6network",
    "qt6qml", "qt6quick", "qt6svg", "qt6dbus",
    "libcrypto", "libssl",
)

# Never imported by anything here; a hook or a stray reference can drag
# them in regardless.
EXCLUDED_MODULES = [
    "tkinter", "unittest", "pydoc", "doctest", "pdb", "lib2to3",
    "sqlite3", "test", "distutils", "setuptools", "pip",
    "matplotlib", "scipy", "pandas",
    "PyQt6.QtNetwork", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtPdf",
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
]


def _keep(entry):
    name = entry[0].lower()
    return not any(dropped in name for dropped in EXCLUDED_BINARIES)


a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    # labels/ is read at runtime by functions/labels.py, which looks for
    # it beside the executable (sys._MEIPASS when frozen).
    datas=_icon_files() + [('labels', 'labels')],
    hiddenimports=['gui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=0,
)

a.binaries = [entry for entry in a.binaries if _keep(entry)]

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
    icon=os.path.join(BUILD_DIR, "icons", "tomba", "tomba1.ico"),
)

if sys.platform == "darwin":
    # macOS wants a bundle rather than a bare executable, or it has
    # nowhere to put the icon and Gatekeeper has nothing to check.
    app = BUNDLE(
        exe,
        name='Tomba2Edit.app',
        icon=os.path.join(BUILD_DIR, "icons", "tomba", "tomba1.ico"),
        bundle_identifier='club.tomba.tomba2edit',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
        },
    )

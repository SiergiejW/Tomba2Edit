# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Tomba2Edit.

    pyinstaller main.spec

PyInstaller does not cross-compile, and cannot: it builds by copying
the interpreter and the compiled extensions off the machine it is run
on. This one spec builds for whichever OS runs it - a .exe on Windows,
a .app on macOS - but the Mac build has to happen on a Mac, with the
same dependencies installed there (PyQt6, PyOpenGL, numpy, pillow).

A macOS build is also for one CPU architecture: the one its Python is.
Built on Apple silicon it will not run on an Intel Mac. `target_arch`
in EXE below can be set to 'universal2' instead, but only if every
wheel on that machine is universal2, which is not a given.

An unsigned .app is refused by Gatekeeper on first open. Either
right-click it and choose Open, or run:

    xattr -dr com.apple.quarantine Tomba2Edit.app

WHAT IS DELIBERATELY LEFT OUT

The unfiltered build came to 60 MB, and most of the difference was
things the tool never touches:

    icons/          6.9 MB and 3908 files, of which icons/icons.py
                    names 16, totalling 23 KB - and the old datas list
                    picked the whole folder up twice over. _icon_files()
                    below reads icons.py for the ones it actually asks
                    for, so adding an icon there is enough to get it
                    bundled.
    opengl32sw.dll  21 MB of software OpenGL. Qt falls back to it when
                    the machine has no usable GL driver, which for a
                    viewer built entirely out of GL 3.3 shaders is not
                    worth 21 MB - it would run at seconds per frame if
                    it ran at all. The exception is a virtual machine or
                    a remote desktop session, where there may be no GPU
                    driver to fall back FROM: for a build meant to run
                    in one of those, take "opengl32sw" back out of
                    EXCLUDED_BINARIES.
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
#
# Qt6Network is NOT excluded, however little this tool wants a network
# stack: Qt6Multimedia.dll links against it, so dropping it makes the
# built exe die on startup with "DLL load failed while importing
# QtMultimedia". The voice and music players need QtMultimedia.
EXCLUDED_BINARIES = (
    "opengl32sw",
    "qt6pdf",
    "qt6qml", "qt6quick", "qt6svg", "qt6dbus",
    "libcrypto", "libssl",
)

# Never imported by anything here; a hook or a stray reference can drag
# them in regardless. QtNetwork stays for the reason above.
EXCLUDED_MODULES = [
    "tkinter", "unittest", "pydoc", "doctest", "pdb", "lib2to3",
    "sqlite3", "test", "distutils", "setuptools", "pip",
    "matplotlib", "scipy", "pandas",
    "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtPdf",
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
]


def _multimedia_plugins():
    """Qt's audio backends, as (source, destination folder).

    QMediaPlayer and QAudioSink do nothing without one of these: the
    module imports, and then every file silently fails to play. They sit
    in a plugins folder PyInstaller does not pick up on its own."""
    import PyQt6
    root = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins",
                        "multimedia")
    if not os.path.isdir(root):
        print("spec: no multimedia plugins found - audio will not play")
        return []
    return [(os.path.join(root, name), os.path.join("PyQt6", "Qt6",
                                                    "plugins", "multimedia"))
            for name in os.listdir(root)]


def _keep(entry):
    name = entry[0].lower()
    return not any(dropped in name for dropped in EXCLUDED_BINARIES)


# Windows takes the .ico straight; for a Mac bundle PyInstaller wants an
# .icns and will make one from a PNG, so hand it the PNG there.
APP_ICON = os.path.join(BUILD_DIR, "icons", "tomba",
                        "tomba1.png" if sys.platform == "darwin" else "tomba1.ico")


a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    # labels/ is read at runtime by functions/labels.py, which looks for
    # it beside the executable (sys._MEIPASS when frozen).
    datas=_icon_files() + [('labels', 'labels')] + _multimedia_plugins(),
    # QtMultimedia is imported inside the functions that play audio, so
    # the analysis does not always see it; naming it here is what gets
    # its DLLs collected.
    hiddenimports=['gui', 'PyQt6.QtMultimedia', 'lameenc'],
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
    icon=APP_ICON,
)

if sys.platform == "darwin":
    # macOS wants a bundle rather than a bare executable, or it has
    # nowhere to put the icon and Gatekeeper has nothing to check.
    app = BUNDLE(
        exe,
        name='Tomba2Edit.app',
        icon=APP_ICON,
        bundle_identifier='club.tomba.tomba2edit',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
        },
    )

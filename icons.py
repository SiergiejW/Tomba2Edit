import os
import sys


def resource_path(relative_path):
    """Get the absolute path to the resource (icon files, etc.) inside a bundled app."""
    try:
        # PyInstaller creates a temporary folder at runtime and places the app's resources there.
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath("icons")

    return os.path.join(base_path, relative_path)
icon_window = resource_path("icons/tomba/tomba1.ico")
icon_TXTD = resource_path("icons/graphics/quill.png")
icon_SPRT = resource_path("icons/graphics/fruit.png")
icon_TANP = resource_path("icons/tomba/tomba1.png")
icon_SMST = resource_path("icons/graphics/block.png")
icon_MDAT = resource_path("icons/tomba/79.png")
icon_SCLD = resource_path("icons/graphics/molecule.png")
icon_BGMP = resource_path("icons/graphics/map.png")
icon_BETP = resource_path("icons/graphics/cassette.png")
icon_ALFD = resource_path("icons/graphics/clapperboard.png")
icon_DRWB = resource_path("icons/graphics/border-all.png")

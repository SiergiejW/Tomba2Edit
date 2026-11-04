import os
import sys


def resource_path(relative_path):
    """Get the absolute path to the resource (icon files, etc.) inside a bundled app."""
    try:
        # PyInstaller creates a temporary folder at runtime and places the app's resources there.
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)
icon_window = resource_path("icons/tomba/tomba1.ico")
icon_disc = resource_path("icons/graphics/disc.png")
icon_TXTD = resource_path("icons/graphics/quill.png")
icon_TXTD_master = resource_path("icons/graphics/book-brown.png")
icon_TXTD_entry = resource_path("icons/graphics/report.png")
icon_TXT2 = resource_path("icons/graphics/quill.png")
icon_TXT2_entry = resource_path("icons/graphics/report.png")
icon_SPRT = resource_path("icons/graphics/fruit.png")
icon_TANP = resource_path("icons/tomba/tomba1.png")
icon_SMST = resource_path("icons/graphics/block.png")
icon_MDAT = resource_path("icons/tomba/79.png")
icon_SCLD = resource_path("icons/graphics/molecule.png")
icon_BGMP = resource_path("icons/graphics/map.png")
icon_BETP = resource_path("icons/graphics/cassette.png")
icon_ALFD = resource_path("icons/graphics/clapperboard.png")
icon_DRWB = resource_path("icons/graphics/border-all.png")
icon_VRAM = resource_path("icons/tomba/123.png")
icon_CVRAM = resource_path("icons/tomba/44.png")
# Palette previews generated from US retail assets.
icon_placement_chest = resource_path("icons/placements/us-retail/chest.png")
icon_placement_crystal = resource_path("icons/placements/us-retail/crystal.png")
icon_placement_item = resource_path("icons/placements/us-retail/item.png")
icon_placement_apple = resource_path("icons/placements/us-retail/apple.png")
icon_placement_weapon = resource_path("icons/placements/us-retail/weapon.png")

"""Draws a DRWB grid.

Nothing here touches Qt - it returns PIL images, and the caller
displays them.

The grid is drawn level-aligned by default (X across, Z down, the same
way round the DRWA viewer draws its own map), which is the TRANSPOSE of
how the file stores it. `stored` draws it the file's way instead, for
reading alongside a hex editor.
"""
import colorsys

from PIL import Image

from gui.drwb.drwb_parser import BITS

EMPTY_COLOR = (0, 0, 0, 0)          # left to the canvas's own checker

# Cells that hold a flag but no geometry, and cells that hold geometry
# but no flag - the two ways a DRWB and its level can disagree. The
# second never happens on the disc, which is why it's drawn in alarm
# red: seeing it means the DRWB has been matched to the wrong MDAT.
NO_GEOMETRY_COLOR = (95, 95, 110)
UNFLAGGED_COLOR = (255, 70, 70)

# Set, but not by whatever the current mode is colouring - kept visible
# so a single flag's region can be seen against the rest of the map.
SET_ELSEWHERE_COLOR = (70, 80, 95)

GOLDEN_RATIO_CONJUGATE = 0.6180339887498949

# key -> (menu label, what the colour means)
COLOR_MODES = (
    ("value", "Byte value",
     "A colour per distinct byte, so cells holding the same flags match"),
    ("low", "Low nibble",
     "Bits 0-3 only. In the big files these are a subset of the high nibble"),
    ("high", "High nibble",
     "Bits 4-7 only"),
    ("count", "Flags set",
     "How many of the eight bits are on, blue (one) to red (most)"),
    ("bit", "One flag",
     "A single bit's cells, chosen with the spin box beside this menu"),
)


def value_color(value, saturation=0.62, value_level=0.95):
    """A colour per byte value. Hashed onto the wheel so numbers that
    differ by a bit don't come out nearly the same colour."""
    hue = ((value * 2654435761) % 65536) / 65536.0
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value_level)
    return (int(r * 255), int(g * 255), int(b * 255))


def ramp_color(t, saturation=0.85, value=0.95):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    r, g, b = colorsys.hsv_to_rgb(0.66 * (1.0 - t), saturation, value)
    return (int(r * 255), int(g * 255), int(b * 255))


def cell_color(byte, mode, bit=0):
    """The colour for one cell's byte, or None to leave it empty."""
    if not byte:
        return None
    if mode == "low":
        low = byte & 0x0F
        return value_color(low) if low else None
    if mode == "high":
        high = byte >> 4
        return value_color(high) if high else None
    if mode == "count":
        set_bits = bin(byte).count("1")
        return ramp_color((set_bits - 1) / max(BITS - 1, 1))
    if mode == "bit":
        return value_color(1 << bit) if byte >> bit & 1 else None
    return value_color(byte)


def render_grid(drwb, mode="value", bit=0, stored=False, occupied=None):
    """The map, one pixel per cell.

    `occupied` is the set of (x, z) cells the level has geometry in
    (see drwb_parser.coverage) - when given, cells are shaded by
    whether the flags and the geometry agree, underneath the flags
    themselves:

        a cell with geometry and no flag  - red, this never happens
        a cell with a flag and no geometry - grey
    """
    side = drwb.side
    image = Image.new("RGBA", (side, side), EMPTY_COLOR)
    pixels = image.load()
    compare = occupied is not None and not stored
    for a in range(side):
        for b in range(side):
            byte = (drwb.raw_at(a, b) if stored else drwb.value_at(a, b)) or 0
            has_geometry = compare and (a, b) in occupied
            color = cell_color(byte, mode, bit)

            if color is None:
                if byte:
                    color = SET_ELSEWHERE_COLOR
                elif has_geometry:
                    color = UNFLAGGED_COLOR
                else:
                    continue
            elif compare and not has_geometry:
                # Flagged ground the level has no polygons on - dimmed
                # towards grey, so what does line up with the level is
                # what stands out.
                color = tuple(int(c * 0.45 + n * 0.55)
                              for c, n in zip(color, NO_GEOMETRY_COLOR))
            pixels[a, b] = tuple(color) + (255,)
    return image


def render_planes(drwb, zoom=3, gap=4):
    """All eight flags side by side, each as its own small map - the
    quickest way to see that every bit draws a connected region."""
    side = drwb.side
    sheet = Image.new("RGBA", ((side * zoom + gap) * BITS - gap, side * zoom),
                      (0, 0, 0, 0))
    for bit in range(BITS):
        tile = render_grid(drwb, mode="bit", bit=bit)
        sheet.alpha_composite(
            tile.resize((side * zoom, side * zoom), Image.Resampling.NEAREST),
            ((side * zoom + gap) * bit, 0))
    return sheet

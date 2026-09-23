"""Sky gradients: what three areas draw behind everything, not a picture.

FUN_A0E__80114178 (the water pig boss), FUN_A04__80118448 (Kujara Ranch,
outside past approximate location 0x2B) and FUN_A0L__8010bb64 (the
ending's first section, before its cutscene 4) each link four POLY_G4
quads across the whole 320-wide screen into the back of the ordering
table (0x1FFC). Vertices 0 and 1 are the top edge, 2 and 3 the bottom,
and every row is offset by s = pitch * -0x8E8 >> 12 - pitch being the
camera's turn about X (0x1F8000F0) in 4096ths of a circle. So a band sits
a fixed angle from level: 0x8E8 / 360 rows per degree about row 120.
"""
import os

import numpy as np

SCREEN_CENTRE = 120
ROWS_PER_DEGREE = 0x8E8 / 360.0

# Rows per degree the image is laid out in, over the backdrop's 180.
IMAGE_ROWS_PER_DEGREE = 8
IMAGE_WIDTH = 4


def _bgr(value):
    return (value & 0xFF, value >> 8 & 0xFF, value >> 16 & 0xFF)


BLUE = _bgr(0xAC0606)
LIGHT = _bgr(0xEA9898)
NAVY = _bgr(0x390000)
MID = _bgr(0xCB4F4F)

# Per quad: (top row, bottom row, top colour, bottom colour), rows at s = 0.
SKIES = {
    "A0E": ((-120, 60, BLUE, BLUE), (60, 120, BLUE, LIGHT),
            (120, 180, LIGHT, NAVY), (180, 600, NAVY, NAVY)),
    "A04": ((-200, 0, LIGHT, LIGHT), (0, 120, LIGHT, MID),
            (120, 360, MID, BLUE), (360, 600, BLUE, BLUE)),
    "A0L": ((-320, 60, BLUE, BLUE), (60, 120, BLUE, LIGHT),
            (120, 180, LIGHT, NAVY), (180, 600, NAVY, NAVY)),
}

DRAWERS = {
    "A0E": "FUN_A0E__80114178, every frame",
    "A04": "FUN_A04__80118448, outside past location 0x2B",
    "A0L": "FUN_A0L__8010bb64, in section 1 before ending cutscene 4",
}


def quads_for(overlay_path):
    return SKIES.get(os.path.basename(overlay_path or "")[:3].upper())


def image(overlay_path, span_degrees=180.0):
    """(h, w, 3) uint8 for the level viewer's backdrop - row 0 looking
    straight up, the last looking straight down - or None."""
    quads = quads_for(overlay_path)
    if quads is None:
        return None
    rows = int(span_degrees * IMAGE_ROWS_PER_DEGREE)
    degrees = -span_degrees / 2 + span_degrees * (np.arange(rows) + 0.5) / rows
    screen = SCREEN_CENTRE + ROWS_PER_DEGREE * degrees
    out = np.zeros((rows, 3), dtype=np.float64)
    painted = np.zeros(rows, dtype=bool)
    for top, bottom, top_colour, bottom_colour in quads:
        inside = (screen >= top) & (screen < bottom)
        t = ((screen[inside] - top) / (bottom - top))[:, None]
        out[inside] = (np.array(top_colour) * (1 - t) + np.array(bottom_colour) * t)
        painted |= inside
    # The game never exposes space beyond the first/last full-screen quad:
    # camera pitch stops first. The editor permits freer looking, so extend
    # the nearest edge colour instead of revealing the zero-filled (black)
    # part of this synthetic image before the viewer's own pitch clamp takes
    # over. This is especially visible in AREA_08's A04 gradient.
    first = np.flatnonzero(painted)
    if first.size:
        lo, hi = first[0], first[-1]
        out[:lo] = out[lo]
        out[hi + 1:] = out[hi]
    column = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    return np.repeat(column[:, None, :], IMAGE_WIDTH, axis=1)


def under(picture, sky):
    """`picture` (h, w, 3) with `sky` showing through its black - the
    transparent texels - both hung over the same span of pitch."""
    picture = np.asarray(picture, dtype=np.uint8)
    rows = np.minimum((np.arange(picture.shape[0]) + 0.5)
                      * sky.shape[0] / picture.shape[0], sky.shape[0] - 1).astype(int)
    column = sky[rows, 0]
    clear = picture.sum(axis=2) == 0
    out = picture.copy()
    out[clear] = np.broadcast_to(column[:, None, :], picture.shape)[clear]
    return out

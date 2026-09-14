"""Texture windows: faces drawn through a moving 64-texel cell of their page.

The Last Pig Boss's arena (A0F) animates part of its MDAT this way rather
than by palette. FUN_A0F__80115364 draws the room; its cell drawers
(FUN_A0F__80114a5c tris, FUN_A0F__80114e70 quads) read flags from the top
byte of each packet's second colour word, and a face with bit 2 or bit 3
set goes into an ordering-table slot of its own, framed by two GP0(E2h)
texture-window commands, with a scroll added to its UVs:

    bit 2   every frame: the window steps one cell along a 3x3 grid of
            64x64 cells, and u scrolls by -2
    bit 3   the same on odd frames only (src_SpriteAnimationFrame & 1),
            u scrolling by -1

Through the window a texel is cell + ((uv + scroll) & 63). The counters
(DAT_A0F__80120928..3A) all start at 0. Other areas set these flag bits
too, but their drawers mean something else by them, so the rules are per
overlay.
"""
import os
from dataclasses import dataclass

import numpy as np

CELL = 64
GRID = 3


@dataclass(frozen=True)
class Rule:
    mode: int           # which window the shaders take: 1 or 2
    flag: int           # the packet flag bit
    every: int          # frames per step
    scroll_u: int       # texels per step
    scroll_v: int


RULES = {"A0F": (Rule(1, 0x04, 1, -2, 0), Rule(2, 0x08, 2, -1, 0))}


def rules_for(overlay_path):
    """The rules an area's drawer follows, () for none."""
    return RULES.get(os.path.basename(overlay_path or "")[:3].upper(), ())


def window_at(rule, frame):
    """(cell u, cell v, scroll u, scroll v) in texels at game frame `frame`."""
    steps = frame // rule.every
    cell = steps % (GRID * GRID)
    return (float(cell % GRID * CELL), float(cell // GRID * CELL),
            float(rule.scroll_u * steps % CELL), float(rule.scroll_v * steps % CELL))


def mode_of(flags):
    """The window a face's flags put it through - bit 2 first, as the
    drawers test it - 0 for none."""
    return 1 if flags & 0x04 else 2 if flags & 0x08 else 0


def vertex_modes(model_data):
    """float32 per vertex: the window its face is drawn through."""
    out = np.zeros(len(model_data.get("vertices") or ()), dtype=np.float32)
    flags = model_data.get("face_flags") or ()
    for face, value in zip(model_data.get("faces") or (), flags):
        mode = mode_of(value)
        if mode:
            out[face] = mode
    return out

"""Texture windows: faces drawn through a moving cell of their page.

A GP0(E2h) texture-window command limits what a primitive samples: a texel
is cell + ((uv + scroll) & 63). Two areas animate MDAT faces this way, and
pick the faces by packet flags - the top byte of each packet's second
colour word, which their own cell drawers read:

A0F, the Last Pig Boss (FUN_A0F__80115364, FUN_A0F__80114a5c/80114e70):

    0x04    every frame the window steps a 3x3 grid of 64-texel cells and
            u scrolls by -2
    0x08    the same on odd frames only (src_SpriteAnimationFrame & 1),
            u scrolling by -1

A01, Large Mine Underground (f_RenderPipeAreaEnvironment, FUN_A01__801311fc),
while cursed: every 3 frames the window steps a 4x2 grid of 64-texel cells.
Faces flagged 0x10 are sent to the window's ordering-table slot, and so is
the lava its drawer builds (functions/environment_meshes.py), whose v also
scrolls by +1 a frame.

The counters all start at 0. Other areas set the same bits for other
things, so the rules are per overlay.
"""
import os
from dataclasses import dataclass

import numpy as np

CELL = 64
# Faces functions/environment_meshes.py builds carry this flag - past the
# packet byte, so it never meets a real one.
GENERATED = 0x100


@dataclass(frozen=True)
class Rule:
    mode: int               # which of the two windows the shaders take: 1 or 2
    flag: int               # the face flag bit
    step: int = 1           # frames per cell
    columns: int = 3
    rows: int = 3
    scroll_u: int = 0       # texels per scroll step
    scroll_v: int = 0
    scroll_step: int = 1    # frames per scroll step


RULES = {
    "A0F": (Rule(1, 0x04, scroll_u=-2),
            Rule(2, 0x08, step=2, scroll_u=-1, scroll_step=2)),
    "A01": (Rule(1, 0x10, step=3, columns=4, rows=2),
            Rule(2, GENERATED, step=3, columns=4, rows=2, scroll_v=1)),
}


def rules_for(overlay_path):
    """The rules an area's drawer follows, () for none."""
    return RULES.get(os.path.basename(overlay_path or "")[:3].upper(), ())


def window_at(rule, frame):
    """(cell u, cell v, scroll u, scroll v) in texels at game frame `frame`."""
    cell = (frame // rule.step) % (rule.columns * rule.rows)
    scrolls = frame // rule.scroll_step
    return (float(cell % rule.columns * CELL), float(cell // rule.columns * CELL),
            float(rule.scroll_u * scrolls % CELL), float(rule.scroll_v * scrolls % CELL))


def vertex_flags(model_data):
    """float32 per vertex: the flags of the face it belongs to."""
    out = np.zeros(len(model_data.get("vertices") or ()), dtype=np.float32)
    flags = model_data.get("face_flags") or ()
    for face, value in zip(model_data.get("faces") or (), flags):
        if value:
            out[face] = value
    return out

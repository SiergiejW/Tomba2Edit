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

A01, Large Mine Underground (f_RenderPipeAreaEnvironment), while cursed:
every 3 frames DAT_A01__801388ec steps u by 64 over four columns, and each
wrap steps DAT_A01__801388ee v by 64 over two rows. The cell drawers
(FUN_A01__8012f8d8 tris, FUN_A01__8013000c quads) ADD those to the u/v
bytes of a face flagged 0x10 - no window, so a face keeps its place in its
cell. A quad also flagged 0x80 takes the other path, where 0x08-0x40 are
per-vertex depth-cue flicker and nothing moves. The lava its drawer builds
(FUN_A01__801311fc, functions/environment_meshes.py) is a real E2 window
on the same counters, v scrolling by +1 a frame.

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
# gui/mdat/mdat.py marks a quad's faces with this, also past the packet byte.
QUAD = 0x200


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
    add: bool = False       # cell added to the uv bytes, not an E2 window
    skip: int = 0           # faces carrying all these bits don't take it


RULES = {
    "A0F": (Rule(1, 0x04, scroll_u=-2),
            Rule(2, 0x08, step=2, scroll_u=-1, scroll_step=2)),
    "A01": (Rule(1, 0x10, step=3, columns=4, rows=2, add=True, skip=QUAD | 0x80),
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

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

A08, the Water Temple (FUN_A08__8012a7cc, cell drawers FUN_A08__80140fbc/
801411d8): every frame DAT_A08__80145a6c steps u by 64 over three columns
and each wrap steps v by 64 over three rows, added to faces flagged 0x04.
That is the waterfall functions/uv_anim.py already finds and animates, so
it has no rule here - two would move it twice.

A0A, the Fire Pig Boss (FUN_A0A__8011024c, cell drawers FUN_A0A__8010f97c
tris, 8010fd74 quads): every 4 frames DAT_A0A__8011bf1e steps through six
(u, v) cells in the order the table at 0x8011BF20 lists them, and every
frame DAT_A0A__8011bf18 takes 1 off u, wrapping at 64. A face flagged 0x04
gets cell + scroll ADDED to its u/v bytes - the lava running - and one
flagged 0x08 the cell alone. The rule reads the table, and uv_anim's guess
at those faces is dropped (gui/clut_animation.py).

The counters all start at 0. Other areas set the same bits for other
things, so the rules are per overlay.
"""
import os
from dataclasses import dataclass, replace

import numpy as np

from functions import game_build

CELL = 64
# Faces functions/environment_meshes.py builds carry this flag - past the
# packet byte, so it never meets a real one.
GENERATED = 0x100
# gui/mdat/mdat.py marks a quad's faces with this, also past the packet byte.
QUAD = 0x200
# gui/smst/smst_parser.py marks every face with this: an actor's model part,
# which f_DrawModelPrimitiveStreamForCurrentArea sends through an area's cell
# drawers only in some areas (A08 yes; A01's FUN_80132dc0 and A0F's generic
# drawer never add the cell).
MODEL = 0x400


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
    models: bool = False    # actor model parts take it too, not only rooms
    # Cells in the order the drawer's own table lists them, not a grid:
    # (u, v) byte pairs at this overlay address - read by rules_for.
    cell_table: int = 0
    cell_count: int = 0
    cells: tuple = ()


RULES = {
    "A0F": (Rule(1, 0x04, scroll_u=-2),
            Rule(2, 0x08, step=2, scroll_u=-1, scroll_step=2)),
    "A01": (Rule(1, 0x10, step=3, columns=4, rows=2, add=True, skip=QUAD | 0x80),
            Rule(2, GENERATED, step=3, columns=4, rows=2, scroll_v=1)),
    "A0A": (Rule(1, 0x04, step=4, scroll_u=-1, add=True, cell_table=0x8011BF20, cell_count=6),
            Rule(2, 0x08, step=4, add=True, cell_table=0x8011BF20, cell_count=6)),
}


def rules_for(overlay_path):
    """The rules an area's drawer follows, () for none, with any cell table
    read out of the overlay."""
    rules = RULES.get(os.path.basename(overlay_path or "")[:3].upper(), ())
    if not any(rule.cell_table for rule in rules):
        return rules
    try:
        with open(overlay_path, "rb") as f:
            overlay = f.read()
    except OSError:
        return ()
    image = os.path.basename(overlay_path)[:3].upper()
    out = []
    for rule in rules:
        if rule.cell_table:
            at = game_build.overlay_offset(image, rule.cell_table)
            if at is None:
                continue
            raw = overlay[at:at + rule.cell_count * 2]
            rule = replace(rule, cells=tuple(zip(raw[0::2], raw[1::2])))
        out.append(rule)
    return tuple(out)


def window_at(rule, frame):
    """(cell u, cell v, scroll u, scroll v) in texels at game frame `frame`.
    An added cell carries its scroll in the same bytes."""
    step = frame // rule.step
    if rule.cells:
        cell_u, cell_v = rule.cells[step % len(rule.cells)]
    else:
        cell = step % (rule.columns * rule.rows)
        cell_u, cell_v = cell % rule.columns * CELL, cell // rule.columns * CELL
    scrolls = frame // rule.scroll_step
    scroll_u, scroll_v = rule.scroll_u * scrolls % CELL, rule.scroll_v * scrolls % CELL
    if rule.add:
        return float((cell_u + scroll_u) % 256), float((cell_v + scroll_v) % 256), 0.0, 0.0
    return float(cell_u), float(cell_v), float(scroll_u), float(scroll_v)


def vertex_flags(model_data):
    """float32 per vertex: the flags of the face it belongs to."""
    out = np.zeros(len(model_data.get("vertices") or ()), dtype=np.float32)
    flags = model_data.get("face_flags") or ()
    for face, value in zip(model_data.get("faces") or (), flags):
        if value:
            out[face] = value
    return out

"""The crystals and apples as pictures, ready to hang in the level.

functions/pickup_art.py says which sprite of the resident bank a reward
shows, which palette to recolour it with, and how the frames run. This
turns that into something drawable: every frame a level needs, cut out
of VRAM and packed into one texture, with the rectangle each landed in.

WHY A PALETTE OVERRIDE

A sprite piece carries the CLUT it was authored with, and for the seven
crystals that CLUT is the same one - they are one piece of art. What
tells a hundred-AP crystal from a thousand-AP one is that the pickup
routine puts a different palette on it before drawing (see
pickup_art.RewardArt.clut). So the piece is re-rendered per palette,
and the atlas is keyed by (frame, clut) rather than by frame.

WHAT SIZE THEY ARE

f_BuildActorSpriteQuadVertices, which turns a frame into four vertices,
multiplies every coordinate it writes by FIVE - so one texel is five
world units, whatever is being drawn. A PICKUP then gets the actor scale
of 0x1300 the pickup routine sets it to, in the PSX's 4096ths, which
makes a texel just under six. A sprite OBJECT's handler sets no scale at
all, so it keeps the five.

The reward's own width and height are NOT this. They are a collision
box - the routine doubles them into a second pair of fields - and using
them would size a crystal by what it can be picked up from rather than
by how big it is drawn.

WHAT ELSE IS DRAWN THIS WAY

Not only pickups: a handful of placed OBJECTS are sprites too - the
jumpable fish, the torch that burns until it is put out. They come in
through the same billboards() with art that
functions/object_sprites.py builds, and out of the same area bank.
"""
from dataclasses import dataclass, replace

import numpy as np

from gui.sprt import sprt_render
from gui.sprt.sprt_parser import load_sprt

# The bank every area shares - the first file of the resident chunk,
# right at the front of the DAT - and the one an area keeps to itself,
# which is SDAT id 10 (see functions/pickup_art.py).
RESIDENT_SPRT_ID = 0
AREA_SPRT_ID = 10

# The scale the pickup routine gives the actor, in the PSX's 4096ths.
ACTOR_SCALE = 0x1300
ONE = 0x1000

# What f_BuildActorSpriteQuadVertices multiplies every vertex it writes
# by, before the actor's scale is applied.
TEXEL_UNITS = 5

# A pickup's own scale on top of that; a sprite OBJECT sets none, so it
# gets the five and nothing more.
PICKUP_UNITS = TEXEL_UNITS * ACTOR_SCALE / ONE
OBJECT_UNITS = float(TEXEL_UNITS)

# A gap between packed frames, so filtering can't bleed one into the next.
PAD = 1


@dataclass
class Placed:
    """Where one frame ended up in the atlas, and how big it is.

    Sizes are in TEXELS, not world units: what a texel is worth differs
    between a pickup and a sprite object, and the frame itself does not
    know which is asking for it."""

    u0: float
    v0: float
    u1: float
    v1: float
    width: float            # in texels
    height: float
    # Where the sprite's own origin sits inside it, also in texels,
    # measured from the top left - a sprite is hung by its origin, not
    # by its corner.
    origin_x: float
    origin_y: float


class SpriteBank:
    """The resident sprite bank, cut against one area's VRAM."""

    def __init__(self, dat_path, dat_start, offset, size, vram):
        self.sprt = load_sprt(dat_path, dat_start, offset, size)
        self.textures = sprt_render.VRAMTextures(vram) if vram else None
        self._images = {}

    def count(self):
        return len(self.sprt.sprites)

    def image(self, frame, clut=None):
        """(RGBA image, origin x, origin y) for one frame, or None.

        `clut` replaces the palette every piece was authored with, which
        is how one crystal becomes seven."""
        key = (frame, clut)
        if key in self._images:
            return self._images[key]
        self._images[key] = None
        if 0 <= frame < len(self.sprt.sprites):
            sprite = self.sprt.sprites[frame]
            if clut is not None:
                sprite = replace(sprite, pieces=[replace(p, clut=clut)
                                                 for p in sprite.pieces])
            try:
                self._images[key] = sprt_render.render_sprite(sprite,
                                                              self.textures)
            except Exception:
                self._images[key] = None
        return self._images[key]


def build_atlas(banks, wanted):
    """(atlas as an RGBA array, {(bank, frame, clut): Placed}) for every
    frame a level needs.

    `banks` is {name: SpriteBank} - a level draws its pickups out of two,
    the resident one and its own.

    Packed in rows rather than tightly: a level asks for a few dozen
    frames of at most fifty texels, so the simple shelf is small enough
    and the arithmetic is worth not getting wrong."""
    cut = []
    for key in sorted(set(wanted)):
        which, frame, clut = key
        bank = banks.get(which)
        made = bank.image(frame, clut) if bank is not None else None
        if made is None:
            continue
        image, origin_x, origin_y = made
        if image.width and image.height:
            cut.append((key, np.asarray(image, dtype=np.uint8),
                        origin_x, origin_y))
    if not cut:
        return None, {}

    width = max(64, max(a.shape[1] for _k, a, _x, _y in cut) + PAD * 2)
    width = min(1024, max(width, int(np.sqrt(
        sum((a.shape[1] + PAD) * (a.shape[0] + PAD)
            for _k, a, _x, _y in cut)) * 1.4)))
    rows, row, row_width, row_height = [], [], 0, 0
    for entry in cut:
        w = entry[1].shape[1] + PAD
        if row and row_width + w > width:
            rows.append((row, row_height))
            row, row_width, row_height = [], 0, 0
        row.append(entry)
        row_width += w
        row_height = max(row_height, entry[1].shape[0] + PAD)
    if row:
        rows.append((row, row_height))

    height = max(1, sum(h for _r, h in rows) + PAD)
    width = max(width, max((sum(e[1].shape[1] + PAD for e in r)
                            for r, _h in rows), default=1) + PAD)
    atlas = np.zeros((height, width, 4), dtype=np.uint8)
    placed, y = {}, PAD
    for row, row_height in rows:
        x = PAD
        for key, pixels, origin_x, origin_y in row:
            h, w = pixels.shape[:2]
            atlas[y:y + h, x:x + w] = pixels
            placed[key] = Placed(
                u0=x / width, v0=y / height,
                u1=(x + w) / width, v1=(y + h) / height,
                width=float(w), height=float(h),
                origin_x=float(origin_x), origin_y=float(origin_y))
            x += w + PAD
        y += row_height
    return atlas, placed


@dataclass
class Billboard:
    """One pickup hanging in the level, with its animation."""

    index: int              # which instance of the scene it is
    x: float
    y: float
    z: float
    # (Placed, ticks) per step of the animation, in order.
    steps: tuple = ()
    loops: bool = False
    units: float = PICKUP_UNITS      # world units per texel

    def frame_now(self, tick):
        """Which frame is showing at `tick`, or None if it has none."""
        if not self.steps:
            return None
        total = sum(max(t, 1) for _p, t in self.steps)
        if self.loops:
            tick %= max(total, 1)
        elif tick >= total:
            return self.steps[-1][0]
        for placed, ticks in self.steps:
            tick -= max(ticks, 1)
            if tick < 0:
                return placed
        return self.steps[-1][0]


def billboards(instances, placed):
    """[Billboard, ...] for every pickup whose frames got into the atlas.

    An instance with no art - a chest, which is a model - is left out,
    and so is one whose sprite could not be cut.  """
    out = []
    for instance in instances:
        art = getattr(instance, "art", None)
        if art is None or not art.frames:
            continue
        key_clut = art.clut if art.recolored else None
        steps = tuple((placed[(art.bank, f.frame, key_clut)], f.ticks)
                      for f in art.frames
                      if (art.bank, f.frame, key_clut) in placed)
        if not steps:
            continue
        out.append(Billboard(index=instance.index, x=instance.x, y=instance.y,
                             z=instance.z, steps=steps, loops=art.loops,
                             units=(OBJECT_UNITS if instance.role == "object"
                                    or getattr(instance, "object_sprite", False)
                                    else PICKUP_UNITS)))
    return out


def wanted_frames(instances):
    """{(bank, frame, clut or None)} - every picture a level needs cut."""
    out = set()
    for instance in instances:
        art = getattr(instance, "art", None)
        if art is None:
            continue
        clut = art.clut if art.recolored else None
        out.update((art.bank, f.frame, clut) for f in art.frames)
    return out


def frame_at(art, tick):
    """Which step of a reward's animation is showing at `tick`.

    The steps carry their own durations, so this walks them rather than
    dividing: a ten-step crystal at two ticks a step comes round every
    twenty. A sequence that doesn't loop holds on its last step."""
    frames = art.frames if art is not None else ()
    if not frames:
        return None
    total = sum(max(f.ticks, 1) for f in frames)
    if art.loops:
        tick %= max(total, 1)
    elif tick >= total:
        return frames[-1]
    for step in frames:
        tick -= max(step.ticks, 1)
        if tick < 0:
            return step
    return frames[-1]

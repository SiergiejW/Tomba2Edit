"""Animated textures that move by UV rather than by palette.

The waterfalls and the lava do not cycle a CLUT (see clut_anim.py) -
their palette is a plain gradient. They animate by stepping the UVs
across a grid of frames drawn side by side on one texture page. AREA_22's
waterfall is 9 frames in a 3x3 grid of 64x64 cells; AREA_0E's lava is 12.

Confirmed against five PCSX savestates taken in the Purified Water
Temple: the game builds its own quads for the falling part, and every one
of them lands on a cell of exactly that grid. The frame each object is
showing is a byte in its object record (0x800EFD47 + n * 0xC4 there), so
which cell is up at a given moment is runtime state, not file data - the
grid itself is all that can be read off the disc.

DETECTION. A group of faces is animated this way when:

  * its UVs sit inside ONE cell and fill at least half of it, and
  * neighbouring cells hold the same picture again, moved.

"The same picture again" is a correlation of the cells' luminance, which
is what separates animation frames from the tiled scenery that shares a
page: a page of brickwork correlates around 0.5, the waterfall's frames
0.90 and the lava's 0.93. Palette-histogram overlap does not separate
them - unrelated tiles drawn from one palette score just as high.

Frames must also form one connected run, and there must be at least
MIN_FRAMES of them. Both guard the same thing: two lookalike cells with
other art between them are masonry, not an animation. Over the 33 level
meshes on the retail disc this flags the lava and nothing else, and it
recovers the waterfall's 3x3 grid exactly as the savestates show it.

The rate is NOT known - see gui/clut_animation.py.
"""
import numpy as np

from functions import psx_vram

PAGE = psx_vram.UV_WRAP

# Cell sizes to try, largest first. Both known cases are 64.
SIZES = (128, 64, 32)

# The UVs must span this much of the cell each way. Keeps a small decal
# that happens to sit in a busy page from being animated.
FILL = 0.5

# Luminance correlation for "the same picture, moved".
MATCH = 0.88

# A cell this empty is a hole in the page, not a frame.
MIN_BUSY = 0.25

# Fewer frames than this is not worth trusting: every 2- and 3-cell match
# on the disc turned out to be ordinary tiled art, while the two real
# animations have 9 and 12.
MIN_FRAMES = 4


class UVAnimation:
    """One face group's frame grid."""

    def __init__(self, clut, texpage, cell, frames):
        self.clut = clut
        self.texpage = texpage
        self.cell = cell
        self.frames = frames        # [(du, dv), ...] in texels, from the home cell

    def __len__(self):
        return len(self.frames)

    def offset_at(self, frame):
        """The frame's UV offset in texels."""
        return self.frames[frame % len(self.frames)]

    def atlas_offset_at(self, frame):
        """Same, as a fraction of the atlas the 3D views sample."""
        du, dv = self.offset_at(frame)
        return du / psx_vram.ATLAS_WIDTH, dv / psx_vram.ATLAS_HEIGHT


def page_texels(vram, texpage):
    """A texture page's 256x256 4bpp indices."""
    byte_x, row0 = psx_vram.page_origin(texpage)
    raw = np.frombuffer(bytes(vram), dtype=np.uint8).reshape(
        psx_vram.VRAM_ROWS, psx_vram.VRAM_STRIDE)
    rows = raw[row0:row0 + PAGE, byte_x:byte_x + psx_vram.PAGE_BYTES]
    out = np.empty((PAGE, PAGE), dtype=np.uint8)
    out[:, 0::2] = rows & 0x0F
    out[:, 1::2] = rows >> 4
    return out


def luminance(vram, clut_address):
    """The palette as 16 luminances, transparent entries at 0."""
    palette = psx_vram.read_palette(vram, clut_address, 16, transparent_zero=True)
    return np.array([0.0 if c[3] == 0 else 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
                     for c in palette], dtype=np.float64)


def home_cell(uvs, size):
    """(u, v) of the cell these UVs sit in, or None if they don't sit in
    one, or don't fill enough of it."""
    us = [u for u, _v in uvs]
    vs = [v for _u, v in uvs]
    u0, v0 = min(us) // size * size, min(vs) // size * size
    if max(us) >= u0 + size or max(vs) >= v0 + size:
        return None
    if (max(us) - min(us) + 1) < size * FILL or (max(vs) - min(vs) + 1) < size * FILL:
        return None
    return u0, v0


def _correlation(a, b):
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def _connected(cells, start):
    """The 4-connected run containing `start`."""
    if start not in cells:
        return set()
    seen, stack = {start}, [start]
    while stack:
        cu, cv = stack.pop()
        for step in ((cu + 1, cv), (cu - 1, cv), (cu, cv + 1), (cu, cv - 1)):
            if step in cells and step not in seen:
                seen.add(step)
                stack.append(step)
    return seen


def detect(texels, palette, uvs):
    """(cell size, [(du, dv), ...]) for one face group, or None."""
    picture = palette[texels]
    for size in SIZES:
        home = home_cell(uvs, size)
        if home is None:
            continue
        u0, v0 = home
        base = picture[v0:v0 + size, u0:u0 + size]
        if (texels[v0:v0 + size, u0:u0 + size] != 0).mean() < MIN_BUSY:
            continue
        across = PAGE // size
        alike = set()
        for cv in range(across):
            for cu in range(across):
                u, v = cu * size, cv * size
                if (texels[v:v + size, u:u + size] != 0).mean() < MIN_BUSY:
                    continue
                if _correlation(base, picture[v:v + size, u:u + size]) < MATCH:
                    continue
                alike.add((cu, cv))
        run = _connected(alike, (u0 // size, v0 // size))
        if len(run) >= MIN_FRAMES:
            frames = [(cu * size - u0, cv * size - v0)
                      for cv, cu in sorted((c[1], c[0]) for c in run)]
            return size, frames
    return None


def group_uvs(model_data):
    """{(clut address, texpage): [(u, v), ...]} in page texels.

    The viewers hold UVs as atlas fractions (see psx_vram.atlas_uv), so
    this turns them back into the whole texel numbers a packet carried."""
    out = {}
    coords = model_data.get("texture_coords") or ()
    info = model_data.get("texture_info") or ()
    for i, face in enumerate(model_data.get("faces") or ()):
        if i >= len(info):
            break
        page, clut = info[i][0], info[i][1]
        points = out.setdefault((clut, page), [])
        for vertex in face:
            if vertex >= len(coords):
                continue
            u, v = coords[vertex]
            points.append((round(u * psx_vram.ATLAS_WIDTH - 0.5) % PAGE,
                           round(v * psx_vram.ATLAS_HEIGHT - 0.5) % PAGE))
    return out


def find_animations(vram, model_data):
    """{clut address: UVAnimation} for everything in `model_data` that
    animates by UV. Empty when there is no VRAM or nothing qualifies."""
    if not vram or len(vram) < psx_vram.VRAM_SIZE or not model_data:
        return {}
    found, pages, palettes = {}, {}, {}
    for (clut, page), uvs in group_uvs(model_data).items():
        if page not in pages:
            pages[page] = page_texels(vram, page)
        if clut not in palettes:
            palettes[clut] = luminance(vram, clut)
        got = detect(pages[page], palettes[clut], uvs)
        if got:
            found[clut] = UVAnimation(clut, page, got[0], got[1])
    return found

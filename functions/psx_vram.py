"""PSX video memory conventions, shared by every format that samples it.

VRAM is one 1024x512 buffer of 16-bit halfwords - 0x800 bytes per row,
1MB in total - holding textures and palettes together, with no
distinction between them beyond where something points. On this disc it
comes out of an area's TOMBA2.IMG chunk (see
gui.vram_viewer.decode_vram_bytes).

Two things address into it, and both are stored the same way whether
they turn up in an SPRT sprite piece or a BGMP background:

    CLUT      a palette's position, packed as bits 0-5 = x / 16
              halfwords, bits 6-14 = y. Bit 15 is set on plenty of real
              entries and is not part of the address.
    texpage   a 64-halfword by 256-row tile of VRAM: bits 0-3 pick the
              column, bit 4 the half of VRAM it sits in. 64 halfwords
              is 256 texels at 4bpp and 128 at 8bpp, which is why the
              origin below is given in bytes rather than texels.
"""

VRAM_STRIDE = 0x800
VRAM_ROWS = 512
VRAM_SIZE = VRAM_STRIDE * VRAM_ROWS

PAGE_HALFWORDS = 64
PAGE_ROWS = 256
PAGE_BYTES = PAGE_HALFWORDS * 2

# U and V are single bytes, so a read running off the right or bottom of
# a texture page wraps back to 0 instead of clamping.
UV_WRAP = 256


class VRAMError(ValueError):
    """Raised when there's no usable VRAM to sample."""


def check_vram(vram_bytes):
    """Raise unless `vram_bytes` is a full VRAM buffer. Callers hand the
    message straight to the user, so it says what was wrong with it."""
    if vram_bytes is None or len(vram_bytes) < VRAM_SIZE:
        raise VRAMError(
            f"VRAM must be {VRAM_SIZE} bytes, got "
            f"{0 if vram_bytes is None else len(vram_bytes)}")
    return vram_bytes


def clut_index(clut):
    """The CLUT attribute with the stray bit 15 masked off."""
    return clut & 0x7FFF


def clut_xy(clut):
    """(x, y) of a palette in VRAM halfword coordinates."""
    value = clut_index(clut)
    return (value & 0x3F) * 16, (value >> 6) & 0x1FF


def clut_address(clut):
    """Byte address of a palette in VRAM."""
    x, y = clut_xy(clut)
    return x * 2 + y * VRAM_STRIDE


def clut_address_xy(address):
    """(x, y) in halfword coordinates of a palette at `address` - the
    inverse of clut_address(), for anything holding the address rather
    than the attribute it came from."""
    return (address % VRAM_STRIDE) // 2, address // VRAM_STRIDE


def page_origin(texpage):
    """(byte offset within a VRAM row, first row) of a texture page."""
    return (texpage & 0xF) * PAGE_BYTES, ((texpage >> 4) & 1) * PAGE_ROWS


# The 3D views hand the whole of VRAM to the GPU as one texture and let
# the UVs pick out of it: 16 texture pages across and two down, 256
# texels each at 4bpp (see gui.vram_viewer.vram_index_image).
ATLAS_COLUMNS = 16
ATLAS_ROWS = 2
ATLAS_PAGE = UV_WRAP
ATLAS_WIDTH = ATLAS_COLUMNS * ATLAS_PAGE
ATLAS_HEIGHT = ATLAS_ROWS * ATLAS_PAGE


# A code-drawn polygon's picture is a cutout - a chain link, a flame: art in
# its UV box and none in a ring this many texels round it, which dense level
# art never shows. See page_under.
CUTOUT_RING = 2


def _touching(boxes):
    """Boxes (u0, v0, u1, v1, inclusive) with the overlapping or adjoining
    ones joined - two halves of one chain link are one picture."""
    joined = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(joined):
            for j in range(i + 1, len(joined)):
                b = joined[j]
                if (a[0] <= b[2] + 1 and b[0] <= a[2] + 1
                        and a[1] <= b[3] + 1 and b[1] <= a[3] + 1):
                    joined[i] = [min(a[0], b[0]), min(a[1], b[1]),
                                 max(a[2], b[2]), max(a[3], b[3])]
                    del joined[j]
                    merged = True
                    break
            if merged:
                break
    return [tuple(b) for b in joined]


def _cutout(shown, box):
    """How much `box` of a page's shown-texel mask looks like a cutout: its
    share of shown texels, times the share of hidden ones in the ring."""
    u0, v0, u1, v1 = box
    inside = shown[v0:v1 + 1, u0:u1 + 1]
    around = shown[max(v0 - CUTOUT_RING, 0):v1 + CUTOUT_RING + 1,
                   max(u0 - CUTOUT_RING, 0):u1 + CUTOUT_RING + 1]
    ring = around.size - inside.size
    ring_shown = (int(around.sum()) - int(inside.sum())) / ring if ring else 0.0
    return float(inside.mean()) * (1.0 - ring_shown)


def page_under(vram, clut, boxes):
    """The page a packet naming only its CLUT is drawn from - the GPU keeps
    whatever page was set last, so it is read off the texels: the page where
    every one of the polygons' pictures is a cutout through the palette.
    None if no page shows one."""
    import numpy as np
    data = np.frombuffer(bytes(vram), dtype=np.uint8).reshape(VRAM_ROWS, VRAM_STRIDE)
    at = clut_address(clut)
    colours = np.frombuffer(bytes(vram[at:at + 32]), dtype="<u2") != 0
    pictures = _touching(boxes)
    best = None
    for page in range(ATLAS_COLUMNS * ATLAS_ROWS):
        byte_x, row0 = page_origin(page)
        rows = data[row0:row0 + PAGE_ROWS, byte_x:byte_x + PAGE_BYTES]
        index = np.empty((PAGE_ROWS, UV_WRAP), dtype=np.uint8)
        index[:, 0::2] = rows & 0x0F
        index[:, 1::2] = rows >> 4
        shown = colours[index]
        score = min(_cutout(shown, box) for box in pictures)
        if score > 0 and (best is None or score > best[0]):
            best = (score, page)
    return best[1] if best else None


def atlas_uv(u, v, texpage):
    """One packet's UV as a coordinate in that atlas, aimed at the
    MIDDLE of the texel rather than at its corner.

    The half texel is not cosmetic. A UV in a packet is a whole texel
    number, so u / ATLAS_WIDTH lands exactly on the boundary between
    texel u - 1 and texel u, and which side of it a fragment comes down
    on is settled by the last bit of the interpolator. That is fine
    right up until a face gives every one of its vertices the SAME UV -
    which is how this game paints a flat colour out of a texture page,
    and it does it constantly: 123 of the 292 faces on the Nishiki bird
    (AREA_08's 20-3FAC4.SMST) are one repeated texel. On those the whole
    polygon is that single sample, so the last bit of the interpolator
    swaps the colour of the entire face, and it swaps back and forth as
    the camera moves. The PSX had no such problem - it addresses texels
    as integers and never interpolates its way onto a boundary.

    Sampling the middle leaves half a texel of clearance on every side,
    which no rounding can cross, and it is the truer reading anyway:
    texel u means texel u, not the seam in front of it."""
    return (((texpage % ATLAS_COLUMNS) * ATLAS_PAGE + u + 0.5) / ATLAS_WIDTH,
            ((texpage // ATLAS_COLUMNS) * ATLAS_PAGE + v + 0.5) / ATLAS_HEIGHT)


def read_palette(vram, address, count=16, transparent_zero=True):
    """`count` colours from VRAM at `address`, as RGBA tuples.

    PSX colours are BGR555 - red in the low five bits - and a colour of
    0x0000 is its fully transparent one. `transparent_zero` is what
    decides whether that's honoured: a sprite piece needs it to have a
    cut-out shape, while a background is drawn opaque and wants the
    same colour as plain black.

    Reads past the end of VRAM come back black rather than raising - a
    256-colour palette on the last row does run off the end."""
    colors = []
    for i in range(count):
        at = address + i * 2
        value = vram[at] | (vram[at + 1] << 8) if at + 1 < len(vram) else 0
        colors.append((
            (value & 0x1F) * 8,
            ((value >> 5) & 0x1F) * 8,
            ((value >> 10) & 0x1F) * 8,
            0 if (transparent_zero and value == 0) else 255,
        ))
    return colors

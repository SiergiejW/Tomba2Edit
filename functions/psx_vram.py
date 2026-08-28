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

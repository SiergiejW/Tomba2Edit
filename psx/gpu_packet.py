"""Reading and writing one GPU packet's fields.

An SMST group and an MDAT group hold the same thing - a run of PSX draw
primitives, 36 bytes a triangle and 44 a quad - and both parsers already
measure the same field offsets from the same place (the draw code, at
packet + 3). What neither of them has is the other direction, which is
what an editor needs, so it lives here once rather than twice.

Everything is addressed by (blob, packet offset, kind). A parsed
polygon already carries that: `polygon["address"] - model["address"]`
is the offset, and `polygon["kind"]` is "tri" or "quad".

    UV          two bytes a corner, texel numbers 0-255 inside the
                packet's own 256x256 page
    vertex      three signed 16-bit words a corner, in the model's own
                space. Y is stored the way the hardware wants it; the
                parsers negate it on the way out for a Y-up viewer, and
                this module does the same so both ends agree.
    colour      FOUR BITS a channel, and two corners share one byte -
                see set_colors. 9 of 15 is neutral.
    page/clut   where in VRAM the face samples from. Neither is data
                inside the blob, which is why moving a model between
                areas breaks its textures.
"""
import struct

TRI_SIZE = 36
QUAD_SIZE = 44

# From the draw code at packet + 3, exactly as formats/models/smst_parser.py
# and formats/geometry/mdat.py measure them.
TRI_VERTS = ((17, 15, 13), (19, 23, 21), (29, 27, 25))
TRI_UVS = ((5, 6), (9, 10), (31, 32))
TRI_COLORS = ((-3, -2, -1, 0), (1, 2, 3, 0), (1, 2, 3, 1))

QUAD_VERTS = ((33, 31, 29), (21, 19, 17), (23, 27, 25), (35, 39, 37))
QUAD_UVS = ((13, 14), (5, 6), (9, 10), (15, 16))
QUAD_COLORS = ((1, 2, 3, 0), (-3, -2, -1, 0), (-3, -2, -1, 1), (1, 2, 3, 1))

LAYOUT = {
    "tri": (TRI_VERTS, TRI_UVS, TRI_COLORS, TRI_SIZE),
    "quad": (QUAD_VERTS, QUAD_UVS, QUAD_COLORS, QUAD_SIZE),
}

# What the parsers divide a colour nibble by, so that neutral reads as
# one. Kept here so an editor can show the same numbers.
COLOR_NEUTRAL = 9
COLOR_MAX = 15

PAGE_MASK = 0x1F
BLEND_SHIFT = 5


class PacketError(ValueError):
    """Raised when a packet can't be read or written as asked."""


def _layout(kind):
    try:
        return LAYOUT[kind]
    except KeyError:
        raise PacketError(f"{kind!r} is not a packet kind") from None


def corners(kind):
    """How many corners this kind of packet has."""
    return len(_layout(kind)[0])


def size(kind):
    """How many bytes one packet of this kind takes."""
    return _layout(kind)[3]


def _check(data, at, kind):
    end = at + size(kind)
    if at < 0 or end > len(data):
        raise PacketError(
            f"a {kind} at {at:#x} runs past the end of a {len(data)}-byte "
            "blob")
    return at + 3


# --------------------------------------------------------------------
# UVs
# --------------------------------------------------------------------

def read_uvs(data, at, kind):
    """[(u, v)] per corner, in packet order."""
    ind = _check(data, at, kind)
    return [(data[ind + ou], data[ind + ov]) for ou, ov in _layout(kind)[1]]


def write_uvs(data, at, kind, uvs):
    """Put [(u, v)] back. `data` is a bytearray and is changed in place."""
    ind = _check(data, at, kind)
    offsets = _layout(kind)[1]
    if len(uvs) != len(offsets):
        raise PacketError(
            f"a {kind} has {len(offsets)} corners, not {len(uvs)}")
    for (ou, ov), (u, v) in zip(offsets, uvs):
        data[ind + ou] = int(u) & 0xFF
        data[ind + ov] = int(v) & 0xFF


def flip_uvs(uvs, horizontal=True):
    """Mirror a face's texture, within the box its own UVs cover.

    Mirroring inside the bounding box rather than about the page's
    middle is what keeps the same texels: the face goes on showing the
    part of the page it always showed, the other way round. It also
    cannot push a UV out of range, which mirroring about anything else
    can."""
    if not uvs:
        return uvs
    us = [u for u, _v in uvs]
    vs = [v for _u, v in uvs]
    lo, hi = (min(us), max(us)) if horizontal else (min(vs), max(vs))
    total = lo + hi
    if horizontal:
        return [(total - u, v) for u, v in uvs]
    return [(u, total - v) for u, v in uvs]


def rotate_uvs(uvs, steps=1):
    """Turn the texture on the face by moving which corner takes which
    UV. Rotating the values instead would need a square box and would
    leave the page on anything else."""
    if not uvs:
        return uvs
    steps %= len(uvs)
    return uvs[steps:] + uvs[:steps]


def nudge_uvs(uvs, du, dv):
    """Slide the whole face across the page, clamped to it.

    Clamped as a block, not per corner: clamping each on its own would
    change the face's shape the moment one corner hit an edge."""
    if not uvs:
        return uvs
    us = [u for u, _v in uvs]
    vs = [v for _u, v in uvs]
    du = max(-min(us), min(du, 255 - max(us)))
    dv = max(-min(vs), min(dv, 255 - max(vs)))
    return [(u + du, v + dv) for u, v in uvs]


# --------------------------------------------------------------------
# Vertices
# --------------------------------------------------------------------

def read_vertices(data, at, kind, y_up=True):
    """[(x, y, z)] per corner. `y_up` negates Y the way the parsers do,
    so what comes out here matches what the viewer draws."""
    ind = _check(data, at, kind)
    out = []
    for ox, oy, oz in _layout(kind)[0]:
        x = struct.unpack_from("<h", data, ind + ox)[0]
        y = struct.unpack_from("<h", data, ind + oy)[0]
        z = struct.unpack_from("<h", data, ind + oz)[0]
        out.append((x, -y if y_up else y, z))
    return out


def write_vertices(data, at, kind, vertices, y_up=True):
    """Put [(x, y, z)] back, in place. Raises rather than wrapping a
    coordinate that will not fit in the signed word it has to go in."""
    ind = _check(data, at, kind)
    offsets = _layout(kind)[0]
    if len(vertices) != len(offsets):
        raise PacketError(
            f"a {kind} has {len(offsets)} corners, not {len(vertices)}")
    for (ox, oy, oz), (x, y, z) in zip(offsets, vertices):
        if y_up:
            y = -y
        for offset, value in ((ox, x), (oy, y), (oz, z)):
            value = int(round(value))
            if not -32768 <= value <= 32767:
                raise PacketError(
                    f"{value} does not fit in the signed 16-bit word a "
                    "coordinate is stored in")
            struct.pack_into("<h", data, ind + offset, value)


# --------------------------------------------------------------------
# Vertex colours
# --------------------------------------------------------------------
#
# Four bits a channel, and TWO CORNERS SHARE ONE BYTE - one takes its
# high nibble and the other its low. Writing a corner therefore has to
# leave the other corner's nibble alone, which is the whole reason this
# is not three plain byte writes.

def read_colors(data, at, kind):
    """[(r, g, b)] per corner, each channel 0-15 with 9 neutral."""
    ind = _check(data, at, kind)
    out = []
    for cr, cg, cb, low in _layout(kind)[2]:
        out.append(tuple(
            (data[ind + o] & 0x0F) if low else (data[ind + o] >> 4)
            for o in (cr, cg, cb)))
    return out


def write_colors(data, at, kind, colors):
    """Put [(r, g, b)] back, in place, keeping the nibble each byte's
    other corner owns."""
    ind = _check(data, at, kind)
    offsets = _layout(kind)[2]
    if len(colors) != len(offsets):
        raise PacketError(
            f"a {kind} has {len(offsets)} corners, not {len(colors)}")
    for (cr, cg, cb, low), rgb in zip(offsets, colors):
        for offset, value in zip((cr, cg, cb), rgb):
            value = max(0, min(COLOR_MAX, int(round(value))))
            byte = data[ind + offset]
            if low:
                data[ind + offset] = (byte & 0xF0) | value
            else:
                data[ind + offset] = (byte & 0x0F) | (value << 4)


# --------------------------------------------------------------------
# Where it samples from
# --------------------------------------------------------------------

def read_page(data, at, kind):
    """(texture page, blend mode) - the page in the low five bits and
    the blend above it."""
    ind = _check(data, at, kind)
    byte = data[ind + 11]
    return byte & PAGE_MASK, (byte >> BLEND_SHIFT) & 3


def write_page(data, at, kind, page, blend=None):
    """Point the face at another page, keeping its blend unless one is
    given. Every other bit of the byte is kept as it was."""
    ind = _check(data, at, kind)
    byte = data[ind + 11]
    if not 0 <= int(page) <= PAGE_MASK:
        raise PacketError(f"page {page} is not one of 0-{PAGE_MASK}")
    byte = (byte & ~PAGE_MASK) | int(page)
    if blend is not None:
        if not 0 <= int(blend) <= 3:
            raise PacketError(f"blend {blend} is not one of 0-3")
        byte = (byte & ~(3 << BLEND_SHIFT)) | (int(blend) << BLEND_SHIFT)
    data[ind + 11] = byte & 0xFF


def read_clut(data, at, kind):
    """The palette's byte address in VRAM.

    Bit 15 of the attribute is set on plenty of real packets and is not
    part of the address (see psx/vram) - it is read past here
    and put back untouched by write_clut."""
    ind = _check(data, at, kind)
    word = struct.unpack_from("<H", data, ind + 7)[0]
    x = (word & 0x3F) << 4
    y = (word >> 6) & 0x1FF
    return x * 2 + y * 0x800


def write_clut(data, at, kind, address):
    """Point the face at another palette, by VRAM byte address."""
    ind = _check(data, at, kind)
    if address < 0 or address % 32:
        raise PacketError(
            f"{address:#x} is not a palette address - a CLUT starts on a "
            "16-halfword boundary, so the address is a multiple of 32")
    y = address // 0x800
    x = (address % 0x800) // 2
    if not 0 <= y <= 0x1FF or not 0 <= x >> 4 <= 0x3F:
        raise PacketError(f"{address:#x} is not inside VRAM")
    word = struct.unpack_from("<H", data, ind + 7)[0]
    word = (word & 0x8000) | ((y & 0x1FF) << 6) | ((x >> 4) & 0x3F)
    struct.pack_into("<H", data, ind + 7, word)


def read_all(data, at, kind):
    """Everything about one packet, for an editor to show at once."""
    page, blend = read_page(data, at, kind)
    return {
        "kind": kind,
        "code": data[at + 3],
        "uvs": read_uvs(data, at, kind),
        "vertices": read_vertices(data, at, kind),
        "colors": read_colors(data, at, kind),
        "page": page,
        "blend": blend,
        "clut": read_clut(data, at, kind),
    }

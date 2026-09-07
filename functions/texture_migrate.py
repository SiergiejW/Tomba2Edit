"""Making a model's textures reachable from somewhere they are not.

THE PROBLEM

A packet does not carry a texture. It carries a texture PAGE number, a
palette address, and UVs - three ways of pointing into VRAM - and VRAM
is whatever the current area's IMG chunk last wrote there. So a model
only draws correctly in areas whose chunk happens to put the right
pixels at those addresses.

Most of Tomba's models are fine because their art is in AREA_01's chunk,
which is loaded once and stays resident. The ones that are not fine are
the ones whose art lives in a single level's chunk: swap such a model in
somewhere else and it samples whatever that level has at those addresses,
which is other art entirely.

WHAT THIS DOES

Given a model and the VRAM it was drawn against, it works out the
smallest rectangles of VRAM the model actually reads, finds somewhere
they can live that the destination will have loaded, copies the pixels
there, and rewrites the model's packets to point at the new place.

Nothing about it is specific to any one model. The same three steps -
measure, place, retarget - work for any SMST whose art is in the wrong
chunk.

WHAT CONSTRAINS THE PLACEMENT

    one page      A UV is a single byte within its texture page, so a
                  texture cannot straddle two pages. A destination has
                  to be a free rectangle inside one page.
    halfword      VRAM is addressed in 16-bit halfwords and 4bpp packs
                  four texels into each, so a move is only lossless if
                  it is a whole number of halfwords across - which makes
                  every horizontal shift a multiple of 4 texels. The
                  nibble a texel sits in never changes.
    16 halfwords  A palette's address packs x/16, so a relocated CLUT
                  has to start on a 16-halfword boundary.

See functions/vram_map.py for what counts as free, and why "free"
usually means "inside what AREA_01 owns".
"""
import struct

from functions import psx_vram

# The packet layout, the same numbers gui/smst/smst_parser.py reads with.
# Offsets are from the draw code (packet + 3).
TRI_SIZE = 36
QUAD_SIZE = 44
GROUP_HEADER = 16
TRI_UVS = ((5, 6), (9, 10), (31, 32))
QUAD_UVS = ((13, 14), (5, 6), (9, 10), (15, 16))
PAGE_BYTE = 11
CLUT_WORD = 7

TEXELS_PER_HALFWORD = 4


class MigrationError(ValueError):
    """Raised when a migration cannot be planned or applied."""


# --- reading a packet -------------------------------------------------

def _packets(blob):
    """(offset of the draw code, uv offsets) for every packet, in order.

    Walks the group table rather than the parsed model, so what gets
    rewritten is the file's own bytes."""
    zero, count = struct.unpack_from("<HH", blob, 0)
    if zero:
        raise MigrationError("this is not an SMST - its first word is not 0")
    offsets = struct.unpack_from(f"<{count}I", blob, 4)
    out = []
    for offset in offsets:
        tris, quads = struct.unpack_from("<HH", blob, offset)
        at = offset + GROUP_HEADER
        for _ in range(tris):
            out.append((at + 3, TRI_UVS))
            at += TRI_SIZE
        for _ in range(quads):
            out.append((at + 3, QUAD_UVS))
            at += QUAD_SIZE
    return out


def _clut_address(word):
    """Byte address in VRAM of a packet's palette."""
    value = word & 0x7FFF
    return ((value & 0x3F) * 16) * 2 + ((value >> 6) & 0x1FF) * psx_vram.VRAM_STRIDE


def _clut_word(address, original):
    """A CLUT attribute pointing at `address`, keeping the stray bit 15
    the original had - it is set on plenty of real entries and is not
    part of the address."""
    x, y = psx_vram.clut_address_xy(address)
    if x % 16:
        raise MigrationError(
            f"a palette must start on a 16-halfword boundary, and "
            f"0x{address:X} is at x={x}")
    value = (x // 16) | ((y & 0x1FF) << 6)
    return value | (original & 0x8000)


def survey(blob):
    """What a model samples: {page: (u0, v0, u1, v1)} and the set of
    palette addresses.

    The box is the tight one the model actually reads, not the whole
    page - moving 256x256 when the model uses 111x24 of it is the
    difference between fitting in the gaps and not fitting anywhere."""
    boxes = {}
    cluts = set()
    for ind, uvs in _packets(blob):
        page = blob[ind + PAGE_BYTE] & 0x1F
        word = struct.unpack_from("<H", blob, ind + CLUT_WORD)[0]
        cluts.add(_clut_address(word))
        for ou, ov in uvs:
            u, v = blob[ind + ou], blob[ind + ov]
            if page in boxes:
                u0, v0, u1, v1 = boxes[page]
                boxes[page] = (min(u0, u), min(v0, v), max(u1, u), max(v1, v))
            else:
                boxes[page] = (u, v, u, v)
    return boxes, sorted(cluts)


# --- planning ---------------------------------------------------------

def source_rect(page, box):
    """The halfword rectangle in VRAM a page's UV box covers.

    Rounded out to whole halfwords, since that is the unit a shard and a
    lossless move both work in."""
    u0, v0, u1, v1 = box
    column = (page % psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_HALFWORDS
    row = (page // psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_ROWS
    hx0 = u0 // TEXELS_PER_HALFWORD
    hx1 = u1 // TEXELS_PER_HALFWORD + 1
    return (column + hx0, row + v0, hx1 - hx0, v1 - v0 + 1)


class Plan:
    """Where everything is going, and what it costs."""

    def __init__(self):
        self.pages = {}      # source page -> (dest page, du texels, dv rows)
        self.rects = {}      # source page -> (src rect, dest rect) halfwords
        self.cluts = {}      # source address -> dest address
        self.notes = []

    def describe(self):
        lines = []
        for page, (dest, du, dv) in sorted(self.pages.items()):
            sx, sy, w, h = self.rects[page][0]
            lines.append(f"page {page} -> page {dest}: {w}x{h} halfwords "
                         f"({w * TEXELS_PER_HALFWORD}x{h} texels) from "
                         f"({sx}, {sy}), UVs shift by ({du:+}, {dv:+})")
        for old, new in sorted(self.cluts.items()):
            lines.append(f"palette 0x{old:X} -> 0x{new:X}")
        return "\n".join(lines + self.notes)


def already_there(blob, old_vram, dest_vram):
    """What the destination already has, byte for byte.

    Most of a model usually does not need moving. Tomba's own art is in
    AREA_01's chunk, which every area has loaded, so only the pages a
    single level owns are actually missing - and moving the rest would
    be work for nothing and space spent for nothing. Returns
    (pages already right, palettes already right)."""
    boxes, cluts = survey(blob)
    pages = {p for p, box in boxes.items()
             if cut(old_vram, source_rect(p, box))
             == cut(dest_vram, source_rect(p, box))}
    kept = {c for c in cluts
            if bytes(old_vram[c:c + 32]) == bytes(dest_vram[c:c + 32])}
    return pages, kept


def needed_for(blob, source_vram, area_vrams):
    """What is missing from AT LEAST ONE of the areas a model must work
    in, and so has to be relocated.

    Asking one area is what makes a migration look unnecessary when it
    is not: Tuxedo Tomba's page 24 is present in the outro's VRAM and in
    nothing else, so checking only there says "nothing to do" and the
    model still breaks everywhere. A page is left alone only when EVERY
    listed area already has it, byte for byte."""
    boxes, cluts = survey(blob)
    keep_pages = set(boxes)
    keep_cluts = set(cluts)
    for vram in area_vrams:
        pages, palettes = already_there(blob, source_vram, vram)
        keep_pages &= pages
        keep_cluts &= palettes
    return keep_pages, keep_cluts


def place(blob, page_dest, clut_dest, keep_pages=(), keep_cluts=()):
    """A Plan from destinations somebody has chosen.

    `page_dest` is {source page: (dest page, dest x, dest y)} in
    halfwords and `clut_dest` {source address: dest address}. This is
    what a hand-placed migration goes through; plan() is the same thing
    with the destinations searched for instead."""
    boxes, _cluts = survey(blob)
    out = Plan()
    for page, (dest_page, dx, dy) in sorted(page_dest.items()):
        if page in keep_pages:
            continue
        box = boxes[page]
        rect = source_rect(page, box)
        dest_column = (dest_page % psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_HALFWORDS
        dest_row = (dest_page // psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_ROWS
        du = ((dx - dest_column) - box[0] // TEXELS_PER_HALFWORD) \
            * TEXELS_PER_HALFWORD
        dv = (dy - dest_row) - box[1]
        for value, shift, what in ((box[0], du, "U"), (box[2], du, "U"),
                                   (box[1], dv, "V"), (box[3], dv, "V")):
            if not 0 <= value + shift <= 255:
                raise MigrationError(
                    f"page {page}: {what} would become {value + shift}, which "
                    f"is outside the 0-255 a UV byte can hold. Move it to a "
                    f"spot nearer the page's top-left corner.")
        out.pages[page] = (dest_page, du, dv)
        out.rects[page] = (rect, (dx, dy, rect[2], rect[3]))
    for old, new in sorted(clut_dest.items()):
        if old in keep_cluts:
            continue
        x, _y = psx_vram.clut_address_xy(new)
        if x % 16:
            raise MigrationError(
                f"a palette must start on a 16-halfword boundary, and "
                f"0x{new:X} is at x={x}")
        out.cluts[old] = new
    return out


def plan(blob, free, pages=None, clut_pages=None,
         keep_pages=(), keep_cluts=()):
    """Work out where a model's textures could go.

    `free` is the boolean halfword map from functions/vram_map. `pages`
    limits which texture pages may be used as destinations; the caller
    chooses, since which pages are really spare is a judgement about the
    disc rather than something the shard tables settle (see vram_map's
    header on the display buffers).

    `keep_pages` and `keep_cluts` are what the destination already has -
    normally already_there()'s answer. Anything in them is left pointing
    where it points, which is what keeps a migration down to the part
    that is actually missing."""
    from functions import vram_map

    boxes, cluts = survey(blob)
    boxes = {p: b for p, b in boxes.items() if p not in keep_pages}
    cluts = [c for c in cluts if c not in keep_cluts]
    taken = free.copy()
    out = Plan()
    if keep_pages:
        out.notes.append(
            f"left alone (the destination already has them): page(s) "
            f"{', '.join(str(p) for p in sorted(keep_pages))}")
    if keep_cluts:
        out.notes.append(
            f"palettes left alone: {len(keep_cluts)} of "
            f"{len(keep_cluts) + len(cluts)}")

    for page in sorted(boxes):
        box = boxes[page]
        rect = source_rect(page, box)
        _sx, _sy, w, h = rect
        spots = vram_map.free_rects(taken, w, h, pages=pages, limit=1)
        if not spots:
            raise MigrationError(
                f"page {page} needs a free {w}x{h} halfword block and there "
                f"is nowhere it fits")
        dx, dy, dest_page = spots[0]
        taken[dy:dy + h, dx:dx + w] = False

        dest_column = (dest_page % psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_HALFWORDS
        dest_row = (dest_page // psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_ROWS
        # The UV shift is what the packets get. Horizontal is in texels
        # and is always a multiple of 4, since both ends are halfwords.
        du = (dx - dest_column) * TEXELS_PER_HALFWORD \
            - (box[0] // TEXELS_PER_HALFWORD) * TEXELS_PER_HALFWORD
        dv = (dy - dest_row) - box[1]
        if not 0 <= box[0] + du <= 255 or not 0 <= box[2] + du <= 255:
            raise MigrationError(
                f"page {page}'s UVs would land outside a page after the move")
        if not 0 <= box[1] + dv <= 255 or not 0 <= box[3] + dv <= 255:
            raise MigrationError(
                f"page {page}'s V would land outside a page after the move")
        out.pages[page] = (dest_page, du, dv)
        out.rects[page] = (rect, (dx, dy, w, h))

    for address in cluts:
        spots = vram_map.free_rects(taken, 16, 1,
                                    pages=clut_pages if clut_pages is not None
                                    else pages, limit=1, align=16)
        spot = next(((x, y) for x, y, _p in spots), None)
        if spot is None:
            raise MigrationError(
                f"palette 0x{address:X} needs 16 free halfwords starting on "
                f"a 16-halfword boundary and there is nowhere it fits")
        dx, dy = spot
        taken[dy, dx:dx + 16] = False
        out.cluts[address] = dx * 2 + dy * psx_vram.VRAM_STRIDE
    return out


# --- applying ---------------------------------------------------------

def retarget(blob, plan_):
    """`blob` with every packet pointing at the plan's destinations."""
    out = bytearray(blob)
    for ind, uvs in _packets(out):
        page = out[ind + PAGE_BYTE] & 0x1F
        move = plan_.pages.get(page)
        if move is not None:
            dest, du, dv = move
            # Bits 5-6 are the blend mode and stay put; only the page
            # number in the low five bits changes.
            out[ind + PAGE_BYTE] = (out[ind + PAGE_BYTE] & 0xE0) | dest
            for ou, ov in uvs:
                out[ind + ou] = (out[ind + ou] + du) & 0xFF
                out[ind + ov] = (out[ind + ov] + dv) & 0xFF
        word = struct.unpack_from("<H", out, ind + CLUT_WORD)[0]
        new = plan_.cluts.get(_clut_address(word))
        if new is not None:
            struct.pack_into("<H", out, ind + CLUT_WORD,
                             _clut_word(new, word))
    return bytes(out)


def cut(vram, rect):
    """The bytes of a halfword rectangle of VRAM, row by row."""
    x, y, w, h = rect
    out = bytearray()
    for row in range(h):
        at = (y + row) * psx_vram.VRAM_STRIDE + x * 2
        out += bytes(vram[at:at + w * 2])
    return bytes(out)


def shards_for(plan_, vram):
    """[(x, y, w, h, pixels), ...] to add to a destination chunk.

    Pixels come out of the VRAM the model was drawn against, so what
    lands is exactly what it was sampling before."""
    out = []
    for page in sorted(plan_.pages):
        source, dest = plan_.rects[page]
        out.append((dest[0], dest[1], dest[2], dest[3], cut(vram, source)))
    for old, new in sorted(plan_.cluts.items()):
        x, y = psx_vram.clut_address_xy(new)
        out.append((x, y, 16, 1, bytes(vram[old:old + 32])))
    return out


def split_shards(shards, dest_vram):
    """(shards that must be written, shards already there) .

    Two models can share art. Tuxedo Tomba's black and red suits have
    byte-identical pixels on both the pages they sample and differ only
    in three palettes, so migrating the second one after the first
    should point at the first one's copy rather than write a second.

    A destination holding exactly the bytes we were going to put there
    is not a collision - it is the same texture, and the only work left
    is retargeting the UVs at it."""
    write, reuse = [], []
    for shard in shards:
        x, y, w, h, pixels = shard
        (reuse if cut(dest_vram, (x, y, w, h)) == pixels else write).append(shard)
    return write, reuse


def verify(before, after, old_vram, new_vram):
    """Check every packet still reads the same texels.

    The only test that means anything: a migration is correct when the
    picture is unchanged, and the way to know is to sample both models
    against their own VRAM and compare. Returns (checked, [mismatches])."""
    from functions import uv_anim

    pages_before = {}
    pages_after = {}
    checked = 0
    bad = []
    for (ind_b, uvs_b), (ind_a, uvs_a) in zip(_packets(before),
                                              _packets(after)):
        page_b = before[ind_b + PAGE_BYTE] & 0x1F
        page_a = after[ind_a + PAGE_BYTE] & 0x1F
        if page_b not in pages_before:
            pages_before[page_b] = uv_anim.page_texels(old_vram, page_b)
        if page_a not in pages_after:
            pages_after[page_a] = uv_anim.page_texels(new_vram, page_a)
        for (ou, ov), (nu, nv) in zip(uvs_b, uvs_a):
            u0, v0 = before[ind_b + ou], before[ind_b + ov]
            u1, v1 = after[ind_a + nu], after[ind_a + nv]
            checked += 1
            if pages_before[page_b][v0, u0] != pages_after[page_a][v1, u1]:
                bad.append((page_b, u0, v0, page_a, u1, v1))
    return checked, bad

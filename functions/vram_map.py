"""Who claims what in VRAM, and what is really spare.

THE SHARD TABLES ARE NOT THE WHOLE ANSWER

Every IMG chunk declares the rectangles it writes, so it is tempting to
call the union of them "used" and everything else "free". That is wrong,
and a savestate says so flatly. Checked against a PCSX state taken in
Town of the Fishermen:

  - x halfwords 0-319, all 512 rows, are full of pixels no chunk
    declares. That is the display - two 320x256 buffers stacked - and it
    is written every frame. Pages 0-4 and 16-20 are exactly that region,
    which is why nothing claims them and why they are the last place a
    texture may go.
  - The resident set is not AREA_01 alone. AREA_00, AREA_01 and AREA_02
    all matched the state at 100%, as did AREA_04, which is that level's
    own chunk. Everything else matched around 10%, which is chance.

So what is loaded at any moment is chunks 0, 1 and 2 plus the area you
are standing in, and the display region on top. Free space is what none
of those touch - and it is scarce: for AREA_04 it comes to about 12,600
halfwords, all of it in pages 24-31.

That is why free_for() takes the areas a texture has to survive in. A
model used in three levels needs space none of those three claims; a
model used everywhere needs space no level claims at all.

Coordinates here are HALFWORDS, which is what a shard header uses: x is
0-1023, y is 0-511. A 4bpp texel is a quarter of a halfword, so a texel
column is x * 4 - see texture_migrate for the conversion.
"""
import os
import struct

import numpy as np

from functions import img_codec, psx_vram

IDX_STRIDE = 0x800

# The chunks that are loaded whatever area you are in - measured from a
# savestate, not assumed. See the header.
ALWAYS_RESIDENT = (0, 1, 2)

# The display. Two 320x256 buffers stacked, so every row of the first 320
# halfword columns. Nothing declares it and everything overwrites it.
DISPLAY_COLUMNS = 320

# The texture pages that lie entirely inside the display region, and so
# can never hold a texture.
DISPLAY_PAGES = tuple(p for p in range(32)
                      if (p % psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_HALFWORDS
                      + psx_vram.PAGE_HALFWORDS <= DISPLAY_COLUMNS)

# The pages a texture can actually go in - everything the display does
# not own.
USABLE_PAGES = tuple(p for p in range(32) if p not in DISPLAY_PAGES)


def chunk_shards(idx_path, img_path):
    """{chunk: [(x, y, w, h, packed), ...]} for every area with a chunk."""
    count = os.path.getsize(idx_path) // IDX_STRIDE
    out = {}
    with open(idx_path, "rb") as idx, open(img_path, "rb") as img:
        data = img.read()
        for chunk in range(count):
            idx.seek(chunk * IDX_STRIDE)
            start, end = struct.unpack("<2I", idx.read(8))
            if end <= start:
                continue
            try:
                shards, _first = img_codec.read_chunk_header(data[start:end])
            except Exception:
                continue
            out[chunk] = list(shards)
    return out


def empty_map():
    return np.zeros((psx_vram.VRAM_ROWS, psx_vram.VRAM_STRIDE // 2), dtype=bool)


def display_map():
    """The region the game draws its frames in."""
    out = empty_map()
    out[:, :DISPLAY_COLUMNS] = True
    return out


def claims_of(shards, areas):
    """What the given chunks write, as a halfword map."""
    out = empty_map()
    for area in areas:
        for x, y, w, h, _packed in shards.get(area, ()):
            out[y:y + h, x:x + w] = True
    return out


def loaded_for(shards, area):
    """Everything in VRAM while you are standing in `area`: the always
    resident chunks, that area's own, and the display."""
    return claims_of(shards, tuple(ALWAYS_RESIDENT) + (area,)) | display_map()


def loaded_vram(shards, chunk_vram, area):
    """What VRAM holds while you stand in `area`.

    The always-resident chunks laid down first, then that area's own on
    top, each writing only the rectangles it declares - which is what
    the console does and what makes this different from merging whole
    decompressed buffers. `chunk_vram(area)` supplies one chunk's
    decompressed megabyte, or None."""
    out = bytearray(psx_vram.VRAM_SIZE)
    for chunk in tuple(ALWAYS_RESIDENT) + (area,):
        source = chunk_vram(chunk)
        if source is None:
            continue
        for x, y, w, h, _packed in shards.get(chunk, ()):
            for row in range(y, y + h):
                at = row * psx_vram.VRAM_STRIDE + x * 2
                out[at:at + w * 2] = source[at:at + w * 2]
    return out


def owners_of(shards, rect, areas=None):
    """{area: halfwords} for every chunk that writes into `rect`.

    Which matters more than it looks, because of load order. The
    resident chunks go down first and the area's own chunk goes on top,
    so a shard added to AREA_01 that lands where AREA_07 also writes is
    not overwriting AREA_07 - AREA_07 overwrites IT, every time you walk
    in there. Overwriting somebody else's art only works if your shard
    is in the same chunk as theirs, where being added last is what
    settles it.

    Nothing here decides that; it just says whose space it is so the
    caller can."""
    x0, y0, w, h = rect
    out = {}
    for area, rects in shards.items():
        if areas is not None and area not in areas:
            continue
        count = 0
        for sx, sy, sw, sh, _packed in rects:
            across = min(x0 + w, sx + sw) - max(x0, sx)
            down = min(y0 + h, sy + sh) - max(y0, sy)
            if across > 0 and down > 0:
                count += across * down
        if count:
            out[area] = count
    return out


def anywhere_but_display():
    """Everywhere a texture could physically go, ignoring who owns it.

    The relaxed map a deliberate overwrite is planned against. The
    display is still excluded - it is redrawn every frame, so nothing
    put there survives a single one."""
    return ~display_map()


def free_for(shards, areas, occupied=None):
    """Halfwords nothing writes in ANY of `areas`.

    A texture placed here survives in every one of them, which is the
    only sense in which space is free: there is no such thing as free
    VRAM in general, only VRAM that the areas you care about leave
    alone.

    `occupied` is what savestates say is in use (see
    functions/state_vram.py) and is the only way to account for what the
    game uploads at runtime rather than out of the IMG - palettes and
    sprite art that no shard table mentions. Without one, this is an
    optimistic answer and should be treated as such."""
    used = display_map() | claims_of(shards, ALWAYS_RESIDENT)
    for area in areas:
        used |= claims_of(shards, (area,))
    if occupied is not None:
        used |= occupied
    return ~used


def claim_maps(idx_path, img_path):
    """(resident, level, free) over the whole disc.

    `free` here is what NO area writes and the display does not own, so
    a texture placed in it survives everywhere. That is the strictest
    reading and the one a model meant for every level needs."""
    shards = chunk_shards(idx_path, img_path)
    resident = claims_of(shards, ALWAYS_RESIDENT)
    level = claims_of(shards, [c for c in shards
                               if c not in ALWAYS_RESIDENT])
    return resident, level, free_for(shards, list(shards))


def free_rects(free, width, height, pages=None, limit=64, align=1):
    """Where a `width` x `height` halfword block fits in `free`.

    Returns [(x, y, page), ...]. `pages` limits the search to those
    texture pages, which is how a caller keeps off the display buffers
    without this module having to decide which those are.

    A block must sit inside ONE page: a UV is a byte within its page, so
    a texture spanning two pages cannot be addressed by a packet at all.

    `align` constrains x to a multiple of it, measured from VRAM's own
    left edge rather than from the page's. A palette needs align=16,
    because its address packs x/16 and anything else is unaddressable.
    """
    out = []
    for page in sorted(pages if pages is not None else range(32)):
        column = (page % psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_HALFWORDS
        first_row = (page // psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_ROWS
        if width > psx_vram.PAGE_HALFWORDS or height > psx_vram.PAGE_ROWS:
            continue
        block = free[first_row:first_row + psx_vram.PAGE_ROWS,
                     column:column + psx_vram.PAGE_HALFWORDS]
        rows, columns = block.shape
        # The page's own left edge is a multiple of 64 halfwords, so an
        # x aligned within the page is aligned in VRAM too.
        step = max(1, align)
        for y in range(rows - height + 1):
            band = block[y:y + height, :].all(axis=0)
            x = 0
            while x <= columns - width:
                if x % step == 0 and band[x:x + width].all():
                    out.append((column + x, first_row + y, page))
                    if len(out) >= limit:
                        return out
                    x += max(width, step)
                else:
                    x += 1
    return out


def describe(idx_path, img_path):
    """A short report on what is claimed and what is not."""
    resident, level, free = claim_maps(idx_path, img_path)
    lines = [
        f"resident (AREA_{RESIDENT_AREA:02X}): {int(resident.sum())} halfwords",
        f"level chunks: {int(level.sum())} halfwords",
        f"claimed by both: {int((resident & level).sum())} halfwords",
        f"free: {int(free.sum())} halfwords",
    ]
    for page in range(32):
        column = (page % psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_HALFWORDS
        row = (page // psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_ROWS
        block = free[row:row + psx_vram.PAGE_ROWS,
                     column:column + psx_vram.PAGE_HALFWORDS]
        if block.any():
            lines.append(f"  page {page}: {int(block.sum())} free of "
                         f"{block.size}")
    return "\n".join(lines)

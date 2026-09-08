"""Painting a sprite piece, and putting it back in VRAM.

A piece is a rectangle of 4-bit indices somewhere in a texture page, and
the sprite you see is that rectangle with its flips applied (see
gui/sprt/sprt_render.py, whose sampling this is the exact mirror of).
Editing one is therefore two jobs kept apart on purpose:

    read      the piece's texels, flips undone, so what is painted on is
              what the sprite shows rather than what the file stores.
    write     those indices back into VRAM, flips redone, nibble by
              nibble - and then the changed halfwords out as IMG shards.

WHAT MAKES THIS FIDDLY

    nibbles   Two texels share a byte, so writing one is a read-modify-
              write and the odd/even column decides which half.
    wrapping  A UV is a single byte and wraps at 256. Thirty-four pieces
              on the retail disc cross that edge, so a piece's source
              rectangle can be in two halves at opposite ends of its
              page, and both reading and writing have to follow it
              round.
    flips     A mirrored piece stores its texels the other way about.
              Undoing the flip to paint and redoing it to save is what
              keeps the brush where the cursor is.

WHAT IS SHARED

Nothing here knows about Qt or about the IMG. It changes a VRAM buffer;
`changed_rects()` then says which halfword rectangles differ from the
original, and those go to functions/img_writer.py as ordinary shards.
That is the same path the texture migration uses, so a painted sprite
and a moved texture reach the disc the same way.
"""
import struct

from functions.psx_vram import (
    UV_WRAP, VRAM_STRIDE, clut_address_xy, read_palette)


def source_columns(piece):
    """The texel columns a piece covers, in page space, in order.

    A list rather than a range because of the wrap: a piece starting at
    u0=240 and 32 wide covers 240..255 and then 0..15."""
    return [(piece.u0 + i) % UV_WRAP for i in range(piece.ww)]


def source_rows(piece):
    return [(piece.v0 + i) % UV_WRAP for i in range(piece.hh)]


def read_texel(vram, piece, u, v):
    """One texel of a page, by its page-space column and row."""
    base = (piece.page_row0 + v) * VRAM_STRIDE + piece.page_byte_x
    byte = vram[base + (u >> 1)]
    return (byte & 0x0F) if not (u & 1) else (byte >> 4)


def write_texel(vram, piece, u, v, value):
    """Set one texel. `vram` must be mutable."""
    base = (piece.page_row0 + v) * VRAM_STRIDE + piece.page_byte_x
    at = base + (u >> 1)
    byte = vram[at]
    if u & 1:
        vram[at] = (byte & 0x0F) | ((value & 0x0F) << 4)
    else:
        vram[at] = (byte & 0xF0) | (value & 0x0F)


def piece_indices(vram, piece):
    """The piece as hh rows of ww indices, flips undone.

    Row 0 column 0 is the piece's top-left as drawn, which is what makes
    a click on the canvas mean the texel under it."""
    columns = source_columns(piece)
    rows = source_rows(piece)
    if piece.hflip:
        columns = list(reversed(columns))
    if piece.vflip:
        rows = list(reversed(rows))
    return [[read_texel(vram, piece, u, v) for u in columns] for v in rows]


def apply_indices(vram, piece, indices):
    """Write hh rows of ww indices back, flips redone.

    The inverse of piece_indices() down to the same column and row
    lists, so a read followed by a write changes nothing."""
    columns = source_columns(piece)
    rows = source_rows(piece)
    if piece.hflip:
        columns = list(reversed(columns))
    if piece.vflip:
        rows = list(reversed(rows))
    for y, row in enumerate(indices):
        if y >= len(rows):
            break
        for x, value in enumerate(row):
            if x >= len(columns):
                break
            write_texel(vram, piece, columns[x], rows[y], value)


def palette(vram, piece, transparent_zero=True):
    """The piece's 16 (or 256) colours."""
    return read_palette(vram, piece.clut_address,
                        256 if piece.is_8bpp else 16,
                        transparent_zero=transparent_zero)


def read_palette_at(vram, address, eight_bit=False, transparent_zero=True):
    """A palette by address rather than by piece - what previewing
    another CLUT needs before anything has been committed."""
    return read_palette(vram, address, 256 if eight_bit else 16,
                        transparent_zero=transparent_zero)


def nearest_index(colour, colours):
    """Which palette entry an imported pixel becomes.

    Transparent goes to whichever entry the hardware draws as nothing,
    because that is what a hole in a sprite IS - and index 0 is only
    conventionally that one, so it is looked for rather than assumed."""
    if colour[3] < 8:
        for index, entry in enumerate(colours):
            if not entry[3]:
                return index
        return 0
    best, at = None, 0
    for index, entry in enumerate(colours):
        if not entry[3]:
            continue
        gap = sum(abs(a - b) for a, b in zip(colour[:3], entry[:3]))
        if gap == 0:
            return index
        if best is None or gap < best:
            best, at = gap, index
    return at


def indices_from_image(image, colours, width, height):
    """An RGBA PIL image as indices, matched to a palette."""
    rgba = image.convert("RGBA")
    if rgba.size != (width, height):
        rgba = rgba.resize((width, height))
    pixels = list(rgba.getdata())
    return [[nearest_index(pixels[y * width + x], colours)
             for x in range(width)]
            for y in range(height)]


def image_from_indices(indices, colours):
    """Indices as an RGBA PIL image, for export."""
    from PIL import Image
    height = len(indices)
    width = len(indices[0]) if height else 0
    out = bytearray()
    for row in indices:
        for value in row:
            out += bytes(colours[value] if value < len(colours)
                         else (255, 0, 255, 255))
    return Image.frombytes("RGBA", (max(width, 1), max(height, 1)),
                           bytes(out) or bytes(4))


def clut_attribute(address, original=0):
    """A CLUT attribute pointing at `address`, keeping the stray bit 15.

    Bit 15 is set on roughly a third of the retail disc's pieces and is
    not part of the address (see the parser's header), so it is carried
    across rather than dropped - a rewritten piece should differ from
    the one it replaces in the address and nothing else."""
    x, y = clut_address_xy(address)
    if x % 16:
        raise ValueError(
            f"a palette starts on a 16-halfword boundary, and 0x{address:X} "
            f"is at x={x}")
    return ((x // 16) | ((y & 0x1FF) << 6)) | (original & 0x8000)


def set_piece_clut(blob, piece, address):
    """`blob` with one piece pointed at a different palette.

    The SPRT blob, not VRAM: this changes which colours the game draws
    the piece through, and no texel moves. Same art, different palette
    is how the disc itself does Tuxedo Tomba's black and red suits."""
    out = bytearray(blob)
    attribute = clut_attribute(address, piece.clut)
    # clut is the third field of the 16-byte record, two bytes in.
    struct.pack_into("<H", out, piece.offset + 2, attribute)
    return bytes(out)


def clut_pool(sprt):
    """Every palette the pieces of one bank draw through, with how many
    pieces use each.

    The useful pool to offer: a palette already in this bank is one the
    area is known to have loaded, which is most of what makes a recolour
    work at all."""
    counts = {}
    for sprite in sprt.sprites:
        for piece in sprite.pieces:
            counts[piece.clut_address] = counts.get(piece.clut_address, 0) + 1
    return sorted(counts.items())


def changed_rects(original, edited):
    """The halfword rectangles that differ, as [(x, y, w, h), ...].

    Found per row, then stacked: a run at the same columns on the next
    row down grows the rectangle instead of starting another. That
    matters more than it looks, because a shard is padded out to a 0x800
    sector - so a 22x22 piece left as 22 one-row shards costs 45KB and
    eats an eighth of a chunk's shard table, where the one rectangle it
    really is costs 2KB and one slot.

    A piece that wraps its page still comes out as two rectangles, one
    at each end, rather than one spanning everything between."""
    runs = []
    rows = len(original) // VRAM_STRIDE
    for y in range(rows):
        row = y * VRAM_STRIDE
        here = []
        x = 0
        while x < VRAM_STRIDE // 2:
            at = row + x * 2
            if original[at:at + 2] == edited[at:at + 2]:
                x += 1
                continue
            start = x
            while x < VRAM_STRIDE // 2:
                at = row + x * 2
                if original[at:at + 2] == edited[at:at + 2]:
                    break
                x += 1
            here.append((start, x - start))
        runs.append(here)

    out = []
    open_rects = {}                     # (x, w) -> (y, height so far)
    for y in range(rows + 1):
        here = dict.fromkeys(runs[y]) if y < rows else {}
        for key, (top, height) in list(open_rects.items()):
            if key in here:
                open_rects[key] = (top, height + 1)
            else:
                out.append((key[0], top, key[1], height))
                del open_rects[key]
        for key in here:
            open_rects.setdefault(key, (y, 1))
    return sorted(out, key=lambda r: (r[1], r[0]))


def shards_for(edited, rects):
    """[(x, y, w, h, pixels), ...] ready for functions/img_writer.py."""
    out = []
    for x, y, w, h in rects:
        pixels = bytearray()
        for row in range(h):
            at = (y + row) * VRAM_STRIDE + x * 2
            pixels += bytes(edited[at:at + w * 2])
        out.append((x, y, w, h, bytes(pixels)))
    return out

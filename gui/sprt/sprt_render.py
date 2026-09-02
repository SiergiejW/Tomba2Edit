"""Turns SPRT pieces into images by sampling PSX VRAM.

Every viewer that draws a sprite builds it here, so what they draw
always comes from one place. Nothing in this module touches Qt - it
returns PIL images and plain numbers, and the caller displays them.

A piece's texels are 4- or 8-bit indices into a CLUT that lives in the
same VRAM as the art, so both come out of one buffer - see
functions.psx_vram for how the two are addressed.
"""
import colorsys

from PIL import Image, ImageDraw

from functions.psx_vram import UV_WRAP, VRAM_STRIDE, check_vram, read_palette

# Spaces consecutive placeholder colours far apart on the wheel - same
# trick, and the same reason, as scld_render.entry_color.
GOLDEN_RATIO_CONJUGATE = 0.6180339887498949


def piece_color(index, saturation=0.6, value=0.95):
    """A piece's stand-in colour, used when there's no VRAM to sample."""
    r, g, b = colorsys.hsv_to_rgb((index * GOLDEN_RATIO_CONJUGATE) % 1.0,
                                  saturation, value)
    return int(r * 255), int(g * 255), int(b * 255)


def _sample_row(vram, base, u0, count, eight_bit):
    """`count` texel indices starting at u0 in the row at `base`,
    wrapping at 256 (see UV_WRAP)."""
    out = []
    u, left = u0 % UV_WRAP, count
    while left > 0:
        run = min(left, UV_WRAP - u)
        if eight_bit:
            out.extend(vram[base + u:base + u + run])
        else:
            nibbles = []
            for byte in vram[base + (u >> 1):base + ((u + run + 1) >> 1)]:
                nibbles.append(byte & 0x0F)
                nibbles.append(byte >> 4)
            start = u & 1
            out.extend(nibbles[start:start + run])
        u, left = 0, left - run
    # An 8bpp piece against the far edge of VRAM can read past the end,
    # which slicing quietly shortens rather than raising - pad it back
    # so the row is always `count` wide.
    if len(out) < count:
        out.extend([0] * (count - len(out)))
    return out


class VRAMTextures:
    """One area's decompressed VRAM, with the palettes it has been asked
    for so far kept around - a sprite bank reuses a handful of CLUTs
    across hundreds of pieces."""

    def __init__(self, vram_bytes):
        self.vram = check_vram(vram_bytes)
        self._palettes = {}

    def palette(self, piece):
        """This piece's CLUT as RGBA tuples. A colour of 0x0000 is the
        PSX's fully transparent one; everything else is opaque here,
        since whether a piece blends at all is a property of the draw
        call rather than of the palette (see
        SpritePiece.semi_transparency)."""
        key = (piece.clut_index, piece.is_8bpp)
        cached = self._palettes.get(key)
        if cached is not None:
            return cached

        colors = read_palette(self.vram, piece.clut_address,
                              256 if piece.is_8bpp else 16)
        self._palettes[key] = colors
        return colors

    def piece_image(self, piece):
        """The piece as an RGBA image, ww x hh, flips applied."""
        if piece.ww == 0 or piece.hh == 0:
            return Image.new("RGBA", (max(piece.ww, 1), max(piece.hh, 1)), (0, 0, 0, 0))

        palette = self.palette(piece)
        pal_bytes = [bytes(c) for c in palette]
        fallback = bytes((255, 0, 255, 255))  # index past the end of its own CLUT
        rows = bytearray()
        for y in range(piece.hh):
            v = (piece.v0 + (piece.hh - 1 - y if piece.vflip else y)) % UV_WRAP
            base = (piece.page_row0 + v) * VRAM_STRIDE + piece.page_byte_x
            indices = _sample_row(self.vram, base, piece.u0, piece.ww, piece.is_8bpp)
            if piece.hflip:
                indices.reverse()
            for i in indices:
                rows += pal_bytes[i] if i < len(pal_bytes) else fallback
        return Image.frombytes("RGBA", (piece.ww, piece.hh), bytes(rows))


def placeholder_piece_image(piece):
    """Stand-in for a piece with no VRAM behind it: a flat block in the
    piece's own colour, so the layout is still readable."""
    r, g, b = piece_color(piece.index)
    im = Image.new("RGBA", (max(piece.ww, 1), max(piece.hh, 1)), (r, g, b, 70))
    ImageDraw.Draw(im).rectangle((0, 0, im.width - 1, im.height - 1),
                                 outline=(r, g, b, 200))
    return im


def render_sprite(sprite, textures, margin=0):
    """One sprite, drawn back to front.

    Pieces are listed front-to-back in the file, so this walks them in
    reverse: the last piece lands first and the first piece ends up on
    top. With flat, coplanar quads that order is the only thing deciding
    what covers what.

    `textures` may be None, which draws placeholder blocks instead.

    Returns (image, ox, oy) - ox/oy being where the sprite's origin sits
    inside the image."""
    x0, y0, x1, y1 = sprite.extent()
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    canvas = Image.new("RGBA", (max(x1 - x0, 1), max(y1 - y0, 1)), (0, 0, 0, 0))
    for piece in reversed(sprite.pieces):
        im = (textures.piece_image(piece) if textures is not None
              else placeholder_piece_image(piece))
        canvas.alpha_composite(im, (piece.pX - x0, piece.pY - y0))
    return canvas, -x0, -y0


def sheet_cell(sprites):
    """(cell_w, cell_h, ox, oy) big enough to hold every sprite in the
    bank with its origin at the same spot in each cell - so a bank reads
    as one set of registered poses rather than a row of loose crops."""
    left = top = right = bottom = 1
    for sprite in sprites:
        x0, y0, x1, y1 = sprite.extent()
        left, top = max(left, -x0), max(top, -y0)
        right, bottom = max(right, x1), max(bottom, y1)
    return left + right, top + bottom, left, top


def render_sheet(sprites, textures, columns):
    """Every sprite of the bank on one grid, each centred on its origin.

    Returns (image, cell_w, cell_h, ox, oy) - the cell metrics let the
    caller map a click back to a sprite and draw per-cell overlays."""
    cell_w, cell_h, ox, oy = sheet_cell(sprites)
    columns = max(1, columns)
    rows = (len(sprites) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cell_w, max(rows, 1) * cell_h), (0, 0, 0, 0))
    for sprite in sprites:
        im, sx, sy = render_sprite(sprite, textures)
        cx = (sprite.index % columns) * cell_w + ox - sx
        cy = (sprite.index // columns) * cell_h + oy - sy
        sheet.alpha_composite(im, (cx, cy))
    return sheet, cell_w, cell_h, ox, oy


# The cell border drawn into an exported sheet. Faint on purpose: it is
# there to say where one sprite stops and the next starts, on artwork
# that is often only a few pixels across, without being mistaken for
# part of the art.
SHEET_BORDER = (255, 255, 255, 64)


def draw_cell_borders(sheet, cell_w, cell_h, columns, count,
                      color=SHEET_BORDER):
    """A copy of `sheet` with a line around every occupied cell.

    The viewer draws its grid as a screen overlay, which is not in the
    image the exporter saves - so a sheet written out has nothing
    separating one card from the next. This puts the same lines into the
    pixels.

    Only cells that hold a sprite are boxed. The last row is usually
    part empty, and ruling lines across the gap would suggest cards that
    are not there."""
    lined = sheet.copy()
    pen = ImageDraw.Draw(lined)
    columns = max(1, columns)
    for index in range(count):
        col, row = index % columns, index // columns
        x, y = col * cell_w, row * cell_h
        pen.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline=color)
    return lined

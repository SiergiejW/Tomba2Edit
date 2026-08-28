"""Draws BGMP backgrounds by sampling PSX VRAM.

Nothing here touches Qt - it returns PIL images, and the caller
displays them. See functions.psx_vram for how the texture page and the
palettes are addressed.

The work is done a palette at a time, not a tile at a time: the whole
256x256 page is coloured once per palette the map actually uses (there
are rarely more than six), and every tile is then a crop out of one of
those. Doing it per tile would recolour the same texels thousands of
times over.
"""
import colorsys

from PIL import Image, ImageDraw

from functions.psx_vram import (
    PAGE_BYTES, VRAM_STRIDE, check_vram, page_origin, read_palette)
from gui.bgmp.bgmp_parser import PAGE_TILES, PALETTE_STRIDE, TILE

PAGE_SIZE = 256

# Same golden-ratio hue trick as scld_render.entry_color and
# sprt_render.piece_color - consecutive palettes land far apart on the
# wheel instead of fading into each other.
GOLDEN_RATIO_CONJUGATE = 0.6180339887498949


def palette_color(index, saturation=0.55, value=0.9):
    """A palette's stand-in colour, used when there's no VRAM to sample."""
    r, g, b = colorsys.hsv_to_rgb((index * GOLDEN_RATIO_CONJUGATE) % 1.0,
                                  saturation, value)
    return int(r * 255), int(g * 255), int(b * 255)


class BackgroundTextures:
    """One area's decompressed VRAM, with the tile page's texel indices
    read once and each palette's coloured copy of it kept as it's asked
    for."""

    def __init__(self, vram_bytes, transparent_zero=False):
        self.vram = check_vram(vram_bytes)
        # Backgrounds are drawn opaque, so colour 0 is plain black
        # rather than a hole - unlike a sprite piece, which needs the
        # cut-out. Switchable because seeing which tiles are "empty" is
        # exactly what you want when reading a map.
        self.transparent_zero = transparent_zero
        self._indices = {}
        self._pages = {}

    def indices(self, texpage):
        """The page's 65536 4bpp texel indices, row by row."""
        cached = self._indices.get(texpage)
        if cached is not None:
            return cached
        byte_x, row0 = page_origin(texpage)
        out = []
        for row in range(PAGE_SIZE):
            base = (row0 + row) * VRAM_STRIDE + byte_x
            for byte in self.vram[base:base + PAGE_BYTES]:
                out.append(byte & 0x0F)
                out.append(byte >> 4)
        self._indices[texpage] = out
        return out

    def palette(self, bgmp, index):
        """Palette `index` of this file's stack, as RGBA tuples."""
        return read_palette(self.vram,
                            bgmp.clut_address + index * PALETTE_STRIDE,
                            transparent_zero=self.transparent_zero)

    def page_image(self, bgmp, palette_index):
        """The whole 256x256 tile page, coloured with one palette."""
        key = (bgmp.texpage, bgmp.clut, palette_index, self.transparent_zero)
        cached = self._pages.get(key)
        if cached is not None:
            return cached
        colors = self.palette(bgmp, palette_index)
        image = Image.new("RGBA", (PAGE_SIZE, PAGE_SIZE))
        image.putdata([colors[i] for i in self.indices(bgmp.texpage)])
        self._pages[key] = image
        return image


def render_background(bgmp, textures):
    """The whole background as one image, tile by tile.

    `textures` may be None, which draws each tile as a flat block in its
    palette's colour instead - the map's shape stays readable without
    the art."""
    width, height = bgmp.pixel_size
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if textures is None:
        draw = ImageDraw.Draw(canvas)
        for tile in bgmp.tiles:
            x, y = tile.col * TILE, tile.row * TILE
            r, g, b = palette_color(tile.palette)
            draw.rectangle((x, y, x + TILE - 1, y + TILE - 1),
                           fill=(r, g, b, 90), outline=(r, g, b, 190))
        return canvas

    for tile in bgmp.tiles:
        page = textures.page_image(bgmp, tile.palette)
        cell = page.crop((tile.page_x, tile.page_y,
                          tile.page_x + TILE, tile.page_y + TILE))
        canvas.paste(cell, (tile.col * TILE, tile.row * TILE))
    return canvas


def render_page(bgmp, textures, palette_index):
    """The source texture page on its own, coloured with one palette -
    the sheet every tile of the background is cut from."""
    if textures is None:
        image = Image.new("RGBA", (PAGE_SIZE, PAGE_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        r, g, b = palette_color(palette_index)
        for cell in range(PAGE_TILES * PAGE_TILES):
            x, y = (cell % PAGE_TILES) * TILE, (cell // PAGE_TILES) * TILE
            draw.rectangle((x, y, x + TILE - 1, y + TILE - 1), outline=(r, g, b, 150))
        return image
    return textures.page_image(bgmp, palette_index)


def palette_swatch(bgmp, textures, palette_index, cell=12):
    """A 16-colour strip for one palette, for the palette list."""
    image = Image.new("RGBA", (cell * 16, cell), (0, 0, 0, 255))
    if textures is None:
        r, g, b = palette_color(palette_index)
        ImageDraw.Draw(image).rectangle((0, 0, image.width - 1, cell - 1),
                                        fill=(r, g, b, 255))
        return image
    draw = ImageDraw.Draw(image)
    for i, color in enumerate(textures.palette(bgmp, palette_index)):
        draw.rectangle((i * cell, 0, i * cell + cell - 1, cell - 1),
                       fill=color[:3] + (255,))
    return image

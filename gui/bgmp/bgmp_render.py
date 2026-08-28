"""Draws BGMP backgrounds by sampling PSX VRAM.

Nothing here touches Qt - it returns PIL images, and the caller
displays them. See functions.psx_vram for how the texture page and the
palettes are addressed.

The work is done a palette at a time, not a tile at a time: the whole
256x256 page is coloured once per palette the map actually uses (there
are rarely more than six), and every tile is then a crop out of one of
those. Doing it per tile would recolour the same texels thousands of
times over.

Two things a background needs that its file doesn't carry:

`page_y_offset` shifts where the tile grid sits inside the texture
page. AREA_04's tiles are authored 8 rows down its page - its sky tile
reads half cloud without the shift, and comes out flat with it - while
every other background on the disc wants 0. Nothing in the header says
so (the unknown fields are constant across all sixteen files), and the
VRAM is placed correctly - every shard decompresses to exactly its
declared size and lands on a page boundary - so the shift has to come
from the game itself, most likely a PSX texture window, whose Y offset
is counted in the same 8-pixel units. detect_page_y_offset() recovers
it from the artwork.

`phase` rotates a palette's cycling run. Several palettes hold a closed,
evenly stepped ring of colours - AREA_04's palette 5, which covers the
1188 sea tiles, steps its red channel 0B-10-15-1A-1F-1A-15-10 and back,
with blue and green pinned at maximum. A ring like that has one purpose:
the game rotates it and the water glitters. How fast, and in which
direction, is in the code rather than the data - rendering with a phase
shows what the cycling looks like, not what the game literally does.
"""
import colorsys
from collections import Counter

from PIL import Image, ImageDraw

from functions.psx_vram import (
    PAGE_BYTES, VRAM_STRIDE, check_vram, page_origin, read_palette)
from gui.bgmp.bgmp_parser import PAGE_TILES, PALETTE_STRIDE, TILE

PAGE_SIZE = 256

# A cycling run has to be this long, step by this much at most, and keep
# its steps this even, before it's called one. Tuned against the disc:
# it accepts the sea palettes (8 entries, every step exactly 40) and the
# two other closed rings, and passes over ordinary gradients, which are
# just as smooth but don't join up end to end.
MIN_CYCLE_LEN = 6
MAX_CYCLE_STEP = 64
MAX_CYCLE_SPREAD = 8

# Same golden-ratio hue trick as scld_render.entry_color and
# sprt_render.piece_color - consecutive palettes land far apart on the
# wheel instead of fading into each other.
GOLDEN_RATIO_CONJUGATE = 0.6180339887498949


def palette_color(index, saturation=0.55, value=0.9):
    """A palette's stand-in colour, used when there's no VRAM to sample."""
    r, g, b = colorsys.hsv_to_rgb((index * GOLDEN_RATIO_CONJUGATE) % 1.0,
                                  saturation, value)
    return int(r * 255), int(g * 255), int(b * 255)


def detect_palette_cycle(colors):
    """The palette's cycling run as (start, length), or None.

    Looks for the longest run of consecutive entries whose colours form
    a closed loop - each step small, all steps about equal, and the last
    entry stepping back to the first by the same amount. An ordinary
    gradient fails the last part: its ends are far apart.

    Runs are allowed to revisit colours; the sea palettes ramp up and
    back down again, which is a ring of eight entries holding five
    distinct colours."""
    for start in range(len(colors)):
        for length in range(len(colors), MIN_CYCLE_LEN - 1, -1):
            if start + length > len(colors):
                continue
            run = colors[start:start + length]
            steps = [_color_step(run[i], run[(i + 1) % length])
                     for i in range(length)]
            if (min(steps) == 0 or max(steps) > MAX_CYCLE_STEP
                    or max(steps) - min(steps) > MAX_CYCLE_SPREAD):
                continue
            return start, length
    return None


def _color_step(a, b):
    return max(abs(a[i] - b[i]) for i in range(3))


def _lcm(a, b):
    from math import gcd
    return a * b // gcd(a, b)


def detect_page_y_offset(bgmp, textures):
    """Where this background's tile grid sits inside its texture page.

    Uses the tile the map leans on most - always a big field of sky or
    water, and always a single flat colour. Whichever offset makes that
    tile uniform is the one the artwork was cut on. Offset 0 wins any
    tie, and anything ambiguous falls back to it, so this only ever
    moves a background that plainly needs moving."""
    if textures is None or not bgmp.tiles:
        return 0
    raw = Counter(tile.raw for tile in bgmp.tiles).most_common(1)[0][0]
    page_x, page_y, palette = (raw & 0x0F) * TILE, raw & 0xF0, raw >> 8
    colors = textures.palette(bgmp, palette)
    indices = textures.indices(bgmp.texpage)

    uniform = []
    for offset in range(TILE):
        seen = {colors[indices[((page_y + offset + y) % PAGE_SIZE) * PAGE_SIZE
                               + page_x + x]][:3]
                for y in range(TILE) for x in range(TILE)}
        if len(seen) == 1:
            uniform.append(offset)
    if not uniform or 0 in uniform:
        return 0
    return uniform[0] if len(uniform) == 1 else 0


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
        self._palettes = {}
        self._cycles = {}

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

    def palette(self, bgmp, index, phase=0):
        """Palette `index` of this file's stack, as RGBA tuples.

        `phase` rotates the palette's cycling run, if it has one; every
        other entry stays put. See this module's docstring for what the
        phase does and doesn't claim."""
        colors = self._base_palette(bgmp, index)
        if not phase:
            return colors
        cycle = self.cycle(bgmp, index)
        if cycle is None:
            return colors
        start, length = cycle
        shift = phase % length
        run = colors[start:start + length]
        return colors[:start] + run[shift:] + run[:shift] + colors[start + length:]

    def _base_palette(self, bgmp, index):
        key = (bgmp.clut, index)
        colors = self._palettes.get(key)
        if colors is None:
            colors = self._palettes[key] = read_palette(
                self.vram, bgmp.clut_address + index * PALETTE_STRIDE,
                transparent_zero=self.transparent_zero)
        return colors

    def cycle(self, bgmp, index):
        """This palette's cycling run as (start, length), or None."""
        key = (bgmp.clut, index)
        if key not in self._cycles:
            self._cycles[key] = detect_palette_cycle(
                [c[:3] for c in self._base_palette(bgmp, index)])
        return self._cycles[key]

    def cycle_length(self, bgmp, palettes):
        """How many phases it takes for every cycling palette in
        `palettes` to come back round together - the length of one loop
        of the whole background. 1 when nothing cycles."""
        total = 1
        for index in palettes:
            cycle = self.cycle(bgmp, index)
            if cycle is not None:
                total = _lcm(total, cycle[1])
        return total

    def is_blank_palette(self, bgmp, index):
        """Whether a palette is entirely black - which is what an area's
        VRAM looks like where nothing was ever loaded into it. Three
        backgrounds on the retail disc point at palettes their own area
        never fills in, and come out black; saying so beats letting it
        look like a decoding failure."""
        return all(color[:3] == (0, 0, 0) for color in self.palette(bgmp, index))

    def page_image(self, bgmp, palette_index, phase=0):
        """The whole 256x256 tile page, coloured with one palette."""
        key = (bgmp.texpage, bgmp.clut, palette_index, self.transparent_zero, phase)
        cached = self._pages.get(key)
        if cached is not None:
            return cached
        colors = self.palette(bgmp, palette_index, phase)
        image = Image.new("RGBA", (PAGE_SIZE, PAGE_SIZE))
        image.putdata([colors[i] for i in self.indices(bgmp.texpage)])
        self._pages[key] = image
        return image


def render_background(bgmp, textures, page_y_offset=0, phase=0):
    """The whole background as one image, tile by tile.

    `page_y_offset` shifts every tile's source rows inside the page and
    `phase` rotates the cycling palettes - see this module's docstring
    for where each comes from.

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
        page = textures.page_image(bgmp, tile.palette, phase)
        top = (tile.page_y + page_y_offset) % PAGE_SIZE
        if top + TILE <= PAGE_SIZE:
            cell = page.crop((tile.page_x, top, tile.page_x + TILE, top + TILE))
        else:
            # An offset tile against the bottom of the page wraps back
            # to the top, the same way the hardware's V does.
            cell = Image.new("RGBA", (TILE, TILE))
            split = PAGE_SIZE - top
            cell.paste(page.crop((tile.page_x, top, tile.page_x + TILE, PAGE_SIZE)), (0, 0))
            cell.paste(page.crop((tile.page_x, 0, tile.page_x + TILE, TILE - split)),
                       (0, split))
        canvas.paste(cell, (tile.col * TILE, tile.row * TILE))
    return canvas


def render_page(bgmp, textures, palette_index, page_y_offset=0, phase=0):
    """The source texture page on its own, coloured with one palette -
    the sheet every tile of the background is cut from.

    Rolled up by `page_y_offset` so the cells line up with a 16-pixel
    grid drawn over it, and so a cell's position here is the one the
    tile map names."""
    if textures is None:
        image = Image.new("RGBA", (PAGE_SIZE, PAGE_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        r, g, b = palette_color(palette_index)
        for cell in range(PAGE_TILES * PAGE_TILES):
            x, y = (cell % PAGE_TILES) * TILE, (cell // PAGE_TILES) * TILE
            draw.rectangle((x, y, x + TILE - 1, y + TILE - 1), outline=(r, g, b, 150))
        return image
    page = textures.page_image(bgmp, palette_index, phase)
    offset = page_y_offset % PAGE_SIZE
    if not offset:
        return page
    rolled = Image.new("RGBA", (PAGE_SIZE, PAGE_SIZE))
    rolled.paste(page.crop((0, offset, PAGE_SIZE, PAGE_SIZE)), (0, 0))
    rolled.paste(page.crop((0, 0, PAGE_SIZE, offset)), (0, PAGE_SIZE - offset))
    return rolled


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

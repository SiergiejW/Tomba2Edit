"""The font and menu page in TOMBA2.IMG, out to a PNG and back.

Chunk 0 of the IMG is one 256x256 4bpp page holding every glyph the game
draws as text, plus the menu words that are artwork rather than text. It
sits at the same place in VRAM on every build - x 3840, y 256 in texels -
and the builds differ only in how tall the shard covering it is, and in
what the artwork says.

    rows   0.. 39   system font, 8x8 - two of its rows fit in one row of
                    the dialogue grid
    rows  40..167   dialogue font, 8x16, 32 glyphs a row, `code = row *
                    32 + column` (see tombadict). Where it starts moves
                    between builds; find_glyph_top() locates it.
    rows 168..223   menu artwork - Items, Event, Full!! and the rest are
                    sprites of their own widths, not grid cells, so a
                    word can span several 8-pixel columns
    rows 224..255   CLUTs, 16 RGB555 entries each, four to a VRAM row

Only the dialogue font is a grid. The system font is the same grid at
half the height, and the artwork is not on a grid at all.

A page is exported as an indexed PNG, one pixel per texel, its value
being the 4-bit index the game looks up in whatever CLUT the drawing
code has selected. Editing keeps those indices: the palette in the PNG
is only there to make the file legible in an image editor.
"""
import os
import struct

from PIL import Image

from functions.img_codec import compress, decompress, read_chunk_header

# Where the page lives, and how big it is.
FONT_CHUNK = 0
PAGE_X = 3840          # in 4bpp texels
PAGE_Y = 256
PAGE_W = 256
PAGE_H = 256

# The dialogue frame, and the palette it is drawn with. Three 18x16
# pieces sit side by side in the page: a shallow top, a section with the
# two side edges, and a deeper top. The box is built from these.
FRAME_Y = 136
FRAME_X = 176
FRAME_PIECE_W = 18
FRAME_PIECE_H = 16
FRAME_PIECES = (11, 35, 59)      # x offsets from FRAME_X
FRAME_CLUT = (255, 2)            # row, slot

# The system font, and where the CLUTs sit. Rows are page rows.
SYSTEM_TOP = 0
SYSTEM_H = 8
CLUT_TOP = 224
CLUT_ENTRIES = 16

# The dialogue font's grid - see the module docstring. Where its first
# row starts moves between builds, because the European ones carry two
# extra rows of accented capitals and lowercase.
GLYPH_TOP = 40
GLYPH_W = 8
GLYPH_H = 16
GLYPH_COLS = 32

# Row 1 of the grid is "@ABC..." on every Latin build, so a page is
# placed by finding it rather than by trusting a table of offsets.
_ANCHOR_CODE = 0x20
_ANCHOR_ROW = 1

# A 16-step ramp, so an exported page reads as an image rather than a
# black square. Index 0 is the transparent one every glyph sits on.
_PALETTE = []
for _i in range(16):
    _v = 0 if _i == 0 else 40 + _i * 14
    _PALETTE += [_v, _v, _v]
_PALETTE += [0] * (768 - len(_PALETTE))


class PageSpec:
    """One 4bpp page in the IMG, and where its chunk lands in VRAM.

    The font page is not the only one a translation has to touch. The
    title screen's menu - New Game, Load Game, Options, Start Game - is
    artwork in chunk 2, not glyphs, so it cannot be reached through the
    font page at all, and a build that leaves it in English is not
    translated. The two pages differ only in geometry and in where their
    palettes sit, so the reading and writing below is shared and this
    says which one is meant.

    `clut_top` is a row within the page, not a VRAM row: chunk 2 keeps
    its palettes in a shard of their own further down VRAM, which lands
    at the bottom of the page rect the same way chunk 0's do.
    """

    def __init__(self, chunk, x, y, width, height, clut_top, name):
        self.chunk = chunk
        self.x = x                  # in 4bpp texels
        self.y = y
        self.width = width
        self.height = height
        self.clut_top = clut_top
        self.name = name

    def __repr__(self):
        return "PageSpec(%r, chunk %d)" % (self.name, self.chunk)


# The two pages worth editing. FONTS is what every existing caller
# means, so it stays the default everywhere.
FONTS = PageSpec(FONT_CHUNK, PAGE_X, PAGE_Y, PAGE_W, PAGE_H, CLUT_TOP,
                 "Fonts")
# Chunk 2, AREA_02. The art is 1024x240 at VRAM y256 and the palettes
# are a shard of their own, 1024x5 at y507 - room for 80, though only
# five hold anything. Taking the page down to y512 puts those palettes
# at rows 251-255 of it, which is the same shape as the font page.
TITLE = PageSpec(2, 2560, 256, 1024, 256, 251, "Main Title")


class FontPageError(ValueError):
    """Raised when a page can't be read or written."""


def _chunk_bounds(cd_folder, chunk=FONT_CHUNK):
    idx_path = os.path.join(cd_folder, "TOMBA2.IDX")
    with open(idx_path, "rb") as idx:
        idx.seek(chunk * 0x800)
        img_start, img_end = struct.unpack("<2I", idx.read(8))
    return img_start, img_end


def _shards_covering_page(shards, spec=None):
    """Which shards of the chunk fall inside the page, with where each
    one lands in it.

    Shard x and width are in 16-bit words; at 4bpp a word is four
    texels."""
    spec = spec or FONTS
    out = []
    for i, (x, y, w, h, packed) in enumerate(shards):
        tx = x * 4
        tw = w * 4
        if tx + tw <= spec.x or tx >= spec.x + spec.width:
            continue
        if y + h <= spec.y or y >= spec.y + spec.height:
            continue
        out.append((i, (x, y, w, h, packed), tx - spec.x, y - spec.y, tw, h))
    return out


def read_page(cd_folder, spec=None):
    """A page as a list of rows of 4-bit indices."""
    spec = spec or FONTS
    img_start, img_end = _chunk_bounds(cd_folder, spec.chunk)
    with open(os.path.join(cd_folder, "TOMBA2.IMG"), "rb") as img:
        img.seek(img_start)
        data = img.read(img_end - img_start)
    shards, pos = read_chunk_header(data)

    page = [[0] * spec.width for _ in range(spec.height)]
    offsets = {}
    at = pos
    for i, (x, y, w, h, packed) in enumerate(shards):
        offsets[i] = at
        at += packed

    for i, (x, y, w, h, packed), px, py, tw, th in _shards_covering_page(
            shards, spec):
        pixels = decompress(data, offsets[i], packed, w)
        stride = w * 2
        for row in range(th):
            if not 0 <= py + row < spec.height:
                continue
            base = row * stride
            for byte in range(stride):
                if base + byte >= len(pixels):
                    break
                value = pixels[base + byte]
                for half in range(2):
                    col = px + byte * 2 + half
                    if 0 <= col < spec.width:
                        page[py + row][col] = (value >> (4 * half)) & 0x0F
    return page


def read_cluts(cd_folder, spec=None):
    """The palettes stored in the page, as lists of 16 (r, g, b, a).

    They are 16-bit words rather than 4-bit texels, so they are read
    from VRAM directly. PSX colour is RGB555 with the top bit marking
    semi-transparency; 0 is transparent."""
    spec = spec or FONTS
    return read_cluts_from(read_page(cd_folder, spec), spec)


def read_cluts_from(page, spec=None):
    """The palettes of a page already in hand.

    Editing one - which replacing an 8bpp picture does, since its
    palette lives in the page like everything else - has to re-read them
    without going back to the disc, or the view keeps drawing through
    the colours that were just written over."""
    spec = spec or FONTS
    out = []
    for row in range(spec.clut_top, spec.height):
        words = []
        line = page[row]
        # Four texels make one 16-bit word, low nibble first.
        for w in range(spec.width // 4):
            i = w * 4
            words.append(line[i] | (line[i + 1] << 4)
                         | (line[i + 2] << 8) | (line[i + 3] << 12))
        for c in range(0, len(words), CLUT_ENTRIES):
            block = words[c:c + CLUT_ENTRIES]
            if len(block) < CLUT_ENTRIES or not any(block):
                continue
            out.append((row, c // CLUT_ENTRIES,
                        [_rgb555(v) for v in block]))
    return out


def _rgb555(word):
    """One PSX colour word as (r, g, b, a), 0-255.

    The top bit marks the colour semi-transparent, which in the mode the
    dialogue box uses means half the colour over half the background, so
    it comes back at alpha 128. The frame's interior greys are the only
    place in the page that sets it; the glyph palettes are all opaque."""
    if word == 0:
        return (0, 0, 0, 0)
    r = (word & 0x1F) * 255 // 31
    g = ((word >> 5) & 0x1F) * 255 // 31
    b = ((word >> 10) & 0x1F) * 255 // 31
    return (r, g, b, 128 if word & 0x8000 else 255)


def export_png(cd_folder, path, clut=None):
    """Write the page to `path` as an indexed PNG.

    With `clut` - a list of 16 (r, g, b, a) from read_cluts() - the PNG
    carries that palette, so the page looks the way the game draws it.
    Without one it gets a grey ramp. Either way the pixels are the same
    4-bit indices, so a file exported with a palette imports back
    unchanged."""
    page = read_page(cd_folder)
    image = Image.new("P", (PAGE_W, PAGE_H))
    if clut:
        flat = []
        for r, g, b, _a in clut:
            flat += [r, g, b]
        flat += [0] * (768 - len(flat))
        image.putpalette(flat)
    else:
        image.putpalette(_PALETTE)
    image.putdata([v for row in page for v in row])
    image.save(path)
    return path


def import_png(cd_folder, path):
    """Read an indexed PNG back into the page and rewrite the IMG."""
    image = Image.open(path)
    if image.size != (PAGE_W, PAGE_H):
        raise FontPageError(
            f"{os.path.basename(path)} is {image.size[0]}x{image.size[1]}, "
            f"and a font page is {PAGE_W}x{PAGE_H}.")
    if image.mode != "P":
        raise FontPageError(
            f"{os.path.basename(path)} is mode {image.mode}; the page has to "
            "stay indexed (mode P), since its pixels are CLUT indices.")
    return write_page(cd_folder, list(image.getdata()),
                      what=os.path.basename(path))


def write_page(cd_folder, page, what="the page", spec=None):
    """Write a page back into the IMG. `page` is 256 rows of 4-bit
    indices, or one flat sequence of PAGE_W * PAGE_H of them.

    Only the shards the page covers are re-compressed; every other shard
    keeps its original bytes. A shard is refused if its new form needs
    more room than it was given, which leaves the disc untouched."""
    if page and isinstance(page[0], (list, tuple)):
        page = [v for row in page for v in row]
    else:
        page = list(page)
    spec = spec or FONTS
    if len(page) != spec.width * spec.height:
        raise FontPageError(
            f"{what} has {len(page)} pixels; {spec.name} is "
            f"{spec.width * spec.height}.")
    if max(page) > 15:
        raise FontPageError(
            f"{what} uses index {max(page)}; the page is 4bpp, so only "
            "0-15 exist.")

    img_start, img_end = _chunk_bounds(cd_folder, spec.chunk)
    img_path = os.path.join(cd_folder, "TOMBA2.IMG")
    with open(img_path, "rb") as img:
        img.seek(img_start)
        data = bytearray(img.read(img_end - img_start))
    shards, pos = read_chunk_header(data)

    offsets = {}
    at = pos
    for i, (x, y, w, h, packed) in enumerate(shards):
        offsets[i] = at
        at += packed

    rebuilt = {}
    for i, (x, y, w, h, packed), px, py, tw, th in _shards_covering_page(
            shards, spec):
        pixels = bytearray(decompress(data, offsets[i], packed, w))
        stride = w * 2
        for row in range(th):
            if not 0 <= py + row < spec.height:
                continue
            base = row * stride
            for byte in range(stride):
                if base + byte >= len(pixels):
                    break
                lo_col = px + byte * 2
                hi_col = lo_col + 1
                value = pixels[base + byte]
                if 0 <= lo_col < spec.width:
                    value = ((value & 0xF0)
                             | page[(py + row) * spec.width + lo_col])
                if 0 <= hi_col < spec.width:
                    value = ((value & 0x0F)
                             | (page[(py + row) * spec.width + hi_col] << 4))
                pixels[base + byte] = value
        packed_new = compress(bytes(pixels), w)
        if len(packed_new) > packed:
            raise FontPageError(
                f"Edited shard {i} needs {len(packed_new)} bytes and has "
                f"{packed}. Nothing was written. Glyphs compress better "
                "the more flat runs they have, so a simpler shape, or a "
                "smaller edit, will fit.")
        rebuilt[i] = packed_new

    for i, packed_new in rebuilt.items():
        _x, _y, _w, _h, packed = shards[i]
        start = offsets[i]
        data[start:start + packed] = packed_new + bytes(packed - len(packed_new))

    with open(img_path, "r+b") as img:
        img.seek(img_start)
        img.write(bytes(data))
    return len(rebuilt)


def find_glyph_top(page, reference=None):
    """Where this page's dialogue grid starts.

    Slides the reference build's "@ABC..." row over the page and takes
    the best fit, so a build that carries extra rows above the grid is
    placed by what it draws rather than by a hard-coded offset. Returns
    (top, how well it matched, 0.0-1.0).

    `reference` is the row to look for, as returned by glyph_row(); with
    none given the caller gets GLYPH_TOP back unexamined."""
    if reference is None:
        return GLYPH_TOP, 0.0
    best_top, best_score = GLYPH_TOP, -1.0
    for top in range(PAGE_H - GLYPH_H):
        same = 0
        for y in range(GLYPH_H):
            row = page[top + y]
            same += sum(1 for x in range(PAGE_W) if row[x] == reference[y][x])
        score = same / (GLYPH_H * PAGE_W)
        if score > best_score:
            best_score, best_top = score, top
    return best_top - _ANCHOR_ROW * GLYPH_H, best_score


def glyph_row(page, row, top=GLYPH_TOP):
    """One whole row of the grid, for use as a reference."""
    y = top + row * GLYPH_H
    return tuple(tuple(page[y + i]) for i in range(GLYPH_H))


def read_frame(cd_folder, clut=FRAME_CLUT):
    """The frame's three pieces as lists of (r, g, b, a) rows.

    In page order the pieces are the top, the sides and the bottom; the
    game composes its boxes out of these rather than storing a finished
    border. Which piece is which is told by where its corners are inset,
    and confirmed by the interior gradient, which runs unbroken from the
    top piece through the sides into the bottom one.

    The art is the same for every box the game draws - only the palette
    changes, so `clut` picks the context: (255, 2) is the grey dialogue
    box, (255, 3) the pink one item notices use, (254, 3) the pale
    yellow of the control hints."""
    page = read_page(cd_folder)
    palette = None
    for row, slot, pal in read_cluts(cd_folder):
        if (row, slot) == clut:
            palette = pal
            break
    if palette is None:
        return []
    out = []
    for x0 in FRAME_PIECES:
        piece = []
        for y in range(FRAME_PIECE_H):
            line = []
            for x in range(FRAME_PIECE_W):
                index = page[FRAME_Y + y][FRAME_X + x0 + x]
                line.append(palette[index])
            piece.append(line)
        out.append(piece)
    return out


def glyph_box(code, top=GLYPH_TOP):
    """(left, top, right, bottom) of one dialogue-font glyph in the page,
    or None for a code the grid doesn't reach."""
    row, col = divmod(code, GLYPH_COLS)
    top = top + row * GLYPH_H
    if top + GLYPH_H > PAGE_H:
        return None
    left = col * GLYPH_W
    return (left, top, left + GLYPH_W, top + GLYPH_H)


def get_glyph(page, code, top=GLYPH_TOP):
    """One glyph's pixels as a list of GLYPH_H rows, or None if the code
    is off the grid."""
    box = glyph_box(code, top)
    if box is None:
        return None
    left, gtop, right, bottom = box
    return [list(page[y][left:right]) for y in range(gtop, bottom)]


def set_glyph(page, code, cell, top=GLYPH_TOP):
    """Put `cell` - GLYPH_H rows of GLYPH_W indices - at `code`.

    Edits `page` in place and returns it, so several glyphs can be set
    before one write_page() call sends them all to the disc together."""
    box = glyph_box(code, top)
    if box is None:
        raise FontPageError(f"Code {code} is not on the grid.")
    left, gtop, right, bottom = box
    if len(cell) != GLYPH_H or any(len(r) != GLYPH_W for r in cell):
        raise FontPageError(
            f"A glyph is {GLYPH_W}x{GLYPH_H}; got "
            f"{len(cell[0]) if cell else 0}x{len(cell)}.")
    for y, row in enumerate(cell):
        page[gtop + y][left:right] = list(row)
    return page


def glyphs(page, top=GLYPH_TOP):
    """{code: tuple of rows} for every glyph the dialogue grid covers."""
    out = {}
    code = 0
    while True:
        box = glyph_box(code, top)
        if box is None:
            return out
        left, top, right, bottom = box
        out[code] = tuple(tuple(page[y][left:right]) for y in range(top, bottom))
        code += 1

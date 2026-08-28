"""BGMP (background map) file parser.

Thanks to vervalkon (Tomba Club) - see examples/BGMP2png.py, which this
follows.

A background is not stored as a picture. It is a grid of 16x16 tiles,
each one a two-byte reference into a single 256x256 texture page in
VRAM: which of that page's 16x16 cells to take, and which of the 16
palettes stacked below the file's CLUT to draw it with. Everything a
background is made of already sits in the area's VRAM - the file only
says how to arrange it.

Layout of one BGMP blob:
    header (0x14 bytes):
        texpage  : u16 - PSX texture page holding the tiles; > 0xF puts
                         it in the lower half of VRAM (see
                         functions.psx_vram.page_origin)
        clut     : u16 - where the FIRST palette sits, in PSX CLUT
                         format. The other 15 follow it straight down
                         VRAM, one per row - so palette n is at
                         clut_address + n * 0x800.
        clut_x   : u16 - the CLUT's x, in halfwords
        clut_y   : u16 - the CLUT's y. Both are just `clut` spelled
                         out, and agree with it on every file on the
                         disc; kept as a cross-check, not read.
        unk1     : u16 - 0 throughout
        unk2     : u16 - 0 throughout
        width    : u8  - map size in tiles
        height   : u8
        map_size : u16 - the tile map's length in bytes, always
                         width * height * 2
        unk3     : u16 - 0 throughout
        unk4     : u16 - 2 throughout

    tile map [0x14 .. 0x14 + map_size): u16 per tile, left to right and
        top to bottom:
            bits 0-3   column of the source cell in the texture page
            bits 4-7   row of it (so the low byte is the cell number in
                       a 16x16 grid, and x/y are it times 16)
            bits 8-15  which palette to draw the cell with

    trailer [.. + 4): 0x00FF, then a u16 whose high byte is a palette
        index within the range the map uses. Unidentified.

    Anything past that is slack in the file's slot rather than part of
    the map - one background on the retail disc has 1720 bytes of it,
    holding an older map and a fragment of a build script.
"""
import struct
from dataclasses import dataclass, field

from functions import psx_vram

HEADER = struct.Struct("<HHHHHHBBHHH")
HEADER_SIZE = 0x14
TRAILER_SIZE = 4

# Tiles are 16x16, and a 256x256 texture page holds a 16x16 grid of them.
TILE = 16
PAGE_TILES = 16

# The palettes are stacked one per VRAM row below the file's CLUT.
PALETTE_COUNT = 16
PALETTE_STRIDE = psx_vram.VRAM_STRIDE


class BGMPError(ValueError):
    """Raised when a blob doesn't read as BGMP."""


@dataclass
class BGMPTile:
    index: int
    col: int
    row: int
    raw: int

    @property
    def cell(self):
        """Which of the texture page's 256 cells this tile takes."""
        return self.raw & 0xFF

    @property
    def page_x(self):
        return (self.raw & 0x0F) * TILE

    @property
    def page_y(self):
        return self.raw & 0xF0

    @property
    def palette(self):
        return self.raw >> 8


@dataclass
class BGMPFile:
    texpage: int
    clut: int
    clut_x: int
    clut_y: int
    unk1: int
    unk2: int
    width: int
    height: int
    map_size: int
    unk3: int
    unk4: int
    tiles: list = field(default_factory=list)
    trailer: tuple = ()
    slack: int = 0
    size: int = 0

    @property
    def pixel_size(self):
        return self.width * TILE, self.height * TILE

    @property
    def clut_address(self):
        """Byte address of palette 0 in VRAM."""
        return psx_vram.clut_address(self.clut)

    @property
    def clut_echo_agrees(self):
        """Whether the header's spelled-out CLUT x/y match `clut`. True
        on every file on the disc; a False would mean the fields aren't
        what they look like."""
        return (self.clut_x, self.clut_y) == psx_vram.clut_xy(self.clut)

    @property
    def palettes_fit(self):
        """How many of the 16 palettes are inside VRAM at all. A CLUT
        near the bottom leaves room for only a few rows - every file on
        the disc stays within what fits."""
        return max(0, min(PALETTE_COUNT, psx_vram.VRAM_ROWS - self.clut_y))

    @property
    def palettes_used(self):
        return sorted({t.palette for t in self.tiles})

    @property
    def page_origin(self):
        """(byte offset within a VRAM row, first row) of the tile page."""
        return psx_vram.page_origin(self.texpage)

    def tile_at(self, col, row):
        if 0 <= col < self.width and 0 <= row < self.height:
            return self.tiles[row * self.width + col]
        return None

    def tiles_using(self, cell):
        """Every tile taking one particular cell of the texture page."""
        return [t for t in self.tiles if t.cell == cell]


def parse_bgmp(data):
    """Parse one BGMP blob into a BGMPFile. Raises BGMPError if it
    doesn't hold together as one."""
    if len(data) < HEADER_SIZE:
        raise BGMPError(f"only {len(data)} bytes, too short for a header")

    (texpage, clut, clut_x, clut_y, unk1, unk2,
     width, height, map_size, unk3, unk4) = HEADER.unpack_from(data, 0)

    if width == 0 or height == 0:
        raise BGMPError(f"empty map ({width}x{height} tiles)")
    if map_size != width * height * 2:
        raise BGMPError(
            f"map size {map_size:#x} doesn't match {width}x{height} tiles "
            f"({width * height * 2:#x})")
    if HEADER_SIZE + map_size > len(data):
        raise BGMPError(
            f"a {width}x{height} map needs {HEADER_SIZE + map_size:#x} bytes, "
            f"blob is {len(data):#x}")

    raw = struct.unpack_from(f"<{width * height}H", data, HEADER_SIZE)
    tiles = [BGMPTile(i, i % width, i // width, v) for i, v in enumerate(raw)]

    end = HEADER_SIZE + map_size
    trailer = ()
    if end + TRAILER_SIZE <= len(data):
        trailer = struct.unpack_from("<HH", data, end)
        end += TRAILER_SIZE

    return BGMPFile(
        texpage=texpage, clut=clut, clut_x=clut_x, clut_y=clut_y,
        unk1=unk1, unk2=unk2, width=width, height=height, map_size=map_size,
        unk3=unk3, unk4=unk4, tiles=tiles, trailer=trailer,
        slack=len(data) - end, size=len(data),
    )


def load_bgmp(dat_file_path, dat_start, offset, size):
    """Read and parse the BGMP blob at dat_start + offset."""
    if not size:
        raise BGMPError("no size for this entry, so there is no blob to read")
    with open(dat_file_path, "rb") as f:
        f.seek(dat_start + offset)
        data = f.read(size)
    return parse_bgmp(data)

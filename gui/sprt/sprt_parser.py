"""SPRT (sprite) file parser.

Thanks to vervalkon (Tomba Club), who mapped the piece layout out of the
same engine's SPR files.

A Tomba! 2 sprite is not a bitmap. It is a short list of textured quads
- "pieces" - each one cut out of a PSX texture page in VRAM and pinned
at its own signed offset from the sprite's origin. Drawing a simple
sprite is drawing one textured quad; drawing a layered one is drawing
that quad and then another on top of it.

Nothing in the file carries a Z. The quads are flat and coplanar, so
"on top" is settled by draw order alone: pieces are listed front to
back, so the LAST piece is drawn first and the FIRST piece ends up
topmost (see sprt_render.render_sprite).

Layout of one SPRT blob:
    pointer table: (u16 amount, u16 offset) pairs, one per sprite,
                   running from offset 0 up to where the sprite data
                   starts. `amount` is that sprite's piece count,
                   `offset` its first piece in bytes from the blob
                   start. The table carries no count of its own - it
                   ends where the lowest offset begins, which is what
                   parse_sprt() walks to.

    pieces:        16 bytes each, `amount` of them back to back:
        tlX, tlY : u8  - top-left corner UV, inside the texture page
        clut     : u16 - palette location in VRAM, PSX CLUT format:
                         bits 0-5 = x / 16 halfwords, bits 6-14 = y.
                         Bit 15 is set on roughly a third of the retail
                         disc's pieces and is not part of the address -
                         mask it off.
        trX, trY : u8  - top-right corner UV
        pg       : u16 - PSX texpage attribute: bits 0-3 page x (x 64
                         halfwords), bit 4 page y (x 256 rows), bits
                         5-6 semi-transparency mode, bit 7 set for an
                         8bpp page (retail Tomba! 2 is 4bpp throughout)
        blX, blY : u8  - bottom-left corner UV
        ww, hh   : u8  - the piece's size in texels
        brX, brY : u8  - bottom-right corner UV
        pX, pY   : s8  - where the piece's top-left texel lands,
                         relative to the sprite's origin

    Every piece on the retail disc is an axis-aligned rectangle - no
    rotation, no stretch, the corners only ever differ by ww and hh.
    What the corners do carry is the flips: a piece whose tlX sits ww
    AHEAD of trX is drawn mirrored horizontally, and likewise tlY/blY
    vertically, which puts the source rectangle at tlX - ww rather than
    at tlX. UVs are single bytes and wrap at 256 - 34 pieces on the
    retail disc cross that edge (tlX=240, trX=0 for a 16-wide piece) -
    so every U/V step wraps rather than clamps. SpritePiece.u0/v0,
    .hflip and .vflip resolve all of that into a plain source rect.
"""
import struct
from dataclasses import dataclass, field

from functions import psx_vram

PIECE_SIZE = 0x10
_PIECE = struct.Struct("<BBHBBHBBBBBBbb")


@dataclass
class SpritePiece:
    """One textured quad. The raw fields are as read; everything the
    renderer actually needs is derived below them."""
    index: int
    offset: int          # byte offset in the blob, for hex cross-reference
    tlX: int
    tlY: int
    clut: int
    trX: int
    trY: int
    pg: int
    blX: int
    blY: int
    ww: int
    hh: int
    brX: int
    brY: int
    pX: int
    pY: int

    # --- texture page ---

    @property
    def texpage(self):
        """Page number 0..31, as the PSX numbers them (col + row * 16)."""
        return self.pg & 0x1F

    @property
    def page_byte_x(self):
        """Byte offset of the page's left edge within a VRAM row."""
        return psx_vram.page_origin(self.pg)[0]

    @property
    def page_row0(self):
        """First VRAM row of the page."""
        return psx_vram.page_origin(self.pg)[1]

    @property
    def is_8bpp(self):
        return bool(self.pg & 0x80)

    @property
    def semi_transparency(self):
        """PSX blend mode 0..3 (B/2+F/2, B+F, B-F, B+F/4). Only applies
        to pieces the game draws as semi-transparent, which is not
        recorded here - kept for reference, not used when rendering."""
        return (self.pg >> 5) & 3

    # --- palette ---

    @property
    def clut_index(self):
        """CLUT attribute with the stray bit 15 masked off."""
        return psx_vram.clut_index(self.clut)

    @property
    def clut_address(self):
        """Byte address of the palette in VRAM."""
        return psx_vram.clut_address(self.clut)

    @property
    def clut_xy(self):
        """(x, y) of the palette in VRAM halfword coordinates."""
        return psx_vram.clut_xy(self.clut)

    # --- source rectangle ---

    @property
    def hflip(self):
        """True when tl/tr run right-to-left, i.e. the piece is mirrored
        horizontally. Compared mod 256 so pieces wrapping the page edge
        aren't mistaken for flipped ones."""
        return ((self.trX - self.tlX) & 0xFF) != self.ww

    @property
    def vflip(self):
        return ((self.blY - self.tlY) & 0xFF) != self.hh

    @property
    def u0(self):
        """Left edge of the source rect, flips undone."""
        return (self.tlX - self.ww) & 0xFF if self.hflip else self.tlX

    @property
    def v0(self):
        """Top edge of the source rect, flips undone."""
        return (self.tlY - self.hh) & 0xFF if self.vflip else self.tlY

    @property
    def is_axis_aligned(self):
        """False would mean a rotated or sheared quad. Nothing on the
        retail disc is, and the renderer assumes it - the viewer flags
        any piece that isn't instead of drawing it wrong."""
        return (self.tlY == self.trY and self.blY == self.brY
                and self.tlX == self.blX and self.trX == self.brX)

    @property
    def rect(self):
        """(x0, y0, x1, y1) the piece covers in sprite space."""
        return self.pX, self.pY, self.pX + self.ww, self.pY + self.hh


@dataclass
class Sprite:
    index: int
    offset: int      # where this sprite's pieces start in the blob
    pieces: list = field(default_factory=list)

    def extent(self, include_origin=True):
        """(x0, y0, x1, y1) around every piece in sprite space.

        `include_origin` keeps (0, 0) inside the box, so sprites of one
        bank stay registered against each other instead of each being
        cropped to its own art."""
        if not self.pieces:
            return (0, 0, 1, 1)
        x0 = min(p.pX for p in self.pieces)
        y0 = min(p.pY for p in self.pieces)
        x1 = max(p.pX + p.ww for p in self.pieces)
        y1 = max(p.pY + p.hh for p in self.pieces)
        if include_origin:
            x0, y0 = min(x0, 0), min(y0, 0)
            x1, y1 = max(x1, 1), max(y1, 1)
        return x0, y0, x1, y1


@dataclass
class SPRTFile:
    sprites: list = field(default_factory=list)
    table_size: int = 0      # bytes of pointer table, = where sprite 0 starts
    size: int = 0

    @property
    def piece_count(self):
        return sum(len(s.pieces) for s in self.sprites)

    @property
    def odd_pieces(self):
        """Pieces that aren't plain axis-aligned rectangles - see
        SpritePiece.is_axis_aligned. Empty on the retail disc."""
        return [(s.index, p) for s in self.sprites for p in s.pieces
                if not p.is_axis_aligned]


class SPRTError(ValueError):
    """Raised when a blob doesn't read as SPRT."""


def parse_pointer_table(data):
    """The (amount, offset) pairs at the head of the blob.

    Walks pairs until the table has grown to meet the lowest offset seen
    so far - that offset is where the first sprite's pieces begin, and
    so where the table has to stop. Taking the minimum rather than the
    first pair's offset costs nothing and doesn't assume the sprites are
    stored in table order."""
    pairs = []
    pos = 0
    table_end = 4
    lowest = None
    while pos + 4 <= len(data):
        amount, offset = struct.unpack_from("<HH", data, pos)
        pairs.append((amount, offset))
        pos += 4
        lowest = offset if lowest is None else min(lowest, offset)
        if table_end == lowest:
            return pairs
        if table_end > lowest:
            raise SPRTError(
                f"pointer table runs past the first sprite (offset {lowest:#x} "
                f"inside a {table_end:#x}-byte table)")
        table_end += 4
    raise SPRTError("ran out of data before the pointer table ended")


def parse_sprt(data):
    """Parse one SPRT blob into an SPRTFile. Raises SPRTError if it
    doesn't hold together as one."""
    pairs = parse_pointer_table(data)
    table_size = len(pairs) * 4
    sprites = []
    for index, (amount, offset) in enumerate(pairs):
        end = offset + amount * PIECE_SIZE
        if offset < table_size or end > len(data):
            raise SPRTError(
                f"sprite {index}: {amount} piece(s) at {offset:#x} fall outside "
                f"the blob (table {table_size:#x}, size {len(data):#x})")
        pieces = []
        for p in range(amount):
            at = offset + p * PIECE_SIZE
            pieces.append(SpritePiece(p, at, *_PIECE.unpack_from(data, at)))
        sprites.append(Sprite(index, offset, pieces))
    return SPRTFile(sprites=sprites, table_size=table_size, size=len(data))


def load_sprt(dat_file_path, dat_start, offset, size):
    """Read and parse the SPRT blob at dat_start + offset."""
    if not size:
        raise SPRTError("no size for this entry, so there is no blob to read")
    with open(dat_file_path, "rb") as f:
        f.seek(dat_start + offset)
        data = f.read(size)
    return parse_sprt(data)

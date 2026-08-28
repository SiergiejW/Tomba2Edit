"""DRWB (the second drawmap) parser.

There are only four DRWBs on the retail disc - AREA_04, AREA_0A,
AREA_1B and AREA_20 - and all four are exactly 0xA90 bytes, which is
52 x 52. AREA_0A's and AREA_20's are byte for byte identical.

Unlike a DRWA, which is a table of pointers into the geometry right
behind it, a DRWB is one byte per cell and points at nothing. What it
does is line up with the level:

    Read with a stride of 52 and TRANSPOSED against the DRWA - the
    DRWB's stored column is the DRWA's row (world Z), its stored row is
    the DRWA's column (world X) - every cell where the level actually
    has geometry has a non-zero byte. All four files, offset (0, 0), no
    cell missed:

        AREA_04  343/343      AREA_0A  437/437
        AREA_1B  115/115      AREA_20  454/454

    The stride is not a guess: of every width from 2 to 200, 52 is by
    some way the best at making each byte match the one a row below it
    (0.59-0.83, next best 104, which is two rows of it).

    AREA_1B is worth knowing about - its DRWB matches that area's
    SECOND MDAT (file 17, id 0x20), not the id-8 one, which is why
    load_drwb() is handed candidates to choose between rather than
    being told which MDAT is the right one.

Each file sets more cells than its level has geometry in - AREA_04 110
more, AREA_0A 580 - so the map covers ground the geometry doesn't, some
of it touching the level and some well away from it.

The byte is eight flags rather than a number: every bit on its own
draws a connected region of the map, and in the two big files the low
nibble is a strict subset of the high one (AREA_0A: bit 0 and bit 4 are
the same 371 cells; bit 1's 516 cells are all inside bit 5's 526). In
AREA_1B the two nibbles never overlap at all.

WHAT THE BITS MEAN IS NOT DECODED. vervalkon's recollection is that of
DRWA and DRWB "one of them determined the visibility of polygon
groups", and a per-cell flag set that covers the level plus a margin
would fit that, but nothing here confirms it. The viewer shows the
planes and lets them be compared against the level; it doesn't claim to
know what they switch.
"""
import math
from collections import Counter
from dataclasses import dataclass

# Every DRWB on the disc. A different one would be read at whatever
# square its own length makes.
DISC_SIDE = 52
DISC_SIZE = DISC_SIDE * DISC_SIDE

BITS = 8


class DRWBError(ValueError):
    """Raised when a blob doesn't read as a DRWB."""


@dataclass
class DRWBFile:
    side: int
    cells: bytes
    address: int = 0

    # --- reading it the two ways round ---

    def raw_at(self, col, row):
        """The byte as STORED - `col` and `row` are positions in the
        file itself, for cross-referencing against a hex editor."""
        if 0 <= col < self.side and 0 <= row < self.side:
            return self.cells[row * self.side + col]
        return None

    def value_at(self, x, z):
        """The byte for the cell the LEVEL has at (x, z) - the same
        (column, row) a DRWA would call it. This is the transpose of
        how the file stores it; see the module docstring."""
        if 0 <= x < self.side and 0 <= z < self.side:
            return self.cells[x * self.side + z]
        return None

    # --- what's in it ---

    @property
    def size(self):
        return len(self.cells)

    @property
    def set_count(self):
        return sum(1 for b in self.cells if b)

    @property
    def values(self):
        """{byte value: how many cells hold it}, zero left out."""
        return Counter(b for b in self.cells if b)

    @property
    def bit_counts(self):
        """How many cells each of the eight flags is set in."""
        return [sum(1 for b in self.cells if b >> n & 1) for n in range(BITS)]

    @property
    def bits_used(self):
        return [n for n, count in enumerate(self.bit_counts) if count]

    def plane(self, bit):
        """Every level cell (x, z) with `bit` set."""
        return {(i // self.side, i % self.side)
                for i, b in enumerate(self.cells) if b >> bit & 1}

    def set_cells(self):
        """Every level cell (x, z) with any flag at all."""
        return {(i // self.side, i % self.side)
                for i, b in enumerate(self.cells) if b}

    def bounds(self):
        """(x0, x1, z0, z1) around the set cells, in level cells, or
        None if nothing is set."""
        cells = self.set_cells()
        if not cells:
            return None
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        return min(xs), max(xs), min(zs), max(zs)

    def nibble_overlap(self):
        """(shared, low only, high only) counted over bit n against bit
        n+4, summed across the four pairs - the low-nibble-inside-the-
        high-nibble pattern the module docstring describes."""
        shared = low_only = high_only = 0
        for n in range(BITS // 2):
            for b in self.cells:
                low = b >> n & 1
                high = b >> (n + BITS // 2) & 1
                shared += low and high
                low_only += low and not high
                high_only += high and not low
        return shared, low_only, high_only


def parse_drwb(data, address=0):
    """Parse one DRWB blob. Raises DRWBError if its length isn't a
    square, since the grid's width is the only thing that says how to
    read it and nothing in the file states it."""
    if not data:
        raise DRWBError("empty blob")
    side = math.isqrt(len(data))
    if side * side != len(data):
        raise DRWBError(
            f"{len(data)} bytes isn't a square grid - every DRWB on the disc "
            f"is {DISC_SIZE} bytes ({DISC_SIDE}x{DISC_SIDE})")
    return DRWBFile(side=side, cells=bytes(data), address=address)


def load_drwb(dat_file_path, dat_start, offset, size):
    """Read and parse the DRWB blob at dat_start + offset."""
    if not size:
        raise DRWBError("no size for this entry, so there is no blob to read")
    with open(dat_file_path, "rb") as f:
        f.seek(dat_start + offset)
        data = f.read(size)
    return parse_drwb(data, address=dat_start + offset)


def coverage(drwb, drwa):
    """How much of a level this DRWB accounts for, as
    (covered, total, extra):

        covered - cells with geometry whose DRWB byte is non-zero
        total   - cells with geometry
        extra   - cells the DRWB sets that hold no geometry

    All four files on the disc cover their level completely, which is
    what identifies which MDAT a DRWB belongs to - see match_mdat().
    """
    occupied = {(g.col, g.row) for g in drwa.groups}
    covered = sum(1 for x, z in occupied if drwb.value_at(x, z))
    return covered, len(occupied), len(drwb.set_cells()) - covered


def match_mdat(drwb, candidates):
    """Pick the DRWA whose level this DRWB covers best.

    `candidates` is [(label, DRWAFile), ...]. Returns
    (label, drwa, covered, total) for the best, or None if there are no
    candidates. AREA_1B's DRWB goes with that area's second MDAT rather
    than its first, so which one it is has to be measured, not assumed.
    """
    best = None
    for label, drwa in candidates:
        if not drwa.groups:
            continue
        covered, total, _extra = coverage(drwb, drwa)
        score = covered / total if total else 0
        if best is None or score > best[0]:
            best = (score, label, drwa, covered, total)
    if best is None:
        return None
    _score, label, drwa, covered, total = best
    return label, drwa, covered, total

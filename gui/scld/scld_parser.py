"""SCLD (collision) file parser.

Thanks to vervalkon (Tomba Club) for the table layout.

An SCLD holds the area's collision *planes*. A plane is not a path: it
is a grid of 64-unit square cells laid over the level's floor plan, and
the game finds the cell under an actor by plain division. The placement
here is the game's own, read out of MAIN.EXE - f_LoadAreaPlaneDescriptor,
f_ProjectActorOntoAreaPlane, f_FindAreaPlaneCollisionCell and
f_ComputeAreaPlaneCellCorrection - rather than fitted to the geometry.

Layout of one SCLD blob:
    header:  u16 entry_count (N)
    pointer table: (N + 1) x u16, word offsets from the start of the blob
                   (x2 for the byte offset). The last is not an entry -
                   it serves as the final entry's `next_base`.

Each of the N pointers locates one entry - one plane:
    entry header (0x14 bytes):
        xxx1, xxx2, yyy1, yyy2 : s16   - the plane's two endpoints, in
                                         the game's x and z. Not a
                                         bounding box to interpolate
                                         across: the low corner is where
                                         the cell grid starts, and the
                                         span in x is what decides how
                                         many columns of cells there are.
        unkn                   : s16   - the plane's gradient, signed
                                         2.14 fixed point. The game
                                         projects onto the plane with
                                             z = yyy1 + ((x - xxx1) * unkn >> 14)
                                         when x is the longer axis, and
                                         with x and z swapped when z is.
                                         Exact on all 599 entries of the
                                         US disc.
        ls, le                 : u8    - the plane reached off each end,
                                         one-based; 0 means the end is a
                                         wall and the actor is clamped
        ptr1..ptr4             : u16   - word offsets from THIS entry's
                                         base (x2 for bytes)

    data0  [header_end .. ptr1) : the column index - one (row_base,
                                  first cell) u16 pair per 64-unit column
                                  of x, then an (FFFF, count) end-stop.
                                  See columns(). A plane whose x span is
                                  zero is one column wide and carries no
                                  end-stop.
    table1 [ptr1 .. ptr2)       : 8-byte records - the cells themselves,
                                  (u16 flags, u16 first, u16 count,
                                  u16 profile):
                                    flags - bit 0 mirrors the cell in z,
                                            bit 1 in x, bit 2 transposes
                                            it, bit 3 picks the profile's
                                            second form; bit 15 means the
                                            record is not a leaf but a
                                            step to a neighbouring cell,
                                            and bits 14|15 together a
                                            step through table2
                                    first,
                                    count - this cell's run of table3
                                            records. They tile table3
                                            exactly - no overlap anywhere
                                            on the disc
                                    profile - the low and high byte are
                                            the plane's line across this
                                            cell, in cell-local units.
                                            See cell_line()
    table2 [ptr2 .. ptr3)       : 8-byte records, walked by the bit-14|15
                                  cells. Not decoded here.
    table3 [ptr3 .. ptr4)       : 8-byte records - the surfaces stacked in
                                  one cell: (u16 kind, s16 pos, s16 rise,
                                  u16 profile).
                                    kind  - low nibble is a surface type
                                    pos   - the surface's height; height
                                            in the viewers is -pos
                                    rise  - how much higher the surface
                                            gets across the cell; the
                                            game tests an actor against
                                            pos .. pos + max(rise, 0)
                                    profile - which tail record applies
    tail   [ptr4 .. next_base)  : 3-byte records, padded to a word. The
                                  last two bytes are a signed vector of
                                  magnitude ~64 (1.0 == 64); the first is
                                  a separate signed scalar. Not used.

World placement (SCLDEntry.trace()):
        col, row  from the column index
        x = min(xxx1, xxx2) + 64 * col + local x
        z = min(yyy1, yyy2) + 64 * row + local z
        y = -record.pos
    with (local x, local z) from the cell's own profile - see
    cell_line(). Nothing is interpolated and nothing is fitted: a
    record's place is read off the file.

    A SCLD file's entries can span more world than one MDAT room, so this
    space does not register against any single room.
"""
import struct
from dataclasses import dataclass, field

# The side of one collision cell, in world units. The game divides by it
# with a shift, so it is a hard 64 everywhere.
CELL = 64


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _s16(b, o):
    return struct.unpack_from("<h", b, o)[0]


def cell_line(flags, profile):
    """Where the plane's own line crosses one cell, as (local x, local z)
    in 0..63.

    A cell is stored in a canonical orientation and mirrored into place
    by its flags, so the line is solved there and turned back afterwards.
    In that frame the profile is a function of one axis:

        bit 3 set    z = lo + ((hi - lo) * x >> 6)
        bit 3 clear  z = hi * (x - lo) / (63 - lo)

    and the sample is taken at the middle of the axis it is a function
    of. This is f_ComputeAreaPlaneCellCorrection run backwards: that
    subtracts the same value from a probe's local z to push it onto the
    plane."""
    lo, hi = profile & 0xFF, profile >> 8
    along = CELL // 2
    if flags & 0x8:
        across = lo + (((hi - lo) * along) >> 6)
    elif lo ^ 0x3F:
        across = (hi * (along - lo)) // (lo ^ 0x3F)
    else:
        across = along
    across = max(0, min(0x3F, across))
    if flags & 0x4:
        along, across = across, along     # transposed cell
    if flags & 0x2:
        along ^= 0x3F                     # mirrored in x
    if flags & 0x1:
        across ^= 0x3F                    # mirrored in z
    return along, across


@dataclass
class PathPoint:
    kind: int
    pos: int
    elevation: int
    seg_index: int
    record_offset: int


@dataclass
class Cell:
    """One 64-unit square of a plane, and the records standing in it."""
    index: int          # which table1 record
    col: int            # column along x, from the start of the grid
    row: int            # row along z
    flags: int
    first: int          # first table3 record
    count: int
    profile: int
    x: int              # world x of the cell's low corner
    z: int              # world z of the cell's low corner

    @property
    def leaf(self):
        """Whether the cell holds surfaces itself. A cell with bit 15 set
        is a step to a neighbouring cell instead, and its `first`/`count`
        mean something else."""
        return not self.flags & 0x8000

    def point(self):
        """(x, z) of this cell's sample - where the plane's line crosses
        it."""
        dx, dz = cell_line(self.flags, self.profile)
        return self.x + dx, self.z + dz


@dataclass
class SCLDEntry:
    index: int
    base: int
    xxx1: int
    xxx2: int
    yyy1: int
    yyy2: int
    unkn: int
    ls: int
    le: int
    ptr1: int
    ptr2: int
    ptr3: int
    ptr4: int
    data0: list = field(default_factory=list)
    assets: list = field(default_factory=list)   # table1 raw 4-tuples
    objects: list = field(default_factory=list)  # table2 raw 8-tuples
    path: list = field(default_factory=list)     # PathPoint, in file order
    tail: bytes = b""

    @property
    def slope(self):
        """The plane's gradient across its shorter axis, as a fraction.
        `unkn` holds it in signed 2.14 fixed point."""
        return struct.unpack("<h", struct.pack("<H", self.unkn))[0] / 16384.0

    @property
    def origin(self):
        """(x, z) of the grid's low corner - where cell (0, 0) starts."""
        return min(self.xxx1, self.xxx2), min(self.yyy1, self.yyy2)

    @property
    def width(self):
        """Columns of cells, along x. A plane with no x span is still one
        cell wide - the game widens a degenerate axis by 64."""
        return (abs(self.xxx2 - self.xxx1) >> 6) + 1

    def columns(self):
        """data0 as one (row_base, first cell) pair per column.

        The pair says where that column's run of cells starts: the cell
        holding row r is table1[first + r - row_base], valid until the
        next column's `first`. An (FFFF, count) end-stop closes the
        list and gives the last column its bound."""
        d0 = self.data0
        pairs = [(d0[k], d0[k + 1]) for k in range(0, len(d0) - 1, 2)]
        return pairs[:self.width]

    def cells(self):
        """Every cell of this plane, in table1 order."""
        d0 = self.data0
        pairs = [(d0[k], d0[k + 1]) for k in range(0, len(d0) - 1, 2)]
        x0, z0 = self.origin
        out = []
        for col in range(min(self.width, len(pairs))):
            base, first = pairs[col]
            if base == 0xFFFF:
                continue
            end = pairs[col + 1][1] if col + 1 < len(pairs) else len(self.assets)
            for k in range(max(0, min(end, len(self.assets)) - first)):
                flags, at, run, profile = self.assets[first + k]
                out.append(Cell(index=first + k, col=col, row=base + k,
                                flags=flags, first=at, count=run,
                                profile=profile,
                                x=x0 + CELL * col, z=z0 + CELL * (base + k)))
        return out

    def placed(self):
        """(table3 record index, cell) for every record a cell claims, in
        record order.

        Cells that only step to a neighbour claim nothing, so a few
        records - about 4% of the disc - are left unplaced here; they are
        reached through table2, which is not decoded."""
        owner = {}
        for cell in self.cells():
            if not cell.leaf:
                continue
            for k in range(cell.count):
                r = cell.first + k
                if 0 <= r < len(self.path) and r not in owner:
                    owner[r] = cell
        return [(r, owner[r]) for r in sorted(owner)]

    def trace(self):
        """This entry's records as world (x, y, z), in record order.

        The viewers' axes, not the game's: the game's z is the viewers'
        x and vice versa, the same swap gui/mdat/mdat.py makes for room
        geometry (see gui/level/level_scene.view_position)."""
        return [self._point(self.path[r], cell) for r, cell in self.placed()]

    def records(self):
        """The table3 record index behind each point of trace()."""
        return [r for r, _cell in self.placed()]

    def wall_candidates(self):
        """Each cell's records joined bottom to top - the stack of
        surfaces standing at one 64-unit square.

        Which of those pairs is a real wall face is not decoded; these
        are all of them, for checking by eye."""
        out = []
        for cell in self.cells():
            if not cell.leaf:
                continue
            column = sorted((r for r in range(cell.first,
                                              min(cell.first + cell.count,
                                                  len(self.path)))),
                            key=lambda r: self.path[r].pos)
            pts = [self._point(self.path[r], cell) for r in column]
            out.extend([a, b] for a, b in zip(pts, pts[1:]))
        return out

    def _point(self, record, cell):
        gx, gz = cell.point()
        return gz, -record.pos, gx


@dataclass
class SCLDFile:
    entry_count: int
    pointers: list
    entries: list


def parse_scld(blob: bytes) -> SCLDFile:
    count = _u16(blob, 0)
    ptrs = [_u16(blob, 2 + 2 * i) for i in range(count + 1)]

    entries = []
    for i in range(count):
        base = ptrs[i] * 2
        next_base = ptrs[i + 1] * 2
        if base == 0:
            continue

        xxx1, xxx2, yyy1, yyy2 = struct.unpack_from("<4h", blob, base)
        unkn = _u16(blob, base + 8)
        ls, le = blob[base + 10], blob[base + 11]
        rp1, rp2, rp3, rp4 = struct.unpack_from("<4H", blob, base + 12)
        p1, p2, p3, p4 = base + rp1 * 2, base + rp2 * 2, base + rp3 * 2, base + rp4 * 2

        entry = SCLDEntry(
            index=i, base=base,
            xxx1=xxx1, xxx2=xxx2, yyy1=yyy1, yyy2=yyy2,
            unkn=unkn, ls=ls, le=le,
            ptr1=p1, ptr2=p2, ptr3=p3, ptr4=p4,
        )

        header_end = base + 20
        entry.data0 = [_u16(blob, o) for o in range(header_end, p1, 2)]

        entry.assets = [struct.unpack_from("<4H", blob, o) for o in range(p1, p2, 8)]

        entry.objects = [struct.unpack_from("<4H", blob, o) for o in range(p2, p3, 8)]

        for o in range(p3, p4, 8):
            kind = _u16(blob, o)
            pos = _s16(blob, o + 2)
            elevation = _s16(blob, o + 4)
            seg_index = _u16(blob, o + 6)
            entry.path.append(PathPoint(kind, pos, elevation, seg_index, o))

        entry.tail = bytes(blob[p4:next_base])

        entries.append(entry)

    return SCLDFile(entry_count=count, pointers=ptrs, entries=entries)


def load_scld(dat_path: str, dat_start: int, offset: int, size: int) -> SCLDFile:
    with open(dat_path, "rb") as f:
        f.seek(dat_start + offset)
        blob = f.read(size)
    return parse_scld(blob)


def find_area_scld_location(idx_path: str, chunk_index: int):
    """Scan one AREA's SDAT pointer table in TOMBA2.IDX for its SCLD
    (id 7) entry - same chunk layout idx_parser.parse_idx_file() reads.
    Returns (dat_start, offset, size), or None if this area has no
    collision file."""
    chunk_size = 0x800
    with open(idx_path, "rb") as idx:
        idx.seek(chunk_index * chunk_size)
        _, _, dat_start, dat_end, pointer_amount = struct.unpack("<5I", idx.read(20))
        raw = idx.read(pointer_amount * 4)
    pointers = struct.unpack(f"<{pointer_amount}I", raw)
    entries = [(v >> 24, v & 0xFFFFFF) for v in pointers]
    for i, (id_, offset) in enumerate(entries):
        if id_ == 7:
            next_offset = entries[i + 1][1] if i + 1 < len(entries) else dat_end - dat_start
            return dat_start, offset, next_offset - offset
    return None

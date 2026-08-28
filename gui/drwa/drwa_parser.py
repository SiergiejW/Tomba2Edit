"""DRWA (drawmap) parser.

Thanks to vervalkon (Tomba Club), who worked the format out - this
follows his description, and gui/mdat/mdat.py reads the same bytes to
build the 3D room.

A DRWA is not a file of its own on the disc: it is the head of an MDAT
entry, and the rest of that entry is the level's geometry. It is a
top-down grid over the level, one 16-bit word per cell:

    header (4 bytes):
        rows    : u16 - cells down the level (world Z)
        columns : u16 - cells across it (world X)

    Note the order: the ROW count comes first, so the grid's stride is
    the second word, not the first. It matters on every level but the
    square ones - read the other way round, a level's map comes out
    sheared into diagonal runs instead of its own shape.

    grid [4 .. 4 + rows * columns * 2): u16 per cell, left to right and
        top to bottom. 0xFFFF is an empty cell; anything else is a
        pointer, in 4-byte units, from the START OF THE DRWA - so the
        polygon group it names sits at drwa_address + value * 4.

    Each group begins with two u16 counts, triangles then quads,
    followed by that many 36-byte triangle and 44-byte quad records.

The first cell to carry a pointer points at the first group, which is
the first thing after the grid (rounded up to the 4 bytes a pointer can
address), and the groups run on from there, back to back, in the order
the cells are read - the last one ending exactly at the end of the MDAT
entry. That is what makes a DRWA worth reading before anything else:
its smallest and largest pointers bound the whole of the geometry that
follows, without the IDX having to be consulted at all.

Every pointer on the retail disc is distinct, so a group belongs to
exactly one cell, and a cell to one group.

A cell is a square patch of the world seen from above: fitting each
group's own vertices against its column and row comes back square on
all 33 of the disc's levels, 570-650 world units a side (AREA_07, the
biggest, is the one exception at 1024), and fits at R2 0.97 to 1.00 on
both axes. So the grid is the level's floor plan, and cell_size()
measures a level's own square from its geometry.
"""
import struct
from dataclasses import dataclass, field

EMPTY = 0xFFFF

HEADER = struct.Struct("<HH")
HEADER_SIZE = 4

# Pointers are stored in 4-byte units - vervalkon's "multiply by four".
POINTER_UNIT = 4

GROUP_HEADER = struct.Struct("<hh")
GROUP_HEADER_SIZE = 4
TRI_SIZE = 36
QUAD_SIZE = 44

# Where a record keeps its vertices, as (x, y, z) byte offsets from the
# record's own start. Straight out of gui/mdat/mdat.py - the fields are
# not in a tidy order, and this is the order they are actually in.
TRI_VERTS = ((20, 18, 16), (22, 26, 24), (32, 30, 28))
QUAD_VERTS = ((36, 34, 32), (24, 22, 20), (26, 30, 28), (38, 42, 40))

# A grid larger than this is taken as proof the blob isn't a DRWA, well
# before an absurd width x height can be multiplied out into a read.
# The largest on the disc is AREA_07's 64x64.
MAX_CELLS = 1 << 16


class DRWAError(ValueError):
    """Raised when a blob doesn't read as a DRWA."""


@dataclass
class DRWAGroup:
    """One cell's polygon group, and where it sits in the world."""

    index: int          # position in the pointer order, which is also file order
    cell: int           # index into the grid
    col: int
    row: int
    pointer: int        # the u16 as stored
    offset: int         # pointer * 4 - bytes from the start of the DRWA
    tris: int
    quads: int
    size: int           # bytes, header and records together
    faces: list = field(default_factory=list)   # [[(x, y, z), ...], ...]

    @property
    def end(self):
        return self.offset + self.size

    @property
    def vertices(self):
        return [v for face in self.faces for v in face]

    @property
    def bounds(self):
        """(x0, x1, y0, y1, z0, z1), or None for an empty group."""
        verts = self.vertices
        if not verts:
            return None
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        zs = [v[2] for v in verts]
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

    @property
    def centre(self):
        """Mean of this group's vertices, or None if it has none."""
        verts = self.vertices
        if not verts:
            return None
        n = len(verts)
        return (sum(v[0] for v in verts) / n,
                sum(v[1] for v in verts) / n,
                sum(v[2] for v in verts) / n)


@dataclass
class DRWAFile:
    width: int
    height: int
    address: int                                 # where the DRWA starts in the DAT
    cells: tuple = ()                            # the raw u16s, in reading order
    groups: list = field(default_factory=list)   # in pointer order
    strays: list = field(default_factory=list)   # (cell, value) that aren't pointers
    declared_size: int = 0                       # what the IDX gives this entry
    extent: int = 0                              # where the last group actually ends

    @property
    def cell_count(self):
        return self.width * self.height

    @property
    def map_size(self):
        return self.cell_count * 2

    @property
    def header_end(self):
        return HEADER_SIZE + self.map_size

    @property
    def data_start(self):
        """The first byte a pointer can name after the grid - the grid's
        end rounded up to 4, since pointers are in 4-byte units. Every
        level on the disc puts its first group exactly here."""
        return (self.header_end + POINTER_UNIT - 1) // POINTER_UNIT * POINTER_UNIT

    @property
    def padding(self):
        """Bytes of alignment between the grid and the first group."""
        return self.data_start - self.header_end

    @property
    def slack(self):
        """Bytes of the entry left over past the last group. 0 across
        the disc - the geometry fills its slot exactly."""
        return max(0, self.declared_size - self.extent) if self.declared_size else 0

    @property
    def contiguous(self):
        """Whether the groups run back to back from the grid's end, with
        no gap and no overlap. True across the disc."""
        at = self.data_start
        for group in self.groups:
            if group.offset != at:
                return False
            at = group.end
        return True

    @property
    def tri_count(self):
        return sum(g.tris for g in self.groups)

    @property
    def quad_count(self):
        return sum(g.quads for g in self.groups)

    @property
    def bounds(self):
        """(x0, x1, y0, y1, z0, z1) over every group, or None."""
        boxes = [g.bounds for g in self.groups if g.bounds]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), max(b[1] for b in boxes),
                min(b[2] for b in boxes), max(b[3] for b in boxes),
                min(b[4] for b in boxes), max(b[5] for b in boxes))

    def cell_size(self):
        """How much world a cell covers, measured from the geometry
        itself: (units per column, units per row, how well it fits).

        A straight-line fit of every group's centre against its column
        and row. The fit is what says the grid really is a floor plan -
        it comes back square, and at 1.00, on every level on the disc.
        None when there is too little to fit."""
        points = [(g.col, g.row, g.centre) for g in self.groups if g.centre]
        if len(points) < 3:
            return None
        dx, fit_x = _slope([p[0] for p in points], [p[2][0] for p in points])
        dz, fit_z = _slope([p[1] for p in points], [p[2][2] for p in points])
        return dx, dz, min(fit_x, fit_z)

    def cell_at(self, col, row):
        if 0 <= col < self.width and 0 <= row < self.height:
            return self.cells[row * self.width + col]
        return None

    def group_at(self, col, row):
        """The group a cell points at, or None if the cell is empty."""
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        return self._by_cell.get(row * self.width + col)

    def __post_init__(self):
        self._by_cell = {g.cell: g for g in self.groups}


def _slope(xs, ys):
    """(slope, R2) of a straight line through the points."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if not sxx:
        return 0.0, 0.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x
    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    total = sum((y - mean_y) ** 2 for y in ys)
    return slope, (1 - residual / total if total else 1.0)


def _read_group(data, offset):
    """Counts, byte length and vertices of the group at `offset` within
    `data`. Raises DRWAError if it doesn't fit or doesn't read."""
    if offset + GROUP_HEADER_SIZE > len(data):
        raise DRWAError(f"group at 0x{offset:X} starts past the end of the blob")
    tris, quads = GROUP_HEADER.unpack_from(data, offset)
    if tris < 0 or quads < 0:
        raise DRWAError(f"group at 0x{offset:X} has a negative count "
                        f"({tris} tris, {quads} quads)")
    size = GROUP_HEADER_SIZE + tris * TRI_SIZE + quads * QUAD_SIZE
    if offset + size > len(data):
        raise DRWAError(
            f"group at 0x{offset:X} needs {size:#x} bytes for {tris} tris and "
            f"{quads} quads, and the blob ends at {len(data):#x}")

    faces = []
    at = offset + GROUP_HEADER_SIZE
    for count, stride, layout in ((tris, TRI_SIZE, TRI_VERTS),
                                  (quads, QUAD_SIZE, QUAD_VERTS)):
        for _ in range(count):
            face = []
            for ox, oy, oz in layout:
                x = struct.unpack_from("<h", data, at + ox)[0]
                y = struct.unpack_from("<h", data, at + oy)[0]
                z = struct.unpack_from("<h", data, at + oz)[0]
                # Y flipped, exactly as gui/mdat/mdat.py does it, so the
                # heights here read the same way round as the 3D viewer's.
                face.append((x, -y, z))
            faces.append(face)
            at += stride
    return tris, quads, size, faces


def parse_drwa(data, address=0, declared_size=0):
    """Parse the DRWA at the head of one MDAT blob, following every
    pointer into the geometry behind it. Raises DRWAError if the blob
    doesn't hold together as one."""
    if len(data) < HEADER_SIZE:
        raise DRWAError(f"only {len(data)} bytes, too short for a header")

    # Rows first, columns second - see the note in the module docstring.
    height, width = HEADER.unpack_from(data, 0)
    if width == 0 or height == 0:
        raise DRWAError(f"empty grid ({width}x{height} cells)")
    if width * height > MAX_CELLS:
        raise DRWAError(f"a {width}x{height} grid is too big to be a drawmap")

    cell_count = width * height
    if HEADER_SIZE + cell_count * 2 > len(data):
        raise DRWAError(
            f"a {width}x{height} grid needs {HEADER_SIZE + cell_count * 2:#x} "
            f"bytes, blob is {len(data):#x}")

    cells = struct.unpack_from(f"<{cell_count}H", data, HEADER_SIZE)
    grid_end = HEADER_SIZE + cell_count * 2
    data_start = (grid_end + POINTER_UNIT - 1) // POINTER_UNIT * POINTER_UNIT

    # In pointer order, which is also the order the groups sit in the
    # file - and, on the disc, the order the cells are read in too.
    pointed = sorted(((v, i) for i, v in enumerate(cells) if v != EMPTY),
                     key=lambda pair: pair[0])
    groups = []
    strays = []
    for value, cell in pointed:
        offset = value * POINTER_UNIT
        if offset < data_start:
            # Would land inside the grid itself, so it isn't a pointer.
            strays.append((cell, value))
            continue
        tris, quads, size, faces = _read_group(data, offset)
        groups.append(DRWAGroup(
            index=len(groups), cell=cell, col=cell % width, row=cell // width,
            pointer=value, offset=offset, tris=tris, quads=quads, size=size,
            faces=faces))

    return DRWAFile(
        width=width, height=height, address=address, cells=cells,
        groups=groups, strays=strays,
        declared_size=declared_size or len(data),
        extent=max((g.end for g in groups), default=data_start),
    )


def blob_extent(f, address):
    """How many bytes the DRWA at `address` and its geometry occupy,
    read straight from the file - the whole MDAT entry, without needing
    the IDX to say how long it is. This is the thing vervalkon's "as
    long as you know where a DRWA starts, you can get the whole MDAT
    out" comes down to: the largest pointer names the last group, and
    that group's own counts say where it ends."""
    f.seek(address)
    header = f.read(HEADER_SIZE)
    if len(header) < HEADER_SIZE:
        raise DRWAError("blob ends inside the header")
    rows, columns = HEADER.unpack(header)
    if rows == 0 or columns == 0 or rows * columns > MAX_CELLS:
        raise DRWAError(f"{columns}x{rows} isn't a drawmap grid")

    cell_count = rows * columns
    raw = f.read(cell_count * 2)
    if len(raw) < cell_count * 2:
        raise DRWAError("blob ends inside the grid")
    cells = struct.unpack(f"<{cell_count}H", raw)

    pointers = [v for v in cells if v != EMPTY]
    grid_end = (HEADER_SIZE + cell_count * 2 + POINTER_UNIT - 1) // POINTER_UNIT * POINTER_UNIT
    if not pointers:
        return grid_end

    last = max(pointers) * POINTER_UNIT
    f.seek(address + last)
    counts = f.read(GROUP_HEADER_SIZE)
    if len(counts) < GROUP_HEADER_SIZE:
        raise DRWAError(f"the last pointer names 0x{last:X}, past the end of the file")
    tris, quads = GROUP_HEADER.unpack(counts)
    if tris < 0 or quads < 0:
        raise DRWAError(f"the last group has a negative count ({tris}, {quads})")
    return last + GROUP_HEADER_SIZE + tris * TRI_SIZE + quads * QUAD_SIZE


def load_drwa(dat_file_path, dat_start, offset, size=None):
    """Read and parse the DRWA (and the geometry it points at) of the
    MDAT entry at dat_start + offset.

    `size` is what the IDX gives the entry, and is only used as a
    cross-check: how much to read is worked out from the drawmap itself
    (see blob_extent)."""
    address = dat_start + offset
    with open(dat_file_path, "rb") as f:
        extent = blob_extent(f, address)
        f.seek(address)
        data = f.read(max(extent, size or 0))
    if len(data) < extent:
        raise DRWAError(f"needs {extent:#x} bytes, the file holds {len(data):#x}")
    return parse_drwa(data, address=address, declared_size=size or extent)

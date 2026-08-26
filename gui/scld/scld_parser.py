"""SCLD (collision) file parser.

Thanks to vervalkon (Tomba Club).

Layout of one SCLD blob:
    header:  u16 entry_count (N)
    pointer table: (N + 1) x u16, word offsets from the START OF THE BLOB
                   (multiply by 2 for the byte offset). The last one isn't
                   a real entry - it's only used as the final entry's
                   `next_base`, unused elsewhere, and isn't always 0000.

Each of the N pointers locates one "entry" - a single collision path:
    entry header (0x14 bytes), fields are:
        xxx1, xxx2, yyy1, yyy2 : s16   - 2D bounding box for this entry
        unkn                   : u16   - alternates between 2 values around
                                          one ls/le loop; likely a side/rail
                                          tag, not a count
        ls, le                 : u8, u8  - this entry's own link id, and the
                                            link id of the entry that
                                            continues after it (see
                                            "World placement" below)
        ptr1, ptr2, ptr3, ptr4 : u16   - word offsets, relative to THIS
                                         entry's own base address (not the
                                         blob start like the outer table).
                                         Multiply by 2 and add entry base for
                                         the byte address.

    data0  [header_end   .. ptr1) : u16 order/index map, pairs like
                                     (0000,0000) (0000,0001) (0000,0002)...
    table1 [ptr1 .. ptr2)         : 8-byte records - (u16 flags, u16 index,
                                     u16 run, u16 pad). `index` is a record
                                     index into table3 and `run` is that
                                     group's length, i.e. table1 partitions
                                     table3 into groups: index[k+1] ==
                                     index[k] + run[k]. Holds for 89% of
                                     flags==0 records across every area;
                                     C0xx-flagged records interleave a
                                     second list and break the walk.
                                     Each group is one sample station along
                                     the entry - see "World placement".
    table2 [ptr2 .. ptr3)         : 16-byte records - door/crossroad object
                                     placements, referencing a segment index.
    table3 [ptr3 .. ptr4)         : 8-byte records - the elevation/path
                                     samples: (u16 kind, s16 pos, s16 elev,
                                     u16 seg_index), in walk order.
    tail   [ptr4 .. next_base)    : 3-byte records, one per distinct
                                     seg_index, padded to a word. Last two
                                     bytes are a signed vector of magnitude
                                     ~64 (a normal/tangent at 1.0 == 64);
                                     the first is a separate signed scalar.
                                     Not used for placement - it is 0 for
                                     every record of some entries, which
                                     still need placement corrections.

World placement (SCLDEntry.trace()):
        y = -record.pos
        x = yyy1 + (yyy2 - yyy1) * t
        z = xxx1 + (xxx2 - xxx1) * t
    where t is the record's fraction along the entry (see _fractions).

    t = group_index / group_count, using the table1 groups above: every
    record in one group is the SAME station and shares one t. Records do
    NOT each get their own step - a group is a station sampled several
    ways (a ground reference, then the surface(s) there), so spreading
    them out stretches the entry. Checked against two entries hand-fixed
    against real level geometry (AREA_05 entries 9 and 15): fit of the
    records that fix actually moved rises from R2 0.91 -> 0.99 and
    0.93 -> 0.99 respectively. Nothing is fitted - the grouping is read
    from the file.

    t = i / N (vervalkon's original) is what `use_table1_groups = False`
    restores. It reproduces his own OBJ export for AREA_08 exactly (max
    error 0.0, X/Z swapped by his export convention) - which is why that
    OBJ can't be used to check any of this: it is that formula's own
    output, not an independent record of the level. Where the two differ
    it is by up to ~2300 units, on 92% of AREA_08's records.

    `elevation`, `ls`, and `le` are not used by this formula. `ls`/`le`
    link entries into a loop (`le` equals another entry's own `ls`,
    usually but not always that entry's index); entries 0..9 of AREA_05
    are contiguous along z (each entry's xxx2 + 1 == the next's xxx1),
    so a loop is a corridor split into pieces, but treating it as one
    continuous walked path did not match ground truth.

    His OBJ connects every record of an entry in file order as a single
    line - polylines() matches that.

    One SCLD file's entries can span more world area than a single MDAT
    room covers, so this coordinate space does not necessarily register
    against any one MDAT room directly.
"""
import struct
from dataclasses import dataclass, field


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _s16(b, o):
    return struct.unpack_from("<h", b, o)[0]


@dataclass
class PathPoint:
    kind: int
    pos: int
    elevation: int
    seg_index: int
    record_offset: int


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

    # Class-wide so the viewer can A/B this against vervalkon's i/N.
    use_table1_groups = True

    def trace(self, reverse=False):
        """This entry's path as a list of (x, y, z) world points, one per
        table3 record, in file order. See the world-placement formula in
        this module's docstring. `reverse=True` swaps which end of the
        bounding box record index 0 lands on (x/z only, y unaffected) -
        confirmed necessary for specific entries by direct visual check
        against level geometry, but false by default since it's wrong
        for most entries (e.g. every entry in AREA_08, verified exactly
        against vervalkon's own OBJ export)."""
        n = len(self.path)
        if n == 0:
            return []
        fracs = self._fractions(reverse)
        return [self._point(p, t) for p, t in zip(self.path, fracs)]

    def polylines(self, reverse=False):
        """trace() as a single connected track covering every record in
        file order, matching vervalkon's own OBJ export (one `l` line
        through every vertex of an entry, in order). Returns a list
        containing that one run (or none, if this entry has no path)."""
        pts = self.trace(reverse)
        return [pts] if pts else []

    def group_starts(self):
        """This entry's table1 group boundaries as record indices into
        `path`. Empty if table1 doesn't describe this entry's records
        (no assets, or it doesn't start at record 0)."""
        n = len(self.path)
        bounds = sorted({a[1] for a in self.assets if a[1] < n})
        return bounds if bounds and bounds[0] == 0 else []

    def _fractions(self, reverse):
        """Fraction along the bounding box (t in x/z = a + (b-a)*t), one
        per record - see the world-placement formula in this module's
        docstring. Falls back to vervalkon's i/N when table1 doesn't
        cover this entry; note that for an entry whose groups are all
        one record long the two are identical anyway."""
        n = len(self.path)
        starts = self.group_starts() if self.use_table1_groups else []
        if starts:
            count = len(starts)
            fracs = []
            gi = 0
            for i in range(n):
                while gi + 1 < count and starts[gi + 1] <= i:
                    gi += 1
                fracs.append(gi / count)
        else:
            fracs = [i / n for i in range(n)]
        return list(reversed(fracs)) if reverse else fracs

    def _point(self, p, t):
        x = self.yyy1 + (self.yyy2 - self.yyy1) * t
        y = -p.pos
        z = self.xxx1 + (self.xxx2 - self.xxx1) * t
        return x, y, z


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

        entry.objects = [struct.unpack_from("<8H", blob, o) for o in range(p2, p3, 16)]

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

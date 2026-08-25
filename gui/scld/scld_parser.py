"""SCLD (collision) file parser.

Format reverse-engineered by vervalkon (Tomba Club). World placement
matches vervalkon's 2018 OBJ exports for AREA_04 and AREA_08 exactly.

Layout of one SCLD blob:
    header:  u16 entry_count (N)
    pointer table: (N + 1) x u16, word offsets from the START OF THE BLOB
                   (multiply by 2 for the byte offset). Last one is a 0000
                   terminator, not a real entry.

Each of the N pointers locates one "entry" - a single collision path:
    entry header (0x14 bytes), fields are:
        xxx1, xxx2, yyy1, yyy2 : s16   - 2D bounding box for this entry
        unkn                   : u16   - unknown
        ls, le                 : u8, u8  - unknown; not used by world
                                            placement
        ptr1, ptr2, ptr3, ptr4 : u16   - word offsets, relative to THIS
                                         entry's own base address (not the
                                         blob start like the outer table).
                                         Multiply by 2 and add entry base for
                                         the byte address.

    data0  [header_end   .. ptr1) : u16 order/index map, pairs like
                                     (0000,0000) (0000,0001) (0000,0002)...
    table1 [ptr1 .. ptr2)         : 8-byte records - per-segment asset/type
                                     list (u16 flags, u16 index, u16 type,
                                     u16 pad); C0xx-flagged words mark
                                     special multi-slot entries.
    table2 [ptr2 .. ptr3)         : 16-byte records - door/crossroad object
                                     placements, referencing a segment index.
    table3 [ptr3 .. ptr4)         : 8-byte records - the elevation/path
                                     samples: (u16 kind, s16 pos, s16 elev,
                                     u16 seg_index), in walk order.

World placement (SCLDEntry.trace()):
    for i, record in enumerate(entry.path), N = len(entry.path):
        t = i / N                                  # NOT i/(N-1)
        x = xxx1 + (xxx2 - xxx1) * t
        z = yyy1 + (yyy2 - yyy1) * t
        y = -record.pos
    xxx maps straight to X, yyy straight to Z (no axis swap). `pos` is
    negated directly, no unwrapping. `elevation`, `ls`, and `le` are not
    used by this formula; their meaning is unknown.

    One SCLD file's entries can span more world area than a single MDAT
    room covers, so this coordinate space does not necessarily register
    against any one MDAT room directly.
"""
import statistics
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

    def trace(self):
        """This entry's path as a list of (x, y, z) world points, one per
        table3 record, in file order. See the world-placement formula in
        this module's docstring."""
        n = len(self.path)
        if n == 0:
            return []
        return [self._point(i, p) for i, p in enumerate(self.path)]

    def polylines(self):
        """trace() split into separate connected runs wherever two
        consecutive records' `pos` differs by an extreme amount relative
        to this entry's own typical step - e.g. one record set per stair
        tread, where `pos` resets back near its start every repeat instead
        of continuing to climb. Point values are unchanged from trace();
        this only decides which consecutive points get a line drawn
        between them, so a reset doesn't draw a spike across the jump."""
        pts = self.trace()
        if len(pts) < 2:
            return [pts] if pts else []

        deltas = [abs(self.path[i].pos - self.path[i - 1].pos) for i in range(1, len(self.path))]
        typical = statistics.median(deltas) or 1
        cap = max(typical * 6, 256)

        runs = [[pts[0]]]
        for i, d in enumerate(deltas, start=1):
            if d > cap:
                runs.append([])
            runs[-1].append(pts[i])
        return [r for r in runs if r]

    def _point(self, i, p):
        t = i / len(self.path)
        x = self.xxx1 + (self.xxx2 - self.xxx1) * t
        z = self.yyy1 + (self.yyy2 - self.yyy1) * t
        y = -p.pos
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

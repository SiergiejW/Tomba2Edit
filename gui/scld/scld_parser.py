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
import bisect
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
        """trace() split into one connected run per sub-path (see
        branch_blocks). Entries that aren't split come back as a single
        run covering every record in file order, matching vervalkon's own
        OBJ export (one `l` line through every vertex, in order)."""
        pts = self.trace(reverse)
        if not pts:
            return []
        blocks = self.branch_blocks() if self.use_table1_groups else []
        if not blocks:
            return [pts]
        bounds = [s for _, s in blocks]
        edges = bounds + [len(pts)]
        runs = sorted(range(len(bounds)), key=lambda k: blocks[k][0])
        return [pts[edges[k]:edges[k + 1]] for k in runs]

    def _walk(self):
        """Each real table1 record as the (start, end) span of table3
        records it covers. The run==0 markers (0x8000) that only delimit
        sub-lists are skipped.

        A record's `index` is normally absolute, but the C0xx-flagged
        sub-lists restart theirs from 0, so those are relative to wherever
        the previous list left off - taken absolutely they orphan the
        records in between, which all collapse onto one station. So the
        walk keeps a cursor and only believes `index` when it hasn't gone
        backwards. That lands exactly on the last record for 89% of
        entries, against 65% for trusting `run` alone.

        A C0xx record's `run` is not reliable either - it reads 2 where
        the station is plainly 3 - so those end at the next anchor
        instead, which is what the records themselves say. That only
        moves entries whose C0xx run disagrees with their anchors."""
        n = len(self.path)
        if not self.path:
            return []
        anchor = self.path[0].kind
        spans = []
        cursor = 0
        for flags, index, run, _pad in self.assets:
            if run == 0:
                continue
            start = index if index >= cursor else cursor
            if start >= n:
                break
            end = start + run
            if flags & 0xC000 == 0xC000:
                nxt = next((i for i in range(start + 1, n)
                            if self.path[i].kind == anchor), None)
                if nxt is not None:
                    end = nxt
            spans.append((start, end))
            cursor = end
        return spans

    def _asset_starts(self):
        """Each table1 record's first table3 record, in table1 order, with
        None for the records _walk() skips - see there."""
        spans = iter(self._walk())
        out = []
        for _flags, _index, run, _pad in self.assets:
            out.append(next(spans, (None,))[0] if run else None)
        return out

    def group_starts(self):
        """This entry's table1 group boundaries as record indices into
        `path`. Empty if table1 doesn't describe this entry's records
        (no assets, or it doesn't start at record 0).

        table1 doesn't always account for every record: a run can end
        short of where the next one's `index` picks up. Such a stretch
        only starts a new station where it repeats this entry's own
        anchor record - the fixed reference every station opens with.
        Without an anchor it is the tail of the station before it, and
        cutting it off there splits one station across two positions.
        Both cases are common (38 gaps hold an anchor, 32 don't)."""
        n = len(self.path)
        if not self.path:
            return []
        anchor = self.path[0].kind
        starts = []
        cursor = 0
        for start, end in self._walk():
            starts.extend(i for i in range(cursor, start)
                          if self.path[i].kind == anchor)
            starts.append(start)
            cursor = end
        starts.extend(i for i in range(cursor, n)
                      if self.path[i].kind == anchor)
        return starts if starts and starts[0] == 0 else []

    @property
    def auto_reverse(self):
        """Whether this entry's records run against its bounding box, so
        record 0 belongs at the xxx2/yyy2 end. True when the box's z runs
        backwards (xxx2 < xxx1), or - for an entry with no z extent at
        all - when its x does.

        Derived from AREA_16, whose 9 entries ring a loop and whose flipped
        set was established by eye: the 4 flipped are exactly the 4 with
        xxx2 < xxx1, and z decides it even where z is the *minor* axis
        (entries 1 and 2 share a dx of +1215 and differ only in the sign
        of dz - and only entry 1 is flipped). Also matches all three
        known AREA_05 entries (14 flipped, 9 and 15 not). Known
        exception: AREA_04 entry 12, flipped by eye but with a dz of
        +63 against a dx of -447 - a near-vertical entry whose small
        positive dz may just be path thickness. Override per entry in
        the viewer where this gets one wrong."""
        dz = self.xxx2 - self.xxx1
        return dz < 0 or (dz == 0 and self.yyy2 < self.yyy1)

    def branch_blocks(self):
        """This entry's sub-paths as (branch id, first record), in file
        order. data0 is a list of (branch, table1 index) pairs terminated
        by FFFF; consecutive pairs sharing a branch are one sub-path.

        The branch id is the sub-path's place along the walked path, and
        it counts DOWN through the file - so the pieces are stored back
        to front and have to be walked by ascending id. Doing that turns
        the height profile of every multi-sub-path entry checked into a
        single monotonic ramp; in file order it resets at each boundary,
        which is the sawtooth.

        Empty unless data0 really is describing sub-paths here - about
        half of all entries reuse it as a plain counter - so each field
        is checked rather than assumed, and table1 has to agree on where
        the pieces start."""
        d0 = self.data0
        pairs = [(d0[k], d0[k + 1]) for k in range(0, len(d0) - 1, 2)]
        heads = []
        for branch, t1 in pairs:
            if branch == 0xFFFF:
                break
            if not heads or heads[-1][0] != branch:
                heads.append((branch, t1))
        if len(heads) < 2:
            return []
        ids = [b for b, _ in heads]
        if ids[-1] != 0 or any(ids[k] <= ids[k + 1] for k in range(len(ids) - 1)):
            return []
        if any(t1 >= len(self.assets) for _, t1 in heads):
            return []
        resolved = self._asset_starts()
        # A head can point at one of the run==0 markers that open a
        # sub-list rather than at the run itself - take the first real
        # record at or after it.
        starts = []
        for _, t1 in heads:
            nxt = next((s for s in resolved[t1:] if s is not None), None)
            starts.append(nxt)
        if any(s is None for s in starts):
            return []
        # table1 has to agree: every sub-path must begin on a record whose
        # flags open a run (bit 1 set, bit 3 clear). data0 alone is not
        # enough - plenty of entries reuse it as a counter and pass the
        # checks above while pointing at the middle of a run, which splits
        # the entry somewhere it doesn't divide.
        opens = {s for (flags, _i, _r, _p), s in zip(self.assets, resolved)
                 if s is not None and flags & 0x2 and not flags & 0x8}
        if not set(starts) <= opens:
            return []
        if starts[0] != 0 or starts[-1] >= len(self.path):
            return []
        if len(set(starts)) != len(starts) or starts != sorted(starts):
            return []
        # A run of ids straight down from len-1 to 0 carries no ordering:
        # that is data0 numbering the pieces off as it lists them, and the
        # file order is already the walk order. Only ids with gaps in them
        # are naming positions, and those are the entries whose profile
        # needs reordering to come out monotonic.
        if ids == list(range(len(ids) - 1, -1, -1)):
            return []
        return list(zip(ids, starts))

    def _fractions(self, reverse):
        """Fraction along the bounding box (t in x/z = a + (b-a)*t), one
        per record - see the world-placement formula in this module's
        docstring. Falls back to vervalkon's i/N when table1 doesn't
        cover this entry; note that for an entry whose groups are all
        one record long the two are identical anyway."""
        n = len(self.path)
        starts = self.group_starts() if self.use_table1_groups else []
        if not starts:
            fracs = [i / n for i in range(n)]
            return list(reversed(fracs)) if reverse else fracs

        edges = starts + [n]
        order = list(range(len(starts)))
        blocks = self.branch_blocks() if self.use_table1_groups else []
        if blocks:
            # Walk the sub-paths by ascending branch id, keeping each
            # one's own stations in file order.
            bounds = [s for _, s in blocks]
            ids = [b for b, _ in blocks]
            block_of = [bisect.bisect_right(bounds, s) - 1 for s in starts]
            order.sort(key=lambda k: (ids[block_of[k]], k))

        count = len(starts)
        fracs = [0.0] * n
        for step, k in enumerate(order):
            # Flip the station, not the record order - reversing the list
            # itself would tear records off their own group whenever
            # groups differ in length.
            g = count - 1 - step if reverse else step
            for i in range(edges[k], edges[k + 1]):
                fracs[i] = g / count
        return fracs

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

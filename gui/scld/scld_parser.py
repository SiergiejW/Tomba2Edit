"""SCLD (collision) file parser.

Thanks to vervalkon (Tomba Club).

Layout of one SCLD blob:
    header:  u16 entry_count (N)
    pointer table: (N + 1) x u16, word offsets from the start of the blob
                   (x2 for the byte offset). The last is not an entry -
                   it serves as the final entry's `next_base`.

Each of the N pointers locates one entry - a single collision path:
    entry header (0x14 bytes):
        xxx1, xxx2, yyy1, yyy2 : s16   - 2D bounding box. The corners are
                                         both inside it, so an axis of
                                         0..63 is 64 units long; every
                                         extent is tiles * 64 - 1.
        unkn                   : u16   - alternates between two values
                                         around one ls/le loop
        ls, le                 : u8    - this entry's link id, and the
                                         link id of another entry
        ptr1..ptr4             : u16   - word offsets from THIS entry's
                                         base (x2 for bytes)

    data0  [header_end .. ptr1) : (branch, table1 index) u16 pairs,
                                  terminated by FFFF. Pairs sharing a
                                  branch are one sub-path; the branch is
                                  its place along the walked path and
                                  counts down through the file. A run of
                                  branches straight down from len-1 to 0
                                  is a plain counter and carries no
                                  ordering.
    table1 [ptr1 .. ptr2)       : 8-byte records - (u16 flags, u16 index,
                                  u16 run, u16 pad). Partitions table3
                                  into groups, one group per sample
                                  station along the entry.
                                    index - first table3 record, absolute
                                            except on C0xx records, where
                                            it restarts and is relative to
                                            the previous list's end
                                    run   - records in the group; on C0xx
                                            records it is unreliable, and
                                            a run of consecutive C0xx
                                            records shares the ground up
                                            to the next ordinary index,
                                            split evenly between them
                                    flags - bit 0 closes a sub-list, bit 1
                                            opens one, both together mark
                                            a seam station written down
                                            twice; run == 0 marks a
                                            delimiter, not a group
    table2 [ptr2 .. ptr3)       : 16-byte records - (u16 kind, u16 first
                                  record, u16 count, ...). Describes the
                                  C0xx sub-elements of table3.
    table3 [ptr3 .. ptr4)       : 8-byte records - the path samples:
                                  (u16 kind, s16 pos, s16 elevation,
                                  u16 seg_index), in walk order.
                                    kind      - low nibble is a surface
                                                type; only 1, 2, 4 and 8
                                                occur
                                    pos       - depth; height is -pos
                                    elevation - how far this surface may
                                                move to the next station;
                                                0 means it does not
                                    seg_index - which stretch of level the
                                                record sits in
    tail   [ptr4 .. next_base)  : 3-byte records, one per seg_index,
                                  padded to a word. The last two bytes are
                                  a signed vector of magnitude ~64 (1.0 ==
                                  64) whose frame is unidentified; the
                                  first is a separate signed scalar. Not
                                  used.

World placement (SCLDEntry.trace()):
        y = -record.pos
        x = yyy1 + extent(yyy1, yyy2) * t
        z = xxx1 + extent(xxx1, xxx2) * t
    where extent() is the inclusive axis length and t is the record's
    fraction along the entry (see _fractions). Stations sit one 64-unit
    tile apart, within an entry and across the join to the next.

    Every record of a table1 group shares one t: a group is a single
    station sampled several ways - a ground reference, then the
    surface(s) found there. `use_table1_groups = False` restores t = i/N,
    one step per record.

    A SCLD file's entries can span more world than one MDAT room, so this
    space does not register against any single room.
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

    def trace(self, reverse=None):
        """This entry's path as a list of (x, y, z) world points, one per
        table3 record, in file order. See the world-placement formula in
        this module's docstring.

        `reverse` swaps which end of the bounding box the first station
        lands on (x/z only; y is unaffected). None, the default, lets the
        entry decide from its own header - see auto_reverse. Pass
        True/False only to override that by hand."""
        n = len(self.path)
        if n == 0:
            return []
        if reverse is None:
            reverse = self.auto_reverse
        fracs = self._fractions(reverse)
        return [self._point(p, t) for p, t in zip(self.path, fracs)]

    def polylines(self, reverse=None):
        """trace() split into one connected run per sub-path (see
        branch_blocks), each in file order. An entry with no sub-paths
        comes back as a single run over every record."""
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
        """Each table1 group as the (start, end) span of table3 records it
        covers. Records with run == 0 delimit sub-lists and are skipped.

        `index` is trusted only while it has not gone backwards; a C0xx
        sub-list restarts its own from 0, so it is relative to wherever
        the previous list ended. A C0xx record's `run` is not used: a run
        of consecutive C0xx records shares the ground up to the next
        ordinary index, split evenly between them, falling back to `run`
        where that does not divide."""
        n = len(self.path)
        if not self.path:
            return []
        anchor = self.path[0].kind
        real = [(flags, index, run) for flags, index, run, _pad in self.assets
                if run]
        spans = []
        cursor = 0
        k = 0
        while k < len(real):
            flags, index, run = real[k]
            if flags & 0xC000 != 0xC000:
                start = index if index >= cursor else cursor
                if start >= n:
                    break
                spans.append((start, start + run))
                cursor = start + run
                k += 1
                continue

            group = []
            while k < len(real) and real[k][0] & 0xC000 == 0xC000:
                group.append(real[k])
                k += 1
            start = cursor
            if start >= n:
                break
            nxt_index = real[k][1] if k < len(real) else None
            limit = nxt_index if nxt_index is not None and nxt_index >= start else n
            span = limit - start
            if span > 0 and span % len(group) == 0:
                each = span // len(group)
                for _ in group:
                    spans.append((start, start + each))
                    start += each
            else:
                for gflags, _gi, grun in group:
                    end = start + grun
                    hit = next((i for i in range(start + 1, n)
                                if self.path[i].kind == anchor), None)
                    if hit is not None and hit <= limit:
                        end = hit
                    spans.append((start, end))
                    start = end
            cursor = start
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
        """Group boundaries as record indices into `path`. Empty when
        table1 does not describe this entry's records.

        table1 need not account for every record. A stretch it leaves out
        starts a new station only where it repeats this entry's anchor -
        the fixed reference each station opens with; otherwise it is the
        tail of the station before it."""
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
        that record 0 belongs at the xxx2/yyy2 end.

        The box's z decides it, even where z is the minor axis. A box one
        tile deep in z (|dz| < 64) has no z direction to read - that is
        the path's thickness - and x decides instead."""
        dz = self.xxx2 - self.xxx1
        if abs(dz) < 64:
            return self.yyy2 < self.yyy1
        return dz < 0

    def branch_blocks(self):
        """This entry's sub-paths as (branch, first record), in file
        order, or empty when data0 is not describing sub-paths here.

        The branch is a sub-path's place along the walked path and counts
        down through the file, so the pieces are stored back to front and
        are walked by ascending branch. Each field is checked rather than
        assumed: branches must descend to 0, resolve to strictly
        increasing in-range records that table1 also opens a run on, and
        carry gaps - a plain descending counter means file order is
        already walk order."""
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
        # A run opens either on its own flags (bit 1 set, bit 3 clear) or
        # by being the first record after one of the run==0 markers that
        # close a sub-list - a C0xx sub-list starts that way and carries
        # no opening flag of its own.
        opens = set()
        after_marker = True
        for (flags, _i, run, _p), s in zip(self.assets, resolved):
            if run == 0:
                after_marker = True
                continue
            if s is not None and (after_marker
                                  or (flags & 0x2 and not flags & 0x8)):
                opens.add(s)
            after_marker = False
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

    def surfaces(self, reverse=None):
        """This entry's records regrouped into the runs that read as lines
        along it - the walkable surfaces.

        A station holds several records at one position: a ground
        reference and the surface(s) there. Joining them in file order
        stitches up and down between those heights; a surface runs the
        other way, sampled again at each station, so a run carries on to
        whichever record at the next station continues it.

        `elevation` decides which one. It bounds how far a surface may
        move between stations, and a pairing is allowed only while

            |height change| <= |elevation a| + |elevation b| + 64

        the 64 being the one tile a surface may step without saying so.
        That keeps two surfaces passing at one station from being
        swapped. Ties are settled on elevation as well, since the record
        continuing a surface carries a similar one.

        Neither `kind` nor `seg_index` groups records: both change along
        a single surface. A run that skips stations is broken there
        rather than joined across the gap.

        Returns runs of (x, y, z), each with 2+ points, ordered along the
        entry."""
        n = len(self.path)
        if n == 0:
            return []
        if reverse is None:
            reverse = self.auto_reverse
        fracs = self._fractions(reverse)
        pts = [self._point(p, t) for p, t in zip(self.path, fracs)]
        ordered = sorted(set(fracs))
        step = min((b - a for a, b in zip(ordered, ordered[1:])), default=0.0)
        gap = step * 1.75 if step else float("inf")

        by_station = {}
        for i in range(n):
            by_station.setdefault(fracs[i], []).append(i)

        runs, open_runs = [], []
        for f in sorted(by_station):
            free = list(by_station[f])
            cand = []
            for ri, (lf, run) in enumerate(open_runs):
                if f - lf > gap:
                    continue
                a = self.path[run[-1]]
                for i in by_station[f]:
                    b = self.path[i]
                    rise = abs(a.pos - b.pos)
                    if rise > abs(a.elevation) + abs(b.elevation) + 64:
                        continue
                    cand.append((rise + abs(a.elevation - b.elevation), ri, i))
            cand.sort()
            taken_run, taken_rec = set(), set()
            for _score, ri, i in cand:
                if ri in taken_run or i in taken_rec:
                    continue
                taken_run.add(ri)
                taken_rec.add(i)
                open_runs[ri] = (f, open_runs[ri][1] + [i])
                free.remove(i)
            still = []
            for ri, (lf, run) in enumerate(open_runs):
                if ri in taken_run:
                    still.append((lf, run))
                elif len(run) > 1:
                    runs.append(run)
            open_runs = still + [(f, [i]) for i in free]
        for _lf, run in open_runs:
            if len(run) > 1:
                runs.append(run)
        return [[pts[i] for i in run] for run in runs]

    def _join_stations(self):
        """Stations whose table1 record both closes one sub-list and opens
        the next (flag bits 0 and 1 together).

        Such a station is the seam itself - the same point written down
        twice, once ending one list and once starting the next - so it
        shares its place with the station after it rather than taking a
        slot of its own."""
        starts = self.group_starts()
        flags = [f for f, _i, run, _p in self.assets if run]
        return {k for k in range(len(starts))
                if k < len(flags) and flags[k] & 0x3 == 0x3}

    def _fractions(self, reverse):
        """Each record's fraction along the bounding box, one per record.

        Stations are the table1 groups, walked in branch order (see
        branch_blocks) with join stations sharing a slot. An entry whose
        sub-paths are stored back to front is itself laid down against
        its box, so walking them in order also flips it.

        Falls back to i/N per record when table1 does not cover this
        entry, or when `use_table1_groups` is off."""
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

        # Sub-paths stored back to front mean the entry is itself laid
        # down against its box, the same flip auto_reverse describes, so
        # walking them in order also reverses it.
        flip = reverse != bool(blocks)
        joins = self._join_stations()
        slot = [0] * len(starts)
        count = 0
        for step, k in enumerate(order):
            slot[k] = count
            if k not in joins or step == len(order) - 1:
                count += 1
        fracs = [0.0] * n
        for k in order:
            # Flip the station, not the record order - reversing the list
            # itself would tear records off their own group whenever
            # groups differ in length.
            g = count - 1 - slot[k] if flip else slot[k]
            for i in range(edges[k], edges[k + 1]):
                fracs[i] = g / count
        return fracs

    @staticmethod
    def _extent(a0, a1):
        """An axis's length. Both corners are inside the box, so 0..63 is
        64 units long, not 63."""
        d = a1 - a0
        if d == 0:
            return 0
        return d + (1 if d > 0 else -1)

    def _point(self, p, t):
        x = self.yyy1 + self._extent(self.yyy1, self.yyy2) * t
        y = -p.pos
        z = self.xxx1 + self._extent(self.xxx1, self.xxx2) * t
        return x, y, z


@dataclass
class SCLDFile:
    entry_count: int
    pointers: list
    entries: list

    def _walk_ends(self, entry):
        """(first-station records, last-station records, start xz, end xz)
        in walk order, or None for an empty entry."""
        n = len(entry.path)
        if n == 0:
            return None
        fracs = entry._fractions(entry.auto_reverse)
        pts = entry.trace()
        lo, hi = min(fracs), max(fracs)
        first = [i for i in range(n) if fracs[i] == lo]
        last = [i for i in range(n) if fracs[i] == hi]
        return (first, last,
                (pts[first[0]][0], pts[first[0]][2]),
                (pts[last[0]][0], pts[last[0]][2]), pts)

    def seams(self, max_gap=96.0):
        """Segments joining one entry's last station to the next entry's
        first, so a surface running past the end of its own entry reads
        as one line.

        Nothing in an entry points at whichever one continues it, and
        ls/le do not: they link entries into loops that skip a
        neighbour. Position does - an entry's walk-end lands one tile
        short of its successor's walk-start. Records are then paired
        across the seam as surfaces() pairs them along one entry, under
        the same elevation budget.

        Returns 2-point runs of (x, y, z)."""
        ends = {}
        for e in self.entries:
            w = self._walk_ends(e)
            if w:
                ends[e.index] = (e, w)

        # Nearest first, and an entry's start can only continue one other,
        # so a fork doesn't get stitched twice.
        cands = []
        for i, (ea, (fa, la, sa, na, pa)) in ends.items():
            for j, (eb, (fb, lb, sb, nb, pb)) in ends.items():
                if i == j:
                    continue
                d = ((na[0] - sb[0]) ** 2 + (na[1] - sb[1]) ** 2) ** 0.5
                if d <= max_gap:
                    cands.append((d, i, j))
        cands.sort()

        runs, used_from, used_to = [], set(), set()
        for _d, i, j in cands:
            if i in used_from or j in used_to:
                continue
            used_from.add(i)
            used_to.add(j)
            ea, (fa, la, sa, na, pa) = ends[i]
            eb, (fb, lb, sb, nb, pb) = ends[j]
            free = list(fb)
            pairs = sorted(
                ((abs(ea.path[x].pos - eb.path[y].pos), x, y)
                 for x in la for y in fb
                 if ea.path[x].kind & 0x0F == eb.path[y].kind & 0x0F),
                key=lambda t: (t[0], t[1], t[2]))
            taken_a, taken_b = set(), set()
            for _dp, x, y in pairs:
                if x in taken_a or y in taken_b:
                    continue
                taken_a.add(x)
                taken_b.add(y)
                runs.append([pa[x], pb[y]])
        return runs


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

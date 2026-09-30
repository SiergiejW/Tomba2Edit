"""SCLD planes decoded as far as the game reads them - what there is to draw.

scld_parser.py places every table3 record at its cell's sample point. That
is enough to see where records are, but not what they do. The game walks a
plane as a 2D picture - distance s along the plane's line, height y - and
this module builds that picture from the same tables:

    segment   a floor, ceiling or sloped face: one table3 record in one cell,
              running from where the plane's line enters the cell (height
              `pos`) to where it leaves (`pos + rise`)
    wall      a vertical at one cell edge, from `pos` to `pos + rise`
    junction  a lane switch. A grid cell whose flags are 0xC000-class holds
              a run of table2 records: the first is the cell's own geometry
              (a "layer" - its records were unplaced before), the rest say
              which plane Up / Down leads to (low byte of their flags), and
              between which body heights
    link      the plane reached off each end of a plane (ls / le)

Everything is game coordinates here (x, y down, z). The drawing code turns
them into viewer axes with view().

What a record's `kind` means is only what the game's own code tests:

    bit 0 floor, bit 1 ceiling, bits 2 / 3 a wall that blocks one way / the
    other along the plane (f_ResolveTerrainProbeHorizontalSurfaceBySideMask),
    0x40 a wall's foot at the cell's exit edge, 0x80 height changes across
    the cell (a ramp when it is a floor, a sloped face when it is a wall);
    0x100 a wall Grapple sticks to, 0x10 / 0x20 a ceiling Grapple sticks to
    (FUN_800696c4); the class (bits 8-11) picks footstep sounds
    (f_TriggerTombaTerrainFootstep) and, with the high nibble, terrain slides
    (f_TryStartTombaContactAction). Nothing else is named.
"""
import math
from dataclasses import dataclass, field

from formats.collision.scld_parser import CELL, WALL_SIDES, wall_foot

FLOOR, CEILING = 0x01, 0x02
WALL_A, WALL_B = 0x04, 0x08
EXIT_EDGE, SLOPE = 0x40, 0x80
GRAPPLE_WALL = 0x100
GRAPPLE_CEILING = 0x30

# Cell flags (table1, and table2 when a cell refers to it).
STEP, REDIRECT, LINKS = 0x8000, 0x4000, 0xC000
LINK_GATED, LINK_DOWN, LINK_UP, LINK_AUTO = 0x01, 0x02, 0x04, 0x08
LINK_WALK = 0x08            # bit of a junction's mode nibble: a walking switch, else a hop


# ----------------------------------------------------------------- kinds

@dataclass(frozen=True)
class KindInfo:
    role: str               # floor, ceiling, wall, slope (a sloped wall face) or other
    bits: tuple             # each set bit, in words
    grapple: str            # "", "wall", "ceiling" or "ceiling (hangs lower)"
    material: int           # class, kind bits 8-11
    hi: int                 # kind bits 12-15
    sound: str              # what Tomba's footsteps do on it, for floors


def describe_kind(kind):
    role = ("floor" if kind & FLOOR else "ceiling" if kind & CEILING
            else "slope" if kind & SLOPE and kind & WALL_SIDES
            else "wall" if kind & WALL_SIDES else "other")
    bits = []
    if kind & FLOOR:
        bits.append("floor")
    if kind & CEILING:
        bits.append("ceiling")
    if kind & WALL_A:
        bits.append("blocks one way (0x04)")
    if kind & WALL_B:
        bits.append("blocks the other way (0x08)")
    if kind & 0x10:
        bits.append("0x10")
    if kind & 0x20:
        bits.append("0x20")
    if kind & EXIT_EDGE:
        bits.append("foot at the cell's exit edge")
    if kind & SLOPE:
        bits.append("height changes across the cell")
    if kind & GRAPPLE_WALL:
        bits.append("0x100")
    material, hi = (kind >> 8) & 0xF, kind >> 12
    grapple = ""
    if role in ("wall", "slope") and kind & GRAPPLE_WALL:
        grapple = "wall"
    elif role == "ceiling" and kind & GRAPPLE_CEILING:
        grapple = "ceiling (hangs lower)" if kind & 0x20 else "ceiling"
    sound = ""
    if role == "floor":
        if material in (1, 2):
            sound = "footstep sound 2"
        elif material in (5, 6) and (hi & 7) == 0:
            sound = "footstep sound 0x8A"
        elif material == 10:
            sound = "footstep sound 0x90"
        if material == 0 and hi in (2, 3):
            sound = (sound + "; " if sound else "") + f"terrain slide (high nibble {hi})"
    return KindInfo(role, tuple(bits), grapple, material, hi, sound)


def describe_cell_flags(flags):
    """A table1 / table2 cell's flags in words."""
    if flags & LINKS == LINKS:
        out = ["junction cell"]
        if flags & LINK_GATED:
            out.append("height-gated")
        if flags & LINK_DOWN:
            out.append("Down switch")
        if flags & LINK_UP:
            out.append("Up switch")
        if flags & LINK_AUTO:
            out.append("automatic")
        return ", ".join(out)
    if flags & STEP:
        return "steps to a neighbouring cell"
    if flags & REDIRECT:
        return f"redirects to plane {flags & 0xFF}"
    out = []
    if flags & 4:
        out.append("transposed")
    if flags & 2:
        out.append("mirrored in x")
    if flags & 1:
        out.append("mirrored in z")
    out.append("profile form B" if flags & 8 else "profile form A")
    return ", ".join(out)


# ----------------------------------------------------------------- geometry

def _local(flags, x, z):
    """A canonical cell point turned into place by the cell's flags."""
    if flags & 4:
        x, z = z, x
    mirror = flags & 3
    if mirror == 2:
        x = 63 - x
    elif mirror == 1:
        z = 63 - z
    elif mirror == 3:
        x, z = 63 - x, 63 - z
    return x, z


def view(x, y, z):
    """Game (x, y down, z) in the viewers' axes - see scld_parser.SCLDEntry.trace."""
    return (z, -y, x)


@dataclass
class Slot:
    """One cell's worth of geometry: a grid cell, or a junction cell's own layer."""
    col: int
    row: int
    flags: int
    first: int
    count: int
    profile: int
    layer: bool = False         # from table2, beside a junction
    index: int = 0              # table1 (or table2) record
    ax: float = 0.0             # where the plane's line enters the cell
    az: float = 0.0
    bx: float = 0.0             # and leaves it
    bz: float = 0.0
    s_in: float = 0.0
    s_out: float = 0.0
    records: list = field(default_factory=list)     # table3 record numbers

    @property
    def s_low(self):
        return min(self.s_in, self.s_out)

    @property
    def s_high(self):
        return max(self.s_in, self.s_out)


@dataclass
class Segment:
    """A floor, ceiling or sloped face: entry to exit of one cell."""
    record: int
    kind: int
    slot: Slot
    info: KindInfo
    a: tuple                # (x, y, z) game, where the line enters the cell
    b: tuple                # where it leaves

    @property
    def role(self):
        """floor, ceiling, or slope - a sloped wall face, or a record that only has bit 0x80."""
        return self.info.role if self.info.role in ("floor", "ceiling") else "slope"

    @property
    def mid(self):
        return tuple((p + q) / 2 for p, q in zip(self.a, self.b))


@dataclass
class Wall:
    record: int
    kind: int
    slot: Slot
    info: KindInfo
    x: float
    z: float
    y_bottom: int           # game y of pos
    y_top: int              # game y of pos + rise

    @property
    def mid(self):
        return (self.x, (self.y_bottom + self.y_top) / 2, self.z)


@dataclass
class Junction:
    col: int
    row: int
    x: float
    y: float
    z: float
    direction: str          # "up" or "down" - the pad direction that takes it
    how: str                # "manual" or "automatic"
    dest: int               # plane number (one-based)
    gated: bool
    gate_base: int          # table2 height words; see gate_heights()
    gate_window: int
    mode: int               # the mode nibble; LINK_WALK set means a walking switch
    facing: bool            # cell flag 0x10
    flags: int
    slot: object = None     # the junction cell's own layer, where it has one

    @property
    def walk(self):
        return bool(self.mode & LINK_WALK)

    def gate_heights(self):
        """(low, high) body height, y up, the switch is allowed between - or
        None when the cell does not gate on height. The test is the port's
        SwitchDestination: ((base - (y - 128)) & 0xFFFF) <= window."""
        if not self.gated:
            return None
        base = self.gate_base - 0x10000 if self.gate_base & 0x8000 else self.gate_base
        return (-(base + 128), -(base - self.gate_window + 128))


@dataclass
class Redirect:
    col: int
    row: int
    x: float
    z: float
    dest: int


@dataclass
class Run:
    """Consecutive segments of one kind, joined end to end."""
    kind: int
    role: str
    segments: list = field(default_factory=list)


class PlaneGeometry:
    """One SCLD entry (plane), decoded. Built once per entry - geometry(entry)."""

    def __init__(self, entry):
        self.entry = entry
        self.number = entry.index + 1
        self.x1, self.x2, self.z1, self.z2 = entry.xxx1, entry.xxx2, entry.yyy1, entry.yyy2
        dx, dz = self.x2 - self.x1, self.z2 - self.z1
        self.length = math.hypot(dx, dz)
        self.dir = (dx / self.length, dz / self.length) if self.length else (1.0, 0.0)
        self.origin = entry.origin
        self.slots, self.segments, self.walls = [], [], []
        self.junctions, self.redirects = [], []
        self.steps = 0
        self.owner = {}            # table3 record -> its Slot
        self.points = {}           # table3 record -> (x, y, z) game, the middle of what it draws
        self.runs = []
        self._decode()
        self._join()
        self.baseline_y = self._baseline()

    # -- where things are on the plane's line

    def s_of(self, x, z):
        return (x - self.x1) * self.dir[0] + (z - self.z1) * self.dir[1]

    def point_at(self, s):
        return (self.x1 + self.dir[0] * s, self.z1 + self.dir[1] * s)

    def closest_point(self, x, z):
        """(x, z) on this plane's line nearest a point."""
        s = max(0.0, min(self.length, self.s_of(x, z)))
        return self.point_at(s)

    # -- decoding

    def _decode(self):
        e = self.entry
        for cell in e.cells():
            flags = cell.flags
            if flags & LINKS == LINKS:
                self._junction_cell(cell)
            elif flags & STEP:
                self.steps += 1
            elif flags & REDIRECT:
                ox, oz = self.origin
                self.redirects.append(Redirect(
                    cell.col, cell.row, ox + CELL * cell.col + CELL / 2,
                    oz + CELL * cell.row + CELL / 2, flags & 0xFF))
            else:
                self._add_slot(Slot(cell.col, cell.row, flags, cell.first, cell.count,
                                    cell.profile, False, cell.index))

    def _add_slot(self, slot):
        ox, oz = self.origin
        f = slot.flags
        lo, hi = slot.profile & 0xFF, slot.profile >> 8
        if f & 8:
            ax, az, bx, bz = 0, lo, 64, hi
        else:
            ax, az, bx, bz = lo, 0, 63, hi
        ax, az = _local(f, ax, az)
        bx, bz = _local(f, bx, bz)
        cx, cz = ox + CELL * slot.col, oz + CELL * slot.row
        slot.ax, slot.az, slot.bx, slot.bz = cx + ax, cz + az, cx + bx, cz + bz
        slot.s_in, slot.s_out = self.s_of(slot.ax, slot.az), self.s_of(slot.bx, slot.bz)
        self.slots.append(slot)
        path = self.entry.path
        for r in range(slot.first, min(slot.first + slot.count, len(path))):
            if r < 0 or r in self.owner:
                continue
            self.owner[r] = slot
            slot.records.append(r)
            rec = path[r]
            info = describe_kind(rec.kind)
            if info.role in ("floor", "ceiling", "slope") or rec.kind & SLOPE:
                a = (slot.ax, rec.pos, slot.az)
                b = (slot.bx, rec.pos + rec.elevation, slot.bz)
                seg = Segment(r, rec.kind, slot, info, a, b)
                self.segments.append(seg)
                self.points[r] = seg.mid
            elif info.role == "wall":
                # The wall's foot is where the game stops a probe: scld_parser.wall_foot.
                lx, lz = wall_foot(f, slot.profile, rec.kind)
                wall = Wall(r, rec.kind, slot, info, cx + lx, cz + lz,
                            rec.pos, rec.pos + rec.elevation)
                self.walls.append(wall)
                self.points[r] = wall.mid
            else:
                self.points[r] = ((slot.ax + slot.bx) / 2, rec.pos, (slot.az + slot.bz) / 2)

    def _junction_cell(self, cell):
        e = self.entry
        objects = e.objects
        # The run's first record is the cell's own geometry.
        layer = None
        if 0 <= cell.first < len(objects):
            flags, first, count, profile = objects[cell.first]
            if flags & LINKS == 0:
                layer = Slot(cell.col, cell.row, flags, first, count, profile, True, cell.first)
                self._add_slot(layer)
        ox, oz = self.origin
        x, z = ox + CELL * cell.col + CELL / 2, oz + CELL * cell.row + CELL / 2
        if layer is not None:
            x, z = (layer.ax + layer.bx) / 2, (layer.az + layer.bz) / 2
        f = cell.flags
        for direction, index, manual in (("up", cell.profile & 0xFF, f & LINK_UP),
                                         ("down", cell.profile >> 8, f & LINK_DOWN)):
            if not index:
                continue
            if not manual and not f & LINK_AUTO:
                continue
            at = cell.first + index
            if at >= len(objects):
                continue
            target = objects[at]
            mode = (f >> 4) & 15 if direction == "up" else (f >> 8) & 15
            self.junctions.append(Junction(
                cell.col, cell.row, x, 0.0, z, direction,
                "manual" if manual else "automatic", target[0] & 0xFF,
                bool(f & LINK_GATED), target[2], target[3], mode, bool(f & 0x10), f, layer))

    def _join(self):
        """Segments of one kind that meet end to end become runs (scld_lanes'
        paths in the Unity port): each continues the open run whose last knot is
        within 2 of its low end and 1 of its height."""
        def ends(seg):
            return ((self.s_of(seg.a[0], seg.a[2]), seg.a[1]),
                    (self.s_of(seg.b[0], seg.b[2]), seg.b[1]))
        pieces = sorted(self.segments, key=lambda g: g.slot.s_low)
        open_runs = []
        for seg in pieces:
            (sa, ya), (sb, yb) = ends(seg)
            low, high = ((sa, ya), (sb, yb)) if sa <= sb else ((sb, yb), (sa, ya))
            best, gap = None, 1 << 30
            for run in open_runs:
                last = run.segments[-1]
                (la, lya), (lb, lyb) = ends(last)
                ls, ly = (lb, lyb) if lb >= la else (la, lya)
                if run.kind != seg.kind or abs(ls - low[0]) > 2:
                    continue
                g = abs(ly - low[1])
                if g <= 1 and g < gap:
                    best, gap = run, g
            if best is None:
                best = Run(seg.kind, seg.info.role)
                self.runs.append(best)
            else:
                open_runs.remove(best)
            best.segments.append(seg)
            open_runs.append(best)
            open_runs = [r for r in open_runs
                         if max(ends(r.segments[-1])[0][0], ends(r.segments[-1])[1][0]) >= low[0] - 2]

    def _baseline(self):
        floors = sorted(s.a[1] for s in self.segments if s.role == "floor")
        y = floors[len(floors) // 2] if floors else 0
        for j in self.junctions:
            own = [g for g in self.segments if g.slot is j.slot and g.role == "floor"] if j.slot else []
            j.y = own[0].mid[1] if own else y
        return y

    # -- what the viewers ask

    def end_height(self, end):
        """Game y of the floor at this plane's start (0) or end (1), else its baseline."""
        floors = [s for s in self.segments if s.role == "floor"]
        if not floors:
            return self.baseline_y
        if end == 0:
            seg = min(floors, key=lambda g: g.slot.s_low)
            return seg.a[1] if seg.slot.s_in <= seg.slot.s_out else seg.b[1]
        seg = max(floors, key=lambda g: g.slot.s_high)
        return seg.b[1] if seg.slot.s_in <= seg.slot.s_out else seg.a[1]

    def counts(self):
        roles = {"floor": 0, "ceiling": 0, "slope": 0}
        for s in self.segments:
            roles[s.role] += 1
        return {**roles, "wall": len(self.walls), "junction": len(self.junctions),
                "layer": sum(1 for s in self.slots if s.layer)}

    def describe_record(self, record):
        """One table3 record in words."""
        path = self.entry.path
        if not 0 <= record < len(path):
            return f"SCLD plane {self.number}  record {record}"
        rec = path[record]
        info = describe_kind(rec.kind)
        slot = self.owner.get(record)
        facts = [info.role]
        if info.grapple:
            facts.append(f"Grapple sticks ({info.grapple})")
        if info.sound:
            facts.append(info.sound)
        text = (f"SCLD plane {self.number} (entry {self.entry.index})  record {record}  "
                f"kind 0x{rec.kind:04X} ({'; '.join(facts)})  "
                f"class {info.material}, high nibble {info.hi}  "
                f"height {-rec.pos}" + (f" to {-(rec.pos + rec.elevation)} (rise {-rec.elevation})"
                                        if rec.elevation else "")
                + f"  normal {rec.normal}")
        if slot is not None:
            text += (f"  cell ({slot.col}, {slot.row}) flags 0x{slot.flags:04X} "
                     f"({describe_cell_flags(slot.flags)}) profile 0x{slot.profile:04X}"
                     + ("  [a junction cell's own layer]" if slot.layer else ""))
        else:
            text += "  (no cell claims it)"
        return text

    def describe_junction(self, j):
        text = (f"SCLD plane {self.number} junction at cell ({j.col}, {j.row}): "
                f"{j.direction.capitalize()} ({j.how}) -> plane {j.dest}, "
                f"{'walking switch' if j.walk else 'hop'} (mode {j.mode})")
        gate = j.gate_heights()
        if gate:
            text += f", allowed with the body between height {gate[0]} and {gate[1]}"
        return text

    def describe(self):
        """The plane as a few lines."""
        e = self.entry
        c = self.counts()
        ends = lambda n: "wall" if not n else f"plane {n}"
        lines = [
            f"Plane {self.number} (entry {e.index}, base 0x{e.base:X})",
            f"Line  x {self.x1} -> {self.x2},  z {self.z1} -> {self.z2}  "
            f"(length {self.length:.0f}; gradient {e.slope:+.4f}, 0x{e.unkn:04X})",
            f"Start leads to: {ends(e.ls)};  end leads to: {ends(e.le)}",
            f"Cells {len(e.assets)} ({self.steps} step aside, {len(self.redirects)} redirect, "
            f"{sum(1 for x in e.cells() if x.flags & LINKS == LINKS)} junction), "
            f"records {len(e.path)}, table2 {len(e.objects)}",
            f"Floors {c['floor']}, ceilings {c['ceiling']}, sloped faces {c['slope']}, "
            f"walls {c['wall']}, lane switches {c['junction']}",
        ]
        for j in self.junctions:
            lines.append("  " + self.describe_junction(j))
        return lines


def geometry(entry):
    """PlaneGeometry of an entry, built once."""
    g = getattr(entry, "_geometry", None)
    if g is None:
        g = PlaneGeometry(entry)
        entry._geometry = g
    return g

"""One area, assembled: the room, its background, and what stands in it.

The three viewers this replaces each show one file. A level is all of
them at once, and the pieces come from three different places:

    the room        the area's MDAT (id 8), already in world
                    coordinates - gui/mdat/mdat.py
    the background  its BGMP (id 11), which is not geometry at all but
                    a picture drawn behind everything - gui/bgmp/
    what stands     parts of its asset-pack SMSTs, each modelled around
    in it           its own origin, put where they belong by the object
                    table in the area's overlay - functions/placement.py

An INSTANCE below is one thing on screen: the room, or one part of an
SMST standing at one place. Everything the viewer draws is an instance,
and everything the panel lists is an instance, so selecting, hiding and
moving are the same operation whatever was picked.

WHAT IS PLACED AND WHAT IS NOT

An object record says where and which way round, but not what to draw
with - see functions/placement.py. A binding read back out of a
savestate supplies that, for the areas labels/placements.json covers;
an object with no binding is still shown, as a marker at its position,
because where a level's objects are is worth seeing whether or not we
know what each one looks like yet.

A handful of an asset pack's parts need no record: they are authored in
room coordinates rather than around their own origin - AREA_04's four
water surfaces, which are the width of the harbour - and those are put
in the scene as they are. `world_placed()` is what tells them apart.
"""
import colorsys
import math
import os
import struct
from dataclasses import dataclass

import numpy as np

import gui.mdat.mdat as mdat
from functions import placement as placement_module
from gui.smst.smst_parser import parse_smst

# SDAT ids that are always the same thing in an area, whatever build.
ROOM_ID = 8
BACKGROUND_ID = 11
ASSET_PACK_ID = 12

# The marker an object with no known model is drawn as, in world units.
MARKER_SIZE = 90.0

# Colours the markers are drawn in: one per object class, so a level's
# eleven signposts read as eleven of the same thing.
GOLDEN_RATIO_CONJUGATE = 0.6180339887498949


def marker_color(kind):
    """A stand-in colour for one object class - the same golden-ratio
    hue walk gui/scld/scld_render.py and gui/bgmp/bgmp_render.py use, so
    consecutive classes land far apart on the wheel."""
    return colorsys.hsv_to_rgb((kind * GOLDEN_RATIO_CONJUGATE) % 1.0, 0.7, 1.0)


@dataclass
class Instance:
    """One thing standing in the level.

    The first block is what it is; the second is where its geometry
    landed in the scene's shared arrays, laid out to match
    gui/smst/smst_parser.SMSTGroup so the SMST viewer's buffer building
    and part-hiding work on these unchanged."""

    index: int
    role: str                       # "room", "object" or "scenery"
    label: str
    source: tuple = None            # (file id, group index), or None
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    angle: float = 0.0              # degrees about Y
    placement: object = None        # the Placement record, for an object
    # Whether the geometry is already where it belongs - see
    # world_placed(). Such a part is drawn as it is; a transform would
    # move it a second time.
    authored: bool = False

    first_vertex: int = 0
    vertex_count: int = 0
    first_face: int = 0
    face_count: int = 0
    bounds: tuple = ()
    tris: int = 0
    quads: int = 0
    size: int = 0
    offset: int = 0

    @property
    def empty(self):
        return not self.face_count

    @property
    def movable(self):
        return self.role != "room"

    @property
    def centre(self):
        if not self.bounds:
            return (self.x, self.y, self.z)
        x0, x1, y0, y1, z0, z1 = self.bounds
        return ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)

    @property
    def radius(self):
        if not self.bounds:
            return MARKER_SIZE
        x0, x1, y0, y1, z0, z1 = self.bounds
        return max(x1 - x0, y1 - y0, z1 - z0) / 2

    def matrix(self):
        """The rotation this instance is drawn with.

        Only Y turns, which is all a record carries. The sine's sign is
        flipped against the game's own matrix because the axes are: a
        packet's three coordinates are read back to front and Y negated
        (see gui/mdat/mdat.py and view_position below), and swapping X
        with Z reverses which way a turn about Y goes."""
        radians = math.radians(self.angle)
        cos, sin = math.cos(radians), math.sin(radians)
        return np.array([[cos, 0.0, -sin],
                         [0.0, 1.0, 0.0],
                         [sin, 0.0, cos]], dtype=np.float64)

    def to_record(self):
        """Write this instance's position and angle back onto its
        record, in the game's own axes - the inverse of
        view_position()."""
        if self.placement is None:
            return
        self.placement.x = int(round(self.z))
        self.placement.y = int(round(-self.y))
        self.placement.z = int(round(self.x))
        self.placement.angle = int(round(self.angle))

    def describe(self):
        where = f"({self.x:.0f}, {self.y:.0f}, {self.z:.0f})"
        if self.role == "room":
            return f"The room itself - {self.face_count} drawn triangles"
        model = (f"id {self.source[0]} group {self.source[1]}"
                 if self.source else "no model known")
        if self.placement is not None:
            return (f"{self.placement.describe()}<br>{model}, at {where}<br>"
                    f"record {self.placement.index} of table "
                    f"{self.placement.table}, at 0x{self.placement.offset:X} "
                    f"in the overlay")
        return f"{model}, at {where}"


def area_files(idx_path, chunk_index):
    """(dat_start, [(file index, id, offset, size), ...]) for one area.

    The same SDAT walk functions/idx_parser.py does when it builds the
    tree, without building one - the Level Editor is handed an area
    rather than a row."""
    with open(idx_path, "rb") as idx:
        idx.seek(chunk_index * 0x800)
        _img0, _img1, start, end, count = struct.unpack("<5I", idx.read(20))
        if not count or end <= start:
            return start, []
        pointers = struct.unpack(f"<{count}I", idx.read(count * 4))
    entries = [(v >> 24, v & 0xFFFFFF) for v in pointers]
    files = []
    for i, (file_id, offset) in enumerate(entries):
        following = entries[i + 1][1] if i + 1 < len(entries) else end - start
        files.append((i, file_id, offset, following - offset))
    return start, files


def view_position(record):
    """A placement record's (x, y, z) in the space the viewers draw in.

    The two do not agree, and neither is wrong: a packet holds its
    coordinates in an order gui/mdat/mdat.py reads back to front, with Y
    negated, so a model's X is the game's Z and its Z the game's X. The
    records are in the game's order.

    Measured rather than assumed. Against AREA_04's room, reading the
    records as they stand leaves a third of the level's objects outside
    it and a typical object 5,400 units from the nearest bit of room -
    half the level away. Swapped, 94% are inside it and the typical
    object is 187 units off the geometry, which is under one Tomba. The
    same test on AREA_06 and AREA_08 says the same thing."""
    return float(record.z), float(-record.y), float(record.x)


def world_placed(group, room_bounds):
    """Whether an asset pack's part is already standing where it goes.

    Almost every part of an SMST is modelled around its own origin, and
    something else has to say where it belongs. The exceptions are the
    ones there is only ever one of - a water surface across a whole
    harbour - which are authored in room coordinates instead.

    Which is which is measured, since nothing in the file says: a part
    that straddles the origin is modelled around it, and a part whose
    middle lands inside the room it belongs to, further from the origin
    than it is wide, is where it is because someone put it there."""
    if not group.bounds or not room_bounds:
        return False
    x0, x1, y0, y1, z0, z1 = group.bounds
    rx0, rx1, _ry0, _ry1, rz0, rz1 = room_bounds
    if x0 <= 0 <= x1 and z0 <= 0 <= z1:
        return False
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    if math.hypot(cx, cz) <= max(x1 - x0, z1 - z0) / 2:
        return False
    return rx0 <= cx <= rx1 and rz0 <= cz <= rz1


def _bounds(vertices):
    if not len(vertices):
        return ()
    array = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    low, high = array.min(axis=0), array.max(axis=0)
    return (low[0], high[0], low[1], high[1], low[2], high[2])


class LevelScene:
    """Everything one area draws, and the instances it is made of."""

    def __init__(self):
        self.chunk_index = None
        self.dat_path = None
        self.overlay_path = None
        self.dat_start = 0
        self.dat_end = 0
        self.files = []                 # (index, id, offset, size)
        self.by_id = {}                 # id -> (offset, size)
        # {file id: the parsed SMST}, filled as models are asked for.
        self.models = {}
        self.room = None
        self.placements = []
        self.bindings = {}
        self.instances = []
        self.background = None          # a BGMPFile, or None
        self.notes = []                 # what didn't load, for the panel

    # --- loading ------------------------------------------------------

    def load(self, dat_path, idx_path, chunk_index, overlay_path=None):
        """Read one area. Never raises for a missing piece - an area
        with no background, no overlay or no asset pack is still worth
        opening, and the notes say what was not there."""
        self.__init__()
        self.dat_path = dat_path
        self.chunk_index = chunk_index
        self.overlay_path = overlay_path

        dat_start, files = area_files(idx_path, chunk_index)
        self.dat_start = dat_start
        self.dat_end = dat_start + max((o + s for _i, _f, o, s in files),
                                       default=0)
        self.files = files
        self.by_id = {}
        for _index, file_id, offset, size in files:
            self.by_id.setdefault(file_id, (offset, size))

        room_entry = self.by_id.get(ROOM_ID)
        if room_entry and room_entry[1] > 0:
            try:
                self.room = mdat.exportMDAT(dat_start + room_entry[0], dat_path)
            except Exception as e:
                self.notes.append(f"the room (id {ROOM_ID}) wouldn't read: {e}")
        else:
            self.notes.append(f"this area has no room MDAT (id {ROOM_ID})")

        if overlay_path:
            self.placements = placement_module.load_placements(overlay_path)
            if not self.placements:
                self.notes.append(
                    "the overlay holds no object table - a few small areas "
                    "place nothing")
            self.bindings = placement_module.load_bindings(
                os.path.basename(overlay_path))
        else:
            self.notes.append(
                "no overlay for this area, so nothing says where its objects "
                "stand")

        self._build_instances()
        return self

    def model(self, file_id):
        """The parsed SMST with that SDAT id, or None. Cached: an area
        holds a dozen and a scene usually needs three."""
        if file_id in self.models:
            return self.models[file_id]
        entry = self.by_id.get(file_id)
        self.models[file_id] = None
        if entry and entry[1] > 0:
            offset, size = entry
            try:
                with open(self.dat_path, "rb") as f:
                    f.seek(self.dat_start + offset)
                    self.models[file_id] = parse_smst(
                        f.read(size), address=self.dat_start + offset)
            except Exception as e:
                self.notes.append(f"id {file_id} wouldn't read as an SMST: {e}")
        return self.models[file_id]

    def group(self, source):
        """(model, group) for a (file id, group index), or (None, None)."""
        if not source:
            return None, None
        model = self.model(source[0])
        groups = (model or {}).get("groups") or ()
        if not 0 <= source[1] < len(groups):
            return None, None
        return model, groups[source[1]]

    def _build_instances(self):
        instances = []
        if self.room:
            instances.append(Instance(index=0, role="room", label="Room (MDAT)"))

        room_box = _bounds(self.room["vertices"]) if self.room else ()
        used = set()
        for record in self.placements:
            source = self.bindings.get(record.key())
            if source:
                used.add(source)
            x, y, z = view_position(record)
            _model, group = self.group(source)
            instances.append(Instance(
                index=len(instances), role="object",
                label=f"{record.kind}.{record.slot}",
                source=source, x=x, y=y, z=z,
                angle=float(record.angle), placement=record,
                authored=bool(group is not None
                              and world_placed(group, room_box))))

        pack = self.model(ASSET_PACK_ID)
        for group in (pack or {}).get("groups") or ():
            if group.empty or (ASSET_PACK_ID, group.index) in used:
                continue
            if not world_placed(group, room_box):
                continue
            instances.append(Instance(
                index=len(instances), role="scenery",
                label=f"scenery {group.index}",
                source=(ASSET_PACK_ID, group.index), authored=True))
        self.instances = instances

    # --- geometry -----------------------------------------------------

    def build(self):
        """One model dict for the whole scene, in the shape
        gui/smst/smst_viewer.py draws - so the level viewer inherits its
        shaders, its palette grouping and its blending unchanged, with
        `groups` holding instances instead of a model's parts."""
        scene = {
            "vertices": [], "vertex_colors": [], "faces": [],
            "texture_coords": [], "texture_info": [],
            "tri_count": 0, "quad_count": 0, "groups": self.instances,
        }
        for instance in self.instances:
            instance.first_vertex = len(scene["vertices"])
            instance.first_face = len(scene["faces"])
            if instance.role == "room":
                self._append(scene, self.room)
                instance.tris = self.room.get("tri_count", 0)
                instance.quads = self.room.get("quad_count", 0)
            else:
                model, group = self.group(instance.source)
                if group is not None:
                    self._append(scene, model, group)
                    instance.tris, instance.quads = group.tris, group.quads
                    instance.size, instance.offset = group.size, group.offset
            instance.vertex_count = len(scene["vertices"]) - instance.first_vertex
            instance.face_count = len(scene["faces"]) - instance.first_face
            instance.bounds = _bounds(
                scene["vertices"][instance.first_vertex:])
        scene["tri_count"] = sum(i.tris for i in self.instances)
        scene["quad_count"] = sum(i.quads for i in self.instances)
        return scene

    @staticmethod
    def _append(scene, model, group=None):
        """Copy one model - or one group of it - into the scene arrays,
        renumbering its faces onto the end of what is there."""
        if not model:
            return
        first = group.first_vertex if group is not None else 0
        count = group.vertex_count if group is not None else len(model["vertices"])
        face_first = group.first_face if group is not None else 0
        face_count = (group.face_count if group is not None
                      else len(model["faces"]))
        shift = len(scene["vertices"]) - first
        scene["vertices"].extend(model["vertices"][first:first + count])
        scene["vertex_colors"].extend(model["vertex_colors"][first:first + count])
        scene["texture_coords"].extend(model["texture_coords"][first:first + count])
        for f in range(face_first, face_first + face_count):
            scene["faces"].append([v + shift for v in model["faces"][f]])
            scene["texture_info"].append(model["texture_info"][f])

    def positions(self, scene):
        """Every vertex with its instance's transform applied.

        The room goes in as it is - an MDAT is already in world
        coordinates - and each object turns about its own Y and moves to
        where its record says."""
        verts = np.array(scene["vertices"], dtype=np.float32)
        for instance in self.instances:
            if (not instance.vertex_count or instance.role == "room"
                    or instance.authored):
                continue
            at = instance.first_vertex
            block = verts[at:at + instance.vertex_count].astype(np.float64)
            moved = block @ instance.matrix().T
            moved += (instance.x, instance.y, instance.z)
            verts[at:at + instance.vertex_count] = moved.astype(np.float32)
        return verts

    def markers(self):
        """Line geometry for the objects with no model, as
        (positions, colours) - a diamond and an upright at each.

        An unbound object is still worth drawing: where a level's things
        stand is most of what this view is for, and a marker says that
        much without pretending to know what the thing looks like."""
        positions, colors = [], []
        for instance in self.instances:
            if instance.role != "object" or instance.face_count:
                continue
            x, y, z = instance.x, instance.y, instance.z
            r = MARKER_SIZE
            color = marker_color(instance.placement.kind if instance.placement
                                 else 0)
            ring = [(x - r, y, z), (x, y, z - r), (x + r, y, z), (x, y, z + r)]
            for i, point in enumerate(ring):
                positions.extend(point)
                positions.extend(ring[(i + 1) % len(ring)])
                colors.extend(color)
                colors.extend(color)
            for a, b in (((x, y - r, z), (x, y + r * 2, z)),):
                positions.extend(a)
                positions.extend(b)
                colors.extend(color)
                colors.extend(color)
        return (np.array(positions, dtype=np.float32),
                np.array(colors, dtype=np.float32))

    # --- what the panel offers ----------------------------------------

    def model_choices(self):
        """[(label, (file id, group)), ...] every part this area could
        draw an object with - the asset pack first, since that is where
        a level's props live, then whatever else is already loaded."""
        out = [("(no model - marker only)", None)]
        ids = [ASSET_PACK_ID] + sorted(
            i for i in self.by_id if i != ASSET_PACK_ID and self.models.get(i))
        for file_id in ids:
            model = self.model(file_id)
            for group in (model or {}).get("groups") or ():
                if group.empty:
                    continue
                out.append((f"id {file_id} group {group.index}  "
                            f"({group.tris}t {group.quads}q)",
                            (file_id, group.index)))
        return out

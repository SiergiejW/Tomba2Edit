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
with - see functions/placement.py. Running the object's own code
supplies that (functions/actor_sim.py), with corrections made by eye in
labels/placements.json over it; an object with no model is still shown,
as a marker at its position,
because where a level's objects are is worth seeing whether or not we
know what each one looks like yet.

A handful of an asset pack's parts need no record: they are authored in
room coordinates rather than around their own origin - AREA_04's four
water surfaces, which are the width of the harbour - and those are put
in the scene as they are. `world_placed()` is what tells them apart.
"""
import collections
import copy
import colorsys
import json
import math
import os
import re
import struct
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

import gui.mdat.mdat as mdat
from functions import format_detect
from functions import game_build
from functions import labels
from functions import handler_models
from functions import actor_models
from functions import actor_assembly
from functions import actor_sim
from functions import town_collision
from functions import environment_meshes
from functions import object_sprites
from functions import pickup_art
from functions import psx_vram
from functions import scene_cache
from functions import placement as placement_module
from gui.smst.smst_parser import parse_smst

# SDAT ids that are always the same thing in an area, whatever build.
ROOM_ID = 8
BACKGROUND_ID = 11
ASSET_PACK_ID = 12

# The chunk every area keeps loaded, holding what they all share -
# the chests are models out of it.
RESIDENT_CHUNK = 1

# The first chunk that is an area. The game numbers its areas from 0
# while the IDX numbers its chunks from here, and a couple of tables -
# the per-area sprite sequences among them - are indexed the game's way.
FIRST_AREA_CHUNK = 4

# How many drawn parts a file needs before it is worth trying to stand
# up as a character, and how far below its group count a skeleton may
# be - a model can carry a spare part or two its bones do not.
MIN_CHARACTER_PARTS = 3
SKELETON_SLACK = 3

# How many frames each actor's code runs for before its parts are read:
# enough for the update routines to pose them.
# Eight ticks is enough for most outdoor placements, but several purified
# room actors take a second eight-tick state transition before attaching their
# model. At eight, Donglin's six floating ghosts are all falsely reported as
# "draws nothing"; at sixteen they have the same complete six-part models the
# game displays.
SIM_FRAMES = 16

# How far from the actor that spawned it a child may stand and still be
# drawn as part of it; further than this it gets a row of its own - the
# harbour's sea plants belong to a seesaw but stand across the water.
CHILD_REACH = 2500.0
# A spawned child with this many parts, none from its parent's files, is a
# character of its own and gets its own row (SOP's 0x8010B2D4, 12 parts).
CHARACTER_PARTS = 8
CHARACTER_APART = 500.0

# The sprite banks a simulated actor can draw from - see
# gui/level/pickup_sprites.py.
RESIDENT_SPRITES = 0
AREA_SPRITES = 10

# Names for handlers, recovered by functions/decomp_symbols.py, if the
# decomp has been run through it.
SYMBOLS = os.path.join(
    getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "decomp", "symbols_us.json")
# What main.py answers as the events-done worker, in a built exe.
DONE_WORKER_FLAG = "--level-done-worker"
FOREST_GHOST_ROCK_CRAB = 0x8012EF54
# US retail's; another build's while it is open (functions/game_build.py).
_BUILD = game_build.Addresses(
    globals(), overlay={"FOREST_GHOST_ROCK_CRAB": "A06"},
    slots=("ROOM_ID", "BACKGROUND_ID", "ASSET_PACK_ID", "RESIDENT_SPRITES",
           "AREA_SPRITES"))

# An overlay's scene spawner, by its decomp name - what fills a room.
SPAWNER = re.compile(r"f_Spawn\w*ActorsFromPlacementTable$")

# A persistent chest's record: orientation mode 4 turns it by the byte the
# parser calls `plane`, shifted left 4 (FUN_8003fc78); an actor class with
# bit 0x80 is only drawn inside the interior its `persist` byte names
# (f_SpawnPersistentPickupPlacementTable, f_HandlePersistentChestActor).
CHEST_AUTHORED = 4
INTERIOR_FLAG = 0x80
# How far over a chest's origin its contents are shown, world units.
CHEST_CONTENTS_LIFT = 170.0

# How far outside the box round a room's instances one of its town
# collision planes may reach, in world units.
ROOM_REACH = 400.0

# A purified area's chunk runs the cursed area's overlay, 22 chunks before,
# with that area's bit set in src_PurifiedAreas.
PURIFIED_CHUNKS = range(0x1B, 0x23)
PURIFIED_OFFSET = 22                # a purified chunk less this is its cursed one
# The floor material Tomba's footsteps raise a firefly from, by area number,
# and how far above it (FUN_A04__80115978 spawns 0x28 over the surface).
FIREFLY_MATERIALS = {4: 5, 6: 8}
FIREFLY_LIFT = 0x28
# The snow-depth surface a firefly floor needs in its cell - what
# f_QueryActorTerrainSurface finds over Tomba's feet - by area number.
FIREFLY_DEPTH_MATERIAL = {4: 6}
# A floor Tomba crosses under the game's control (a ladder between planes)
# has this in its kind's low byte; no footstep runs there.
AUTO_FLOOR = 0x10
# The ground firefly's replay: how many spots it rises at in one loop, and
# the frames it stays gone between flights.
FLIGHT_SPOTS = 8
FLIGHT_GAP = 45

# What the actors run from: a fresh game, every event done
# (actor_sim.progress_bytes), or the first with the second's differences.
FRESH, EVENTS_DONE, BOTH = "fresh", "done", "both"
AFTER_EVENTS = ("⧖ only with every event done - each save byte this area's "
                "code reads set to 0xFF (actor_sim.progress_bytes)")
# actor_sim World.spawn_events; its UNPLACED.
EVENT_REACH = 256
EVENT_NOTE = ("⧖ stood up by its code in an event the simulation does not play - "
              "its handler, run alone on a bare record, placed it here")
# actor_sim Actor.discarded - A05's ice cubes once the ranch is purified.
DISCARDED_NOTE = ("its code destroys it before any frame draws it - not in the "
                  "level as it opens, so nothing of it is drawn")
# Rows of the two runs this close, in world units, are the same row; an
# assembly moved less than MOVED has not moved; a row further than
# MERGE_REACH outside every row of the first run stands nowhere real.
# A load slower than this is worth keeping on disc - see functions/scene_cache.py.
CACHE_WORTH = 1.0
# The two runs "Both" needs share nothing, so the events-done one is given a
# process of its own (gui/level/done_worker.py) and the fresh one runs here
# meanwhile. Longer than this and it is built here instead.
DONE_WAIT = 180.0
# How many classes a loading line names before it just counts the rest.
LOG_CLASSES = 6
MERGE_GRID = 16.0
MOVED = 128.0
MERGE_REACH = 2000.0

# CodeModels scans MAIN.EXE plus an overlay's call graph. Fresh and
# events-done scenes use the exact same immutable analysis, as do cursed and
# purified chunks sharing an overlay. Keep it once per process/file version.
_CODE_MODELS = {}


def _code_models(exe_path, overlay_path):
    def stamp(path):
        info = os.stat(path)
        return os.path.normcase(os.path.abspath(path)), info.st_size, info.st_mtime_ns
    key = stamp(exe_path), stamp(overlay_path)
    model = _CODE_MODELS.get(key)
    if model is None:
        model = _CODE_MODELS[key] = handler_models.CodeModels(exe_path,
                                                               overlay_path)
    return model

# Surfaces an environment drawer builds in code (functions/environment_meshes
# .py) are models of the scene's own, numbered from here.
ENVIRONMENT_ID = 0x2000
# And what an actor's draw routine put out as textured polygons, from here.
DRAWN_ID, DRAWN_END = 0x3000, 0x4000
# Game frames a second - a recorded effect clip plays a frame each.
CLIP_HZ = 30


_FILE_LABELS = None


_SLOT_LABELS = None


def file_label(content, slot=None):
    """A file's name in the tree's labels (labels/*.json, by content hash),
    less a trailing 'Model(s)' - or "".

    `slot` is (chunk, index in it): a build laid out like US retail - every
    one but the demos (functions/game_build.py) - keeps the same files in
    the same places, so a file whose bytes differ is still named by where
    it is, as the tree does (labels.LabelSet.by_slot)."""
    global _FILE_LABELS, _SLOT_LABELS
    if _FILE_LABELS is None:
        _FILE_LABELS = {}
        for label_set in labels.builtin():
            for key, entry in label_set.entries.items():
                if entry.name and key not in _FILE_LABELS:
                    _FILE_LABELS[key] = entry.name
        _SLOT_LABELS = [s for s in labels.builtin() if s.build == game_build.REFERENCE]
    name = _FILE_LABELS.get(content or "", "")
    if not name and slot is not None and game_build.current().same_layout:
        for label_set in _SLOT_LABELS:
            entry = label_set.by_slot(*slot)
            if entry is not None and entry.name:
                name = entry.name
                break
    return re.sub(r"\s+Models?$", "", name)


def interior_name(scene):
    """A room by the game's own interior number (src_Interior), which is its
    scene index less one."""
    return f"interior {scene - 1}"


@dataclass
class SceneLine:
    """A line an actor's draw routine puts out (actor_sim.capture_lines),
    in view axes."""
    owner: int                      # instance index, or None
    scene: int                      # room scene, None for the area
    a: tuple
    b: tuple
    color_a: tuple
    color_b: tuple
    blended: bool = False           # semi-transparent: drawn additively
    # Of a recorded clip of lines: shown on this frame of `period`, looping.
    frame: int = None
    period: int = None

# How much of an IDX chunk the trailer takes, at the end of it.
TRAILER_BYTES = 0x700

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


# One colour per group of like things - the list's dot, the view's marker and
# selection box all use it (instance_color). Characters and other actors
# built from their own files keep a hue per class instead.
GROUP_COLORS = {
    "area": (0.75, 0.75, 0.78),
    "chest": (1.0, 0.78, 0.2),        # chests
    "pickup": (0.3, 0.9, 1.0),        # crystals, apples: the pickup table
    "sprite": (1.0, 0.45, 0.78),      # item sprites: contents, hearts, quest items
    "asset": (0.55, 0.88, 0.4),       # parts of the area's SMST asset pack
    "effect": (0.72, 0.52, 1.0),      # geometry the game's code draws
}


def color_group(instance):
    """Which GROUP_COLORS entry an instance takes, or None for a hue per class."""
    if instance.role == "room":
        return "area"
    if instance.pickup is not None:
        return "chest" if instance.pickup.chest else "pickup"
    if instance.drawn_as_sprite:
        return "sprite"
    files = {f for f, _g in instance.sources}
    if files and all(DRAWN_ID <= f < DRAWN_END for f in files):
        return "effect"
    if files == {ASSET_PACK_ID}:
        return "asset"
    return None


def instance_color(instance):
    """(r, g, b) in 0..1 an instance is marked in, everywhere."""
    group = color_group(instance)
    return GROUP_COLORS[group] if group else marker_color(instance.marker_class)


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
    # Every (file id, group index) this object draws. Usually one, but
    # a class can name several and they are all drawn, at the same
    # place: some objects are built from parts - a pole and its flame -
    # and the ones that are really variations read better overlapping
    # than they would as a bare marker.
    sources: tuple = ()
    # Where each of those sits relative to the instance's own origin, in
    # VIEW axes, one per source. A chest's lid is lifted off its body
    # this way (see functions/pickup_art.chest_offsets); everything else
    # draws its parts on the spot.
    offsets: tuple = ()
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    angle: float = 0.0              # degrees about Y
    placement: object = None        # the Placement record, for an object
    pickup: object = None           # the Pickup record, for a pickup
    art: object = None              # its RewardArt, for a pickup
    room: int = None                # which of the scene's MDATs, for a room
    name: str = ""                  # what somebody called this model
    # Whether the geometry is already where it belongs - see
    # world_placed(). Such a part is drawn as it is; a transform would
    # move it a second time.
    authored: bool = False
    # Built by its own code out of several parts, and posed afresh from
    # wherever the instance stands - see functions/actor_assembly.py.
    assembly: object = None
    # (parent instance, which of its sprites) for a pickup riding
    # another instance's assembly.
    follow: tuple = None
    note: str = ""
    # Which room it is in - the scene index its area's spawner filled it
    # from, interior scene - 1 - or None for the area itself.
    scene: int = None
    # A sprite object's picture, drawn beside the model its code also built.
    object_sprite: bool = False
    # An actor that draws nothing, marked where it stands so it can be found.
    marker: bool = False
    # A code-drawn effect played as a flipbook: the head row carries every
    # frame's instance, in order (itself first); each other frame carries
    # (head, frame number) and has no row of its own - see record_clips.
    flip_frames: tuple = ()
    flip: tuple = None
    # Four view-space corners its picture is stretched across instead of
    # facing the camera - a kind-0x14 quad such as a rope.
    quad: tuple = None

    @property
    def timed(self):
        """Gated or waiting on progress: its label or note carries the ⧖."""
        return "⧖" in self.label or "⧖" in (self.note or "")

    # [(first vertex, count, (x, y, z)), ...] for the parts that sit off
    # the instance's origin - filled by build().
    parts: list = None
    # (first vertex, count) or None per source - filled by build().
    spans: list = None

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
    def source(self):
        """The first of this object's models - what the panel's model
        box shows and sets, since picking one there means "draw this and
        nothing else"."""
        return self.sources[0] if self.sources else None

    @source.setter
    def source(self, value):
        self.sources = (value,) if value else ()
        self.assembly = None

    @property
    def empty(self):
        return not self.face_count

    @property
    def movable(self):
        # Something spawned is where its spawner puts it.
        return self.role not in ("room", "spawned")

    @property
    def marked(self):
        """Whether it gets a marker when it has no geometry."""
        return self.movable or self.marker

    @property
    def drawn_as_sprite(self):
        """Whether the viewer hangs a picture here rather than geometry.

        A crystal has no model to build into the scene's arrays - it is
        a sprite out of the shared bank - so it is drawn its own way and
        must not get a marker on top of it."""
        if self.pickup is not None and self.pickup.chest:
            return False
        art = self.art
        return bool(art is not None and art.frames)

    @property
    def marker_class(self):
        """What its marker is coloured by - the object class for an
        object, the reward for a pickup, so a level's crystals read as
        crystals and its apples as apples."""
        if self.placement is not None:
            return self.placement.kind
        if self.pickup is not None:
            return self.pickup.art_reward
        return 0

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

    @property
    def game_position(self):
        """(x, y, z) in the game's own axes - what its records and RAM hold,
        and so what the editor shows. x/y/z are the viewers' (view_position)."""
        return (self.z, -self.y, self.x)

    @game_position.setter
    def game_position(self, point):
        self.x, self.y, self.z = view_point(point)

    def to_record(self):
        """Write this instance's position and angle back onto its
        record, in the game's own axes - the inverse of
        view_position()."""
        record = self.placement if self.placement is not None else self.pickup
        if record is None:
            return
        record.x = int(round(self.z))
        record.y = int(round(-self.y))
        record.z = int(round(self.x))
        if self.placement is not None:
            record.angle = int(round(self.angle))

    def describe(self):
        where = "({:.0f}, {:.0f}, {:.0f})".format(*self.game_position)
        if self.role == "room":
            return f"The room itself - {self.face_count} drawn triangles"
        files = {f for f, _g in self.sources}
        if len(self.sources) > 2 and len(files) == 1:
            # A whole character, which is every group of one file - too
            # many to list, and listing them says nothing anyway.
            model = f"the whole of id {self.sources[0][0]}, {len(self.sources)} parts"
        else:
            model = (", ".join(f"id {f} group {g}" for f, g in self.sources)
                     or "no model known")
        if self.assembly is not None:
            model = f"{len(self.sources)} parts"
        if self.name:
            model = f"{self.name} - {model}"
        note = f"{self.note}<br>" if self.note else ""
        if self.role == "spawned":
            what = "a sprite" if self.drawn_as_sprite else model
            return f"{note}{what}, at {where}"
        if self.placement is not None and self.art is not None:
            frames = "/".join(str(f.frame) for f in self.art.frames)
            return (f"{self.placement.describe()}<br>"
                    f"a sprite, not a model: frames {frames}"
                    f"{' looping' if self.art.loops else ''} of the area's "
                    f"own bank, at {where}<br>"
                    f"record {self.placement.index} of table "
                    f"{self.placement.table}, at 0x{self.placement.offset:X} "
                    f"in the overlay")
        if self.placement is not None:
            return (f"{self.placement.describe()}<br>{model}, at {where}<br>"
                    f"{note}"
                    f"record {self.placement.index} of table "
                    f"{self.placement.table}, at 0x{self.placement.offset:X} "
                    f"in the overlay")
        if self.pickup is not None:
            return (f"{self.pickup.describe(self.art)}<br>{model}, at {where}<br>"
                    f"record {self.pickup.index} of pickup table "
                    f"{self.pickup.table}, at 0x{self.pickup.offset:X} "
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


def trail_files(idx_path, chunk_index):
    """[(address, size), ...] for every file in an area's trailer.

    The trailer is the last 0x700 bytes of the area's IDX chunk, holding
    start/end pairs of absolute DAT addresses - the same walk
    functions/idx_parser.py does to build the NN_TRAIL folder."""
    with open(idx_path, "rb") as idx:
        idx.seek(chunk_index * 0x800 + (0x800 - TRAILER_BYTES))
        raw = idx.read(TRAILER_BYTES)
    if len(raw) < TRAILER_BYTES:
        return []
    values = struct.unpack(f"<{TRAILER_BYTES // 4}I", raw)
    return [(values[i], values[i + 1] - values[i])
            for i in range(0, len(values) - 1, 2)
            if values[i + 1] > values[i]]


def room_entries(idx_path, dat_path, chunk_index):
    """Every MDAT that makes up this area's level, as
    [(address, size, where it came from), ...].

    All of them, not the first: an area's level is often several MDATs
    that stand together in one world. The Water Temple keeps its rooms
    in the TRAIL rather than in the SDAT at all; the Ranch Area has the
    room and the flight over it; the Ranch Summit has the main level and
    the minigame. They share a SCLD between them, which is what says
    they belong in the same space.

    The SDAT ones come from gui.mdat.mdat.area_mdat_entries, which finds
    them by the 0xFFFF at the head of a drawmap rather than by id -
    theirs is usually 8 but not always. The trailer's have no id at all,
    so they are read the way the tree reads them, out of their own
    bytes."""
    out = []
    try:
        start, files = area_files(idx_path, chunk_index)
        ids = {index: file_id for index, file_id, _o, _s in files}
        found = mdat.area_mdat_entries(idx_path, dat_path, chunk_index)
    except (OSError, ValueError, struct.error):
        return []
    for index, dat_start, offset, size in sorted(
            found, key=lambda e: (ids.get(e[0]) != ROOM_ID, e[0])):
        out.append((dat_start + offset, size, f"id {ids.get(index, '?')}"))
    for address, size in trail_files(idx_path, chunk_index):
        try:
            best = format_detect.identify_at(dat_path, address, size)
        except (OSError, ValueError, struct.error):
            continue
        if best and best[0].kind == "MDAT":
            out.append((address, size, f"trail 0x{address:X}"))
    return out


# A pickup has no handler of its own - one routine draws them all and
# picks the model off the reward - so a binding covers every pickup of
# one reward at once. Keyed in the same shape a placement's is, with a
# handler of 0 to tell the two apart: a real handler is an address.
PICKUP_HANDLER = 0


def pickup_key(record):
    """What a pickup's model is looked up by."""
    return record.art_reward, int(record.apple), PICKUP_HANDLER


def instance_key(instance):
    """What an instance's model is remembered under, or None for the
    ones that aren't placed by a table - the room and the scenery."""
    if instance.placement is not None:
        return instance.placement.key()
    if instance.pickup is not None:
        return pickup_key(instance.pickup)
    return None


# A chest carries no angle of its own. The game projects it onto one of
# the area's collision planes and takes the heading from that plane's
# span - ratan2(z1 - z0, x1 - x0) over the plane descriptor's first four
# halfwords, negated (f_ApplyAreaPlaneDirectionToActor, and the
# `-DAT_1f8001a0` at the end of it). The record's `plane` byte numbers
# the planes from one, so it is one past the SCLD entry it means.
#
# Measured against savestates: exact on all fourteen of AREA_04's chests
# and on every AREA_05 chest whose actor had finished starting up.
PLANE_BASE = 1

# How far outside its own box a plane still counts as containing a
# point - the slack f_SelectAreaPlaneContainingPosition allows on each
# side when it has to find the plane rather than being told it.
PLANE_SLACK = 0x80


def plane_heading(entry):
    """The heading a plane gives whatever stands on it, in degrees."""
    across = entry.xxx2 - entry.xxx1
    along = entry.yyy2 - entry.yyy1
    if not across and not along:
        return 0.0
    return -math.degrees(math.atan2(along, across)) % 360.0


def view_offset(offset):
    """A displacement in the game's axes, in the viewers'.

    The same swap view_position() does, without the move: X and Z change
    places and Y flips."""
    x, y, z = offset
    return (z, -y, x)


# Game axes to view axes and back - its own inverse.
VIEW_AXES = np.array([[0.0, 0.0, 1.0], [0.0, -1.0, 0.0], [1.0, 0.0, 0.0]])


def view_point(point):
    """A world point in the game's axes, in the viewers'."""
    return float(point[2]), float(-point[1]), float(point[0])


def game_state(instance):
    """Where an instance stands, the way an actor holds it."""
    roll = instance.placement.angle2 if instance.placement is not None else 0
    return actor_assembly.State(
        np.array([instance.z, -instance.y, instance.x], dtype=np.float64),
        actor_assembly.degrees_to_units(instance.angle),
        actor_assembly.degrees_to_units(roll))


# How close a spawned actor must stand to a pickup record to be it.
PICKUP_MATCH = 100.0


def _at_origin(position):
    return position is None or (abs(position[0]) < 1 and abs(position[2]) < 1)


def drawn_model(polys, to_view, uv_frames=None):
    """A one-group model of captured textured polygons (actor_sim
    .textured_primitives), both windings, or None for none. Page and blend
    mode are the packet's own page word; `uv_frames` is actor_sim's
    {CLUT word: steps}, kept by CLUT address."""
    model = {"vertices": [], "vertex_colors": [], "texture_coords": [], "faces": [],
             "texture_info": [], "face_flags": [], "tri_count": 0, "quad_count": 0}
    for corners, uvs, colours, clut, blended, page_word in polys:
        address = (actor_sim.SOLID_CLUT if clut == actor_sim.SOLID_CLUT else
                   environment_meshes.clut_address(clut))
        page, blend = page_word & 0x1F, (page_word >> 5) & 3
        base = len(model["vertices"])
        for point, (u, v), colour in zip(corners, uvs, colours):
            model["vertices"].append(list(to_view(np.asarray(point, dtype=np.float64))))
            model["vertex_colors"].append(list(colour))
            model["texture_coords"].append(psx_vram.atlas_uv(u, v, page))
        info = (page, address, blended, blend)
        # A quad is triangles 0-1-2 and 1-3-2.
        triangles = ((0, 1, 2), (1, 3, 2)) if len(corners) == 4 else ((0, 1, 2),)
        for triangle in triangles:
            for winding in (triangle, triangle[::-1]):
                model["faces"].append([base + t for t in winding])
                model["texture_info"].append(info)
                model["face_flags"].append(0)
        model["quad_count" if len(corners) == 4 else "tri_count"] += 1
    if not model["faces"]:
        return None
    model["uv_frames"] = {environment_meshes.clut_address(clut): steps
                          for clut, steps in (uv_frames or {}).items()}
    model["groups"] = [SimpleNamespace(
        index=0, first_vertex=0, vertex_count=len(model["vertices"]),
        first_face=0, face_count=len(model["faces"]), tris=model["tri_count"],
        quads=model["quad_count"], size=0, offset=0, empty=False)]
    return model


def camera_facing_frames(frames, tolerance=2.0):
    """Whether captured polygons are projected sprite cards.

    Capture uses an identity camera, so a projected sprite is an XY card at
    one depth. World geometry (floors, chains, meshes) has real depth. This
    geometric test recovers fire, glare and steam as billboards without a
    handler-name exception.
    """
    polygons = [poly for frame in frames for poly in frame]
    if not polygons:
        return False
    for corners, _uvs, _colours, _clut, _blended, _page in polygons:
        points = np.asarray(corners, dtype=np.float64)
        if len(points) not in (3, 4) or np.ptp(points[:, 2]) > tolerance:
            return False
        if np.ptp(points[:, 0]) < 1 or np.ptp(points[:, 1]) < 1:
            return False
    return True


def _box(polys):
    points = np.asarray([q for poly in polys for q in poly[0]], dtype=np.float64)
    return (*points[:, :2].min(axis=0), *points[:, :2].max(axis=0))


def _near_groups(boxes, gap):
    """Indices of `boxes` (x0, y0, x1, y1) linked by coming within `gap`."""
    boxes = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    parent = list(range(len(boxes)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(boxes)):
        near = np.nonzero((boxes[i + 1:, 0] <= boxes[i, 2] + gap)
                          & (boxes[i + 1:, 2] >= boxes[i, 0] - gap)
                          & (boxes[i + 1:, 1] <= boxes[i, 3] + gap)
                          & (boxes[i + 1:, 3] >= boxes[i, 1] - gap))[0] + i + 1
        for j in near:
            a, b = root(i), root(int(j))
            if a != b:
                parent[b] = a
    groups = {}
    for i in range(len(boxes)):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values())


def family_groups(polys, families, clips, gap=1024.0):
    """[(still polygons, [family, ...])] - a bucket's still polygons and whole
    clip families, grouped by where they draw. One group if they are close."""
    units = [("poly", poly) for poly in polys] + [
        ("family", family) for family in families
        if any(frame for frame in clips[family])]
    if len(units) < 2:
        return [(polys, families)]
    boxes = [_box([what]) if kind == "poly"
             else _box([p for frame in clips[what] for p in frame])
             for kind, what in units]
    groups = _near_groups(boxes, gap)
    if len(groups) < 2:
        return [(polys, families)]
    return [([units[i][1] for i in group if units[i][0] == "poly"],
             [units[i][1] for i in group if units[i][0] == "family"])
            for group in groups]


def spatial_parts(frames, gap=1024.0):
    """`frames` split into groups whose polygons never come within `gap`
    of each other on screen, over the whole clip; [frames] if one group."""
    boxes, owners = [], []
    for f, frame in enumerate(frames):
        for p, poly in enumerate(frame):
            boxes.append(_box([poly]))
            owners.append((f, p))
    if len(boxes) < 2:
        return [frames]
    groups = _near_groups(boxes, gap)
    if len(groups) < 2:
        return [frames]
    sets = [{owners[i] for i in members} for members in groups]
    return [[[poly for p, poly in enumerate(frame) if (f, p) in members]
             for f, frame in enumerate(frames)]
            for members in sets]


def captured_blend(frames):
    """The PSX blend mode used by a captured sprite, or None."""
    modes = [((page >> 5) & 3) for frame in frames
             for _corners, _uvs, _colours, _clut, blended, page in frame
             if blended]
    return collections.Counter(modes).most_common(1)[0][0] if modes else None


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


def _start_done(dat_path, idx_path, chunk_index, overlay_path, exe_path):
    """Start the events-done run beside this one - see gui/level/done_worker.py.
    It leaves its scene in the cache; None if it could not be started."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # A built exe has no -m: main.py hands it DONE_WORKER_FLAG instead.
    command = ([sys.executable, DONE_WORKER_FLAG] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "gui.level.done_worker"])
    try:
        return subprocess.Popen(
            command + [dat_path, idx_path, str(chunk_index), overlay_path, exe_path],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return None


class LevelScene:
    """Everything one area draws, and the instances it is made of."""

    def __init__(self):
        self.chunk_index = None
        self.dat_path = None
        self.overlay_path = None
        self.overlay_data = b""
        # Which build the disc is - see functions/game_build.py.
        self.build_name = game_build.REFERENCE
        # The area's actors, run - see functions/actor_sim.py.
        self.world = None
        self._symbols = None
        self._own_symbols = None
        self.dat_start = 0
        self.dat_end = 0
        self.files = []                 # (index, id, offset, size)
        self.by_id = {}                 # id -> (offset, size)
        # {file id: the parsed SMST}, filled as models are asked for.
        self.models = {}
        # {file id: content hash} - what a name is filed under, since a
        # file id means a different thing in every area.
        self.content = {}
        # Every MDAT this area's level is made of - see room_entries().
        self.rooms = []
        self.placements = []
        self.pickups = []               # the crystals and apples
        # {reward: RewardArt} - what each pickup is and how it is
        # drawn, out of MAIN.EXE. See functions/pickup_art.py.
        self.reward_art = {}
        # {chest kind: ((file, group), ...)} - a chest's body and lid,
        # which unlike a crystal's sprite are models we can just draw.
        self.chest_models = {}
        # Where a chest's body and lid sit, already in view axes.
        self.chest_offsets = ()
        # The area's collision planes, for the chests' headings.
        self.planes = []
        # {handler: [Sequence, ...]} for the classes that are sprites
        # rather than models - see functions/object_sprites.py.
        self.sprite_classes = {}
        # MAIN.EXE, kept for the skeletons a character is stood up on.
        self.exe_path = None
        # {file id: ([(file, group), ...], offsets)} - a whole character
        # assembled, or None where the file is not one.
        self._characters = {}
        # {"12:7": name} - what things have been called by hand.
        self.model_names = {}
        # {handler: Build} for the classes the code builds whole - see
        # functions/actor_models.py.
        self.actor_builds = {}
        # {file id: (dat_start, (offset, size))} for the resident chunk,
        # which every area keeps loaded - see model().
        self.resident = {}
        # {file id: (chunk, index in it)} - where a name is looked up when
        # this build's bytes differ from the labelled one's (file_label).
        self.slots = {}
        self.bindings = {}
        # Where each binding came from - "code" or "corrected".
        self.binding_source = {}
        self.code = None
        self.instances = []
        self.background = None          # a BGMPFile, or None
        self.notes = []                 # what didn't load, for the panel
        self.progress = FRESH
        # scene -> records in its table, None for no table - see
        # actor_sim.World.run_rooms; rooms with nothing drawn still listed.
        self.room_tables = {}
        # {drawn model id: the captured polygons it was built from} - what a
        # sprite rip cuts out of VRAM (functions/sprite_rip.py).
        self.drawn_polys = {}
        # {instance index: polygon frames}. Projected sprites are converted
        # into true camera-facing billboards once the area's VRAM is present.
        self.captured_billboards = {}
        self.captured_billboard_blends = {}

    # --- loading ------------------------------------------------------

    def load(self, dat_path, idx_path, chunk_index, overlay_path=None,
             exe_path=None, progress=FRESH, publish=None):
        """Read one area. Never raises for a missing piece - an area
        with no background, no overlay or no asset pack is still worth
        opening, and the notes say what was not there."""
        self.__init__()
        self.dat_path = dat_path
        self.chunk_index = chunk_index
        self.overlay_path = overlay_path
        self.exe_path = exe_path
        self.progress = progress
        # Every address the loaders know is US retail's until this says
        # which build the disc is.
        self.build_name = game_build.use(exe_path).name

        # The same area, built from the same disc and the same code, is the
        # same scene - functions/scene_cache.py keeps it.
        name = scene_cache.key(dat_path, idx_path, chunk_index, overlay_path or "",
                               exe_path or "", progress)
        kept = scene_cache.read(name)
        if kept is not None:
            self.__dict__.update(kept)
            return self
        started = time.perf_counter()
        done_run = None
        if progress == BOTH and overlay_path and exe_path and publish is None:
            done_run = _start_done(dat_path, idx_path, chunk_index, overlay_path, exe_path)

        dat_start, files = area_files(idx_path, chunk_index)
        self.dat_start = dat_start
        self.dat_end = dat_start + max((o + s for _i, _f, o, s in files),
                                       default=0)
        self.files = files
        self.by_id = {}
        for index, file_id, offset, size in files:
            self.by_id.setdefault(file_id, (offset, size))
            self.slots.setdefault(file_id, (chunk_index, index))

        resident_start, resident = area_files(idx_path, RESIDENT_CHUNK)
        for index, file_id, offset, size in resident:
            self.resident.setdefault(file_id, (resident_start, (offset, size)))
            self.slots.setdefault(file_id, (RESIDENT_CHUNK, index))

        for address, _size, where in room_entries(idx_path, dat_path, chunk_index):
            try:
                self.rooms.append((where, mdat.exportMDAT(address, dat_path)))
            except Exception as e:
                self.notes.append(f"the MDAT at {where} wouldn't read: {e}")
        if not self.rooms:
            self.notes.append("this area has no room MDAT")

        if overlay_path:
            try:
                with open(overlay_path, "rb") as f:
                    self.overlay_data = f.read()
            except OSError:
                pass
            self.placements = placement_module.load_placements(overlay_path)
            if publish is not None:
                self.instances = [Instance(index=n, role="room",
                    label=f"Area ({where})", room=n)
                    for n, (where, _room) in enumerate(self.rooms)]
                for record in self.placements:
                    x, y, z = view_point(record.position)
                    self.instances.append(Instance(index=len(self.instances),
                        role="object", label=f"{record.kind}.{record.slot}",
                        placement=record, marker=True, x=x, y=y, z=z,
                        angle=record.angle))
                publish(self, "Room and placement markers")
            if not self.placements:
                self.notes.append(
                    "the overlay holds no object table - a few small areas "
                    "place nothing")
            self._bind(overlay_path, exe_path)
            try:
                with open(overlay_path, "rb") as f:
                    self.sprite_classes = object_sprites.by_handler(
                        f.read(), [r.handler for r in self.placements])
            except OSError:
                pass
            # The crystals and the apples are a table of their own, and
            # MAIN.EXE is what says where it is.
            if exe_path:
                self.pickups = placement_module.load_pickups(overlay_path,
                                                             exe_path)
                try:
                    with open(overlay_path, "rb") as f:
                        raw = f.read()
                    self.reward_art = pickup_art.reward_art(
                        exe_path, overlay=raw, area=self.area_index)
                    self.chest_models = pickup_art.chest_models(exe_path)
                    self.chest_offsets = tuple(
                        view_offset(o)
                        for o in pickup_art.chest_offsets(exe_path))
                except pickup_art.PickupArtError as e:
                    self.notes.append(f"couldn't read the reward table: {e}")
        else:
            self.notes.append(
                "no overlay for this area, so nothing says where its objects "
                "stand")

        # Wanted before the instances are built: a chest takes its
        # heading off the plane it stands on.
        self.planes = self._load_planes(idx_path, dat_path, chunk_index)
        self.model_names = placement_module.load_model_names()
        if overlay_path and exe_path:
            self.world = self._simulate(idx_path, publish)

        self._built_model = None
        self._build_instances()
        if progress == BOTH:
            # The scene in hand is the complete Fresh result before the Done
            # differences are merged. Keep it under its own key too; the Done
            # worker already keeps its result. One default Both load therefore
            # makes all three Progress choices instant afterwards.
            fresh_name = scene_cache.key(
                dat_path, idx_path, chunk_index, overlay_path or "",
                exe_path or "", FRESH)
            fresh_state = {k: v for k, v in self.__dict__.items()
                           if k != "world"}
            fresh_state["progress"] = FRESH
            scene_cache.write(fresh_name, fresh_state)
        if done_run is not None:
            try:
                done_run.wait(timeout=DONE_WAIT)
            except Exception:
                done_run.kill()
        if progress == BOTH and self.world is not None:
            if publish is not None:
                publish(self, "Fresh scene ready; loading completed-event variants")
            # Built beside this one, or built here now - either way it is the
            # same scene, and the cache has it if the run finished.
            self._merge(LevelScene().load(dat_path, idx_path, chunk_index,
                                          overlay_path, exe_path, EVENTS_DONE))
        if time.perf_counter() - started > CACHE_WORTH:
            scene_cache.write(name, {k: v for k, v in self.__dict__.items()
                                     if k != "world"})
        return self

    def _merge(self, done):
        """Add what `done` - this area run with every event done - stands and
        this does not: whatever appeared, moved or changed, marked ⧖."""
        self._built_model = None
        def centre(instance):
            pieces = getattr(instance.assembly, "pieces", None) or ()
            points = [p.position if hasattr(p, "position") else p[1] for p in pieces]
            if not points:
                return ()
            mean = np.mean(np.asarray(points, dtype=np.float64), axis=0)
            return tuple(int(v) for v in np.round(mean / MOVED))

        def key(instance):
            if instance.flip_frames or instance.flip is not None:
                # A recorded effect's particles are random: it is its name.
                return ("flip", instance.label.replace("⧖ ", ""), instance.scene)
            frames = tuple(f.frame for f in getattr(instance.art, "frames", None) or ())
            return (instance.role, instance.label.replace("⧖ ", ""),
                    tuple(tuple(s) for s in instance.sources
                          if not DRAWN_ID <= s[0] < DRAWN_END), instance.scene,
                    frames, centre(instance),
                    *(round(v / MERGE_GRID) for v in (instance.x, instance.y, instance.z)))

        # Where this run's rows stand, per room: a row of the other far
        # outside that took its place from a table it ran off.
        boxes = {}
        for i in self.instances:
            point = (i.x, i.y, i.z)
            if i.role == "room" or _at_origin(point):
                continue
            low, high = boxes.get(i.scene, (point, point))
            boxes[i.scene] = (tuple(map(min, low, point)), tuple(map(max, high, point)))

        def plausible(instance):
            # A record's place is authored - 16.6's temple stands at the
            # origin once the giant fish is woken.
            if instance.role in ("object", "pickup"):
                return True
            point = (instance.x, instance.y, instance.z)
            box = boxes.get(instance.scene)
            return not _at_origin(point) and (box is None or all(
                low - MERGE_REACH <= v <= high + MERGE_REACH
                for v, low, high in zip(point, *box)))

        mine = {key(i): i.index for i in self.instances}
        records = {p.key(): p for p in self.placements}
        pickups = {pickup_key(p): p for p in self.pickups}
        index, added = {}, []
        taken = set()               # done's own indices of the rows added
        for instance in done.instances:
            k = key(instance)
            if instance.flip is not None and instance.flip[0] not in taken:
                # A frame of a loop whose head this run already has: added,
                # it hung off a head that does not list it - never hidden,
                # drawn in every view with every row unticked.
                index[instance.index] = None
                continue
            if instance.role in ("room", "scenery") or k in mine or not plausible(instance):
                index[instance.index] = mine.get(k)
                continue
            taken.add(instance.index)
            index[instance.index] = instance.index = mine[k] = len(self.instances)
            self.instances.append(instance)
            added.append(instance)
        for instance in added:
            if instance.flip is not None:
                head = index.get(instance.flip[0])
                instance.flip = None if head is None else (head, instance.flip[1])
            if instance.flip_frames:
                instance.flip_frames = tuple(index[i] for i in instance.flip_frames
                                             if index.get(i) is not None)
            if instance.follow is not None:
                parent = index.get(instance.follow[0])
                instance.follow = None if parent is None else (parent, instance.follow[1])
            if instance.placement is not None:
                instance.placement = records.get(instance.placement.key(), instance.placement)
            if instance.pickup is not None:
                instance.pickup = pickups.get(pickup_key(instance.pickup), instance.pickup)
            if "⧖" not in instance.label:
                instance.label = f"⧖ {instance.label}"
            instance.note = f"{instance.note}<br>{AFTER_EVENTS}" if instance.note else AFTER_EVENTS
        new = {i.index for i in added}
        seen = {(line.scene, line.a, line.b, getattr(line, "frame", None))
                for line in self.lines}
        for line in done.lines:
            owner = index.get(line.owner) if line.owner is not None else None
            frame = getattr(line, "frame", None)
            if owner in new or (line.scene, line.a, line.b, frame) not in seen:
                self.lines.append(SceneLine(owner, line.scene, line.a, line.b,
                                            line.color_a, line.color_b, line.blended,
                                            frame, getattr(line, "period", None)))
        # Captured polygons are numbered per run: the other's move past ours.
        base = max((k + 1 for k in self.models if DRAWN_ID <= k < DRAWN_END), default=DRAWN_ID)
        for instance in added:
            if instance.sources and DRAWN_ID <= instance.sources[0][0] < DRAWN_END:
                moved = base + instance.sources[0][0] - DRAWN_ID
                self.models[moved] = done.models.get(instance.sources[0][0])
                self.drawn_polys[moved] = getattr(done, "drawn_polys", {}).get(
                    instance.sources[0][0], ())
                instance.sources = ((moved, 0),)
        for file_id, model in done.models.items():
            if not DRAWN_ID <= file_id < DRAWN_END:
                self.models.setdefault(file_id, model)
        for file_id, content in done.content.items():
            self.content.setdefault(file_id, content)
        for old, frames in getattr(done, "captured_billboards", {}).items():
            new_index = index.get(old)
            if new_index is not None:
                self.captured_billboards[new_index] = frames
                blend = getattr(done, "captured_billboard_blends", {}).get(old)
                if blend is not None:
                    self.captured_billboard_blends[new_index] = blend
        self.notes.append(f"with every event done: {len(added)} more row(s), marked ⧖")

    def _simulate(self, idx_path, publish=None):
        """Run every placed actor's own code - see functions/actor_sim.py.
        None if it cannot be run; the scene falls back on reading models
        out of the handlers."""
        number = handler_models.overlay_number(self.overlay_path)
        if number is None:
            # SOP.BIN, the intro: New Game leaves src_CurrentArea at 0.
            number = 0
        elif number < 0:
            return None
        spawner = self._scene_spawner()
        def stage(world, title):
            if publish is None:
                return
            self.world = world
            self._built_model = None
            self._build_instances()
            publish(self, title)

        try:
            return actor_sim.simulate(
                self.exe_path, self.overlay_path, self.dat_path, idx_path,
                self.chunk_index, number, self.placements,
                actor_assembly.degrees_to_units, frames=SIM_FRAMES,
                spawner=spawner, purified=self.chunk_index in PURIFIED_CHUNKS,
                chests=[p for p in self.pickups if p.chest],
                finished=(actor_sim.progress_bytes(self.overlay_data)
                          if self.progress == EVENTS_DONE and self.overlay_data else ()),
                log=self._log_actors, publish=stage if publish else None,
                ground_fireflies=self._firefly_spots)
        except Exception as e:
            if publish is not None:
                raise
            self.notes.append(f"couldn't run the objects' own code: {e}")
            return None

    def _firefly_ground(self, area_number):
        """Every spot a ground firefly can rise from, as game (x, y, z), a
        little above the floor (FUN_A04__80115978 spawns 0x28 over it).

        A footstep on the area's firefly material raises one (Kujara
        Ranch's 5, one in eight steps; Donglin's 8, one in sixteen while
        running) - but only where Tomba's feet are under the surface
        f_QueryActorTerrainSurface picks, which is the cell's last floor
        record: Kujara's 0x601 snow depth after each 0x501 ground. A floor
        with no such record (Kujara's planes 29 and 30) raises none, nor
        does one Tomba is carried over (AUTO_FLOOR - the ladder of plane 8)."""
        material = FIREFLY_MATERIALS.get(area_number)
        if material is None:
            return ()
        depth = FIREFLY_DEPTH_MATERIAL.get(area_number)
        out = []
        for entry in self.planes:
            where = dict(zip(entry.records(), entry.trace()))
            for cell in entry.cells():
                if not cell.leaf:
                    continue
                records = range(cell.first, min(cell.first + cell.count, len(entry.path)))
                floors = [r for r in records if entry.path[r].kind & 1]
                for at, r in enumerate(floors):
                    kind = entry.path[r].kind
                    if (kind >> 8) & 0xF != material or kind & AUTO_FLOOR or r not in where:
                        continue
                    if depth is not None and not any(
                            (entry.path[q].kind >> 8) & 0xF == depth
                            for q in floors[at + 1:]):
                        continue
                    vx, vy, vz = where[r]
                    # The viewers' axes back to the game's (see SCLDEntry.trace).
                    out.append((vz, -vy - FIREFLY_LIFT, vx))
        return tuple(out)

    @property
    def _firefly_spots(self):
        spots = getattr(self, "_firefly_cache", None)
        if spots is None:
            number = handler_models.overlay_number(self.overlay_path)
            spots = self._firefly_cache = (
                self._firefly_ground(number) if number is not None else ())
        return spots

    def _add_firefly_flight(self, instances, flight, spots):
        """The one ground firefly the game keeps up at a time: its recorded
        flight replayed at one spot after another, picked at random, gone
        FLIGHT_GAP frames between."""
        import random
        origin, frames = flight
        rng = random.Random(self.chunk_index)
        order, last = [], None
        for _ in range(min(FLIGHT_SPOTS, len(spots))):
            pick = rng.randrange(len(spots))
            if pick == last and len(spots) > 1:
                pick = (pick + 1) % len(spots)
            order.append(pick)
            last = pick
        out = []
        for pick in order:
            shift = [spots[pick][k] - origin[k] for k in range(3)]
            for polys in frames:
                out.append(tuple(
                    (tuple((c[0] + shift[0], c[1] + shift[1], c[2] + shift[2])
                           for c in poly[0]),) + tuple(poly[1:])
                    for poly in polys))
            out.extend([()] * FLIGHT_GAP)
        points = [view_point(spots[pick]) for pick in order]
        cx, cy, cz = np.asarray(points, dtype=np.float64).mean(axis=0)
        index = len(instances)
        label = "the area: ground firefly"
        instances.append(Instance(
            index=index, role="spawned", label=label,
            x=float(cx), y=float(cy), z=float(cz), name=label,
            note=(f"one firefly at a time, as the game keeps it: its whole "
                  f"{len(frames)}-frame flight (actor_sim.record_firefly_flight) "
                  f"replayed at {len(order)} of the {len(spots)} spots a "
                  f"footstep could raise it from, picked at random.")))
        self.captured_billboards[index] = tuple(out)
        blend = captured_blend(frames)
        if blend is not None:
            self.captured_billboard_blends[index] = blend

    def _log_actors(self, message, actors=None):
        """One line of simulate()'s progress on the console: the stage, and
        what it stood up counted by class, most first."""
        line = f"AREA_{self.chunk_index:02X} ({self.progress}): {message}"
        if actors is not None:
            live = [a for a in actors if not a.discarded]
            counts = collections.Counter(self.handler_name(a.handler) for a in live)
            top = ", ".join(f"{n}x {name}" for name, n in counts.most_common(LOG_CLASSES))
            more = len(counts) - LOG_CLASSES
            line += f" - {len(live)} actor(s)" + (f": {top}" if top else "") + (
                f", +{more} more class(es)" if more > 0 else "")
        print(line, flush=True)

    def _scene_spawner(self):
        """The overlay's scene spawner, or None: by its decomp name, else by
        what its scene controller does (actor_sim.find_scene_spawner)."""
        tag = os.path.basename(self.overlay_path or "")[:3].upper()
        try:
            with open(SYMBOLS) as f:
                names = json.load(f)
        except (OSError, ValueError):
            names = {}
        for name, (where, address) in names.items():
            if where == tag and SPAWNER.match(name):
                # The decomp's is US retail's address.
                found = game_build.current().overlay(tag, address)
                if found:
                    return found
        return (actor_sim.find_scene_spawner(self.overlay_data)
                if self.overlay_data else None)

    def handler_name(self, handler):
        """What the decomp calls a handler, or its address: the build's own
        decomp first, where it has one (decomp/symbols_jp-demo.json), else
        US retail's for the same routine."""
        if self._symbols is None:
            self._symbols, self._own_symbols = {}, {}
            own = os.path.join(os.path.dirname(SYMBOLS),
                               f"symbols_{self.build_name}.json")
            for path, into in ((SYMBOLS, self._symbols), (own, self._own_symbols)):
                try:
                    with open(path) as f:
                        for name, (tag, address) in json.load(f).items():
                            if not name.startswith("FUN_"):
                                into.setdefault((tag, address), name)
                except (OSError, ValueError):
                    pass
        tag = "MAIN" if handler < actor_sim.OVERLAY_BASE else os.path.basename(
            self.overlay_path or "")[:3].upper()
        name = self._own_symbols.get((tag, handler))
        if name:
            return name
        # US retail's decomp names US retail's addresses.
        us = game_build.current().us(handler, tag)
        return self._symbols.get((tag, us), f"0x{handler:08X}")

    def _sim_art(self, actor):
        """A simulated sprite as art the billboards can draw."""
        if not actor.frames:
            return None
        frames = tuple(pickup_art.Frame(frame=f, ticks=t) for f, t in actor.frames)
        if actor.bank == RESIDENT_SPRITES:
            # A pickup's reward art carries its recolouring; anything else
            # out of the shared bank keeps its own palette.
            reward = self.reward_art.get(actor.reward)
            if reward is not None and {f.frame for f in reward.frames} & {
                    f.frame for f in frames}:
                return reward
            # The palette its code put over the pieces' own (+0x5C).
            clut = getattr(actor, "sprite_clut", 0) or pickup_art.OWN_PALETTE
        elif actor.bank == AREA_SPRITES:
            clut = pickup_art.AREA_BANK
        else:
            return None
        return pickup_art.RewardArt(
            reward=-1, width=0, height=0, item=-1, sequence=-1, clut=clut,
            frames=frames, loops=actor.loops, name="",
            semi_transparent=actor.sprite_semi)

    def _actor_name(self, actor):
        """A handler's name - or, for a chest or an item some code stood
        up, what it is and what it gives."""
        if actor.player:
            return "Tomba (the player)"
        read = self.world.mem.read if self.world is not None else None
        handler = actor.born[0] if actor.born else actor.handler
        if read is not None and handler == actor_sim.CHEST_HANDLER:
            kind = read(actor.address + actor_sim.SLOT, 1) & 0x7F
            contents = (read(actor.address + actor_sim.CHEST_CONTENTS, 2)
                        & actor_sim.CONTENTS_MASK)
            art = self.reward_art.get(contents)
            what = placement_module.CHEST_KINDS.get(kind, f"chest kind {kind}")
            return f"{what}: {art.grants if art is not None else f'reward {contents}'}"
        if read is not None and handler == actor_sim.SECONDARY_ITEM_HANDLER:
            reward = read(actor.address + actor_sim.SLOT, 1) & 0x7F
            art = self.reward_art.get(reward)
            return art.grants if art is not None else f"item, reward {reward}"
        if isinstance(actor.family_key, tuple) and actor.family_key[:1] == ("ground-firefly",):
            return "ground firefly (rises under Tomba's feet on this floor)"
        if read is not None and handler == FOREST_GHOST_ROCK_CRAB:
            variant = (actor.born[1] if actor.born is not None
                       else read(actor.address + actor_sim.SLOT, 1))
            return (f"Forest ghost {variant + 1}" if variant < 3
                    else f"Rock Crab balance actor {variant - 2}")
        return self.handler_name(actor.handler)

    @staticmethod
    def _gate_note(actor):
        """Why an actor would not be there in a fresh game, or ""."""
        if actor.gated is not None:
            return (f"⧖ gated: its scene record's condition {actor.gated[0]} "
                    f"(argument {actor.gated[1]}) does not hold in a fresh game")
        if actor.waiting:
            return "⧖ its init waits on progress a fresh game has not made"
        return ""

    def _part_named(self, sources):
        """A character's name when its file has none: its first named part
        (the SMST viewer names parts one by one)."""
        for source in sources:
            name = self.named((source,))
            if name:
                return name
        return ""

    def _spawn_label(self, label, posed):
        """A spawned row's name: what its model is called, or which file
        it is and how big, after what made it."""
        if not posed.sources:
            return label
        files = collections.Counter(f for f, _g in posed.sources)
        file_id, _n = files.most_common(1)[0]
        named = (self.named(tuple(s for s in posed.sources if s[0] == file_id))
                 or self._part_named(posed.sources))
        where = (f"trail {file_id - actor_sim.TRAIL_ID:#x}"
                 if file_id >= actor_sim.TRAIL_ID else f"id {file_id}")
        what = named or f"{where}, {len(posed.sources)} parts"
        return f"{what} ({label})"

    def _posed(self, actors, anchor, position, yaw, name):
        """A Posed out of a group of simulated actors, or None if they
        drew nothing this scene can show."""
        pieces, owners = [], []
        discarded = [a for a in actors if a.discarded]
        for number, a in enumerate(actors):
            # A zero draw-count is as definitive for a scene-spawned actor as
            # it is for a placement actor. AREA_20's six Rock Crab balance
            # records initialise the shared ghost/crab model but hide it;
            # exposing those parts created six bogus identical "ghosts".
            if a.dead or a.discarded or a.hidden:
                continue
            for p in a.parts:
                if self.group(p.source)[1] is not None:
                    pieces.append(p)
                    owners.append((number, self.handler_name(a.handler), a.position))
        riders = []
        for a in actors:
            if a.parts or a.discarded:
                continue
            art = self._sim_art(a)
            if art is not None and not _at_origin(a.position):
                label = art.label() if art.name or art.reward >= 0 else ""
                riders.append(actor_sim.Rider(
                    label or self.handler_name(a.handler), a.reward,
                    a.position, art, a.quad))
        if not pieces and not riders and not discarded:
            return None
        spawned = len(actors) - 1
        note = (f"built by running its own code: {len(pieces)} parts"
                + (f", {spawned} spawned actor(s)" if spawned else "")
                + f"<br>handler {self.handler_name(anchor.handler)}")
        if discarded:
            note += (f"<br>{DISCARDED_NOTE}: "
                     + ", ".join(sorted({self.handler_name(a.handler) for a in discarded})))
        return actor_sim.Posed(name, note, pieces, riders, position, yaw, owners)

    @staticmethod
    def _append_pose_loop(instances, head, actor, assembly):
        """Add an actor's exact captured skeletal loop behind its head row."""
        pose_clip = getattr(actor, "pose_clip", ()) if actor is not None else ()
        if assembly is None or len(pose_clip) < 2:
            return
        animated_sources = tuple(p.source for p in pose_clip[0])
        if tuple(assembly.sources) == animated_sources:
            animated_at = tuple(range(len(assembly.pieces)))
        else:
            # A character may carry a static child model (AREA_04's actor
            # 0x80121978 is 18 animated body parts plus one attached prop).
            # Owners use actor 0 for the root passed to _posed; replace only
            # that actor's pieces and retain every attached child's pose.
            animated_at = tuple(n for n, owner in enumerate(assembly.owners)
                                if owner[0] == 0)
            if (len(animated_at) != len(animated_sources)
                    or tuple(assembly.sources[n] for n in animated_at)
                    != animated_sources):
                return
        frames = [head.index]
        for frame_number, pieces in enumerate(pose_clip):
            full_pieces = list(assembly.pieces)
            for at, piece in zip(animated_at, pieces):
                full_pieces[at] = piece
            posed = actor_sim.Posed(
                assembly.name, assembly.note, full_pieces, assembly.riders,
                assembly.origin, assembly.yaw, assembly.owners)
            if frame_number == 0:
                head.assembly = posed
                head.sources = posed.sources
                continue
            frame = copy.copy(head)
            frame.index = len(instances)
            frame.assembly = posed
            frame.sources = posed.sources
            frame.label = f"{head.label} (idle frame {frame_number})"
            frame.flip = (head.index, frame_number)
            frame.flip_frames = ()
            instances.append(frame)
            frames.append(frame.index)
        head.flip_frames = tuple(frames)
        head.note = (f"{head.note}<br>" if head.note else "") + (
            f"{len(frames)}-frame exact idle loop from its update routine")
    def built_actor(self, handler):
        """([(file, group), ...], offsets) for a class the code builds
        whole, or None.

        Every part, every offset and the file itself come out of the one
        call that makes it - see functions/actor_models.py - so this is
        preferred over standing a model up on a skeleton picked by fit."""
        build = self.actor_builds.get(handler)
        if build is None:
            return None
        model = self.model(build.file_id)
        groups = (model or {}).get("groups") or ()
        rest = actor_models.rest_offsets(build.bones)
        parts, offsets = [], []
        for index in range(min(build.parts, len(groups))):
            if groups[index].empty:
                continue
            parts.append((build.file_id, index))
            offsets.append(view_offset(rest[index]))
        if not parts:
            return None
        return tuple(parts), tuple(offsets)

    def character(self, file_id):
        """([(file, group), ...], offsets) standing one whole character
        up on its skeleton, or None if that file is not one.

        A handler attaches ONE part of an enemy - the pig's head, a
        shopkeeper's body - because the rest arrive with the animation
        that drives it. Nothing here animates, so the whole model is put
        up in its rest pose instead: every group of the file, each moved
        to its own bone's joint. That is the same rest pose the ANMP
        viewer opens on, and the offsets are in the model's own space,
        so they need no axis swap the way a chest's do.

        The area's asset pack is never a character: its groups are a
        level's props, one thing each, and standing them on a skeleton
        would pile them up."""
        if file_id in self._characters:
            return self._characters[file_id]
        self._characters[file_id] = None
        if file_id == ASSET_PACK_ID or not self.exe_path:
            return None
        model = self.model(file_id)
        groups = (model or {}).get("groups") or ()
        drawn = [g for g in groups if not g.empty]
        if len(drawn) < MIN_CHARACTER_PARTS:
            return None
        try:
            from gui.anmp import game_rest
            sources = game_rest.load_sources(self.exe_path, self.overlay_path)
            counts = list(range(max(3, len(groups) - SKELETON_SLACK),
                                len(groups) + 1))
            best = game_rest.best_for(sources, model, counts)
            if best is None:
                return None
            _label, _offset, bones, _limbs, _grade, _tried = best
            pivots = game_rest.joints(bones)
        except Exception as e:
            self.notes.append(f"id {file_id} wouldn't stand up: {e}")
            return None
        parts, offsets = [], []
        for index in range(min(len(pivots), len(groups))):
            if groups[index].empty:
                continue
            parts.append((file_id, index))
            offsets.append(tuple(float(v) for v in pivots[index]))
        if len(parts) < MIN_CHARACTER_PARTS:
            return None
        self._characters[file_id] = (tuple(parts), tuple(offsets))
        return self._characters[file_id]

    @staticmethod
    def _load_planes(idx_path, dat_path, chunk_index):
        """The area's SCLD entries, or [] - it is only wanted for the
        chests' headings, so a missing one costs nothing else."""
        try:
            from gui.scld.scld_parser import find_area_scld_location, load_scld
            where = find_area_scld_location(idx_path, chunk_index)
            if not where:
                return []
            return load_scld(dat_path, *where).entries
        except Exception:
            return []

    def chest_heading(self, record):
        """Which way a chest faces, out of the plane it stands on.

        A record whose `plane` is 0 - or past the end of the area's
        planes - is not told which one it is on, and the game looks for
        the plane whose box the chest stands in. So does this."""
        at = record.plane - PLANE_BASE
        if not 0 <= at < len(self.planes):
            at = self._plane_containing(record.x, record.z)
        if at is None:
            return 0.0
        return plane_heading(self.planes[at])

    def _plane_containing(self, x, z, slack=PLANE_SLACK):
        """Which plane's box a world point falls in, or None."""
        for at, entry in enumerate(self.planes):
            x0, x1 = sorted((entry.xxx1, entry.xxx2))
            z0, z1 = sorted((entry.yyy1, entry.yyy2))
            if (x0 - slack <= x <= x1 + slack
                    and z0 - slack <= z <= z1 + slack):
                return at
        return None

    @property
    def area_index(self):
        """This area's number the way the game counts them, which is not
        the way the IDX does - see FIRST_AREA_CHUNK."""
        if self.chunk_index is None:
            return None
        chunk = self.chunk_index
        if chunk in PURIFIED_CHUNKS:
            # The purified copy is the same area to the game (src_CurrentArea):
            # its per-area tables - A04's area-bank Potato sprite - are that one's.
            chunk -= PURIFIED_OFFSET
        return chunk - FIRST_AREA_CHUNK

    def _bind(self, overlay_path, exe_path):
        """Work out what each object is drawn with: the handler's own code,
        and over it a correction made by eye. Nothing comes from a
        savestate - what a level holds is read off the disc."""
        name = os.path.basename(overlay_path)
        self.binding_source = {}
        corrected = {key: (model,) for key, model in placement_module.load_bindings(
            name, section=placement_module.CORRECTED).items()}
        self.code_bindings = self._code_bindings(overlay_path, exe_path)
        for label, found in (("code", self.code_bindings),
                             ("corrected", corrected)):
            for key, models in found.items():
                self.bindings[key] = models
                self.binding_source[key] = label

    def _code_bindings(self, overlay_path, exe_path):
        """What the handlers' own code says - see
        functions/handler_models.py. Never fatal: a disc opened without
        a MAIN.EXE beside it just falls back on the other sources."""
        if not exe_path or not os.path.exists(exe_path):
            self.notes.append(
                "no MAIN.EXE beside this disc, so nothing says what the "
                "objects are drawn with beyond corrections made by hand")
            return {}
        try:
            self.code = _code_models(exe_path, overlay_path)
        except Exception as e:
            self.notes.append(f"couldn't read the handlers' code: {e}")
            return {}
        images = (self.code.exe, self.code.overlay)
        for handler in {r.handler for r in self.placements}:
            built = actor_models.builds_for(images, handler,
                                            self.code.file_table)
            if built:
                self.actor_builds[handler] = built[0]
        out, cache = {}, {}
        for record in self.placements:
            found = self.code.choices(record, cache)
            if found:
                out[record.key()] = tuple(found)
        return out

    def choices_for(self, instance):
        """Every model the code says this object's class can attach, or
        [] - what to offer first when a class has more than one."""
        if self.code is None or instance.placement is None:
            return []
        try:
            return self.code.choices(instance.placement)
        except Exception:
            return []

    def model(self, file_id):
        """The parsed SMST with that SDAT id, or None. Cached: an area
        holds a dozen and a scene usually needs three."""
        if file_id in self.models:
            return self.models[file_id]
        if file_id >= actor_sim.TRAIL_ID:
            return self._trail_model(file_id)
        start, entry = self.dat_start, self.by_id.get(file_id)
        if entry is None:
            # Not one of this area's own. The resident chunk is loaded
            # whatever area you are in and holds what every area shares -
            # the chests among it - so look there before giving up.
            start, entry = self.resident.get(file_id, (start, None))
        self.models[file_id] = None
        if entry and entry[1] > 0:
            offset, size = entry
            try:
                with open(self.dat_path, "rb") as f:
                    f.seek(start + offset)
                    raw = f.read(size)
                self.content[file_id] = labels.content_key(raw)
                self.models[file_id] = parse_smst(raw, address=start + offset)
            except Exception as e:
                self.notes.append(f"id {file_id} wouldn't read as an SMST: {e}")
        return self.models[file_id]

    def _trail_model(self, file_id):
        """An SMST an area loaded out of its IDX trail at run time - a room's
        NPCs - named actor_sim.TRAIL_ID + its resource index."""
        self.models[file_id] = None
        trail = self.world.trail if self.world is not None else ()
        index = file_id - actor_sim.TRAIL_ID
        if index + 1 < len(trail) and trail[index + 1] > trail[index]:
            try:
                with open(self.dat_path, "rb") as f:
                    f.seek(trail[index])
                    raw = f.read(trail[index + 1] - trail[index])
                self.content[file_id] = labels.content_key(raw)
                self.models[file_id] = parse_smst(raw, address=trail[index])
            except Exception as e:
                self.notes.append(f"trail resource {index:#x} wouldn't read as "
                                  f"an SMST: {e}")
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

    def room_bounds(self):
        """The box round every MDAT this area draws, together."""
        return _bounds([v for _where, room in self.rooms
                        for v in room["vertices"]])

    def _build_instances(self):
        self._built_model = None
        self.drawn_polys = {}
        self.captured_billboards = {}
        self.captured_billboard_blends = {}
        instances = []
        self.lines, drew = [], set()
        drawn = {}                  # (owner, scene) -> textured polygons
        stepped = {}                # (owner, scene) -> {CLUT word: UV steps}
        clipped = {}                # (owner, scene) -> {family with a clip}
        clips = self.world.clips if self.world is not None else {}
        # An effect with a clip of its own - a Seed of Strength - is its own
        # row: (owner, scene, address) -> (frames, handler name).
        actor_clips = getattr(self.world, "actor_clips", {}) if self.world is not None else {}
        solo = {}
        moving_lines = []           # (owner, scene, frames of lines, name)

        def take(actors, owner, scene):
            """The lines and polygons these actors drew, under `owner`'s row."""
            for actor in actors:
                suppressed = actor.dead or actor.discarded
                # Allocation ancestry is not ownership. A captured Capper
                # retires after creating its independent pipe-steam actor.
                # The child's own lifecycle determines whether it is drawn.
                if suppressed:
                    continue
                if id(actor) in drew or not (actor.lines or actor.polys
                                             or getattr(actor, "line_clip", None)):
                    continue
                drew.add(id(actor))
                family = self.world.family(actor) if actor.polys and clips else None
                own = actor_clips.get(actor.address) if scene is None and actor.polys else None
                if own is not None:
                    key = (owner, scene, actor.address)
                    solo[key] = (own, self._actor_name(actor))
                    drawn[key] = []
                elif family in clips and scene is None:
                    # Drawn frame by frame from its clip instead.
                    clipped.setdefault((owner, scene, None), set()).add(family)
                    drawn.setdefault((owner, scene, None), [])
                elif actor.polys:
                    drawn.setdefault((owner, scene, None), []).extend(actor.polys)
                    stepped.setdefault((owner, scene, None), {}).update(actor.uv_frames or {})
                line_clip = getattr(actor, "line_clip", None)
                if line_clip:
                    # Drawn frame by frame from its clip, on a row of its own.
                    moving_lines.append((owner, scene, line_clip,
                                         self._actor_name(actor)))
                if line_clip is not None:
                    continue            # its lines are in a clip (its spawner's)
                for a, b, color_a, color_b, blended in actor.lines:
                    self.lines.append(SceneLine(owner, scene, view_point(a),
                                                view_point(b), color_a, color_b,
                                                blended))

        for number, (where, _room) in enumerate(self.rooms):
            instances.append(Instance(
                index=len(instances), role="room",
                label=f"Area ({where})", room=number))

        room_box = self.room_bounds()
        used = set()
        assembled = []
        world = self.world
        by_record = {}
        loose = []                  # [(label, [actors])] standing on their own
        if world is not None:
            for actor in world.actors:
                if actor.record is not None:
                    by_record[id(actor.record)] = actor
            for actor in world.actors:
                # The shared workers' spawns are Tomba and the persistent
                # pickups, which the scene already draws from their tables.
                # A chest carrying its table record is drawn by its pickup row.
                # What a discarded actor let go stands on its own: a purified
                # ranch's ice cube is gone, the item it held is not.
                parent = (world.by_address.get(actor.spawner)
                          if actor.spawner is not None else None)
                if (not (actor.dead or actor.discarded)
                        and actor.record is None
                        and (actor.spawner is None
                             or (parent is not None and parent.discarded))
                        and actor.pickup is None
                        and actor.worker not in actor_sim.SHARED_WORKERS):
                    # A child built of other files, standing apart, is a
                    # character of its own (SOP: the intro Tomba's script
                    # brings 0x8010B2D4 on) - not a creature frozen in its block.
                    tree = actor_sim.subtree(world, actor)
                    own = {p.source[0] for p in actor.parts}

                    def near_head(a):
                        if a is actor or a.position is None or actor.position is None:
                            return True
                        return not (len(a.parts) >= CHARACTER_PARTS
                                    and not own & {p.source[0] for p in a.parts}
                                    and np.linalg.norm(a.position - actor.position)
                                    > CHARACTER_APART)
                    near = [a for a in tree if near_head(a)]
                    loose.append((f"scene: {self._actor_name(actor)}", near))
                    loose.extend((f"scene: {self._actor_name(a)}",
                                  [x for x in actor_sim.subtree(world, a)
                                   if x not in near])
                                 for a in tree if a not in near
                                 and self.world.by_address.get(a.spawner) in near)
        for record in self.placements:
            states = self.sprite_classes.get(record.handler) or ()
            actor = by_record.get(id(record))
            # A class drawn as a sprite has no model, and whatever
            # handler_models found for it was something else the handler
            # touched - so it is dropped rather than drawn. The sprite its
            # own code started wins over the class's first state: the tables
            # go by handler, the code by slot.
            class_art = object_sprites.first_state(states)
            # The class's states belong to all its slots: once this one's
            # code has run, only what it started is its sprite.
            # So does one whose code chose a sprite bank but has not started
            # a sequence yet - 15.6's fish, waiting to jump.
            guess_class = (actor is None or not actor.ran or actor.waiting
                           or (not actor.parts and actor.sprite_bank is not None))
            art = ((self._sim_art(actor) if actor is not None else None)
                   or (object_sprites.as_art(class_art)
                       if class_art and guess_class else None))
            sources, offsets = (), ()
            assembly = None
            kept_sprite = None
            nonvisual = ((record.handler, record.slot)
                         in actor_sim.NONVISUAL_PLACEMENTS)
            hidden = actor is not None and (actor.hidden or actor.discarded
                                             or nonvisual)
            # The sprite tables go by handler, and one handler can give a
            # slot a model instead (f_UpdateDonglinInteriorQuestObjectActor,
            # slot 10) - what the code attached decides.
            if actor is not None and (not art or actor.parts) and not hidden:
                tree = actor_sim.subtree(world, actor)
                near = [a for a in tree if a is actor or np.linalg.norm(
                    a.position - actor.position) <= CHILD_REACH]
                loose.extend((f"{record.kind}.{record.slot} spawned: "
                              f"{self._actor_name(a)}", [a])
                             for a in tree if a not in near)
                assembly = self._posed(
                    near, actor, np.array(record.position, dtype=np.float64),
                    actor_assembly.degrees_to_units(record.angle),
                    self.handler_name(record.handler))
                if assembly is not None and not assembly.sources:
                    assembly = None
                if assembly is not None and art:
                    kept_sprite, art = art, None
            elif actor is not None and not hidden:
                # A sprite's own spawns - 52.0's two clouds - stand apart.
                loose.extend((f"{record.kind}.{record.slot} spawned: "
                              f"{self._actor_name(a)}", [a])
                             for a in actor_sim.subtree(world, actor) if a is not actor)
            if assembly is None and not art and not hidden:
                assembly = actor_assembly.assemble(self.overlay_data, record)
                if assembly is not None and not self._loads(assembly.sources):
                    assembly = None
            # Its own code ran, allocated a part and left it without a
            # model: it draws nothing, and whatever the handler's
            # immediates name is the model it loads and then clears.
            # So is a guess of anything else, once its code has run to a
            # standstill: a blank part draws nothing whatever was named
            # (A05 68.3's door and boulder were two such immediates).
            guessed = self.bindings.get(record.key()) or ()
            invisible = (actor is not None and not actor.parts
                         and actor.blank and not actor.frames and guessed
                         and self.binding_source.get(record.key()) == "code"
                         and ({tuple(g) for g in guessed} <= set(actor.loaded)
                              or (actor.ran and not actor.dead and not actor.waiting)))
            # Ran, attached nothing, and set a draw routine of its own (+0x18):
            # it draws itself - steam, sparks - so a model its handler's
            # immediates named is not it.
            if (actor is not None and not actor.parts and not actor.frames
                    and not actor.dead and guessed
                    and self.binding_source.get(record.key()) == "code"
                    and world._code(world.mem.read(actor.address + actor_sim.DRAW, 4))):
                invisible = True
            # Its code set its draw count (+0x08) to 0 - a door frame only
            # drawn from inside, a warp - so no part of it is drawn.
            if hidden:
                invisible = True
            gate = self._gate_note(actor) if actor is not None and not actor.parts else ""
            if assembly is not None:
                sources = assembly.sources
            elif invisible:
                sources = ()
            elif not art:
                built = self.built_actor(record.handler)
                if built is not None:
                    sources, offsets = built
                else:
                    sources = self.bindings.get(record.key()) or ()
                    if len(set(f for f, _g in sources)) == 1:
                        whole = self.character(sources[0][0])
                        if whole is not None:
                            sources, offsets = whole
            used.update(sources)
            x, y, z = view_position(record)
            _model, group = self.group(sources[0] if sources else None)
            note = assembly.note if assembly is not None else ""
            if hidden:
                if nonvisual:
                    note = ("interior-transition/warp trigger: its temporary "
                            "initializer model is not level geometry")
                else:
                    note = (DISCARDED_NOTE if actor.discarded else
                            "its code sets its draw count (+0x08) to 0: nothing of it is drawn")
            if gate:
                note = f"{note}<br>{gate}" if note else gate
            called = self.file_named(sources)
            instances.append(Instance(
                index=len(instances), role="object",
                label=(f"{'⧖ ' if gate else ''}{record.kind}.{record.slot}"
                       + (f" {called}" if called else "")),
                sources=tuple(sources), offsets=offsets, x=x, y=y, z=z,
                name=(assembly.name if assembly is not None
                      else self.named(sources)),
                angle=float(record.angle), placement=record,
                art=art, assembly=assembly, note=note,
                authored=bool(assembly is None and group is not None
                              and world_placed(group, room_box))))
            head = instances[-1]
            self._append_pose_loop(instances, head, actor, assembly)
            if assembly is not None:
                assembled.append(head)
            if actor is not None:
                take(actor_sim.subtree(world, actor), head.index, None)
            if kept_sprite:
                # Its class's sprite state still shows, beside the model.
                instances.append(Instance(
                    index=len(instances), role="spawned",
                    label=f"{record.kind}.{record.slot} sprite",
                    art=kept_sprite, x=x, y=y, z=z,
                    object_sprite=True,
                    note=f"the sprite state of {record.kind}.{record.slot}'s class"))

        chests = {id(a.pickup): a for a in (world.actors if world is not None else ())
                  if a.pickup is not None}
        for record in self.pickups:
            sources = self.bindings.get(pickup_key(record)) or ()
            x, y, z = view_position(record)
            art = self.reward_art.get(
                record.contents if record.chest else record.art_reward)
            offsets = ()
            heading = 0.0
            scene = None
            assembly = None
            actor = None
            if record.chest:
                # Every 2.5-D chest takes its yaw from the collision plane.
                # This must happen even when simulation supplied a posed body;
                # previously that successful path accidentally left yaw at 0.
                heading = self.chest_heading(record)
                if record.type & INTERIOR_FLAG:
                    scene = record.persist + 1
                # f_HandlePersistentChestActor, run: body and lid where its
                # behaviour byte stood them - on the ground, turned to the
                # plane, or turned by the record.
                actor = chests.get(id(record))
                if actor is not None and actor.parts and not sources:
                    # Its parts already stand where its code put them and turned
                    # the way it turned them (+0x56): the pose is anchored
                    # there, so the row's own angle adds nothing until edited.
                    # Anchored at the record with yaw 0 it was turned twice -
                    # about a point it may have slid 3000 units away from.
                    yaw = actor_sim.s16(self.world.mem.read(
                        actor.address + actor_sim.TURN + 2, 2))
                    turned = round((yaw * 360.0 / 4096.0) % 360.0)
                    anchor = (actor.position if actor.position is not None
                              else np.array(record.position, dtype=np.float64))
                    # The turn its parts were read with: sometimes already
                    # the actor's, sometimes not yet (AREA_0A #46's are
                    # square while +0x56 says 38) - the pose adds the rest.
                    m = actor.parts[0].matrix
                    baked = round(math.degrees(math.atan2(m[0][2], m[0][0])) % 360.0)
                    assembly = self._posed([actor], actor, anchor,
                                           actor_assembly.degrees_to_units(baked),
                                           record.name(art))
                    if assembly is not None and not assembly.sources:
                        assembly = None
                    if assembly is not None:
                        heading = float(turned)
                        x, y, z = view_point(np.asarray(anchor, dtype=np.float64))
                if assembly is not None:
                    sources = assembly.sources
                else:
                    if not sources:
                        sources = self.chest_models.get(
                            record.reward & placement_module.PICKUP_REWARD_MASK, ())
                        offsets = self.chest_offsets
            used.update(sources)
            _model, group = self.group(sources[0] if sources else None)
            instances.append(Instance(
                index=len(instances), role="pickup",
                label=record.name(art), art=art, sources=tuple(sources),
                offsets=offsets, name=self.named(sources),
                x=x, y=y, z=z, pickup=record, assembly=assembly,
                angle=float(heading), scene=scene,
                note=assembly.note if assembly is not None else "",
                authored=bool(assembly is None and group is not None
                              and world_placed(group, room_box))))
            if record.chest and art is not None:
                # What it holds, over its lid - seen once it is opened.
                # f_UpdatePersistentChestOpeningAndSpawnDrop hands the drop the
                # chest's item id as its object flags; with any set,
                # f_HandleOverworldItemPickup builds a model (the Grapple) out
                # of the reward's palette word (file) and sequence (group),
                # else a sprite (Potato X3).
                flags = (self.world.mem.read(actor.address + actor_sim.ITEM_ID, 1) & 0x7F
                         if actor is not None and self.world is not None else 0)
                drop = (art.clut & 0x7FFF, art.sequence)
                model = bool(flags) and self.group(drop)[1] is not None
                ground = (actor.position if actor is not None and actor.position is not None
                          else np.array(record.position, dtype=np.float64))
                cx, cy, cz = view_point(np.array(
                    [ground[0], ground[1] - CHEST_CONTENTS_LIFT, ground[2]]))
                if model:
                    instances.append(Instance(
                        index=len(instances), role="spawned",
                        label=f"{record.name(art)}: contents", sources=(drop,),
                        name=self.named((drop,)), x=cx, y=cy, z=cz, scene=scene,
                        note=f"reward {record.contents}, what the chest gives when "
                             "opened - a model, file and group from its reward entry"))
                elif art.frames:
                    instances.append(Instance(
                        index=len(instances), role="spawned",
                        label=f"{record.name(art)}: contents", art=art,
                        x=cx, y=cy, z=cz, scene=scene,
                        note=f"reward {record.contents}, what the chest gives when opened"))

        # What the assembled objects spawn: pickups that ride them, and
        # props that stand wherever their spawner's table says.
        for parent in assembled:
            spawner = f"spawned by {parent.label} ({parent.assembly.name})"
            state = game_state(parent)
            for number, sprite in enumerate(parent.assembly.sprites(state)):
                x, y, z = view_point(sprite.position)
                quad = getattr(sprite, "quad", None)
                instances.append(Instance(
                    index=len(instances), role="spawned", label=sprite.label,
                    art=(getattr(sprite, "art", None)
                         or self.reward_art.get(sprite.reward)),
                    x=x, y=y, z=z,
                    quad=tuple(view_point(c) for c in quad) if quad is not None else None,
                    follow=(parent.index, number), note=spawner))
            for prop in parent.assembly.props():
                if not self._loads(prop.sources):
                    continue
                used.update(prop.sources)
                x, y, z = view_point(prop.pieces[0][1])
                instances.append(Instance(
                    index=len(instances), role="spawned", label=prop.label,
                    sources=prop.sources, x=x, y=y, z=z, assembly=prop,
                    name=prop.name, note=spawner))

        # Actors the scene's own workers made, and children that wandered
        # off from their spawner: each a row of its own, where it stood.
        # One standing on a pickup record is that pickup - the chests the
        # scene already draws from their table - and one at the origin
        # never found its place (Tomba himself, before he is put down).
        taken = [view_position(p) for p in self.pickups]
        for label, actors in loose:
            head = actors[0]
            if _at_origin(head.position):
                continue
            hx, hy, hz = view_point(head.position)
            if any(abs(hx - px) < PICKUP_MATCH and abs(hz - pz) < PICKUP_MATCH
                   and abs(hy - py) < PICKUP_MATCH * 4 for px, py, pz in taken):
                continue
            posed = self._posed(actors, head, head.position, 0, label)
            if posed is None:
                continue
            label = self._spawn_label(label, posed)
            if self._gate_note(head):
                label = f"⧖ {label}"
            x, y, z = view_point(head.position)
            index = len(instances)
            instances.append(Instance(
                index=index, role="spawned", label=label,
                sources=posed.sources, x=x, y=y, z=z,
                assembly=posed if posed.sources else None,
                name=label, note=posed.note))
            self._append_pose_loop(instances, instances[index], head, posed)
            used.update(posed.sources)
            take(actors, index, None)
            if not posed.sources:
                for rider in posed.riders:
                    rx, ry, rz = view_point(rider.position)
                    instances[-1].art = rider.art
                    instances[-1].x, instances[-1].y, instances[-1].z = rx, ry, rz
                    if rider.quad is not None:
                        instances[-1].quad = tuple(view_point(c) for c in rider.quad)
                    break
                continue
            for number, rider in enumerate(posed.riders):
                rx, ry, rz = view_point(rider.position)
                instances.append(Instance(
                    index=len(instances), role="spawned", label=rider.label,
                    art=rider.art, x=rx, y=ry, z=rz, follow=(index, number),
                    quad=(tuple(view_point(c) for c in rider.quad)
                          if rider.quad is not None else None),
                    note=f"carried by {label}"))

        # The rooms: every scene the area's spawner has a table for, run
        # as the game runs it when Tomba walks in (functions/actor_sim.py).
        self.room_tables = dict(getattr(world, "room_tables", {}) or {})
        for scene, actors in sorted((world.rooms if world is not None
                                     else {}).items()):
            within = {a.address for a in actors}
            for root in actors:
                if root.spawner in within or _at_origin(root.position):
                    continue
                tree = actor_sim.subtree(world, root, actors)
                gate = self._gate_note(root)
                label = (f"{'⧖ ' if gate else ''}{interior_name(scene)}: "
                         f"{self._actor_name(root)}")
                posed = self._posed(tree, root, root.position, 0, label)
                x, y, z = view_point(root.position)
                if posed is None:
                    # Draws nothing - a door trigger, a talk spot: marked.
                    if not any(a.lines or a.polys for a in tree):
                        instances.append(Instance(
                            index=len(instances), role="spawned",
                            label=f"{label} (draws nothing)", x=x, y=y, z=z,
                            marker=True, scene=scene,
                            note=f"{interior_name(scene)}, from its scene table: "
                                 f"its code ran and drew nothing"
                                 + (f"<br>{gate}" if gate else "")))
                    continue
                note = (f"{interior_name(scene)}, from its scene "
                        f"table<br>{posed.note}" + (f"<br>{gate}" if gate else ""))
                follow = None
                if posed.sources:
                    follow = len(instances)
                    instances.append(Instance(
                        index=follow, role="spawned",
                        label=self._spawn_label(label, posed),
                        sources=posed.sources, x=x, y=y, z=z, assembly=posed,
                        name=label, note=note, scene=scene))
                    self._append_pose_loop(instances, instances[follow], root, posed)
                    used.update(posed.sources)
                take(tree, follow, scene)
                for number, rider in enumerate(posed.riders):
                    rx, ry, rz = view_point(rider.position)
                    instances.append(Instance(
                        index=len(instances), role="spawned",
                        label=f"{interior_name(scene)}: {rider.label}", art=rider.art,
                        x=rx, y=ry, z=rz, note=note, scene=scene,
                        follow=(follow, number) if follow is not None else None))

        reach = EVENT_REACH if self.placements else 1
        for handler, tree in (world.events if world is not None else ()):
            anchor = next((a for a in tree if (a.parts or a.frames) and a.position is not None
                           and max(abs(a.position[0]), abs(a.position[2])) >= reach),
                          None)
            if anchor is None:
                continue
            label = f"⧖ event: {self.handler_name(handler)}"
            posed = self._posed(tree, anchor, anchor.position, 0, label)
            if posed is None:
                continue
            x, y, z = view_point(anchor.position)
            index = len(instances)
            instances.append(Instance(
                index=index, role="spawned", label=self._spawn_label(label, posed),
                sources=posed.sources, x=x, y=y, z=z,
                assembly=posed if posed.sources else None,
                name=label, note=f"{EVENT_NOTE}<br>{posed.note}"))
            self._append_pose_loop(instances, instances[index], anchor, posed)
            used.update(posed.sources)
            take(tree, index, None)
            if not posed.sources and posed.riders:
                rider = posed.riders[0]
                instances[-1].art = rider.art
                instances[-1].x, instances[-1].y, instances[-1].z = view_point(rider.position)

        for number, (name, drawer, model) in enumerate(environment_meshes.models(
                os.path.basename(self.overlay_path or ""), self.overlay_data,
                self.chunk_index in PURIFIED_CHUNKS, view_point)):
            self.models[ENVIRONMENT_ID + number] = model
            instances.append(Instance(
                index=len(instances), role="scenery",
                label=f"{name} (built by {drawer})",
                sources=((ENVIRONMENT_ID + number, 0),), authored=True,
                name=name, note=f"no file holds it: {drawer} builds it every "
                                f"frame - see functions/environment_meshes.py"))

        # Lines whose actor got no row of its own still show, by room.
        if world is not None:
            take(world.actors, None, None)
            for scene, actors in world.rooms.items():
                take(actors, None, scene)

        # What draw routines put out as textured polygons - A01's chains - a
        # model each, already in world coordinates.
        if drawn:
            number = 0
            seen_heads = {}
            for key, polys in drawn.items():
                owner, scene, alone = key
                families = sorted(clipped.get(key, ()), key=repr)
                actor = (world.by_address.get(alone)
                         if world is not None and alone is not None else None)
                snow_firefly = (actor is not None
                                and isinstance(actor.family_key, tuple)
                                and actor.family_key[:1] in (("snow-firefly",),
                                                             ("ground-firefly",)))
                # Unattributed projected packets from unrelated emitters -
                # A01's pipe bubbles and a vent 8000 units away - share one
                # bucket; each spatial group is its own row, looping on its
                # own clips' period rather than the longest in the bucket.
                split = alone is None and owner is None
                if alone is not None:
                    parts = [(solo[key][0], [alone])]
                else:
                    parts = []
                    for static, group in (family_groups(polys, families, clips)
                                          if split else [(polys, families)]):
                        frames = [list(static)]
                        if group:
                            period = max(len(clips[family]) for family in group)
                            frames = [
                                list(static) + [polygon for family in group
                                                for polygon in clips[family][
                                                    frame % len(clips[family])]]
                                for frame in range(period)]
                        parts += [(part, group) for part in
                                  (spatial_parts(frames) if split else [frames])]
                for frames, families in parts:
                    as_billboard = snow_firefly or camera_facing_frames(frames)
                    if as_billboard:
                        points = [view_point(np.asarray(point, dtype=np.float64))
                                  for frame_polys in frames for poly in frame_polys
                                  for point in poly[0]]
                        if not points:
                            continue
                        cx, cy, cz = np.asarray(points, dtype=np.float64).mean(axis=0)
                        index = len(instances)
                        label = (f"the area: {self._actor_name(actor)}" if actor is not None
                                 else "the area: projected effect")
                        instances.append(Instance(
                            index=index, role="spawned", label=label,
                            x=float(cx), y=float(cy), z=float(cz), name=label,
                            note=(f"{len(frames)} game-code frames; rendered as a "
                                  "camera-facing billboard."
                                  + (" Preview origin is the game's stored "
                                     "free-flight/reward position, not the "
                                     "still-unresolved terrain trigger point."
                                     if snow_firefly else ""))))
                        self.captured_billboards[index] = tuple(frames)
                        blend = captured_blend(frames)
                        if blend is not None:
                            self.captured_billboard_blends[index] = blend
                        continue
                    models = [drawn_model(f, view_point, stepped.get(key)) for f in frames]
                    if not any(models):
                        continue
                    head = (instances[owner].label if owner is not None
                            else interior_name(scene) if scene is not None else "the area")
                    if alone is not None:
                        head = f"{head}: {solo[key][1]}"
                        seen_heads[head] = seen_heads.get(head, 0) + 1
                        head = f"{head} {seen_heads[head]}"
                    every = np.concatenate([np.asarray(m["vertices"], dtype=np.float64)
                                            for m in models if m])
                    cx, cy, cz = every.mean(axis=0)
                    first = len(instances)
                    for frame, model in enumerate(models):
                        sources = ()
                        if model is not None:
                            self.models[DRAWN_ID + number] = model
                            self.drawn_polys[DRAWN_ID + number] = tuple(frames[frame])
                            sources = ((DRAWN_ID + number, 0),)
                            number += 1
                        note = (f"{len(polys)} polygon(s) its draw routine put out "
                                f"(actor_sim.capture_lines), on the page their packets name"
                                + (", UVs stepped by the frame counter"
                                   if model and model["uv_frames"] else ""))
                        if families:
                            note = (f"a moving effect: {len(frames)} frames of what its code "
                                    f"draws, run on as the game runs it (actor_sim.record_clips)"
                                    f" and played back at {CLIP_HZ} a second")
                        instances.append(Instance(
                            index=len(instances), role="spawned",
                            label=f"{head}: drawn by its code"
                                  + (f" (frame {frame})" if frame else ""),
                            sources=sources, authored=True, scene=scene,
                            x=float(cx), y=float(cy), z=float(cz),
                            name=f"{head}: drawn by its code", note=note,
                            flip=(first, frame) if frame else None))
                    if families:
                        instances[first].flip_frames = tuple(range(first, len(instances)))

        # Every line drawer under one row is one clip - A08's ripple is a
        # ring an actor - on a row of its own, its lines each on its frame.
        grouped = collections.defaultdict(list)
        for owner, scene, frames, name in moving_lines:
            grouped[(owner, scene)].append((frames, name))
        for (owner, scene), clips in grouped.items():
            period = max(len(frames) for frames, _name in clips)
            points = np.array([view_point(p) for frames, _name in clips for lines in frames
                               for line in lines for p in line[:2]], dtype=np.float64)
            if not len(points):
                continue
            cx, cy, cz = points.mean(axis=0)
            head = instances[owner].label if owner is not None else "the area"
            label = f"{head}: moving lines drawn by its code"
            index = len(instances)
            instances.append(Instance(
                index=index, role="spawned", scene=scene, label=label, name=label,
                x=float(cx), y=float(cy), z=float(cz),
                note=(f"{period} frames of the lines {len(clips)} actor(s) "
                      f"({clips[0][1]}) draw, recorded as they run "
                      f"(actor_sim.record_line_clips), played back at {CLIP_HZ} a second")))
            for frame in range(period):
                for frames, _name in clips:
                    for a, b, color_a, color_b, blended in frames[frame % len(frames)]:
                        self.lines.append(SceneLine(index, scene, view_point(a), view_point(b),
                                                    color_a, color_b, blended, frame, period))

        flight = getattr(world, "firefly_flight", None) if world is not None else None
        if flight and self._firefly_spots:
            self._add_firefly_flight(instances, flight, self._firefly_spots)

        pack = self.model(ASSET_PACK_ID)
        for group in (pack or {}).get("groups") or ():
            if group.empty or (ASSET_PACK_ID, group.index) in used:
                continue
            if not world_placed(group, room_box):
                continue
            instances.append(Instance(
                index=len(instances), role="scenery",
                label=f"scenery {group.index}",
                sources=((ASSET_PACK_ID, group.index),), authored=True))
        self.instances = instances

    def _loads(self, sources):
        """Whether every file a set of sources names reads as a model."""
        return bool(sources) and all(self.model(f)
                                     for f in {f for f, _g in sources})

    def apply_bindings(self):
        """Point each object at whatever `bindings` now says it is drawn
        with, and say how many changed.

        What a fresh learn has to go through: the bindings are a lookup,
        but an instance carries its own model so that changing one by
        hand does not have to write to the lookup. A binding that says
        nothing leaves the instance alone - learning from a state adds
        knowledge, it does not take any away."""
        room_box = self.room_bounds()
        changed = 0
        for instance in self.instances:
            key = instance_key(instance)
            if key is None or instance.assembly is not None:
                continue
            sources = self.bindings.get(key)
            if not sources or tuple(sources) == instance.sources:
                continue
            instance.sources = tuple(sources)
            _model, group = self.group(instance.sources[0])
            instance.authored = bool(group is not None
                                     and world_placed(group, room_box))
            changed += 1
        if changed:
            self.__dict__.pop("_built_model", None)
        return changed

    # --- geometry -----------------------------------------------------

    def build(self):
        """One model dict for the whole scene, in the shape
        gui/smst/smst_viewer.py draws - so the level viewer inherits its
        shaders, its palette grouping and its blending unchanged, with
        `groups` holding instances instead of a model's parts."""
        kept = getattr(self, "_built_model", None)
        if kept is not None:
            return kept
        scene = {
            "vertices": [], "vertex_colors": [], "faces": [],
            "texture_coords": [], "texture_info": [], "face_flags": [], "face_levels": [],
            "tri_count": 0, "quad_count": 0, "groups": self.instances,
        }
        for instance in self.instances:
            instance.first_vertex = len(scene["vertices"])
            instance.first_face = len(scene["faces"])
            if instance.role == "room":
                _where, room = self.rooms[instance.room]
                self._append(scene, room)
                instance.tris = room.get("tri_count", 0)
                instance.quads = room.get("quad_count", 0)
            else:
                instance.parts = []
                instance.spans = []
                for number, source in enumerate(instance.sources):
                    model, group = self.group(source)
                    if group is None:
                        instance.spans.append(None)
                        continue
                    at, faces_at = len(scene["vertices"]), len(scene["faces"])
                    self._append(scene, model, group)
                    instance.spans.append((at, len(scene["vertices"]) - at))
                    blends = getattr(instance.assembly, "blends", None) or ()
                    blend = blends[number] if number < len(blends) else None
                    if blend is not None:
                        # The actor's render mode, as f_DrawActorModelByRenderMode
                        # applies it over the model's own packets.
                        info = scene["texture_info"]
                        for f in range(faces_at, len(info)):
                            page, clut, _blended, mode = info[f]
                            info[f] = (page, clut, blend, mode)
                    shift = (instance.offsets[number]
                             if number < len(instance.offsets) else None)
                    if shift and any(shift):
                        instance.parts.append(
                            (at, len(scene["vertices"]) - at, shift))
                    instance.tris += group.tris
                    instance.quads += group.quads
                    instance.size, instance.offset = group.size, group.offset
            instance.vertex_count = len(scene["vertices"]) - instance.first_vertex
            instance.face_count = len(scene["faces"]) - instance.first_face
            instance.bounds = _bounds(
                scene["vertices"][instance.first_vertex:])
        scene["tri_count"] = sum(i.tris for i in self.instances)
        scene["quad_count"] = sum(i.quads for i in self.instances)
        self._built_model = scene
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
        flags = model.get("face_flags") or ()
        levels = model.get("face_levels") or ()
        for f in range(face_first, face_first + face_count):
            scene["faces"].append([v + shift for v in model["faces"][f]])
            scene["texture_info"].append(model["texture_info"][f])
            scene["face_flags"].append(flags[f] if f < len(flags) else 0)
            scene["face_levels"].append(levels[f] if f < len(levels) else 0.0)
        if model.get("uv_frames"):
            scene.setdefault("uv_frames", {}).update(model["uv_frames"])

    def positions(self, scene):
        """Every vertex with its instance's transform applied.

        The room goes in as it is - an MDAT is already in world
        coordinates - and each object turns about its own Y and moves to
        where its record says."""
        self._place_followers()
        verts = np.array(scene["vertices"], dtype=np.float32)
        for instance in self.instances:
            if (not instance.vertex_count or instance.role == "room"
                    or instance.authored):
                continue
            if instance.assembly is not None and instance.spans:
                pieces = instance.assembly.pose(game_state(instance))
                for span, (m, t) in zip(instance.spans, pieces):
                    if span is None:
                        continue
                    first, count = span
                    block = verts[first:first + count].astype(np.float64)
                    moved = (block @ (VIEW_AXES @ m @ VIEW_AXES).T
                             + VIEW_AXES @ t)
                    verts[first:first + count] = moved.astype(np.float32)
                continue
            at = instance.first_vertex
            block = verts[at:at + instance.vertex_count].astype(np.float64)
            # A part that hangs off the origin is moved there first, so
            # the instance's own turn carries it round with the rest.
            for first, count, shift in instance.parts or ():
                start = first - at
                block[start:start + count] += shift
            moved = block @ instance.matrix().T
            moved += (instance.x, instance.y, instance.z)
            verts[at:at + instance.vertex_count] = moved.astype(np.float32)
        return verts

    def _place_followers(self):
        """Put every riding pickup back where its parent now holds it."""
        for instance in self.instances:
            if instance.follow is None:
                continue
            parent_index, number = instance.follow
            if not 0 <= parent_index < len(self.instances):
                continue
            parent = self.instances[parent_index]
            if parent.assembly is None:
                continue
            sprites = parent.assembly.sprites(game_state(parent))
            if number < len(sprites):
                instance.x, instance.y, instance.z = view_point(
                    sprites[number].position)

    def collision(self, view=None, rooms=None):
        """gui.collision_overlay.Lines for what `view` shows: None the area,
        a scene number that room, "all" everything.

        A SCLD is the area's - rooms have none. A town's first dataset is
        its streets, the one the game takes while
        src_InsideWeaponlessInterior is 0 (FUN_A02__801258a4,
        FUN_A07__8012ee08); the rest are the rooms', all of them in one
        space, looked up by where Tomba stands. So each of those planes goes
        to the room whose box holds its middle, `rooms` being
        {scene: (low, high)} round its instances in world units; a plane no
        box holds shows only under "all"."""
        from gui import collision_overlay as overlay
        lines = overlay.Lines()
        # Two colours here, not one per plane: in a level what matters is
        # what you stand on and what stops you.
        plain = overlay.LEVEL
        if view in (None, "all"):
            overlay.add_scld(lines, self.planes, **plain)
        elif rooms and view in rooms:
            # A room loads no collision of its own (its enter routine only
            # fills slot 15), so what it stands on is the area's SCLD where
            # the room is.
            low, high = rooms[view]
            overlay.add_scld(lines, self.planes, bounds=(
                low[0] - ROOM_REACH, high[0] + ROOM_REACH,
                low[2] - ROOM_REACH, high[2] + ROOM_REACH), **plain)
        datasets = town_collision.find(self.overlay_data) if self.overlay_data else ()
        for number, dataset in enumerate(datasets):
            for plane in dataset.planes:
                if number == 0:
                    shown = view in (None, "all")
                else:
                    shown = view == "all" or view in self._rooms_of(plane, rooms or {})
                if shown:
                    overlay.add_town(lines, (plane,), view_point, plain=True)
        return lines

    @staticmethod
    def _rooms_of(plane, rooms):
        """The scenes whose box holds a town plane's middle - the smallest
        such box, and any the same size - or an empty set."""
        middle = np.mean([view_point(p) for p in plane.outline()], axis=0)
        sizes = {}
        for scene, (low, high) in rooms.items():
            low, high = np.asarray(low), np.asarray(high)
            if np.all(middle >= low - ROOM_REACH) and np.all(middle <= high + ROOM_REACH):
                sizes[scene] = float(np.prod(high - low + 1.0))
        if not sizes:
            return set()
        least = min(sizes.values())
        return {scene for scene, size in sizes.items() if size <= least * 1.0001}

    def markers(self, hidden=()):
        """Line geometry for the objects with no model, as
        (positions, colours) - a diamond and an upright at each.

        An unbound object is still worth drawing: where a level's things
        stand is most of what this view is for, and a marker says that
        much without pretending to know what the thing looks like.

        Everything placed gets one, not just the objects - a crystal
        whose model nobody has picked yet would otherwise be a row in
        the list and nothing at all in the view."""
        positions, colors = [], []
        for instance in self.instances:
            if (not instance.marked or instance.face_count
                    or instance.index in hidden):
                continue
            if instance.drawn_as_sprite:
                continue
            x, y, z = instance.x, instance.y, instance.z
            r = MARKER_SIZE
            color = instance_color(instance)
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

    def named(self, sources):
        """What the model behind a set of sources is called, if anything.

        A whole character is many groups of one file, so its file's own
        name is what fits; a single part takes its part name first."""
        if not sources:
            return ""
        file_id, group = sources[0]
        if len({f for f, _g in sources}) == 1 and len(sources) > 1:
            group = None
        self.model(file_id)          # so its hash is known
        name = placement_module.model_name(self.model_names,
                                           self.content.get(file_id), group)
        if not name and group is None:
            name = file_label(self.content.get(file_id), self.slots.get(file_id))
        return name

    def file_named(self, sources):
        """The name the tree gives the file most of `sources` come from
        (labels/*.json) - 'Flying Spiker Enemy' - or ""."""
        if not sources:
            return ""
        file_id = collections.Counter(f for f, _g in sources).most_common(1)[0][0]
        self.model(file_id)
        return file_label(self.content.get(file_id), self.slots.get(file_id))

    def smst_files(self):
        """Every file of this area's chunk that reads as an SMST.

        Not only the ones something loaded: a build can carry a model no
        code stands up - the JP demo ships the magic flower's - and it is
        still the area's own art, so it can be picked by hand."""
        kept = getattr(self, "_smst_files", None)
        if kept is not None:
            return kept
        kept = []
        for file_id, (offset, size) in sorted(self.by_id.items()):
            if size <= 0:
                continue
            if self.models.get(file_id) is not None:
                kept.append(file_id)
                continue
            try:
                best = format_detect.identify_at(self.dat_path,
                                                 self.dat_start + offset, size)
            except Exception:
                continue
            if best and best[0].kind == "SMST":
                kept.append(file_id)
        self._smst_files = kept
        return kept

    def model_choices(self):
        """[(label, (file id, group)), ...] every part this area could
        draw an object with - the asset pack first, since that is where
        a level's props live, then the rest of its models."""
        out = [("(no model - marker only)", None)]
        ids = [ASSET_PACK_ID] + [i for i in self.smst_files() if i != ASSET_PACK_ID]
        for file_id in ids:
            model = self.model(file_id)
            for group in (model or {}).get("groups") or ():
                if group.empty:
                    continue
                named = placement_module.model_name(
                    self.model_names, self.content.get(file_id), group.index)
                out.append((f"id {file_id} group {group.index}  "
                            f"({group.tris}t {group.quads}q)"
                            + (f"  - {named}" if named else ""),
                            (file_id, group.index)))
        return out

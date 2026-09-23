"""Where a level's objects stand - the signs, the doors, the chests.

WHAT IS AND ISN'T IN THE LEVEL FILES

An MDAT is the room and nothing else. Everything standing in it - the
signposts, the doors, the ladders, the treasure chests - is a part of
the area's asset-pack SMST, and an SMST says nothing about where a part
belongs: every part is modelled around its own origin (see
formats/models/smst_parser.py). A handful of an asset pack's parts are the
exception, authored in room coordinates because they only ever appear
once - AREA_04's four water surfaces - and those are already in place.
Everything else needs telling.

What tells it is not in TOMBA2.DAT at all. It is a table in the area's
overlay, the Axx.BIN that gui.main_window.overlay_for_area() finds -
the same file formats/animation/clut_anim.py reads the animated palettes out
of. One area, one table, terminated rather than counted:

    record (20 bytes, in a run of them):

        u8  object_flags  the actor's type. 0xFF ends the table, and
                          bit 0x80 is a flag the game keeps; the type
                          itself is the low 4 bits (0 to 4 on the disc)
        u8  alloc         which allocation list it goes on (2 to 9)
        i16 x, y, z   where the object stands, in the same world units
                      an MDAT's vertices are in
        u8  kind      which class of object it is, within this overlay
        u8  slot      which one of that class - the class's own index,
                      and not always dense: AREA_04's signposts are
                      slots 0, 1, 9 and 10
        i16 angle     how far it is turned about Y, IN DEGREES
        i16 angle2    a second turn, in degrees; only five values are
                      used on the whole disc
        i16 condition when 1, skip this object in the purified area;
                      when 2, skip it indoors. 0 on all but a few
        u32 handler   the routine that runs this object. Below the
                      overlay's own load address it is a routine in
                      MAIN.EXE, which is what a class shared by every
                      area looks like - the signpost is one

WHAT IS NOT HERE is which part of the asset pack an object is drawn
with. That binding lives in the handler's own code - it fetches its
model from the area's file table and a group number held as an
immediate in a MIPS instruction - so it cannot be read out of the data.
Running that code (game/actor_sim.py) is what settles it.

HOW THIS WAS READ

The layout above is the one MAIN.EXE's own table walker uses - the
routine at 0x80072A78, which picks a table out of the pointer array at
0x800A4C28 (one entry per area, and a handful of areas that pick a
second table by section) and steps it 20 bytes at a time until the byte
at the front of a record is 0xFF. tables_from_exe() below reads that
array; find_tables() finds the same 31 tables by shape alone, and
agrees with it exactly on the retail disc.

Before that walker was read the records were found by scanning, two
bytes off from where they really start, which put a record's own
object_flags and alloc bytes at the END of the record before it. Their
old names were `flags` and `group`, and `group` in particular meant
nothing: it belonged to the next object, not the one it was read with.
The fields this editor actually uses - position, angle and handler -
were right, and so is the (kind, slot, handler) a binding is keyed by,
so labels/placements.json carried over unchanged.

The record layout was first matched against savestates; nothing here
reads one any more.
"""
import json
import os
import struct
from dataclasses import dataclass

import numpy as np

from game import game_build

RECORD = struct.Struct("<BBhhhBBhhhI")
RECORD_SIZE = RECORD.size          # 20

# Where a record's own fields sit, for writing changed ones back.
POSITION_AT = 2
ANGLE_AT = 10

# Where an overlay's own addresses can point - the same window
# formats/animation/clut_anim.py uses, and for the same reason: the overlays
# load just past MAIN.EXE and none of them is 0x46000 long.
RAM_LOW = 0x80010000
RAM_HIGH = 0x80200000

# The object_flags byte a table ends on, where a record would start.
END = 0xFF

# What the identifying bytes hold on the disc - measured over all 689
# records of all 31 tables, and what tells a record from a stretch of
# some other array. The type is the low nibble because bit 0x80 is a
# flag the game keeps and copies onto the object.
TYPE_MASK = 0x7F
MAX_TYPE = 8
MIN_ALLOC, MAX_ALLOC = 2, 9
MAX_CONDITION = 2

# Where MAIN.EXE keeps the tables, for tables_from_exe(). A PS-EXE
# holds its load address at 0x18 and its code from 0x800 on.
EXE_MAGIC = b"PS-X EXE"
EXE_BASE_AT = 0x18
EXE_TEXT = 0x800

# The array the walker at 0x80072A78 indexes by area, and the second
# tables five areas pick by which section of themselves they are in.
# AREA_03 is the only area with no table at all.
AREA_TABLES = 0x800A4C28
AREA_COUNT = 22
SECTIONS = {
    1: (0x80134918,),
    5: (0x8013C1A4,),
    6: (0x80143ACC, 0x80143AE0),
    8: (0x801432B8, 0x80143470, 0x80143614),
    21: (0x80115018, 0x801150F4, 0x80115180, 0x801151F8, 0x80115310),
}

# Where an overlay lands - the same address gui.main_window uses.
OVERLAY_BASE = 0x80108F9C

# The crystals and the apples - a system of their own, nothing to do
# with the table above. MAIN.EXE holds an array of pointers to them and
# f_SpawnPersistentPickupPlacementTable walks whichever the area asks
# for; the index is a table number and not an area, so which overlay
# owns a table is worked out by parsing it - see find_pickups().
PICKUP_TABLES = 0x800A3EE0
PICKUP_TABLE_COUNT = 42
# How far past the overlay base a table can be, and the shortest run a scan
# for tables by shape takes as one.
OVERLAY_REACH = 0x80000
MIN_PICKUPS = 2

# US retail's; another build's while it is open (game/game_build.py).
_BUILD = game_build.Addresses(
    globals(), main=("AREA_TABLES", "OVERLAY_BASE", "PICKUP_TABLES"),
    per_area=("SECTIONS",))

#     u8  type      \  what the actor is allocated as. 0xFF here ends
#     u8  alloc     /  the table; alloc is 2 or 5 on the whole disc
#     u8  persist   bit 0x80 counts it against the apples rather than
#                   the chests; the low bits are the third argument
#     u8  reward    which pickup it is. 4 is the orange crystal
#     i16 x, y, z
#     i16 bit       which bit of the collected-items bitmap is its own
#     u8  plane     which of the area's collision planes it stands on -
#                   the actor's +0x2A, which f_ResolveActorAreaPlanePosition
#                   projects it onto
#     u8  behaviour  1 drops it onto the ground; 0 leaves it where the
#                   record says
#     u16 config
PICKUP = struct.Struct("<BBBBhhhhBBH")
PICKUP_SIZE = PICKUP.size          # 16
APPLE = 0x80

# A reward's top bit is a flag the spawner keeps, not part of the number.
PICKUP_REWARD_MASK = 0x7F

# An alloc of 2 is a chest. The spawner branches on it: a different
# handler, and `config` splits into the item inside and an effect id.
CHEST_ALLOC = 2
CONTENTS_BITS = 12
CONTENTS_MASK = (1 << CONTENTS_BITS) - 1

# Which chest is which, by the field that means `reward` on everything
# else. Red and green were confirmed from savestates, blue and white by
# eye once the models were drawn.
CHEST_KINDS = {0: "red chest", 1: "green chest",
               2: "blue chest", 3: "white chest"}

# What a pickup record holds on the disc, for telling a table from a
# stretch of something else. The save-bit indices inside one table are
# allocated in order, which is what makes an overlay's own tables
# unmistakable: no other overlay reads one as valid.
PICKUP_MAX_TYPE = 4
PICKUP_ALLOC = (2, 5)
PICKUP_MAX_BIT = 1023

# A run has to be at least this long before it is called a table. Two,
# because three of the real ones are that short - AREA_08's fourth and
# AREA_17's hold three records and AREA_18's holds two - and with the
# identifying bytes checked nothing else that short gets through.
MIN_RECORDS = 2

# An angle is in whole degrees, so anything outside a turn and a bit is
# not one. Records on the disc run from -177 to 315.
MAX_ANGLE = 400

# Rotations are held in 4096ths, the PSX's usual fixed point.
ONE = 4096


class PlacementError(ValueError):
    """Raised when something can't be read for placements."""


@dataclass
class Placement:
    """One object standing in a level."""

    index: int              # which record of the table this is
    table: int              # which of the overlay's tables it is in
    offset: int             # where it sits in the overlay
    object_flags: int       # the actor's type, and 0x80 kept as a flag
    alloc: int              # which allocation list it goes on
    x: int
    y: int
    z: int
    kind: int
    slot: int
    angle: int              # degrees about Y
    angle2: int             # a second turn, in degrees
    condition: int          # 1 skips it purified, 2 skips it indoors
    handler: int
    last: bool = False      # whether the 0xFF terminator follows it

    @property
    def type(self):
        return self.object_flags & TYPE_MASK

    @property
    def position(self):
        return self.x, self.y, self.z

    def key(self):
        """What a binding is looked up by - see load_bindings()."""
        return self.kind, self.slot, self.handler

    def name(self):
        return f"{self.kind}.{self.slot}"

    def describe(self):
        return (f"kind {self.kind} slot {self.slot}, turned {self.angle} deg, "
                f"handler 0x{self.handler:08X}")


def _record(data, offset):
    """The record at `offset`, or None if what is there isn't one."""
    if offset < 0 or offset + RECORD_SIZE > len(data):
        return None
    object_flags, alloc, x, y, z, kind, slot, angle, angle2, condition, \
        handler = RECORD.unpack_from(data, offset)
    if object_flags == END or object_flags & TYPE_MASK > MAX_TYPE:
        return None
    if not MIN_ALLOC <= alloc <= MAX_ALLOC:
        return None
    if handler & 3 or not RAM_LOW <= handler < RAM_HIGH:
        return None
    if not -MAX_ANGLE <= angle <= MAX_ANGLE:
        return None
    if not -MAX_ANGLE <= angle2 <= MAX_ANGLE:
        return None
    if not 0 <= condition <= MAX_CONDITION:
        return None
    return Placement(index=0, table=0, offset=offset,
                     object_flags=object_flags, alloc=alloc, x=x, y=y, z=z,
                     kind=kind, slot=slot, angle=angle, angle2=angle2,
                     condition=condition, handler=handler)


def find_tables(data):
    """[[Placement, ...], ...] for every table of records in an overlay.

    Scanned on two-byte boundaries rather than four: the table is a run
    of 20-byte records and 20 is not a multiple of 4, so a table can and
    does start halfway through a word - A00.BIN's is at 0x3DA20.

    A run only counts as a table if the byte after it is the 0xFF a
    record's object_flags would have been. That is what tells a real one
    from a stretch of some other array that happens to hold
    plausible-looking words, and with the identifying bytes read in the
    right place it is enough on its own: this finds the 31 tables the
    game itself uses, and nothing else - see tables_from_exe().

    An overlay usually holds several, back to back - A0L.BIN has five.
    They are what an area draws in each of its situations, which is how
    one overlay serves an area and its purified twin."""
    tables, at = [], 0
    while at + RECORD_SIZE <= len(data):
        if _record(data, at) is None:
            at += 2
            continue
        run, end = [], at
        while (record := _record(data, end)) is not None:
            run.append(record)
            end += RECORD_SIZE
        closed = end < len(data) and data[end] == END
        if closed and len(run) >= MIN_RECORDS:
            run[-1].last = True
            for i, record in enumerate(run):
                record.index = i
                record.table = len(tables)
            tables.append(run)
        at = max(end, at + 2)
    return tables


@dataclass
class Pickup:
    """One crystal or apple lying in a level."""

    index: int              # which record of the table this is
    table: int              # which entry of MAIN.EXE's array it came from
    offset: int             # where it sits in the overlay
    x: int
    y: int
    z: int
    type: int
    alloc: int
    persist: int
    reward: int             # which pickup it is - 4 is the orange crystal
    bit: int                # its own bit of the collected-items bitmap
    plane: int              # which of the area's collision planes it is on
    behaviour: int
    config: int

    @property
    def chest(self):
        """Whether it is a chest rather than something lying on the
        ground. The spawner gives these a different handler and reads
        two more fields out of `config`; `reward` stops meaning a reward
        and becomes which chest it is - 0 red, 1 green."""
        return self.alloc == CHEST_ALLOC

    @property
    def contents(self):
        """What is inside a chest, as a REWARD number - the same numbering
        a loose pickup's `reward` uses, so it names an item through
        formats.sprites.pickup_art rather than being an item id itself."""
        return self.config & CONTENTS_MASK

    @property
    def effect(self):
        """The chest's effect id - the top nibble of the same field."""
        return self.config >> CONTENTS_BITS

    @property
    def art_reward(self):
        """The reward with its flag bit taken off - what indexes the
        table in formats/sprites/pickup_art.py. The spawner masks it the same
        way before looking anything up."""
        return self.reward & PICKUP_REWARD_MASK

    @property
    def apple(self):
        """Whether it counts against the apples rather than the chests -
        the two are numbered separately."""
        return bool(self.persist & APPLE)

    @property
    def position(self):
        return self.x, self.y, self.z

    def name(self, art=None):
        """What it is, then which one it is. `art` is this reward's entry
        from formats.sprites.pickup_art, which is what says a reward 4 is a
        hundred-AP crystal - without it the number has to do. A chest
        doesn't use that table at all."""
        if self.chest:
            what = CHEST_KINDS.get(self.reward & PICKUP_REWARD_MASK,
                                   f"chest kind {self.reward}")
            holds = art.grants if art is not None else f"reward {self.contents}"
            return f"{what}: {holds} #{self.bit}"
        what = art.label() if art is not None else f"reward {self.reward}"
        return f"{what} #{self.bit}"

    def describe(self, art=None):
        if self.chest:
            what = CHEST_KINDS.get(self.reward & PICKUP_REWARD_MASK,
                                   f"chest kind {self.reward}")
            holds = (f"{art.grants} (reward {self.contents})"
                     if art is not None else f"reward {self.contents}")
            ground = " dropped to the ground" if self.behaviour else ""
            return (f"{what}, holds {holds}, effect {self.effect}, "
                    f"chest bit {self.bit}, plane {self.plane}{ground}")
        bits = [f"reward {self.reward}",
                f"{'apple' if self.apple else 'chest'} bit {self.bit}",
                f"plane {self.plane}"]
        if art is not None:
            bits.insert(0, art.grants)
            frames = "/".join(str(f.frame) for f in art.frames)
            if frames:
                bits.append(f"sprite {frames}"
                            + (" looping" if art.loops else ""))
            where = art.clut_xy
            if where:
                bits.append(f"palette at VRAM {where[0]},{where[1]}")
        return ", ".join(bits)


def _pickup(data, offset):
    """The pickup record at `offset`, or None if what is there isn't one."""
    if offset < 0 or offset + PICKUP_SIZE > len(data):
        return None
    kind, alloc, persist, reward, x, y, z, bit, plane, behaviour, config = \
        PICKUP.unpack_from(data, offset)
    # The type carries the same 0x80 flag a placement record's does - the
    # spawner hands it to the allocator whole and the handler tests the
    # bit separately - so mask it before judging the number. 0xFF still
    # ends a table, since 0x7F is not a type.
    if kind == END or kind & TYPE_MASK > PICKUP_MAX_TYPE:
        return None
    if alloc not in PICKUP_ALLOC:
        return None
    if not 0 <= bit <= PICKUP_MAX_BIT:
        return None
    if not (0 < abs(x) < 32000 and abs(y) < 32000 and 0 < abs(z) < 32000):
        return None
    return Pickup(index=0, table=0, offset=offset, x=x, y=y, z=z, type=kind,
                  alloc=alloc, persist=persist, reward=reward, bit=bit,
                  plane=plane, behaviour=behaviour, config=config)


def pickup_addresses(exe_path):
    """Every address in MAIN.EXE's array of pickup tables, in order."""
    try:
        with open(exe_path, "rb") as f:
            exe = f.read()
    except OSError:
        return []
    if len(exe) < EXE_TEXT or exe[:8] != EXE_MAGIC:
        return []
    base = struct.unpack_from("<I", exe, EXE_BASE_AT)[0]
    at = PICKUP_TABLES - base + EXE_TEXT
    if at < 0 or at + PICKUP_TABLE_COUNT * 4 > len(exe):
        return []
    return list(struct.unpack_from(f"<{PICKUP_TABLE_COUNT}I", exe, at))


def find_pickups(data, exe_path):
    """[[Pickup, ...], ...] for the tables in MAIN.EXE's array that
    belong to THIS overlay.

    Only one overlay is in memory at a time, so every entry of the array
    points into the same window and an area's own entries are the ones
    that read as a table there. Reading as one is a high bar: the save
    bits inside a table are numbered in order, and on US retail that
    leaves no entry that two overlays both claim. On the other builds
    another area's entry can land partway into one of this area's tables
    and read as its tail; no real table starts inside another, since each
    ends on a terminator of its own, so such a tail is dropped."""
    addresses = pickup_addresses(exe_path)
    if not _pointer_array(addresses):
        # The demos keep no such array (game/game_build.py): the
        # tables are found by their shape instead.
        return scan_pickups(data)
    tables = []
    for number, address in enumerate(addresses):
        if not address:
            continue
        at, run = address - OVERLAY_BASE, []
        if at < 0:
            continue
        while (record := _pickup(data, at)) is not None:
            run.append(record)
            at += PICKUP_SIZE
        if not run or at >= len(data) or data[at] != END:
            continue
        if not all(_ordered(r.bit for r in run if r.apple is which)
                   for which in (True, False)):
            continue
        for i, record in enumerate(run):
            record.index, record.table = i, number
        tables.append(run)
    spans = [(t[0].offset, t[-1].offset + PICKUP_SIZE) for t in tables]
    return [t for t in tables
            if not any(lo < t[0].offset < hi for lo, hi in spans)]


def _pointer_array(addresses):
    """Whether what was read for the array of pickup tables is one: every
    entry null or a pointer into the overlay window."""
    return bool(addresses) and all(
        not a or OVERLAY_BASE <= a < OVERLAY_BASE + OVERLAY_REACH for a in addresses)


def scan_pickups(data):
    """find_pickups() without MAIN.EXE's array: every run of pickup records
    that ends on 0xFF with its save bits in order, found by shape the way
    find_tables() finds placements."""
    placed = {offset for table in find_tables(data) for record in table
              for offset in range(record.offset, record.offset + RECORD_SIZE)}
    tables, at = [], 0
    while at + PICKUP_SIZE <= len(data):
        if at in placed or _pickup(data, at) is None:
            at += 2
            continue
        run, end = [], at
        while (record := _pickup(data, end)) is not None and end not in placed:
            run.append(record)
            end += PICKUP_SIZE
        if (len(run) >= MIN_PICKUPS and end < len(data) and data[end] == END
                and all(_ordered(r.bit for r in run if r.apple is which)
                        for which in (True, False))):
            for i, record in enumerate(run):
                record.index, record.table = i, len(tables)
            tables.append(run)
        at = max(end, at + 2)
    return tables


def _ordered(values):
    values = list(values)
    return all(b > a for a, b in zip(values, values[1:]))


def load_pickups(overlay_path, exe_path):
    """Every crystal and apple one area's overlay places. [] when either
    file is missing - a disc opened without a BIN folder has neither."""
    try:
        with open(overlay_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    return [r for table in find_pickups(data, exe_path) for r in table]


def patch_pickups(data, pickups):
    """`data` with each pickup's position written back."""
    out = bytearray(data)
    for pickup in pickups:
        struct.pack_into("<hhh", out, pickup.offset + 4,
                         int(pickup.x), int(pickup.y), int(pickup.z))
    return bytes(out)


def tables_from_exe(exe_path, area):
    """[overlay offset, ...] of the tables MAIN.EXE gives one area.

    The walker at 0x80072A78 reads a pointer per area out of the array
    at 0x800A4C28, and five areas pick a second table by which section
    of themselves they are in - those are the addresses in SECTIONS,
    which the walker holds as immediates. Returns them in the order the
    walker would reach them.

    find_tables() finds exactly these by shape, so this is a check on it
    rather than the way in: it needs a MAIN.EXE, and the addresses only
    hold for the retail build."""
    try:
        with open(exe_path, "rb") as f:
            exe = f.read()
    except OSError:
        return []
    if len(exe) < EXE_TEXT or exe[:8] != EXE_MAGIC:
        return []
    base = struct.unpack_from("<I", exe, EXE_BASE_AT)[0]
    at = AREA_TABLES - base + EXE_TEXT + area * 4
    if not 0 <= area < AREA_COUNT or at + 4 > len(exe):
        return []
    primary = struct.unpack_from("<I", exe, at)[0]
    addresses = ([primary] if primary else []) + list(SECTIONS.get(area, ()))
    return [address - OVERLAY_BASE for address in addresses]


def load_placements(overlay_path):
    """Every object one area's overlay places, in table order.

    Returns [] rather than raising when there is nothing to find: a
    small area may have no table, and a disc opened somewhere without a
    BIN folder has no overlay to read."""
    try:
        with open(overlay_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    return [record for table in find_tables(data) for record in table]


def patch(data, placements):
    """`data` with each placement's position and angle written back.

    Only the six position bytes and the two angle bytes are touched -
    everything else in a record is what the game runs the object with,
    and none of it is this editor's to change."""
    out = bytearray(data)
    for placement in placements:
        struct.pack_into("<hhh", out, placement.offset + POSITION_AT,
                         int(placement.x), int(placement.y), int(placement.z))
        struct.pack_into("<h", out, placement.offset + ANGLE_AT,
                         int(placement.angle))
    return bytes(out)


# --------------------------------------------------------------------
# Which part of the asset pack an object is drawn with
# --------------------------------------------------------------------

BINDINGS_FILE = "placements.json"


def bindings_path():
    """Beside the labels files, and found the same way - so a built exe
    reads it out of the bundle rather than off a folder that is not
    there. game.labels.NOT_LABELS is what keeps the labels loader
    from trying to read it as one."""
    from game import labels
    return os.path.join(labels.labels_dir(), BINDINGS_FILE)


# Models picked by hand in the Level Editor, over what the code attaches.
CORRECTED = "corrections"

# What things are called. Keyed by the file's CONTENT HASH, the same
# identity game/labels.py gives an entry: a file id is no good,
# because every area's "file 12" is a different asset pack and every
# evil pig is a different "file 18". A whole file is named under its
# hash and one part of it under "hash:group", which is what an asset
# pack wants since each of its groups is a different prop.
NAMED = "model_names"


def _read_bindings(path=None):
    try:
        with open(path or bindings_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _image(overlay_name, handler):
    """Which image a handler lives in: its overlay's, or MAIN.EXE's."""
    if game_build.US_OVERLAY_BASE <= handler < game_build.US_AREA_BASE:
        return os.path.splitext(overlay_name)[0].upper()
    return game_build.MAIN


def _rows_to_bindings(rows, overlay_name=""):
    # Kept under US retail's handler addresses, looked up by the open build's.
    build = game_build.current()
    out = {}
    for row in rows or ():
        try:
            handler = int(row["handler"], 16)
            here = build.address(handler, _image(overlay_name, handler))
            if not here:
                continue
            out[(int(row["kind"]), int(row["slot"]), here)] = (
                int(row["file"]), int(row["group"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _us_handler(overlay_name, handler):
    build = game_build.current()
    image = (os.path.splitext(overlay_name)[0].upper()
             if build.overlay_base <= handler < build.area_base else game_build.MAIN)
    return build.us(handler, image) or handler


def load_bindings(overlay_name, path=None, section=None):
    """{(kind, slot, handler): (file id, group)} for one overlay.

    Both sections at once by default, corrections over the top of what
    was learned - see this module's docstring on why the binding cannot
    come off the disc at all. Pass `section` to read just one, which is
    what rewriting one of them needs.

    Missing file, missing overlay and unreadable json all mean the same
    thing here: nothing is known, and the editor shows the objects as
    markers."""
    data = _read_bindings(path)
    sections = (section,) if section else (CORRECTED,)
    out = {}
    for name in sections:
        out.update(_rows_to_bindings((data.get(name) or {}).get(overlay_name),
                                     overlay_name))
    return out


def load_model_names(path=None):
    """{"12:7": name} and {"36": name} - whatever has been named."""
    rows = _read_bindings(path).get(NAMED) or {}
    return {str(k): str(v) for k, v in rows.items() if v}


def save_model_names(names, path=None):
    """Rewrite the names, leaving every other section alone."""
    path = path or bindings_path()
    data = _read_bindings(path)
    data[NAMED] = {str(k): str(v) for k, v in sorted(names.items()) if v}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    return path


def model_name(names, content, group=None):
    """What to call one model, most specific first: the part's own name,
    else the whole file's, else nothing.

    `content` is the file's hash - see game.labels.content_key."""
    if not content:
        return ""
    if group is not None:
        own = names.get(f"{content}:{group}")
        if own:
            return own
    return names.get(str(content), "")


def name_key(content, group=None):
    """The key one name is stored under."""
    return f"{content}:{group}" if group is not None else str(content)


def _bindings_to_rows(overlays):
    rows = {}
    for name, bindings in sorted(overlays.items()):
        # A binding of None is "this object has no model" - which is
        # what an object starts as, so writing it down would only be
        # recording that nothing is known.
        rows[name] = [
            {"kind": kind, "slot": slot,
             "handler": f"0x{_us_handler(name, handler):08X}",
             "file": source[0], "group": source[1]}
            for (kind, slot, handler), source in sorted(bindings.items())
            if source is not None
        ]
    return rows


def save_bindings(overlays, path=None, section=CORRECTED):
    """Rewrite one section of labels/placements.json, keeping the others."""
    path = path or bindings_path()
    data = _read_bindings(path)
    data[section] = _bindings_to_rows(overlays)
    data["note"] = ("Models picked by hand in the Level Editor (\"corrections\"), "
                    "over what the objects' own code attaches, and names given "
                    "to models.")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    return path

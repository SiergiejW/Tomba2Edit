"""Where a level's objects stand - the signs, the doors, the chests.

WHAT IS AND ISN'T IN THE LEVEL FILES

An MDAT is the room and nothing else. Everything standing in it - the
signposts, the doors, the ladders, the treasure chests - is a part of
the area's asset-pack SMST, and an SMST says nothing about where a part
belongs: every part is modelled around its own origin (see
gui/smst/smst_parser.py). A handful of an asset pack's parts are the
exception, authored in room coordinates because they only ever appear
once - AREA_04's four water surfaces - and those are already in place.
Everything else needs telling.

What tells it is not in TOMBA2.DAT at all. It is a table in the area's
overlay, the Axx.BIN that gui.main_window.overlay_for_area() finds -
the same file functions/clut_anim.py reads the animated palettes out
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
It CAN be read out of a savestate, which is what bindings_from_state()
below does, and what labels/placements.json holds the results of.

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

The rest was read from PCSX savestates. An area's whole SDAT chunk is loaded verbatim
into RAM, so the model each live object points at says which file and
which group it is; each live object also carries a PSX MATRIX - a 3x3
rotation in 4096ths and three 32-bit translations - 0x30 into a 68-byte
record. Matching those translations against the 16-bit triples in the
overlay is what found the table: in AREA_04 every one of the 64 records
lands on an object the game really has there, and the rotation matrix
agrees with the record's `angle` read as degrees (record 3 says 86, and
the matrix holds cos = 289/4096, sin = 4086/4096, which is 85.95).
"""
import json
import os
import struct
from dataclasses import dataclass

import numpy as np

RECORD = struct.Struct("<BBhhhBBhhhI")
RECORD_SIZE = RECORD.size          # 20

# Where a record's own fields sit, for writing changed ones back.
POSITION_AT = 2
ANGLE_AT = 10

# Where an overlay's own addresses can point - the same window
# functions/clut_anim.py uses, and for the same reason: the overlays
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

#     u8  type      \  what the actor is allocated as. 0xFF here ends
#     u8  alloc     /  the table; alloc is 2 or 5 on the whole disc
#     u8  persist   bit 0x80 counts it against the apples rather than
#                   the chests; the low bits are the third argument
#     u8  reward    which pickup it is. 4 is the orange crystal
#     i16 x, y, z
#     i16 bit       which bit of the collected-items bitmap is its own
#     u8  field2a
#     u8  behaviour
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

# One instance record in RAM, in a savestate: a model pointer, then a
# PSX MATRIX at +0x30 (the 3x3 is at +0x1C) and an x/y/z scale.
INSTANCE_SIZE = 68
INSTANCE_MODEL = 0x00
INSTANCE_ROTATION = 0x1C
INSTANCE_TRANSLATION = 0x30

# PCSX-Reloaded savestate: a 32-byte header, a version word, one byte
# of "was this HLE", then a 128x96 RGB screenshot, then the 2MB of RAM.
STATE_MAGIC = b"STv4"
STATE_RAM = 32 + 4 + 1 + 128 * 96 * 3
PSX_RAM_SIZE = 0x200000
PSX_RAM_BASE = 0x80000000

# DuckStation savestate: 'DUCC', a version, a title and a serial, then
# a run of u32 fields of which the last four are the ones that matter -
# how the payload is compressed, how big it is either way, and where it
# starts. The payload is a stream of named sections whose layout is
# DuckStation's own business and changes between versions; RAM is
# somewhere inside it and is found rather than seeked to (see
# solve_origin).
DUCK_MAGIC = b"DUCC"
DUCK_TITLE = 128
DUCK_SERIAL = 32
DUCK_FIELDS = 4 + 4 + DUCK_TITLE + DUCK_SERIAL   # where the u32 run starts
DUCK_COMPRESSION = DUCK_FIELDS + 8 * 4           # the last four fields
DUCK_NONE, DUCK_DEFLATE, DUCK_ZSTD = 0, 1, 2

# A chunk of the DAT is loaded on a 2KB boundary - every one seen in a
# state is, and the disc is read in 2KB sectors - so a base that is not
# is not a base. That is what makes solving for one cheap: there are
# only a couple of thousand places it can be.
LOAD_ALIGN = 0x800

# How many of a buffer's words have to land on a model before its load
# address is believed. A real one is agreed on by dozens; a wrong one
# picks up a handful by chance.
ANCHOR_POINTERS = 12

# How many of an overlay's probes have to agree before its position in a
# buffer is believed - see anchor_origin().
ANCHOR_RUNS = 3

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
        """Which item is inside a chest."""
        return self.config & CONTENTS_MASK

    @property
    def effect(self):
        """The chest's effect id - the top nibble of the same field."""
        return self.config >> CONTENTS_BITS

    @property
    def art_reward(self):
        """The reward with its flag bit taken off - what indexes the
        table in functions/pickup_art.py. The spawner masks it the same
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
        from functions.pickup_art, which is what says a reward 4 is a
        hundred-AP crystal - without it the number has to do. A chest
        doesn't use that table at all."""
        if self.chest:
            what = CHEST_KINDS.get(self.reward & PICKUP_REWARD_MASK,
                                   f"chest kind {self.reward}")
            return f"{what} (item {self.contents}) #{self.bit}"
        what = art.label() if art is not None else f"reward {self.reward}"
        return f"{what} #{self.bit}"

    def describe(self, art=None):
        if self.chest:
            what = CHEST_KINDS.get(self.reward & PICKUP_REWARD_MASK,
                                   f"chest kind {self.reward}")
            return (f"{what}, holds item {self.contents}, effect "
                    f"{self.effect}, chest bit {self.bit}")
        bits = [f"reward {self.reward}",
                f"{'apple' if self.apple else 'chest'} bit {self.bit}"]
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
    kind, alloc, persist, reward, x, y, z, bit, _f2a, behaviour, config = \
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
                  behaviour=behaviour, config=config)


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
    bits inside a table are numbered in order, and requiring that leaves
    no entry that two overlays both claim."""
    tables = []
    for number, address in enumerate(pickup_addresses(exe_path)):
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
    there. functions.labels.NOT_LABELS is what keeps the labels loader
    from trying to read it as one."""
    from functions import labels
    return os.path.join(labels.labels_dir(), BINDINGS_FILE)


# The two sections of labels/placements.json. "overlays" is what the
# savestate correlation worked out and is rewritten wholesale every time
# it is run again; "corrections" is what a person put right by eye in
# the Level Editor, is never rewritten by the correlation, and wins.
LEARNED = "overlays"
CORRECTED = "corrections"


def _read_bindings(path=None):
    try:
        with open(path or bindings_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _rows_to_bindings(rows):
    out = {}
    for row in rows or ():
        try:
            out[(int(row["kind"]), int(row["slot"]),
                 int(row["handler"], 16))] = (int(row["file"]), int(row["group"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


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
    sections = (section,) if section else (LEARNED, CORRECTED)
    out = {}
    for name in sections:
        out.update(_rows_to_bindings((data.get(name) or {}).get(overlay_name)))
    return out


def _bindings_to_rows(overlays):
    rows = {}
    for name, bindings in sorted(overlays.items()):
        # A binding of None is "this object has no model" - which is
        # what an object starts as, so writing it down would only be
        # recording that nothing is known.
        rows[name] = [
            {"kind": kind, "slot": slot, "handler": f"0x{handler:08X}",
             "file": source[0], "group": source[1]}
            for (kind, slot, handler), source in sorted(bindings.items())
            if source is not None
        ]
    return rows


def save_bindings(overlays, path=None, section=LEARNED):
    """Rewrite one section of labels/placements.json, leaving the other
    exactly as it was.

    `overlays` is {overlay name: {(kind, slot, handler): (file, group)}}.
    Which section it goes in decides what happens to it later: the
    savestate correlation owns LEARNED and rewrites all of it, so a
    correction made by hand has to go in CORRECTED to survive the next
    run of it."""
    path = path or bindings_path()
    data = _read_bindings(path)
    data.setdefault(LEARNED, {})
    data.setdefault(CORRECTED, {})
    data[section] = _bindings_to_rows(overlays)
    data["note"] = (
        "Which asset-pack part each of an area's placed objects is drawn "
        "with. Not on the disc - the binding is an immediate in the "
        f"handler's own code. \"{LEARNED}\" is read back out of PCSX "
        "savestates by functions.placement.bindings_from_state() and is "
        f"rewritten whenever that is run again; \"{CORRECTED}\" is what "
        "somebody put right by eye in the Level Editor, wins over it, and "
        "is never touched by it.")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"note": data["note"], LEARNED: data[LEARNED],
                   CORRECTED: data[CORRECTED]}, f, indent=1)
        f.write("\n")


# --------------------------------------------------------------------
# Reading a savestate
# --------------------------------------------------------------------

def _duckstation_payload(data):
    """A DuckStation state's section stream, decompressed."""
    kind, compressed, plain, at = struct.unpack_from("<4I", data,
                                                     DUCK_COMPRESSION)
    body = data[at:at + compressed]
    if len(body) < compressed:
        raise PlacementError("the state is cut short - its payload is missing")
    if kind == DUCK_NONE:
        return body
    if kind == DUCK_DEFLATE:
        import zlib
        return zlib.decompress(body)
    if kind == DUCK_ZSTD:
        try:
            import zstandard
        except ImportError:
            raise PlacementError(
                "this state is Zstandard-compressed and the zstandard module "
                "isn't installed. Either `pip install zstandard`, or set "
                "DuckStation's Save State Compression to Deflate or None and "
                "take the state again.")
        return zstandard.ZstdDecompressor().decompress(
            body, max_output_size=max(plain, 1) + 1)
    raise PlacementError(f"unknown save state compression {kind}")


def state_memory(path):
    """(bytes holding PSX RAM, where PSX 0x80000000 sits in them).

    The offset is None when the format does not say - which is the
    DuckStation case: its payload is a stream of named sections whose
    layout is version-specific, so RAM is somewhere inside rather than
    at a fixed place. solve_origin() works it out from the state's own
    pointers instead.

    PCSX-Reloaded is the simple one: a fixed header and a screenshot,
    then RAM. Both its plain and its gzipped form are read."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        import gzip
        data = gzip.decompress(data)
    if data.startswith(STATE_MAGIC):
        ram = data[STATE_RAM:STATE_RAM + PSX_RAM_SIZE]
        if len(ram) < PSX_RAM_SIZE:
            raise PlacementError(
                f"the state holds only {len(ram)} bytes of RAM, "
                f"not {PSX_RAM_SIZE}")
        return ram, 0
    if data.startswith(DUCK_MAGIC):
        return _duckstation_payload(data), None
    # A plain 2MB dump is worth taking too - it is what every other
    # emulator's "save memory" gives, and it needs no unwrapping.
    if len(data) == PSX_RAM_SIZE:
        return data, 0
    raise PlacementError(
        "this isn't a savestate this can read - PCSX-Reloaded (\"STv4\"), "
        "DuckStation (\"DUCC\") and a raw 2MB RAM dump are what it knows")


def state_ram(path):
    """Just the memory out of a state whose layout says where RAM is."""
    data, origin = state_memory(path)
    if origin is None:
        raise PlacementError("this state's RAM has to be located first")
    return data[origin:origin + PSX_RAM_SIZE]


# How many places in a chunk to look for, and how much of each. Twelve
# rather than a handful because a probe can miss for reasons that say
# nothing about whether the area is loaded - see find_chunk().
PROBE_COUNT = 12
PROBE_BYTES = 48

# How many times one probe's bytes are allowed to turn up before the
# rest are ignored. A short run that repeats all over RAM is no evidence
# either way, and following every copy of it only costs time.
MAX_PROBE_HITS = 16


def match_chunk(ram, dat_path, dat_start, dat_end):
    """(where the chunk sits in `ram`, how many of the probes agreed,
    how many could vote) - the working half of find_chunk() below, split
    out so that two areas both claiming to be loaded can be told apart
    by which is better supported.

    The chunk goes into RAM verbatim, so this is a vote: twelve probes
    taken through it, every place each one turns up, and the base that
    most of them agree on.

    A vote rather than unanimity because a probe can fail while the area
    is perfectly well loaded. The game writes into the chunk it has
    loaded, so a probe can land on bytes that have since been changed;
    and a probe can also turn up somewhere else entirely, since areas
    share assets and a purified area is a copy of the one it mirrors.
    Requiring all three of three probes to agree - which is what this
    did - threw away AREA_05 for one probe out of three landing 0x6C
    away from where the other two put it."""
    size = dat_end - dat_start
    votes = {}
    usable = 0
    with open(dat_path, "rb") as f:
        for n in range(PROBE_COUNT):
            at = int(size * (n + 0.5) / PROBE_COUNT) & ~3
            f.seek(dat_start + at)
            probe = f.read(PROBE_BYTES)
            if len(probe) < PROBE_BYTES or probe.count(probe[:1]) == len(probe):
                continue
            usable += 1
            seen, found = set(), ram.find(probe)
            while found >= 0 and len(seen) < MAX_PROBE_HITS:
                seen.add(found - at)
                found = ram.find(probe, found + 1)
            for base in seen:
                votes[base] = votes.get(base, 0) + 1
    if not votes:
        return None, 0, usable
    base = max(votes, key=votes.get)
    return base, votes[base], usable


def find_chunk(ram, dat_path, dat_start, dat_end, origin=0):
    """Where an area's SDAT chunk is loaded in RAM, as a PSX address, or
    None. `origin` is where PSX 0x80000000 sits in `ram`."""
    at, agreed, usable = match_chunk(ram, dat_path, dat_start, dat_end)
    # A strict majority of the probes that could vote at all. Anything
    # less is one area's assets turning up inside another's.
    if at is None or agreed * 2 <= usable:
        return None
    return PSX_RAM_BASE + at - origin


def anchor_origin(data, overlay_path, overlay_base=None):
    """Where PSX 0x80000000 sits in a buffer, worked out from the area's
    overlay, or None.

    The overlay is a file on the disc that is loaded whole at an address
    the overlays themselves give up (functions.clut_anim.folder_base),
    so finding its bytes in the buffer says where memory begins - and,
    unlike solving it from what is being drawn, it says so whether or
    not anything is."""
    from functions import clut_anim

    if not overlay_path or not os.path.exists(overlay_path):
        return None
    if overlay_base is None:
        overlay_base = clut_anim.folder_base(os.path.dirname(overlay_path))
    if not overlay_base:
        return None
    with open(overlay_path, "rb") as f:
        overlay = f.read()
    votes = {}
    for n in range(PROBE_COUNT):
        at = int(len(overlay) * (n + 0.5) / PROBE_COUNT) & ~3
        probe = overlay[at:at + PROBE_BYTES]
        if len(probe) < PROBE_BYTES or probe.count(probe[:1]) == len(probe):
            continue
        found = data.find(probe)
        while found >= 0:
            origin = found - at - (overlay_base - PSX_RAM_BASE)
            if origin >= 0:
                votes[origin] = votes.get(origin, 0) + 1
            found = data.find(probe, found + 1)
    if not votes:
        return None
    origin = max(votes, key=votes.get)
    return origin if votes[origin] >= ANCHOR_RUNS else None


def solve_origin(data, chunk_at, group_offsets):
    """Where PSX 0x80000000 sits in a buffer whose format doesn't say.

    Worked out from the state's own pointers. Every drawn object holds
    the address of the model it draws - a group inside the area's chunk
    - so the chunk's load address is whichever one makes the most of
    the buffer's pointer-shaped words land exactly on a group. Knowing
    where the chunk sits in the buffer then gives the origin.

    Cheap because a chunk is loaded on a 2KB boundary, which leaves a
    couple of thousand addresses to try rather than two million.

    Scored on NEIGHBOURS rather than on hits. Pointer-shaped words are
    everywhere and a wrong base picks up plenty of them by chance; what
    it cannot fake is the shape of the array they live in, which is one
    object every 68 bytes. Counting only the model pointers that have
    another one exactly a record away is what tells the real base from
    the several thousand that merely score well."""
    if not group_offsets:
        return None
    raw = np.frombuffer(data[:len(data) // 4 * 4], dtype="<u4")
    where = np.nonzero((raw >= RAM_LOW) & (raw < RAM_HIGH))[0]
    if not where.size:
        return None
    words = raw[where].astype(np.int64)
    at = (where * 4).astype(np.int64)
    span = max(group_offsets) + 1
    marks = np.zeros(span, dtype=bool)
    marks[np.asarray(sorted(group_offsets), dtype=np.int64)] = True

    best, score = None, 0
    for base in range(RAM_LOW, RAM_HIGH - span, LOAD_ALIGN):
        offsets = words - base
        hit = (offsets >= 0) & (offsets < span)
        if hit.sum() <= score:
            continue
        hit[hit] = marks[offsets[hit]]
        found = at[hit]
        if found.size <= score:
            continue
        # How many of them have another one exactly one record along.
        neighbours = int(np.isin(found + INSTANCE_SIZE, found).sum())
        if neighbours > score:
            best, score = base, neighbours
    if best is None or score < ANCHOR_POINTERS:
        return None
    return chunk_at - (best - PSX_RAM_BASE)


def live_instances(ram, models, low, high):
    """[(model, translation, rotation), ...] for every object the state
    has standing somewhere.

    `models` maps a RAM address to whatever the caller wants back for
    the model living there - the asset pack's groups, keyed by where
    each group landed. Everything with a translation of (0, 0, 0) is
    skipped: an area keeps a record per object it CAN draw, and the ones
    it isn't drawing sit at the origin."""
    words = np.frombuffer(ram[:len(ram) // 4 * 4], dtype="<u4")
    candidates = np.nonzero((words >= low) & (words < high))[0] * 4
    out = []
    for address in candidates:
        at = int(address)
        if at + INSTANCE_SIZE > len(ram):
            continue
        model = models.get(struct.unpack_from("<I", ram, at)[0])
        if model is None:
            continue
        translation = struct.unpack_from("<3i", ram, at + INSTANCE_TRANSLATION)
        if not any(translation):
            continue
        rotation = struct.unpack_from("<9h", ram, at + INSTANCE_ROTATION)
        out.append((model, translation, rotation))
    return out


def group_offsets(dat_path, dat_start, area_files):
    """{offset within the chunk: (file id, group)} for every SMST group
    an area holds - where its models sit before it is loaded anywhere."""
    from functions.format_detect import smst_groups

    out = {}
    with open(dat_path, "rb") as f:
        for file_id, offset, size in area_files:
            if size <= 0:
                continue
            f.seek(dat_start + offset)
            data = f.read(size)
            try:
                groups = smst_groups(data)
            except Exception:
                continue
            for index, group_offset, *_rest in groups:
                out[offset + group_offset] = (file_id, index)
    return out


def group_addresses(dat_path, dat_start, area_files, base):
    """{RAM address: (file id, group)} for every SMST group in an area
    whose chunk is loaded at `base`."""
    return {base + offset: model for offset, model
            in group_offsets(dat_path, dat_start, area_files).items()}


def bindings_from_state(state_path, dat_path, dat_start, dat_end,
                        area_files, placements, overlay_path=None):
    """{(kind, slot, handler): (file id, group)} learned from one state.

    `area_files` is [(file id, offset in the chunk, size), ...] for the
    area the state is standing in, and `placements` its overlay's table.
    An object is bound when its translation in RAM is exactly a record's
    position - the static ones are, to the unit; the ones that walk
    about are not, and are left alone.

    THE MODEL BELONGS TO THE RECORD BEFORE THE ONE THE MATRIX MATCHES.
    A live object's matrix is the NEXT record's, one along the table,
    while the model pointer at the head of the same 68 bytes is this
    record's - so a position match lands one record late and the model
    has to be walked back.

    That is measured, not assumed. AREA_04's descriptors run in table
    order, and reading them straight through gives 14.1 a signpost and
    then hands the same signpost to 15.0 and 12.0, which are a signpost
    of the other kind and a door. Walked back one, every one of them
    lands on what is really there - checked by eye against the room -
    and record 14.0, which no state had standing anywhere and which
    therefore got no binding at all, picks one up. Across the whole disc
    it also drops the number of object classes drawing with more than
    one model from 24 to 15, which is what a class is supposed to look
    like.

    Even so a binding is a good start rather than the last word: the
    Level Editor's model list is what corrects one, and its "Keep
    models" button is what keeps the correction.

    Raises PlacementError if the state was taken somewhere else, which
    is the one thing worth telling the user about: everything else here
    just comes back with fewer bindings than it might have."""
    ram, origin = state_memory(state_path)
    at, agreed, usable = match_chunk(ram, dat_path, dat_start, dat_end)
    if at is None or agreed * 2 <= usable:
        raise PlacementError(
            "this area's files aren't in that state's memory - it was most "
            "likely taken in a different area")
    if origin is None:
        # A state whose format does not say where RAM begins - the
        # DuckStation case. The overlay is the better of the two ways of
        # finding out, because it is there whether or not the game is
        # drawing anything; what is being drawn is the fallback.
        origin = anchor_origin(ram, overlay_path)
        if origin is None:
            origin = solve_origin(
                ram, at, group_offsets(dat_path, dat_start, area_files))
        if origin is None:
            raise PlacementError(
                "this area is in that state, but there is nothing in it to "
                "measure memory against - no overlay to match and nothing "
                "drawing a model")
    base = PSX_RAM_BASE + at - origin
    models = group_addresses(dat_path, dat_start, area_files, base)
    if not models:
        raise PlacementError("this area holds no models to bind objects to")

    by_position = {}
    for placement in placements:
        by_position.setdefault(placement.position, []).append(placement)
    # The record one earlier in the same table - see the note above on
    # why the model has to be walked back to it.
    before = {(p.table, p.index): p for p in placements}

    out = {}
    for model, translation, _rotation in live_instances(
            ram, models, base, base + (dat_end - dat_start)):
        for placement in by_position.get(translation, ()):
            owner = before.get((placement.table, placement.index - 1))
            if owner is not None:
                out[owner.key()] = model
    if not out and not any(
            True for _m, _t, _r in live_instances(
                ram, models, base, base + (dat_end - dat_start))):
        raise PlacementError(
            "the area is loaded in that state but nothing in it is standing "
            "anywhere - it was most likely taken on a loading screen, before "
            "the level was built")
    return out


# --------------------------------------------------------------------
# Rebuilding labels/placements.json from a folder of savestates
# --------------------------------------------------------------------

# Which overlay belongs to which area. Mirrors
# gui.main_window.MainWindow.OVERLAY_NAMES, kept here so the command
# line below runs without the GUI; the app itself uses MainWindow's.
_PURIFIED_OFFSET = 22
_OVERLAYS = {6: "START.BIN", 7: "DEMO.BIN", 8: "GAME.BIN",
             32: "SOP.BIN", 34: "OPN.BIN", 35: "CRD.BIN"}
for _i in range(22):
    _OVERLAYS[10 + _i] = f"A0{'0123456789ABCDEFGHIJKL'[_i]}.BIN"


def overlay_for_chunk(chunk_index):
    name = _OVERLAYS.get(chunk_index + 6)
    if ((not name or not name.startswith("A0"))
            and chunk_index >= _PURIFIED_OFFSET):
        name = _OVERLAYS.get(chunk_index - _PURIFIED_OFFSET + 6)
    return name


def read_areas(idx_path):
    """{chunk index: (dat_start, dat_end, [(file id, offset, size), ...])}
    for every area with an SDAT chunk - the same walk
    functions/idx_parser.py does, without building a tree."""
    areas = {}
    with open(idx_path, "rb") as idx:
        for chunk in range(os.path.getsize(idx_path) // 0x800):
            idx.seek(chunk * 0x800)
            _img0, _img1, start, end, count = struct.unpack("<5I", idx.read(20))
            if not count or end <= start:
                continue
            pointers = struct.unpack(f"<{count}I", idx.read(count * 4))
            entries = [(v >> 24, v & 0xFFFFFF) for v in pointers]
            files = [(file_id, offset,
                      (entries[i + 1][1] if i + 1 < len(entries)
                       else end - start) - offset)
                     for i, (file_id, offset) in enumerate(entries)]
            areas[chunk] = (start, end, files)
    return areas


def _learn(states_folder, cd_folder, bin_folder, out_path=None):
    """Walk a folder of savestates and write labels/placements.json."""
    idx_path = os.path.join(cd_folder, "TOMBA2.IDX")
    dat_path = os.path.join(cd_folder, "TOMBA2.DAT")
    areas = read_areas(idx_path)
    tables = {}
    learned = {}
    for root, _dirs, names in os.walk(states_folder):
        for name in sorted(names):
            state = os.path.join(root, name)
            try:
                ram = state_ram(state)
            except (PlacementError, OSError):
                continue
            # AREA_01 is skipped: it holds the models every area shares
            # and is resident whatever the state is standing in. An area
            # and its purified twin hold the same bytes, so both can
            # match - which is no trouble at all, since they run on the
            # same overlay and the lower chunk is the one to name. Two
            # unrelated areas both matching is a different thing, and
            # the one whose probes agree most is the one that is really
            # there.
            hits = []
            for chunk, (start, end, _files) in sorted(areas.items()):
                if chunk == 1:
                    continue
                base, agreed, usable = match_chunk(ram, dat_path, start, end)
                if base is not None and agreed * 2 > usable:
                    hits.append((agreed / max(usable, 1), chunk))
            if not hits:
                print(f"{name}: no area - skipped")
                continue
            best = max(share for share, _c in hits)
            chunk = min(c for share, c in hits if share == best)
            overlay = overlay_for_chunk(chunk)
            others = {overlay_for_chunk(c) for _s, c in hits} - {overlay}
            if others:
                print(f"{name}: AREA_{chunk:02X} ({best:.0%} of probes) over "
                      f"{sorted(others)}")
            path = os.path.join(bin_folder, overlay) if overlay else None
            if not path or not os.path.exists(path):
                print(f"{name}: AREA_{chunk:02X} has no overlay - skipped")
                continue
            if overlay not in tables:
                tables[overlay] = load_placements(path)
            start, end, files = areas[chunk]
            found = bindings_from_state(state, dat_path, start, end, files,
                                        tables[overlay])
            learned.setdefault(overlay, {}).update(found)
            print(f"{name}: AREA_{chunk:02X} ({overlay}) "
                  f"{len(tables[overlay])} objects, {len(found)} bound")
    save_bindings(learned, out_path)
    print(f"\n{sum(len(b) for b in learned.values())} bindings over "
          f"{len(learned)} overlays -> {out_path or bindings_path()}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 4:
        print(__doc__)
        print("usage: python -m functions.placement <savestates> <CD folder> "
              "<BIN folder> [out.json]")
        raise SystemExit(2)
    _learn(*sys.argv[1:5])


"""What a crystal or an apple looks like, read out of MAIN.EXE.

A pickup carries no art of its own. Its record (functions/placement.py)
says only which REWARD it is - 0 is the one-heart apple, 4 the hundred-AP
orange crystal - and one resident routine draws them all, looking the
rest up by that reward. Two tables settle it.

THE REWARD TABLE, at 0x800A29CC, eight bytes each - the base and the
field order are the routine's own, read off the lui/addiu pair at
0x8004A8A4 rather than guessed:

    i16 sequence          which animation to play - an index into the
                          sequence table below
    u16 clut              the palette to draw it with, as a PSX CLUT
                          attribute. 1 means "not a resident sprite":
                          the pickup comes out of the area's own bank
                          instead, and both the sequence table and the
                          file it indexes are different ones
    u8  width, height     the pickup's box, in world units
    i16 item              which inventory item it grants, -1 for the
                          ones that are health or AP rather than a thing

THE SEQUENCE TABLE, at 0x80017334: a pointer per sequence, each to a run
of four-byte steps -

    u16 frame             which sprite of the bank to show
    u16 control           the top two bits are what to do next and the
                          low fourteen are how many ticks to wait:
                            0x0000  show it, then fall through
                            0x4000  show it, then jump to the address in
                                    the NEXT word
                            0x8000  show it and stop there
                            0xC000  jump as 0x4000 and take the step
                                    jumped to as the current one

That is the whole animation system. The orange crystal is sequence 1 and
reads 6,5,4,3,2,1,2,3,4,5 at two ticks a frame before jumping back to
the top - a ten-step loop that swings between frames 1 and 6.

WHICH BANK THE FRAMES ARE NUMBERED IN

Two of them. A clut of 1 means the reward is not a resident sprite: its
frames come out of the AREA's own SPRT - SDAT file 10, which is where
the routine's second resource base 0x800ECF80 points, ten entries into
the file table at 0x800ECF58 - and its sequences out of a per-area table
the overlay holds, addressed by 0x800A58FC[area]. Everything else uses
the resident bank, the SPRT at the very front of the DAT that every area
shares, and the sequence table above.
"""
import struct
from dataclasses import dataclass

from functions import game_build, psx_vram

# The PS-EXE header holds the load address at 0x18 and the image from
# 0x800 - the same two numbers functions/placement.py reads.
EXE_MAGIC = b"PS-X EXE"
EXE_BASE_AT = 0x18
EXE_TEXT = 0x800

REWARD_TABLE = 0x800A29CC
REWARD = struct.Struct("<hHBBh")
REWARD_SIZE = REWARD.size          # 8

# The table has no terminator, so it is read to a length. Fifty is where
# it stops making sense - entry 50 asks for sprite 2912 out of a bank of
# a few hundred - and the highest reward the disc actually uses is 37.
REWARD_COUNT = 50

# A reward's top bit isn't part of the number: the spawner keeps it as a
# flag of its own and masks the reward down to seven bits before looking
# anything up.
REWARD_MASK = 0x7F

SEQUENCE_TABLE = 0x80017334

# The other bank: a pointer per area, into that area's own overlay, and
# the SDAT id its sprites live in.
AREA_SEQUENCES = 0x800A58FC
AREA_SEQUENCE_COUNT = 22
AREA_BANK_FILE = 10

# Where an overlay lands, for reading a per-area sequence out of one.
OVERLAY_BASE = 0x80108F9C
STEP = struct.Struct("<HH")
STEP_SIZE = 8                      # a jump step carries its target too

# What the top two bits of a step's control word say to do.
GO_ON, JUMP, STOP, JUMP_RELOAD = 0x0000, 0x4000, 0x8000, 0xC000
OPCODE, TICKS = 0xC000, 0x3FFF

# The clut field has two values that aren't palettes. 1 says the pickup
# isn't a resident sprite at all; 0 says draw it with whatever palette
# the sprite itself carries, which is what the routine's `field_0x5c = 0`
# leaves behind. Anything else overrides the sprite's own.
AREA_BANK = 1
OWN_PALETTE = 0

# How far to walk a sequence before calling it runaway. The longest on
# the disc is ten steps.
MAX_STEPS = 64


# What a reward actually gives, from the switch the pickup runs when it
# is touched. Everything not listed grants the inventory item the reward
# table names, so it is described by that instead.
GRANTS = {
    0: "+1 heart", 1: "+2 hearts",
    4: "100 AP", 5: "200 AP", 6: "500 AP", 7: "1,000 AP",
    8: "5,000 AP", 9: "10,000 AP", 10: "20,000 AP", 11: "100,000 AP",
    17: "magic gauge",
}


# Chests are the exception: they are models, not sprites. The routine at
# 0x80040410 builds one out of TWO parts of file 1 - a body and a lid -
# and takes both group numbers from this table, indexed by which chest it
# is. Red and green are confirmed from savestates; 2 and 3 are the other
# two colours, in an order nobody has checked yet.
CHEST_MODELS = 0x800A3B28
CHEST_KIND_COUNT = 4
CHEST_PARTS = 2
CHEST_FILE = 1

# Where each of those two parts sits relative to the chest's own origin,
# six bytes of x, y, z apiece. This is the whole of a chest's skeleton:
# the body is at the origin and the lid is lifted off it. Without it the
# two are drawn at the same point and grow through each other.
CHEST_OFFSETS = 0x800A3B1C
OFFSET = struct.Struct("<hhh")

# What every item is called. Twelve bytes each, the last eight being
# pointers to the name and the description - and on the retail disc
# those point at strings inside MAIN.EXE, so the names can just be read.
ITEM_TABLE = 0x800A2BE8
ITEM = struct.Struct("<BBBBII")
ITEM_COUNT = 0xA8

# US retail's; another build's while it is open (functions/game_build.py).
_BUILD = game_build.Addresses(globals(), main=(
    "REWARD_TABLE", "SEQUENCE_TABLE", "AREA_SEQUENCES", "OVERLAY_BASE",
    "CHEST_MODELS", "CHEST_OFFSETS", "ITEM_TABLE"),
    slots=("AREA_BANK_FILE", "CHEST_FILE"))

# A name ends at a nul and breaks over a newline in the menus.
NUL = b"\x00"
BREAK = "\n"


class PickupArtError(ValueError):
    """Raised when MAIN.EXE can't be read for any of this."""


@dataclass
class Frame:
    """One step of an animation."""

    frame: int              # which sprite of the bank
    ticks: int              # how long it stays up


@dataclass
class RewardArt:
    """Everything the game knows about how one reward is drawn."""

    reward: int
    width: int
    height: int
    item: int               # -1 when it grants health or AP, not a thing
    sequence: int
    clut: int
    frames: tuple = ()      # the sequence, expanded
    loops: bool = False     # whether it runs forever or stops on the last
    name: str = ""          # what the item it grants is called, if any
    semi_transparent: bool = False

    @property
    def bank(self):
        """Which sprite bank its frames are numbered in."""
        return "resident" if self.resident else "area"

    @property
    def resident(self):
        """Whether it comes out of the shared sprite bank. The handful
        that don't are drawn from their own area's."""
        return self.clut != AREA_BANK

    @property
    def recolored(self):
        """Whether the reward overrides the sprite's own palette. Seven
        rewards share one crystal and differ only here."""
        return self.clut not in (AREA_BANK, OWN_PALETTE)

    @property
    def clut_xy(self):
        """Where the palette it is recoloured with sits in VRAM, in
        halfword coordinates, or None when it keeps its own."""
        return psx_vram.clut_xy(self.clut) if self.recolored else None

    @property
    def still(self):
        return len(self.frames) <= 1

    @property
    def grants(self):
        """What touching it gives, in words."""
        if self.name:
            return self.name
        if self.reward in GRANTS:
            return GRANTS[self.reward]
        return f"item {self.item}" if self.item >= 0 else "something"

    def label(self):
        """Short enough for a row in the Level Editor's list."""
        return f"{self.grants}{'' if self.still else ', animated'}"

    def describe(self):
        frames = "/".join(str(f.frame) for f in self.frames) or "none"
        where = self.clut_xy
        if where:
            palette = f"recoloured from VRAM {where}"
        elif self.resident:
            palette = "the sprite's own palette"
        else:
            palette = "drawn from the area's own bank"
        return (f"reward {self.reward}: {self.width}x{self.height}, "
                f"frames {frames}{' looping' if self.loops else ''}, "
                f"{palette}")


def _image(exe_path):
    try:
        with open(exe_path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise PickupArtError(str(e)) from e
    if len(data) < EXE_TEXT or data[:8] != EXE_MAGIC:
        raise PickupArtError("that isn't a PS-EXE")
    return data, struct.unpack_from("<I", data, EXE_BASE_AT)[0]


def _at(data, base, address, size=1):
    at = address - base + EXE_TEXT
    if at < 0 or at + size > len(data):
        raise PickupArtError(f"0x{address:08X} is outside MAIN.EXE")
    return at


def read_sequence(exe_path, index):
    """(frames, loops) for one animation, walked from its first step.

    A sequence that jumps back on itself is cut where it repeats: the
    frames up to that point are the whole of what it shows."""
    data, base = _image(exe_path)
    return _sequence(data, base, index)


def _sequence(data, base, index):
    at = _at(data, base, SEQUENCE_TABLE + index * 4, 4)
    address = struct.unpack_from("<I", data, at)[0]
    frames, seen = [], set()
    while len(frames) < MAX_STEPS:
        if address in seen:
            return tuple(frames), True
        seen.add(address)
        at = _at(data, base, address, STEP_SIZE if len(data) else 4)
        frame, control = STEP.unpack_from(data, at)
        frames.append(Frame(frame=frame, ticks=control & TICKS))
        opcode = control & OPCODE
        if opcode == STOP:
            return tuple(frames), False
        if opcode in (JUMP, JUMP_RELOAD):
            address = struct.unpack_from("<I", data, at + 4)[0]
        elif opcode == GO_ON:
            address += 4
        else:
            return tuple(frames), False
    return tuple(frames), False


def chest_models(exe_path):
    """{chest kind: ((file, group), (file, group))} - the body and lid
    each kind of chest is built from.

    Unlike everything else here these are real models out of the DAT, so
    the Level Editor can draw a chest without being taught anything."""
    data, base = _image(exe_path)
    out = {}
    for kind in range(CHEST_KIND_COUNT):
        at = _at(data, base, CHEST_MODELS + kind * CHEST_PARTS * 2,
                 CHEST_PARTS * 2)
        groups = struct.unpack_from(f"<{CHEST_PARTS}H", data, at)
        out[kind] = tuple((CHEST_FILE, group) for group in groups)
    return out


def item_names(exe_path):
    """{item id: name} out of MAIN.EXE.

    A name is stored as a pointer, so this follows it; the newline the
    game breaks the name over in a menu is turned back into a space."""
    data, base = _image(exe_path)
    out = {}
    for item in range(ITEM_COUNT):
        try:
            at = _at(data, base, ITEM_TABLE + item * ITEM.size, ITEM.size)
        except PickupArtError:
            break
        pointer = ITEM.unpack_from(data, at)[4]
        try:
            first = _at(data, base, pointer)
        except PickupArtError:
            continue
        last = data.find(NUL, first)
        text = data[first:last if last >= 0 else first]
        if text:
            out[item] = text.decode("ascii", "replace").replace(BREAK, " ")
    return out


def chest_offsets(exe_path):
    """((x, y, z), ...) for a chest's parts, in the game's own axes.

    The same for every kind - only the models differ - so it is one
    tuple rather than one per chest."""
    data, base = _image(exe_path)
    at = _at(data, base, CHEST_OFFSETS, CHEST_PARTS * OFFSET.size)
    return tuple(OFFSET.unpack_from(data, at + i * OFFSET.size)
                 for i in range(CHEST_PARTS))


def _area_sequence(exe, exe_base, overlay, area, index):
    """(frames, loops) for a sequence out of ONE AREA's own table.

    Same steps as the resident sequences, but both the table of pointers
    and the steps themselves live in the overlay - so the walk is over
    the overlay's bytes, at the overlay's own load address."""
    at = _at(exe, exe_base, AREA_SEQUENCES + area * 4, 4)
    table = struct.unpack_from("<I", exe, at)[0]
    if not table:
        return (), False

    def word(address):
        off = address - OVERLAY_BASE
        if off < 0 or off + 4 > len(overlay):
            raise PickupArtError(f"0x{address:08X} is outside the overlay")
        return struct.unpack_from("<I", overlay, off)[0]

    address, frames, seen = word(table + index * 4), [], set()
    while len(frames) < MAX_STEPS:
        if address in seen:
            return tuple(frames), True
        seen.add(address)
        off = address - OVERLAY_BASE
        if off < 0 or off + 4 > len(overlay):
            raise PickupArtError(f"0x{address:08X} is outside the overlay")
        frame, control = STEP.unpack_from(overlay, off)
        frames.append(Frame(frame=frame, ticks=control & TICKS))
        opcode = control & OPCODE
        if opcode == STOP:
            return tuple(frames), False
        if opcode in (JUMP, JUMP_RELOAD):
            address = word(address + 4)
        elif opcode == GO_ON:
            address += 4
        else:
            return tuple(frames), False
    return tuple(frames), False


def reward_art(exe_path, count=REWARD_COUNT, overlay=None, area=None):
    """{reward: RewardArt} for every reward MAIN.EXE describes.

    `overlay` and `area` are wanted only for the handful of rewards
    drawn out of the area's own bank - without them those come back with
    no frames rather than wrong ones."""
    data, base = _image(exe_path)
    names = item_names(exe_path)
    out = {}
    for reward in range(count):
        at = _at(data, base, REWARD_TABLE + reward * REWARD_SIZE, REWARD_SIZE)
        sequence, clut, width, height, item = REWARD.unpack_from(data, at)
        art = RewardArt(reward=reward, width=width, height=height, item=item,
                        sequence=sequence, clut=clut,
                        name=names.get(item, ""))
        try:
            if art.resident:
                art.frames, art.loops = _sequence(data, base, sequence)
            elif overlay and area is not None and 0 <= area < AREA_SEQUENCE_COUNT:
                art.frames, art.loops = _area_sequence(data, base, overlay,
                                                       area, sequence)
        except PickupArtError:
            art.frames, art.loops = (), False
        out[reward] = art
    return out


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        print("usage: python -m functions.pickup_art <MAIN.EXE>")
        raise SystemExit(2)
    for reward, art in sorted(reward_art(sys.argv[1]).items()):
        print(art.describe())

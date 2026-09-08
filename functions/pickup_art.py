"""What a crystal or an apple looks like, read out of MAIN.EXE.

A pickup carries no art of its own. Its record (functions/placement.py)
says only which REWARD it is - 0 is the one-heart apple, 4 the hundred-AP
orange crystal - and one resident routine draws them all, looking the
rest up by that reward. Two tables settle it.

THE REWARD TABLE, at 0x800A29D0, eight bytes each:

    u8  width, height     the pickup's box, in world units
    i16 item              which inventory item it grants, -1 for the
                          ones that are health or AP rather than a thing
    u16 sequence          which animation to play - an index into the
                          sequence table below
    u16 clut              the palette to draw it with, as a PSX CLUT
                          attribute. 1 means "not a resident sprite":
                          the pickup comes out of the area's own bank
                          instead, and both the sequence table and the
                          file it indexes are different ones

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

WHAT IS NOT HERE is the sprite bank the frames are numbered in. For a
resident pickup that is the SPRT at the front of the DAT, the one every
area shares.
"""
import struct
from dataclasses import dataclass

from functions import psx_vram

# The PS-EXE header holds the load address at 0x18 and the image from
# 0x800 - the same two numbers functions/placement.py reads.
EXE_MAGIC = b"PS-X EXE"
EXE_BASE_AT = 0x18
EXE_TEXT = 0x800

REWARD_TABLE = 0x800A29D0
REWARD = struct.Struct("<BBhHH")
REWARD_SIZE = REWARD.size          # 8

# Nothing on the disc rewards past here; the table runs on into other
# data, so it is read to a length rather than to a terminator.
REWARD_COUNT = 32

SEQUENCE_TABLE = 0x80017334
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


def reward_art(exe_path, count=REWARD_COUNT):
    """{reward: RewardArt} for every reward MAIN.EXE describes."""
    data, base = _image(exe_path)
    out = {}
    for reward in range(count):
        at = _at(data, base, REWARD_TABLE + reward * REWARD_SIZE, REWARD_SIZE)
        width, height, item, sequence, clut = REWARD.unpack_from(data, at)
        art = RewardArt(reward=reward, width=width, height=height, item=item,
                        sequence=sequence, clut=clut)
        if art.resident:
            try:
                art.frames, art.loops = _sequence(data, base, sequence)
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

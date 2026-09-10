"""ANMP (animation) parser - the pose tables TANP, BETP, ALFD and the
map's ALFP/MDAP all are.

format_detect gets as far as saying a blob is one of these: a u32 table
whose entries are (u24 offset, u8 tag), the offsets climbing, the first
being the table's own length. What follows is what each of those
pointers names.

ONE POINTER, ONE FRAME

Every pointer is a frame of animation, and the frames sit back to back
behind the table. Tomba's own TANP is 1152 of them.

THE TAG BYTE IS THE FRAME'S SHAPE

It is not an id. Read it as:

    bits 0-5   how many limbs this frame rotates
    bit 7      the frame also carries a root translation, ahead of them
    bit 6      it carries a scale per limb as well - see below

which makes a plain frame (limbs + root) * 3 values long. Each value is
12 bits, so it is ceil(slots * 4.5) bytes. Checked against every frame
on the retail disc: 13440 of 13440 whose pointer has bit 6 clear have
exactly that many bytes before the next pointer. Tomba's frames are
0x91 - seventeen limbs and a root - and come to 81 bytes, which is the
number the Blender scripts this follows had hard-coded.

That the count is per FRAME and not per file is worth knowing: one file
mixes them. Tomba's TANP has five 20-limb frames among its 18-limb
ones.

BIT 6 IS A PER-LIMB SCALE

A flagged frame is nine bytes a slot, not four and a half: the three
rotations as usual, then three more 12-bit values. FUN_80076904 shifts
each of those left by three and stores them at the part's +0x38, which
is its scale - so 512 in the file is 4096, or 1.0.

Checked against the whole disc: all 2464 flagged frames end exactly
where the next frame begins under this layout, as do all 13440
unflagged ones, and 61737 of the 76203 scale values are exactly 4096.
It is what the sea anemone stretches with, and the water and earth pigs.

A VALUE

Three per limb, in the order X, Y, Z.

    rotation     value / 0x1000 of a full turn, about x, then y, then z
    translation  the root's three, signed 12-bit, in world units
    scale        4096 = 1.0, flagged frames only

The rotation is unsigned and wraps, so 0xFFF and 0 are the same angle.
FUN_80076904 writes the three straight into the part's rotation SVECTOR
at +0x08, +0x0A and +0x0C in that order, and
f_UpdateActorSequentialAxisPartTransforms turns that into a matrix with
RotMatrixX, then RotMatrixY, then RotMatrixZ - so the limb's local
rotation is Rx * Ry * Rz.

Both halves are measured, not just read: frame 98 of the ghost guard's
animation reproduces all sixteen live rotation SVECTORs in a savestate
value for value, and Rx * Ry * Rz reproduces the matrices the game
built from them to within 14 parts in 4096 across 33 of 34 characters
in eleven states - where the next-best axis order is 250 to 2150 out.

WHAT IS NOT IN HERE

Which limb is which piece of the model, and where the joints are. An
SMST is a list of polygon groups with no skeleton attached (see
gui/smst/smst_parser.py) and nothing in an ANMP names a group. Both
answers are on the disc, in the 8-byte bone table the area's overlay or
MAIN.EXE carries - see functions/skeleton.py - but which table belongs
to which animation is not, so it is chosen elsewhere and not guessed at
here.
"""
import struct
from dataclasses import dataclass, field

# Three 12-bit values per limb.
VALUES_PER_LIMB = 3
BITS_PER_VALUE = 12
VALUE_MASK = (1 << BITS_PER_VALUE) - 1

# What the tag byte means.
LIMB_COUNT_MASK = 0x3F
ROOT_SLOT_BIT = 0x80
SCALE_BIT = 0x40

# The order the three values come in.
AXIS_ORDER = ("x", "y", "z")

# A flagged frame carries a scale beside each rotation, so six values a
# slot rather than three, which packs to whole bytes.
WIDE_VALUES_PER_LIMB = 6
WIDE_SLOT_BYTES = 9

# The file holds a scale shifted right by three - 512 is 1.0.
SCALE_SHIFT = 3


class ANMPError(ValueError):
    """Raised when a blob doesn't read as an animation table."""


@dataclass
class Frame:
    index: int
    offset: int             # bytes from the start of the blob
    tag: int
    limbs: list = field(default_factory=list)   # [(x, y, z) raw, ...]
    root: tuple = ()        # (x, y, z) signed, or () when bit 7 is clear
    scales: list = field(default_factory=list)  # [(x, y, z), ...], bit 6 only

    @property
    def limb_count(self):
        return len(self.limbs)

    @property
    def flagged(self):
        """Whether bit 6 is set - this frame carries scales too."""
        return bool(self.tag & SCALE_BIT)

    def rotations(self):
        """Each limb's (x, y, z) in radians - the order the file has."""
        import math
        turn = VALUE_MASK + 1
        return [(x / turn * math.tau, y / turn * math.tau, z / turn * math.tau)
                for x, y, z in self.limbs]

    def translation(self):
        """The root's (x, y, z) in world units, or (0, 0, 0)."""
        if not self.root:
            return (0.0, 0.0, 0.0)
        x, y, z = (_signed12(v) for v in self.root)
        return (float(x), float(y), float(z))

    def scaling(self):
        """Each limb's (x, y, z) scale, 1.0 where the frame carries none."""
        if not self.scales:
            return [(1.0, 1.0, 1.0)] * len(self.limbs)
        return [(x / 4096, y / 4096, z / 4096) for x, y, z in self.scales]


def _shortest_step(a, b):
    """How far to turn from raw angle `a` to raw angle `b`, the short way
    round. These are 12-bit angles that wrap, so going from 0xFF0 to
    0x010 is 32 units forwards, not 4064 units back - lerping the raw
    numbers would spin the limb most of a turn the wrong way."""
    half = (VALUE_MASK + 1) // 2
    return (b - a + half) % (VALUE_MASK + 1) - half


def blend(first, second, amount):
    """A pose part-way between two frames, as (rotations, translation,
    scales).

    `amount` runs 0 at `first` to 1 at `second`. Rotations take the
    short way round each axis; the root translation and the scales are
    plain lerps.

    Frames of different shapes are not blended - the limbs would not
    line up - so a pair with different limb counts snaps to whichever
    of the two is nearer."""
    import math

    if second is None or first.limb_count != second.limb_count:
        frame = first if amount < 0.5 or second is None else second
        return frame.rotations(), frame.translation(), frame.scaling()

    turn = VALUE_MASK + 1
    rotations = []
    for (ax, ay, az), (bx, by, bz) in zip(first.limbs, second.limbs):
        x = ax + _shortest_step(ax, bx) * amount
        y = ay + _shortest_step(ay, by) * amount
        z = az + _shortest_step(az, bz) * amount
        rotations.append((x / turn * math.tau,
                          y / turn * math.tau,
                          z / turn * math.tau))

    ta, tb = first.translation(), second.translation()
    translation = tuple(a + (b - a) * amount for a, b in zip(ta, tb))

    sa, sb = first.scaling(), second.scaling()
    scales = [tuple(a + (b - a) * amount for a, b in zip(pa, pb))
              for pa, pb in zip(sa, sb)]
    return rotations, translation, scales


@dataclass
class ANMPFile:
    address: int = 0
    frames: list = field(default_factory=list)
    size: int = 0

    def __len__(self):
        return len(self.frames)

    @property
    def limb_counts(self):
        from collections import Counter
        return Counter(f.limb_count for f in self.frames)


def _signed12(value):
    """A 12-bit two's-complement value as a Python int."""
    return value - 0x1000 if value & 0x800 else value


def frame_size(tag):
    """How many bytes the frame a pointer with this tag names takes.

    Three twelve-bit values a slot, rounded up to whole bytes - or six
    of them, exactly nine bytes, when bit 6 says the frame carries a
    scale as well."""
    slots = (tag & LIMB_COUNT_MASK) + (1 if tag & ROOT_SLOT_BIT else 0)
    if tag & SCALE_BIT:
        return slots * WIDE_SLOT_BYTES, slots
    return -(-(slots * VALUES_PER_LIMB * BITS_PER_VALUE) // 8), slots


def _unpack_12bit(data, at, count):
    """`count` 12-bit values from `at`, packed two to three bytes and
    big-endian within each pair - the order the game writes them and the
    Blender scripts read them (three hex digits at a time)."""
    out = []
    for i in range(count):
        bit = i * BITS_PER_VALUE
        byte = at + bit // 8
        if byte + 1 >= len(data):
            raise ANMPError(f"frame runs past the end of the blob at {byte:#x}")
        pair = (data[byte] << 8) | data[byte + 1]
        out.append((pair >> 4) & VALUE_MASK if bit % 8 == 0 else pair & VALUE_MASK)
    return out


def parse_anmp(data, address=0):
    """Every frame in one ANMP blob. Raises ANMPError if it isn't one."""
    if len(data) < 8:
        raise ANMPError("too short for a pointer table")
    first = struct.unpack_from("<I", data, 0)[0] & 0xFFFFFF
    if not first or first % 4 or first > len(data):
        raise ANMPError(f"first pointer is {first:#x}, not a whole table")
    count = first // 4
    raw = struct.unpack_from(f"<{count}I", data, 0)

    frames = []
    for i, value in enumerate(raw):
        offset, tag = value & 0xFFFFFF, value >> 24
        size, slots = frame_size(tag)
        if not slots or offset + size > len(data):
            continue
        root, limbs, scales = (), [], []
        if tag & SCALE_BIT:
            # Nine bytes a slot: rotation then scale, slot by slot, so
            # each one starts on a byte boundary of its own.
            at = offset
            if tag & ROOT_SLOT_BIT:
                root = tuple(_unpack_12bit(data, at, VALUES_PER_LIMB))
                at += WIDE_SLOT_BYTES
            for _limb in range(tag & LIMB_COUNT_MASK):
                six = _unpack_12bit(data, at, WIDE_VALUES_PER_LIMB)
                limbs.append(tuple(six[:VALUES_PER_LIMB]))
                scales.append(tuple(v << SCALE_SHIFT
                                    for v in six[VALUES_PER_LIMB:]))
                at += WIDE_SLOT_BYTES
        else:
            values = _unpack_12bit(data, offset, slots * VALUES_PER_LIMB)
            if tag & ROOT_SLOT_BIT:
                root = tuple(values[:VALUES_PER_LIMB])
                values = values[VALUES_PER_LIMB:]
            limbs = [tuple(values[n:n + VALUES_PER_LIMB])
                     for n in range(0, len(values), VALUES_PER_LIMB)]
        frames.append(Frame(index=i, offset=offset, tag=tag,
                            limbs=limbs, root=root, scales=scales))

    if not frames:
        raise ANMPError("no frame in the table could be read")
    return ANMPFile(address=address, frames=frames, size=len(data))


def load_anmp(dat_file_path, address, size):
    """Read and parse the ANMP blob at `address` in the DAT."""
    if not size:
        raise ANMPError("no size for this entry, so there is no blob to read")
    with open(dat_file_path, "rb") as f:
        f.seek(address)
        data = f.read(size)
    return parse_anmp(data, address=address)

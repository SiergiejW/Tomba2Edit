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
    bit 6      something else, undecoded - see below

which makes the frame (limbs + root) * 3 values long. Each value is 12
bits, so a frame is ceil(slots * 4.5) bytes. Checked against every
frame on the retail disc: 13336 of 13336 whose pointer has bit 6 clear
have exactly that many bytes before the next pointer. Tomba's frames
are 0x91 - seventeen limbs and a root - and come to 81 bytes, which is
the number the Blender scripts this follows had hard-coded.

That the count is per FRAME and not per file is worth knowing: one file
mixes them. Tomba's TANP has five 20-limb frames among its 18-limb
ones.

WHAT BIT 6 IS NOT KNOWN

2465 frames on the disc set it. The base size still reads correctly,
but the gap to the next pointer is anything from 18 to 598 bytes more
than that, with no pattern found here - so those frames are decoded at
their base size and the remainder ignored. It is not a limb count, and
it is not a fixed extra block.

A VALUE

Three per limb, in the order Y, Z, X - not X, Y, Z.

    rotation     value / 0xFFF of a full turn
    translation  the root's three, signed 12-bit, in world units

The rotation is unsigned and wraps, so 0xFFF and 0 are the same angle.
Karlos of the Tomba Club found the game feeding these to the GTE's
rotation matrix ops (gte_rtv0_b), which is consistent with them being
plain Euler angles about the three axes.

WHAT IS NOT IN HERE

Which limb is which piece of the model, and where the joints are. An
SMST is a list of polygon groups with no skeleton attached (see
gui/smst/smst_parser.py) and nothing in an ANMP names a group. That
mapping is knowledge, like the file names are, and it lives in a
skeleton file rather than being guessed at here.
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
UNKNOWN_BIT = 0x40

# The order the three values come in.
AXIS_ORDER = ("y", "z", "x")


class ANMPError(ValueError):
    """Raised when a blob doesn't read as an animation table."""


@dataclass
class Frame:
    index: int
    offset: int             # bytes from the start of the blob
    tag: int
    limbs: list = field(default_factory=list)   # [(y, z, x) raw, ...]
    root: tuple = ()        # (y, z, x) signed, or () when bit 7 is clear

    @property
    def limb_count(self):
        return len(self.limbs)

    @property
    def flagged(self):
        """Whether the undecoded bit 6 is set on this frame."""
        return bool(self.tag & UNKNOWN_BIT)

    def rotations(self):
        """Each limb's (x, y, z) in radians, in axis order rather than
        the order the file stores them."""
        import math
        out = []
        for y, z, x in self.limbs:
            out.append((x / (VALUE_MASK + 1) * math.tau,
                        y / (VALUE_MASK + 1) * math.tau,
                        z / (VALUE_MASK + 1) * math.tau))
        return out

    def translation(self):
        """The root's (x, y, z) in world units, or (0, 0, 0)."""
        if not self.root:
            return (0.0, 0.0, 0.0)
        y, z, x = (_signed12(v) for v in self.root)
        return (float(x), float(y), float(z))


def _shortest_step(a, b):
    """How far to turn from raw angle `a` to raw angle `b`, the short way
    round. These are 12-bit angles that wrap, so going from 0xFF0 to
    0x010 is 32 units forwards, not 4064 units back - lerping the raw
    numbers would spin the limb most of a turn the wrong way."""
    half = (VALUE_MASK + 1) // 2
    return (b - a + half) % (VALUE_MASK + 1) - half


def blend(first, second, amount):
    """A pose part-way between two frames, as (rotations, translation).

    `amount` runs 0 at `first` to 1 at `second`. Rotations take the
    short way round each axis; the root translation is a plain lerp.

    Frames of different shapes are not blended - the limbs would not
    line up - so a pair with different limb counts snaps to whichever
    of the two is nearer."""
    import math

    if second is None or first.limb_count != second.limb_count:
        frame = first if amount < 0.5 or second is None else second
        return frame.rotations(), frame.translation()

    turn = VALUE_MASK + 1
    rotations = []
    for (ay, az, ax), (by, bz, bx) in zip(first.limbs, second.limbs):
        y = ay + _shortest_step(ay, by) * amount
        z = az + _shortest_step(az, bz) * amount
        x = ax + _shortest_step(ax, bx) * amount
        rotations.append((x / turn * math.tau,
                          y / turn * math.tau,
                          z / turn * math.tau))

    ta, tb = first.translation(), second.translation()
    translation = tuple(a + (b - a) * amount for a, b in zip(ta, tb))
    return rotations, translation


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
    """How many bytes the frame a pointer with this tag names takes -
    (limbs + root) * 3 twelve-bit values, rounded up to whole bytes."""
    slots = (tag & LIMB_COUNT_MASK) + (1 if tag & ROOT_SLOT_BIT else 0)
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
        values = _unpack_12bit(data, offset, slots * VALUES_PER_LIMB)
        root = ()
        if tag & ROOT_SLOT_BIT:
            root = tuple(values[:VALUES_PER_LIMB])
            values = values[VALUES_PER_LIMB:]
        limbs = [tuple(values[n:n + VALUES_PER_LIMB])
                 for n in range(0, len(values), VALUES_PER_LIMB)]
        frames.append(Frame(index=i, offset=offset, tag=tag,
                            limbs=limbs, root=root))

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

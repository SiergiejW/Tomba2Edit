"""How a character is put together from its body parts.

A character model is not one mesh. It is a set of separate pieces - head,
upper arm, forearm, hand, thigh, shin, foot - each its own little model,
placed every frame by walking a bone tree. Assembling one by hand means
guessing which piece hangs off which and by how much; the game does not
guess, because it carries the tree as data.

Three things go into it, and they are in three different places.

The tree lives in MAIN.EXE as a packed array of 8-byte records:

    s16 parent      index of the bone this one hangs from, -1 for a root
    s16 x, y, z     where it sits relative to that parent, at rest

Tomba's is at file offset 0x947A8 (RAM 0x800A3FA8 - MAIN.EXE loads at
0x80010000 behind a 2048-byte header) and runs 17 records. It has two
roots, bones 0 and 8, the upper and lower body; bone 15, the hair, hangs
off the head, so the two are one tree rather than two.

The pieces live in an archive of sub-models, the same shape as the other
asset archives on this disc:

    u16 ?, u16 count, then count u32 offsets from the head of the archive

and bone i is sub-model i. Tomba's archive holds 21 sub-models for 17
bones; the spare four are costume alternates, and the pig suit swaps its
head and both hands for three of them.

The pose lives in RAM, one 68-byte node per bone, which is what a
savestate shows and what read_nodes reads:

    +0x00  SVECTOR  x, y, z, parent      the record above, copied in
    +0x08  SVECTOR  rotation             animation writes this
    +0x10  SVECTOR  a second rotation    added while walking
    +0x18  MATRIX   3x3 rotation x4096, 2 bytes pad, 3 s32 translation
    +0x38  SVECTOR  scale, 4096 = 1.0
    +0x40  u32      pointer to this bone's sub-model

The rule that places a bone is the one every node in three savestates
obeys to the unit, animated or not:

    t_child = t_parent + M_parent * local_translation / 4096

so a rest pose needs nothing but the tree, and an animated pose needs
only each bone's rotation matrix on top of it.

What is not worked out here is how the rotation SVECTOR at +0x08 encodes
that matrix - it is not Euler angles in any axis order, in the usual
4096-units-to-a-turn convention. Animation therefore still has to read
the matrices rather than the angles.
"""
import struct

NODE = 0x44             # a runtime node
RECORD = 8              # a bone in the MAIN.EXE table
ONE = 4096              # 1.0, in the fixed point the GTE works in
IDENTITY = (ONE, 0, 0, 0, ONE, 0, 0, 0, ONE)

# MAIN.EXE is linked to this address and carries a 2048-byte header, so
# an address seen in RAM and an offset into the file convert both ways.
EXE_LOAD = 0x80010000
EXE_HEADER = 0x800


def exe_offset(address):
    return address - EXE_LOAD + EXE_HEADER


def exe_address(offset):
    return offset - EXE_HEADER + EXE_LOAD


def read_table(exe, offset, bones):
    """The bone tree: [(parent, x, y, z), ...]."""
    return [struct.unpack_from("<4h", exe, offset + i * RECORD)
            for i in range(bones)]


def valid_table(bones):
    """Whether a run of records can be a tree.

    A parent always sits earlier in the array than its child, so a table
    that refers forwards is not one."""
    if not bones or bones[0][0] != -1:
        return False
    for i, (parent, *_offset) in enumerate(bones):
        if parent < -1 or parent >= i:
            return False
    return True


def archive_offsets(data, at=0):
    """Where each sub-model starts, from an archive header.

    Offsets are from the head of the archive, and the last thing before
    the first sub-model is the table itself - which is what makes a
    header recognisable."""
    _spare, count = struct.unpack_from("<2H", data, at)
    if not 0 < count < 4096 or at + 4 + count * 4 > len(data):
        return None
    offsets = struct.unpack_from(f"<{count}I", data, at + 4)
    if offsets[0] != 4 + count * 4:
        return None                 # the table must end where the data starts
    return list(offsets)


def read_nodes(ram, at, count):
    """The live nodes out of a RAM image - one per bone."""
    out = []
    for i in range(count):
        o = at + i * NODE
        x, y, z, parent = struct.unpack_from("<4h", ram, o)
        out.append({
            "parent": parent,
            "local": (x, y, z),
            "rotation": struct.unpack_from("<3h", ram, o + 8),
            "walk": struct.unpack_from("<3h", ram, o + 0x10),
            "matrix": struct.unpack_from("<9h", ram, o + 0x18),
            "world": struct.unpack_from("<3i", ram, o + 0x2C),
            "scale": struct.unpack_from("<3h", ram, o + 0x38),
            "model": struct.unpack_from("<I", ram, o + 0x40)[0],
        })
    return out


def find_node_arrays(ram, least=8):
    """[(offset, count)] for every character posed in a RAM image.

    A node is recognised by the three things about it that cannot be
    coincidence together: a model pointer into RAM, a plausible scale,
    and three rows of a rotation matrix that are all 4096 long."""
    out = []
    at = 0
    while at + NODE <= len(ram):
        if _is_node(ram, at):
            start, count = at, 0
            while _is_node(ram, at):
                count += 1
                at += NODE
            if count >= least:
                out.append((start, count))
            continue
        at += 4
    return out


def _is_node(ram, at):
    if at + NODE > len(ram):
        return False
    pointer = struct.unpack_from("<I", ram, at + 0x40)[0]
    if pointer >> 24 != 0x80 or (pointer & 0x1FFFFF) >= len(ram):
        return False
    scale = struct.unpack_from("<3h", ram, at + 0x38)
    if not all(0 < v <= 4 * ONE for v in scale):
        return False
    matrix = struct.unpack_from("<9h", ram, at + 0x18)
    for row in range(3):
        length = sum(matrix[row * 3 + k] ** 2 for k in range(3)) ** 0.5
        if not 0.9 * ONE < length < 1.1 * ONE:
            return False
    parent = struct.unpack_from("<h", ram, at + 6)[0]
    return -1 <= parent <= 255


def assemble(bones, matrices=None, root=(0, 0, 0)):
    """Place every bone: [(matrix, (x, y, z))] in world space.

    With no matrices this is the rest pose - the tree laid out with no
    rotation anywhere, which is what a model viewer wants to show before
    any animation is applied."""
    placed = []
    for i, (parent, x, y, z) in enumerate(bones):
        rotation = IDENTITY if matrices is None else tuple(matrices[i])
        if parent < 0:
            placed.append((rotation, tuple(root)))
            continue
        upper, origin = placed[parent]
        here = tuple(
            origin[r] + (upper[r * 3 + 0] * x + upper[r * 3 + 1] * y
                         + upper[r * 3 + 2] * z) // ONE
            for r in range(3))
        placed.append((rotation, here))
    return placed


def children(bones):
    """{parent: [child, ...]} - the tree the other way round."""
    out = {}
    for i, (parent, *_rest) in enumerate(bones):
        out.setdefault(parent, []).append(i)
    return out

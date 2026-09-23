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
    +0x10  SVECTOR  tween step           added to +0x08 once a tick
    +0x18  MATRIX   3x3 rotation x4096, 2 bytes pad, 3 s32 translation
    +0x38  SVECTOR  scale, 4096 = 1.0
    +0x40  u32      pointer to this bone's sub-model

f_InitializeMultiPartActorModel writes that layout: record halfword 0
goes to +0x06 and 1..3 to +0x00, the scale is set to unit, and +0x40
takes entry i of the sub-model archive - though f_SetActorPartModel can
re-point a part afterwards, and the ghost guard's tongue is entries
16..24 rather than 7..15.

The rule that places a bone is the one every node in three savestates
obeys to the unit, animated or not:

    t_child = t_parent + M_parent * local_translation / 4096

which is f_UpdateActorUnscaledPartTransforms: MulMatrix0 leaves the
parent's matrix in the GTE, and the ApplyRotMatrix straight after it
turns the local offset by exactly that. A root bone is no exception -
its offset is turned by the ACTOR's own matrix at +0x98 and added to the
actor's position at +0xAC, so a root sits at its own offset rather than
at the origin.

+0x10 IS A TWEEN STEP, NOT A SECOND ROTATION

FUN_80075ff8 fills it: the shortest way round from where a limb is now
to where the next frame wants it, divided by the number of ticks the
move is given. FUN_80075f0c then adds it into +0x08 once a tick. So a
state caught mid-move holds a rotation a step or two off the frame it
is heading for, which is worth knowing before reading one as ground
truth.

SCALE HAS NO TWEEN STEP

The scale at +0x38 is real and bit-6 ANMP frames write it. The file stores
three 12-bit values per limb after the rotation; FUN_80076904 shifts each
left by three, making file value 512 become runtime 4096 (1.0). Ordinary
frames leave the previous scale untouched. FUN_80075ff8 computes tween
deltas only for translation and rotation; when its target is a scaled
frame, the target scale is written immediately rather than interpolated.

Most scaled actors compose local rotation times scale under the parent's
matrix. Sea Anemones are a deliberate exception: their A04 routine first
uses that scaled hierarchy to place joints, then rebuilds world rotations
without inherited scale and applies each segment's own scale. That keeps a
stretched stalk long without compounding every ancestor's width into its
head. See formats/animation/anmp_skeleton.py for both paths.

HOW +0x08 ENCODES THE MATRIX

Plain Euler angles after all, 4096 units to a turn, applied about x,
then y, then z:

    R_local = Rx(vx) * Ry(vy) * Rz(vz)      M_child = M_parent * R_local

which is f_UpdateActorSequentialAxisPartTransforms doing RotMatrixX,
RotMatrixY, RotMatrixZ onto an identity. Measured against eleven
savestates: peeling the parent off every posed bone and solving for the
composition picks this one for 33 of 34 characters, worst element 14 in
4096 - GTE rounding - where the next-best of the 48 orders and sign
choices tried is 250 to 2150 out. The odd one out is an actor whose
matrices had gone stale, and it fits in the two other states it appears
in.

The three values come off an ANMP frame in that same x, y, z order:
frame 98 of the ghost guard's animation reproduces all sixteen live
rotation SVECTORs value for value.
"""
import array
import struct
import sys

NODE = 0x44             # a runtime node
RECORD = 8              # a bone in the MAIN.EXE table
ONE = 4096              # 1.0, in the fixed point the GTE works in
IDENTITY = (ONE, 0, 0, 0, ONE, 0, 0, 0, ONE)

# The biggest scale a live node may hold and still be believed. Taken
# from the disc rather than picked: the largest value any animation
# frame carries is 17128, in the sea anemone's - which is why 4 * ONE
# was too tight. It rejected the anemone's own stretched segments, so
# find_node_arrays could not see it and no savestate could settle its
# pairing.
MOST_SCALE = 5 * ONE

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


REACH = 512             # no bone sits further than this from its parent

MOST_ROOTS = 3          # see most_roots() below


def most_roots(bones):
    """How many bones may hang off nothing in a skeleton this size.

    A character has an upper body and a pelvis - Tomba's roots are
    bones 0 and 8, the miner's 0 and 8, the Town of the Fishermen pig's
    0 and 10 - so two is what a real skeleton has, and three is
    headroom for a big one that does something unusual.

    Being strict here matters more than it looks. The alternative is
    not a few extra guesses to sort through: a stretch of 0xFFFF filler
    reads as a skeleton in which every bone is a root, and A02.BIN
    holds enough of it to answer a 16-bone search with 4,206 imaginary
    skeletons out of 4,214. Scoring that many against a model is what
    made opening one of that area's animations hang. Capped, the same
    overlay offers four.

    The allowance scales because a small table is far easier to satisfy
    by accident than a large one - three records of filler are nothing,
    where sixteen consecutive plausible ones are rare. Letting a
    3-bone search take three roots puts that overlay back to 5,442
    matches; holding it to two gives eleven."""
    return min(MOST_ROOTS, max(2, bones // 4))


def _shorts(data):
    """The whole binary as signed 16-bit words - what the scan reads.

    Records are four of these, so scanning by word covers both the
    8-byte stride the tables really use and the odd 2-byte alignment a
    block could in principle start on, without re-unpacking each record
    from bytes every time."""
    words = array.array("h")
    usable = len(data) // 2 * 2
    words.frombytes(bytes(data[:usable]))
    if sys.byteorder == "big":
        words.byteswap()
    return words


def tables_of_size(data, bones, words=None):
    """Every offset where a skeleton of exactly `bones` bones starts.

    Nothing in the data says how long a skeleton is. They sit together
    in one block, one immediately after another - MAIN.EXE keeps the
    player's, an area's overlay keeps that area's characters - and the
    game knows each character's bone count from its own code rather than
    from anything written down beside it. So the count has to come from
    outside; the viewer takes it from how many limbs the animation
    actually rotates.

    Trying instead to split the block up by looking for where one table
    ends does not work, and the way it fails is quiet. It needs a rule
    for telling a skeleton's own second root - the pelvis, which hangs
    off nothing - from the root that starts the next skeleton along, and
    there is no such rule: Tomba and the pipe-area miner both put their
    pelvis at index 8, but the Town of the Fishermen pig puts its at 10,
    so any fixed answer chops some character's table in half and hands
    back a plausible-looking piece of one.

    What marks a run of records as a skeleton of this length:

      - it opens on a root, parent -1
      - every other bone hangs off an earlier one, never a later one
      - no bone sits absurdly far from its parent
      - most bones are actually offset from their parent, which is what
        keeps a stretch of zero padding behind one stray -1 from
        reading as a skeleton of bones all in the same place

    WHAT IS DELIBERATELY NOT CHECKED

    Whether the table stops there. It used to be: a match was thrown
    away if the record just past the end could be another bone of the
    same skeleton, on the grounds that a real table would have ended.
    That is not how the game reads one.
    f_InitializeMultiPartActorModel is handed a part count by its
    caller and copies that many records, so a block can hold more than
    any one character takes - and the Donglin Forest koma pig is
    exactly that case. Its four records at A06.BIN 0x392BC, confirmed
    against a savestate, are followed by two more plausible ones, so
    the test discarded the only correct answer and the pig could not be
    paired at all.

    The length has to come from the animation's limb count either way,
    so nothing was being pinned down that the caller did not already
    know. Dropping it costs about four times as many candidates - 685
    against 176 over every overlay at five bone counts, worst case 44
    in one overlay - which is a longer list to choose from, not the
    thousands the roots cap is there to prevent.
    """
    # Two records is not evidence of a skeleton. A pair that passes
    # every test here is a coincidence rather than a find - A02.BIN
    # answers a 2-bone search with 5,648 of them against 11 for three -
    # and nothing on the disc is jointed that simply anyway: the
    # smallest table any savestate has confirmed is the armadillo's
    # seven.
    if bones < 3:
        return []
    if words is None:
        words = _shorts(data)
    step = 4                              # one record, in words
    span = bones * step
    out = []
    limit = len(words) - span
    root_cap = most_roots(bones)
    # Every table actually found on this disc - the player's in
    # MAIN.EXE, the miner's, the pig's, and the block of them in
    # A02.BIN - starts on a 4-byte boundary, which is no surprise for
    # data a MIPS binary indexes as 16-bit pairs. Stepping in whole
    # words rather than halves throws out a large tail of matches that
    # begin midway through a record and are noise, and halves the scan
    # besides.
    for start in range(0, limit + 1, 2):
        if words[start] != -1:            # cheap first filter: a root?
            continue
        moved = 0
        roots = 0
        for i in range(bones):
            at = start + i * step
            parent = words[at]
            if i == 0:
                if parent != -1:
                    break
            elif not -1 <= parent <= i - 1:
                break
            roots += parent == -1
            if roots > root_cap:
                break
            x, y, z = words[at + 1], words[at + 2], words[at + 3]
            if abs(x) > REACH or abs(y) > REACH or abs(z) > REACH:
                break
            moved += (x or y or z) != 0
        else:
            if moved * 2 < bones:
                continue                  # all-but-motionless: padding
            out.append(start * 2)
    return out


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
    if not all(0 < v <= MOST_SCALE for v in scale):
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
            # A root's own offset counts too - the game turns it by the
            # actor's matrix and adds it to the actor's position, which
            # with no actor is the offset itself.
            placed.append((rotation, (root[0] + x, root[1] + y, root[2] + z)))
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

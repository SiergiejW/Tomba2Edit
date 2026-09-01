"""The rest pose, read out of the game instead of measured by hand.

gui/anmp/skeleton.py had to work out three things an animation frame
does not carry - which group each limb turns, what hangs off what, and
where the joints are - and got the first from measurement, the second
from a Blender rig, and the third from an approximation off bounding
boxes. All three are on the disc, in a table of 8-byte records
(functions/skeleton.py):

    s16 parent      the bone this one hangs off, -1 for a root
    s16 x, y, z     where it sits relative to that parent, at rest

which answers what hangs off what and where the joints are exactly. The
first - which group - falls out of it too: bone i is group i, which is
the order an SMST already packs its parts in.

The player's table is in MAIN.EXE; every other character's is in the
overlay for the area they appear in, which is why an NPC needs the area
open before it can be stood up.

Placing a part is a translation and nothing else. The game never
unpacks an SMST: it draws group i with bone i's matrix, so a group's
vertices are already relative to its own joint - which is why an SMST
looks like a heap, all the arm pieces sitting within a unit or two of
each other. That also shows in the hand-measured rest pose this
replaces: every rotation in gui/anmp/tomba_rest.py is within 0.007 of
the identity, because there was never a rotation to find.

The game's axes are not the viewer's. A bone's (x, y, z) becomes
(z, -y, x): the game runs y downwards, and its x is the viewer's depth.
"""
import numpy as np

from functions import skeleton

# The layout Tomba and the pipe-area miner share: an upper body at 0
# with the head and arms on it, a pelvis at 8 with the legs. Anything
# past the legs is named by number - it differs per character, and is
# the hair on Tomba but a pickaxe on the miner.
COMMON = ("chest", "head",
          "left_arm_upper", "left_arm_lower", "left_hand",
          "right_arm_upper", "right_arm_lower", "right_hand",
          "pelvis",
          "left_leg_upper", "left_leg_lower", "left_foot",
          "right_leg_upper", "right_leg_lower", "right_foot")

# The parent list those names describe. Only a skeleton actually shaped
# like this gets them: they are a reading of one particular layout, not
# a general truth about what bone 2 is. The Town of the Fishermen pig
# hangs ears off its head at 2 and 3, puts its arms at 4 and 7 and its
# pelvis at 10, so borrowing COMMON for it would confidently label an
# ear "left_arm_upper". A skeleton that doesn't match is numbered
# instead, which says less but nothing untrue.
HUMANOID = (-1, 0, 0, 2, 3, 0, 5, 6, -1, 8, 9, 10, 8, 12, 13)

# What Tomba's two spare bones are, so his hierarchy keeps the names the
# rest of the viewer already uses for them.
TOMBA_EXTRA = ("ponytail_start", "ponytail_end")

# How much better one skeleton has to fit than another before the
# difference is believed rather than treated as a tie - see best_for.
# MARGIN covers a character's costume variants, which sit well apart
# without being different skeletons; TIE is for scores close enough to
# be the same measurement twice, as Mizuno's 0.09 and her neighbour's
# 0.08 are.
MARGIN = 0.10
TIE = 0.03


def load_sources(exe_path, overlay_path, others=(), cache=None):
    """[(label, bytes)] to look for skeletons in, nearest first.

    This area's own overlay leads, then the player's table in MAIN.EXE.
    `others` is offered for a caller that wants to search wider, but
    MainWindow deliberately does not - see its _skeleton_sources."""
    out = []
    cache = {} if cache is None else cache

    def read(label, path):
        if not path:
            return
        if path not in cache:
            try:
                with open(path, "rb") as f:
                    cache[path] = f.read()
            except OSError:
                cache[path] = None
        if cache[path] is not None:
            out.append((label, cache[path]))

    read("overlay", overlay_path)
    read("MAIN.EXE", exe_path)
    for path in others:
        if path != overlay_path:
            read("other", path)
    return out


def candidates(sources, limbs):
    """Every skeleton with this many bones: [(label, offset, bones)]."""
    out = []
    for label, data in sources or ():
        for offset in skeleton.tables_of_size(data, limbs):
            out.append((label, offset,
                        skeleton.read_table(data, offset, limbs)))
    return out


def pick(sources, limbs):
    """The skeleton to use for a model with this many limbs, or None."""
    found = candidates(sources, limbs)
    return found[0][2] if found else None


def mesh_blocks(model):
    """One vertex array per group, or None for a group with no mesh.

    Built once and handed to fit() for every candidate: an area's
    overlay can offer thousands of same-sized tables to score, and
    rebuilding a model's several thousand vertices as an array inside
    each of those calls is what turned scoring a busy area into a
    visible hang."""
    import numpy as np

    vertices = np.asarray(model.get("vertices") or (), dtype=float)
    groups = model.get("groups") or ()
    if not len(vertices) or not groups:
        return None
    blocks = []
    for group in groups:
        if group.vertex_count < 3:
            blocks.append(None)
        else:
            blocks.append(
                vertices[group.first_vertex:group.first_vertex + group.vertex_count])
    return blocks


def fit(bones, model, blocks=None):
    """How badly a skeleton fits a model - lower is better, or None if
    there is nothing to measure against.

    Bone count alone does not identify a skeleton. An area's overlay
    holds one per character and several of them are the same size, so
    "the first table with the right number of bones" picks a different
    character's proportions about as often as not - which is what a
    posed model coming out subtly, or wildly, wrong looks like.

    The model itself settles it. Its parts are stored in bone-local
    space, so a part's own mesh says how far away the next joint down
    the limb should be: an upper arm's mesh reaches about as far as the
    elbow, a thigh's about as far as the knee. Scoring each bone's
    offset from its parent against how far the parent's mesh actually
    reaches in that direction separates the character's own skeleton
    from a same-sized stranger's clearly - 0.14 against 0.67 and worse
    for the pipe-area miner, 0.19 against 0.41 for the Town of the
    Fishermen pig, both checked against the skeleton their savestate
    proves is right.

    The axes differ between the two: a bone offset is (x, y, z) in the
    game's, a vertex is in the model's, and the one is (z, -y, x) of
    the other."""
    import numpy as np

    if blocks is None:
        blocks = mesh_blocks(model)
    if not blocks:
        return None
    total = 0.0
    counted = 0
    for parent, x, y, z in bones:
        if not 0 <= parent < len(blocks):
            continue
        block = blocks[parent]
        if block is None:
            continue
        distance = (x * x + y * y + z * z) ** 0.5
        if distance < 1e-6:
            continue
        offset = np.array([z / distance, -y / distance, x / distance])
        reach = float(np.max(block @ offset))
        total += abs(distance - reach) / max(distance, 1.0)
        counted += 1
    return total / counted if counted else None


def best_for(sources, model, limb_counts):
    """The skeleton that fits `model` best, as (label, offset, bones,
    limbs, score) - or None.

    Every limb count the animation actually uses is tried, not just the
    commonest: a bone the frames rarely move is still part of the
    skeleton, and which count is the real one is exactly what is not
    known up front. Fit decides between them, so a wrong guess at the
    count loses to the right one on its own merits rather than on the
    order they were tried in."""
    blocks = mesh_blocks(model)
    scored = []
    for rank, (label, data) in enumerate(sources or ()):
        for limbs in limb_counts:
            for offset in skeleton.tables_of_size(data, limbs):
                bones = skeleton.read_table(data, offset, limbs)
                grade = fit(bones, model, blocks)
                scored.append([grade if grade is not None else 9e9,
                               rank, offset, label, bones, limbs])
    if not scored:
        return None

    # Fit tells one character's skeleton from another's easily - the
    # miner's own scores 0.14 against 0.21 and worse for his
    # neighbours', the pig's 0.19 against 0.41. Where it stops deciding
    # anything, two different things are going on, and they want
    # opposite answers.
    #
    # In MAIN.EXE the tables are one character in several outfits.
    # Tomba has five 17-bone tables there, and against his default model
    # they score 0.156 to 0.230 - noise, two of them byte-identical.
    # Fit picks the pig suit's wider shoulders, which is exactly the
    # arms-too-far-apart it produced, so there the earliest table wins
    # instead: the order the game's own data is written in.
    best = min(row[0] for row in scored)
    winner = min(scored, key=lambda row: (row[0], row[1], row[2]))
    if winner[3] == "MAIN.EXE":
        close = [row for row in scored
                 if row[0] <= best + MARGIN and row[3] == "MAIN.EXE"]
        grade, _rank, offset, label, bones, limbs = min(
            close, key=lambda row: (row[1], row[2]))
        return label, offset, bones, limbs, grade, len(scored)

    # An overlay's tables are different characters, and there position
    # says nothing - Mizuno's is the second of four near-identical
    # scorers, so taking the earliest picks a neighbour. What separates
    # her is that her table has as many bones as her animation mostly
    # moves. That only breaks a genuine tie: the miner's own table is
    # 16 bones where his frames mostly use 15, and it wins anyway
    # because 0.14 against 0.21 is fit actually deciding.
    tied = [row for row in scored if row[0] <= best + TIE]
    usual = limb_counts[0] if limb_counts else None
    preferred = [row for row in tied if row[5] == usual]
    grade, _rank, offset, label, bones, limbs = min(
        preferred or tied, key=lambda row: row[0])
    return label, offset, bones, limbs, grade, len(scored)


def humanoid(bones):
    """Whether COMMON's names really describe this skeleton - see it."""
    return tuple(b[0] for b in bones[:len(HUMANOID)]) == HUMANOID


def hierarchy(bones):
    """((name, parent), ...) in the shape the viewer already expects."""
    known = humanoid(bones)
    out = []
    for i, (parent, *_place) in enumerate(bones):
        if known and i < len(COMMON):
            name = COMMON[i]
        elif known and len(bones) == 17 and i - len(COMMON) < len(TOMBA_EXTRA):
            name = TOMBA_EXTRA[i - len(COMMON)]
        else:
            name = f"bone {i}"
        out.append((name, None if parent < 0 else parent))
    return tuple(out)


def joints(bones):
    """Where every bone sits at rest, in the viewer's axes, as (n, 3)."""
    placed = skeleton.assemble(bones)
    return np.array([(z, -y, x) for _matrix, (x, y, z) in placed],
                    dtype=np.float64)


def rest_pose(model, bones, spares=None):
    """Stand a packed SMST up on its own skeleton: (vertices, pivots).

    Each group is moved to its bone's joint - no rotation, because the
    game applies none either. A spare group, one past the animated
    bones, is placed with the bone it stands in for so switching to it
    puts it where its counterpart was."""
    pivots = joints(bones)
    vertices = np.array(model["vertices"], dtype=np.float64)
    groups = model["groups"]

    def place(group_index, at):
        if group_index >= len(groups):
            return
        group = groups[group_index]
        if not group.vertex_count:
            return
        first = group.first_vertex
        block = vertices[first:first + group.vertex_count]
        vertices[first:first + group.vertex_count] = block + at

    for i in range(len(bones)):
        place(i, pivots[i])
    for extra, stands_in_for in (spares or {}).items():
        if stands_in_for < len(bones):
            place(extra, pivots[stands_in_for])
    return vertices, pivots

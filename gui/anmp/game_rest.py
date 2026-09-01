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


def load_sources(exe_path, overlay_path):
    """[(label, bytes)] to look for skeletons in, nearest first.

    The overlay comes first: an area's own characters are the ones being
    posed, and the player's table in MAIN.EXE is the fallback."""
    out = []
    for label, path in (("overlay", overlay_path), ("MAIN.EXE", exe_path)):
        if not path:
            continue
        try:
            with open(path, "rb") as f:
                out.append((label, f.read()))
        except OSError:
            continue
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


def fit(bones, model):
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

    vertices = np.asarray(model.get("vertices") or (), dtype=float)
    groups = model.get("groups") or ()
    if not len(vertices) or not groups:
        return None
    errors = []
    for parent, x, y, z in bones:
        if not 0 <= parent < len(groups):
            continue
        group = groups[parent]
        if not group.vertex_count or group.vertex_count < 3:
            continue
        offset = np.array([z, -y, x], dtype=float)
        distance = float(np.linalg.norm(offset))
        if distance < 1e-6:
            continue
        block = vertices[group.first_vertex:group.first_vertex + group.vertex_count]
        reach = float(np.max(block @ (offset / distance)))
        errors.append(abs(distance - reach) / max(distance, 1.0))
    return float(np.mean(errors)) if errors else None


def best_for(sources, model, limb_counts):
    """The skeleton that fits `model` best, as (label, offset, bones,
    limbs, score) - or None.

    Every limb count the animation actually uses is tried, not just the
    commonest: a bone the frames rarely move is still part of the
    skeleton, and which count is the real one is exactly what is not
    known up front. Fit decides between them, so a wrong guess at the
    count loses to the right one on its own merits rather than on the
    order they were tried in."""
    scored = []
    for limbs in limb_counts:
        for label, offset, bones in candidates(sources, limbs):
            grade = fit(bones, model)
            scored.append((grade if grade is not None else 9e9,
                           label, offset, bones, limbs))
    if not scored:
        return None
    scored.sort(key=lambda row: row[0])
    grade, label, offset, bones, limbs = scored[0]
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

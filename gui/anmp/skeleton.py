"""How an ANMP's limbs hang off an SMST's polygon groups.

An animation frame is a list of rotations and nothing else (see
anmp_parser). To turn that into a pose you need three things it doesn't
carry, and none of them are anywhere on the disc:

    which group each limb rotates
    what hangs off what
    where the joints are

WHICH GROUP. Settled by measurement rather than assumption. Tomba's
model has 21 groups and his TANP rotates 17 limbs, and the limbs line up
with groups 0-16 in order: every left/right pair the ordering predicts
turns out to be a mirrored pair in the model, with matching vertex
counts and opposite x (36/36 at x +/-19, 30/30 at +/-22, 38/38, 33/33,
22/22), the head is the tall 210-vertex group at the top, the pelvis
sits below the chest, and ponytail_start/end land on the model's only
two flat 2-quad pieces. Groups 17-20 are the spares - two more heads and
two more hands, the mouth-open and hand-open variants, drawn instead of
their counterparts rather than as well as them.

WHAT HANGS OFF WHAT. From the rig in Patryk's "Tomba animation
baked.blend", which is where this hierarchy is read off - the one thing
here that came from outside the disc.

WHERE THE JOINTS ARE. Approximated - see _joint_towards. A limb hinges
at the end of itself that faces its parent, which puts a shoulder, a
neck and a hip roughly right, but it is measured off bounding boxes and
not read from anywhere, so a posed limb can sit some units out from
where the game would put it. The real pivots are not on the disc in any
form found so far.
"""
import numpy as np

# (name, index of the limb it hangs off, or None for a root). The order
# IS the order the frame's limbs come in, and the index into the SMST's
# groups. Read off the rig; see the module docstring.
TOMBA_HIERARCHY = (
    ("chest", None),
    ("head", 0),
    ("left_arm_upper", 0),
    ("left_arm_lower", 2),
    ("left_hand", 3),
    ("right_arm_upper", 0),
    ("right_arm_lower", 5),
    ("right_hand", 6),
    ("pelvis", None),
    ("left_leg_upper", 8),
    ("left_leg_lower", 9),
    ("left_foot", 10),
    ("right_leg_upper", 8),
    ("right_leg_lower", 12),
    ("right_foot", 13),
    ("ponytail_start", 1),
    ("ponytail_end", 15),
)

# Anything whose limb count this tool has no hierarchy for is posed as a
# flat list of limbs off a single root - every part rotates about its own
# centre, independently. Wrong, but it moves and it is honest about it.
HIERARCHIES = {len(TOMBA_HIERARCHY): TOMBA_HIERARCHY}


def hierarchy_for(limb_count):
    """The named hierarchy for a model with this many limbs, or a flat
    one. Returns (hierarchy, named) so a caller can say which it got."""
    known = HIERARCHIES.get(limb_count)
    if known:
        return known, True
    return tuple((f"limb {i}", None) for i in range(limb_count)), False


def _joint_towards(bounds, centre, parent_centre):
    """Where a limb hinges: the end of it that faces its parent.

    Take the axis the two centres are furthest apart on - for an upper
    arm out at x +/-19 off a chest at x 0 that is x, for a head above a
    chest it is y - and put the joint on that face of the limb's own
    bounding box, at the limb's centre on the other two axes. An upper
    arm hinges at the shoulder end, a head at the neck, a thigh at the
    hip, and this finds all three.

    The obvious alternative - the point of the box nearest the parent's
    centre - collapses whenever the two overlap, which for a head whose
    hair reaches down past the chest it does: it returns the parent's
    own centre and the limb swings about a point inside its parent."""
    offset = np.asarray(centre) - np.asarray(parent_centre)
    axis = int(np.argmax(np.abs(offset)))
    pivot = np.array(centre, dtype=np.float64)
    low, high = bounds[axis * 2], bounds[axis * 2 + 1]
    pivot[axis] = low if offset[axis] > 0 else high
    return pivot


def rest_pose(model, hierarchy):
    """Stand a packed SMST up into its rest pose, and say where its
    joints are: (vertices, pivots), or None if this model has no
    measured rest pose.

    An SMST stores its parts packed rather than assembled (the arm
    pieces sit within a unit or two of each other), so posing has to
    start by putting them where they belong. For Tomba that placement
    and those joints are measured - see gui/anmp/tomba_rest.py - rather
    than guessed at from bounding boxes the way rest_pivots() below has
    to for everything else."""
    from gui.anmp.tomba_rest import REST_POSE

    names = [name for name, _ in hierarchy]
    if any(name not in REST_POSE for name in names):
        return None
    groups = model["groups"]
    if len(groups) < len(names):
        return None

    verts = np.array(model["vertices"], dtype=np.float64)
    pivots = np.zeros((len(names), 3))
    for i, name in enumerate(names):
        rotation, offset, pivot = REST_POSE[name]
        pivots[i] = pivot
        g = groups[i]
        if not g.vertex_count:
            continue
        at = g.first_vertex
        block = verts[at:at + g.vertex_count]
        verts[at:at + g.vertex_count] = block @ np.array(rotation).T + offset

    # Anything past the animated limbs - Tomba's spare heads and hands -
    # is placed with the part it stands in for, so switching to one puts
    # it in the right place instead of back at the origin.
    for extra, stands_in_for in SPARES.get(len(names), {}).items():
        if extra >= len(groups) or stands_in_for >= len(names):
            continue
        rotation, offset, _pivot = REST_POSE[names[stands_in_for]]
        g = groups[extra]
        if not g.vertex_count:
            continue
        at = g.first_vertex
        block = verts[at:at + g.vertex_count]
        verts[at:at + g.vertex_count] = block @ np.array(rotation).T + offset

    return verts, pivots


# Groups past the animated limbs, and which limb each one stands in for:
# Tomba's mouth-open head and half head, and his two open hands.
SPARES = {17: {17: 1, 18: 1, 19: 4, 20: 7}}


def rest_pivots(groups, hierarchy):
    """Where each limb turns, in model space, as an (n, 3) array.

    A root limb turns about its own centre; anything else about the end
    of itself that faces its parent (see _joint_towards)."""
    centres = []
    for i, _ in enumerate(hierarchy):
        g = groups[i] if i < len(groups) else None
        centres.append(np.array(g.centre if g and g.bounds else (0.0, 0.0, 0.0),
                                dtype=np.float64))
    pivots = []
    for i, (_name, parent) in enumerate(hierarchy):
        g = groups[i] if i < len(groups) else None
        if parent is None or not g or not g.bounds:
            pivots.append(centres[i])
        else:
            pivots.append(_joint_towards(g.bounds, centres[i], centres[parent]))
    return np.array(pivots)


# The rest pose was measured in Blender's Z-up axes and turned into the
# viewer's Y-up ones by this (a -90 degree turn about x, which keeps the
# handedness a plain axis swap would flip). The animation's angles are in
# those same Z-up axes, so a rotation has to be conjugated by it -
# S @ R @ S.T - rather than just used as it stands, or the limbs turn
# about the wrong axes and come apart from each other.
AXIS_CHANGE = np.array([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])


def _euler_matrix(rx, ry, rz):
    """Rotation about x, then y, then z, in the viewer's axes. The signs
    follow the Blender scripts that were used to check this format by
    eye: x as read, y and z negated."""
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(-ry), np.sin(-ry)
    cz, sz = np.cos(-rz), np.sin(-rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return AXIS_CHANGE @ (mz @ my @ mx) @ AXIS_CHANGE.T


def pose_transforms(frame, hierarchy, pivots, translation_scale=1.0):
    """(rotation, offset) per limb, so a vertex v of limb i in the rest
    model poses to `rotation @ (v - pivot) + offset`.

    Composed down the hierarchy: a limb carries its parent's rotation,
    so bending an elbow takes the hand with it."""
    rotations = frame.rotations()
    root = np.array(frame.translation(), dtype=np.float64) * translation_scale

    out = [None] * len(hierarchy)
    order = sorted(range(len(hierarchy)),
                   key=lambda i: _depth(hierarchy, i))
    for i in order:
        if i >= len(rotations):
            out[i] = (np.eye(3), pivots[i] + root)
            continue
        local = _euler_matrix(*rotations[i])
        parent = hierarchy[i][1]
        if parent is None:
            out[i] = (local, pivots[i] + root)
        else:
            prot, poff = out[parent]
            combined = prot @ local
            # where this limb's pivot has been carried to by its parent
            moved = prot @ (pivots[i] - pivots[parent]) + poff
            out[i] = (combined, moved)
    return out


def _depth(hierarchy, i, _seen=None):
    depth = 0
    seen = set()
    while hierarchy[i][1] is not None and i not in seen:
        seen.add(i)
        i = hierarchy[i][1]
        depth += 1
    return depth

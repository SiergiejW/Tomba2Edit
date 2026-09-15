"""Which of two faces in one plane the PSX draws on top.

The GPU has no depth buffer: primitives are linked into an ordering table
by depth, and one linked into a slot already holding others goes in at its
head - so of two faces at one depth, the one put out first is drawn last,
on top. Coplanar faces share their depth, so in the game the earlier packet
of an MDAT entry or SMST group wins (A01's quad #1065 over #1068, slot 0
over slot 3). A depth buffer leaves that to rounding, and they flicker.

face_levels counts, for every polygon, the later polygons of its entry or
group that lie in its plane and overlap it; the viewers pull each face that
many DEPTH_TIE steps toward the camera. A face that overlaps nothing keeps
its depth, so nothing is pushed through anything else.
"""
import numpy as np

NORMAL_MATCH = 0.999        # |cos| between two polygons' normals
PLANE_GAP = 2.0             # units a corner may sit off the other's plane
OVERLAP = 0.5               # extent both must share along each in-plane axis
# One level in normalised device depth: 4 steps of a 24-bit depth buffer.
DEPTH_TIE = 8.0 / (1 << 24)


def face_levels(model, key):
    """[float] per face - see the module docstring. `key` is the polygon
    field that groups them: "entry" for an MDAT, "group" for an SMST."""
    vertices = np.asarray(model.get("vertices") or (), dtype=np.float64).reshape(-1, 3)
    levels = [0.0] * len(model.get("faces") or ())
    owners = {}
    for polygon in model.get("polygons") or ():
        owners.setdefault(polygon.get(key), []).append(polygon)
    for polygons in owners.values():
        if len(polygons) < 2:
            continue
        rings = np.empty((len(polygons), 4, 3))
        for n, polygon in enumerate(polygons):
            ring = vertices[polygon["first_vertex"]:
                            polygon["first_vertex"] + polygon["vertex_count"]]
            rings[n, :len(ring)] = ring
            rings[n, len(ring):] = ring[-1]
        normals = np.cross(rings[:, 1] - rings[:, 0], rings[:, 2] - rings[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 1e-9
        normals[valid] /= lengths[valid, None]
        above = np.zeros(len(polygons))
        for i in np.flatnonzero(valid[:-1]):
            later = np.arange(i + 1, len(polygons))
            later = later[valid[later] & (np.abs(normals[later] @ normals[i]) >= NORMAL_MATCH)]
            if not later.size:
                continue
            offset = np.abs(rings[later] @ normals[i] - normals[i] @ rings[i, 0])
            later = later[offset.max(axis=1) <= PLANE_GAP]
            if not later.size:
                continue
            axis_u = rings[i, 1] - rings[i, 0]
            axis_u /= np.linalg.norm(axis_u) or 1.0
            axis_v = np.cross(normals[i], axis_u)
            shared = np.ones(later.size, dtype=bool)
            for axis in (axis_u, axis_v):
                own, theirs = rings[i] @ axis, rings[later] @ axis
                shared &= (np.minimum(own.max(), theirs.max(axis=1))
                           - np.maximum(own.min(), theirs.min(axis=1))) > OVERLAP
            above[i] = np.count_nonzero(shared)
        for polygon, level in zip(polygons, above):
            for f in range(polygon["first_face"], polygon["first_face"] + polygon["face_count"]):
                levels[f] = float(level)
    return levels


def vertex_levels(model):
    """float32 per vertex: its face's level."""
    out = np.zeros(len(model.get("vertices") or ()), dtype=np.float32)
    for face, level in zip(model.get("faces") or (), model.get("face_levels") or ()):
        if level:
            out[face] = level
    return out

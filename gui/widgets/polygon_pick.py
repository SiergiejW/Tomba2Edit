"""Picking one polygon out of a model, and saying which one it is.

MDAT and SMST are the same packets reached two different ways (see
formats/models/smst_parser.py's header), so they get the same picking: a ray
through the triangles rather than an id buffer, since the geometry is
already in hand and nothing has to be read back out of a framebuffer Qt
owns.

Both viewers hand this module the vertices they actually drew - posed,
spread, whatever - so what gets picked is what is on screen rather than
what was in the file.

WHAT A SELECTION PRINTS

Every printout names the thing in the DAT rather than on screen: the
absolute address of the packet, the group or drawmap entry it belongs
to, and its place inside that. Those three are enough to find it in a
hex editor and enough to point at in conversation.
"""
import numpy as np
from PyQt6.QtGui import QVector4D


def build_face_index(faces, polygons, face_count):
    """Which polygon each triangle belongs to - a quad contributes two.

    Returned as an int array parallel to `faces`, so the ray test can go
    straight from the triangle it hit to the polygon that owns it."""
    lookup = np.zeros(face_count, dtype=np.int64)
    for polygon in polygons:
        first = polygon["first_face"]
        lookup[first:first + polygon["face_count"]] = polygon["index"]
    return np.asarray(faces, dtype=np.int64), lookup


def ray_through(mvp, x, y, width, height):
    """The click at (x, y) as (origin, unit direction) in model space, or
    None if the matrix will not invert."""
    inverse, ok = mvp.inverted()
    if not ok:
        return None

    def unproject(z):
        point = inverse.map(QVector4D(2.0 * x / max(width, 1) - 1.0,
                                      1.0 - 2.0 * y / max(height, 1), z, 1.0))
        if not point.w():
            return None
        return np.array([point.x() / point.w(), point.y() / point.w(),
                         point.z() / point.w()], dtype=np.float64)

    near, far = unproject(-1.0), unproject(1.0)
    if near is None or far is None:
        return None
    direction = far - near
    length = np.linalg.norm(direction)
    if length < 1e-9:
        return None
    return near, direction / length


def nearest_polygon(origin, direction, vertices, faces, face_polygon,
                    drawable=None):
    """The polygon of the nearest triangle the ray crosses, or None.

    Moller-Trumbore over every triangle at once. `drawable` is an
    optional boolean mask over triangles, so a hidden part cannot be
    picked through the empty space where it is not being drawn."""
    if not len(faces):
        return None
    a = vertices[faces[:, 0]]
    edge1 = vertices[faces[:, 1]] - a
    edge2 = vertices[faces[:, 2]] - a
    pvec = np.cross(direction, edge2)
    det = np.einsum("ij,ij->i", edge1, pvec)
    live = np.abs(det) > 1e-12
    if drawable is not None:
        live &= drawable
    if not live.any():
        return None
    inv = np.zeros_like(det)
    inv[live] = 1.0 / det[live]
    tvec = origin - a
    u = np.einsum("ij,ij->i", tvec, pvec) * inv
    qvec = np.cross(tvec, edge1)
    v = np.einsum("j,ij->i", direction, qvec) * inv
    t = np.einsum("ij,ij->i", edge2, qvec) * inv
    hit = live & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6) & (t > 1e-6)
    if not hit.any():
        return None
    return int(face_polygon[int(np.argmin(np.where(hit, t, np.inf)))])


def outline_segments(polygon, vertices):
    """The polygon's edge ring as flat [x, y, z, ...] pairs of points.

    `vertices` is whatever the caller draws with, so the outline lands on
    the geometry rather than beside it.

    A polygon's vertices are stored as a ring - a quad's two triangles
    are (0,1,2) and (0,2,3) - so walking it and joining each point to the
    next closes the outline on its own."""
    first, count = polygon["first_vertex"], polygon["vertex_count"]
    ring = vertices[first:first + count]
    out = []
    for i in range(count):
        out.extend(ring[i])
        out.extend(ring[(i + 1) % count])
    return out


# --- saying which one it is ------------------------------------------

def describe_polygon(polygon, *, owner=""):
    """One line naming a picked polygon by what it is in the DAT.

    `owner` is whatever the format calls the thing it sits in - "group 3"
    for an SMST part, "entry 41" for a drawmap entry."""
    bits = [f"{polygon['kind']} #{polygon['index']}"]
    if owner:
        bits.append(f"in {owner}")
    if polygon.get("slot") is not None:
        bits.append(f"slot {polygon['slot']}")
    bits.append(f"packet @ 0x{polygon['address']:X}")
    bits.append(f"page {polygon['page']}")
    bits.append(f"clut 0x{polygon['clut']:X}")
    if polygon.get("transparent"):
        bits.append(f"semi-transparent (blend {polygon.get('blend')})")
    texels = polygon.get("texels")
    if texels:
        bits.append("uv " + " ".join(f"({u},{v})" for u, v in texels))
    return "  ".join(bits)

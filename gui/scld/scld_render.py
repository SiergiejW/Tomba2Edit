# scld_render.py
"""Builds the vertex/colour buffers for SCLD collision geometry.

Every viewer that draws collision data builds it here, so what they draw
always comes from one place. Nothing in this module touches OpenGL - it
returns plain lists, and the caller uploads them.

Coordinates come back in raw world units; divide by UNIT_SCALE before
handing them to a shader.
"""
import colorsys

# Raw PSX units per rendered unit. MDAT rooms use the same scale, so
# collision drawn at this scale lines up with the room around it.
UNIT_SCALE = 1000.0

# Steps a hue by the golden-ratio conjugate, which spaces consecutive
# entries far apart on the wheel instead of letting them fade into each
# other the way index/count would with dozens of entries.
GOLDEN_RATIO_CONJUGATE = 0.6180339887498949

# Records drop to this opacity when their surfaces are drawn over them.
SCAFFOLD_ALPHA = 0.5

# Cross-entry joins, which belong to two entries at once and so take
# neither entry's colour.
SEAM_COLOR = (0.93, 0.93, 0.85)


def entry_color(index, saturation=0.65, value=0.95):
    """This entry's colour, stable across viewers and redraws."""
    return colorsys.hsv_to_rgb((index * GOLDEN_RATIO_CONJUGATE) % 1.0,
                               saturation, value)


def unkn_color(entry, saturation=0.65, value=0.95):
    """A colour per distinct `unkn` value, so entries sharing one are
    drawn alike. Entries with unkn == 0 come back grey."""
    if not entry.unkn:
        return (0.45, 0.45, 0.45)
    # Hash the value into a hue so unrelated numbers land apart.
    return colorsys.hsv_to_rgb(
        ((entry.unkn * 2654435761) % 65536) / 65536.0, saturation, value)


def room_bounds(vertices, margin=0.1):
    """(x0, x1, z0, z1) around a room's vertices, widened by `margin` of
    its own size so collision running along an edge isn't clipped.

    None when there is nothing to bound."""
    if not vertices:
        return None
    xs = [v[0] for v in vertices]
    zs = [v[2] for v in vertices]
    x0, x1 = min(xs), max(xs)
    z0, z1 = min(zs), max(zs)
    return (x0 - (x1 - x0) * margin, x1 + (x1 - x0) * margin,
            z0 - (z1 - z0) * margin, z1 + (z1 - z0) * margin)


def entries_in_bounds(entries, bounds):
    """Entries whose bounding box overlaps `bounds` at all.

    A coarse filter only: one entry can span several rooms, so an entry
    that overlaps may still be mostly elsewhere. Points are filtered
    individually as well - see `contains`."""
    if bounds is None:
        return list(entries)
    x0, x1, z0, z1 = bounds
    out = []
    for e in entries:
        ex0, ex1 = sorted((e.yyy1, e.yyy2))
        ez0, ez1 = sorted((e.xxx1, e.xxx2))
        if ex0 <= x1 and ex1 >= x0 and ez0 <= z1 and ez1 >= z0:
            out.append(e)
    return out


def contains(bounds, point):
    """Whether a world point falls inside `bounds` (True if unbounded)."""
    if bounds is None:
        return True
    x0, x1, z0, z1 = bounds
    return x0 <= point[0] <= x1 and z0 <= point[2] <= z1


def build_points(entries, reverse_for=None, bounds=None, color_by=None):
    """One point per table3 record.

    `reverse_for(entry)` may return True/False to force an entry's
    direction, or None to leave it to the entry's own header.
    `color_by(entry)` overrides the per-entry colour.

    Returns (verts, colors, ranges, positions):
        ranges[entry.index]    = (first vertex, count), for redrawing one
                                 entry on its own
        positions[entry.index] = that entry's points, scaled for display
    """
    verts, colors, ranges, positions = [], [], {}, {}
    for entry in entries:
        rgb = color_by(entry) if color_by else entry_color(entry.index)
        rev = reverse_for(entry) if reverse_for else None
        pts = entry.trace(reverse=rev)
        start = len(verts)
        for pt in pts:
            if not contains(bounds, pt):
                continue
            verts.append(pt)
            colors.append(rgb)
        ranges[entry.index] = (start, len(verts) - start)
        positions[entry.index] = [(p[0] / UNIT_SCALE, p[1] / UNIT_SCALE,
                                   p[2] / UNIT_SCALE) for p in pts]
    return verts, colors, ranges, positions


def build_lines(scld_file, entries, reverse_for=None, bounds=None,
                surfaces=True, seams=True, color_by=None):
    """Line geometry as consecutive vertex pairs, ready for GL_LINES.

    `surfaces` draws each walkable surface along its entry, `seams` the
    joins where one carries on into the next entry.

    Returns (verts, colors)."""
    verts, colors = [], []

    def add(run, rgb):
        for a, b in zip(run, run[1:]):
            if not (contains(bounds, a) and contains(bounds, b)):
                continue
            verts.append(a)
            verts.append(b)
            colors.append(rgb)
            colors.append(rgb)

    for entry in entries:
        rev = reverse_for(entry) if reverse_for else None
        if surfaces:
            rgb = (color_by(entry, saturation=0.70, value=1.0) if color_by
                   else entry_color(entry.index, saturation=0.70, value=1.0))
            for run in entry.surfaces(reverse=rev):
                add(run, rgb)
    if seams and scld_file is not None:
        for run in scld_file.seams():
            add(run, SEAM_COLOR)
    return verts, colors

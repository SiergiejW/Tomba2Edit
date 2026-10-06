"""Collision as lines, built and drawn one way by every view that shows it.

A SCLD plane is drawn as the game reads it (formats/collision/scld_geometry.py):
floors, ceilings and sloped faces as lines from where the plane enters a cell
to where it leaves, walls as verticals, lane switches as arrows to the plane
they lead to - plus, optionally, each plane's centre line, the links between
planes, the cell grid, a cross at every record and translucent curtains.
CollisionStyle says which, and how they are coloured; the level editor, the
MDAT view and the SCLD viewer all build from one. A town plane
(formats/collision/town_collision.py) is its outline in the colour of its kind.

Two layers, drawn at different strengths:

    surface    floors, ceilings, arrows and outlines, SURFACE_ALPHA
    vertical   the walls, VERTICAL_ALPHA

and each twice: depth-tested where nothing covers it, then again where
geometry does at HIDDEN of that, so collision inside a wall still shows.
Building touches no GL; Overlay.draw() needs the context current.
"""
import colorsys
import math
from dataclasses import dataclass

import numpy as np
from OpenGL import GL
from PyQt6.QtOpenGL import QOpenGLBuffer, QOpenGLVertexArrayObject

from gui import gl_profile
from formats.collision import scld_render
from formats.collision.scld_geometry import geometry, view as game_to_view

TICK = 12.0                 # half a sample's cross, world units
SURFACE_ALPHA = 1.0
VERTICAL_ALPHA = 0.6
# A town wall or door is also filled in, see-through, so what it covers
# reads at a glance; floors keep their outline only, over the level.
FILL_ALPHA = 0.3
FILLED = ("wall", "door")
HIDDEN = 0.5
LINE_WIDTH = 2.0
FILL_DEPTH = 40.0           # how far a floor's curtain hangs below it, a ceiling's rises above
ARROW_HEAD = 90.0           # a lane-switch arrow's head, world units
POST = 180.0                # the post a lane-switch arrow stands on

# Colours, by what a surface is. Floors and walls keep the level editor's green and red.
PLAIN_SURFACE = (0.3, 0.9, 0.4)
PLAIN_WALL = (1.0, 0.35, 0.3)
CEILING_COLOR = (0.35, 0.65, 1.0)
SLOPE_COLOR = (1.0, 0.6, 0.2)
GRAPPLE_COLOR = (1.0, 0.85, 0.2)
JUNCTION_UP = (0.3, 0.95, 1.0)
JUNCTION_DOWN = (1.0, 0.5, 0.9)
LINK_COLOR = (0.75, 0.55, 1.0)
REDIRECT_COLOR = (0.95, 0.95, 0.95)
BASELINE_COLOR = (0.62, 0.62, 0.68)
CELL_COLOR = (0.38, 0.38, 0.45)

# How a SCLD is coloured: (key, menu label, what it means).
COLOR_MODES = (
    ("type", "What it is", "floor green, ceiling blue, wall red, sloped face orange, "
                           "Grapple-able gold"),
    ("plane", "Plane", "one hue per plane, stable between views"),
    ("material", "Class (footsteps)", "one hue per surface class - kind bits 8-11, which "
                                      "pick what Tomba's footsteps do; class 0 is grey"),
    ("slope", "Plane gradient (unkn)", "by the plane's own gradient value; flat planes grey"),
)


def material_color(kind):
    """A surface's colour by its class - the kind's bits 8-11, which pick what
    Tomba's footsteps do (f_TriggerTombaTerrainFootstep). Class 0, plain ground,
    is grey; every other value a fixed hue of its own."""
    material = (kind >> 8) & 0xF
    if not material:
        return (0.6, 0.6, 0.62)
    return colorsys.hsv_to_rgb((material * 0.61803) % 1.0, 0.75, 1.0)


@dataclass
class CollisionStyle:
    """What a collision overlay draws and how it colours it - shared by every view."""
    floors: bool = True
    ceilings: bool = True
    slopes: bool = True         # sloped wall faces and other 0x80 records
    walls: bool = True
    junctions: bool = True      # lane switches: arrows to the plane Up / Down leads to
    links: bool = False         # the plane reached off each end of a plane, and redirect cells
    baselines: bool = False     # each plane's own line, with an arrow at its end
    cells: bool = False         # the 64-unit grid cells holding geometry
    samples: bool = False       # a cross at every record
    fill: bool = False          # translucent curtains under floors and over ceilings
    numbers: bool = False       # plane numbers (views that can draw text)
    color: str = "type"         # one of COLOR_MODES
    only_plane: object = None   # an entry index: draw only that plane


def _color(style, entry, kind, info):
    if style.color == "plane":
        return scld_render.entry_color(entry.index)
    if style.color == "slope":
        return scld_render.unkn_color(entry)
    if style.color == "material":
        return material_color(kind)
    if info.grapple:
        return GRAPPLE_COLOR
    return {"floor": PLAIN_SURFACE, "ceiling": CEILING_COLOR,
            "wall": PLAIN_WALL}.get(info.role, SLOPE_COLOR)


def legend_html(style=None):
    """The current colouring, as HTML rows of swatch and meaning."""
    style = style or CollisionStyle()

    def swatch(rgb):
        r, g, b = (int(c * 255) for c in rgb)
        return f'<span style="background-color:rgb({r},{g},{b});">&nbsp;&nbsp;&nbsp;&nbsp;</span>'
    rows = []
    if style.color == "type":
        rows += [(PLAIN_SURFACE, "floor"), (CEILING_COLOR, "ceiling"),
                 (PLAIN_WALL, "wall - blocks one way along the plane"),
                 (SLOPE_COLOR, "sloped face (kind bit 0x80, not a floor)"),
                 (GRAPPLE_COLOR, "Grapple sticks (wall kind 0x100, ceiling 0x10 / 0x20)")]
    elif style.color == "plane":
        rows.append(((0.9, 0.5, 0.5), "each plane its own hue"))
    elif style.color == "material":
        rows.append((material_color(0), "class 0 - plain ground"))
        for c in (1, 2, 3, 4, 5, 6, 7, 10):
            note = (" - footstep sound 2" if c in (1, 2) else
                    " - footstep sound 0x8A (high nibble 0)" if c in (5, 6) else
                    " - footstep sound 0x90" if c == 10 else "")
            rows.append((material_color(c << 8), f"class {c}{note}"))
    else:
        rows.append(((0.9, 0.5, 0.5), "hue by the plane's gradient; grey = flat"))
    if style.junctions:
        rows += [(JUNCTION_UP, "lane switch, Up - the post stands on the switch; the arrow is the way Tomba faces after it"),
                 (JUNCTION_DOWN, "lane switch, Down")]
    if style.links:
        rows += [(LINK_COLOR, "plane end leading to another plane"),
                 (REDIRECT_COLOR, "cell that redirects to another plane")]
    if style.baselines:
        rows.append((BASELINE_COLOR, "the plane's own line; the arrow is its end"))
    if style.cells:
        rows.append((CELL_COLOR, "a 64-unit cell holding geometry"))
    return "<br>".join(f"{swatch(rgb)} {text}" for rgb, text in rows)


def pick_sample(lines, mvp, scale, x, y, width, height, reach=10):
    """(entry, record) of the collision sample nearest widget point (x, y),
    within `reach` pixels, or None. `mvp` is the view's QMatrix4x4 over
    positions divided by `scale`. A record is a table3 number, or a tuple for
    a lane switch - ("junction", index)."""
    samples = getattr(lines, "samples", None) or ()
    if not samples:
        return None
    points = np.array([(px, py, pz, 1.0) for px, py, pz, _e, _r in samples]) / (
        scale, scale, scale, 1.0)
    matrix = np.array(mvp.data(), dtype=np.float64).reshape(4, 4).T
    clip = points @ matrix.T
    w = clip[:, 3]
    ahead = w > 1e-6
    sx = (clip[:, 0] / np.where(ahead, w, 1) * 0.5 + 0.5) * width
    sy = (1.0 - (clip[:, 1] / np.where(ahead, w, 1) * 0.5 + 0.5)) * height
    gap = np.hypot(sx - x, sy - y)
    gap[~ahead] = np.inf
    best = int(np.argmin(gap))
    if gap[best] > reach:
        return None
    _px, _py, _pz, entry, record = samples[best]
    return entry, record


def sample_text(entry, record):
    """One SCLD record or lane switch, the way every collision view prints it."""
    plane = geometry(entry)
    if isinstance(record, tuple):
        _name, index = record
        return plane.describe_junction(plane.junctions[index])
    return plane.describe_record(record)


TOWN_COLORS = {
    "floor": (0.3, 0.9, 0.4), "wall": (1.0, 0.35, 0.3),
    "door": (1.0, 0.85, 0.2), "ladder": (0.3, 0.7, 1.0),
    "net": (0.7, 0.5, 1.0), "camera": (0.6, 0.6, 0.6),
    "foothold": (0.4, 1.0, 1.0), "ball": (1.0, 0.6, 1.0),
    "other": (0.9, 0.9, 0.9),
}


class Lines:
    """Line pairs in world units, in the two layers."""

    def __init__(self):
        self.surface, self.surface_colors = [], []
        self.vertical, self.vertical_colors = [], []
        self.fills, self.fill_colors = [], []       # triangles
        self.samples = []           # (x, y, z, entry, record) per pickable point - see pick_sample
        self.ranges = {}            # entry index -> (first vertex, count) in surface
        self.vranges = {}           # the same in vertical
        self.labels = []            # (position, text, rgb): plane numbers
        self.linked = set()         # plane ends already joined, so a link is drawn once

    def line(self, a, b, rgb, vertical=False):
        points, colors = ((self.vertical, self.vertical_colors) if vertical
                          else (self.surface, self.surface_colors))
        points.extend((a, b))
        colors.extend((rgb, rgb))

    def __len__(self):
        return (len(self.surface) + len(self.vertical)) // 2

    def arrays(self, scale=1.0):
        """((surface positions, colours), (vertical positions, colours)) as
        (n, 3) float32, positions divided by `scale`."""
        def pack(points, colors):
            return (np.asarray(points, dtype=np.float32).reshape(-1, 3) / scale,
                    np.asarray(colors, dtype=np.float32).reshape(-1, 3))
        return (pack(self.surface, self.surface_colors),
                pack(self.vertical, self.vertical_colors),
                pack(getattr(self, "fills", ()), getattr(self, "fill_colors", ())))


def _cross(lines, x, y, z, rgb):
    lines.line((x - TICK, y, z), (x + TICK, y, z), rgb)
    lines.line((x, y, z - TICK), (x, y, z + TICK), rgb)


def _arrow(lines, a, b, rgb, toward):
    """A lane-switch arrow: a post standing on the switch cell, a thin line along the floor
    to where the destination plane is, and from the post's top an arrow along `toward` (a
    unit (x, z) in viewer axes): the way Tomba faces once he has switched."""
    top = (a[0], a[1] + POST, a[2])
    lines.line(a, top, rgb)
    lines.line(a, b, rgb)
    ux, uz = toward
    tip = (top[0] + ux * ARROW_HEAD * 2, top[1], top[2] + uz * ARROW_HEAD * 2)
    lines.line(top, tip, rgb)
    back = (tip[0] - ux * ARROW_HEAD, tip[1], tip[2] - uz * ARROW_HEAD)
    wing = ARROW_HEAD * 0.5
    for sign in (1, -1):
        lines.line(tip, (back[0] - uz * wing * sign, back[1], back[2] + ux * wing * sign), rgb)
        lines.line(tip, (back[0], back[1] + wing * sign, back[2]), rgb)


def add_scld(lines, entries, bounds=None, style=None, all_entries=None):
    """Each entry's collision as `style` says. `bounds` (x0, x1, z0, z1) keeps
    only what stands inside it; `all_entries` are the planes a lane switch or
    link may lead to (default: `entries`, which a view may have cut down)."""
    style = style or CollisionStyle()
    inside = scld_render.contains
    geoms = {e.index + 1: geometry(e) for e in (all_entries or entries)}
    for entry in entries:
        if style.only_plane is not None and entry.index != style.only_plane:
            continue
        g = geometry(entry)
        first, vfirst = len(lines.surface), len(lines.vertical)
        wanted = {"floor": style.floors, "ceiling": style.ceilings, "slope": style.slopes}
        for seg in g.segments:
            if not wanted[seg.role]:
                continue
            a, b = game_to_view(*seg.a), game_to_view(*seg.b)
            mid = tuple((p + q) / 2 for p, q in zip(a, b))
            if not inside(bounds, mid):
                continue
            rgb = _color(style, entry, seg.kind, seg.info)
            lines.line(a, b, rgb)
            if style.fill and seg.role != "slope":
                down = -FILL_DEPTH if seg.role == "floor" else FILL_DEPTH
                a2, b2 = (a[0], a[1] + down, a[2]), (b[0], b[1] + down, b[2])
                lines.fills.extend((a, b, b2, a, b2, a2))
                lines.fill_colors.extend((rgb,) * 6)
            lines.samples.append((*mid, entry, seg.record))
            if style.samples:
                _cross(lines, *mid, rgb)
        if style.walls:
            for wall in g.walls:
                a = game_to_view(wall.x, wall.y_bottom, wall.z)
                b = game_to_view(wall.x, wall.y_top, wall.z)
                if not inside(bounds, a):
                    continue
                rgb = _color(style, entry, wall.kind, wall.info)
                lines.line(a, b, rgb, vertical=True)
                lines.samples.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2,
                                      entry, wall.record))
        if style.junctions:
            for index, j in enumerate(g.junctions):
                dest = geoms.get(j.dest)
                a = game_to_view(j.x, j.y, j.z)
                if not inside(bounds, a):
                    continue
                b, toward = a, (0.0, 1.0)
                if dest is not None:
                    tx, tz = dest.closest_point(j.x, j.z)
                    b = game_to_view(tx, j.y, tz)
                    # After the switch Tomba faces along the destination plane's line: the way
                    # it is drawn, or the reverse - Up keeps it when cell flag 0x10 is set, Down
                    # when it is clear (the port's SetDestinationPlane: profileYaw).
                    sign = 1 if (j.direction == "up") == j.facing else -1
                    toward = (sign * dest.dir[1], sign * dest.dir[0])
                _arrow(lines, a, b, JUNCTION_UP if j.direction == "up" else JUNCTION_DOWN, toward)
                lines.samples.append((a[0], a[1] + POST, a[2], entry, ("junction", index)))
        if style.links:
            _links(lines, entry, g, geoms, bounds)
        if style.baselines:
            a = game_to_view(g.x1, g.baseline_y, g.z1)
            b = game_to_view(g.x2, g.baseline_y, g.z2)
            if inside(bounds, a) or inside(bounds, b):
                lines.line(a, b, BASELINE_COLOR)
                dx, dz = b[0] - a[0], b[2] - a[2]
                length = math.hypot(dx, dz)
                if length:
                    ux, uz = dx / length, dz / length
                    for sign in (1, -1):
                        lines.line(b, (b[0] - ux * ARROW_HEAD - uz * ARROW_HEAD * 0.5 * sign, b[1],
                                       b[2] - uz * ARROW_HEAD + ux * ARROW_HEAD * 0.5 * sign),
                                   BASELINE_COLOR)
        if style.cells:
            for slot in g.slots:
                ys = sorted(entry.path[r].pos for r in slot.records if r < len(entry.path))
                y = ys[len(ys) // 2] if ys else g.baseline_y
                ox, oz = g.origin
                x0, z0 = ox + 64 * slot.col, oz + 64 * slot.row
                corners = [game_to_view(x0, y, z0), game_to_view(x0 + 64, y, z0),
                           game_to_view(x0 + 64, y, z0 + 64), game_to_view(x0, y, z0 + 64)]
                if not inside(bounds, corners[0]):
                    continue
                for k in range(4):
                    lines.line(corners[k], corners[(k + 1) % 4], CELL_COLOR)
        if style.numbers:
            mx, mz = (g.x1 + g.x2) / 2, (g.z1 + g.z2) / 2
            lines.labels.append((game_to_view(mx, g.baseline_y, mz), str(g.number),
                                 scld_render.entry_color(entry.index)))
        lines.ranges[entry.index] = (first, len(lines.surface) - first)
        lines.vranges[entry.index] = (vfirst, len(lines.vertical) - vfirst)
    return lines


def _links(lines, entry, g, geoms, bounds):
    """The plane reached off each end of this one (ls / le), and redirect cells."""
    inside = scld_render.contains
    ends = ((0, entry.ls, (g.x1, g.z1)), (1, entry.le, (g.x2, g.z2)))
    for end, number, (sx, sz) in ends:
        other = geoms.get(number)
        if not number or other is None:
            continue
        near = min((0, 1), key=lambda k: math.hypot(
            (other.x1, other.x2)[k] - sx, (other.z1, other.z2)[k] - sz))
        key = frozenset(((entry.index, end), (other.entry.index, near)))
        if key in lines.linked:
            continue
        lines.linked.add(key)
        a = game_to_view(sx, g.end_height(end), sz)
        b = game_to_view((other.x1, other.x2)[near], other.end_height(near),
                         (other.z1, other.z2)[near])
        if inside(bounds, a):
            lines.line(a, b, LINK_COLOR)
    for r in g.redirects:
        other = geoms.get(r.dest)
        if other is None:
            continue
        tx, tz = other.closest_point(r.x, r.z)
        a = game_to_view(r.x, g.baseline_y, r.z)
        if inside(bounds, a):
            lines.line(a, game_to_view(tx, other.baseline_y, tz), REDIRECT_COLOR)


def add_town(lines, planes, transform=lambda p: p, plain=False):
    """Town planes outlined, each in its kind's colour - or, `plain`, in the
    two the level editor draws collision in."""
    for plane in planes:
        rgb = (TOWN_COLORS.get(plane.kind, TOWN_COLORS["other"]) if not plain
               else PLAIN_WALL if plane.kind == "wall"
               else TOWN_COLORS["door"] if plane.kind == "door" else PLAIN_SURFACE)
        corners = [transform(p) for p in plane.outline()]
        for k, corner in enumerate(corners):
            lines.line(corner, corners[(k + 1) % len(corners)], rgb)
        if plane.kind in FILLED and len(corners) >= 3:
            for k in range(1, len(corners) - 1):
                lines.fills.extend((corners[0], corners[k], corners[k + 1]))
                lines.fill_colors.extend((rgb,) * 3)
    return lines


class Overlay:
    """Both layers on the GPU, drawn in their passes."""

    def __init__(self):
        self._layers = [[QOpenGLVertexArrayObject(),
                         QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer),
                         QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer), 0]
                        for _ in range(3)]
        self._pending = None

    def set(self, lines, scale):
        self._pending = lines.arrays(scale)

    def clear(self):
        self.set(Lines(), 1.0)

    def has_lines(self):
        if self._pending is not None:
            return any(len(p) for p, _c in self._pending)
        return any(layer[3] for layer in self._layers)

    def sync(self):
        if self._pending is None:
            return
        for layer, (positions, colors) in zip(self._layers, self._pending):
            vao, vbo, cbo, _count = layer
            if not vao.isCreated():
                vao.create()
            vao.bind()
            for buffer, array, location in ((vbo, positions, 0), (cbo, colors, 1)):
                if not buffer.isCreated():
                    buffer.create()
                buffer.bind()
                data = np.ascontiguousarray(array, dtype=np.float32)
                buffer.allocate(data.tobytes(), data.nbytes)
                GL.glEnableVertexAttribArray(location)
                GL.glVertexAttribPointer(location, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
            vao.release()
            layer[3] = len(positions)
        self._pending = None

    def draw(self, program, surface=True, vertical=True):
        """Draw with `program` bound, untextured, taking colour per vertex
        and opacity from its `alpha` uniform."""
        self.sync()
        wanted = ((self._layers[0], SURFACE_ALPHA, surface, GL.GL_LINES),
                  (self._layers[1], VERTICAL_ALPHA, vertical, GL.GL_LINES),
                  (self._layers[2], FILL_ALPHA, surface, GL.GL_TRIANGLES))
        if not any(layer[3] and on for layer, _a, on, _m in wanted):
            return
        GL.glDepthMask(GL.GL_FALSE)
        gl_profile.set_line_width(LINE_WIDTH)
        blend, cull = GL.glIsEnabled(GL.GL_BLEND), GL.glIsEnabled(GL.GL_CULL_FACE)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glDisable(GL.GL_CULL_FACE)
        for func, scale in ((GL.GL_LESS, 1.0), (GL.GL_GREATER, HIDDEN)):
            GL.glDepthFunc(func)
            for (vao, _vbo, _cbo, count), alpha, on, mode in wanted:
                if not (on and count):
                    continue
                program.setUniformValue("alpha", alpha * scale)
                vao.bind()
                GL.glDrawArrays(mode, 0, count)
                vao.release()
        GL.glDepthFunc(GL.GL_LESS)
        if not blend:
            GL.glDisable(GL.GL_BLEND)
        if cull:
            GL.glEnable(GL.GL_CULL_FACE)
        GL.glDepthMask(GL.GL_TRUE)
        program.setUniformValue("alpha", 1.0)

    def draw_surface(self, first, count, layer=0):
        """Part of the surface (layer 0) or vertical (1) layer again, as it
        stands - for a highlight."""
        vao, _vbo, _cbo, total = self._layers[layer]
        if count <= 0 or first >= total:
            return
        vao.bind()
        GL.glDrawArrays(GL.GL_LINES, first, min(count, total - first))
        vao.release()

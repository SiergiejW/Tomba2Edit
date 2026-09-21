"""Collision as lines, built and drawn one way by every view that shows it.

The level editor's look: a SCLD sample is a small cross in its entry's
colour, a wall record (SCLDEntry.walls) a vertical line from its foot to
its top, and a town plane (functions/town_collision.py) its outline in the
colour of its kind.

Two layers, drawn at different strengths:

    surface    crosses and plane outlines, SURFACE_ALPHA
    vertical   the walls, VERTICAL_ALPHA

and each twice: depth-tested where nothing covers it, then again where
geometry does at HIDDEN of that, so collision inside a wall still shows.
Building touches no GL; Overlay.draw() needs the context current.
"""
import numpy as np
from OpenGL import GL
from PyQt6.QtOpenGL import QOpenGLBuffer, QOpenGLVertexArrayObject

from gui.scld import scld_render

TICK = 12.0                 # half a sample's cross, world units
SURFACE_ALPHA = 1.0
VERTICAL_ALPHA = 0.6
# A town wall or door is also filled in, see-through, so what it covers
# reads at a glance; floors keep their outline only, over the level.
FILL_ALPHA = 0.3
FILLED = ("wall", "door")
HIDDEN = 0.5
LINE_WIDTH = 1.0

# The level editor draws collision as two things - what you stand on and
# what stops you - rather than one colour per plane; the SCLD view keeps its
# own colours, where telling one entry from the next is the point.
PLAIN_SURFACE = (0.3, 0.9, 0.4)
PLAIN_WALL = (1.0, 0.35, 0.3)

def material_color(kind):
    """A floor sample's colour by its record's material - the kind's high
    byte, which picks what Tomba's footsteps do there (A04's footstep actor:
    splashes, snow, fireflies). 0, plain ground, keeps PLAIN_SURFACE; every
    other value a fixed hue of its own."""
    material = (kind >> 8) & 0xFF
    if not material:
        return PLAIN_SURFACE
    import colorsys
    return colorsys.hsv_to_rgb((material * 0.61803) % 1.0, 0.75, 1.0)


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
        self.ranges = {}            # entry index -> (first vertex, count) in surface

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


def add_scld(lines, entries, bounds=None, color_by=None, wall_color=None,
             record_color=None):
    """Each entry's samples as crosses and its walls as verticals.
    `bounds` (x0, x1, z0, z1) keeps only what stands inside it;
    `wall_color` draws every wall alike instead of in its entry's colour;
    `record_color(kind)` colours a sample by its record's kind instead."""
    inside = scld_render.contains
    for entry in entries:
        rgb = color_by(entry) if color_by else scld_render.entry_color(entry.index)
        first = len(lines.surface)
        for (x, y, z), r in zip(entry.trace(), entry.records()):
            if not inside(bounds, (x, y, z)):
                continue
            tone = record_color(entry.path[r].kind) if record_color else rgb
            lines.line((x - TICK, y, z), (x + TICK, y, z), tone)
            lines.line((x, y, z - TICK), (x, y, z + TICK), tone)
        lines.ranges[entry.index] = (first, len(lines.surface) - first)
        for a, b in entry.walls():
            if inside(bounds, a):
                lines.line(a, b, wall_color or rgb, vertical=True)
    return lines


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
        GL.glLineWidth(LINE_WIDTH)
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

    def draw_surface(self, first, count):
        """Part of the surface layer again, as it stands - for a highlight."""
        vao, _vbo, _cbo, total = self._layers[0]
        if count <= 0 or first >= total:
            return
        vao.bind()
        GL.glDrawArrays(GL.GL_LINES, first, min(count, total - first))
        vao.release()

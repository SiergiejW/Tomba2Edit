"""Draws a DRWA two ways: the grid as it is stored, and the geometry it
points at, seen from above.

Nothing here touches Qt - it returns PIL images, and the caller displays
them. Both pictures take their colours from the same per-group table, so
a cell and the polygons it draws are always the same colour, which is
what makes the pair readable together: the grid says where a group sits
in the drawmap, the footprint says where it sits in the level.
"""
import colorsys

from PIL import Image, ImageDraw

# Same golden-ratio hue trick as gui.scld.scld_render.entry_color -
# consecutive groups land far apart on the wheel rather than fading into
# each other over a thousand of them.
GOLDEN_RATIO_CONJUGATE = 0.6180339887498949

EMPTY_COLOR = (0, 0, 0, 0)          # left to the canvas's own checker
FLAT_COLOR = (110, 170, 235)

# The footprint's longer side, in pixels, before zooming.
FOOTPRINT_SIZE = 900

# key -> (menu label, what the colour means)
COLOR_MODES = (
    ("order", "Pointer order",
     "Hue by the order the groups sit in the file, which is the order "
     "their cells are read in"),
    ("size", "Group size",
     "How many bytes of geometry the cell's group holds - blue is small, "
     "red is the largest on the level"),
    ("faces", "Face count",
     "Triangles and quads together, blue to red"),
    ("x", "World X",
     "Where the group actually is along X, blue to red"),
    ("y", "World height",
     "Where the group actually is along Y, blue (low) to red (high)"),
    ("z", "World Z",
     "Where the group actually is along Z, blue to red"),
    ("flat", "Occupied only",
     "One colour for every cell that points at geometry"),
)


def group_color(index, saturation=0.65, value=0.95):
    """This group's colour, stable across redraws."""
    r, g, b = colorsys.hsv_to_rgb((index * GOLDEN_RATIO_CONJUGATE) % 1.0,
                                  saturation, value)
    return (int(r * 255), int(g * 255), int(b * 255))


def ramp_color(t, saturation=0.85, value=0.95):
    """A blue-to-red ramp over t in [0, 1], for the modes that measure
    something rather than just tell groups apart."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    r, g, b = colorsys.hsv_to_rgb(0.66 * (1.0 - t), saturation, value)
    return (int(r * 255), int(g * 255), int(b * 255))


def _measured(groups, measure):
    """Ramp colours over whatever `measure` returns per group. Groups it
    can't measure come back grey."""
    values = {g.index: measure(g) for g in groups}
    known = [v for v in values.values() if v is not None]
    lo, hi = (min(known), max(known)) if known else (0, 0)
    span = hi - lo
    colors = {}
    for index, value in values.items():
        if value is None:
            colors[index] = (130, 130, 130)
        else:
            colors[index] = ramp_color((value - lo) / span if span else 0.5)
    return colors


def group_colors(drwa, mode="order"):
    """{group index: (r, g, b)} for one colour mode."""
    groups = drwa.groups
    if mode == "flat":
        return {g.index: FLAT_COLOR for g in groups}
    if mode == "size":
        return _measured(groups, lambda g: g.size)
    if mode == "faces":
        return _measured(groups, lambda g: g.tris + g.quads)
    if mode in ("x", "y", "z"):
        axis = "xyz".index(mode)
        return _measured(groups, lambda g: (g.centre or (None,) * 3)[axis])
    return {g.index: group_color(g.index) for g in groups}


def render_grid(drwa, colors):
    """The drawmap itself, one pixel per cell. Empty cells are left
    transparent, so the canvas's checker shows what the level never
    covers."""
    image = Image.new("RGBA", (drwa.width, drwa.height), EMPTY_COLOR)
    pixels = image.load()
    for group in drwa.groups:
        pixels[group.col, group.row] = colors[group.index] + (255,)
    return image


class Footprint:
    """The level from above, and the group under any pixel of it.

    `index_image` is the same picture drawn in group indices instead of
    colours - one lookup turns a click into a group, without hit-testing
    thousands of polygons.
    """

    def __init__(self, image, index_image, bounds, scale, margin):
        self.image = image
        self.index_image = index_image
        self.bounds = bounds            # (x0, x1, z0, z1) in world units
        self.scale = scale              # pixels per world unit
        self.margin = margin

    @property
    def size(self):
        return self.image.size

    def project(self, x, z):
        """A world point, in image pixels. X runs right and Z down, the
        way the drawmap's own columns and rows do."""
        x0, _x1, z0, _z1 = self.bounds
        return ((x - x0) * self.scale + self.margin,
                (z - z0) * self.scale + self.margin)

    def group_at(self, px, py):
        """Index of the group drawn at a pixel, or None."""
        if not (0 <= px < self.image.width and 0 <= py < self.image.height):
            return None
        value = self.index_image.getpixel((px, py))
        return value - 1 if value else None


def render_footprint(drwa, colors, target=FOOTPRINT_SIZE, margin=4):
    """Every group's polygons projected onto the XZ plane, each in its
    own colour. None if the level has no geometry to draw.

    This is the level as the drawmap covers it, so it can be read
    against the grid: a cell there is a patch here."""
    bounds = drwa.bounds
    if bounds is None:
        return None
    x0, x1, _y0, _y1, z0, z1 = bounds
    span = max(x1 - x0, z1 - z0)
    if span <= 0:
        return None

    scale = target / span
    width = max(int((x1 - x0) * scale) + margin * 2 + 1, 1)
    height = max(int((z1 - z0) * scale) + margin * 2 + 1, 1)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    index_image = Image.new("I", (width, height), 0)
    draw = ImageDraw.Draw(image)
    index_draw = ImageDraw.Draw(index_image)

    footprint = Footprint(image, index_image, (x0, x1, z0, z1), scale, margin)
    for group in drwa.groups:
        color = colors[group.index] + (255,)
        for face in group.faces:
            points = [footprint.project(v[0], v[2]) for v in face]
            # Outlined as well as filled: a wall seen from directly above
            # is a line, and a fill alone would leave it invisible.
            draw.polygon(points, fill=color, outline=color)
            index_draw.polygon(points, fill=group.index + 1,
                               outline=group.index + 1)
    return footprint

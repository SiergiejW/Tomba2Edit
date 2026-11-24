"""Vector-looking, pixelated map symbols: warp door, timed hourglass, signpost "!".

Drawn the way Windows 2003 drew its icons - a dark outline, a glossy vertical gradient, a one pixel
highlight down the lit edge - on a 16 x 16 grid and then scaled with nearest-neighbour, so they stay
crisp and blocky at any size. The warp door, the timed hourglass, the signpost "!" and the save-point floppy share one red.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPixmap

GRID = 16
RED_TOP, RED_BOTTOM, RED_LINE = (255, 120, 112), (196, 24, 24), (84, 0, 0)       # warp and timed
GOLD_TOP, GOLD_BOTTOM, GOLD_LINE = (255, 232, 96), (232, 150, 0), (92, 52, 0)    # signpost

_HOURGLASS = (
    "................",
    "..############..",
    "..############..",
    "...##########...",
    "....########....",
    ".....######.....",
    "......####......",
    ".......##.......",
    ".......##.......",
    "......####......",
    ".....######.....",
    "....########....",
    "...##########...",
    "..############..",
    "..############..",
    "................",
)
_DOOR = (
    "................",
    "....########....",
    "...##########...",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "..############..",
    "................",
)
_BANG = (
    "................",
    "......####......",
    ".....######.....",
    ".....######.....",
    ".....######.....",
    ".....######.....",
    ".....######.....",
    "......####......",
    "......####......",
    "......####......",
    ".......##.......",
    "................",
    "......####......",
    ".....######.....",
    ".....######.....",
    "......####......",
)
# Details painted over the gradient: (column, row) -> colour. The door's knob and the hourglass sand.
_SPEAKER = (
    "................",
    "................",
    "......#.........",
    ".....##....#....",
    "....###.....#...",
    "###.####.#...#..",
    "###.####..#..#..",
    "###.####..#..#..",
    "###.####..#..#..",
    "###.####.#...#..",
    "....###.....#...",
    ".....##....#....",
    "......#.........",
    "................",
    "................",
    "................",
)
_FLOPPY = (
    "................",
    ".##############.",
    ".#............#.",
    ".#.##########.#.",
    ".#.##########.#.",
    ".#.##########.#.",
    ".#............#.",
    ".#............#.",
    ".#.##########.#.",
    ".#.#........#.#.",
    ".#.#........#.#.",
    ".#.#........#.#.",
    ".##############.",
    "................",
    "................",
    "................",
)
BLUE_TOP, BLUE_BOTTOM, BLUE_LINE = (150, 190, 255), (30, 80, 200), (0, 20, 90)
_KNOB = {(10, 8): (255, 236, 150), (10, 9): (232, 170, 40)}
_SAND = {(7, 10): (255, 232, 150), (8, 10): (255, 232, 150), (6, 11): (255, 214, 110), (7, 11): (255, 214, 110),
         (8, 11): (255, 214, 110), (9, 11): (255, 214, 110), (7, 4): (255, 232, 150), (8, 4): (255, 232, 150)}

_cache = {}


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _paint(mask, top, bottom, line, details=None, dark=None):
    """A GRID x GRID image: outline, gradient fill, highlight along the left and top edges."""
    cells = {(x, y) for y, row in enumerate(mask) for x, ch in enumerate(row) if ch == '#'}
    rows = [y for _x, y in cells]
    first, last = min(rows), max(rows)
    image = QImage(GRID, GRID, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    def put(x, y, rgb):
        if 0 <= x < GRID and 0 <= y < GRID:
            image.setPixelColor(x, y, QColor(*rgb))

    for x, y in cells:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            if (x + dx, y + dy) not in cells:
                put(x + dx, y + dy, line)
    for x, y in cells:
        shade = _mix(top, bottom, (y - first) / max(1, last - first))
        if (x - 1, y) not in cells or (x, y - 1) not in cells:
            shade = _mix(shade, (255, 255, 255), 0.55)        # the lit edge
        elif (x + 1, y) not in cells or (x, y + 1) not in cells:
            shade = _mix(shade, line, 0.35)                   # the shaded edge
        put(x, y, shade)
    for (x, y), rgb in (details or {}).items():
        put(x, y, rgb)
    return image


def _icon(kind):
    if kind == 'warp':
        return _paint(_DOOR, RED_TOP, RED_BOTTOM, RED_LINE, _KNOB)
    if kind == 'timed':
        return _paint(_HOURGLASS, RED_TOP, RED_BOTTOM, RED_LINE, _SAND)
    if kind == 'save':
        return _paint(_FLOPPY, RED_TOP, RED_BOTTOM, RED_LINE)
    if kind == 'speaker':
        return _paint(_SPEAKER, BLUE_TOP, BLUE_BOTTOM, BLUE_LINE)
    return _paint(_BANG, RED_TOP, RED_BOTTOM, RED_LINE)


def pixmap(kind, width, height=None, bright=False):
    """The symbol `kind` ('warp' | 'timed' | 'sign' | 'save' | 'speaker') at width x height, hard-edged. `bright` lightens it."""
    height = height or width
    key = (kind, width, height, bright)
    if key not in _cache:
        image = _icon(kind)
        if bright:
            for y in range(GRID):
                for x in range(GRID):
                    c = image.pixelColor(x, y)
                    if c.alpha():
                        image.setPixelColor(x, y, QColor(*_mix((c.red(), c.green(), c.blue()), (255, 255, 255), 0.3), c.alpha()))
        _cache[key] = QPixmap.fromImage(image.scaled(max(1, width), max(1, height), Qt.AspectRatioMode.IgnoreAspectRatio,
                                                     Qt.TransformationMode.FastTransformation))
    return _cache[key]

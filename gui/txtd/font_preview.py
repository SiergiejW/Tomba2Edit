"""Draws text the way the game draws it, from the disc's own font page.

The page holds two fonts on the same grid (see functions/fontpage.py):
8x16 glyphs for dialogue and 8x8 for the smaller notices. Both index the
same table, so a byte picks the same character in either - only the cell
size differs.

    dialogue (TXTD), TXT1, BIN   big
    TXT2, MAIN.EXE               small

THE JAPANESE DISC DRAWS ITS BIG TEXT FROM SOMEWHERE ELSE

Its page has no kanji on it at all, and its dialogue is full of them:
the glyphs come out of the console's own 16x15 font instead (see
functions/biosfont). Only the big text does. Its TXT2 is still the
page's 8x8 font, character for character, which is why that page
carries a kana system font nothing else on the disc uses - so the two
sizes are not the same alphabet at two sizes there, they are two
alphabets. MAIN.EXE's pool is small text but console-drawn on every
build, and console-drawn is Shift-JIS here, so it asks for the big
sheet by name rather than by size.

Glyph pixels are 4-bit CLUT indices and the page carries the palettes,
so the colours here are the disc's own rather than an approximation. A
colour control selects one: the control byte is the VRAM row its palette
sits on, {$WHITE} being 0xF0 at row 240 and the rest following to
{$GREEN} at 244, all in the row's fourth slot. Glyphs are drawn almost
entirely in index 1, the fill, and index 6, the outline.

A character's cell is found from the character, not from the byte it
encodes to. Most bytes are the cell number, but not all: a space encodes
to 0xFB, which the game acts on rather than draws, and indexing the grid
with it would land on a symbol several rows down.
"""
import os

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from functions import biosfont, fontpage
from gui.txtd import translation

# Which palette each colour control selects. The value is the VRAM row,
# which is the control's own byte.
COLORS = {
    "{$WHITE}": 0xF0,
    "{$ORANGE}": 0xF1,
    "{$BLUE}": 0xF2,
    "{$PINK}": 0xF3,
    "{$GREEN}": 0xF4,
}
CLUT_SLOT = 3
DEFAULT_COLOR = 0xF0

# The button icons, and the cells each is drawn from. They are 16 wide
# against the grid's 8, so each takes two cells side by side.
#
# They need no palette of their own. Indices 7 to 15 hold the button
# colours and are identical in all five text palettes - only 1 to 6
# change with the colour control - so each icon keeps its own colour
# whatever colour the text around it is:
#
#     circle    7, 8     red
#     cross     9, 10    blue
#     triangle  11, 12   green
#     square    13       pink
#
# The control byte is not the cell: {$CIRCLE} encodes to 0xCD, the same
# way a space encodes to 0xFB, and the game draws the icon from here.
ICONS = {
    "{$CIRCLE}": (160, 161),
    "{$CROSS}": (162, 163),
    "{$TRIANGLE}": (164, 165),
    "{$SQUARE}": (166, 167),
}

# The prompt the game puts at the end of a line of dialogue, waiting for
# the player.
MARKER_CELLS = ICONS["{$CIRCLE}"]

# Behind the preview, so text is judged against something like the scene
# it will sit on rather than a flat panel.
BACKGROUND = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "icons", "tomba",
    "txtd_background.jpg")

# The dialogue box is built from the page's own frame pieces (see
# fontpage.read_frame), as a nine-slice. Each piece is 18 wide with a
# 3-pixel border either side.
#
# The art is stored upside down, so every piece is read bottom-up and
# piece 2 is the top edge, piece 0 the bottom:
#
#     piece 2   rows 4..0    the top
#     piece 1   rows 7..0    the middle, stretched down the box
#     piece 0   rows 7..3    the bottom
#
# Two things agree on that and neither does on any other arrangement.
# The corners are inset on the outermost row at both ends, and the
# interior greys come out monotonic - 57 at the top falling to 16 at the
# bottom - with no step at either seam. Read the other way up the box is
# darkest at the top and the seams jump.
FRAME_MARGIN = 10
FRAME_SCALE = 2
FRAME_BORDER = 3          # left and right border, in source pixels
FRAME_EDGE_H = 5          # rows in the top and the bottom edge
FRAME_TOP_Y = 4           # topmost row of the top edge, inside piece 2
FRAME_BOT_Y = 7           # topmost row of the bottom edge, inside piece 0
FRAME_MID_H = 8           # rows of piece 1, stretched down the box
FRAME_FILL = QColor(0, 0, 0, 224)
TEXT_INSET = 14

# The pieces are the same art in every box the game draws; the palette is
# what changes with the context, so a style is just which palette to
# take. How much of the scene shows through is not a setting: the
# interior multiplies (see _nine_slice_split), so its own greys decide.
FRAME_STYLES = {
    "dialogue": {"clut": (255, 2)},
    "notice": {"clut": (255, 3)},
}
DEFAULT_STYLE = "dialogue"

# The box is drawn only as big as the text in it, so it grows and
# shrinks with what is being edited.
MIN_BOX_W = 8 * FRAME_SCALE
MIN_BOX_H = 12 * FRAME_SCALE

BIG_H = fontpage.GLYPH_H
SMALL_H = fontpage.SYSTEM_H
CELL_W = fontpage.GLYPH_W

# The console font's glyphs are 15 rows in a 16-row cell - the four of
# them on the sign reading 漁師の村 sit one row apart from the top of
# their box, and a whole cell is what the game steps by.
JP_LINE_H = 16


class FontSheet:
    """One disc's glyphs, both sizes, cut once and kept."""

    def __init__(self, cd_folder, glyph_top=None, style=DEFAULT_STYLE):
        self.page = fontpage.read_page(cd_folder)
        if glyph_top is None:
            glyph_top = fontpage.GLYPH_TOP
        self.glyph_top = glyph_top
        self.palettes = {}
        for row, slot, pal in fontpage.read_cluts(cd_folder):
            if slot == CLUT_SLOT:
                self.palettes[row] = pal
        chosen = FRAME_STYLES.get(style)
        # The Japanese page's box is one upright 24x24 nine-slice in the
        # system font's grid, not three upside-down pieces - see
        # fontpage.read_jp_frame.
        from gui.txtd import dicts
        self.frame_upright = dicts.japanese_disc()
        if chosen is None:
            self.frame = []
        elif self.frame_upright:
            self.frame = fontpage.read_jp_frame(cd_folder, chosen["clut"])
        else:
            self.frame = fontpage.read_frame(cd_folder, chosen["clut"])

    def palette(self, row):
        """The palette a colour control selects, falling back to a plain
        white one where the disc has none at that row."""
        pal = self.palettes.get(row)
        if pal:
            return pal
        return [(0, 0, 0, 0)] + [(255, 255, 255, 255)] * 15

    def cell(self, code, big):
        """One glyph as rows of 4-bit indices, or None if off the grid."""
        height = BIG_H if big else SMALL_H
        top = self.glyph_top if big else fontpage.SYSTEM_TOP
        row, col = divmod(code, fontpage.GLYPH_COLS)
        y = top + row * height
        x = col * CELL_W
        if y + height > fontpage.PAGE_H or x + CELL_W > fontpage.PAGE_W:
            return None
        return [self.page[y + i][x:x + CELL_W] for i in range(height)]

    def render(self, runs, big=True, scale=2, marker=False):
        """`runs` as [(codes, (r, g, b))] -> a QImage of the text.

        Codes that break the line start a new one; codes with no glyph
        leave a gap, so a line's spacing still reads correctly. Drawn at
        one pixel per texel and scaled up whole, which keeps the pixels
        square and hard-edged.

        Lines with nothing drawn on them at the end are dropped. An
        entry ends in the terminator on its own line, and the breaks
        that separate it from the text are not part of the message, so
        counting them would make the box taller than the game's.

        With `marker`, the prompt icon is placed at the bottom right,
        padded out from the last line so it sits in the corner."""
        height = BIG_H if big else SMALL_H
        lines = [[]]
        for codes, color in runs:
            for code in codes:
                if code is None and codes == [None]:
                    lines.append([])
                else:
                    lines[-1].append((code, color))
        while len(lines) > 1 and all(code is None for code, _c in lines[-1]):
            lines.pop()

        if marker and big:
            color = lines[-1][-1][1] if lines[-1] else DEFAULT_COLOR
            widest = max(len(line) for line in lines)
            end = max(widest, len(lines[-1]) + len(MARKER_CELLS))
            lines[-1] += [(None, color)] * (end - len(MARKER_CELLS)
                                            - len(lines[-1]))
            lines[-1] += [(code, color) for code in MARKER_CELLS]

        cols = max((len(line) for line in lines), default=0)
        width = max(cols * CELL_W, 1)
        rows = max(len(lines) * height, 1)

        buffer = bytearray(width * rows * 4)
        for ly, line in enumerate(lines):
            for lx, (code, color) in enumerate(line):
                cell = None if code is None else self.cell(code, big)
                if cell is None:
                    continue        # a space still takes its column
                palette = self.palette(color)
                for yy in range(height):
                    row = cell[yy]
                    base = ((ly * height + yy) * width + lx * CELL_W) * 4
                    for xx in range(CELL_W):
                        index = row[xx]
                        if not index:
                            continue
                        red, green, blue, alpha = palette[index]
                        if not alpha:
                            continue
                        at = base + xx * 4
                        # QImage's ARGB32 is BGRA in memory on little-endian.
                        buffer[at] = blue
                        buffer[at + 1] = green
                        buffer[at + 2] = red
                        buffer[at + 3] = 255
        image = QImage(bytes(buffer), width, rows, width * 4,
                       QImage.Format.Format_ARGB32).copy()
        if scale != 1:
            image = image.scaled(width * scale, rows * scale,
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
        return image


class BiosSheet(FontSheet):
    """The Japanese disc's big text, drawn from the console's font.

    Everything but the glyphs is the page's: the palettes a colour
    control selects and the frame around the box both still come off
    the disc, because the game draws these glyphs with the same text
    palettes it draws its own with. A glyph is one bit deep, and the
    game expands it into palette index 1 - the fill - so that is the
    colour the ink takes here.
    """

    def glyph(self, char):
        """One character as 15 sixteen-bit row masks, or None."""
        try:
            raw = char.encode("shift_jis")
        except UnicodeEncodeError:
            return None
        if len(raw) != 2:
            return None
        try:
            return biosfont.rows((raw[0] << 8) | raw[1])
        except biosfont.BiosFontError:
            return None

    def render(self, runs, big=True, scale=2, marker=False):
        """`runs` as [(characters, (r, g, b))] -> a QImage.

        Same shape as FontSheet.render and the same line handling; only
        the cell size and where a glyph comes from differ. The prompt
        icon is not drawn: it is page artwork, and which cells this
        build draws it from has not been established."""
        lines = [[]]
        for chars, color in runs:
            for char in chars:
                if char is None and chars == [None]:
                    lines.append([])
                else:
                    lines[-1].append((char, color))
        while len(lines) > 1 and all(char is None for char, _c in lines[-1]):
            lines.pop()

        cols = max((len(line) for line in lines), default=0)
        width = max(cols * biosfont.GLYPH_W, 1)
        rows = max(len(lines) * JP_LINE_H, 1)

        buffer = bytearray(width * rows * 4)
        for ly, line in enumerate(lines):
            for lx, (char, color) in enumerate(line):
                shape = None if char is None else self.glyph(char)
                if shape is None:
                    continue        # a space still takes its column
                red, green, blue, alpha = self.palette(color)[1]
                if not alpha:
                    continue
                for yy in range(biosfont.GLYPH_H):
                    mask = shape[yy]
                    if not mask:
                        continue
                    base = ((ly * JP_LINE_H + yy) * width
                            + lx * biosfont.GLYPH_W) * 4
                    for xx in range(biosfont.GLYPH_W):
                        if not (mask & (0x8000 >> xx)):
                            continue
                        at = base + xx * 4
                        # QImage's ARGB32 is BGRA in memory on little-endian.
                        buffer[at] = blue
                        buffer[at + 1] = green
                        buffer[at + 2] = red
                        buffer[at + 3] = 255
        image = QImage(bytes(buffer), width, rows, width * 4,
                       QImage.Format.Format_ARGB32).copy()
        if scale != 1:
            image = image.scaled(width * scale, rows * scale,
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
        return image


def cell_for(char):
    """The grid cell a character is drawn from, or None if it isn't
    drawn - a space, or anything with no glyph.

    The table in force decides, so a translation's own letters draw from
    whichever cells it claimed. Codes the game acts on rather than draws
    are refused, since there is no glyph behind them.

    Falling back to `ord - 32` covers a character the table has no entry
    for: the disc's own alphabet sits at exactly those cells, so an
    untranslated build behaves as before."""
    if char == " ":
        return None
    from gui.txtd import dicts

    if dicts.japanese_disc():
        # The Japanese page's own grid, which shares nothing with the
        # Latin one - kana where the Latin build has ASCII, and its
        # ASCII 0x6F further along. See gui/txtd/jptext.cells().
        from gui.txtd import jptext
        return jptext._cells_reverse().get(char)
    code = translation.active().cells().get(char)
    if code is not None:
        return code
    point = ord(char)
    if 0x21 <= point <= 0x7E:
        return point - 32
    return None


def glyph_key(char):
    """What the big Japanese sheet draws a character with: the character
    itself, or None where nothing is drawn."""
    return None if char == " " else char


def split_runs(text, icons=True, mapper=cell_for):
    """Editor text as [(cells, colour)], with line breaks as None.

    Colour controls switch the tint and the button controls draw their
    icon; other {$...} tokens are skipped, since they tell the game to
    do something rather than to draw.

    `icons` is off for the small font, whose grid has nothing at those
    cells - the icons are drawn at the dialogue font's size only.

    `mapper` turns one character into whatever the sheet draws it with -
    a grid cell by default, and the character itself for the console
    font, which is indexed by character rather than by cell."""
    runs = []
    color = DEFAULT_COLOR
    cells = []

    def flush():
        nonlocal cells
        if cells:
            runs.append((cells, color))
            cells = []

    i = 0
    while i < len(text):
        for token, row in COLORS.items():
            if text.startswith(token, i):
                flush()
                color = row
                i += len(token)
                break
        else:
            if text[i] == "\n":
                flush()
                runs.append(([None], color))
                i += 1
            elif text.startswith("{$", i) and "}" in text[i:i + 12]:
                end = text.index("}", i) + 1
                icon = ICONS.get(text[i:end]) if icons else None
                if icon:
                    cells.extend(icon)
                i = end                          # a control, not a glyph
            else:
                cells.append(mapper(text[i]))
                i += 1
    flush()
    return runs


def _upright_slice(image, width, height, inner_alpha=128, keep_alpha=False):
    """The Japanese box at any size, from its own 24x24 nine-slice.

    Same idea as _nine_slice below and the same two rules - repeat the
    middle columns, stretch the middle rows rather than tile them, since
    the interior runs a gradient meant to cross the whole box. What it
    does not do is read the art bottom-up: this one is stored the right
    way up."""
    side = fontpage.JP_FRAME_SIDE
    if not image or len(image) < side or len(image[0]) < side:
        return None
    b = fontpage.JP_FRAME_BORDER
    e = fontpage.JP_FRAME_EDGE_H
    if width < 2 * b or height < 2 * e:
        return None
    inner_w = side - 2 * b
    mid_h = side - 2 * e
    inner_h = max(height - 2 * e, 1)

    def column(x):
        if x < b:
            return x
        if x >= width - b:
            return side - (width - x)
        return b + ((x - b) % inner_w)

    def source_row(y):
        if y < e:
            return y
        if y >= height - e:
            return side - (height - y)
        return e + min((y - e) * mid_h // inner_h, mid_h - 1)

    buffer = bytearray(width * height * 4)
    for y in range(height):
        line = image[source_row(y)]
        for x in range(width):
            r, g, bl, a = line[column(x)]
            if a not in (0, 255) and not keep_alpha:
                a = inner_alpha
            at = (y * width + x) * 4
            buffer[at] = bl
            buffer[at + 1] = g
            buffer[at + 2] = r
            buffer[at + 3] = a
    return QImage(bytes(buffer), width, height, width * 4,
                  QImage.Format.Format_ARGB32).copy()


def _nine_slice_split(pieces, width, height, upright=False):
    """The box as two layers: its opaque border, and its interior.

    They are drawn differently. The border is ordinary opaque pixels.
    The interior is marked semi-transparent in the palette and darkens
    what is behind it rather than averaging with it - a dark grey over
    the scene goes almost black while the scene's texture still shows
    through, which is what the game does and what plain alpha blending
    cannot reproduce. Multiplying by the interior's own greys gives
    that, and keeps its top-to-bottom gradient."""
    both = _nine_slice(pieces, width, height, keep_alpha=True,
                       upright=upright)
    if both is None:
        return None, None
    border = QImage(both.size(), QImage.Format.Format_ARGB32)
    border.fill(0)
    interior = QImage(both.size(), QImage.Format.Format_ARGB32)
    interior.fill(QColor(255, 255, 255).rgb())      # white leaves multiply alone
    for y in range(both.height()):
        for x in range(both.width()):
            colour = both.pixelColor(x, y)
            if colour.alpha() == 255:
                border.setPixelColor(x, y, colour)
            elif colour.alpha():
                interior.setPixelColor(
                    x, y, QColor(colour.red(), colour.green(), colour.blue()))
    return border, interior


def _nine_slice(pieces, width, height, inner_alpha=128,
                keep_alpha=False, upright=False):
    """The dialogue box at any size, from the disc's own pieces.

    The corners are taken as they are and the top and bottom edges repeat
    their middle columns, so the border keeps its own pixels at any size.
    The middle piece is stretched down the box rather than tiled: its
    side columns are the same on every row, but its interior runs a
    gradient (16 to 49) meant to cross the whole box, and tiling it
    leaves bands."""
    if upright:
        return _upright_slice(pieces, width, height, inner_alpha, keep_alpha)
    if not pieces or len(pieces) < 3 or width < 8 or height < 12:
        return None
    top, mid, bottom = pieces[2], pieces[1], pieces[0]
    b = FRAME_BORDER
    src_w = len(top[0])
    inner_w = src_w - 2 * b

    def column(x):
        """Source column for a destination column, repeating the middle."""
        if x < b:
            return x
        if x >= width - b:
            return src_w - (width - x)
        return b + ((x - b) % inner_w)

    inner_h = max(height - 2 * FRAME_EDGE_H, 1)

    def row_of(y):
        """(piece, source row) for a destination row. Every piece is
        read bottom-up, since the art is stored upside down."""
        if y < FRAME_EDGE_H:
            return top, FRAME_TOP_Y - y
        if y >= height - FRAME_EDGE_H:
            return bottom, FRAME_BOT_Y - (y - (height - FRAME_EDGE_H))
        sy = (y - FRAME_EDGE_H) * FRAME_MID_H // inner_h
        return mid, FRAME_MID_H - 1 - min(sy, FRAME_MID_H - 1)

    buffer = bytearray(width * height * 4)
    for y in range(height):
        piece, sy = row_of(y)
        line = piece[sy]
        for x in range(width):
            r, g, bl, a = line[column(x)]
            if a not in (0, 255) and not keep_alpha:
                a = inner_alpha        # the interior, however solid it reads
            at = (y * width + x) * 4
            buffer[at] = bl
            buffer[at + 1] = g
            buffer[at + 2] = r
            buffer[at + 3] = a
    return QImage(bytes(buffer), width, height, width * 4,
                  QImage.Format.Format_ARGB32).copy()


class _Canvas(QWidget):
    """Paints the scene behind, the dialogue frame, and the text in it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._message = "No disc open - preview unavailable."
        self._background = QPixmap(BACKGROUND) if os.path.exists(BACKGROUND)             else QPixmap()
        self._pieces = None
        self._upright = False
        self._boxed = True

    def set_frame(self, pieces, upright=False):
        """Take the frame from the disc, or None to draw a plain box.

        `upright` is the Japanese page's one-image nine-slice rather
        than the Latin page's three upside-down pieces."""
        want = fontpage.JP_FRAME_SIDE if upright else 3
        self._pieces = pieces if pieces and len(pieces) >= want else None
        self._upright = upright
        self.update()

    def set_boxed(self, boxed):
        """Whether to draw a box at all. Text the game puts straight on
        the scene, with no border around it, is drawn the same way."""
        self._boxed = boxed
        self.update()

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self._message = ""
        self._resize_to_fit()
        self.update()

    def set_message(self, text):
        self._pixmap = None
        self._message = text
        self.update()

    def _inset(self):
        """The gap between the border and the text. Without a border
        there is nothing to clear, so the text sits on the scene."""
        return TEXT_INSET if self._boxed else 0

    def _resize_to_fit(self):
        """Ask for room for the whole box, so text longer than the pane
        scrolls rather than being cropped."""
        if self._pixmap is None:
            self.setMinimumSize(0, 0)
            return
        edge = 2 * (FRAME_MARGIN + self._inset())
        self.setMinimumSize(self._pixmap.width() + edge,
                            self._pixmap.height() + edge)

    def _box(self):
        """The dialogue box, only as big as the text it holds.

        It grows with the text and sits centred along the bottom of the
        scene, where the game puts it. The lines inside stay left
        aligned; it is the box that is centred, not the text in it."""
        room = self.rect().adjusted(FRAME_MARGIN, FRAME_MARGIN,
                                    -FRAME_MARGIN, -FRAME_MARGIN)
        if self._pixmap is None:
            return room
        inset = self._inset()
        width = max(min(self._pixmap.width() + 2 * inset, room.width()),
                    MIN_BOX_W)
        height = max(min(self._pixmap.height() + 2 * inset, room.height()),
                     MIN_BOX_H)
        return QRect(room.left() + (room.width() - width) // 2,
                     room.bottom() - height + 1, width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self._background.isNull():
            scaled = self._background.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(0, 0, scaled)
        else:
            painter.fillRect(self.rect(), QColor(16, 16, 16))

        box = self._box()
        if box.width() <= 0 or box.height() <= 0:
            painter.end()
            return

        # Composed at its own scale and blown up whole, so the border
        # stays hard-edged however big the pane is.
        if self._boxed:
            least_w = 2 * fontpage.JP_FRAME_BORDER if self._upright else 8
            least_h = 2 * fontpage.JP_FRAME_EDGE_H if self._upright else 12
            border, interior = _nine_slice_split(
                self._pieces,
                max(box.width() // FRAME_SCALE, least_w),
                max(box.height() // FRAME_SCALE, least_h),
                upright=self._upright)
            if border is None:
                painter.fillRect(box, FRAME_FILL)
                painter.setPen(QPen(QColor(255, 255, 255), 2))
                painter.drawRect(box.adjusted(0, 0, -1, -1))
            else:
                target = QRect(box.left(), box.top(),
                               border.width() * FRAME_SCALE,
                               border.height() * FRAME_SCALE)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Multiply)
                painter.drawImage(target, interior)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.drawImage(target, border)

        inset = self._inset()
        if self._pixmap is not None:
            painter.drawPixmap(box.left() + inset,
                               box.top() + inset, self._pixmap)
        elif self._message:
            painter.setPen(QPen(QColor(190, 190, 190)))
            painter.drawText(box.adjusted(inset or TEXT_INSET,
                                          inset or TEXT_INSET, 0, 0),
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignTop, self._message)
        painter.end()


class FontPreview(QWidget):
    """Shows the selected entry drawn in the disc's own font, in a frame
    like the one the game puts around dialogue."""

    def __init__(self, big=True, style=DEFAULT_STYLE, marker=False,
                 console_font=False, parent=None):
        super().__init__(parent)
        self.big = big
        self.style = style
        self.marker = marker
        # Text the console draws rather than the game - MAIN.EXE's pool.
        # It only changes anything on the Japanese disc, where what the
        # console draws with is a font of its own.
        self.console_font = console_font
        self.sheet = None
        self._canvas = _Canvas()
        self._canvas.set_boxed(style in FRAME_STYLES)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("background: #101010; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

    def _uses_console_font(self):
        """Whether this preview draws from the console's font rather
        than the page - the Japanese disc's big text, and its MAIN.EXE
        pool whatever size that is drawn at."""
        from gui.txtd import dicts
        return dicts.japanese_disc() and (self.big or self.console_font)

    def set_source(self, cd_folder, glyph_top=None):
        """Point the preview at a disc, or None to blank it."""
        if not cd_folder:
            self.sheet = None
            self._canvas.set_message("No disc open - preview unavailable.")
            return
        sheet_class = BiosSheet if self._uses_console_font() else FontSheet
        try:
            self.sheet = sheet_class(cd_folder, glyph_top, self.style)
            self._canvas.set_frame(self.sheet.frame, self.sheet.frame_upright)
            self._canvas.set_message("")
        except Exception as exc:
            self.sheet = None
            self._canvas.set_message(f"Font page unreadable: {exc}")

    def set_text(self, text):
        if self.sheet is None:
            return
        if not text:
            self._canvas.set_message("")
            return
        if isinstance(self.sheet, BiosSheet):
            runs = split_runs(text, icons=False, mapper=glyph_key)
            image = self.sheet.render(runs)
        else:
            image = self.sheet.render(split_runs(text, self.big), self.big,
                                      marker=self.marker)
        self._canvas.set_pixmap(QPixmap.fromImage(image))

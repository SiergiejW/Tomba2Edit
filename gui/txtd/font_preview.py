"""Draws text the way the game draws it, from the disc's own font page.

The page holds two fonts on the same grid (see functions/fontpage.py):
8x16 glyphs for dialogue and 8x8 for the smaller notices. Both index the
same table, so a byte picks the same character in either - only the cell
size differs.

    dialogue (TXTD), TXT1, BIN   big
    TXT2, MAIN.EXE               small

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
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from functions import fontpage

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

# Controls that move the cursor rather than draw.
BREAKS = ("\n", "{$END}\n\n")

BIG_H = fontpage.GLYPH_H
SMALL_H = fontpage.SYSTEM_H
CELL_W = fontpage.GLYPH_W


class FontSheet:
    """One disc's glyphs, both sizes, cut once and kept."""

    def __init__(self, cd_folder, glyph_top=None):
        self.page = fontpage.read_page(cd_folder)
        if glyph_top is None:
            glyph_top = fontpage.GLYPH_TOP
        self.glyph_top = glyph_top
        self.palettes = {}
        for row, slot, pal in fontpage.read_cluts(cd_folder):
            if slot == CLUT_SLOT:
                self.palettes[row] = pal

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

    def render(self, runs, big=True, scale=2):
        """`runs` as [(codes, (r, g, b))] -> a QImage of the text.

        Codes that break the line start a new one; codes with no glyph
        leave a gap, so a line's spacing still reads correctly. Drawn at
        one pixel per texel and scaled up whole, which keeps the pixels
        square and hard-edged."""
        height = BIG_H if big else SMALL_H
        lines = [[]]
        for codes, color in runs:
            for code in codes:
                if code is None and codes == [None]:
                    lines.append([])
                else:
                    lines[-1].append((code, color))
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


def cell_for(char):
    """The grid cell a character is drawn from, or None if it isn't
    drawn - a space, or anything with no glyph.

    Codes run from '!', so printable ASCII is `ord - 32`. The accented
    letters sit in their own two rows (see gui/txtd/dicts.py)."""
    point = ord(char)
    if char == " ":
        return None
    if 0x21 <= point <= 0x7E:
        return point - 32
    if 0xC0 <= point <= 0xDF:
        return 160 + point - 0xC0
    if 0xE0 <= point <= 0xFF:
        return 192 + point - 0xE0
    return None


def split_runs(text):
    """Editor text as [(cells, colour)], with line breaks as None.

    Colour controls switch the tint; other {$...} tokens are skipped,
    since they tell the game to do something rather than to draw."""
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
                i = text.index("}", i) + 1        # a control, not a glyph
            else:
                cells.append(cell_for(text[i]))
                i += 1
    flush()
    return runs


class FontPreview(QWidget):
    """Shows the selected entry drawn in the disc's own font."""

    def __init__(self, big=True, parent=None):
        super().__init__(parent)
        self.big = big
        self.sheet = None
        self._label = QLabel("No disc open - preview unavailable.")
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignTop)
        self._label.setStyleSheet("background: #101010; padding: 6px;")
        # Rendered text is as wide as the line, which can run past the
        # pane, so it scrolls rather than being squeezed.
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._label)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("background: #101010; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

    def set_source(self, cd_folder, glyph_top=None):
        """Point the preview at a disc, or None to blank it."""
        if not cd_folder:
            self.sheet = None
            self._label.setText("No disc open - preview unavailable.")
            return
        try:
            self.sheet = FontSheet(cd_folder, glyph_top)
        except Exception as exc:
            self.sheet = None
            self._label.setText(f"Font page unreadable: {exc}")

    def set_text(self, text):
        if self.sheet is None:
            return
        if not text:
            self._label.setPixmap(QPixmap())
            self._label.setText("")
            return
        image = self.sheet.render(split_runs(text), self.big)
        self._label.setPixmap(QPixmap.fromImage(image))

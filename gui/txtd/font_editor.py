"""Editing the font page and the character table together.

A translation needs both halves to agree: a shape drawn in a grid cell,
and a table saying what character that cell's code means. This window
puts them side by side - pick a cell, draw in it, name it - and writes
the shape to the disc's IMG and the name to the table beside it (see
gui/txtd/translation.py).

Which cells are safe to claim is the question a translator actually has,
and the disc answers it. Every code is marked by what claiming it would
cost:

    taken   the disc's own text uses this code
    art     a shape is drawn here but no text asks for it, so the shape
            is unreachable and can be drawn over
    blank   nothing drawn and nothing named

"art" is where the room is. The US page spends over a hundred cells on
symbols, kana and arrows that no string on the disc ever prints, which
is far more than any Latin alphabet needs.
"""
import os

from PyQt6.QtCore import QRect, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QScrollArea,
                             QSplitter, QVBoxLayout, QWidget)

from functions import fontpage
from gui.txtd import translation

# How the three states are marked in the grid.
STATE_COLORS = {
    "taken": QColor(90, 90, 90),
    "art": QColor(190, 130, 40),
    "blank": QColor(60, 150, 80),
}
STATE_TEXT = {
    "taken": "used by the disc's own text",
    "art": "art nothing prints - free to draw over",
    "blank": "empty and unnamed",
}

GRID_CELL = 20            # a cell's size in the picker, in screen pixels
GRID_COLS = 32
EDIT_ZOOM = 22            # a pixel's size in the editor

# The palette a glyph is drawn with. Index 0 is the transparent ground;
# glyphs use 1 as the fill and 6 as the outline, and the icon colours
# live from 7 up (see gui/txtd/font_preview.py).
PEN_INDICES = (0, 1, 2, 3, 4, 5, 6)


class _Measure(QThread):
    """Measuring which codes the disc's text uses, off the GUI thread.

    The scan decodes every text file on the disc and takes over a
    minute, so it cannot run where it would freeze the window."""

    done = pyqtSignal(object)

    def __init__(self, dat_path, parent=None):
        super().__init__(parent)
        self.dat_path = dat_path

    def run(self):
        from functions import codeuse
        try:
            self.done.emit(codeuse.used_codes(self.dat_path))
        except Exception:
            self.done.emit(None)


class _GlyphGrid(QWidget):
    """Every cell of the page, marked by what claiming it would cost."""

    picked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = None
        self.top = fontpage.GLYPH_TOP
        self.states = {}
        self.selected = None
        self._rows = 8
        self.setMinimumSize(GRID_COLS * GRID_CELL + 1, 8 * GRID_CELL + 1)

    def set_page(self, page, top, states):
        self.page = page
        self.top = top
        self.states = states
        self._rows = max(1, (max(states) + 1 + GRID_COLS - 1) // GRID_COLS)
        self.setMinimumSize(GRID_COLS * GRID_CELL + 1,
                            self._rows * GRID_CELL + 1)
        self.update()

    def sizeHint(self):
        return QSize(GRID_COLS * GRID_CELL + 1, self._rows * GRID_CELL + 1)

    def mousePressEvent(self, event):
        col = int(event.position().x()) // GRID_CELL
        row = int(event.position().y()) // GRID_CELL
        code = row * GRID_COLS + col
        if 0 <= col < GRID_COLS and code in self.states:
            self.selected = code
            self.picked.emit(code)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 24))
        if self.page is None:
            painter.end()
            return
        for code, state in self.states.items():
            row, col = divmod(code, GRID_COLS)
            box = QRect(col * GRID_CELL, row * GRID_CELL,
                        GRID_CELL, GRID_CELL)
            painter.fillRect(box, STATE_COLORS[state].darker(260))
            cell = fontpage.get_glyph(self.page, code, self.top)
            if cell:
                painter.drawImage(box.adjusted(2, 2, -2, -2),
                                  _cell_image(cell))
            painter.setPen(QPen(STATE_COLORS[state], 1))
            painter.drawRect(box.adjusted(0, 0, -1, -1))
        if self.selected is not None and self.selected in self.states:
            row, col = divmod(self.selected, GRID_COLS)
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.drawRect(QRect(col * GRID_CELL, row * GRID_CELL,
                                   GRID_CELL, GRID_CELL).adjusted(1, 1, -1, -1))
        painter.end()


def _cell_image(cell, palette=None):
    """One glyph as an image, in the plain white the editor draws with."""
    height = len(cell)
    width = len(cell[0]) if height else 0
    buffer = bytearray(width * height * 4)
    for y, row in enumerate(cell):
        for x, index in enumerate(row):
            if not index:
                continue
            if palette:
                r, g, b, a = palette[index]
            else:
                level = 255 - min(index, 7) * 22
                r = g = b = level
                a = 255
            at = (y * width + x) * 4
            buffer[at] = b
            buffer[at + 1] = g
            buffer[at + 2] = r
            buffer[at + 3] = a
    return QImage(bytes(buffer), width, height, width * 4,
                  QImage.Format.Format_ARGB32).copy()


class _PixelEditor(QWidget):
    """One glyph, big enough to draw in."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cell = None
        self.pen = 1
        self.setMinimumSize(fontpage.GLYPH_W * EDIT_ZOOM + 1,
                            fontpage.GLYPH_H * EDIT_ZOOM + 1)

    def set_cell(self, cell):
        self.cell = [list(row) for row in cell] if cell else None
        self.update()

    def _paint_at(self, pos):
        if self.cell is None:
            return
        x = int(pos.x()) // EDIT_ZOOM
        y = int(pos.y()) // EDIT_ZOOM
        if 0 <= y < len(self.cell) and 0 <= x < len(self.cell[0]):
            if self.cell[y][x] != self.pen:
                self.cell[y][x] = self.pen
                self.changed.emit()
                self.update()

    def mousePressEvent(self, event):
        self._paint_at(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons():
            self._paint_at(event.position())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        if self.cell is None:
            painter.setPen(QPen(QColor(150, 150, 150)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Pick a cell")
            painter.end()
            return
        for y, row in enumerate(self.cell):
            for x, index in enumerate(row):
                box = QRect(x * EDIT_ZOOM, y * EDIT_ZOOM, EDIT_ZOOM, EDIT_ZOOM)
                if index:
                    level = 255 - min(index, 7) * 22
                    painter.fillRect(box, QColor(level, level, level))
                else:
                    painter.fillRect(box, QColor(44, 44, 44))
                painter.setPen(QPen(QColor(70, 70, 70)))
                painter.drawRect(box.adjusted(0, 0, -1, -1))
        painter.end()


class FontEditor(QWidget):
    """The translation window: the page's cells, one glyph to draw in,
    and what each cell's code means."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Font & translation")
        self.cd_folder = None
        self.dat_path = None
        self.page = None
        self.top = fontpage.GLYPH_TOP
        self.code = None
        self.used_codes = set()
        self._measure = None

        self.grid = _GlyphGrid()
        self.grid.picked.connect(self._select)
        grid_scroll = QScrollArea()
        grid_scroll.setWidget(self.grid)
        grid_scroll.setWidgetResizable(True)

        self.editor = _PixelEditor()
        self.editor.changed.connect(self._touched)

        self.pen_box = QComboBox()
        for index in PEN_INDICES:
            label = {0: "0 - transparent", 1: "1 - fill",
                     6: "6 - outline"}.get(index, f"{index}")
            self.pen_box.addItem(label, index)
        self.pen_box.setCurrentIndex(1)
        self.pen_box.currentIndexChanged.connect(
            lambda _i: setattr(self.editor, "pen",
                               self.pen_box.currentData()))

        self.char_edit = QLineEdit()
        self.char_edit.setPlaceholderText("character this code means, e.g. ą")
        self.char_edit.setMaxLength(12)

        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)

        assign = QPushButton("Assign character")
        assign.clicked.connect(self._assign)
        write = QPushButton("Write glyph to disc")
        write.clicked.connect(self._write_glyph)
        save = QPushButton("Save translation")
        save.clicked.connect(self._save_table)
        export = QPushButton("Export glyph PNG")
        export.clicked.connect(self._export_glyph)
        imp = QPushButton("Import glyph PNG")
        imp.clicked.connect(self._import_glyph)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("Glyph"))
        side_layout.addWidget(self.editor)
        pen_row = QHBoxLayout()
        pen_row.addWidget(QLabel("Pen"))
        pen_row.addWidget(self.pen_box)
        side_layout.addLayout(pen_row)
        side_layout.addWidget(self.state_label)
        side_layout.addWidget(QLabel("Means"))
        side_layout.addWidget(self.char_edit)
        side_layout.addWidget(assign)
        side_layout.addWidget(write)
        side_layout.addWidget(export)
        side_layout.addWidget(imp)
        side_layout.addWidget(save)
        side_layout.addStretch(1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(grid_scroll)
        split.addWidget(side)
        split.setStretchFactor(0, 3)

        self.status = QLabel("No disc open.")
        layout = QVBoxLayout(self)
        layout.addWidget(split)
        layout.addWidget(self.status)
        self.resize(1000, 640)

    # --- loading -------------------------------------------------------

    def set_source(self, cd_folder, dat_path=None, top=None):
        """Point the window at a disc.

        Opens on whatever is known immediately. Which codes the disc's
        own text uses is measured from the text files, which takes over
        a minute, so a disc that has not been measured yet gets its
        answer from a background scan and the grid is marked again when
        it lands."""
        from functions import codeuse

        self.cd_folder = cd_folder
        self.dat_path = dat_path
        if not cd_folder:
            self.status.setText("No disc open.")
            return
        try:
            self.page = fontpage.read_page(cd_folder)
        except Exception as exc:
            self.status.setText(f"Font page unreadable: {exc}")
            return
        table = translation.load(cd_folder)
        self.top = top or table.glyph_top or fontpage.GLYPH_TOP

        measured = codeuse.cached(dat_path) if dat_path else None
        self.used_codes = set(measured or ())
        self._refresh_grid()
        name = table.name or "none"
        where = os.path.basename(cd_folder)
        if measured is None and dat_path:
            self.status.setText(
                f"{where} - translation: {name}. Measuring which codes the "
                "disc's text uses; until it lands, every named code is "
                "marked taken.")
            self._start_measure(dat_path)
        else:
            self._report_free(where, name)

    def _report_free(self, where, name):
        free = sum(1 for s in self.grid.states.values() if s != "taken")
        art = sum(1 for s in self.grid.states.values() if s == "art")
        self.status.setText(
            f"{where} - translation: {name}. {free} codes free "
            f"({art} of them holding art nothing prints).")

    def _start_measure(self, dat_path):
        if self._measure is not None and self._measure.isRunning():
            return
        self._measure = _Measure(dat_path, self)
        self._measure.done.connect(self._measured)
        self._measure.start()

    def _measured(self, used):
        if not used or self.page is None:
            self.status.setText("Could not measure the disc's text; every "
                                "named code is marked taken.")
            return
        self.used_codes = set(used)
        self._refresh_grid()
        if self.code is not None:
            self._select(self.code)
        self._report_free(os.path.basename(self.cd_folder or ""),
                          translation.active().name or "none")

    def _refresh_grid(self):
        states = {}
        for code, state, _char in translation.free_codes(
                self.page, self.used_codes, self.top):
            states[code] = state
        self.grid.set_page(self.page, self.top, states)

    # --- editing -------------------------------------------------------

    def _select(self, code):
        self.code = code
        self.editor.set_cell(fontpage.get_glyph(self.page, code, self.top))
        state = self.grid.states.get(code, "taken")
        char = translation.active().letters().get(code)
        self.state_label.setText(
            f"Code {code} (0x{code:02X}) - {STATE_TEXT[state]}")
        self.char_edit.setText("" if char is None else char)

    def _touched(self):
        if self.code is not None:
            self.status.setText(
                f"Code 0x{self.code:02X} edited - not written yet.")

    def _assign(self):
        if self.code is None:
            return
        try:
            translation.active().claim(self.code,
                                       self.char_edit.text() or None)
        except ValueError as exc:
            QMessageBox.warning(self, "Not a drawable code", str(exc))
            return
        translation.apply(translation.active())
        self._refresh_grid()
        self.status.setText(
            f"0x{self.code:02X} now means {self.char_edit.text()!r}. "
            "Save the translation to keep it.")

    def _write_glyph(self):
        if self.code is None or self.editor.cell is None:
            return
        fontpage.set_glyph(self.page, self.code, self.editor.cell, self.top)
        try:
            fontpage.write_page(self.cd_folder, self.page)
        except Exception as exc:
            QMessageBox.warning(self, "Could not write", str(exc))
            return
        self._refresh_grid()
        self.status.setText(f"Glyph 0x{self.code:02X} written to the IMG.")

    def _save_table(self):
        if not self.cd_folder:
            return
        table = translation.active()
        table.glyph_top = self.top
        path = translation.save(self.cd_folder, table)
        self.status.setText(f"Translation saved to {path}")

    def _export_glyph(self):
        if self.code is None or self.editor.cell is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export glyph", f"glyph_{self.code:02X}.png", "PNG (*.png)")
        if not path:
            return
        _cell_image(self.editor.cell).save(path)
        self.status.setText(f"Glyph exported to {path}")

    def _import_glyph(self):
        if self.code is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import glyph", "", "PNG (*.png)")
        if not path:
            return
        image = QPixmap(path).toImage()
        if image.width() != fontpage.GLYPH_W or \
                image.height() != fontpage.GLYPH_H:
            QMessageBox.warning(
                self, "Wrong size",
                f"A glyph is {fontpage.GLYPH_W}x{fontpage.GLYPH_H}; that "
                f"file is {image.width()}x{image.height()}.")
            return
        # Anything not transparent becomes the fill; the outline is drawn
        # in afterwards by hand, since a flat PNG carries no index.
        cell = []
        for y in range(fontpage.GLYPH_H):
            row = []
            for x in range(fontpage.GLYPH_W):
                colour = image.pixelColor(x, y)
                row.append(0 if colour.alpha() < 128 else 1)
            cell.append(row)
        self.editor.set_cell(cell)
        self._touched()

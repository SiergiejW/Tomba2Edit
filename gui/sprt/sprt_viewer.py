"""SPRT viewer - the sprite banks of TOMBA2.DAT, drawn against the
area's own VRAM.

Two views over the same bank: the whole thing as a contact sheet, every
sprite on a grid registered to its origin, or one sprite on its own.
Either way the pieces are what's interesting, so the table underneath
lists them with the draw order that decides which of them covers which
(the file has no Z - see gui/sprt/sprt_parser.py).
"""
import os

from PIL.ImageQt import ImageQt
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from functions.psx_vram import VRAMError
from gui import panel_title
from gui.pixel_canvas import PixelCanvas, fit_zoom, zoom_label
from gui.sprt import sprt_edit
from gui.sprt.sprt_edit_panel import SpriteEditPanel
from gui.sprt.sprt_parser import SPRTError, load_sprt
from gui.sprt.sprt_render import (
    VRAMTextures, draw_cell_borders, piece_color, render_sheet, render_sprite)

# Sprites are small, so a view opens zoomed to fit rather than at 1:1,
# where a 24x24 sprite would be a speck in a 900px pane. The caps keep
# the fit sane in the other direction: a one-piece 8x8 sprite blown up
# 24x is no more readable than at 8x.
DEFAULT_ZOOM = 4
SHEET_MAX_FIT_ZOOM = 4
SPRITE_MAX_FIT_ZOOM = 8

# Columns in the contact sheet.
SHEET_COLUMNS = 16

GRID_COLOR = QColor(255, 255, 255, 40)
ORIGIN_COLOR = QColor(255, 255, 255, 150)
SELECTION_COLOR = QColor(90, 170, 255, 220)
HIGHLIGHT_COLOR = QColor(255, 255, 255, 230)


class SpriteCanvas(PixelCanvas):
    """The bank's own overlays on top of the shared pixel canvas:
    origin crosshairs, per-piece outlines, and the contact sheet's cell
    grid and selection."""

    def __init__(self, parent=None):
        super().__init__(zoom=DEFAULT_ZOOM, parent=parent)
        self.mode = "sheet"
        self.origin = (0, 0)          # sprite mode: origin inside the image
        self.cell = (1, 1, 0, 0)      # sheet mode: (w, h, origin x, origin y)
        self.columns = SHEET_COLUMNS
        self.sprite_count = 0
        self.selected = None
        self.piece_rects = []         # (index, x, y, w, h) in image pixels
        self.highlighted_piece = None
        self.show_origin = True
        self.show_outlines = False
        self.show_grid = True

    def set_content(self, image, mode, origin=(0, 0), cell=(1, 1, 0, 0),
                    columns=SHEET_COLUMNS, sprite_count=0, piece_rects=()):
        self.mode = mode
        self.origin = origin
        self.cell = cell
        self.columns = columns
        self.sprite_count = sprite_count
        self.piece_rects = list(piece_rects)
        self.highlighted_piece = None
        self.set_image(image)

    def clear(self):
        self.piece_rects = []
        self.sprite_count = 0
        super().clear()

    def sprite_at(self, x, y):
        """Which sprite the image pixel (x, y) falls in, on the contact
        sheet, or None."""
        if self.mode != "sheet" or not self.sprite_count:
            return None
        cw, ch, _, _ = self.cell
        col, row = x // cw, y // ch
        index = row * self.columns + col
        if 0 <= col < self.columns and 0 <= index < self.sprite_count:
            return int(index)
        return None

    def paint_overlays(self, painter, area):
        if self.mode == "sheet":
            self._paint_sheet_overlays(painter, area)
        else:
            self._paint_sprite_overlays(painter)

    def _paint_sheet_overlays(self, painter, area):
        cw, ch, ox, oy = self.cell
        s = self.scaled
        cell_w, cell_h = max(s(cw), 1), max(s(ch), 1)
        rows = (self.sprite_count + self.columns - 1) // max(self.columns, 1)
        # Only the cells the repaint actually touches.
        first_col = max(0, area.left() // cell_w)
        last_col = min(self.columns - 1, area.right() // cell_w)
        first_row = max(0, area.top() // cell_h)
        last_row = min(rows - 1, area.bottom() // cell_h)
        if self.show_grid:
            painter.setPen(QPen(GRID_COLOR, 1))
            for c in range(first_col, last_col + 2):
                painter.drawLine(c * cell_w, 0, c * cell_w, rows * cell_h)
            for r in range(first_row, last_row + 2):
                painter.drawLine(0, r * cell_h, self.columns * cell_w, r * cell_h)
        if self.show_origin:
            painter.setPen(QPen(ORIGIN_COLOR, 1))
            for r in range(first_row, last_row + 1):
                for c in range(first_col, last_col + 1):
                    if r * self.columns + c >= self.sprite_count:
                        break
                    x, y = c * cell_w + s(ox), r * cell_h + s(oy)
                    painter.drawLine(x - 3, y, x + 3, y)
                    painter.drawLine(x, y - 3, x, y + 3)
        if self.selected is not None and self.selected < self.sprite_count:
            painter.setPen(QPen(SELECTION_COLOR, 2))
            painter.drawRect(QRect((self.selected % self.columns) * cell_w,
                                   (self.selected // self.columns) * cell_h,
                                   cell_w, cell_h))

    def _paint_sprite_overlays(self, painter):
        s = self.scaled
        if self.show_outlines:
            for index, x, y, w, h in self.piece_rects:
                r, g, b = piece_color(index)
                painter.setPen(QPen(QColor(r, g, b, 220), 1))
                painter.drawRect(QRect(s(x), s(y), s(w), s(h)))
        if self.highlighted_piece is not None:
            for index, x, y, w, h in self.piece_rects:
                if index == self.highlighted_piece:
                    painter.setPen(QPen(HIGHLIGHT_COLOR, 2))
                    painter.drawRect(QRect(s(x), s(y), s(w), s(h)))
        if self.show_origin:
            ox, oy = self.origin
            x, y = s(ox), s(oy)
            painter.setPen(QPen(ORIGIN_COLOR, 1))
            painter.drawLine(x - 6, y, x + 6, y)
            painter.drawLine(x, y - 6, x, y + 6)


class SPRTViewer(QWidget):
    """Sprite bank browser: the bank on the left, the drawing in the
    middle, the selected sprite's pieces underneath."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sprt_data = None
        self._source = None
        self._vram = None
        self._vram_original = None
        # Set by MainWindow so a save can mark the IMG dirty.
        self.img_written = None
        # Set by MainWindow to stage the SPRT blob as a file edit.
        self.stage_edit = None
        self._blob = None
        self.textures = None
        self.current_index = None
        self._sprite_images = {}     # sprite index -> (PIL image, ox, oy)
        self._sheet_image = None     # PIL image of the whole bank
        # The tree row's name, set by MainWindow, so a save dialog opens
        # with what the file is called rather than its offsets.
        self.export_name = None
        self._source = ("", 0, 0, None)  # dat path, dat_start, offset, chunk
        self._file_key = None        # which blob is loaded, for _fit_zoom
        self._fitted_for = None      # (view, file key) the zoom was fitted to

        self.canvas = SpriteCanvas()
        self.canvas.clicked.connect(self._on_canvas_clicked)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.canvas)

        self.sprite_table = QTableWidget(0, 6)
        self.sprite_table.setHorizontalHeaderLabels(
            ["#", "Pieces", "W", "H", "Pages", "At"])
        self._prepare_table(self.sprite_table)
        self.sprite_table.itemSelectionChanged.connect(self._on_sprite_row_changed)

        self.piece_table = QTableWidget(0, 13)
        self.piece_table.setHorizontalHeaderLabels(
            ["#", "Draw", "U", "V", "W", "H", "Page", "bpp", "CLUT", "STP",
             "Flip", "Pos", "At"])
        self._prepare_table(self.piece_table)
        self.piece_table.itemSelectionChanged.connect(self._on_piece_row_changed)

        self.info_label = panel_title.make_info_label("No SPRT loaded")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_toolbar())

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(panel_title.make_panel_title("Sprites"))
        left_layout.addWidget(self.sprite_table)

        right = QSplitter(Qt.Orientation.Vertical, self)
        canvas_panel = QWidget(self)
        canvas_layout = QVBoxLayout(canvas_panel)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)
        canvas_layout.addWidget(self.scroll_area)
        pieces_panel = QWidget(self)
        pieces_layout = QVBoxLayout(pieces_panel)
        pieces_layout.setContentsMargins(0, 0, 0, 0)
        pieces_layout.setSpacing(0)
        pieces_layout.addWidget(panel_title.make_panel_title(
            "Pieces (drawn last to first - piece 0 ends up on top)"))
        pieces_layout.addWidget(self.piece_table)
        # Painting the selected piece - see gui/sprt/sprt_edit_panel.py.
        self.editor = SpriteEditPanel(self)
        self.editor.edited.connect(self._on_edited)
        self.editor.saved_to_img = self._on_saved_to_img
        self.editor.clut_committed = self._on_clut_committed
        right.addWidget(canvas_panel)
        right.addWidget(pieces_panel)
        right.addWidget(self.editor)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 0)
        right.setStretchFactor(2, 0)
        right.setSizes([420, 170, 300])

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 900])
        layout.addWidget(splitter, stretch=1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 4, 10, 4)
        bottom.addWidget(self.info_label)
        layout.addLayout(bottom)

    def _build_toolbar(self):
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 5, 10, 5)
        bar.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.sheet_btn = QPushButton("Whole bank")
        self.sheet_btn.setCheckable(True)
        self.sheet_btn.setChecked(True)
        self.sheet_btn.setToolTip("Every sprite of this file on one grid, "
                                  "each drawn around its own origin")
        self.sheet_btn.toggled.connect(self._on_mode_toggled)

        zoom_out = QPushButton("Zoom Out")
        zoom_in = QPushButton("Zoom In")
        zoom_reset = QPushButton("1:1")
        zoom_out.clicked.connect(lambda: self._zoom_by(-1))
        zoom_in.clicked.connect(lambda: self._zoom_by(1))
        zoom_reset.clicked.connect(lambda: self._set_zoom(1))
        self.zoom_label = QLabel(f"{DEFAULT_ZOOM}x")

        self.origin_check = QCheckBox("Origin")
        self.origin_check.setChecked(True)
        self.origin_check.setToolTip("Crosshair at each sprite's origin - the "
                                     "point its pieces are placed relative to")
        self.origin_check.toggled.connect(self._on_origin_toggled)

        self.outline_check = QCheckBox("Piece outlines")
        self.outline_check.setToolTip("Outline each piece of the single sprite "
                                      "in its own colour")
        self.outline_check.setEnabled(False)  # single-sprite view only, and we open on the sheet
        self.outline_check.toggled.connect(self._on_outline_toggled)

        self.grid_check = QCheckBox("Grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self._on_grid_toggled)

        export_btn = QPushButton("Export PNG")
        export_btn.setToolTip("Save what's on screen - the whole bank or the "
                              "single sprite - as a transparent PNG")
        export_btn.clicked.connect(self.export_png)

        export_all_btn = QPushButton("Export Sheet")
        export_all_btn.setToolTip(
            "Save the whole bank as one PNG - every card laid out on a "
            "sheet - named after this file in the tree. Works whether or "
            "not the Whole bank view is on.")
        export_all_btn.clicked.connect(self.export_bank_png)

        for w in (self.sheet_btn, zoom_out, zoom_in, zoom_reset, self.zoom_label,
                  self.origin_check, self.outline_check, self.grid_check,
                  export_btn, export_all_btn):
            bar.addWidget(w)
        return bar

    @staticmethod
    def _prepare_table(table):
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)

    # --- loading ---

    def load_sprt_data(self, dat_file_path, dat_start, offset, size,
                       chunk_index=None, vram_bytes=None):
        """Parse and draw one SPRT blob.

        `vram_bytes` is the area's decompressed VRAM (see
        gui.vram_viewer.decode_vram_bytes) and is what the pieces are
        cut out of. Without it the sprites still parse and lay out -
        they're just drawn as flat blocks, since a piece is nothing but
        a rectangle of somebody else's VRAM."""
        try:
            self.sprt_data = load_sprt(dat_file_path, dat_start, offset, size)
        except (SPRTError, OSError) as e:
            self._clear("Not readable as SPRT: {}".format(e))
            return False

        self._source = (dat_file_path, dat_start, offset, chunk_index)
        self._file_key = (dat_file_path, dat_start, offset)
        self.textures = None
        vram_note = "no VRAM for this area - showing piece layout only"
        # The editor paints into this buffer, so it has to be the very
        # one the renderer samples - not a copy - or the sprite would
        # not change as it is drawn on.
        self._vram = bytearray(vram_bytes) if vram_bytes is not None else None
        self._vram_original = bytes(self._vram) if self._vram else None
        self.editor.cd_folder = (os.path.dirname(dat_file_path)
                                 if dat_file_path else None)
        self.editor.chunk_index = chunk_index
        # The bank's own bytes and the palettes its pieces already use -
        # a recolour is nearly always to one of those.
        with open(dat_file_path, "rb") as f:
            f.seek(dat_start + offset)
            self._blob = f.read(size)
        self.editor.set_pool(sprt_edit.clut_pool(self.sprt_data), self._blob)
        self.editor.set_piece(None, None)
        if self._vram is not None:
            try:
                self.textures = VRAMTextures(self._vram)
                vram_note = None
            except VRAMError as e:
                vram_note = str(e)

        self._sprite_images.clear()
        self._sheet_image = None
        self.current_index = 0 if self.sprt_data.sprites else None
        self._populate_sprite_table()
        self._show_current()
        self._update_info(vram_note)
        return True

    def _clear(self, message):
        self.sprt_data = None
        self.textures = None
        self.current_index = None
        self._sprite_images.clear()
        self._sheet_image = None
        self.sprite_table.setRowCount(0)
        self.piece_table.setRowCount(0)
        self.canvas.clear()
        panel_title.set_info(self.info_label, message)

    def _update_info(self, vram_note=None):
        if not self.sprt_data:
            return
        _, dat_start, offset, chunk = self._source
        parts = [
            f"{len(self.sprt_data.sprites)} sprites, {self.sprt_data.piece_count} pieces",
            f"table 0x{self.sprt_data.table_size:X}",
            f"blob 0x{self.sprt_data.size:X} @ 0x{dat_start + offset:08X}",
        ]
        if chunk is not None:
            parts.append(f"AREA_{chunk:02X}")
        if vram_note:
            parts.append(vram_note)
        odd = self.sprt_data.odd_pieces
        if odd:
            parts.append(f"{len(odd)} piece(s) are not axis-aligned rectangles "
                         "and are drawn as if they were")
        panel_title.set_info(self.info_label, "  |  ".join(parts))

    # --- tables ---

    def _populate_sprite_table(self):
        table = self.sprite_table
        table.blockSignals(True)
        sprites = self.sprt_data.sprites if self.sprt_data else []
        table.setRowCount(len(sprites))
        for row, sprite in enumerate(sprites):
            x0, y0, x1, y1 = sprite.extent(include_origin=False)
            pages = ",".join(str(p) for p in sorted({p.texpage for p in sprite.pieces}))
            cells = [str(sprite.index), str(len(sprite.pieces)), str(x1 - x0),
                     str(y1 - y0), pages, f"0x{sprite.offset:X}"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, sprite.index)
                table.setItem(row, col, item)
        table.blockSignals(False)
        if sprites:
            table.selectRow(0)

    def _populate_piece_table(self, sprite):
        """Refill the piece list, keeping whichever piece was selected.

        The selection has to be restored by PIECE, not by row, and the
        editor driven explicitly afterwards. Leaving it to the signal
        does not work: the rows are rebuilt with signals blocked, so
        row 0 stays "selected" from the previous sprite and clicking it
        emits nothing - which is why a piece would sometimes refuse to
        open at all."""
        table = self.piece_table
        was = self._selected_piece()
        table.blockSignals(True)
        pieces = sprite.pieces if sprite else []
        table.setRowCount(len(pieces))
        for row, piece in enumerate(pieces):
            flips = "".join(("H" if piece.hflip else "", "V" if piece.vflip else "")) or "-"
            # Pieces are stored front-to-back, so the last one is drawn
            # first: draw order 1 is the bottom layer.
            draw_order = len(pieces) - piece.index
            cells = [
                str(piece.index),
                f"{draw_order}" + (" (top)" if piece.index == 0 and len(pieces) > 1 else ""),
                str(piece.u0),
                str(piece.v0),
                str(piece.ww),
                str(piece.hh),
                str(piece.texpage),
                "8" if piece.is_8bpp else "4",
                f"0x{piece.clut_index:04X}",
                str(piece.semi_transparency),
                flips,
                f"{piece.pX},{piece.pY}",
                f"0x{piece.offset:X}",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, piece.index)
                    r, g, b = piece_color(piece.index)
                    item.setForeground(QColor(r, g, b))
                table.setItem(row, col, item)
        table.clearSelection()
        wanted = was if any(p.index == was for p in pieces) else (
            pieces[0].index if pieces else None)
        for row, piece in enumerate(pieces):
            if piece.index == wanted:
                table.selectRow(row)
                break
        table.blockSignals(False)
        self.canvas.highlighted_piece = wanted
        self._edit_piece(wanted)

    def _selected_piece(self):
        """Which piece index is selected, or None."""
        rows = self.piece_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.piece_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_sprite_row_changed(self):
        rows = self.sprite_table.selectionModel().selectedRows()
        if not rows or not self.sprt_data:
            return
        item = self.sprite_table.item(rows[0].row(), 0)
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None or index == self.current_index:
            return
        self.current_index = index
        self._show_current()
        self._print_sprite(index)

    def _base_address(self):
        """Where this SPRT blob starts in the DAT, so a piece can be
        named by an address rather than by its place in a list."""
        if not self._source:
            return 0
        _path, dat_start, offset, _chunk = self._source
        return (dat_start or 0) + (offset or 0)

    def _print_sprite(self, index):
        data = self.sprt_data
        if not data or index is None or index >= len(data.sprites):
            return
        sprite = data.sprites[index]
        base = self._base_address()
        print(f"selected: SPRT @ 0x{base:X}  sprite {index} "
              f"@ 0x{base + sprite.offset:X} (+0x{sprite.offset:X})  "
              f"{len(sprite.pieces)} piece(s)  extent {sprite.extent()}")

    def _on_piece_row_changed(self):
        rows = self.piece_table.selectionModel().selectedRows()
        if not rows:
            self.canvas.highlighted_piece = None
        else:
            item = self.piece_table.item(rows[0].row(), 0)
            self.canvas.highlighted_piece = item.data(Qt.ItemDataRole.UserRole)
            self._print_piece(item.data(Qt.ItemDataRole.UserRole))
            self._edit_piece(item.data(Qt.ItemDataRole.UserRole))
        self.canvas.update()

    def _edit_piece(self, piece_index):
        """Hand the selected piece to the editor."""
        data = self.sprt_data
        if not data or self.current_index is None or piece_index is None:
            self.editor.set_piece(None, None)
            return
        sprite = data.sprites[self.current_index]
        piece = next((p for p in sprite.pieces if p.index == piece_index), None)
        self.editor.set_piece(piece, self._vram, self._vram_original)

    def _on_edited(self):
        """A texel was painted - the cached images are stale."""
        self._sprite_images.clear()
        self._sheet_image = None
        if self.textures is not None:
            # The palettes are cached per CLUT and a painted texel can
            # be inside one, so they go too.
            self.textures._palettes.clear()
        self._show_current(keep_pieces=True)

    def _on_clut_committed(self, blob):
        """A piece was pointed at another palette: stage the SPRT blob
        and redraw, since every cached image of it is now wrong."""
        self._blob = blob
        self._sprite_images.clear()
        self._sheet_image = None
        self._show_current()
        self._populate_piece_table(
            self.sprt_data.sprites[self.current_index])
        if self.stage_edit is not None:
            self.stage_edit(blob, "sprite palette changed")

    def _on_saved_to_img(self):
        """The editor rewrote TOMBA2.IMG. MainWindow has to know, or an
        export ships the original IMG beside the rewritten IDX - see
        MainWindow.img_dirty."""
        if self.img_written is not None:
            self.img_written()

    def _print_piece(self, piece_index):
        data = self.sprt_data
        if not data or self.current_index is None or piece_index is None:
            return
        sprite = data.sprites[self.current_index]
        piece = next((p for p in sprite.pieces if p.index == piece_index), None)
        if piece is None:
            return
        base = self._base_address()
        print(f"selected: SPRT @ 0x{base:X}  sprite {self.current_index} "
              f"piece {piece.index} @ 0x{base + piece.offset:X} "
              f"(+0x{piece.offset:X})  page {piece.pg}  clut 0x{piece.clut:X}  "
              f"{piece.ww}x{piece.hh} at ({piece.pX}, {piece.pY})")

    def _on_canvas_clicked(self, x, y):
        index = self.canvas.sprite_at(x, y)
        if index is not None:
            self.select_sprite(index)

    def select_sprite(self, index):
        """Show sprite `index` on its own - what clicking a cell of the
        contact sheet does."""
        if not self.sprt_data or not (0 <= index < len(self.sprt_data.sprites)):
            return
        self.current_index = index
        self.sprite_table.selectRow(index)
        if self.sheet_btn.isChecked():
            self.sheet_btn.setChecked(False)   # redraws through _on_mode_toggled
        else:
            self._show_current()

    # --- drawing ---

    def _sprite_image(self, index):
        cached = self._sprite_images.get(index)
        if cached is None:
            sprite = self.sprt_data.sprites[index]
            cached = render_sprite(sprite, self.textures, margin=1)
            self._sprite_images[index] = cached
        return cached

    def _show_current(self, keep_pieces=False):
        """Redraw. `keep_pieces` skips rebuilding the piece list, which
        is what painting wants: a texel changes the picture and nothing
        about the pieces, and rebuilding the list mid-stroke would move
        the selection out from under the brush."""
        if not self.sprt_data or not self.sprt_data.sprites:
            self.canvas.clear()
            self.piece_table.setRowCount(0)
            return

        index = self.current_index if self.current_index is not None else 0
        sprite = self.sprt_data.sprites[index]
        if not keep_pieces:
            self._populate_piece_table(sprite)
        self.canvas.selected = index

        if self.sheet_btn.isChecked():
            if self._sheet_image is None:
                self._sheet_image = render_sheet(
                    self.sprt_data.sprites, self.textures, SHEET_COLUMNS)
            image, cell_w, cell_h, ox, oy = self._sheet_image
            self.canvas.set_content(
                _to_qimage(image), "sheet", cell=(cell_w, cell_h, ox, oy),
                columns=SHEET_COLUMNS, sprite_count=len(self.sprt_data.sprites))
            self._fit_zoom("sheet", image.size, SHEET_MAX_FIT_ZOOM)
        else:
            image, sx, sy = self._sprite_image(index)
            x0, y0, _, _ = sprite.extent()
            rects = [(p.index, p.pX - x0 + 1, p.pY - y0 + 1, p.ww, p.hh)
                     for p in sprite.pieces]  # +1 for render_sprite's margin
            self.canvas.set_content(_to_qimage(image), "sprite", origin=(sx, sy),
                                    piece_rects=rects)
            self._fit_zoom("sprite", image.size, SPRITE_MAX_FIT_ZOOM)

    def _fit_zoom(self, view, image_size, max_zoom):
        """Pick a zoom that shows the whole thing when a view is first
        entered - a 400-sprite sheet and a 24x24 sprite want wildly
        different ones - and then leave it alone, so paging through the
        bank doesn't keep resizing under the cursor."""
        key = (view, self._file_key)
        if key == self._fitted_for:
            return
        self._fitted_for = key
        self._set_zoom(fit_zoom(image_size, self.scroll_area.viewport().size(),
                                max_zoom))

    # --- toolbar ---

    def _on_mode_toggled(self, checked):
        self.outline_check.setEnabled(not checked)
        self._show_current()

    def _on_origin_toggled(self, checked):
        self.canvas.show_origin = checked
        self.canvas.update()

    def _on_outline_toggled(self, checked):
        self.canvas.show_outlines = checked
        self.canvas.update()

    def _on_grid_toggled(self, checked):
        self.canvas.show_grid = checked
        self.canvas.update()

    def _zoom_by(self, direction):
        self.canvas.zoom_by(direction)
        self.zoom_label.setText(zoom_label(self.canvas.zoom))

    def _set_zoom(self, zoom):
        self.canvas.set_zoom(zoom)
        self.zoom_label.setText(zoom_label(self.canvas.zoom))

    def export_png(self):
        """Save whatever the canvas is showing, at 1:1."""
        if not self.sprt_data or not self.sprt_data.sprites:
            return
        _, dat_start, offset, chunk = self._source
        area = f"AREA_{chunk:02X}_" if chunk is not None else ""
        stem = self.export_name or f"{area}SPRT_{dat_start + offset:08X}"
        if self.sheet_btn.isChecked():
            image = self._sheet_image[0]
            default = f"{stem}.png"
        else:
            image = self._sprite_image(self.current_index or 0)[0]
            default = f"{stem}_{self.current_index or 0:03d}.png"
        path, _unused = QFileDialog.getSaveFileName(
            self, "Save sprite as PNG", default, "PNG Image (*.png)")
        if not path:
            return
        try:
            image.save(path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write {path}:\n\n{e}")
            return
        panel_title.set_info(self.info_label, f"Wrote {os.path.basename(path)}")


    def export_bank_png(self):
        """The whole bank as one PNG - the contact sheet, saved as seen.

        The sheet is rendered here rather than taken from the canvas,
        so this works with the single-sprite view on and does not depend
        on having looked at the bank first.

        Named after the row in the tree, since that is what the file is
        called everywhere else in the program - the offsets the other
        export falls back to are only useful for a file nobody has
        named yet."""
        if not self.sprt_data or not self.sprt_data.sprites:
            return
        if self._sheet_image is None:
            self._sheet_image = render_sheet(
                self.sprt_data.sprites, self.textures, SHEET_COLUMNS)
        image, cell_w, cell_h, _ox, _oy = self._sheet_image
        # The Grid checkbox draws cell lines on screen as an overlay,
        # which is not in the image itself - so the same switch decides
        # whether they are drawn into the file.
        if self.grid_check.isChecked():
            image = draw_cell_borders(image, cell_w, cell_h, SHEET_COLUMNS,
                                      len(self.sprt_data.sprites))
        _unused, dat_start, offset, chunk = self._source
        area = f"AREA_{chunk:02X}_" if chunk is not None else ""
        default = (self.export_name
                   or f"{area}SPRT_{dat_start + offset:08X}") + ".png"
        path, _picked = QFileDialog.getSaveFileName(
            self, "Save the whole bank as PNG", default, "PNG Image (*.png)")
        if not path:
            return
        try:
            image.save(path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed",
                                 f"Couldn't write {path}:\n\n{e}")
            return
        panel_title.set_info(
            self.info_label,
            f"Wrote {os.path.basename(path)} - {len(self.sprt_data.sprites)} "
            f"sprites, {image.width}x{image.height}")


def _to_qimage(pil_image):
    """PIL RGBA -> QImage, copied so it survives the PIL image (and the
    buffer ImageQt wraps) being garbage collected."""
    return ImageQt(pil_image).copy()

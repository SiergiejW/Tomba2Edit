"""BGMP viewer - the backgrounds of TOMBA2.DAT, drawn against the
area's own VRAM.

The background is on the right, the texture page it is cut from below
it. A background is nothing but an arrangement of that page's 16x16
cells (see gui/bgmp/bgmp_parser.py), so the two views are wired
together: click a tile of the background to see which cell and palette
it came from, or click a cell of the page to light up every tile using
it.
"""
import os

from PIL.ImageQt import ImageQt
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QIcon, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from functions.psx_vram import VRAMError
from gui import panel_title
from gui.bgmp.bgmp_parser import BGMPError, PAGE_TILES, TILE, load_bgmp
from gui.bgmp.bgmp_render import (
    BackgroundTextures, palette_swatch, render_background, render_page)
from gui.pixel_canvas import PixelCanvas, fit_zoom, zoom_label

# A background is 576x1152 or so - it opens shrunk to fit, where the
# texture page is small enough to want magnifying instead.
BACKGROUND_MAX_FIT_ZOOM = 2
PAGE_MAX_FIT_ZOOM = 3

# Widget pixels per tile below which grids and outlines stop being
# either, and become fill.
GRID_MIN_STEP = 8

GRID_COLOR = QColor(255, 255, 255, 45)
SELECTION_COLOR = QColor(90, 170, 255, 230)
USAGE_COLOR = QColor(255, 220, 90, 200)
USAGE_FILL = QColor(255, 220, 90, 110)


class TileGridCanvas(PixelCanvas):
    """Shared behaviour of the two views: a 16-pixel tile grid, one
    selected tile, and any number of highlighted ones."""

    def __init__(self, zoom=1, parent=None):
        super().__init__(zoom=zoom, parent=parent)
        self.show_grid = True
        self.selected = None       # (col, row)
        self.highlights = []       # [(col, row), ...]

    def tile_at(self, x, y):
        return x // TILE, y // TILE

    def set_selected(self, cell):
        self.selected = cell
        self.update()

    def set_highlights(self, cells):
        self.highlights = list(cells)
        self.update()

    def _tile_rect(self, col, row):
        step = max(self.scaled(TILE), 1)
        return QRect(col * step, row * step, step, step)

    def paint_overlays(self, painter, area):
        if self.image is None:
            return
        step = max(self.scaled(TILE), 1)
        cols = self.image.width() // TILE
        rows = self.image.height() // TILE
        if self.show_grid and step >= GRID_MIN_STEP:
            # Any tighter than this and a grid is just a fill.
            painter.setPen(QPen(GRID_COLOR, 1))
            first_col = max(0, area.left() // step)
            last_col = min(cols, area.right() // step + 1)
            first_row = max(0, area.top() // step)
            last_row = min(rows, area.bottom() // step + 1)
            for c in range(first_col, last_col + 1):
                painter.drawLine(c * step, 0, c * step, rows * step)
            for r in range(first_row, last_row + 1):
                painter.drawLine(0, r * step, cols * step, r * step)
        if self.highlights:
            # Zoomed out, outlining every tile that uses a cell just
            # fills the area with mesh - wash them instead.
            outline = step >= GRID_MIN_STEP
            painter.setPen(QPen(USAGE_COLOR, 1) if outline else Qt.PenStyle.NoPen)
            painter.setBrush(Qt.BrushStyle.NoBrush if outline else USAGE_FILL)
            for col, row in self.highlights:
                rect = self._tile_rect(col, row)
                if rect.intersects(area):
                    painter.drawRect(rect)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.selected is not None:
            painter.setPen(QPen(SELECTION_COLOR, 2))
            painter.drawRect(self._tile_rect(*self.selected))


class BGMPViewer(QWidget):
    """Background browser: details and palettes on the left, the
    background and its source texture page on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bgmp_data = None
        self.textures = None
        self.vram_bytes = None
        self.page_palette = 0
        self._background_image = None
        self._page_image = None
        self._source = ("", 0, 0, None)   # dat path, dat_start, offset, chunk
        self._file_key = None
        self._fitted = {}                 # view -> the file its zoom was fitted to
        self._vram_note = None

        self.background_canvas = TileGridCanvas()
        self.background_canvas.clicked.connect(self._on_background_clicked)
        self.background_scroll = _scroll_for(self.background_canvas)

        self.page_canvas = TileGridCanvas()
        self.page_canvas.clicked.connect(self._on_page_clicked)
        self.page_scroll = _scroll_for(self.page_canvas)

        self.details_table = QTableWidget(0, 2)
        self.details_table.setHorizontalHeaderLabels(["Field", "Value"])
        _prepare_table(self.details_table)

        self.palette_table = QTableWidget(0, 3)
        self.palette_table.setHorizontalHeaderLabels(["#", "Colours", "Tiles"])
        _prepare_table(self.palette_table)
        self.palette_table.itemSelectionChanged.connect(self._on_palette_row_changed)

        self.page_title = panel_title.make_panel_title("Source texture page")
        self.info_label = QLabel("No BGMP loaded")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_toolbar())

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(panel_title.make_panel_title("Header"))
        left_layout.addWidget(self.details_table)
        left_layout.addWidget(panel_title.make_panel_title("Palettes used"))
        left_layout.addWidget(self.palette_table)
        # Both tables size their columns to their contents, and the
        # palette swatches are wide - without a cap the details pane
        # would push the whole viewer wider than the window.
        left.setMaximumWidth(420)

        right = QSplitter(Qt.Orientation.Vertical, self)
        right.addWidget(_titled(self, "Background", self.background_scroll))
        right.addWidget(_titled(self, self.page_title, self.page_scroll))
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 0)
        right.setSizes([520, 300])

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

        zoom_out = QPushButton("Zoom Out")
        zoom_in = QPushButton("Zoom In")
        zoom_reset = QPushButton("1:1")
        for button in (zoom_out, zoom_in, zoom_reset):
            button.setToolTip("Zooms the background. Ctrl+wheel zooms whichever "
                              "view is under the pointer.")
        zoom_out.clicked.connect(lambda: self._zoom_by(-1))
        zoom_in.clicked.connect(lambda: self._zoom_by(1))
        zoom_reset.clicked.connect(lambda: self._set_zoom(1))
        self.zoom_label = QLabel("1x")

        self.grid_check = QCheckBox("Tile grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self._on_grid_toggled)

        self.alpha_check = QCheckBox("Colour 0 transparent")
        self.alpha_check.setToolTip(
            "Backgrounds are drawn opaque, so the PSX's transparent colour "
            "shows as black. Tick this to punch it out instead, which is how "
            "to see which tiles are empty.")
        self.alpha_check.toggled.connect(self._on_alpha_toggled)

        export_btn = QPushButton("Export PNG")
        export_btn.setToolTip("Save the assembled background as a PNG")
        export_btn.clicked.connect(self.export_png)

        export_page_btn = QPushButton("Export page")
        export_page_btn.setToolTip("Save the source texture page, as coloured "
                                   "by the palette shown below")
        export_page_btn.clicked.connect(self.export_page_png)

        for w in (zoom_out, zoom_in, zoom_reset, self.zoom_label,
                  self.grid_check, self.alpha_check, export_btn, export_page_btn):
            bar.addWidget(w)
        return bar

    # --- loading ---

    def load_bgmp_data(self, dat_file_path, dat_start, offset, size,
                       chunk_index=None, vram_bytes=None):
        """Parse and draw one BGMP blob.

        `vram_bytes` is the area's decompressed VRAM (see
        gui.vram_viewer.decode_vram_bytes), which holds both the tiles
        and their palettes. Without it the map still parses and lays
        out - each tile is drawn as a flat block in its palette's
        colour, since the file itself carries no pixels at all."""
        try:
            self.bgmp_data = load_bgmp(dat_file_path, dat_start, offset, size)
        except (BGMPError, OSError) as e:
            self._clear(f"Not readable as BGMP: {e}")
            return False

        self._source = (dat_file_path, dat_start, offset, chunk_index)
        self._file_key = (dat_file_path, dat_start, offset)
        self.vram_bytes = vram_bytes
        self.textures = None
        self._vram_note = "no VRAM for this area - showing the tile map only"
        if vram_bytes is not None:
            try:
                self.textures = BackgroundTextures(
                    vram_bytes, transparent_zero=self.alpha_check.isChecked())
                self._vram_note = None
            except VRAMError as e:
                self._vram_note = str(e)

        used = self.bgmp_data.palettes_used
        self.page_palette = used[0] if used else 0
        self.background_canvas.set_selected(None)
        self.background_canvas.set_highlights(())
        self.page_canvas.set_selected(None)
        self._populate_details()
        self._populate_palettes()
        self._redraw()
        self._update_info()
        return True

    def _clear(self, message):
        self.bgmp_data = None
        self.textures = None
        self._background_image = None
        self._page_image = None
        self.details_table.setRowCount(0)
        self.palette_table.setRowCount(0)
        self.background_canvas.clear()
        self.page_canvas.clear()
        self.info_label.setText(message)

    def _update_info(self, extra=None):
        if not self.bgmp_data:
            return
        bgmp = self.bgmp_data
        _, dat_start, offset, chunk = self._source
        width, height = bgmp.pixel_size
        parts = [
            f"{bgmp.width}x{bgmp.height} tiles ({width}x{height} px)",
            f"page {bgmp.texpage}",
            f"CLUT 0x{bgmp.clut:04X} at ({bgmp.clut_x},{bgmp.clut_y}), "
            f"{len(bgmp.palettes_used)} of {bgmp.palettes_fit} palettes used",
            f"blob 0x{bgmp.size:X} @ 0x{dat_start + offset:08X}",
        ]
        if chunk is not None:
            parts.append(f"AREA_{chunk:02X}")
        if self._vram_note:
            parts.append(self._vram_note)
        blank = self._blank_palettes()
        if blank:
            parts.append(
                f"palette{'s' if len(blank) > 1 else ''} {','.join(str(i) for i in blank)} "
                "not in this area's VRAM - those tiles draw black")
        if bgmp.slack:
            parts.append(f"{bgmp.slack} bytes of slack after the map")
        if not bgmp.clut_echo_agrees:
            parts.append("header's CLUT x/y disagree with its CLUT word")
        if extra:
            parts.insert(0, extra)
        self.info_label.setText("  |  ".join(parts))

    def _blank_palettes(self):
        """Palettes the map uses that this area's VRAM never filled in."""
        if not self.bgmp_data or self.textures is None:
            return []
        return [i for i in self.bgmp_data.palettes_used
                if self.textures.is_blank_palette(self.bgmp_data, i)]

    # --- tables ---

    def _populate_details(self):
        bgmp = self.bgmp_data
        rows = [
            ("Texture page", f"{bgmp.texpage} (0x{bgmp.texpage:X})"),
            ("Page origin", "byte {}, row {}".format(*bgmp.page_origin)),
            ("CLUT", f"0x{bgmp.clut:04X}"),
            ("CLUT x, y", f"{bgmp.clut_x}, {bgmp.clut_y}"),
            ("Palettes below it", str(bgmp.palettes_fit)),
            ("Map", f"{bgmp.width} x {bgmp.height} tiles"),
            ("Pixels", "{} x {}".format(*bgmp.pixel_size)),
            ("Map bytes", f"0x{bgmp.map_size:X}"),
            ("Trailer", " ".join(f"0x{v:04X}" for v in bgmp.trailer) or "-"),
            ("Slack", f"{bgmp.slack} bytes"),
            ("unk1, unk2", f"0x{bgmp.unk1:X}, 0x{bgmp.unk2:X}"),
            ("unk3, unk4", f"0x{bgmp.unk3:X}, 0x{bgmp.unk4:X}"),
        ]
        self.details_table.setRowCount(len(rows))
        for row, (field, value) in enumerate(rows):
            self.details_table.setItem(row, 0, QTableWidgetItem(field))
            self.details_table.setItem(row, 1, QTableWidgetItem(value))

    def _populate_palettes(self):
        table = self.palette_table
        table.blockSignals(True)
        bgmp = self.bgmp_data
        counts = {}
        for tile in bgmp.tiles:
            counts[tile.palette] = counts.get(tile.palette, 0) + 1
        used = bgmp.palettes_used
        table.setRowCount(len(used))
        for row, index in enumerate(used):
            number = QTableWidgetItem(str(index))
            number.setData(Qt.ItemDataRole.UserRole, index)
            table.setItem(row, 0, number)
            swatch = QTableWidgetItem()
            swatch.setIcon(QIcon(_to_pixmap(
                palette_swatch(bgmp, self.textures, index))))
            table.setItem(row, 1, swatch)
            table.setItem(row, 2, QTableWidgetItem(str(counts.get(index, 0))))
        table.setIconSize(QSize(16 * 12, 12))
        table.resizeColumnsToContents()
        table.blockSignals(False)
        if used:
            # Keep whichever palette the page view is on - this also
            # runs when the swatches are rebuilt for the alpha toggle,
            # which shouldn't throw the selection back to the top.
            self._select_palette_row(self.page_palette if self.page_palette in used
                                     else used[0])

    def _on_palette_row_changed(self):
        rows = self.palette_table.selectionModel().selectedRows()
        if not rows or not self.bgmp_data:
            return
        index = self.palette_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if index is None or index == self.page_palette:
            return
        self.page_palette = index
        self._draw_page()

    # --- drawing ---

    def _redraw(self):
        if not self.bgmp_data:
            return
        self._background_image = render_background(self.bgmp_data, self.textures)
        self.background_canvas.set_image(_to_qimage(self._background_image))
        self._fit("background", self._background_image.size,
                  self.background_scroll, self.background_canvas,
                  BACKGROUND_MAX_FIT_ZOOM)
        self._draw_page()

    def _draw_page(self):
        if not self.bgmp_data:
            return
        self._page_image = render_page(self.bgmp_data, self.textures, self.page_palette)
        self.page_canvas.set_image(_to_qimage(self._page_image))
        self._fit("page", self._page_image.size, self.page_scroll,
                  self.page_canvas, PAGE_MAX_FIT_ZOOM)
        self.page_title.setText(
            f"Source texture page {self.bgmp_data.texpage}, palette {self.page_palette}")

    def _fit(self, view, image_size, scroll, canvas, max_zoom):
        """Fit a view the first time this file reaches it, then leave
        the zoom where the user put it - switching palettes redraws the
        page view, and shouldn't move it."""
        if self._fitted.get(view) == self._file_key:
            return
        self._fitted[view] = self._file_key
        canvas.set_zoom(fit_zoom(image_size, scroll.viewport().size(), max_zoom))
        if view == "background":
            self.zoom_label.setText(zoom_label(canvas.zoom))

    # --- interaction ---

    def _on_background_clicked(self, x, y):
        if not self.bgmp_data:
            return
        col, row = self.background_canvas.tile_at(x, y)
        tile = self.bgmp_data.tile_at(col, row)
        if tile is None:
            return
        self.background_canvas.set_selected((col, row))
        self.background_canvas.set_highlights(())
        self.page_canvas.set_selected((tile.raw & 0x0F, (tile.raw & 0xF0) >> 4))
        if tile.palette != self.page_palette:
            self._select_palette_row(tile.palette)
        self._update_info(
            f"tile ({col},{row}) = 0x{tile.raw:04X}: page cell {tile.cell} "
            f"at ({tile.page_x},{tile.page_y}), palette {tile.palette}")

    def _on_page_clicked(self, x, y):
        if not self.bgmp_data:
            return
        col, row = self.page_canvas.tile_at(x, y)
        cell = row * PAGE_TILES + col
        users = self.bgmp_data.tiles_using(cell)
        self.page_canvas.set_selected((col, row))
        self.background_canvas.set_selected(None)
        self.background_canvas.set_highlights([(t.col, t.row) for t in users])
        self._update_info(
            f"page cell {cell} at ({col * TILE},{row * TILE}): used by "
            f"{len(users)} tile(s)")

    def _select_palette_row(self, palette):
        for row in range(self.palette_table.rowCount()):
            if self.palette_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == palette:
                self.palette_table.selectRow(row)
                return

    # --- toolbar ---

    def _on_grid_toggled(self, checked):
        for canvas in (self.background_canvas, self.page_canvas):
            canvas.show_grid = checked
            canvas.update()

    def _on_alpha_toggled(self, checked):
        if self.vram_bytes is None or not self.bgmp_data:
            return
        try:
            self.textures = BackgroundTextures(self.vram_bytes, transparent_zero=checked)
        except VRAMError:
            return
        self._populate_palettes()
        self._redraw()

    def _zoom_by(self, direction):
        self.background_canvas.zoom_by(direction)
        self.zoom_label.setText(zoom_label(self.background_canvas.zoom))

    def _set_zoom(self, zoom):
        self.background_canvas.set_zoom(zoom)
        self.zoom_label.setText(zoom_label(self.background_canvas.zoom))

    def export_png(self):
        self._export(self._background_image, "background")

    def export_page_png(self):
        self._export(self._page_image, f"page{self.page_palette:02d}")

    def _export(self, image, suffix):
        if image is None:
            return
        _, dat_start, offset, chunk = self._source
        area = f"AREA_{chunk:02X}_" if chunk is not None else ""
        default = f"{area}BGMP_{dat_start + offset:08X}_{suffix}.png"
        path, _unused = QFileDialog.getSaveFileName(
            self, "Save as PNG", default, "PNG Image (*.png)")
        if not path:
            return
        try:
            image.save(path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write {path}:\n\n{e}")
            return
        self.info_label.setText(f"Wrote {os.path.basename(path)}")


def _scroll_for(canvas):
    area = QScrollArea()
    area.setWidgetResizable(False)
    area.setAlignment(Qt.AlignmentFlag.AlignCenter)
    area.setWidget(canvas)
    return area


def _titled(parent, title, widget):
    panel = QWidget(parent)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(panel_title.make_panel_title(title) if isinstance(title, str) else title)
    layout.addWidget(widget)
    return panel


def _prepare_table(table):
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)


def _to_qimage(pil_image):
    """PIL RGBA -> QImage, copied so it survives the PIL image (and the
    buffer ImageQt wraps) being garbage collected."""
    return ImageQt(pil_image).copy()


def _to_pixmap(pil_image):
    return QPixmap.fromImage(_to_qimage(pil_image))

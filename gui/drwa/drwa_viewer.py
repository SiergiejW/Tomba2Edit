"""DRWA viewer - the drawmap at the head of every MDAT entry.

The grid is on top, the level it points at, seen from above, below it.
Both are drawn from the same per-group colours (see
gui/drwa/drwa_render.py), so the two views can be read against each
other: click a cell to light up the polygons it draws, or click the
level to find the cell that draws them.

The grid IS the level seen from above - the two views are the same
picture at different resolutions, one cell to a square patch of world
570-1024 units a side. The header panel measures that square from the
geometry, so a level whose two views don't line up says so in its own
numbers rather than only to the eye.
"""
import os
import struct

from PIL.ImageQt import ImageQt
from PyQt6.QtCore import Qt, QPointF, QRect, QRectF
from PyQt6.QtGui import QColor, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui import panel_title
from gui.drwa.drwa_parser import DRWAError, load_drwa
from gui.drwa.drwa_render import (
    COLOR_MODES, group_colors, render_footprint, render_grid)
from gui.pixel_canvas import PixelCanvas, fit_zoom, zoom_label

# A drawmap is at most 64 cells across, so it wants magnifying; the
# footprint is drawn near a thousand pixels wide and wants shrinking.
GRID_MAX_FIT_ZOOM = 16
FOOTPRINT_MAX_FIT_ZOOM = 1

# Widget pixels per cell below which the cell grid stops being a grid
# and becomes a fill.
GRID_MIN_STEP = 6

GRID_COLOR = QColor(255, 255, 255, 45)
SELECTION_COLOR = QColor(255, 255, 255, 235)
SELECTION_SHADOW = QColor(0, 0, 0, 200)


class CellCanvas(PixelCanvas):
    """The drawmap itself - one pixel per cell, with the selected cell
    outlined and an optional grid over it."""

    def __init__(self, parent=None):
        super().__init__(zoom=8, parent=parent)
        self.show_grid = True
        self.selected = None    # (col, row)

    def set_selected(self, cell):
        self.selected = cell
        self.update()

    def paint_overlays(self, painter, area):
        if self.image is None:
            return
        step = max(self.scaled(1), 1)
        if self.show_grid and step >= GRID_MIN_STEP:
            painter.setPen(QPen(GRID_COLOR, 1))
            for col in range(max(0, area.left() // step),
                             min(self.image.width(), area.right() // step + 1) + 1):
                painter.drawLine(col * step, 0, col * step, self.image.height() * step)
            for row in range(max(0, area.top() // step),
                             min(self.image.height(), area.bottom() // step + 1) + 1):
                painter.drawLine(0, row * step, self.image.width() * step, row * step)
        if self.selected is not None:
            col, row = self.selected
            rect = QRect(col * step, row * step, step, step)
            # Dark under light: a single-pixel cell can be any colour at
            # all, and one outline alone disappears against half of them.
            painter.setPen(QPen(SELECTION_SHADOW, 3))
            painter.drawRect(rect.adjusted(-1, -1, 1, 1))
            painter.setPen(QPen(SELECTION_COLOR, 1))
            painter.drawRect(rect.adjusted(-1, -1, 1, 1))


class FootprintCanvas(PixelCanvas):
    """The level from above, with one group's polygons outlined.

    The outline is drawn here rather than baked into the image so it
    stays one pixel wide at any zoom, and so selecting doesn't cost a
    re-render of the whole level."""

    def __init__(self, parent=None):
        super().__init__(zoom=1, parent=parent)
        self.footprint = None
        self.selected_faces = ()
        self.selected_box = None

    def set_footprint(self, footprint):
        self.footprint = footprint
        self.selected_faces = ()
        self.selected_box = None
        self.set_image(_to_qimage(footprint.image) if footprint else None)

    def set_selected(self, group):
        """`group` is a DRWAGroup, or None to clear."""
        if group is None or self.footprint is None or not group.faces:
            self.selected_faces = ()
            self.selected_box = None
        else:
            self.selected_faces = [
                [self.footprint.project(v[0], v[2]) for v in face]
                for face in group.faces]
            # One group out of a thousand is a small shape on a big map;
            # the outline says which polygons, the box says where to look.
            points = [p for face in self.selected_faces for p in face]
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            self.selected_box = (min(xs), min(ys), max(xs), max(ys))
        self.update()

    def set_image(self, image):
        if image is None:
            self.clear()
        else:
            super().set_image(image)

    def paint_overlays(self, painter, area):
        if not self.selected_faces:
            return
        for width, color in ((3, SELECTION_SHADOW), (1, SELECTION_COLOR)):
            painter.setPen(QPen(color, width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for face in self.selected_faces:
                painter.drawPolygon(QPolygonF(
                    [QPointF(x * self.zoom, y * self.zoom) for x, y in face]))
            if self.selected_box:
                x0, y0, x1, y1 = self.selected_box
                painter.drawRect(QRectF(
                    QPointF(x0 * self.zoom - 4, y0 * self.zoom - 4),
                    QPointF(x1 * self.zoom + 4, y1 * self.zoom + 4)))


class DRWAViewer(QWidget):
    """Drawmap browser: the header and its groups on the left, the grid
    and the level it draws on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drwa = None
        self.footprint = None
        self._grid_image = None
        self._colors = {}
        self._source = ("", 0, 0, None)   # dat path, dat_start, offset, chunk
        self._file_key = None
        self._fitted = {}                 # view -> the file its zoom was fitted to
        self._selected = None             # a DRWAGroup

        self.grid_canvas = CellCanvas()
        self.grid_canvas.clicked.connect(self._on_grid_clicked)
        self.grid_scroll = _scroll_for(self.grid_canvas)

        self.footprint_canvas = FootprintCanvas()
        self.footprint_canvas.clicked.connect(self._on_footprint_clicked)
        self.footprint_scroll = _scroll_for(self.footprint_canvas)

        self.details_table = QTableWidget(0, 2)
        self.details_table.setHorizontalHeaderLabels(["Field", "Value"])
        _prepare_table(self.details_table)

        self.groups_table = QTableWidget(0, 7)
        self.groups_table.setHorizontalHeaderLabels(
            ["#", "Cell", "Pointer", "Address", "Tris", "Quads", "Bytes"])
        _prepare_table(self.groups_table)
        self.groups_table.setSortingEnabled(True)
        # Qt's default indicator is descending, and re-enabling sorting
        # after each load would then open every file bottom-up; set it
        # once here and the table follows whatever the user picks after.
        self.groups_table.horizontalHeader().setSortIndicator(
            0, Qt.SortOrder.AscendingOrder)
        self.groups_table.itemSelectionChanged.connect(self._on_group_row_changed)

        self.grid_title = panel_title.make_panel_title("Drawmap grid")
        self.footprint_title = panel_title.make_panel_title("Level from above")
        self.info_label = QLabel("No DRWA loaded")

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
        left_layout.addWidget(panel_title.make_panel_title("Groups"))
        left_layout.addWidget(self.groups_table)
        left.setMaximumWidth(460)

        right = QSplitter(Qt.Orientation.Vertical, self)
        right.addWidget(_titled(self, self.grid_title, self.grid_scroll))
        right.addWidget(_titled(self, self.footprint_title, self.footprint_scroll))
        right.setStretchFactor(0, 0)
        right.setStretchFactor(1, 1)
        right.setSizes([320, 520])

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 900])
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
            button.setToolTip("Zooms the grid. Ctrl+wheel zooms whichever "
                              "view is under the pointer.")
        zoom_out.clicked.connect(lambda: self._zoom_by(-1))
        zoom_in.clicked.connect(lambda: self._zoom_by(1))
        zoom_reset.clicked.connect(lambda: self._set_zoom(1))
        self.zoom_label = QLabel("8x")

        self.mode_combo = QComboBox()
        for key, label, tip in COLOR_MODES:
            self.mode_combo.addItem(label, key)
            self.mode_combo.setItemData(self.mode_combo.count() - 1, tip,
                                        Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setToolTip(
            "What the colours mean. Both views always share them, so a "
            "cell and the polygons it draws match.")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.grid_check = QCheckBox("Cell grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self._on_grid_toggled)

        export_grid = QPushButton("Export grid")
        export_grid.setToolTip("Save the drawmap as a PNG, one pixel per cell")
        export_grid.clicked.connect(self.export_grid_png)

        export_map = QPushButton("Export map")
        export_map.setToolTip("Save the top-down view of the level as a PNG")
        export_map.clicked.connect(self.export_footprint_png)

        for w in (zoom_out, zoom_in, zoom_reset, self.zoom_label,
                  QLabel("  Colour:"), self.mode_combo, self.grid_check,
                  export_grid, export_map):
            bar.addWidget(w)
        return bar

    # --- loading ---

    def load_drwa_data(self, dat_file_path, dat_start, offset, size=None,
                       chunk_index=None):
        """Parse and draw the drawmap of one MDAT entry, along with the
        geometry its pointers reach."""
        try:
            self.drwa = load_drwa(dat_file_path, dat_start, offset, size)
        except (DRWAError, OSError, struct.error) as e:
            self._clear(f"Not readable as a DRWA: {e}")
            return False

        self._source = (dat_file_path, dat_start, offset, chunk_index)
        self._file_key = (dat_file_path, dat_start, offset)
        self._selected = None
        self._populate_details()
        self._populate_groups()
        self._redraw()
        self._update_info()
        return True

    def _clear(self, message):
        self.drwa = None
        self.footprint = None
        self._grid_image = None
        self._selected = None
        self.details_table.setRowCount(0)
        self.groups_table.setRowCount(0)
        self.grid_canvas.clear()
        self.footprint_canvas.set_footprint(None)
        self.info_label.setText(message)

    # --- drawing ---

    def _redraw(self):
        if not self.drwa:
            return
        self._colors = group_colors(self.drwa, self.mode_combo.currentData())
        self._grid_image = render_grid(self.drwa, self._colors)
        self.grid_canvas.set_image(_to_qimage(self._grid_image))
        self._fit("grid", self._grid_image.size, self.grid_scroll,
                  self.grid_canvas, GRID_MAX_FIT_ZOOM)

        self.footprint = render_footprint(self.drwa, self._colors)
        self.footprint_canvas.set_footprint(self.footprint)
        if self.footprint:
            self._fit("footprint", self.footprint.size, self.footprint_scroll,
                      self.footprint_canvas, FOOTPRINT_MAX_FIT_ZOOM)
        self._apply_selection()

    def _fit(self, view, image_size, scroll, canvas, max_zoom):
        """Fit a view the first time this file reaches it, then leave
        the zoom where the user put it - changing colour mode redraws
        both views, and shouldn't move either."""
        if self._fitted.get(view) == self._file_key:
            return
        self._fitted[view] = self._file_key
        canvas.set_zoom(fit_zoom(image_size, scroll.viewport().size(), max_zoom))
        if view == "grid":
            self.zoom_label.setText(zoom_label(canvas.zoom))

    # --- tables ---

    def _populate_details(self):
        drwa = self.drwa
        _path, dat_start, offset, chunk = self._source
        occupied = len(drwa.groups)
        rows = [
            ("Grid", f"{drwa.width} x {drwa.height} cells"),
            ("Cells used", f"{occupied} of {drwa.cell_count} "
                           f"({occupied * 100 // max(drwa.cell_count, 1)}%)"),
            ("DRWA at", f"0x{drwa.address:08X}"),
            ("Grid bytes", f"0x{drwa.map_size:X}"),
            ("First group at", f"+0x{drwa.data_start:X}"
                               + (f" ({drwa.padding} byte pad)" if drwa.padding else "")),
            ("Geometry", f"{drwa.tri_count} tris, {drwa.quad_count} quads"),
            ("MDAT extent", f"0x{drwa.extent:X} bytes"),
            ("IDX says", f"0x{drwa.declared_size:X} bytes"
                         + ("" if drwa.slack == 0 else f", {drwa.slack} spare")),
            ("Groups back to back", "yes" if drwa.contiguous else "no"),
        ]
        if chunk is not None:
            rows.insert(0, ("Area", f"AREA_{chunk:02X}"))
        if drwa.strays:
            rows.append(("Not pointers", ", ".join(
                f"cell {c} = 0x{v:04X}" for c, v in drwa.strays[:4])))
        measured = drwa.cell_size()
        if measured:
            dx, dz, fit = measured
            rows.append(("Cell covers", f"{dx:.0f} x {dz:.0f} world units "
                                        f"(fit {fit:.2f})"))
        bounds = drwa.bounds
        if bounds:
            rows.extend([
                ("World X", f"{bounds[0]:.0f} .. {bounds[1]:.0f}"),
                ("World Y", f"{bounds[2]:.0f} .. {bounds[3]:.0f}"),
                ("World Z", f"{bounds[4]:.0f} .. {bounds[5]:.0f}"),
            ])
        self.details_table.setRowCount(len(rows))
        for row, (fieldname, value) in enumerate(rows):
            self.details_table.setItem(row, 0, QTableWidgetItem(fieldname))
            self.details_table.setItem(row, 1, QTableWidgetItem(value))

    def _populate_groups(self):
        table = self.groups_table
        table.blockSignals(True)
        table.setSortingEnabled(False)
        groups = self.drwa.groups
        table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            index_item = QTableWidgetItem()
            index_item.setData(Qt.ItemDataRole.DisplayRole, group.index)
            index_item.setData(Qt.ItemDataRole.UserRole, group.index)
            table.setItem(row, 0, index_item)
            table.setItem(row, 1, QTableWidgetItem(f"{group.col},{group.row}"))
            # Fixed-width hex, so sorting these columns as text still
            # puts them in numeric order.
            table.setItem(row, 2, QTableWidgetItem(f"{group.pointer:04X}"))
            table.setItem(row, 3, QTableWidgetItem(
                f"{self.drwa.address + group.offset:08X}"))
            for column, value in ((4, group.tris), (5, group.quads),
                                  (6, group.size)):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value)
                table.setItem(row, column, item)
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()
        table.blockSignals(False)

    def _on_group_row_changed(self):
        rows = self.groups_table.selectionModel().selectedRows()
        if not rows or not self.drwa:
            return
        index = self.groups_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if index is None or (self._selected and self._selected.index == index):
            return
        self._select(self.drwa.groups[index], from_table=True)

    # --- interaction ---

    def _on_grid_clicked(self, x, y):
        if not self.drwa:
            return
        group = self.drwa.group_at(x, y)
        if group is None:
            self._select(None)
            self._update_info(f"cell ({x},{y}) is empty - 0xFFFF, no geometry")
            return
        self._select(group)

    def _on_footprint_clicked(self, x, y):
        if not self.drwa or self.footprint is None:
            return
        index = self.footprint.group_at(x, y)
        if index is None:
            self._select(None)
            self._update_info("nothing drawn there")
            return
        self._select(self.drwa.groups[index])

    def _select(self, group, from_table=False):
        self._selected = group
        self._apply_selection(scroll_table=not from_table)
        if group is None:
            self._update_info()
            return
        bounds = group.bounds
        where = ""
        if bounds:
            where = (f", world X {bounds[0]}..{bounds[1]} "
                     f"Y {bounds[2]}..{bounds[3]} Z {bounds[4]}..{bounds[5]}")
        self._update_info(
            f"cell ({group.col},{group.row}) = 0x{group.pointer:04X} -> "
            f"0x{self.drwa.address + group.offset:08X}: group {group.index}, "
            f"{group.tris} tris, {group.quads} quads, 0x{group.size:X} bytes{where}")

    def _apply_selection(self, scroll_table=True):
        group = self._selected
        self.grid_canvas.set_selected((group.col, group.row) if group else None)
        self.footprint_canvas.set_selected(group)
        if group is None or not scroll_table:
            return
        table = self.groups_table
        for row in range(table.rowCount()):
            if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == group.index:
                table.blockSignals(True)
                table.selectRow(row)
                table.scrollToItem(table.item(row, 0))
                table.blockSignals(False)
                return

    def _update_info(self, extra=None):
        if not self.drwa:
            return
        drwa = self.drwa
        _path, _dat_start, _offset, chunk = self._source
        parts = [
            f"{drwa.width}x{drwa.height} cells, {len(drwa.groups)} pointing at "
            f"geometry",
            f"{drwa.tri_count} tris, {drwa.quad_count} quads",
            f"MDAT 0x{drwa.extent:X} bytes @ 0x{drwa.address:08X}",
        ]
        if chunk is not None:
            parts.append(f"AREA_{chunk:02X}")
        if not drwa.contiguous:
            parts.append("groups are NOT back to back - unusual")
        if drwa.strays:
            parts.append(f"{len(drwa.strays)} cell(s) hold something that "
                         "isn't a pointer")
        if drwa.slack:
            parts.append(f"{drwa.slack} bytes of the entry past the last group")
        if extra:
            parts.insert(0, extra)
        self.info_label.setText("  |  ".join(parts))

    # --- toolbar ---

    def _on_mode_changed(self):
        if self.drwa:
            self._redraw()

    def _on_grid_toggled(self, checked):
        self.grid_canvas.show_grid = checked
        self.grid_canvas.update()

    def _zoom_by(self, direction):
        self.grid_canvas.zoom_by(direction)
        self.zoom_label.setText(zoom_label(self.grid_canvas.zoom))

    def _set_zoom(self, zoom):
        self.grid_canvas.set_zoom(zoom)
        self.zoom_label.setText(zoom_label(self.grid_canvas.zoom))

    def export_grid_png(self):
        self._export(self._grid_image, "grid")

    def export_footprint_png(self):
        self._export(self.footprint.image if self.footprint else None, "map")

    def _export(self, image, suffix):
        if image is None:
            return
        _path, dat_start, offset, chunk = self._source
        area = f"AREA_{chunk:02X}_" if chunk is not None else ""
        default = f"{area}DRWA_{dat_start + offset:08X}_{suffix}.png"
        path, _unused = QFileDialog.getSaveFileName(
            self, "Save as PNG", default, "PNG Image (*.png)")
        if not path:
            return
        try:
            image.save(path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed",
                                 f"Couldn't write {path}:\n\n{e}")
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
    layout.addWidget(title)
    layout.addWidget(widget)
    return panel


def _prepare_table(table):
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)


def _to_qimage(pil_image):
    """PIL RGBA -> QImage, copied so it survives the PIL image (and the
    buffer ImageQt wraps) being garbage collected."""
    return ImageQt(pil_image).copy()

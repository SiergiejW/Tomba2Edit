"""DRWB viewer - the second drawmap, of which the disc holds four.

A DRWB is a 52x52 grid of flag bytes that lines up with a level's
geometry but points at nothing (see gui/drwb/drwb_parser.py). So this
viewer is built around comparing it with the level rather than
following it: the area's MDATs are loaded, the one this DRWB actually
covers is picked by measuring, and its occupied cells are shown
underneath the flags. Cells carrying flags that the level has no
geometry in are dimmed; geometry with no flag over it would come up
red, and doesn't on any of the four.

What the eight flags switch is not decoded. The plane strip along the
bottom shows all eight at once, and the colour modes cut the byte up
the ways that look meaningful - whole byte, either nibble, one bit -
so the question stays open to look at rather than being answered here.
"""
import os
import struct

from PIL.ImageQt import ImageQt
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui import panel_title
from gui.drwa.drwa_parser import DRWAError, load_drwa
from gui.drwb.drwb_parser import (
    BITS, DRWBError, coverage, load_drwb, match_mdat)
from gui.drwb.drwb_render import COLOR_MODES, render_grid, render_planes
from gui.mdat.mdat import area_mdat_entries
from gui.pixel_canvas import PixelCanvas, fit_zoom, zoom_label

GRID_MAX_FIT_ZOOM = 16
PLANES_MAX_FIT_ZOOM = 4

# Widget pixels per cell below which the cell grid stops being a grid
# and becomes a fill.
GRID_MIN_STEP = 6

GRID_COLOR = QColor(255, 255, 255, 45)
SELECTION_COLOR = QColor(255, 255, 255, 235)
SELECTION_SHADOW = QColor(0, 0, 0, 200)


class CellCanvas(PixelCanvas):
    """One pixel per cell, with the selected cell outlined."""

    def __init__(self, parent=None):
        super().__init__(zoom=8, parent=parent)
        self.show_grid = True
        self.selected = None    # (col, row) in whatever the current view is

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
                painter.drawLine(col * step, 0, col * step,
                                 self.image.height() * step)
            for row in range(max(0, area.top() // step),
                             min(self.image.height(), area.bottom() // step + 1) + 1):
                painter.drawLine(0, row * step, self.image.width() * step,
                                 row * step)
        if self.selected is not None:
            col, row = self.selected
            rect = QRect(col * step, row * step, step, step).adjusted(-1, -1, 1, 1)
            painter.setPen(QPen(SELECTION_SHADOW, 3))
            painter.drawRect(rect)
            painter.setPen(QPen(SELECTION_COLOR, 1))
            painter.drawRect(rect)


class DRWBViewer(QWidget):
    """Flag-map browser: what's in the file on the left, the map and
    its eight planes on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drwb = None
        self.drwa = None            # the level this map covers, if found
        self.mdat_label = None
        self.occupied = None        # its occupied cells, as (x, z)
        self._grid_image = None
        self._planes_image = None
        self._source = ("", 0, 0, None)
        self._file_key = None
        self._fitted = {}
        self._selected = None       # (x, z), always level-aligned

        self.grid_canvas = CellCanvas()
        self.grid_canvas.clicked.connect(self._on_grid_clicked)
        self.grid_scroll = _scroll_for(self.grid_canvas)

        self.planes_canvas = PixelCanvas(zoom=2)
        self.planes_scroll = _scroll_for(self.planes_canvas)

        self.details_table = QTableWidget(0, 2)
        self.details_table.setHorizontalHeaderLabels(["Field", "Value"])
        _prepare_table(self.details_table)

        self.values_table = QTableWidget(0, 4)
        self.values_table.setHorizontalHeaderLabels(
            ["Byte", "Bits", "Cells", "With geometry"])
        _prepare_table(self.values_table)

        self.grid_title = panel_title.make_panel_title("Flag map")
        self.info_label = panel_title.make_info_label("No DRWB loaded")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_toolbar())

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(panel_title.make_panel_title("File"))
        left_layout.addWidget(self.details_table)
        left_layout.addWidget(panel_title.make_panel_title("Byte values"))
        left_layout.addWidget(self.values_table)
        left.setMaximumWidth(460)

        right = QSplitter(Qt.Orientation.Vertical, self)
        right.addWidget(_titled(self, self.grid_title, self.grid_scroll))
        right.addWidget(_titled(
            self, panel_title.make_panel_title(
                "The eight flags on their own, bit 0 first"),
            self.planes_scroll))
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 0)
        right.setSizes([560, 220])

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
        zoom_out.clicked.connect(lambda: self._zoom_by(-1))
        zoom_in.clicked.connect(lambda: self._zoom_by(1))
        zoom_reset.clicked.connect(lambda: self._set_zoom(1))
        self.zoom_label = QLabel("8x")

        self.mode_combo = QComboBox()
        for key, label, tip in COLOR_MODES:
            self.mode_combo.addItem(label, key)
            self.mode_combo.setItemData(self.mode_combo.count() - 1, tip,
                                        Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.bit_spin = QSpinBox()
        self.bit_spin.setRange(0, BITS - 1)
        self.bit_spin.setPrefix("bit ")
        self.bit_spin.setToolTip("Which flag the \"One flag\" mode draws")
        self.bit_spin.valueChanged.connect(self._on_mode_changed)

        self.geometry_check = QCheckBox("Compare with level")
        self.geometry_check.setChecked(True)
        self.geometry_check.setToolTip(
            "Dim the cells that carry flags but hold no geometry, and mark "
            "in red any geometry with no flag over it - which nothing on "
            "the disc has")
        self.geometry_check.toggled.connect(self._on_mode_changed)

        self.stored_check = QCheckBox("Stored layout")
        self.stored_check.setToolTip(
            "Draw the grid the way the file stores it - transposed against "
            "the level - for reading alongside a hex editor. The comparison "
            "with the level is off in this view.")
        self.stored_check.toggled.connect(self._on_mode_changed)

        self.grid_check = QCheckBox("Cell grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self._on_grid_toggled)

        export_btn = QPushButton("Export PNG")
        export_btn.clicked.connect(self.export_png)

        for w in (zoom_out, zoom_in, zoom_reset, self.zoom_label,
                  QLabel("  Colour:"), self.mode_combo, self.bit_spin,
                  self.geometry_check, self.stored_check, self.grid_check,
                  export_btn):
            bar.addWidget(w)
        return bar

    # --- loading ---

    def load_drwb_data(self, dat_file_path, dat_start, offset, size,
                       chunk_index=None, idx_path=None):
        """Parse and draw one DRWB blob.

        `idx_path` and `chunk_index` are what let the area's MDATs be
        loaded and the matching one found; without them the map still
        draws, just with nothing to compare it against."""
        try:
            self.drwb = load_drwb(dat_file_path, dat_start, offset, size)
        except (DRWBError, OSError, struct.error) as e:
            self._clear(f"Not readable as a DRWB: {e}")
            return False

        self._source = (dat_file_path, dat_start, offset, chunk_index)
        self._file_key = (dat_file_path, dat_start, offset)
        self._selected = None
        self._match_level(dat_file_path, idx_path, chunk_index)
        self._populate_details()
        self._populate_values()
        self._redraw()
        self._update_info()
        return True

    def _match_level(self, dat_file_path, idx_path, chunk_index):
        """Find which of this area's MDATs the map covers. AREA_1B's
        goes with that area's second one, so this measures rather than
        taking the first."""
        self.drwa = None
        self.mdat_label = None
        self.occupied = None
        if not idx_path or chunk_index is None:
            return
        candidates = []
        try:
            for file_index, dat_start, offset, size in area_mdat_entries(
                    idx_path, dat_file_path, chunk_index):
                try:
                    candidates.append((f"file {file_index:02X}",
                                       load_drwa(dat_file_path, dat_start,
                                                 offset, size)))
                except (DRWAError, OSError, struct.error):
                    continue
        except (OSError, struct.error) as e:
            print(f"Could not scan AREA_{chunk_index:02X} for MDATs: {e}")
            return

        best = match_mdat(self.drwb, candidates)
        if best is None:
            return
        self.mdat_label, self.drwa, _covered, _total = best
        self.occupied = {(g.col, g.row) for g in self.drwa.groups}

    def _clear(self, message):
        self.drwb = None
        self.drwa = None
        self.occupied = None
        self._grid_image = None
        self._planes_image = None
        self._selected = None
        self.details_table.setRowCount(0)
        self.values_table.setRowCount(0)
        self.grid_canvas.clear()
        self.planes_canvas.clear()
        panel_title.set_info(self.info_label, message)

    # --- drawing ---

    def _comparing(self):
        return (self.geometry_check.isChecked() and self.occupied is not None
                and not self.stored_check.isChecked())

    def _redraw(self):
        if not self.drwb:
            return
        stored = self.stored_check.isChecked()
        self._grid_image = render_grid(
            self.drwb, mode=self.mode_combo.currentData(),
            bit=self.bit_spin.value(), stored=stored,
            occupied=self.occupied if self._comparing() else None)
        self.grid_canvas.set_image(_to_qimage(self._grid_image))
        self._fit("grid", self._grid_image.size, self.grid_scroll,
                  self.grid_canvas, GRID_MAX_FIT_ZOOM)

        self._planes_image = render_planes(self.drwb)
        self.planes_canvas.set_image(_to_qimage(self._planes_image))
        self._fit("planes", self._planes_image.size, self.planes_scroll,
                  self.planes_canvas, PLANES_MAX_FIT_ZOOM)

        self.grid_title.setText(
            "Flag map, as stored (transposed against the level)" if stored
            else "Flag map, laid over the level - X across, Z down")
        self._apply_selection()

    def _fit(self, view, image_size, scroll, canvas, max_zoom):
        if self._fitted.get(view) == self._file_key:
            return
        self._fitted[view] = self._file_key
        canvas.set_zoom(fit_zoom(image_size, scroll.viewport().size(), max_zoom))
        if view == "grid":
            self.zoom_label.setText(zoom_label(canvas.zoom))

    # --- tables ---

    def _populate_details(self):
        drwb = self.drwb
        _path, _dat_start, _offset, chunk = self._source
        shared, low_only, high_only = drwb.nibble_overlap()
        bounds = drwb.bounds()
        rows = [
            ("Grid", f"{drwb.side} x {drwb.side} cells"),
            ("Blob", f"0x{drwb.size:X} bytes @ 0x{drwb.address:08X}"),
            ("Cells set", f"{drwb.set_count} of {drwb.side * drwb.side}"),
            ("Distinct bytes", str(len(drwb.values))),
            ("Flags used", ", ".join(str(b) for b in drwb.bits_used) or "-"),
            ("Low vs high nibble",
             f"{shared} both, {low_only} low only, {high_only} high only"),
        ]
        if chunk is not None:
            rows.insert(0, ("Area", f"AREA_{chunk:02X}"))
        if bounds:
            x0, x1, z0, z1 = bounds
            rows.append(("Set cells span", f"X {x0}..{x1}, Z {z0}..{z1}"))
        if self.drwa is not None:
            covered, total, extra = coverage(self.drwb, self.drwa)
            rows.extend([
                ("Level", f"MDAT {self.mdat_label}, "
                          f"{self.drwa.width} x {self.drwa.height} cells"),
                ("Geometry covered", f"{covered} of {total}"
                                     + (" - all of it" if covered == total else "")),
                ("Flagged, no geometry", str(extra)),
            ])
        else:
            rows.append(("Level", "no MDAT to compare against"))
        for label, count in zip(range(BITS), drwb.bit_counts):
            if count:
                rows.append((f"Flag {label}", f"{count} cells"))

        self.details_table.setRowCount(len(rows))
        for row, (fieldname, value) in enumerate(rows):
            self.details_table.setItem(row, 0, QTableWidgetItem(fieldname))
            self.details_table.setItem(row, 1, QTableWidgetItem(value))

    def _populate_values(self):
        table = self.values_table
        table.blockSignals(True)
        values = sorted(self.drwb.values.items(), key=lambda kv: -kv[1])
        table.setRowCount(len(values))
        for row, (value, count) in enumerate(values):
            table.setItem(row, 0, QTableWidgetItem(f"0x{value:02X}"))
            table.setItem(row, 1, QTableWidgetItem(f"{value:08b}"))
            count_item = QTableWidgetItem()
            count_item.setData(Qt.ItemDataRole.DisplayRole, count)
            table.setItem(row, 2, count_item)
            with_geometry = "-"
            if self.occupied is not None:
                hits = sum(1 for x, z in self.occupied
                           if self.drwb.value_at(x, z) == value)
                with_geometry = str(hits)
            table.setItem(row, 3, QTableWidgetItem(with_geometry))
        table.resizeColumnsToContents()
        table.blockSignals(False)

    # --- interaction ---

    def _on_grid_clicked(self, a, b):
        if not self.drwb:
            return
        stored = self.stored_check.isChecked()
        byte = self.drwb.raw_at(a, b) if stored else self.drwb.value_at(a, b)
        if byte is None:
            return
        self._selected = (a, b)
        self._apply_selection()

        where = (f"stored cell (col {a}, row {b})" if stored
                 else f"level cell (X {a}, Z {b})")
        bits = ", ".join(str(n) for n in range(BITS) if byte >> n & 1) or "none"
        parts = [f"{where} = 0x{byte:02X} ({byte:08b}), flags {bits}"]
        if self.occupied is not None and not stored:
            parts.append("level has geometry here" if (a, b) in self.occupied
                         else "no geometry here")
        self._update_info("  ".join(parts))

    def _apply_selection(self):
        self.grid_canvas.set_selected(self._selected)

    def _update_info(self, extra=None):
        if not self.drwb:
            return
        drwb = self.drwb
        _path, _dat_start, _offset, chunk = self._source
        parts = [
            f"{drwb.side}x{drwb.side} cells, {drwb.set_count} set",
            f"{len(drwb.values)} distinct bytes",
            f"flags {', '.join(str(b) for b in drwb.bits_used) or 'none'}",
        ]
        if chunk is not None:
            parts.append(f"AREA_{chunk:02X}")
        if self.drwa is not None:
            covered, total, extra_cells = coverage(drwb, self.drwa)
            parts.append(f"covers {covered}/{total} of MDAT {self.mdat_label}'s "
                         f"cells, {extra_cells} more with no geometry")
        else:
            parts.append("no MDAT matched")
        parts.append("what the flags switch is undecoded")
        if extra:
            parts.insert(0, extra)
        panel_title.set_info(self.info_label, "  |  ".join(parts))

    # --- toolbar ---

    def _on_mode_changed(self):
        if self.drwb:
            self._redraw()
            self._update_info()

    def _on_grid_toggled(self, checked):
        self.grid_canvas.show_grid = checked
        self.grid_canvas.update()

    def _zoom_by(self, direction):
        self.grid_canvas.zoom_by(direction)
        self.zoom_label.setText(zoom_label(self.grid_canvas.zoom))

    def _set_zoom(self, zoom):
        self.grid_canvas.set_zoom(zoom)
        self.zoom_label.setText(zoom_label(self.grid_canvas.zoom))

    def export_png(self):
        if self._grid_image is None:
            return
        _path, dat_start, offset, chunk = self._source
        area = f"AREA_{chunk:02X}_" if chunk is not None else ""
        default = f"{area}DRWB_{dat_start + offset:08X}.png"
        path, _unused = QFileDialog.getSaveFileName(
            self, "Save as PNG", default, "PNG Image (*.png)")
        if not path:
            return
        try:
            self._grid_image.save(path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed",
                                 f"Couldn't write {path}:\n\n{e}")
            return
        panel_title.set_info(self.info_label, f"Wrote {os.path.basename(path)}")


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

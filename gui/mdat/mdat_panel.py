"""The drawmap, its entries and their polygons, beside the 3D view.

An MDAT is a grid over the level (the DRWA - see gui/drwa/drwa_parser.py)
whose cells point at entries, and each entry is a header giving a
triangle and a quad count followed by that many records. That is the
shape this shows: one row per entry, its polygons under it, and for
whichever is selected a yellow outline in the 3D view.

Selecting a polygon also shows what it is drawn with - its palette as a
16-colour strip, and its texture page with the polygon's own UVs marked
on it, which is the quickest way to see which corner of which page a
face is actually taking.
"""
import numpy as np
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHBoxLayout, QLabel, QScrollArea,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from functions import psx_vram, uv_anim
from gui.pixel_canvas import PixelCanvas, fit_zoom

COLUMNS = ["Item", "Cell", "Tris", "Quads", "Size", "Page", "CLUT", "Address"]

# What a row's UserRole carries: ("entry", index) or ("polygon", index).
ROLE = Qt.ItemDataRole.UserRole

UV_OUTLINE = QColor(255, 235, 40)
UV_SHADOW = QColor(0, 0, 0, 160)

PAGE = psx_vram.UV_WRAP

# PSX draw modes, by the type byte a record carries. Only the ones the
# disc actually uses are named; anything else is shown as its number.
BLEND_NAMES = {0: "half", 1: "add", 2: "subtract", 3: "quarter"}


class UVCanvas(PixelCanvas):
    """A texture page with one polygon's UVs drawn over it."""

    def __init__(self, parent=None):
        super().__init__(zoom=1, parent=parent)
        self.ring = ()

    def set_ring(self, ring):
        self.ring = tuple(ring)
        self.update()

    def paint_overlays(self, painter, area):
        if len(self.ring) < 2:
            return
        points = [(self.scaled(u) + self.scaled(1) // 2,
                   self.scaled(v) + self.scaled(1) // 2) for u, v in self.ring]
        for width, color in ((3, UV_SHADOW), (1, UV_OUTLINE)):
            painter.setPen(QPen(color, width))
            for i, (x, y) in enumerate(points):
                nx, ny = points[(i + 1) % len(points)]
                painter.drawLine(x, y, nx, ny)


class MDATPanel(QWidget):
    """Entry list, selection details and the 3D view, side by side."""

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self._syncing = False

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemExpanded.connect(self._on_expanded)

        self.summary = QLabel("No MDAT loaded", self)
        self.summary.setWordWrap(True)

        self.details = QLabel("Ctrl+click a polygon in the view, or pick one "
                              "from the list.", self)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        self.clut_strip = QLabel(self)
        self.clut_strip.setFixedHeight(18)

        self.uv_canvas = UVCanvas(self)
        page_scroll = QScrollArea(self)
        page_scroll.setWidgetResizable(False)
        page_scroll.setWidget(self.uv_canvas)
        page_scroll.setMinimumHeight(180)
        self._page_scroll = page_scroll

        texture = QGroupBox("Texture", self)
        texture_layout = QVBoxLayout(texture)
        texture_layout.setContentsMargins(6, 6, 6, 6)
        texture_layout.addWidget(self.clut_strip)
        texture_layout.addWidget(page_scroll)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.summary)
        left_layout.addWidget(self.tree, 1)
        left_layout.addWidget(self.details)
        left_layout.addWidget(texture)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 780])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        viewer.selection_changed.connect(self._on_viewer_selection)

    # --- filling ------------------------------------------------------

    def populate(self):
        """Rebuild from whatever the viewer has loaded."""
        self._syncing = True
        self.tree.clear()
        model = self.viewer.model_data or {}
        entries = model.get("entries") or ()
        polygons = model.get("polygons") or ()
        rows, columns = model.get("drawmap") or (0, 0)
        self.summary.setText(
            f"Drawmap {columns} x {rows} cells, {rows * columns} in all, "
            f"{len(entries)} of them pointing at an entry.\n"
            f"{model.get('tri_count', 0)} triangles and "
            f"{model.get('quad_count', 0)} quads - {len(polygons)} polygons, "
            f"{len(model.get('faces') or ())} drawn triangles.")

        for entry in entries:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, f"Entry {entry['index']}")
            item.setText(1, f"{entry['cell']}  ({entry['col']}, {entry['row']})")
            item.setText(2, str(entry["tris"]))
            item.setText(3, str(entry["quads"]))
            item.setText(4, str(entry["size"]))
            span = polygons[entry["first_polygon"]:
                            entry["first_polygon"] + entry["polygon_count"]]
            item.setText(5, _distinct(p["page"] for p in span))
            item.setText(6, _distinct(f"0x{p['clut']:06X}" for p in span))
            item.setText(7, f"0x{entry['address']:X}")
            item.setData(0, ROLE, ("entry", entry["index"]))
            if entry["polygon_count"]:
                item.setChildIndicatorPolicy(
                    QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        for column in range(len(COLUMNS)):
            self.tree.resizeColumnToContents(column)
        self._syncing = False
        self._show_details(None, None)

    def _on_expanded(self, item):
        """Fill an entry's polygons the first time it is opened. A level
        has thousands of them and only a handful are ever looked at."""
        if item.childCount() or item.data(0, ROLE) is None:
            return
        kind, index = item.data(0, ROLE)
        if kind != "entry":
            return
        model = self.viewer.model_data or {}
        entry = (model.get("entries") or ())[index]
        polygons = model.get("polygons") or ()
        self._syncing = True
        for i in range(entry["first_polygon"],
                       entry["first_polygon"] + entry["polygon_count"]):
            polygon = polygons[i]
            child = QTreeWidgetItem(item)
            child.setText(0, f"{polygon['kind']} {polygon['slot']}")
            child.setText(4, "36" if polygon["kind"] == "tri" else "44")
            child.setText(5, str(polygon["page"]))
            child.setText(6, f"0x{polygon['clut']:06X}")
            child.setText(7, f"0x{polygon['address']:X}")
            child.setData(0, ROLE, ("polygon", i))
        self._syncing = False

    # --- selection ----------------------------------------------------

    def _on_selection(self):
        if self._syncing:
            return
        items = self.tree.selectedItems()
        if not items:
            self.viewer.select()
            return
        data = items[0].data(0, ROLE)
        if data is None:
            return
        kind, index = data
        if kind == "entry":
            self.viewer.select(entry=index)
        else:
            self.viewer.select(polygon=index)

    def _on_viewer_selection(self, entry, polygon):
        """Follow a pick made in the 3D view."""
        self._show_details(entry, polygon)
        if self._syncing:
            return
        self._syncing = True
        try:
            item = self._entry_item(entry)
            if item is not None and polygon is not None:
                item.setExpanded(True)
                self._on_expanded(item)
                for row in range(item.childCount()):
                    child = item.child(row)
                    if child.data(0, ROLE) == ("polygon", polygon):
                        item = child
                        break
            self.tree.clearSelection()
            if item is not None:
                item.setSelected(True)
                self.tree.scrollToItem(item)
        finally:
            self._syncing = False

    def _entry_item(self, entry):
        if entry is None:
            return None
        for row in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(row)
            if item.data(0, ROLE) == ("entry", entry):
                return item
        return None

    # --- what the selection is drawn with ------------------------------

    def _show_details(self, entry_index, polygon_index):
        model = self.viewer.model_data or {}
        entries = model.get("entries") or ()
        polygons = model.get("polygons") or ()
        if polygon_index is not None and polygon_index < len(polygons):
            polygon = polygons[polygon_index]
            entry = entries[polygon["entry"]]
            x, y = psx_vram.clut_address_xy(polygon["clut"])
            self.details.setText(
                f"<b>{polygon['kind']} {polygon['slot']}</b> of entry "
                f"{entry['index']} (cell {entry['cell']}, "
                f"col {entry['col']}, row {entry['row']})<br>"
                f"record at <b>0x{polygon['address']:X}</b>, "
                f"draw type {polygon['type']} "
                f"({'semi-transparent' if polygon['transparent'] else 'opaque'}, "
                f"{BLEND_NAMES.get(polygon['blend'], polygon['blend'])})<br>"
                f"texture page <b>{polygon['page']}</b>, CLUT "
                f"<b>0x{polygon['clut']:06X}</b> at ({x}, {y})<br>"
                f"UVs " + ", ".join(f"({u},{v})" for u, v in polygon["texels"]))
            self._show_texture(polygon)
            return
        if entry_index is not None and entry_index < len(entries):
            entry = entries[entry_index]
            self.details.setText(
                f"<b>Entry {entry['index']}</b> - cell {entry['cell']} "
                f"(col {entry['col']}, row {entry['row']}), pointer "
                f"{entry['pointer']} -> +0x{entry['offset']:X}<br>"
                f"{entry['tris']} triangles and {entry['quads']} quads, "
                f"{entry['size']} bytes at <b>0x{entry['address']:X}</b>")
        else:
            self.details.setText("Ctrl+click a polygon in the view, or pick "
                                 "one from the list.")
        self._show_texture(None)

    def _show_texture(self, polygon):
        vram = getattr(self.viewer, "vram_raw_bytes", None)
        if polygon is None or not vram or len(vram) < psx_vram.VRAM_SIZE:
            self.clut_strip.clear()
            self.uv_canvas.clear()
            self.uv_canvas.set_ring(())
            return
        palette = psx_vram.read_palette(vram, polygon["clut"], 16,
                                        transparent_zero=False)
        self.clut_strip.setPixmap(_swatch(palette, self.clut_strip.height()))

        texels = uv_anim.page_texels(vram, polygon["page"])
        lut = np.array([c[:3] for c in palette], dtype=np.uint8)
        rgb = lut[texels]
        # QImage doesn't own the buffer it is handed, so copy it.
        self.uv_canvas.set_image(QImage(rgb.tobytes(), PAGE, PAGE, PAGE * 3,
                                        QImage.Format.Format_RGB888).copy())
        self.uv_canvas.set_zoom(fit_zoom((PAGE, PAGE),
                                         self._page_scroll.viewport().size()))
        self.uv_canvas.set_ring(polygon["texels"])


def _distinct(values):
    """"9" for one, "3 x" for several - an entry usually draws with one
    page and one palette, and it is worth seeing when it does not."""
    seen = sorted({str(v) for v in values})
    if not seen:
        return ""
    return seen[0] if len(seen) == 1 else f"{len(seen)} x"


def _swatch(palette, height):
    """The 16 colours as one strip."""
    cell = 16
    pixmap = QPixmap(cell * 16, max(height, 8))
    pixmap.fill(QColor(0, 0, 0))
    painter = QPainter(pixmap)
    for i, color in enumerate(palette):
        painter.fillRect(QRect(i * cell, 0, cell, pixmap.height()),
                         QColor(color[0], color[1], color[2]))
    painter.end()
    return pixmap

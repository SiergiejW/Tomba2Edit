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
from math import gcd

import numpy as np
from PIL import Image
from PyQt6.QtCore import Qt, QRect, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from functions import psx_vram, uv_anim
from gui.clut_animation import TICK_HZ, UV_TICKS_PER_FRAME
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

# How often the preview looks at the viewer's tick to see whether the
# animation has moved on. Polled rather than driven, so the preview
# follows the Animate button instead of running a clock of its own.
PREVIEW_POLL_MS = 50

# A polygon's patch of texture is often only a few dozen texels across,
# so a GIF of it is blown up to be worth looking at.
GIF_SCALE = 4

# However long an animation's loop is, a GIF of it stops here.
MAX_GIF_FRAMES = 240


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
        # Which polygon the texture preview is showing, and which frame
        # of its animation, so a poll only redraws when it has moved.
        self._preview = None
        self._preview_frames = None
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._poll_animation)

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
        self.details.setMinimumHeight(78)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
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

        self.png_button = QPushButton("Save PNG...", self)
        self.png_button.setToolTip(
            "Write this polygon's texture page out as it is coloured here "
            "- 256x256, with its palette applied.")
        self.png_button.clicked.connect(self._save_png)
        self.gif_button = QPushButton("Save GIF...", self)
        self.gif_button.setToolTip(
            "Write the polygon's own patch of texture out as an animated "
            "GIF, one frame per step of whatever animates it - the palette "
            "being swapped, the UVs stepping across the page, or both.")
        self.gif_button.clicked.connect(self._save_gif)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.png_button)
        buttons.addWidget(self.gif_button)

        texture = QGroupBox("Texture", self)
        texture_layout = QVBoxLayout(texture)
        texture_layout.setContentsMargins(6, 6, 6, 6)
        texture_layout.addWidget(self.clut_strip)
        texture_layout.addWidget(page_scroll)
        texture_layout.addLayout(buttons)

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
        self.tree.resizeColumnToContents(0)

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
        self._preview = polygon
        self._preview_frames = None
        vram = getattr(self.viewer, "vram_raw_bytes", None)
        usable = (polygon is not None and vram
                  and len(vram) >= psx_vram.VRAM_SIZE)
        self.png_button.setEnabled(bool(usable))
        self.gif_button.setEnabled(bool(usable) and any(self._animations(polygon)))
        if not usable:
            self._preview_timer.stop()
            self.clut_strip.clear()
            self.uv_canvas.clear()
            self.uv_canvas.set_ring(())
            return
        self._draw_preview(fit=True)
        # Polled rather than driven: the preview shows whatever frame the
        # 3D view is on, so it starts and stops with the Animate button
        # instead of keeping a clock of its own.
        if self.gif_button.isEnabled():
            self._preview_timer.start(PREVIEW_POLL_MS)
        else:
            self._preview_timer.stop()

    def _animations(self, polygon):
        """(palette animation, UV animation) for a polygon, either None."""
        if polygon is None:
            return None, None
        clut = polygon["clut"]
        return (self.viewer.clut_animations.get(clut),
                self.viewer.uv_animations.get(clut))

    def _frame_state(self, polygon, tick):
        """(palette frame, UV frame) at `tick`, either None."""
        palette, uv = self._animations(polygon)
        return (palette.frame_at(tick) if palette else None,
                (tick // UV_TICKS_PER_FRAME) % len(uv) if uv else None)

    def _palette_at(self, polygon, frame):
        """The 16 colours the polygon draws with on that frame."""
        animation, _uv = self._animations(polygon)
        if animation is not None and frame is not None:
            return psx_vram.read_palette(animation.frames[frame], 0, 16,
                                         transparent_zero=False)
        return psx_vram.read_palette(self.viewer.vram_raw_bytes,
                                     polygon["clut"], 16, transparent_zero=False)

    def _uv_offset(self, polygon, frame):
        _palette, uv = self._animations(polygon)
        return uv.offset_at(frame) if uv is not None and frame is not None else (0, 0)

    def _page_rgb(self, polygon, palette_frame):
        texels = uv_anim.page_texels(self.viewer.vram_raw_bytes, polygon["page"])
        lut = np.array([c[:3] for c in self._palette_at(polygon, palette_frame)],
                       dtype=np.uint8)
        return lut[texels]

    def _poll_animation(self):
        self._draw_preview()

    def _draw_preview(self, fit=False):
        polygon = self._preview
        if polygon is None:
            return
        state = self._frame_state(polygon, getattr(self.viewer, "anim_tick", 0))
        if not fit and state == self._preview_frames:
            return
        self._preview_frames = state
        palette_frame, uv_frame = state

        self.clut_strip.setPixmap(_swatch(self._palette_at(polygon, palette_frame),
                                          self.clut_strip.height()))
        rgb = self._page_rgb(polygon, palette_frame)
        # QImage doesn't own the buffer it is handed, so copy it.
        self.uv_canvas.set_image(QImage(rgb.tobytes(), PAGE, PAGE, PAGE * 3,
                                        QImage.Format.Format_RGB888).copy())
        if fit:
            self.uv_canvas.set_zoom(fit_zoom((PAGE, PAGE),
                                             self._page_scroll.viewport().size()))
        du, dv = self._uv_offset(polygon, uv_frame)
        self.uv_canvas.set_ring([((u + du) % PAGE, (v + dv) % PAGE)
                                 for u, v in polygon["texels"]])

    # --- export --------------------------------------------------------

    def _stem(self, polygon):
        entry = (self.viewer.model_data["entries"])[polygon["entry"]]
        return (f"page{polygon['page']}_clut{polygon['clut']:06X}"
                f"_entry{entry['index']}_{polygon['kind']}{polygon['slot']}")

    def _save_png(self):
        polygon = self._preview
        if polygon is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save texture page", self._stem(polygon) + ".png",
            "PNG image (*.png)")
        if not path:
            return
        rgb = self._page_rgb(polygon, self._frame_state(
            polygon, getattr(self.viewer, "anim_tick", 0))[0])
        Image.fromarray(rgb, "RGB").save(path)

    def _save_gif(self):
        polygon = self._preview
        if polygon is None:
            return
        frames = self._gif_frames(polygon)
        if not frames:
            QMessageBox.information(self, "Nothing to animate",
                                    "This polygon's texture doesn't animate.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save animated texture", self._stem(polygon) + ".gif",
            "GIF image (*.gif)")
        if not path:
            return
        images = [image for image, _ms in frames]
        try:
            images[0].save(path, save_all=True, append_images=images[1:],
                           duration=[ms for _image, ms in frames], loop=0)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        QMessageBox.information(
            self, "Exported",
            f"Wrote {len(images)} frame(s) of {images[0].width // GIF_SCALE}"
            f"x{images[0].height // GIF_SCALE} texels.")

    def _gif_frames(self, polygon):
        """[(image, milliseconds), ...] over one loop of whatever animates
        this polygon - the palette, the UVs, or both.

        The crop is the polygon's own UV box rather than the whole page:
        what animates is the patch this face takes, and a page around it
        that never changes is just margin."""
        palette_animation, uv_animation = self._animations(polygon)
        if palette_animation is None and uv_animation is None:
            return []
        palette_period = palette_animation.loop_ticks if palette_animation else 1
        uv_period = (len(uv_animation) * UV_TICKS_PER_FRAME) if uv_animation else 1
        period = palette_period * uv_period // gcd(palette_period, uv_period)

        us = [u for u, _v in polygon["texels"]]
        vs = [v for _u, v in polygon["texels"]]
        u0, u1 = min(us), max(us) + 1
        v0, v1 = min(vs), max(vs) + 1

        runs = []
        for tick in range(period):
            state = self._frame_state(polygon, tick)
            if not runs or runs[-1][0] != state:
                if len(runs) >= MAX_GIF_FRAMES:
                    break
                runs.append([state, 0])
            runs[-1][1] += 1

        # A run can come out looking exactly like the one before it - the
        # lava's sheet holds each of its frames twice - and PIL drops a
        # repeated frame while keeping its neighbour's duration, which
        # plays those parts of the loop at double speed. Merging them
        # here keeps the timing right and leaves PIL nothing to drop.
        patches = []
        for (palette_frame, uv_frame), ticks in runs:
            du, dv = self._uv_offset(polygon, uv_frame)
            rgb = self._page_rgb(polygon, palette_frame)
            patch = np.take(np.take(rgb, np.arange(v0 + dv, v1 + dv) % PAGE, axis=0),
                            np.arange(u0 + du, u1 + du) % PAGE, axis=1)
            if patches and np.array_equal(patches[-1][0], patch):
                patches[-1][1] += ticks
            else:
                patches.append([patch, ticks])
        if len(patches) > 1 and np.array_equal(patches[0][0], patches[-1][0]):
            patches[0][1] += patches.pop()[1]

        frames = []
        for patch, ticks in patches:
            image = Image.fromarray(patch, "RGB")
            frames.append((image.resize((image.width * GIF_SCALE,
                                         image.height * GIF_SCALE), Image.NEAREST),
                           max(20, round(ticks * 1000 / TICK_HZ))))
        return frames


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

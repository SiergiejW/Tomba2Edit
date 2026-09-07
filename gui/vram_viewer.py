"""VRAM viewer - one megabyte of PSX video memory, looked at.

VRAM has no format of its own. It is 1024x512 halfwords holding textures
and palettes side by side (see functions/psx_vram.py), and what a region
of it means is settled entirely by what points at it. So this view does
not try to decide: it offers the three readings that are actually useful
and lets the eye pick.

    indices     each byte as two 4-bit texels, grey. The shape of every
                4bpp texture, with no palette needed - which is what to
                look at when hunting for where a texture lives.
    palette     the same texels through a chosen 16-colour CLUT, which
                is how the hardware would draw them.
    direct      each halfword as one BGR555 pixel. What a 16bpp
                background or a palette row really looks like.

A CLUT can be typed in, chosen from the list a loaded model supplies, or
picked straight off the image - right-click any palette row and it is
read from there.

Zoom and pan are done by painting a source rectangle of the image into
the widget rather than by scaling a pixmap into a scroll area. At 8x a
4096x512 image is a 130-megapixel pixmap, which is what made the old
view stutter; this way the cost does not depend on the zoom at all.
"""
import numpy as np
from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from functions import img_codec, psx_vram

# How the two 4bpp readings lay VRAM out: two texels per byte and two
# bytes per halfword, so the picture is FOUR times as wide as VRAM is in
# halfwords - 4096 across against VRAM's own 1024.
TEXEL_WIDTH = psx_vram.ATLAS_WIDTH        # 4096
TEXEL_HEIGHT = psx_vram.VRAM_ROWS         # 512

MODE_INDICES = "4bpp indices (grey)"
MODE_PALETTE = "4bpp through CLUT"
MODE_DIRECT = "16-bit direct (BGR555)"

MIN_ZOOM, MAX_ZOOM = 0.1, 32.0


def decode_vram_bytes(img_data):
    """Decompress one area's TOMBA2.IMG chunk into a raw 1024x512
    16-bit VRAM image - 0x800 bytes per row, 1MB in total.

    The shards and their instruction stream are functions/img_codec.py's
    business; this only lays the results out at the right stride."""
    vram_bytes = bytearray(psx_vram.VRAM_SIZE)
    for (x, y, w, h, _packed), pixels in img_codec.decompress_chunk(img_data):
        row_bytes = w * 2
        for row in range(h):
            at = (y + row) * psx_vram.VRAM_STRIDE + x * 2
            vram_bytes[at:at + row_bytes] = \
                pixels[row * row_bytes:(row + 1) * row_bytes]
    return vram_bytes


def _rows(vram_bytes):
    """VRAM as a (512, 0x800) byte array, padded if it is short."""
    raw = np.frombuffer(bytes(vram_bytes[:psx_vram.VRAM_SIZE]), dtype=np.uint8)
    if raw.size < psx_vram.VRAM_SIZE:
        raw = np.pad(raw, (0, psx_vram.VRAM_SIZE - raw.size))
    return raw.reshape(psx_vram.VRAM_ROWS, psx_vram.VRAM_STRIDE)


def vram_texels(vram_bytes):
    """The 4096x512 array of 4-bit texel indices, low nibble first.

    Which nibble comes first is not a detail: the PSX puts the leftmost
    texel of a pair in the LOW nibble, so reading them the other way
    round mirrors every texture in pairs."""
    rows = _rows(vram_bytes)
    texels = np.empty((TEXEL_HEIGHT, TEXEL_WIDTH), dtype=np.uint8)
    texels[:, 0::2] = rows & 0x0F
    texels[:, 1::2] = rows >> 4
    return texels


def vram_index_image(vram_bytes):
    """The 4096x512 image the 3D viewers sample as an index map.

    Each texel's 4-bit index is spread over R, G and B as index * 17 -
    which is what the fragment shaders' `texture(...).r * 15.0` reads
    back out."""
    grey = vram_texels(vram_bytes) * 17
    rgba = np.empty((TEXEL_HEIGHT, TEXEL_WIDTH, 4), dtype=np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = grey
    rgba[..., 3] = 255
    # QImage doesn't own the buffer it's handed, so copy() before the
    # array goes out of scope.
    return QImage(rgba.tobytes(), TEXEL_WIDTH, TEXEL_HEIGHT, TEXEL_WIDTH * 4,
                  QImage.Format.Format_RGBA8888).copy()


def vram_palette_image(vram_bytes, clut_address, transparent_zero=False):
    """The same texels drawn through one 16-colour palette.

    Colour 0 is the hardware's transparent one, but a whole screen of
    transparency is nothing to look at, so by default it is drawn as the
    black it holds and only marked out on request."""
    palette = np.array(
        psx_vram.read_palette(vram_bytes, clut_address, 16,
                              transparent_zero=transparent_zero),
        dtype=np.uint8)
    rgba = palette[vram_texels(vram_bytes)]
    return QImage(rgba.tobytes(), TEXEL_WIDTH, TEXEL_HEIGHT, TEXEL_WIDTH * 4,
                  QImage.Format.Format_RGBA8888).copy()


def vram_direct_image(vram_bytes):
    """Every halfword as one BGR555 pixel - 1024x512.

    Red is in the low five bits, and bit 15 is the semi-transparency
    flag rather than part of the colour, so it is dropped."""
    rows = _rows(vram_bytes)
    words = rows.view(np.uint16).reshape(psx_vram.VRAM_ROWS, -1)
    rgba = np.empty((psx_vram.VRAM_ROWS, words.shape[1], 4), dtype=np.uint8)
    rgba[..., 0] = ((words & 0x1F) * 255 // 31).astype(np.uint8)
    rgba[..., 1] = (((words >> 5) & 0x1F) * 255 // 31).astype(np.uint8)
    rgba[..., 2] = (((words >> 10) & 0x1F) * 255 // 31).astype(np.uint8)
    rgba[..., 3] = 255
    return QImage(rgba.tobytes(), words.shape[1], psx_vram.VRAM_ROWS,
                  words.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()


class VRAMCanvas(QWidget):
    """The image, with zoom and pan that do not depend on its size.

    The whole picture stays a single unscaled QPixmap; what moves is the
    source rectangle drawn from. Panning is a drag with either button,
    the wheel zooms about the cursor, and nothing is ever resampled
    smoothly - a texel is meant to look like a texel."""

    hovered = pyqtSignal(int, int)          # texel x, y under the cursor
    picked_clut = pyqtSignal(int)           # byte address, from the menu

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

        self.pixmap = None
        self.zoom = 1.0
        # Top-left of the view, in image pixels.
        self.origin = QPointF(0.0, 0.0)
        self.show_grid = False
        # One rectangle in image coordinates to ring - what the IMG view
        # marks the selected shard with. None draws nothing.
        self.highlight = None
        # How many image pixels one VRAM halfword column spans, so the
        # grid and the readout mean the same thing in every mode. At
        # 4bpp a halfword is FOUR texels - two per byte, two bytes to a
        # halfword - and 1 when each halfword is drawn as one pixel.
        self.texels_per_halfword = 4
        self._drag_from = None
        self._drag_origin = None

    def set_image(self, image):
        self.pixmap = QPixmap.fromImage(image) if image is not None else None
        self.update()

    def fit(self):
        if not self.pixmap or not self.pixmap.width():
            return
        self.zoom = min(self.width() / self.pixmap.width(),
                        self.height() / self.pixmap.height())
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom))
        self.origin = QPointF(0.0, 0.0)
        self.update()

    def set_zoom(self, zoom, about=None):
        """Zoom, keeping the image point under `about` where it is."""
        if not self.pixmap:
            return
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if about is None:
            about = QPointF(self.width() / 2, self.height() / 2)
        before = self._to_image(about)
        self.zoom = zoom
        after = self._to_image(about)
        self.origin += before - after
        self._clamp()
        self.update()

    def _to_image(self, point):
        return QPointF(self.origin.x() + point.x() / self.zoom,
                       self.origin.y() + point.y() / self.zoom)

    def _clamp(self):
        """Keep at least a corner of the image in view.

        Scrolling into empty space is what made the old view feel lost -
        there was nothing to say which way back."""
        if not self.pixmap:
            return
        span_x = self.width() / self.zoom
        span_y = self.height() / self.zoom
        max_x = max(0.0, self.pixmap.width() - span_x)
        max_y = max(0.0, self.pixmap.height() - span_y)
        self.origin = QPointF(min(max(0.0, self.origin.x()), max_x),
                              min(max(0.0, self.origin.y()), max_y))

    # --- events -------------------------------------------------------

    def wheelEvent(self, event):
        if not self.pixmap:
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        self.set_zoom(self.zoom * (1.25 ** steps), event.position())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton,
                              Qt.MouseButton.MiddleButton):
            self._drag_from = event.position()
            self._drag_origin = QPointF(self.origin)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            delta = event.position() - self._drag_from
            self.origin = self._drag_origin - delta / self.zoom
            self._clamp()
            self.update()
            return
        at = self._to_image(event.position())
        if self.pixmap and 0 <= at.x() < self.pixmap.width() \
                and 0 <= at.y() < self.pixmap.height():
            self.hovered.emit(int(at.x()), int(at.y()))

    def mouseReleaseEvent(self, _event):
        self._drag_from = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _menu(self, position):
        if not self.pixmap:
            return
        at = self._to_image(QPointF(position))
        # A palette is 16 halfwords starting on a 16-halfword boundary,
        # so the click is snapped to the row it landed in.
        halfword = int(at.x()) // self.texels_per_halfword
        address = (halfword // 16 * 16) * 2 + int(at.y()) * psx_vram.VRAM_STRIDE
        menu = QMenu(self)
        action = QAction(f"Use the palette at 0x{address:X}", menu)
        action.triggered.connect(lambda: self.picked_clut.emit(address))
        menu.addAction(action)
        menu.exec(self.mapToGlobal(position))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 24))
        if not self.pixmap:
            painter.setPen(QColor(160, 160, 160))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No VRAM loaded")
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        span_x = self.width() / self.zoom
        span_y = self.height() / self.zoom
        source = QRectF(self.origin.x(), self.origin.y(), span_x, span_y)
        target = QRectF(0, 0, self.width(), self.height())
        # A source rectangle running past the image leaves the rest of
        # the widget as background rather than stretching the edge.
        clipped = source.intersected(QRectF(0, 0, self.pixmap.width(),
                                            self.pixmap.height()))
        if clipped.isEmpty():
            return
        target = QRectF((clipped.x() - source.x()) * self.zoom,
                        (clipped.y() - source.y()) * self.zoom,
                        clipped.width() * self.zoom,
                        clipped.height() * self.zoom)
        painter.drawPixmap(target, self.pixmap, clipped)
        if self.show_grid:
            self._draw_grid(painter, source)
        if self.highlight is not None:
            painter.setPen(QPen(QColor(255, 220, 40), 2))
            painter.drawRect(QRectF(
                (self.highlight.x() - source.x()) * self.zoom,
                (self.highlight.y() - source.y()) * self.zoom,
                self.highlight.width() * self.zoom,
                self.highlight.height() * self.zoom))

    def show_rect(self, rect):
        """Ring a rectangle and bring it into view."""
        self.highlight = rect
        if rect is not None and self.pixmap:
            self.origin = QPointF(
                max(0.0, rect.center().x() - self.width() / (2 * self.zoom)),
                max(0.0, rect.center().y() - self.height() / (2 * self.zoom)))
            self._clamp()
        self.update()

    def _draw_grid(self, painter, source):
        """Texture-page boundaries: 64 halfwords across, 256 rows down.

        A page is what a face's draw code selects, so these lines are
        where one texture's addressable space ends and the next begins -
        the single most useful thing to see over raw VRAM."""
        step_x = psx_vram.PAGE_HALFWORDS * self.texels_per_halfword
        painter.setPen(QPen(QColor(0, 200, 255, 140), 1))
        x = int(source.x()) // step_x * step_x
        while x <= source.right():
            at = (x - source.x()) * self.zoom
            painter.drawLine(int(at), 0, int(at), self.height())
            x += step_x
        y = int(source.y()) // psx_vram.PAGE_ROWS * psx_vram.PAGE_ROWS
        while y <= source.bottom():
            at = (y - source.y()) * self.zoom
            painter.drawLine(0, int(at), self.width(), int(at))
            y += psx_vram.PAGE_ROWS


class VRAMViewer(QWidget):
    """VRAM, in whichever of its three readings is wanted."""

    def __init__(self):
        super().__init__()
        self.vram_bytes = None
        self._image = None
        self.clut_address = 0
        self.source_name = "VRAM"
        # Set by MainWindow on the older path, kept so loading VRAM here
        # still feeds the MDAT view.
        self.mdat_viewer = None

        self.canvas = VRAMCanvas(self)
        self.canvas.hovered.connect(self._on_hover)
        self.canvas.picked_clut.connect(self.set_clut_address)

        self.mode_box = QComboBox()
        self.mode_box.addItems([MODE_INDICES, MODE_PALETTE, MODE_DIRECT])
        self.mode_box.setToolTip(
            "How to read the bytes. VRAM holds textures and palettes "
            "together with nothing marking which is which, so this is a "
            "choice about what you are looking for, not about what the "
            "data is.")
        self.mode_box.currentTextChanged.connect(self._rerender)

        self.clut_box = QComboBox()
        self.clut_box.setEditable(True)
        self.clut_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.clut_box.setMinimumWidth(220)
        self.clut_box.setToolTip(
            "Which palette to draw the texels through. The list is the "
            "palettes the loaded model actually samples; anything else "
            "can be typed as a hex byte address, or right-clicked "
            "straight off the image.")
        self.clut_box.lineEdit().returnPressed.connect(self._clut_typed)
        self.clut_box.activated.connect(self._clut_chosen)

        self.grid_btn = QPushButton("Page grid")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setToolTip(
            "Mark the texture pages - 64 halfwords across, 256 rows "
            "down, which is the tile a face's draw code selects.")
        self.grid_btn.toggled.connect(self._toggle_grid)

        fit_btn = QPushButton("Fit")
        fit_btn.clicked.connect(self.canvas.fit)
        one_btn = QPushButton("1:1")
        one_btn.clicked.connect(lambda: self.canvas.set_zoom(1.0))
        in_btn = QPushButton("+")
        in_btn.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom * 2))
        out_btn = QPushButton("-")
        out_btn.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom / 2))

        export_btn = QPushButton("Export PNG")
        export_btn.setToolTip("Write what is on screen out at full size.")
        export_btn.clicked.connect(self.export_png)
        raw_btn = QPushButton("Export raw")
        raw_btn.setToolTip(
            "Write the whole megabyte of decompressed VRAM out as it "
            "stands, for a hex editor or another tool.")
        raw_btn.clicked.connect(self.export_raw)

        top = QHBoxLayout()
        top.setContentsMargins(8, 4, 8, 4)
        top.addWidget(QLabel("Read as:"))
        top.addWidget(self.mode_box)
        top.addWidget(QLabel("CLUT:"))
        top.addWidget(self.clut_box)
        top.addWidget(self.grid_btn)
        top.addStretch(1)
        for button in (out_btn, in_btn, one_btn, fit_btn, export_btn, raw_btn):
            top.addWidget(button)

        self.info_label = QLabel("No VRAM loaded")
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 2, 8, 4)
        bottom.addWidget(self.info_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(bottom)

    # --- loading ------------------------------------------------------

    def set_vram_bytes(self, vram_bytes, name="VRAM"):
        """Show a megabyte of VRAM that somebody else has decompressed."""
        self.vram_bytes = bytearray(vram_bytes)
        self.source_name = name
        self._rerender()
        self.canvas.fit()
        return True

    def load_vram_data(self, img_data):
        """Decompress one TOMBA2.IMG chunk and show it."""
        try:
            self.set_vram_bytes(decode_vram_bytes(img_data))
        except Exception as e:
            self.info_label.setText(f"Error loading VRAM: {e}")
            return False
        if self.mdat_viewer:
            self.mdat_viewer.set_vram_image(vram_index_image(self.vram_bytes),
                                            self.vram_bytes)
        return True

    def load_cvrm_data(self, img_data):
        """The same chunk, read as 16-bit colour.

        This is the reading a background wants; it used to be a separate
        code path that decompressed nothing, so it showed the packed
        bytes rather than the picture. Now it is the same decode with
        the mode set for you."""
        try:
            self.set_vram_bytes(decode_vram_bytes(img_data))
        except Exception as e:
            self.info_label.setText(f"Error loading CVRAM: {e}")
            return False
        self.mode_box.setCurrentText(MODE_DIRECT)
        return True

    def process_vram(self, img_data):
        """(PIL image of the indices, raw VRAM bytes).

        Kept for MainWindow, which hands the image straight to ImageQt."""
        vram_bytes = decode_vram_bytes(img_data)
        grey = vram_texels(vram_bytes) * 17
        rgba = np.dstack([grey, grey, grey,
                          np.full_like(grey, 255)])
        return Image.fromarray(rgba, "RGBA"), vram_bytes

    def set_clut_choices(self, choices):
        """[(label, byte address), ...] the loaded model samples."""
        self.clut_box.blockSignals(True)
        self.clut_box.clear()
        for label, address in choices:
            self.clut_box.addItem(f"{label}  0x{address:X}", address)
        self.clut_box.blockSignals(False)

    def set_clut_address(self, address):
        self.clut_address = int(address)
        self.clut_box.setEditText(f"0x{self.clut_address:X}")
        if self.mode_box.currentText() != MODE_PALETTE:
            self.mode_box.setCurrentText(MODE_PALETTE)   # re-renders
        else:
            self._rerender()
        print(f"selected: CLUT @ 0x{self.clut_address:X} "
              f"({self.source_name})")

    # --- rendering ----------------------------------------------------

    def _clut_typed(self):
        # A typed entry is a hex address; a chosen one is a label with
        # the address on the end, so take the last word either way.
        words = self.clut_box.currentText().split()
        text = words[-1] if words else ""
        try:
            self.set_clut_address(int(text, 16))
        except ValueError:
            self.info_label.setText(f"'{text}' is not a hex address")

    def _clut_chosen(self, index):
        address = self.clut_box.itemData(index)
        if address is not None:
            self.set_clut_address(address)

    def _toggle_grid(self, on):
        self.canvas.show_grid = on
        self.canvas.update()

    def _rerender(self):
        if self.vram_bytes is None:
            return
        mode = self.mode_box.currentText()
        if mode == MODE_DIRECT:
            image = vram_direct_image(self.vram_bytes)
            self.canvas.texels_per_halfword = 1
        elif mode == MODE_PALETTE:
            image = vram_palette_image(self.vram_bytes, self.clut_address)
            self.canvas.texels_per_halfword = 4
        else:
            image = vram_index_image(self.vram_bytes)
            self.canvas.texels_per_halfword = 4
        self._image = image
        self.canvas.set_image(image)
        self.info_label.setText(
            f"{self.source_name}: {image.width()}x{image.height()}, {mode}"
            + (f", CLUT 0x{self.clut_address:X}" if mode == MODE_PALETTE
               else ""))

    def _on_hover(self, x, y):
        """Say where the cursor is in the terms the file formats use."""
        halfword = x // self.canvas.texels_per_halfword
        # Two bytes per halfword spread over however many pixels this
        # mode draws it as, which is the one formula both modes share.
        address = (x * 2 // self.canvas.texels_per_halfword
                   + y * psx_vram.VRAM_STRIDE)
        page = (halfword // psx_vram.PAGE_HALFWORDS
                + (y // psx_vram.PAGE_ROWS) * psx_vram.ATLAS_COLUMNS)
        self.info_label.setText(
            f"{self.source_name}  x {x} y {y}  halfword ({halfword}, {y})  "
            f"VRAM 0x{address:X}  page {page}  zoom {self.canvas.zoom:.2f}x")

    # --- export -------------------------------------------------------

    def export_png(self):
        if self._image is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the VRAM picture",
            f"{self.source_name}.png", "PNG (*.png)")
        if not path:
            return
        if self._image.save(path, "PNG"):
            print(f"wrote {path}")
        else:
            QMessageBox.critical(self, "Export failed",
                                 f"Couldn't write {path}")

    def export_raw(self):
        if self.vram_bytes is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the raw VRAM",
            f"{self.source_name}.vram", "Raw VRAM (*.vram *.bin)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self.vram_bytes)
        except OSError as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        print(f"wrote {path} ({len(self.vram_bytes)} bytes)")

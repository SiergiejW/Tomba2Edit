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
    textured    every patch this area's own MDAT room, SMST/SPRT/BGMP files are
                known to sample, each through its own CLUT - see
                functions/vram_preview.py for what that can and can't
                know.

The viewer shows these as two views: Textured VRAM (the default: known
patches through their own CLUTs, over whichever reading is chosen) and
VRAM (the reading alone).

A CLUT can be typed in, chosen from the list a loaded model supplies, or
picked straight off the image - right-click any palette row and the
crosshair lands on it; the arrow keys steer
it from palette to palette. Palette rows known assets draw through are
shown in their own colours.

Zoom and pan are done by painting a source rectangle of the image into
the widget rather than by scaling a pixmap into a scroll area. At 8x a
4096x512 image is a 130-megapixel pixmap, which is what made the old
view stutter; this way the cost does not depend on the zoom at all.
"""
import struct

import numpy as np
from PIL import Image
from PIL.ImageQt import ImageQt
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QTabBar,
    QVBoxLayout, QWidget,
)

from functions import img_codec, psx_vram
from functions.vram_preview import clut_spans, paint_cluts

# How the two 4bpp readings lay VRAM out: two texels per byte and two
# bytes per halfword, so the picture is FOUR times as wide as VRAM is in
# halfwords - 4096 across against VRAM's own 1024.
TEXEL_WIDTH = psx_vram.ATLAS_WIDTH        # 4096
TEXEL_HEIGHT = psx_vram.VRAM_ROWS         # 512

MODE_INDICES = "4bpp indices (grey)"
MODE_FALSE = "4bpp indices (false colour)"
MODE_PALETTE = "Through CLUT"
MODE_DIRECT = "16-bit direct (BGR555)"

VIEW_TEXTURED, VIEW_VRAM = "Textured VRAM", "VRAM"
VIEWS = (VIEW_TEXTURED, VIEW_VRAM)

# What 'Read as' covers in Textured VRAM.
REACH_UNCOLOURED = "Uncoloured texels"
REACH_ALL = "All of VRAM"

# A CLUT's two lengths.
DEPTH_16, DEPTH_256 = "16 colours", "256 colours"


def _false_colours():
    """16 made-up colours, one per 4bpp index, far apart in hue so
    neighbouring indices read apart; index 0 stays black."""
    import colorsys
    out = np.zeros((16, 4), dtype=np.uint8)
    out[:, 3] = 255
    for i in range(1, 16):
        r, g, b = colorsys.hsv_to_rgb(i * 7 % 15 / 15, 0.85, 0.55 + 0.45 * (i % 2))
        out[i, :3] = int(r * 255), int(g * 255), int(b * 255)
    return out


FALSE_COLOURS = _false_colours()

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
    crosshair_moved = pyqtSignal(int)       # byte address, from the arrows

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        # The right button puts the CLUT crosshair down and drags it.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self._picking = None            # address under a held right button

        self.pixmap = None
        self.zoom = 1.0
        # Top-left of the view, in image pixels.
        self.origin = QPointF(0.0, 0.0)
        self.show_grid = False
        # One rectangle in image coordinates to ring - what the IMG view
        # marks the selected shard with. None draws nothing.
        self.highlight = None
        # Several more, as (rect, QColor, label): what the migration
        # preview marks a proposed texture placement with.
        self.highlights = []
        # One of those rects, drawn again with a thick dashed white
        # ring on top - which one the migration preview's own "Moving"
        # list currently has picked, so it stands out among however
        # many other boxes are on screen. None draws nothing extra.
        self.emphasis = None
        # How many image pixels one VRAM halfword column spans, so the
        # grid and the readout mean the same thing in every mode. At
        # 4bpp a halfword is FOUR texels - two per byte, two bytes to a
        # halfword - and 1 when each halfword is drawn as one pixel.
        self.texels_per_halfword = 4
        # A palette row's (halfword x, row) the arrow keys steer, or None.
        self.crosshair = None
        self.crosshair_colours = 16     # the palette's length in halfwords
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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

    def keyPressEvent(self, event):
        """Arrows step the crosshair one palette (16 halfwords) or one
        row; with Shift, a texture page across or 16 rows."""
        if self.crosshair is None:
            return super().keyPressEvent(event)
        big = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        across = psx_vram.PAGE_HALFWORDS if big else 16
        down = 16 if big else 1
        moves = {Qt.Key.Key_Left: (-across, 0), Qt.Key.Key_Right: (across, 0),
                 Qt.Key.Key_Up: (0, -down), Qt.Key.Key_Down: (0, down)}
        move = moves.get(event.key())
        if move is None:
            return super().keyPressEvent(event)
        x, y = self.crosshair
        x = (x + move[0]) % (psx_vram.VRAM_STRIDE // 2)
        y = (y + move[1]) % psx_vram.VRAM_ROWS
        self.crosshair = (x, y)
        self._keep_in_view(x, y)
        self.update()
        self.crosshair_moved.emit(x * 2 + y * psx_vram.VRAM_STRIDE)
        event.accept()

    def _keep_in_view(self, x, y):
        """Pan just enough that the crosshair's row stays on screen."""
        if not self.pixmap:
            return
        left, top = x * self.texels_per_halfword, y
        width = self.crosshair_colours * self.texels_per_halfword
        span_x, span_y = self.width() / self.zoom, self.height() / self.zoom
        ox, oy = self.origin.x(), self.origin.y()
        if left < ox:
            ox = left - span_x / 4
        elif left + width > ox + span_x:
            ox = left + width - span_x * 3 / 4
        if top < oy:
            oy = top - span_y / 4
        elif top + 1 > oy + span_y:
            oy = top + 1 - span_y * 3 / 4
        self.origin = QPointF(ox, oy)
        self._clamp()

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() == Qt.MouseButton.RightButton:
            self._picking = self._palette_at(event.position())
            if self._picking is not None:
                self.picked_clut.emit(self._picking)
            return
        if event.button() in (Qt.MouseButton.LeftButton,
                              Qt.MouseButton.MiddleButton):
            self._drag_from = event.position()
            self._drag_origin = QPointF(self.origin)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._picking is not None:
            address = self._palette_at(event.position())
            if address is not None and address != self._picking:
                self._picking = address
                if self.crosshair is not None:
                    self.crosshair = psx_vram.clut_address_xy(address)
                self.update()
                self.crosshair_moved.emit(address)
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
        self._picking = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _palette_at(self, position):
        """The palette row under a widget point, or None off the image.
        A palette starts on a 16-halfword boundary, so it snaps to one."""
        if not self.pixmap:
            return None
        at = self._to_image(QPointF(position))
        if not (0 <= at.x() < self.pixmap.width() and 0 <= at.y() < self.pixmap.height()):
            return None
        halfword = int(at.x()) // self.texels_per_halfword
        return (halfword // 16 * 16) * 2 + int(at.y()) * psx_vram.VRAM_STRIDE

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
        rings = list(self.highlights)
        if self.highlight is not None:
            rings.append((self.highlight, QColor(255, 220, 40), ""))
        for rect, color, label in rings:
            where = QRectF((rect.x() - source.x()) * self.zoom,
                           (rect.y() - source.y()) * self.zoom,
                           rect.width() * self.zoom,
                           rect.height() * self.zoom)
            painter.setPen(QPen(color, 2))
            painter.drawRect(where)
            if label:
                painter.drawText(where.adjusted(2, -14, 0, 0), label)
        if self.crosshair is not None:
            self._draw_crosshair(painter, source)
        if self.emphasis is not None:
            where = QRectF((self.emphasis.x() - source.x()) * self.zoom,
                           (self.emphasis.y() - source.y()) * self.zoom,
                           self.emphasis.width() * self.zoom,
                           self.emphasis.height() * self.zoom)
            # A couple of pixels bigger all round, so this ring sits
            # outside the coloured one rather than fighting it for the
            # same pixels - both stay legible at once.
            pen = QPen(QColor(255, 255, 255), 3)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(where.adjusted(-3, -3, 3, 3))

    def _draw_crosshair(self, painter, source):
        """Lines across the whole view through the crosshair's palette row,
        and a ring round the row itself."""
        x, y = self.crosshair
        left = (x * self.texels_per_halfword - source.x()) * self.zoom
        right = left + self.crosshair_colours * self.texels_per_halfword * self.zoom
        top = (y - source.y()) * self.zoom
        bottom = top + self.zoom
        middle_x, middle_y = (left + right) / 2, (top + bottom) / 2
        pen = QPen(QColor(255, 60, 200, 150), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, middle_y), QPointF(left - 4, middle_y))
        painter.drawLine(QPointF(right + 4, middle_y), QPointF(self.width(), middle_y))
        painter.drawLine(QPointF(middle_x, 0), QPointF(middle_x, top - 4))
        painter.drawLine(QPointF(middle_x, bottom + 4), QPointF(middle_x, self.height()))
        painter.setPen(QPen(QColor(255, 60, 200), 2))
        painter.drawRect(QRectF(left - 2, top - 2, right - left + 4, bottom - top + 4))

    def center_on(self, rect):
        """Pan - never zoom - so `rect`'s centre is in the middle of the
        view. Leaves self.highlight and self.highlights alone, for a
        caller (like the texture migration preview) whose own rings
        already mark the spot and would rather not draw a second one."""
        if rect is None or not self.pixmap:
            return
        self.origin = QPointF(
            max(0.0, rect.center().x() - self.width() / (2 * self.zoom)),
            max(0.0, rect.center().y() - self.height() / (2 * self.zoom)))
        self._clamp()
        self.update()

    def show_rect(self, rect):
        """Ring a rectangle and bring it into view, or clear the ring
        with rect=None - which still has to repaint even though there
        is then nothing for center_on() to do."""
        self.highlight = rect
        self.center_on(rect)
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
    """One area's VRAM in two views: Textured (every patch through the
    CLUT that draws it) and VRAM (the plain readings)."""

    def __init__(self):
        super().__init__()
        from functions import vram_preview

        self.vram_bytes = None
        self._image = None
        self.clut_address = 0
        self.source_name = "VRAM"
        # Set by MainWindow on the older path, kept so loading VRAM here
        # still feeds the MDAT view.
        self.mdat_viewer = None
        # Set by MainWindow when a chunk is loaded for a specific area -
        # what "Textured" needs to go looking for that area's own art.
        self._area_source = None        # (idx_path, dat_path, chunk_index)
        self._region_cache = {}         # area_source -> [vram_preview.Patch]
        self._layer = None              # (key, RGBA array) of painted regions
        self._stale = False             # level regions came in while hidden
        self._choices = []              # CLUT addresses a loaded model named

        self.view_tabs = QTabBar()
        for view in VIEWS:
            self.view_tabs.addTab(view)
        self.view_tabs.setToolTip(
            "Textured VRAM: this area's art, each patch through the CLUT "
            "its SMST/SPRT/BGMP file or the level editor's capture draws "
            "it with.\nVRAM: the plain readings.")
        self.view_tabs.currentChanged.connect(self._view_changed)

        self.canvas = VRAMCanvas(self)
        self.canvas.hovered.connect(self._on_hover)
        self.canvas.picked_clut.connect(self.set_clut_address)
        self.canvas.crosshair_moved.connect(self._crosshair_moved)

        self.mode_box = QComboBox()
        self.mode_box.addItems(
            [MODE_INDICES, MODE_FALSE, MODE_PALETTE, MODE_DIRECT])
        self.mode_box.setToolTip(
            "How to read the bytes. VRAM holds textures and palettes "
            "together with nothing marking which is which, so this is a "
            "choice about what you are looking for, not about what the "
            "data is.\n\nFalse colour gives each of the 16 indices its own "
            "made-up colour - neighbouring indices stand apart where grey "
            "blurs them.")
        self.mode_box.currentTextChanged.connect(self._mode_changed)
        self.mode_label = QLabel("Read as:")

        self.clut_box = QComboBox()
        self.clut_box.setEditable(True)
        self.clut_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.clut_box.setToolTip(
            "Which palette to draw the texels through. The list is the "
            "palettes the loaded model actually samples; anything else "
            "can be typed as a hex byte address, or right-click a "
            "palette row on the image.")
        self.clut_box.lineEdit().returnPressed.connect(self._clut_typed)
        self.clut_box.activated.connect(self._clut_chosen)
        self.depth_box = QComboBox()
        self.depth_box.addItems([DEPTH_16, DEPTH_256])
        self.depth_box.setToolTip(
            "The CLUT's length: 16 colours read VRAM as 4bpp texels, 256 "
            "colours as 8bpp ones - the two lengths the hardware has.")
        self.depth_box.currentTextChanged.connect(self._depth_changed)

        self.cross_box = QCheckBox("Crosshair")
        self.cross_box.setToolTip(
            "A CLUT crosshair the arrow keys steer (Shift: a page across "
            "or 16 rows). Steering it reads VRAM through the palette "
            "under it.")
        self.cross_box.toggled.connect(self._toggle_crosshair)
        self.reach_box = QComboBox()
        self.reach_box.addItems([REACH_UNCOLOURED, REACH_ALL])
        self.reach_box.setToolTip(
            "In Textured VRAM, what the 'Read as' reading covers: only "
            "texels no known asset draws, or the whole of VRAM.")
        self.reach_box.currentTextChanged.connect(self._rerender)
        self.cluts_box = QCheckBox("CLUTs in colour")
        self.cluts_box.setChecked(True)
        self.cluts_box.setToolTip(
            "Draw every palette row a known asset draws through (and the "
            "crosshair's) in its own colours instead of as grey indices.")
        self.cluts_box.toggled.connect(self._rerender)

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

        # Two short rows rather than one long one: a wide toolbar is a
        # minimum width, and the splitter took it out of the tree.
        top = QHBoxLayout()
        top.setContentsMargins(8, 4, 8, 0)
        top.addWidget(self.mode_label)
        top.addWidget(self.mode_box)
        top.addWidget(QLabel("CLUT:"))
        top.addWidget(self.clut_box)
        top.addWidget(self.depth_box)
        top.addStretch(1)
        options = QHBoxLayout()
        options.setContentsMargins(8, 2, 8, 4)
        for widget in (self.cross_box, self.reach_box, self.cluts_box,
                       self.grid_btn):
            options.addWidget(widget)
        options.addStretch(1)

        self.info_label = QLabel("No VRAM loaded")
        # A long line clips instead of widening the viewer.
        self.info_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                      QSizePolicy.Policy.Preferred)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 2, 8, 4)
        bottom.addWidget(self.info_label, 1)
        for button in (out_btn, in_btn, one_btn, fit_btn, export_btn, raw_btn):
            bottom.addWidget(button)
        # Combos as narrow as a few letters: their longest item is
        # otherwise a minimum width the tree pays for.
        for box in (self.mode_box, self.clut_box, self.depth_box, self.reach_box):
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view_tabs)
        layout.addLayout(top)
        layout.addLayout(options)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(bottom)
        self.setMinimumWidth(320)

        vram_preview.on_level_regions(self._level_regions_changed)
        self._view_changed(self.view_tabs.currentIndex())

    # --- loading ------------------------------------------------------

    def set_area_source(self, idx_path, dat_path, chunk_index):
        """Where "Textured" should go looking for this area's own art.
        Call this before handing over the VRAM itself, so a viewer
        already on Textured picks the new area up on its very next
        render rather than the one after."""
        self._area_source = (idx_path, dat_path, chunk_index)
        self._layer = None

    def set_vram_bytes(self, vram_bytes, name="VRAM"):
        """Show a megabyte of VRAM that somebody else has decompressed."""
        self.vram_bytes = bytearray(vram_bytes)
        self.source_name = name
        self._layer = None
        self._rerender()
        self.canvas.fit()
        return True

    def load_area(self, img_data, name, _file_offset=0):
        """One area's IMG chunk, decompressed."""
        return self.load_vram_data(img_data, name)

    def load_vram_data(self, img_data, name="VRAM"):
        """Decompress one TOMBA2.IMG chunk and show it."""
        try:
            self.set_vram_bytes(decode_vram_bytes(img_data), name)
        except Exception as e:
            self.info_label.setText(f"Error loading VRAM: {e}")
            return False
        if self.mdat_viewer:
            self.mdat_viewer.set_vram_image(vram_index_image(self.vram_bytes),
                                            self.vram_bytes)
        return True

    def load_cvrm_data(self, img_data):
        """The same chunk, read as 16-bit colour."""
        self.view_tabs.setCurrentIndex(VIEWS.index(VIEW_VRAM))
        self.mode_box.setCurrentText(MODE_DIRECT)
        return self.load_vram_data(img_data, self.source_name)

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
        self._choices = [address for _label, address in choices]
        self.clut_box.blockSignals(True)
        self.clut_box.clear()
        for label, address in choices:
            self.clut_box.addItem(f"{label}  0x{address:X}", address)
        self.clut_box.blockSignals(False)

    def set_clut_address(self, address):
        """Read through this palette: the crosshair moves onto it and
        'Read as' switches to the CLUT reading."""
        self.cross_box.blockSignals(True)
        self.cross_box.setChecked(True)
        self.cross_box.blockSignals(False)
        self._place_clut(int(address))
        self._read_through()
        print(f"selected: CLUT @ 0x{self.clut_address:X} "
              f"({self.source_name})")

    def _place_clut(self, address):
        self.clut_address = address
        self.clut_box.setEditText(f"0x{address:X}")
        if self.cross_box.isChecked():
            self.canvas.crosshair = psx_vram.clut_address_xy(address)

    def _colours(self):
        return 256 if self.depth_box.currentText() == DEPTH_256 else 16

    def _tangible(self):
        """Move onto a palette that shows something, if the current one is
        blank: a known asset's CLUT of this length, else the first row of
        VRAM that looks like one."""
        if self.vram_bytes is None:
            return
        colours = self._colours()

        def blank(address):
            words = psx_vram.read_palette(self.vram_bytes, address, colours)
            return len({w[:3] for w in words}) < 3

        if not blank(self.clut_address):
            return
        known = sorted(a for a, n in clut_spans(self._regions()[0])
                       if n == colours) + list(self._choices)
        for address in known:
            if not blank(address):
                self._place_clut(address)
                return
        for row in range(psx_vram.VRAM_ROWS):
            for x in range(0, psx_vram.VRAM_STRIDE // 2 - colours + 1, 16):
                address = row * psx_vram.VRAM_STRIDE + x * 2
                if not blank(address):
                    self._place_clut(address)
                    return

    # --- views --------------------------------------------------------

    def _view(self):
        return VIEWS[self.view_tabs.currentIndex()]

    def _view_changed(self, _index):
        self._rerender()

    def _read_through(self):
        """Switch 'Read as' to the CLUT reading (re-renders either way)."""
        if self.mode_box.currentText() != MODE_PALETTE:
            self.mode_box.setCurrentText(MODE_PALETTE)
        else:
            self._rerender()

    def _mode_changed(self, mode):
        if mode == MODE_PALETTE:
            self._tangible()
        self._rerender()

    def _depth_changed(self, _text):
        self.canvas.crosshair_colours = self._colours()
        if self.mode_box.currentText() == MODE_PALETTE:
            self._tangible()
        self._rerender()

    def _toggle_crosshair(self, on):
        if on:
            self._tangible()
            self.canvas.crosshair = psx_vram.clut_address_xy(self.clut_address)
            self.canvas.setFocus()
        else:
            self.canvas.crosshair = None
        self._rerender()

    def _crosshair_moved(self, address):
        self.clut_address = address
        self.clut_box.setEditText(f"0x{address:X}")
        self._read_through()

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

    def _level_regions_changed(self, chunk_index):
        if self._area_source is None or self._area_source[2] != chunk_index:
            return
        self._layer = None
        if self.isVisible() and self._view() == VIEW_TEXTURED:
            self._rerender()
        else:
            self._stale = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale:
            self._stale = False
            self._rerender()

    def _through_clut(self):
        """The whole of VRAM read through the chosen CLUT, at its length -
        a 256-colour one reads bytes, each two pixels wide here."""
        colours = self._colours()
        palette = np.array(psx_vram.read_palette(
            self.vram_bytes, self.clut_address, colours,
            transparent_zero=False), dtype=np.uint8)
        if colours == 16:
            return palette[vram_texels(self.vram_bytes)]
        return np.repeat(palette[_rows(self.vram_bytes)], 2, axis=1)

    def _rerender(self):
        if self.vram_bytes is None:
            return
        textured = self._view() == VIEW_TEXTURED
        mode = self.mode_box.currentText()
        note = ""
        self.canvas.crosshair_colours = self._colours()
        self.reach_box.setEnabled(textured)
        if mode == MODE_DIRECT:
            image = vram_direct_image(self.vram_bytes)
            self.canvas.texels_per_halfword = 1
            if textured:
                note = " - no patches over the 16-bit reading"
        else:
            self.canvas.texels_per_halfword = 4
            through = mode == MODE_PALETTE
            if through:
                rgba = self._through_clut()
            elif mode == MODE_FALSE:
                rgba = FALSE_COLOURS[vram_texels(self.vram_bytes)]
            else:
                grey = vram_texels(self.vram_bytes) * 17
                rgba = np.empty((TEXEL_HEIGHT, TEXEL_WIDTH, 4), dtype=np.uint8)
                rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = grey
                rgba[..., 3] = 255
            spans = set()
            if textured:
                regions, note = self._regions()
                if self.reach_box.currentText() != REACH_ALL:
                    layer = self._painted(regions)
                    drawn = layer[..., 3] > 0
                    rgba[drawn] = layer[drawn]
                spans = clut_spans(regions)
            if self.cluts_box.isChecked():
                if through:
                    spans.add((self.clut_address, self._colours()))
                paint_cluts(rgba, self.vram_bytes, spans)
            image = QImage(np.ascontiguousarray(rgba).tobytes(), TEXEL_WIDTH,
                           TEXEL_HEIGHT, TEXEL_WIDTH * 4,
                           QImage.Format.Format_RGBA8888).copy()
        self._image = image
        self.canvas.set_image(image)
        self.info_label.setText(
            f"{self.source_name}: {image.width()}x{image.height()}, "
            + ("textured over " if textured else "") + mode
            + (f", CLUT 0x{self.clut_address:X} ({self._colours()} colours)"
               if mode == MODE_PALETTE else "") + note)

    def _regions(self):
        """([Patch], info-line suffix) for Textured: the area's own files
        (cached - parsing them all is not free) and whatever the level
        editor has captured of it so far."""
        from functions import vram_preview

        if self._area_source is None:
            return [], (" - no area to search (opened from somewhere that "
                        "doesn't say which one)")
        idx_path, dat_path, chunk_index = self._area_source
        regions = self._region_cache.get(self._area_source)
        if regions is None:
            try:
                regions = vram_preview.area_regions(
                    idx_path, dat_path, chunk_index)
            except (OSError, struct.error):
                regions = []
            self._region_cache[self._area_source] = regions
        level = vram_preview.level_regions(chunk_index)
        note = (f" - {len(regions)} patch(es) from AREA_{chunk_index:02X}'s "
                f"own files" if regions else
                f" - nothing found in AREA_{chunk_index:02X}'s own files")
        note += (f", {len(level)} from the level editor" if level else
                 ", load it in the level editor for what its code draws")
        return regions + level, note

    def _painted(self, regions):
        """The regions painted through their own CLUTs, as an RGBA array
        transparent elsewhere - kept until the VRAM or regions change."""
        from functions import vram_preview

        key = (id(self.vram_bytes), len(regions))
        if self._layer is None or self._layer[0] != key:
            layer = vram_preview.render_layer(self.vram_bytes, regions)
            self._layer = (key, np.asarray(layer, dtype=np.uint8))
        return self._layer[1]

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

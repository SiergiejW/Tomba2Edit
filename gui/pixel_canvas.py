"""Zoom-and-pan canvas for pixel art, shared by the SPRT and BGMP
viewers.

Nearest-neighbour zoom, along fixed steps: a 24x24 sprite has to be
blown up several times over to be worth looking at, and a 576x1152
background has to be shrunk to be seen whole, but neither wants smooth
interpolation. Drag to pan, Ctrl+wheel to zoom, plain wheel scrolls as
normal.

Everything is painted against event.rect() rather than the whole
widget - a background at 4x is thousands of pixels across, and
repainting all of it on every scroll step would crawl.

Subclasses draw their own explanatory overlays in paint_overlays(),
which runs at display scale, so a one-pixel outline stays one pixel
wide at 16x instead of growing into a block along with the art.
"""
from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QScrollArea, QWidget

# Below 1 the fractions are all 1/n, so shrinking drops whole pixels
# rather than blending them - a background stays readable at a glance.
ZOOM_STEPS = (0.125, 0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 16, 24)
MIN_ZOOM, MAX_ZOOM = ZOOM_STEPS[0], ZOOM_STEPS[-1]

# Neutral enough to read as "nothing here" under either theme.
CHECKER_LIGHT = QColor(154, 154, 154)
CHECKER_DARK = QColor(134, 134, 134)
CHECKER_SIZE = 8


def snap_zoom(value):
    """The zoom step nearest `value`."""
    return min(ZOOM_STEPS, key=lambda step: abs(step - value))


def fit_zoom(image_size, viewport_size, max_zoom=MAX_ZOOM, margin=4):
    """The largest step at which `image_size` fits inside
    `viewport_size`, never above `max_zoom`."""
    width, height = image_size
    room_w = max(viewport_size.width() - margin, 1)
    room_h = max(viewport_size.height() - margin, 1)
    fits = [z for z in ZOOM_STEPS
            if z <= max_zoom and width * z <= room_w and height * z <= room_h]
    return max(fits) if fits else MIN_ZOOM


def zoom_label(zoom):
    """How a zoom reads in the toolbar - "1/4x" beats "0.25x"."""
    return f"{zoom:g}x" if zoom >= 1 else f"1/{round(1 / zoom):g}x"


class PixelCanvas(QWidget):
    """Displays one QImage. Put it in a QScrollArea with
    setWidgetResizable(False) - it sizes itself to image x zoom, and
    finds that scroll area on its own to pan it."""

    # (x, y) in image pixels, on a click that wasn't a drag.
    clicked = pyqtSignal(int, int)

    def __init__(self, zoom=1, parent=None):
        super().__init__(parent)
        self.image = None
        self.zoom = zoom
        # Per-view, because what reads as "nothing" depends on what is
        # drawn over it: pale art wants a darker ground than the light
        # default, and the font page is nearly all light pixels.
        self.checker_light = CHECKER_LIGHT
        self.checker_dark = CHECKER_DARK
        self.checker_size = CHECKER_SIZE
        self._drag_from = None
        self._scroll_from = None
        self._dragged = False

    # --- content ---

    def set_image(self, image):
        self.image = image
        self._resize_to_image()
        self.update()

    def clear(self):
        self.image = None
        self.setMinimumSize(1, 1)
        self.resize(1, 1)
        self.update()

    def set_zoom(self, zoom):
        zoom = snap_zoom(zoom)
        if zoom == self.zoom:
            return
        self.zoom = zoom
        self._resize_to_image()
        self.update()

    def zoom_by(self, direction):
        """One step along ZOOM_STEPS, in or out."""
        index = ZOOM_STEPS.index(snap_zoom(self.zoom))
        self.set_zoom(ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1, index + direction))])

    def scaled(self, value):
        """A length in image pixels, in widget pixels."""
        return int(round(value * self.zoom))

    def _resize_to_image(self):
        if self.image is None:
            return
        size = (self.scaled(self.image.width()), self.scaled(self.image.height()))
        self.setMinimumSize(*size)
        self.resize(*size)

    # --- painting ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        area = event.rect()
        self._paint_checker(painter, area)
        if self.image is None:
            return

        z = self.zoom
        if z < 1:
            # Shrunk to fit, the whole thing is small and cheap to draw
            # at once - and drawing it in pieces would leave rounding
            # seams between them.
            painter.drawImage(QRect(0, 0, self.scaled(self.image.width()),
                                    self.scaled(self.image.height())), self.image)
        else:
            z = int(z)
            x0 = max(0, area.left() // z)
            y0 = max(0, area.top() // z)
            x1 = min(self.image.width(), area.right() // z + 1)
            y1 = min(self.image.height(), area.bottom() // z + 1)
            if x1 > x0 and y1 > y0:
                painter.drawImage(QRect(x0 * z, y0 * z, (x1 - x0) * z, (y1 - y0) * z),
                                  self.image, QRect(x0, y0, x1 - x0, y1 - y0))
        self.paint_overlays(painter, area)

    def paint_overlays(self, painter, area):
        """Hook for subclasses. `area` is the region being repainted, in
        widget pixels - skip anything outside it."""

    def _paint_checker(self, painter, area):
        """The nothing behind a transparent pixel.

        Drawn in widget pixels rather than image ones, so its squares
        neither line up with the texels nor grow when the view is zoomed
        in - which is what keeps it reading as "there is nothing here"
        instead of as part of the picture."""
        light, dark, size = self.checker_light, self.checker_dark, self.checker_size
        painter.fillRect(area, light)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dark)
        first_x = (area.left() // size) * size
        first_y = (area.top() // size) * size
        for y in range(first_y, area.bottom() + 1, size):
            for x in range(first_x, area.right() + 1, size):
                if ((x // size) + (y // size)) % 2:
                    painter.drawRect(x, y, size, size)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    # --- mouse ---

    def _scroll_area(self):
        parent = self.parent()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parent()
        return parent

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            area = self._scroll_area()
            if area is not None:
                self._drag_from = event.position().toPoint()
                self._scroll_from = QPoint(area.horizontalScrollBar().value(),
                                           area.verticalScrollBar().value())
                self._dragged = False

    def mouseMoveEvent(self, event):
        if self._drag_from is None:
            return
        delta = event.position().toPoint() - self._drag_from
        if delta.manhattanLength() > 3:
            self._dragged = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        area = self._scroll_area()
        if area is not None:
            area.horizontalScrollBar().setValue(self._scroll_from.x() - delta.x())
            area.verticalScrollBar().setValue(self._scroll_from.y() - delta.y())

    def mouseReleaseEvent(self, event):
        was_click = self._drag_from is not None and not self._dragged
        self._drag_from = None
        self._scroll_from = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if was_click and self.image is not None:
            pos = event.position().toPoint()
            x, y = int(pos.x() / self.zoom), int(pos.y() / self.zoom)
            if 0 <= x < self.image.width() and 0 <= y < self.image.height():
                self.clicked.emit(x, y)

    def wheelEvent(self, event):
        """Ctrl+wheel zooms; a plain wheel is left to the scroll area."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
        else:
            event.ignore()

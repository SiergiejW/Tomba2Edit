"""Zoom-and-pan canvas for pixel art, shared by the SPRT and BGMP
viewers.

Integer, nearest-neighbour zoom only: these are 16x16 tiles and 24x24
sprites, where a smooth half-scale is worse than useless. Drag to pan,
Ctrl+wheel to zoom, plain wheel scrolls as normal.

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

MIN_ZOOM, MAX_ZOOM = 1, 24

# Neutral enough to read as "nothing here" under either theme.
CHECKER_LIGHT = QColor(154, 154, 154)
CHECKER_DARK = QColor(134, 134, 134)
CHECKER_SIZE = 8


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
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, int(zoom)))
        if zoom == self.zoom:
            return
        self.zoom = zoom
        self._resize_to_image()
        self.update()

    def _resize_to_image(self):
        if self.image is None:
            return
        size = (self.image.width() * self.zoom, self.image.height() * self.zoom)
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
        painter.fillRect(area, CHECKER_LIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(CHECKER_DARK)
        first_x = (area.left() // CHECKER_SIZE) * CHECKER_SIZE
        first_y = (area.top() // CHECKER_SIZE) * CHECKER_SIZE
        for y in range(first_y, area.bottom() + 1, CHECKER_SIZE):
            for x in range(first_x, area.right() + 1, CHECKER_SIZE):
                if ((x // CHECKER_SIZE) + (y // CHECKER_SIZE)) % 2:
                    painter.drawRect(x, y, CHECKER_SIZE, CHECKER_SIZE)
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
            x, y = pos.x() // self.zoom, pos.y() // self.zoom
            if 0 <= x < self.image.width() and 0 <= y < self.image.height():
                self.clicked.emit(int(x), int(y))

    def wheelEvent(self, event):
        """Ctrl+wheel zooms; a plain wheel is left to the scroll area."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom + (1 if event.angleDelta().y() > 0 else -1))
            event.accept()
        else:
            event.ignore()

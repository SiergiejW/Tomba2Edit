"""Player buttons as drawn glyphs instead of words: play, pause, stop, skip,
step.

Painted with QPainter into a QIcon at the screen's pixel ratio, so they stay
sharp at any scaling, and in the theme's colours - a button remembers its
glyph in a property (theme.GLYPH_PROPERTY) and theme.apply_theme repaints it
on a switch. The word it replaces becomes the tooltip.
"""
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (QColor, QIcon, QPainter, QPainterPath,
                         QPainterPathStroker, QPixmap)
from PyQt6.QtWidgets import QApplication

from gui import theme

GLYPH_SIZE = 16
BUTTON_SIZE = 32
# The glyphs a button can wear, and what each says when hovered.
NAMES = {"play": "Play", "pause": "Pause", "stop": "Stop",
         "previous": "Previous", "next": "Next",
         "first": "First frame", "last": "Last frame",
         "step_back": "Previous frame", "step_forward": "Next frame",
         "undo": "Undo", "redo": "Redo"}


def _colour(name):
    if theme.is_modern():
        c = theme.colours()
        return QColor(c["accent_text"] if name in ("play", "pause") else c["text"])
    return QApplication.palette().buttonText().color()


def _triangle(path, x, y, width, height, pointing_right=True):
    if pointing_right:
        path.moveTo(x, y)
        path.lineTo(x + width, y + height / 2)
        path.lineTo(x, y + height)
    else:
        path.moveTo(x + width, y)
        path.lineTo(x, y + height / 2)
        path.lineTo(x + width, y + height)
    path.closeSubpath()


def _shape(name):
    """The glyph as a path on a 16x16 grid."""
    path = QPainterPath()
    if name == "play":
        _triangle(path, 4.5, 2.5, 9.5, 11)
    elif name == "pause":
        path.addRoundedRect(QRectF(3.5, 2.5, 3.2, 11), 1, 1)
        path.addRoundedRect(QRectF(9.3, 2.5, 3.2, 11), 1, 1)
    elif name == "stop":
        path.addRoundedRect(QRectF(3, 3, 10, 10), 1.5, 1.5)
    elif name == "previous":
        path.addRoundedRect(QRectF(2.5, 3, 2.2, 10), 0.8, 0.8)
        _triangle(path, 5.5, 3, 8, 10, pointing_right=False)
    elif name == "next":
        _triangle(path, 2.5, 3, 8, 10)
        path.addRoundedRect(QRectF(11.3, 3, 2.2, 10), 0.8, 0.8)
    elif name == "first":
        path.addRoundedRect(QRectF(1.5, 3, 2, 10), 0.8, 0.8)
        _triangle(path, 4, 3, 5.5, 10, pointing_right=False)
        _triangle(path, 9, 3, 5.5, 10, pointing_right=False)
    elif name == "last":
        _triangle(path, 1.5, 3, 5.5, 10)
        _triangle(path, 6.5, 3, 5.5, 10)
        path.addRoundedRect(QRectF(12.5, 3, 2, 10), 0.8, 0.8)
    elif name == "step_back":
        _triangle(path, 4.5, 3, 7, 10, pointing_right=False)
    elif name == "step_forward":
        _triangle(path, 4.5, 3, 7, 10)
    elif name in ("undo", "redo"):
        # An arrow curling back on itself: a stroked arc for the curl,
        # a filled head on the end it points at. Mirrored for redo, so
        # the pair reads as one gesture in two directions.
        back = name == "undo"
        curl = QPainterPath()
        curl.moveTo(3.0, 9.5)
        curl.cubicTo(3.0, 3.0, 13.0, 3.0, 13.0, 9.5)
        stroked = QPainterPathStroker()
        stroked.setWidth(2.0)
        stroked.setCapStyle(Qt.PenCapStyle.RoundCap)
        arc = stroked.createStroke(curl)
        head = QPainterPath()
        tip = 3.0 if back else 13.0
        head.moveTo(tip, 13.0)
        head.lineTo(tip - 3.4, 8.2)
        head.lineTo(tip + 3.4, 8.2)
        head.closeSubpath()
        path = arc.united(head)
    return path


def icon(name, colour=None):
    """A QIcon of glyph `name`, in `colour` or the theme's."""
    ratio = 1.0
    screen = QApplication.primaryScreen()
    if screen is not None:
        ratio = max(1.0, screen.devicePixelRatio())
    side = round(GLYPH_SIZE * ratio * 2)
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(side / GLYPH_SIZE, side / GLYPH_SIZE)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour or _colour(name))
    painter.drawPath(_shape(name))
    painter.end()
    pixmap.setDevicePixelRatio(side / GLYPH_SIZE)
    return QIcon(pixmap)


def set_glyph(button, name, tooltip=None):
    """Make `button` show glyph `name` and no text. Call again to change it
    - a play button turning into pause."""
    button.setText("")
    button.setProperty(theme.GLYPH_PROPERTY, name)
    button.setIcon(icon(name))
    button.setIconSize(QSize(GLYPH_SIZE, GLYPH_SIZE))
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setToolTip(tooltip or NAMES.get(name, name))
    button.setAccessibleName(NAMES.get(name, name))
    # A property selector in the stylesheet only notices a change on repolish.
    style = button.style()
    style.unpolish(button)
    style.polish(button)


def refresh(button):
    """Repaint a glyph button's icon in the current theme's colours."""
    name = button.property(theme.GLYPH_PROPERTY)
    if name:
        button.setIcon(icon(name))

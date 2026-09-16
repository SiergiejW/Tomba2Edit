"""A row's checkbox, drawn as a dot in the modern theme.

The item stays an ordinary checkable one - Checked is shown, Unchecked is
hidden - so everything that reads or sets the check state is untouched. Under
gui/theme.py's "modern" theme the box is painted as a filled dot in the row's
colour (DOT_COLOR), and as a hollow ring of that colour once unchecked;
clicking the dot flips it. Other themes get the native checkbox.
"""
from PyQt6.QtCore import QEvent, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from gui import theme

# The item data role holding the dot's colour: a QColor, or (r, g, b) in 0..1.
DOT_COLOR = Qt.ItemDataRole.UserRole + 41
DOT_SIZE = 10
DOT_MARGIN = 8          # left of the dot, and between it and the text
RING_WIDTH = 1.6


def _color(value):
    if isinstance(value, QColor):
        return value
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return QColor.fromRgbF(*(max(0.0, min(1.0, float(v))) for v in value[:3]))
    return QColor(theme.colours()["dim"])


def _checked(index):
    # PyQt6 hands the role back as the enum or its int, depending on who set it.
    state = index.data(Qt.ItemDataRole.CheckStateRole)
    return state in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)


class DotDelegate(QStyledItemDelegate):
    def _dotted(self, index):
        return (theme.is_modern()
                and index.flags() & Qt.ItemFlag.ItemIsUserCheckable
                and index.data(Qt.ItemDataRole.CheckStateRole) is not None)

    @staticmethod
    def _dot_rect(rect):
        top = rect.top() + (rect.height() - DOT_SIZE) // 2
        return QRect(rect.left() + DOT_MARGIN, top, DOT_SIZE, DOT_SIZE)

    def paint(self, painter, option, index):
        if not self._dotted(index):
            super().paint(painter, option, index)
            return
        opt = option.__class__(option)
        self.initStyleOption(opt, index)
        # The row's own background, selection and text, without the box.
        opt.features &= ~opt.ViewItemFeature.HasCheckIndicator
        shown = _checked(index)
        opt.rect = option.rect.adjusted(DOT_MARGIN * 2 + DOT_SIZE, 0, 0, 0)
        if not shown:
            opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.colours()["dim"]))
        widget = option.widget
        style = widget.style() if widget is not None else None
        # Background across the whole row first, the dot's column included.
        background = option.__class__(opt)
        background.rect = option.rect
        background.text = ""
        if style is not None:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem,
                                background, painter, widget)
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        color = _color(index.data(DOT_COLOR))
        rect = QRectF(self._dot_rect(option.rect))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if shown:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, RING_WIDTH))
            inset = RING_WIDTH / 2
            painter.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if self._dotted(index):
            size.setHeight(max(size.height(), DOT_SIZE + 12))
        return size

    def editorEvent(self, event, model, option, index):
        if not self._dotted(index):
            return super().editorEvent(event, model, option, index)
        if event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                                QEvent.Type.MouseButtonDblClick):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        hit = self._dot_rect(option.rect).adjusted(-DOT_MARGIN // 2, -4, DOT_MARGIN // 2, 4)
        if not hit.contains(event.position().toPoint()):
            return False
        # Swallow the press and double-click so the row isn't selected or
        # opened; flip on release.
        if event.type() == QEvent.Type.MouseButtonRelease:
            model.setData(index, Qt.CheckState.Unchecked if _checked(index) else Qt.CheckState.Checked,
                          Qt.ItemDataRole.CheckStateRole)
        return True

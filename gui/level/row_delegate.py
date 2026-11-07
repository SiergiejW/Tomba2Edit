"""The level list's first column drawn as a tree.

Each row shows, left to right: guide lines from its parent, a fold arrow if
it has children, an eye (its check state - open is shown, struck out is
hidden), a dot in its class colour, and its name. The items stay ordinary
checkable ones, so everything that reads the check state is untouched.
"""
from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from gui import theme
from gui.widgets.dot_delegate import DOT_COLOR, _checked, _color

BASE = Qt.ItemDataRole.UserRole + 60
DEPTH = BASE            # how deep the row sits
HAS_CHILDREN = BASE + 1
FOLDED = BASE + 2
LAST = BASE + 3         # the last of its parent's children
CONTINUES = BASE + 4    # per ancestor level: does its line carry on past here

INDENT = 22             # one level of the tree
ARROW = 12              # the fold arrow's size
EYE_W, EYE_H = 18, 11
DOT = 10
GAP = 6
LEFT = 6
ROW_HEIGHT = 26


class RowDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, on_fold=None):
        super().__init__(parent)
        self.on_fold = on_fold      # called with the row's data when its arrow is hit

    # --- layout ---------------------------------------------------------

    @staticmethod
    def _depth(index):
        return int(index.data(DEPTH) or 0)

    @staticmethod
    def _tree_left(rect):
        """Where the tree starts: past the eye, which is always at the edge."""
        return rect.left() + LEFT + EYE_W + GAP

    def _eye_rect(self, rect, index):
        return QRect(rect.left() + LEFT, rect.top() + (rect.height() - EYE_H) // 2,
                     EYE_W, EYE_H)

    def _arrow_rect(self, rect, index):
        x = self._tree_left(rect) + self._depth(index) * INDENT
        return QRect(x, rect.top(), INDENT, rect.height())

    def _dot_rect(self, rect, index):
        x = self._arrow_rect(rect, index).right() + 1
        return QRect(x, rect.top() + (rect.height() - DOT) // 2, DOT, DOT)

    def _text_left(self, rect, index):
        return self._dot_rect(rect, index).right() + GAP + 2

    # --- painting -------------------------------------------------------

    def paint(self, painter, option, index):
        opt = option.__class__(option)
        self.initStyleOption(opt, index)
        opt.features &= ~opt.ViewItemFeature.HasCheckIndicator
        text = opt.text
        opt.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        rect = option.rect
        shown = _checked(index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        palette = option.palette
        ink = palette.color(palette.ColorRole.HighlightedText if selected
                            else palette.ColorRole.Text)
        dim = QColor(ink)
        dim.setAlphaF(0.45)
        guide = QColor(ink)
        guide.setAlphaF(0.35)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._guides(painter, rect, index, guide)
        self._arrow(painter, rect, index, ink)
        if index.data(Qt.ItemDataRole.CheckStateRole) is not None:
            self._eye(painter, self._eye_rect(rect, index), ink if shown else dim, shown)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_color(index.data(DOT_COLOR)))
        painter.drawEllipse(QRectF(self._dot_rect(rect, index)))
        painter.setPen(ink if shown else dim)
        left = self._text_left(rect, index)
        text_rect = QRect(left, rect.top(), max(rect.right() - left - 4, 0), rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                          text_rect.width()))
        painter.restore()

    def _guides(self, painter, rect, index, colour):
        """Lines from the parent down the left of the children."""
        depth = self._depth(index)
        painter.setPen(QPen(colour, 1))
        middle = rect.top() + rect.height() // 2
        # The arrow's centre is the line's x for what is under it.
        centre = lambda level: self._tree_left(rect) + level * INDENT + INDENT // 2
        flags = index.data(CONTINUES) or ()
        for level, goes_on in enumerate(flags):
            if goes_on:
                painter.drawLine(centre(level), rect.top(), centre(level), rect.bottom() + 1)
        if depth:
            x = centre(depth - 1)
            last = bool(index.data(LAST))
            painter.drawLine(x, rect.top(), x, middle if last else rect.bottom() + 1)
            reach = self._arrow_rect(rect, index).left() + (
                2 if index.data(HAS_CHILDREN) else INDENT - 2)
            painter.drawLine(x, middle, reach, middle)
        if index.data(HAS_CHILDREN) and not index.data(FOLDED):
            x = centre(depth)
            painter.drawLine(x, middle + ARROW // 2 + 1, x, rect.bottom() + 1)

    def _arrow(self, painter, rect, index, colour):
        if not index.data(HAS_CHILDREN):
            return
        cell = self._arrow_rect(rect, index)
        cx, cy = cell.center().x() + 0.5, cell.center().y() + 0.5
        half = ARROW / 2
        if index.data(FOLDED):          # pointing right
            points = [QPointF(cx - half / 2, cy - half), QPointF(cx - half / 2, cy + half),
                      QPointF(cx + half * 0.75, cy)]
        else:                           # pointing down
            points = [QPointF(cx - half, cy - half / 2), QPointF(cx + half, cy - half / 2),
                      QPointF(cx, cy + half * 0.75)]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPolygon(QPolygonF(points))

    @staticmethod
    def _eye(painter, rect, colour, shown):
        r = QRectF(rect)
        mid = r.center().y()
        almond = QPainterPath(QPointF(r.left(), mid))
        almond.quadTo(QPointF(r.center().x(), r.top() - r.height() * 0.45),
                      QPointF(r.right(), mid))
        almond.quadTo(QPointF(r.center().x(), r.bottom() + r.height() * 0.45),
                      QPointF(r.left(), mid))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(colour, 1.0))
        painter.drawPath(almond)
        if shown:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(r.center(), 2.2, 2.2)
        else:
            painter.setPen(QPen(colour, 1.1))
            painter.drawLine(QPointF(r.left() + 2, r.bottom() + 1.5),
                             QPointF(r.right() - 2, r.top() - 1.5))

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), ROW_HEIGHT))
        return size

    # --- clicks ---------------------------------------------------------

    def editorEvent(self, event, model, option, index):
        if event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                                QEvent.Type.MouseButtonDblClick):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        point = event.position().toPoint()
        eye = self._eye_rect(option.rect, index).adjusted(-4, -6, 4, 6)
        arrow = self._arrow_rect(option.rect, index)
        on_arrow = bool(index.data(HAS_CHILDREN)) and arrow.contains(point)
        has_eye = index.data(Qt.ItemDataRole.CheckStateRole) is not None
        if not ((has_eye and eye.contains(point)) or on_arrow):
            return False
        # Swallow the press and double-click so the row is not selected or
        # framed; act on release.
        if event.type() == QEvent.Type.MouseButtonRelease:
            if on_arrow:
                if self.on_fold is not None:
                    self.on_fold(index)
            else:
                model.setData(index, Qt.CheckState.Unchecked if _checked(index)
                              else Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        return True

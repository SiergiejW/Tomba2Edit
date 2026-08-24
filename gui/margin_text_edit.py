"""
Shared QTextEdit subclass that draws a vertical guide line at a fixed
character column - marks where a screen's on-screen width limit falls,
so overflowing it is visible while typing, not just reported after the
fact in a status line below. Used by SopViewer and TXTDViewer.
"""

from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QTextEdit


class MarginTextEdit(QTextEdit):
    def __init__(self, column, parent=None):
        super().__init__(parent)
        self.margin_column = column

    def paintEvent(self, event):
        super().paintEvent(event)
        char_width = self.fontMetrics().horizontalAdvance("0")
        x = self.document().documentMargin() + self.margin_column * char_width
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#666666"), 1))
        painter.drawLine(int(x), 0, int(x), self.viewport().height())
        painter.end()

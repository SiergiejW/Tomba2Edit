"""The picture, and the bar underneath it that says where you are.

Kept apart from the panel because both are only about showing a frame -
scaling it to whatever room the window has without stretching it out of
shape, and turning a frame number into a time - and neither needs to
know where the frames come from.
"""
from PyQt6.QtCore import Qt, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

# The movies are 320x240 on a screen that showed them at 4:3, which is
# the same shape, so a plain aspect-preserving fit is right.
BACKGROUND = QColor(18, 18, 18)


def to_image(rgb):
    """An (h, w, 3) uint8 array as a QImage that owns its own bytes."""
    height, width, _ = rgb.shape
    data = rgb.tobytes()
    return QImage(data, width, height, 3 * width,
                  QImage.Format.Format_RGB888).copy()


def clock(seconds):
    """Seconds as m:ss.t - tenths, because a frame is a thirtieth."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    minutes, rest = divmod(float(seconds), 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


class MovieScreen(QWidget):
    """Shows one frame, scaled to fit and centred on a dark ground."""

    double_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._message = "No movie selected"
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def show_frame(self, rgb):
        self._pixmap = QPixmap.fromImage(to_image(rgb))
        self._message = ""
        self.update()

    def show_message(self, text):
        self._pixmap = None
        self._message = text
        self.update()

    def frame_pixmap(self):
        return self._pixmap

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)
        if self._pixmap is None:
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             self._message)
            return
        # Smooth on the way up would blur what is a deliberately soft
        # 320x240 picture into mush; fast keeps the pixels honest.
        scaled = self._pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation)
        painter.drawPixmap(
            QRect((self.width() - scaled.width()) // 2,
                  (self.height() - scaled.height()) // 2,
                  scaled.width(), scaled.height()),
            scaled)

    def mouseDoubleClickEvent(self, _event):
        self.double_clicked.emit()

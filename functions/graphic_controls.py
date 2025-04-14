# functions/2D_controls.py

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtWidgets import QApplication


def update_pixmap(self, preserve_position=True, immediate=False):
    if not self.original_pixmap:
        return

    scroll_area = self.scroll_area
    h_scroll = scroll_area.horizontalScrollBar()
    v_scroll = scroll_area.verticalScrollBar()

    if preserve_position and not self.is_stretched and h_scroll.maximum() > 0 and v_scroll.maximum() > 0:
        old_center_x = h_scroll.value() + h_scroll.pageStep() / 2
        old_center_y = v_scroll.value() + v_scroll.pageStep() / 2
        old_center_ratio_x = old_center_x / h_scroll.maximum()
        old_center_ratio_y = old_center_y / v_scroll.maximum()
    else:
        preserve_position = False

    if self.is_stretched:
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        viewport_size = self.scroll_area.viewport().size()
        self.image_label.setPixmap(self.original_pixmap.scaled(
            viewport_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation
        ))
        self.image_label.setFixedSize(viewport_size)
    else:
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.image_label.setMaximumSize(16777215, 16777215)
        self.image_label.setMinimumSize(0, 0)

        if self.zoom_factor == 1.0:
            new_size = self.original_pixmap.size()
        else:
            new_size = QSize(
                int(self.original_pixmap.width() * self.zoom_factor),
                int(self.original_pixmap.height() * self.zoom_factor))

        self.image_label.resize(new_size)
        QApplication.processEvents()

        if self.zoom_factor == 1.0:
            new_pixmap = self.original_pixmap
        else:
            new_pixmap = self.original_pixmap.scaled(
                new_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation)

        self.image_label.setPixmap(new_pixmap)

    self.image_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    if preserve_position:
        def deferred_scroll():
            QApplication.processEvents()
            new_h_max = h_scroll.maximum()
            new_v_max = v_scroll.maximum()
            if new_h_max > 0 and new_v_max > 0:
                target_x = old_center_ratio_x * new_h_max - h_scroll.pageStep() / 2
                target_y = old_center_ratio_y * new_v_max - v_scroll.pageStep() / 2
                h_scroll.setValue(int(max(0, min(new_h_max, target_x))))
                v_scroll.setValue(int(max(0, min(new_v_max, target_y))))

        if immediate:
            deferred_scroll()
        else:
            QTimer.singleShot(0, deferred_scroll)


def reset_zoom(self):
    self.is_stretched = False
    self.zoom_factor = 1.0
    self.update_pixmap(preserve_position=False)
    self.scroll_area.horizontalScrollBar().setValue(0)
    self.scroll_area.verticalScrollBar().setValue(0)


def set_stretched(self):
    self.is_stretched = True
    self.zoom_factor = 1.0
    self.update_pixmap(preserve_position=False)


def zoom_by(self, direction):
    if self.is_stretched:
        self.is_stretched = False

    zoom_change = 0.25 if direction > 0 else -0.25
    new_zoom = max(0.1, min(10.0, self.zoom_factor + zoom_change))

    if new_zoom != self.zoom_factor:
        self.zoom_factor = new_zoom
        self.update_pixmap(preserve_position=True, immediate=True)
        self.update_pixmap(preserve_position=True, immediate=False)


def resizeEvent(self, event):
    if self.is_stretched:
        self.update_pixmap(preserve_position=False)
    super(type(self), self).resizeEvent(event)

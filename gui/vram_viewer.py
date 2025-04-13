from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QPushButton, QSizePolicy
)
from PyQt6.QtGui import QImage, QPixmap, QMouseEvent, QPainter
from PyQt6.QtWidgets import QApplication  # For QWIDGETSIZE_MAX
from PIL import Image
import io
import struct


class VRAMViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.zoom_factor = 1.0
        self.original_pixmap = None
        self.drag_start_pos = None
        self.scroll_start_pos = None

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.setLayout(self.layout)

        # Top bar with zoom controls
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(10, 5, 10, 5)
        top_bar.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.original_btn = QPushButton("Original Size")
        self.stretched_btn = QPushButton("Stretched")
        self.zoom_in_btn = QPushButton("Zoom In")
        self.zoom_out_btn = QPushButton("Zoom Out")

        self.original_btn.clicked.connect(self.reset_zoom)
        self.stretched_btn.clicked.connect(self.set_stretched)
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_by(1))
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_by(-1))

        top_bar.addWidget(self.original_btn)
        top_bar.addWidget(self.stretched_btn)
        top_bar.addWidget(self.zoom_in_btn)
        top_bar.addWidget(self.zoom_out_btn)
        self.layout.addLayout(top_bar)

        # Scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.layout.addWidget(self.scroll_area, stretch=1)

        self.image_label = ZoomableLabel(self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.image_label.setScaledContents(False)
        self.scroll_area.setWidget(self.image_label)

        # Bottom info label
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(10, 5, 10, 5)
        bottom_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.info_label = QLabel("VRAM Viewer")
        bottom_layout.addWidget(self.info_label)
        self.layout.addLayout(bottom_layout)

        self.is_stretched = False  # Default: original size with zoom

    def load_vram_data(self, img_data):
        try:
            vram_image = self.process_vram(img_data)

            if vram_image.mode == "RGB":
                qimage = QImage(vram_image.tobytes(),
                                vram_image.width,
                                vram_image.height,
                                QImage.Format.Format_RGB888)
            else:
                qimage = QImage(vram_image.tobytes(),
                                vram_image.width,
                                vram_image.height,
                                QImage.Format.Format_RGBA8888)

            self.original_pixmap = QPixmap.fromImage(qimage)
            self.reset_zoom()
            self.info_label.setText("VRAM Image Loaded")
            return True
        except Exception as e:
            self.info_label.setText(f"Error loading VRAM: {str(e)}")
            return False

    def update_pixmap(self):
        if not self.original_pixmap:
            return

        if self.is_stretched:
            # Stretched mode - scale to fit viewport
            self.scroll_area.setWidgetResizable(False)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

            viewport_size = self.scroll_area.viewport().size()
            scaled_pixmap = self.original_pixmap.scaled(
                viewport_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation
            )
            self.image_label.setPixmap(scaled_pixmap)
            self.image_label.setFixedSize(viewport_size)
            self.image_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        else:
            # Original/Zoom mode
            self.scroll_area.setWidgetResizable(False)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

            # Remove any size constraints
            self.image_label.setMaximumSize(16777215, 16777215)
            self.image_label.setMinimumSize(0, 0)

            # Apply current zoom
            if self.zoom_factor == 1.0:
                # Original size
                self.image_label.setPixmap(self.original_pixmap)
                self.image_label.resize(self.original_pixmap.size())
            else:
                # Zoomed size
                scaled = self.original_pixmap.scaled(
                    int(self.original_pixmap.width() * self.zoom_factor),
                    int(self.original_pixmap.height() * self.zoom_factor),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.FastTransformation
                )
                self.image_label.setPixmap(scaled)
                self.image_label.resize(scaled.size())

            self.image_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

            # Force update of scrollbar ranges before positioning
            QApplication.processEvents()

    def reset_zoom(self):
        self.is_stretched = False
        self.zoom_factor = 1.0
        self.update_pixmap()

    def set_stretched(self):
        self.is_stretched = True
        self.zoom_factor = 1.0  # Reset zoom when entering stretched mode
        self.update_pixmap()

    def zoom_by(self, direction):
        if self.is_stretched:
            self.is_stretched = False  # Exit stretched mode when zooming

        # Get current scroll positions (as ratios of total possible scroll)
        scroll_area = self.scroll_area
        h_scroll = scroll_area.horizontalScrollBar()
        v_scroll = scroll_area.verticalScrollBar()

        # Calculate current center position (0-1 range)
        if h_scroll.maximum() > 0:
            center_x = h_scroll.value() / h_scroll.maximum()
        else:
            center_x = 0.5

        if v_scroll.maximum() > 0:
            center_y = v_scroll.value() / v_scroll.maximum()
        else:
            center_y = 0.5

        # Apply zoom change
        zoom_change = 0.25 if direction > 0 else -0.25
        new_zoom = max(0.1, min(10.0, self.zoom_factor + zoom_change))

        if new_zoom != self.zoom_factor:
            self.zoom_factor = new_zoom
            self.update_pixmap()

            # Restore center position after zoom
            h_scroll.setValue(int(center_x * h_scroll.maximum()))
            v_scroll.setValue(int(center_y * v_scroll.maximum()))

    def resizeEvent(self, event):
        if self.is_stretched:
            self.update_pixmap()
        super().resizeEvent(event)

    def process_vram(self, img_data):
        img_file = io.BytesIO(img_data)
        c_header_amount = struct.unpack("<I", img_file.read(4))[0]
        c_header_size = c_header_amount * 0xC + 4
        skip = 0x800 - c_header_size

        c_header_list = [struct.unpack("<HHHHI", img_file.read(12)) for _ in range(c_header_amount)]
        img_file.read(skip)

        vram_image = Image.new("RGB", (1024, 512), (0, 0, 0))

        for x, y, w, h, s in c_header_list:
            shard_data = bytearray()
            scompare = 0

            while scompare < s:
                base = img_file.read(1)[0]
                scompare += 1
                amount = base >> 3
                extra = base & 7

                if extra == 0:
                    shard_data.extend(img_file.read(amount))
                    scompare += amount
                else:
                    for _ in range(amount):
                        if len(shard_data) >= 2:
                            ref_pos = len(shard_data) + \
                                      [-0, -1, -w * 2, -w * 2 - 1, -w * 2 - 2, -w * 2 - 3, -w * 2 + 1, -w * 2 + 2][
                                          extra]
                            shard_data.append(shard_data[ref_pos] if ref_pos >= 0 else 0)
                        else:
                            shard_data.append(0)

            for row in range(h):
                for col in range(w):
                    pos = (row * w + col) * 2
                    if pos + 1 < len(shard_data):
                        color16 = (shard_data[pos + 1] << 8) | shard_data[pos]
                        r = ((color16 >> 11) & 0x1F) << 3
                        g = ((color16 >> 5) & 0x3F) << 2
                        b = (color16 & 0x1F) << 3
                        vram_image.putpixel((x + col, y + row), (r, g, b))

        return vram_image


class ZoomableLabel(QLabel):
    def __init__(self, parent_viewer):
        super().__init__()
        self.parent_viewer = parent_viewer
        self.drag_start_pos = None
        self.scroll_start_pos = None

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self.parent_viewer.is_stretched:
            self.drag_start_pos = event.pos()
            scroll_area = self.parent_viewer.scroll_area
            self.scroll_start_pos = QPoint(
                scroll_area.horizontalScrollBar().value(),
                scroll_area.verticalScrollBar().value()
            )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if (self.drag_start_pos is not None and
                self.scroll_start_pos is not None and
                not self.parent_viewer.is_stretched):
            delta = event.pos() - self.drag_start_pos
            scroll_area = self.parent_viewer.scroll_area
            scroll_bar_h = scroll_area.horizontalScrollBar()
            scroll_bar_v = scroll_area.verticalScrollBar()

            scroll_bar_h.setValue(self.scroll_start_pos.x() - delta.x())
            scroll_bar_v.setValue(self.scroll_start_pos.y() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = None
            self.scroll_start_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)  # Nearest neighbor
        if self.pixmap():
            painter.drawPixmap(self.rect(), self.pixmap())
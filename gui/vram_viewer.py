# gui/vram_viewer.py

from PyQt6.QtCore import Qt, QPoint, QSize, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QPushButton, QSizePolicy, QApplication
)
from PyQt6.QtGui import QImage, QPixmap, QMouseEvent, QPainter
from PIL import Image
import io
import struct
# 🔗 Attach external zoom-related methods to VRAMViewer
import functions.graphic_controls as ctrl
from PIL.ImageQt import ImageQt  # Import ImageQt for converting PIL images to QPixmap

class VRAMViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.zoom_factor = 1.0
        self.original_pixmap = None
        self.drag_start_pos = None
        self.scroll_start_pos = None
        self.pending_zoom = None

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.setLayout(self.layout)

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

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.layout.addWidget(self.scroll_area, stretch=1)

        self.image_label = ZoomableLabel(self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.image_label.setScaledContents(False)
        self.scroll_area.setWidget(self.image_label)

        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(10, 5, 10, 5)
        bottom_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.info_label = QLabel("VRAM Viewer")
        bottom_layout.addWidget(self.info_label)
        self.layout.addLayout(bottom_layout)

        self.is_stretched = True

    def load_vram_data(self, img_data):
        try:
            result = self.process_vram(img_data)
            if isinstance(result, tuple):
                vram_image, vram_bytes = result
            else:
                vram_image = result
                vram_bytes = None

            qimage = ImageQt(vram_image).copy()  # ✅ Safe conversion

            self.original_pixmap = QPixmap.fromImage(qimage)
            self.update_pixmap()
            self.set_stretched()  # Changed from self.reset_zoom()
            self.info_label.setText("VRAM Image Loaded")
            if hasattr(self, 'mdat_viewer') and self.mdat_viewer:
                self.mdat_viewer.set_vram_image(qimage, vram_bytes)

            return True
        except Exception as e:
            self.info_label.setText(f"Error loading VRAM: {str(e)}")
            return False

    def load_cvrm_data(self, img_data):
        try:
            img_file = io.BytesIO(img_data)
            c_header_amount = struct.unpack("<I", img_file.read(4))[0]
            c_header_size = c_header_amount * 0xC + 4
            skip = 0x800 - c_header_size

            c_header_list = [struct.unpack("<HHHHI", img_file.read(12)) for _ in range(c_header_amount)]
            img_file.read(skip)

            # Create a blank 4096x512 RGBA image
            vram_image = Image.new("RGBA", (4096, 512))

            # Now, paste compressed shards directly
            for x, y, w, h, s in c_header_list:
                shard_raw = img_file.read(s)

                # Simple method: paste shard raw data block by block
                # 1 pixel = 1 byte (not real compression expansion)
                for row in range(h):
                    for col in range(w):
                        index = (row * w + col) * 2
                        if index + 1 < len(shard_raw):
                            pixel = shard_raw[index:index + 2]
                            val = struct.unpack("<H", pixel)[0]

                            # Fake colorize: show (R, G, B) as some split of bits
                            r = (val >> 10) & 0x1F
                            g = (val >> 5) & 0x1F
                            b = val & 0x1F

                            # Expand to 8-bit color channels
                            r = (r << 3) | (r >> 2)
                            g = (g << 3) | (g >> 2)
                            b = (b << 3) | (b >> 2)

                            vram_image.putpixel((x * 2 + col * 2, y + row), (r, g, b, 255))
                            vram_image.putpixel((x * 2 + col * 2 + 1, y + row), (r, g, b, 255))

            # Load into viewer
            qimage = ImageQt(vram_image).copy()
            self.original_pixmap = QPixmap.fromImage(qimage)
            self.update_pixmap()
            self.set_stretched()
            self.info_label.setText("CVRAM Loaded (raw colored)")
            return True

        except Exception as e:
            self.info_label.setText(f"Error loading CVRAM: {str(e)}")
            return False

    def process_vram(self, img_data):
        import io
        from PIL import Image
        import struct

        img_file = io.BytesIO(img_data)
        c_header_amount = struct.unpack("<I", img_file.read(4))[0]
        c_header_size = c_header_amount * 0xC + 4
        skip = 0x800 - c_header_size

        c_header_list = [struct.unpack("<HHHHI", img_file.read(12)) for _ in range(c_header_amount)]
        img_file.read(skip)

        # Step 1: Simulate raw 1MB VRAM as bytearray (1024x512 x 2 bytes per pixel)
        vram_bytes = bytearray(1024 * 512 * 2)  # 1MB = 524288 words = 1024x512 x 2

        for x, y, w, h, s in c_header_list:
            shard_data = bytearray()
            scompare = 0
            lz = w * 2
            extras = [0, -1, -lz, -lz - 1, -lz - 2, -lz - 3, -lz + 1, -lz + 2]

            # Decompression logic (LZ-style with backrefs)
            while scompare < s:
                control_byte = img_file.read(1)
                if not control_byte:
                    break
                control = control_byte[0]
                scompare += 1

                amount = control >> 3
                extra = control & 0x07

                if extra == 0:
                    chunk = img_file.read(amount)
                    shard_data.extend(chunk)
                    scompare += amount
                else:
                    ref_offset = extras[extra]
                    for _ in range(amount):
                        ref_pos = len(shard_data) + ref_offset
                        if 0 <= ref_pos < len(shard_data):
                            shard_data.append(shard_data[ref_pos])
                        else:
                            shard_data.append(0)

            # Step 2: Copy shard into simulated VRAM respecting 0x800-byte row stride
            for row in range(h):
                shard_start = row * w * 2
                shard_end = shard_start + w * 2
                vram_offset = (y + row) * 0x800 + (x * 2)
                vram_bytes[vram_offset:vram_offset + w * 2] = shard_data[shard_start:shard_end]

        # Step 3: Convert 4bpp VRAM into RGBA image (4096x512)
        vram_image = Image.new("RGBA", (4096, 512))
        for y in range(512):
            for x in range(0, 4096, 2):  # 2 pixels per byte
                byte_index = y * 0x800 + (x // 2)
                if byte_index >= len(vram_bytes):
                    continue
                byte = vram_bytes[byte_index]
                low = byte & 0x0F
                high = (byte >> 4) & 0x0F
                g1 = low * 17
                g2 = high * 17
                vram_image.putpixel((x, y), (g1, g1, g1, 255))
                vram_image.putpixel((x + 1, y), (g2, g2, g2, 255))


        return vram_image, vram_bytes


class ZoomableLabel(QLabel):
    def __init__(self, parent_viewer):
        super().__init__()
        self.parent_viewer = parent_viewer
        self.drag_start_pos = None
        self.scroll_start_pos = None
        self.last_pos = None

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self.parent_viewer.is_stretched:
            self.drag_start_pos = event.pos()
            scroll_area = self.parent_viewer.scroll_area
            self.scroll_start_pos = QPoint(
                scroll_area.horizontalScrollBar().value(),
                scroll_area.verticalScrollBar().value()
            )
            self.last_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if (self.drag_start_pos is not None and
                self.scroll_start_pos is not None and
                not self.parent_viewer.is_stretched):
            delta = event.pos() - self.last_pos
            self.last_pos = event.pos()

            scroll_area = self.parent_viewer.scroll_area
            scroll_bar_h = scroll_area.horizontalScrollBar()
            scroll_bar_v = scroll_area.verticalScrollBar()

            scroll_bar_h.setValue(scroll_bar_h.value() - delta.x())
            scroll_bar_v.setValue(scroll_bar_v.value() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = None
            self.scroll_start_pos = None
            self.last_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if self.pixmap():
            painter.drawPixmap(self.rect(), self.pixmap())




for method_name in ['reset_zoom', 'set_stretched', 'zoom_by', 'update_pixmap', 'resizeEvent']:
    setattr(VRAMViewer, method_name, getattr(ctrl, method_name))


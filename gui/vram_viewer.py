from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QScrollArea
from PyQt6.QtGui import QImage, QPixmap, QMouseEvent
from PIL import Image
import io
import struct


class VRAMViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.setLayout(self.layout)

        # Scroll area to allow toggling scrollbars
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.image_label = ClickableLabel(self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setScaledContents(False)  # we manage scaling manually
        self.scroll_area.setWidget(self.image_label)

        self.layout.addWidget(self.scroll_area, stretch=1)

        # Bottom-left info label
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(10, 5, 10, 5)
        bottom_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.info_label = QLabel("VRAM Viewer")
        bottom_layout.addWidget(self.info_label)
        self.layout.addLayout(bottom_layout)

        self.original_pixmap = None
        self.is_scaled = True  # Default: stretch to fit

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
            self.update_scaled_pixmap()
            self.info_label.setText("VRAM Image Loaded")
            return True
        except Exception as e:
            self.info_label.setText(f"Error loading VRAM: {str(e)}")
            return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scaled_pixmap()

    def update_scaled_pixmap(self):
        if not self.original_pixmap:
            return

        if self.is_scaled:
            # Hide scrollbars
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

            # Scale to fit
            container_size = self.scroll_area.viewport().size()
            scaled_pixmap = self.original_pixmap.scaled(
                container_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation  # Nearest neighbor
            )
            self.image_label.setPixmap(scaled_pixmap)
        else:
            # Show scrollbars if needed
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

            # Use 1:1 size (still apply FastTransformation just to be explicit)
            self.image_label.setPixmap(self.original_pixmap.scaled(
                self.original_pixmap.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation
            ))

    def toggle_scale(self):
        self.is_scaled = not self.is_scaled
        self.update_scaled_pixmap()

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
                                [-0, -1, -w * 2, -w * 2 - 1, -w * 2 - 2, -w * 2 - 3, -w * 2 + 1, -w * 2 + 2][extra]
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


class ClickableLabel(QLabel):
    def __init__(self, parent_viewer):
        super().__init__()
        self.parent_viewer = parent_viewer

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parent_viewer.toggle_scale()

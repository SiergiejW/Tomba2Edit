from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtGui import QImage, QPixmap
from PIL import Image
import io
import struct


class VRAMViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.image_label)

        self.info_label = QLabel("VRAM Viewer")
        self.layout.addWidget(self.info_label)

    def load_vram_data(self, img_data):
        try:
            # Process the VRAM data in memory
            vram_image = self.process_vram(img_data)

            # Convert PIL Image to QImage
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

            self.image_label.setPixmap(QPixmap.fromImage(qimage))
            self.info_label.setText("VRAM Image Loaded")
            return True
        except Exception as e:
            self.info_label.setText(f"Error loading VRAM: {str(e)}")
            return False

    def process_vram(self, img_data):
        # This is a simplified version of your VRAM processing code
        # that works entirely in memory

        # Create a memory file-like object
        img_file = io.BytesIO(img_data)

        # Read header
        c_header_amount = struct.unpack("<I", img_file.read(4))[0]
        c_header_size = c_header_amount * 0xC + 4
        skip = 0x800 - c_header_size

        # Read headers
        c_header_list = []
        for _ in range(c_header_amount):
            c_header = struct.unpack("<HHHHI", img_file.read(12))
            c_header_list.append(c_header)

        img_file.read(skip)  # Skip to next sector

        # Create blank VRAM image (1024x512, 16-bit color)
        vram_image = Image.new("RGB", (1024, 512), (0, 0, 0))

        # Process each shard
        for header in c_header_list:
            x, y, w, h, s = header
            shard_data = bytearray()

            # Decompress shard data
            scompare = 0
            while scompare < s:
                base = img_file.read(1)[0]
                scompare += 1
                if scompare >= s:
                    break

                amount = base >> 3
                extra = base & 7

                if extra == 0:
                    shard_data.extend(img_file.read(amount))
                    scompare += amount
                    if scompare >= s:
                        break
                else:
                    # Handle RLE compression
                    for _ in range(amount):
                        if len(shard_data) >= 2:
                            ref_pos = len(shard_data) + \
                                      [-0, -1, -w * 2, -w * 2 - 1, -w * 2 - 2, -w * 2 - 3, -w * 2 + 1, -w * 2 + 2][
                                          extra]
                            if ref_pos >= 0:
                                shard_data.append(shard_data[ref_pos])
                            else:
                                shard_data.append(0)
                        else:
                            shard_data.append(0)

            # Convert 16-bit color to 24-bit RGB and place in VRAM image
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
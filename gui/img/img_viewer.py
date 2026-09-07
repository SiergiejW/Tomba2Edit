"""TOMBA2.IMG, looked at as a file rather than as somebody's VRAM.

The DAT, the IDX and the sound banks all have views; the IMG did not,
even though it is where every texture on the disc comes from. Its chunks
are pointed at by the IDX exactly as the DAT's are, and each one holds
one area's video memory as a handful of separately compressed
rectangular shards (see functions/img_codec.py).

What this shows is the shard table - where each rectangle lands in VRAM,
what it cost packed, and what it cost the codec to say it - beside the
assembled megabyte those shards decompress into. Selecting a shard rings
it in the picture, which is the quickest way to see which patch of a
level's textures is stored where.
"""
import struct

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from functions import img_codec, psx_vram
from gui import panel_title
from gui.vram_viewer import VRAMCanvas, decode_vram_bytes, vram_index_image

# The IDX record stride, the same 0x800 every other reader of it uses.
IDX_STRIDE = 0x800


def chunk_bounds(idx_path, chunk_index):
    """(start, end) of one area's chunk inside TOMBA2.IMG."""
    with open(idx_path, "rb") as idx:
        idx.seek(chunk_index * IDX_STRIDE)
        start, end, _, _, _ = struct.unpack("<5I", idx.read(20))
    return start, end


class IMGViewer(QWidget):
    """One IMG chunk: its shards, and the VRAM they build."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shards = []
        self._name = "IMG"

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "X", "Y", "W", "H", "Packed", "Raw", "Ratio"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_shard_selected)

        self.canvas = VRAMCanvas(self)
        self.summary = QLabel("No IMG chunk loaded")
        self.summary.setWordWrap(True)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(panel_title.make_panel_title("Shards"))
        left_layout.addWidget(self.table)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.summary)
        right_layout.addWidget(self.canvas, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 800])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def load_chunk(self, img_data, name="IMG", file_offset=0):
        """Show one chunk's worth of TOMBA2.IMG.

        `file_offset` is where the chunk starts in the file, so every
        shard can be given its absolute address rather than one relative
        to a chunk the user would then have to locate."""
        self._name = name
        try:
            shards, first = img_codec.read_chunk_header(img_data)
            decoded = img_codec.decompress_chunk(img_data)
            vram = decode_vram_bytes(img_data)
        except Exception as e:
            self.summary.setText(f"Couldn't read this chunk: {e}")
            self.table.setRowCount(0)
            self.canvas.set_image(None)
            return False

        self._shards = []
        at = first
        self.table.setRowCount(len(shards))
        for row, ((x, y, w, h, packed), (_header, pixels)) in enumerate(
                zip(shards, decoded)):
            raw = w * h * 2
            self._shards.append((x, y, w, h, packed, file_offset + at))
            ratio = f"{packed / raw:.2f}x" if raw else "-"
            for column, text in enumerate(
                    (str(row), str(x), str(y), str(w), str(h),
                     f"0x{packed:X}", f"0x{raw:X}", ratio)):
                self.table.setItem(row, column, QTableWidgetItem(text))
            at += packed

        packed_total = sum(s[4] for s in self._shards)
        self.summary.setText(
            f"{name}: {len(shards)} shard(s) @ 0x{file_offset:X}, "
            f"{packed_total} packed bytes covering "
            f"{sum(w * h * 2 for _, _, w, h, _ in shards)} bytes of VRAM.")
        print(f"selected: {name}  IMG chunk @ 0x{file_offset:X}  "
              f"{len(shards)} shard(s)  0x{packed_total:X} packed")

        self.canvas.texels_per_halfword = 4
        self.canvas.highlight = None
        self.canvas.set_image(vram_index_image(vram))
        self.canvas.fit()
        self.table.clearSelection()
        return True

    def _on_shard_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._shards):
            self.canvas.show_rect(None)
            return
        row = rows[0].row()
        x, y, w, h, packed, at = self._shards[row]
        # The canvas is showing 4bpp texels, four to a halfword, so a
        # shard measured in halfwords is four times as wide on screen.
        span = self.canvas.texels_per_halfword
        self.canvas.show_rect(QRectF(x * span, y, w * span, h))
        print(f"selected: {self._name}  shard {row}  packed @ 0x{at:X} "
              f"(0x{packed:X} bytes)  VRAM ({x}, {y}) {w}x{h} halfwords  "
              f"lands at 0x{x * 2 + y * psx_vram.VRAM_STRIDE:X}")

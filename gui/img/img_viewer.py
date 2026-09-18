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

from PIL.ImageQt import ImageQt
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
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
        self._vram = None
        self._base_summary = ""
        # Set by MainWindow when a chunk is loaded for a specific area -
        # what the Textured toggle needs to go looking for that area's
        # own art. See gui/vram_viewer.py's own copy of this pattern.
        self._area_source = None        # (idx_path, dat_path, chunk_index)
        self._region_cache = {}

        self.textured_btn = QPushButton("Textured (as used)", self)
        self.textured_btn.setCheckable(True)
        self.textured_btn.setChecked(True)
        self.textured_btn.setToolTip(
            "Reconstruct this chunk by finding every SMST, SPRT and BGMP "
            "file that samples it and painting each patch through its "
            "own CLUT, instead of the flat grey index view - a best "
            "effort from the disc's own data (see "
            "functions/vram_preview.py), not a guarantee.")
        self.textured_btn.toggled.connect(self._render)

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
        right_layout.addWidget(self.textured_btn)
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

    def set_area_source(self, idx_path, dat_path, chunk_index):
        """Where the Textured toggle should go looking for this chunk's
        own art. Call before load_chunk() - see the same note on
        gui/vram_viewer.py's copy of this method."""
        self._area_source = (idx_path, dat_path, chunk_index)

    def load_chunk(self, img_data, name="IMG", file_offset=0):
        """Show one chunk's worth of TOMBA2.IMG.

        `file_offset` is where the chunk starts in the file, so every
        shard can be given its absolute address rather than one relative
        to a chunk the user would then have to locate."""
        self._name = name
        try:
            shards, first = img_codec.read_chunk_header(img_data)
            decoded = img_codec.decompress_chunk(img_data)
            self._vram = decode_vram_bytes(img_data)
        except Exception as e:
            self._base_summary = f"Couldn't read this chunk: {e}"
            self.summary.setText(self._base_summary)
            self.table.setRowCount(0)
            self.canvas.set_image(None)
            self._vram = None
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
        # Kept apart from the note _render() adds for Textured mode -
        # appending straight onto self.summary's own text would compound
        # every time the button is toggled, and never come off again
        # once it was toggled back off.
        self._base_summary = (
            f"{name}: {len(shards)} shard(s) @ 0x{file_offset:X}, "
            f"{packed_total} packed bytes covering "
            f"{sum(w * h * 2 for _, _, w, h, _ in shards)} bytes of VRAM.")
        print(f"selected: {name}  IMG chunk @ 0x{file_offset:X}  "
              f"{len(shards)} shard(s)  0x{packed_total:X} packed")

        self.canvas.texels_per_halfword = 4
        self.canvas.highlight = None
        self._render()
        self.canvas.fit()
        self.table.clearSelection()
        return True

    def _render(self):
        """The flat index view, or the Textured reconstruction if the
        button is down - shared so toggling it and loading a fresh
        chunk both go through one place."""
        if self._vram is None:
            return
        if not self.textured_btn.isChecked():
            self.canvas.set_image(vram_index_image(self._vram))
            self.summary.setText(self._base_summary)
            return
        image, note = self._textured_image()
        self.canvas.set_image(image)
        self.summary.setText(self._base_summary + note)

    def _textured_image(self):
        """(QImage, note) for the Textured toggle - see
        gui/vram_viewer.py's own copy of this, which this mirrors."""
        from functions import vram_preview

        if self._area_source is None:
            return vram_index_image(self._vram), (
                "  [Textured: no area to search - this chunk wasn't "
                "opened from an AREA_NN row]")
        idx_path, dat_path, chunk_index = self._area_source
        regions = self._region_cache.get(self._area_source)
        if regions is None:
            try:
                regions = vram_preview.area_regions(
                    idx_path, dat_path, chunk_index)
            except (OSError, struct.error):
                regions = []
            self._region_cache[self._area_source] = regions
        pil_image = vram_preview.render(self._vram, regions)
        qimage = ImageQt(pil_image).copy()
        note = (f"  [Textured: {len(regions)} patch(es) from "
                f"AREA_{chunk_index:02X}'s own MDAT/SMST/SPRT/BGMP]" if regions
                else f"  [Textured: nothing found in AREA_{chunk_index:02X}'s "
                     f"own files]")
        return qimage, note

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

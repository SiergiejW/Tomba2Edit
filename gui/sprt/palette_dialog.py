"""Copying a palette out of one area so another can use it.

WHY IT IS NOT JUST A NUMBER

A piece points at a palette by VRAM address, so pointing AREA_01's
sprite at AREA_19's palette looks like typing an address. It is not:
the address means whatever the CURRENT area loaded there, and AREA_01
loads nothing at all at AREA_19's palette addresses. The sprite would
draw through sixteen zeroes - a hole.

Copying the bytes to that same address does not fix it either. Two
dozen level chunks write over that part of VRAM, and AREA_01's bank is
the resident one that every area draws from, so the palette would be
right in AREA_01 and gone everywhere else.

So the palette is copied to somewhere no area writes, and the piece is
pointed THERE. Sixteen halfwords is small enough that this nearly always
fits, which is what makes recolouring across areas practical at all.

What this cannot see is what the game uploads at runtime - see
functions/state_vram.py. A palette is exactly the kind of thing the game
does upload, so the placement is checked against the shard tables and
said to be optimistic, not proven.
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from functions import img_writer, psx_vram, vram_map
from gui.img.img_viewer import chunk_bounds
from gui.vram_viewer import decode_vram_bytes

SWATCH = 20


class _Swatches(QWidget):
    """Sixteen colours, so a palette can be recognised by eye."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colours = []
        self.setFixedHeight(SWATCH + 4)

    def set_colours(self, colours):
        self.colours = list(colours)
        self.setMinimumWidth(SWATCH * max(len(colours), 1))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        for i, colour in enumerate(self.colours):
            box = (i * SWATCH, 2, SWATCH - 2, SWATCH - 2)
            if not colour[3]:
                painter.fillRect(*box, QColor(70, 70, 70))
            else:
                painter.fillRect(*box, QColor(*colour[:3]))


class PaletteImportDialog(QDialog):
    """Take a palette from one area and put it where every area can see."""

    def __init__(self, cd_folder, destination_chunk=1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Copy a palette from another area")
        self.resize(620, 260)
        self.cd_folder = cd_folder
        self.destination_chunk = destination_chunk
        self.address = None             # where it ended up
        self._vram_cache = {}

        self.idx = os.path.join(cd_folder, "TOMBA2.IDX")
        self.img = os.path.join(cd_folder, "TOMBA2.IMG")
        self.shard_table = vram_map.chunk_shards(self.idx, self.img)

        self.source_box = QComboBox(self)
        for area in sorted(self.shard_table):
            self.source_box.addItem(f"AREA_{area:02X}", area)
        self.source_box.currentIndexChanged.connect(self._refresh)

        self.address_box = QLineEdit(self)
        self.address_box.setPlaceholderText("e.g. F8CE0")
        self.address_box.textChanged.connect(self._refresh)

        self.everywhere = QCheckBox(
            "Put it where NO area writes (needed for a bank used "
            "everywhere)", self)
        self.everywhere.setChecked(True)
        self.everywhere.setToolTip(
            "AREA_01's sprite bank is loaded in every area, so a palette "
            "it uses has to survive in every area too. Untick only if "
            "this sprite is used in one place.")
        self.everywhere.toggled.connect(self._refresh)

        self.swatches = _Swatches(self)
        self.note = QLabel(self)
        self.note.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Take it from", self.source_box)
        form.addRow("Palette address (hex)", self.address_box)
        form.addRow("", self.everywhere)
        form.addRow("Colours", self.swatches)

        self.buttons = QDialogButtonBox(self)
        self.ok = QPushButton("Copy it and use it")
        self.ok.clicked.connect(self._apply)
        self.buttons.addButton(self.ok, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note, 1)
        layout.addWidget(self.buttons)
        self.ok.setEnabled(False)

    # --- reading ------------------------------------------------------

    def _chunk_vram(self, area):
        if area not in self._vram_cache:
            start, end = chunk_bounds(self.idx, area)
            if end <= start:
                self._vram_cache[area] = None
            else:
                with open(self.img, "rb") as f:
                    f.seek(start)
                    self._vram_cache[area] = decode_vram_bytes(
                        f.read(end - start))
        return self._vram_cache[area]

    def _source_bytes(self):
        text = self.address_box.text().strip().lstrip("0xX") or ""
        if not text:
            return None, None
        try:
            address = int(text, 16)
        except ValueError:
            return None, "that is not a hex address"
        if address % 32:
            return None, (f"0x{address:X} is not on a 16-halfword boundary, "
                          f"so no palette starts there")
        area = self.source_box.currentData()
        vram = vram_map.loaded_vram(self.shard_table, self._chunk_vram, area)
        data = bytes(vram[address:address + 32])
        if not any(data):
            return None, (f"AREA_{area:02X} has nothing at 0x{address:X} - "
                          f"sixteen zeroes is not a palette")
        return data, None

    def _refresh(self):
        self.address = None
        self.ok.setEnabled(False)
        data, problem = self._source_bytes()
        if data is None:
            self.swatches.set_colours([])
            self.note.setText(problem or "Type the palette's VRAM address.")
            return
        self.swatches.set_colours(
            psx_vram.read_palette(data, 0, 16, transparent_zero=True))
        scope = (sorted(self.shard_table) if self.everywhere.isChecked()
                 else [self.destination_chunk])
        free = vram_map.free_for(self.shard_table, scope)
        spots = vram_map.free_rects(free, 16, 1,
                                    pages=vram_map.USABLE_PAGES,
                                    limit=1, align=16)
        if not spots:
            self.note.setText(
                "There is nowhere free to put it. Untick the box above to "
                "look only at the destination area, which has far more "
                "room.")
            return
        x, y, _page = spots[0]
        self.address = x * 2 + y * psx_vram.VRAM_STRIDE
        self._data = data
        where = ("no area writes" if self.everywhere.isChecked()
                 else f"AREA_{self.destination_chunk:02X} does not write")
        self.note.setText(
            f"Copy 32 bytes into AREA_{self.destination_chunk:02X}'s chunk "
            f"at <b>0x{self.address:X}</b> (halfword {x}, {y}) - a spot "
            f"{where}.<br><br>The piece will be pointed there. A savestate "
            f"is not loaded, so anything the game uploads at runtime is not "
            f"accounted for.")
        self.ok.setEnabled(True)

    # --- writing ------------------------------------------------------

    def _apply(self):
        if self.address is None:
            return
        try:
            start, end = chunk_bounds(self.idx, self.destination_chunk)
            with open(self.img, "rb") as f:
                f.seek(start)
                chunk = f.read(end - start)
            x, y = psx_vram.clut_address_xy(self.address)
            info = img_writer.rebuild(
                self.idx, self.img,
                {self.destination_chunk: img_writer.add_shards(
                    chunk, [(x, y, 16, 1, self._data)])},
                self.idx, self.img)
        except Exception as e:
            QMessageBox.critical(self, "Couldn't write it",
                                 f"TOMBA2.IMG was not changed:\n\n{e}")
            return
        print(f"palette copied to 0x{self.address:X} in "
              f"AREA_{self.destination_chunk:02X}: {info}")
        self.accept()

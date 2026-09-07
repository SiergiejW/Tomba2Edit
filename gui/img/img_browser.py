"""The whole of TOMBA2.IMG, chunk by chunk, with the gaps called out.

The per-area .IMG rows in the tree show one chunk at a time, which is
the right thing when you already know which area you want. This is the
other question: what is in the file at all, and is any of it reachable?

The IDX is a flat table of 0x800-byte records, one per area, each
opening with the (start, end) of that area's IMG chunk. So everything
the game can ever load is the union of those ranges - and anything
outside them is data the disc carries and never reads. On the retail US
disc that comes to exactly zero bytes, which is the point: a prototype
or a demo that reports more than zero is carrying something, and this
says where.

Two kinds of slack get looked for, because they hide different things:

    unreferenced    bytes of the file no IDX record covers. Whole chunks
                    left behind by a cut area.
    chunk slack     bytes inside a chunk past the end of its own shard
                    table. A shard the packer stopped pointing at.
"""
import os
import struct

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from functions import img_codec
from gui import panel_title
from gui.img.img_viewer import IMGViewer

IDX_STRIDE = 0x800

COLUMNS = ["Area", "IMG start", "IMG end", "Size", "Shards", "VRAM bytes",
           "Slack", "Notes"]


def scan(cd_folder):
    """Everything the IDX says about TOMBA2.IMG, plus what it doesn't.

    Returns (chunks, gaps, img_size). A chunk is a dict; `gaps` is
    [(start, end), ...] of file the IDX never points at."""
    idx_path = os.path.join(cd_folder, "TOMBA2.IDX")
    img_path = os.path.join(cd_folder, "TOMBA2.IMG")
    img_size = os.path.getsize(img_path)
    count = os.path.getsize(idx_path) // IDX_STRIDE

    with open(idx_path, "rb") as idx:
        records = []
        for chunk in range(count):
            idx.seek(chunk * IDX_STRIDE)
            start, end = struct.unpack("<2I", idx.read(8))
            records.append((chunk, start, end))

    with open(img_path, "rb") as img:
        data = img.read()

    chunks = []
    for chunk, start, end in records:
        row = {"chunk": chunk, "start": start, "end": end,
               "size": max(0, end - start), "shards": 0, "vram": 0,
               "slack": 0, "note": ""}
        if end <= start:
            row["note"] = "no IMG data"
            chunks.append(row)
            continue
        try:
            shards, first = img_codec.read_chunk_header(data[start:end])
        except Exception as e:
            row["note"] = f"unreadable header: {e}"
            chunks.append(row)
            continue
        row["shards"] = len(shards)
        row["vram"] = sum(w * h * 2 for _x, _y, w, h, _p in shards)
        body = first + sum(s[4] for s in shards)
        row["slack"] = row["size"] - body
        if row["slack"] > 0:
            row["note"] = f"{row['slack']} bytes past the last shard"
        elif row["slack"] < 0:
            row["note"] = "shard table runs past the end of the chunk"
        chunks.append(row)

    spans = sorted((s, e) for _c, s, e in records if e > s)
    gaps = []
    at = 0
    for start, end in spans:
        if start > at:
            gaps.append((at, start))
        at = max(at, end)
    if at < img_size:
        gaps.append((at, img_size))
    return chunks, gaps, img_size


class IMGBrowser(QWidget):
    """Every chunk in TOMBA2.IMG, and one of them opened."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cd_folder = None
        self._chunks = []

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_chunk_selected)

        self.summary = QLabel("No disc open", self)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        self.dump = QPushButton("Dump unreferenced bytes...", self)
        self.dump.setToolTip(
            "Write every region of TOMBA2.IMG the IDX never points at to "
            "a folder, one file per region, so it can be looked at with "
            "anything. Disabled when there are none.")
        self.dump.setEnabled(False)
        self.dump.clicked.connect(self._dump_unreferenced)

        self.viewer = IMGViewer(self)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(panel_title.make_panel_title("TOMBA2.IMG"))
        left_layout.addWidget(self.table, 1)
        left_layout.addWidget(self.summary)
        left_layout.addWidget(self.dump)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 900])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def load(self, cd_folder):
        """Point the browser at an open disc's CD folder."""
        self.cd_folder = cd_folder
        if not cd_folder:
            self.table.setRowCount(0)
            self.summary.setText("No disc open")
            self.dump.setEnabled(False)
            return
        try:
            chunks, gaps, img_size = scan(cd_folder)
        except Exception as e:
            self.table.setRowCount(0)
            self.summary.setText(f"Couldn't read TOMBA2.IMG: {e}")
            self.dump.setEnabled(False)
            return
        self._chunks = chunks
        self._gaps = gaps

        self.table.setRowCount(len(chunks))
        for row, c in enumerate(chunks):
            cells = (f"AREA_{c['chunk']:02X}",
                     f"0x{c['start']:X}", f"0x{c['end']:X}",
                     f"0x{c['size']:X}",
                     str(c["shards"]) if c["shards"] else "-",
                     f"0x{c['vram']:X}" if c["vram"] else "-",
                     str(c["slack"]) if c["slack"] else "-",
                     c["note"])
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))

        live = [c for c in chunks if c["size"]]
        unreferenced = sum(b - a for a, b in gaps)
        slack = sum(c["slack"] for c in chunks if c["slack"] > 0)
        lines = [
            f"TOMBA2.IMG is {img_size} bytes (0x{img_size:X}). "
            f"{len(live)} of {len(chunks)} areas carry a chunk.",
            f"Unreferenced by the IDX: <b>{unreferenced}</b> bytes"
            + (f" in {len(gaps)} region(s)." if gaps else " - the whole file "
               "is reachable."),
            f"Slack inside chunks: <b>{slack}</b> bytes.",
        ]
        for a, b in gaps[:12]:
            lines.append(f"&nbsp;&nbsp;unreferenced 0x{a:X} .. 0x{b:X} "
                         f"({b - a} bytes)")
        if len(gaps) > 12:
            lines.append(f"&nbsp;&nbsp;... and {len(gaps) - 12} more")
        self.summary.setText("<br>".join(lines))
        self.dump.setEnabled(bool(gaps))

        print(f"selected: TOMBA2.IMG  {img_size} bytes  {len(live)} chunk(s)  "
              f"{unreferenced} unreferenced  {slack} slack")

    def _on_chunk_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._chunks):
            return
        c = self._chunks[rows[0].row()]
        if not c["size"]:
            self.viewer.summary.setText(
                f"AREA_{c['chunk']:02X} has no IMG chunk.")
            return
        path = os.path.join(self.cd_folder, "TOMBA2.IMG")
        with open(path, "rb") as img:
            img.seek(c["start"])
            data = img.read(c["size"])
        self.viewer.load_chunk(data, f"AREA_{c['chunk']:02X}.IMG", c["start"])

    def _dump_unreferenced(self):
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        if not self._gaps or not self.cd_folder:
            return
        out = QFileDialog.getExistingDirectory(
            self, "Where to write the unreferenced regions")
        if not out:
            return
        path = os.path.join(self.cd_folder, "TOMBA2.IMG")
        written = []
        with open(path, "rb") as img:
            for a, b in self._gaps:
                img.seek(a)
                name = f"IMG_unreferenced_{a:08X}_{b:08X}.bin"
                with open(os.path.join(out, name), "wb") as f:
                    f.write(img.read(b - a))
                written.append(name)
                print(f"wrote {name} ({b - a} bytes)")
        QMessageBox.information(
            self, "Written",
            f"Wrote {len(written)} region(s) to {out}.")

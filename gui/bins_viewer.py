"""
GUI section listing every .BIN overlay file found alongside MAIN.EXE
(BIN/A00.BIN..A0L.BIN, CRD.BIN, DEMO.BIN, GAME.BIN, OPN.BIN, SOP.BIN,
START.BIN). Only SOP.BIN - the intro story-crawl overlay, see
functions/sop_editor.py - is currently understood well enough to edit;
every other file is listed for visibility only.
"""

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QTreeView, QWidget, QVBoxLayout, QSplitter, QLabel, QStackedWidget

from gui.sop_viewer import SopViewer

BIN_LOCATION_ROLE = Qt.ItemDataRole.UserRole + 1
_AREA_OVERLAY_RE = re.compile(r"^A0[0-9A-L]\.BIN$", re.IGNORECASE)


class BinsViewer(QWidget):
    content_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.sop_path = None
        self._overlays = []

        layout = QVBoxLayout()
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)

        self.stack = QStackedWidget()
        self.placeholder = QLabel(
            "Select SOP.BIN (the intro story text) to view or edit it.\n\n"
            "The other overlay files here aren't currently understood well "
            "enough to edit safely - listed for visibility only."
        )
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: gray; padding: 16px;")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.sop_viewer = SopViewer()
        self.sop_viewer.content_changed.connect(self.content_changed.emit)

        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.sop_viewer)

        splitter.addWidget(self.tree)
        splitter.addWidget(self.stack)
        splitter.setSizes([300, 700])
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.tree.selectionModel().selectionChanged.connect(self._on_tree_selection_changed)

    def load_overlays(self, overlays, sop_path):
        """overlays: [{"name": str, "size": int}, ...] as found on the
        disc (see ISOHandler.bin_overlays). sop_path: extracted SOP.BIN
        path, or None if it wasn't found."""
        self.clear_cache()
        self._overlays = overlays
        self.sop_path = sop_path

        root = self.tree_model.invisibleRootItem()
        area_overlays = sorted((o for o in overlays if _AREA_OVERLAY_RE.match(o["name"])), key=lambda o: o["name"])
        other_overlays = sorted((o for o in overlays if not _AREA_OVERLAY_RE.match(o["name"])), key=lambda o: o["name"])

        if area_overlays:
            area_folder = QStandardItem(f"Area overlays ({len(area_overlays)})")
            area_folder.setFlags(area_folder.flags() & ~Qt.ItemFlag.ItemIsEditable)
            for o in area_overlays:
                area_folder.appendRow(self._make_item(o))
            root.appendRow(area_folder)

        for o in other_overlays:
            root.appendRow(self._make_item(o))

        if sop_path:
            self.sop_viewer.load_sop(sop_path)

    @staticmethod
    def _make_item(overlay):
        item = QStandardItem(f"{overlay['name']} ({overlay['size']} bytes)")
        item.setData(overlay["name"], BIN_LOCATION_ROLE)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _on_tree_selection_changed(self):
        selected = self.tree.selectionModel().selectedIndexes()
        if not selected:
            return
        item = self.tree_model.itemFromIndex(selected[0])
        name = item.data(BIN_LOCATION_ROLE)
        if name and name.upper() == "SOP.BIN" and self.sop_path:
            self.stack.setCurrentWidget(self.sop_viewer)
        else:
            self.stack.setCurrentWidget(self.placeholder)

    def has_pending_edits(self):
        return self.sop_viewer.has_pending_edits()

    def pending_edits(self):
        return self.sop_viewer.pending_edits()

    def all_edits(self):
        return self.sop_viewer.all_edits()

    def pool_overflowing(self):
        return self.sop_viewer.pool_overflowing()

    def mark_exported(self):
        self.sop_viewer.mark_exported()

    def clear_cache(self):
        self.sop_path = None
        self._overlays = []
        self.tree_model.clear()
        self.sop_viewer.clear_cache()
        self.stack.setCurrentWidget(self.placeholder)

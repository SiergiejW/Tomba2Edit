"""
GUI section listing every .BIN overlay file found alongside MAIN.EXE
(BIN/A00.BIN..A0L.BIN, CRD.BIN, DEMO.BIN, GAME.BIN, OPN.BIN, SOP.BIN,
START.BIN). Only SOP.BIN - the intro story-crawl overlay, see
gui/bins/sop_editor.py - is currently understood well enough to edit;
every other file is listed for visibility only.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QBrush, QColor
from PyQt6.QtWidgets import QTreeView, QWidget, QVBoxLayout, QSplitter, QLabel, QStackedWidget

from gui.bins.sop_viewer import SopViewer
from gui.txtd.txtd_viewer import EDITED_ENTRY_COLOR, EXPORTED_ENTRY_COLOR
from gui import panel_title

BIN_LOCATION_ROLE = Qt.ItemDataRole.UserRole + 1


class BinsViewer(QWidget):
    content_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.sop_path = None
        self._overlays = []
        self._sop_item = None

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)

        tree_panel = QWidget()
        tree_panel_layout = QVBoxLayout()
        tree_panel_layout.setContentsMargins(0, 0, 0, 0)
        tree_panel_layout.setSpacing(0)
        tree_panel_layout.addWidget(panel_title.make_panel_title("Entries window"))
        tree_panel_layout.addWidget(self.tree)
        tree_panel.setLayout(tree_panel_layout)

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
        self.sop_viewer.content_changed.connect(self._refresh_sop_item_color)

        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.sop_viewer)

        splitter.addWidget(tree_panel)
        splitter.addWidget(self.stack)
        splitter.setSizes([350, 700])
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.tree.selectionModel().selectionChanged.connect(self._on_tree_selection_changed)

    def load_overlays(self, overlays, sop_path):
        """overlays: [{"name": str, "size": int}, ...] as found on the
        disc (see ISOHandler.bin_overlays). sop_path: extracted SOP.BIN
        path, or None if it wasn't found. Listed flat, matching how
        they actually sit loose in the disc's BIN/ folder - no
        synthetic grouping."""
        self.clear_cache()
        self._overlays = overlays
        self.sop_path = sop_path

        root = self.tree_model.invisibleRootItem()
        for o in sorted(overlays, key=lambda o: o["name"]):
            item = self._make_item(o)
            if o["name"].upper() == "SOP.BIN":
                self._sop_item = item
            root.appendRow(item)

        if sop_path:
            self.sop_viewer.load_sop(sop_path)
        self._refresh_sop_item_color()

    @staticmethod
    def _make_item(overlay):
        item = QStandardItem(f"{overlay['name']} ({overlay['size']} bytes)")
        item.setData(overlay["name"], BIN_LOCATION_ROLE)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if overlay["name"].upper() != "SOP.BIN":
            item.setForeground(QBrush(QColor("gray")))
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

    def _refresh_sop_item_color(self):
        """Colors SOP.BIN's own row in this tree - orange while it has
        pending edits, green once exported - mirroring how TXTD/TXT2
        files are colored in the main disc tree (see
        SopViewer.pending_state())."""
        if self._sop_item is None:
            return
        state = self.sop_viewer.pending_state()
        if state == "edited":
            self._sop_item.setForeground(QBrush(QColor(EDITED_ENTRY_COLOR)))
        elif state == "exported":
            self._sop_item.setForeground(QBrush(QColor(EXPORTED_ENTRY_COLOR)))
        else:
            self._sop_item.setData(None, Qt.ItemDataRole.ForegroundRole)

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
        self._refresh_sop_item_color()

    def clear_cache(self):
        self.sop_path = None
        self._overlays = []
        self._sop_item = None
        self.tree_model.clear()
        self.sop_viewer.clear_cache()
        self.stack.setCurrentWidget(self.placeholder)

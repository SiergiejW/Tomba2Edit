"""
GUI viewer/editor for MAIN.EXE's string pool (see functions/mainbin_parser.py
and functions/mainbin_editor.py for the scanning/packing logic this wraps).
Same tree-on-left/text-on-right pattern as TXTDViewer/TXT2Viewer, but
simpler: entries are a flat list (offset, length, text).

Saves go through a fixed-budget repack (mainbin_editor.repack_pool) -
entries in the flowable region get tightly packed on every save; pinned
entries (no known table reference) are never editable.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QFont, QBrush, QColor
from PyQt6.QtWidgets import QTreeView, QWidget, QVBoxLayout, QSplitter, QTextEdit, QLabel, QToolButton

from gui.txtd_viewer import EntryTextHighlighter, EDITED_ENTRY_COLOR, EXPORTED_ENTRY_COLOR, ENTRY_LOCATION_ROLE
from functions.mainbin_editor import _mainbin_entries, compute_pool_state, FLOW_REGION_START, FLOW_REGION_END
from functions.mainbin_parser import encode_bytes, MainBinParseError

STATUS_WARNING_COLOR = "#c0392b"
POOL_OK_COLOR = "#1e7d32"
POOL_OVER_COLOR = "#c0392b"


def _is_flowable(offset):
    return FLOW_REGION_START <= offset < FLOW_REGION_END


class MainExeViewer(QWidget):
    # Emitted after any edit changes this file's pending-edit state (i.e.
    # after every keystroke) - MainWindow listens to know when to enable
    # Save and how to color this file's own presence in the UI.
    content_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.exe_path = None
        self.entries = []

        self._current_entry_item = None
        self._loading = False
        self._entry_items = {}
        self._entries_by_offset = {}
        # offsets only - text lives in self.entries[i]["text"], mutated in place
        self._edited_offsets = set()    # pending edit, not yet exported (orange)
        self._exported_offsets = set()  # edited and exported since (green)
        self._original_texts = {}

        layout = QVBoxLayout()

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Select an entry to view/edit its text")
        self.text_edit.setReadOnly(True)
        font = QFont("Courier New", 12)
        font.setWeight(QFont.Weight.Bold)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setFont(font)
        self.text_edit.setMinimumWidth(400)
        # Keep a reference so it isn't garbage-collected.
        self._highlighter = EntryTextHighlighter(self.text_edit.document())

        # Foldable pool-budget notice, above the per-entry status line.
        self.pool_toggle = QToolButton()
        self.pool_toggle.setCheckable(True)
        self.pool_toggle.setChecked(True)
        self.pool_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.pool_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pool_toggle.setText("Text pool")
        self.pool_toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.pool_toggle.toggled.connect(self._on_pool_toggle)

        self.pool_label = QLabel("")
        self.pool_label.setWordWrap(True)
        font_bold = QFont()
        font_bold.setBold(True)
        self.pool_label.setFont(font_bold)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        self.status_label.setWordWrap(True)
        self.status_label.setMaximumWidth(600)

        right_layout.addWidget(self.text_edit)
        right_layout.addWidget(self.pool_toggle)
        right_layout.addWidget(self.pool_label)
        right_layout.addWidget(self.status_label)
        right_panel.setLayout(right_layout)

        splitter.addWidget(self.tree)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 700])
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.tree.selectionModel().selectionChanged.connect(self._on_tree_selection_changed)
        self.text_edit.textChanged.connect(self._on_text_changed)

    def load_exe(self, exe_path):
        """Scan exe_path's string pool and populate the tree. Safe to call
        again with a new path (e.g. a freshly opened ISO/folder) - fully
        resets prior state first."""
        self.clear_cache()
        self.exe_path = exe_path
        self.entries = _mainbin_entries(exe_path)
        self._original_texts = {e["offset"]: e["text"] for e in self.entries}
        self._entries_by_offset = {e["offset"]: e for e in self.entries}

        root = self.tree_model.invisibleRootItem()
        for e in self.entries:
            item = QStandardItem(self._entry_label(e))
            item.setData(e["offset"], ENTRY_LOCATION_ROLE)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._entry_items[e["offset"]] = item
            root.appendRow(item)

        self._update_pool_label()

    @staticmethod
    def _preview_text(text):
        preview = " ".join(text.split())
        return preview if len(preview) <= 60 else preview[:57] + "..."

    def _entry_label(self, entry):
        pin = "[pinned] " if not _is_flowable(entry["offset"]) else ""
        return f"[{entry['offset']:#06x}] {pin}{self._preview_text(entry['text'])}"

    def _find_entry(self, offset):
        return self._entries_by_offset.get(offset)

    def _on_tree_selection_changed(self):
        selected = self.tree.selectionModel().selectedIndexes()
        if not selected:
            return
        item = self.tree_model.itemFromIndex(selected[0])
        offset = item.data(ENTRY_LOCATION_ROLE)
        if offset is None:
            return

        self._current_entry_item = item
        current_text = self._entries_by_offset[offset]["text"]
        pinned = not _is_flowable(offset)

        self._loading = True
        self.text_edit.setPlainText(current_text)
        self.text_edit.setReadOnly(pinned)
        self._loading = False

        self._update_status(offset, current_text)

    def _on_text_changed(self):
        if self._loading or self._current_entry_item is None:
            return
        offset = self._current_entry_item.data(ENTRY_LOCATION_ROLE)
        if offset is None or not _is_flowable(offset):
            return

        new_text = self.text_edit.toPlainText()
        self._entries_by_offset[offset]["text"] = new_text

        if new_text == self._original_texts[offset]:
            self._edited_offsets.discard(offset)
            self._exported_offsets.discard(offset)
        else:
            self._edited_offsets.add(offset)
            self._exported_offsets.discard(offset)

        self._set_item_state(self._current_entry_item, offset)
        self._update_status(offset, new_text)
        self._update_pool_label()
        self.content_changed.emit()

    def _set_item_state(self, item, offset):
        if offset in self._edited_offsets:
            item.setForeground(QBrush(QColor(EDITED_ENTRY_COLOR)))
        elif offset in self._exported_offsets:
            item.setForeground(QBrush(QColor(EXPORTED_ENTRY_COLOR)))
        else:
            item.setData(None, Qt.ItemDataRole.ForegroundRole)
        item.setText(self._entry_label(self._entries_by_offset[offset]))

    def _update_status(self, offset, text):
        if not _is_flowable(offset):
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText(
                "This entry has no known pointer-table reference, so it's "
                "pinned - not editable, to avoid moving or resizing something "
                "nothing confirmed actually reads."
            )
            return

        try:
            encode_bytes(text)
        except MainBinParseError as e:
            self.status_label.setStyleSheet(f"color: {STATUS_WARNING_COLOR}; font-weight: bold;")
            self.status_label.setText(f"Can't encode this text: {e}")
            return

        if offset in self._edited_offsets:
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText("Edited - will be included in the next save.")
        else:
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText("")

    def _on_pool_toggle(self, checked):
        self.pool_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.pool_label.setVisible(checked)

    def _update_pool_label(self):
        state = compute_pool_state(self.entries, self.pending_edits_for_pool())
        used, capacity, free = state["used"], state["capacity"], state["free"]
        if state["errors"]:
            self.pool_label.setStyleSheet(f"color: {STATUS_WARNING_COLOR};")
            self.pool_label.setText(
                f"Text pool: {len(state['errors'])} entry(ies) have invalid text - fix "
                f"those before the byte count is meaningful."
            )
        elif free >= 0:
            self.pool_label.setStyleSheet(f"color: {POOL_OK_COLOR};")
            self.pool_label.setText(f"Text pool: {used} / {capacity} bytes used - {free} free")
        else:
            self.pool_label.setStyleSheet(f"color: {POOL_OVER_COLOR};")
            self.pool_label.setText(
                f"Text pool: {used} / {capacity} bytes used - OVER BUDGET by {-free} "
                f"byte(s). Shorten some entries before saving."
            )

    def pending_edits_for_pool(self):
        """{offset: text} for every flowable entry currently different
        from its original, edited or already-exported alike."""
        return {
            offset: self._entries_by_offset[offset]["text"]
            for offset in (self._edited_offsets | self._exported_offsets)
        }

    def pending_edits(self):
        """{offset: text} for entries dirty since the last export (orange) - UI only, not what a save should pack (see all_edits)."""
        return {offset: self._entries_by_offset[offset]["text"] for offset in self._edited_offsets}

    def all_edits(self):
        """{offset: text} for every entry differing from the on-disk file - what an export must reapply each time."""
        return self.pending_edits_for_pool()

    def has_pending_edits(self):
        return bool(self._edited_offsets)

    def pending_state(self):
        return "edited" if self._edited_offsets else None

    def pool_overflowing(self):
        state = compute_pool_state(self.entries, self.pending_edits_for_pool())
        return state["free"] < 0 or bool(state["errors"])

    def mark_exported(self):
        """Flip every dirty entry from edited (orange) to exported (green)."""
        for offset in list(self._edited_offsets):
            self._edited_offsets.discard(offset)
            self._exported_offsets.add(offset)
            item = self._entry_items.get(offset)
            if item is not None:
                self._set_item_state(item, offset)

    def clear_cache(self):
        """Full reset - called before loading a new exe, and when a new
        ISO/folder is opened with no MAIN.EXE found in it."""
        self.exe_path = None
        self.entries = []
        self._current_entry_item = None
        self._entry_items = {}
        self._entries_by_offset = {}
        self._edited_offsets = set()
        self._exported_offsets = set()
        self._original_texts = {}

        self.tree_model.clear()
        self._loading = True
        self.text_edit.clear()
        self._loading = False
        self.text_edit.setReadOnly(True)
        self.status_label.setStyleSheet("color: gray;")
        self.status_label.setText("")
        self.pool_label.setText("")

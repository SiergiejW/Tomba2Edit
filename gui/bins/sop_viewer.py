"""
GUI viewer/editor for BIN/SOP.BIN's intro story-crawl text (see
gui/bins/sop_editor.py for the scanning/packing logic this wraps).
Same tree-on-left/text-on-right, foldable-pool-budget pattern as
MainExeViewer - lines share one fixed-capacity pool, tightly repacked
and reference-patched on save (see sop_editor's own docstring for the
reference table and shared "blank" pointer this relies on).
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QFont, QBrush, QColor
from PyQt6.QtWidgets import QTreeView, QWidget, QVBoxLayout, QSplitter, QLabel, QToolButton, QTextEdit

from gui.txtd.txtd_viewer import EntryTextHighlighter, EDITED_ENTRY_COLOR, EXPORTED_ENTRY_COLOR, ENTRY_LOCATION_ROLE
from gui.margin_text_edit import MarginTextEdit
from gui import panel_title
from gui.txtd.font_preview import FontPreview
from gui.bins.sop_editor import sop_entries, compute_pool_state, detect_build, UnsupportedSopError, SCREEN_CHAR_LIMIT
from gui.mainbin.mainbin_parser import encode_bytes, MainBinParseError

STATUS_WARNING_COLOR = "#c0392b"
SCREEN_OVERFLOW_COLOR = "#e67e22"
POOL_OK_COLOR = "#1e7d32"
POOL_OVER_COLOR = "#c0392b"


class SopViewer(QWidget):
    content_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.sop_path = None
        self.build = None
        self.entries = []

        self._current_entry_item = None
        self._loading = False
        self._entry_items = {}
        self._entries_by_offset = {}
        self._edited_offsets = set()    # pending edit, not yet exported (orange)
        self._exported_offsets = set()  # edited and exported since (green)
        self._original_texts = {}

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
        tree_panel_layout.addWidget(panel_title.make_panel_title("Entry lines"))
        tree_panel_layout.addWidget(self.tree)
        tree_panel.setLayout(tree_panel_layout)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(panel_title.make_panel_title("Editing window"))

        self.text_edit = MarginTextEdit(SCREEN_CHAR_LIMIT)
        self.text_edit.setPlaceholderText("Select a line to view/edit its text")
        self.text_edit.setReadOnly(True)
        font = QFont("Courier New", 12)
        font.setWeight(QFont.Weight.Bold)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setFont(font)
        self.text_edit.setMinimumWidth(400)
        self._highlighter = EntryTextHighlighter(self.text_edit.document())

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

        # SOP BIN is drawn in the big font - see gui/txtd/font_preview.py.
        # Raw text on top, the game's own rendering below, each half.
        edit_side = QWidget()
        edit_side_layout = QVBoxLayout(edit_side)
        edit_side_layout.setContentsMargins(0, 0, 0, 0)
        edit_side_layout.addWidget(self.text_edit)

        preview_side = QWidget()
        preview_side_layout = QVBoxLayout(preview_side)
        preview_side_layout.setContentsMargins(0, 0, 0, 0)
        preview_side_layout.addWidget(
            panel_title.make_panel_title("In-game preview"))
        self.preview = FontPreview(big=True)
        preview_side_layout.addWidget(self.preview)

        edit_split = QSplitter(Qt.Orientation.Vertical)
        edit_split.addWidget(edit_side)
        edit_split.addWidget(preview_side)
        edit_split.setStretchFactor(0, 1)
        edit_split.setStretchFactor(1, 1)
        edit_split.setChildrenCollapsible(False)
        edit_split.setSizes([10000, 10000])
        right_layout.addWidget(edit_split)
        # The budget line stays under both halves, where it reads as
        # belonging to the entry rather than to the edit box.
        right_layout.addWidget(self.pool_toggle)
        right_layout.addWidget(self.pool_label)
        right_layout.addWidget(self.status_label)
        right_panel.setLayout(right_layout)

        splitter.addWidget(tree_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 700])
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.tree.selectionModel().selectionChanged.connect(self._on_tree_selection_changed)
        self.text_edit.textChanged.connect(self._on_text_changed)

    def load_sop(self, sop_path):
        """Scan sop_path's story text and populate the tree. For a build
        this tool doesn't know the text layout for, falls back to a
        read-only view (self.build stays None) instead of refusing."""
        self.clear_cache()
        self.sop_path = sop_path
        try:
            self.build = detect_build(sop_path)
            self.pool_toggle.setText(f"Text pool ({self.build['label']})")
        except UnsupportedSopError:
            self.build = None
            self.pool_toggle.setText("Text pool (unrecognized build)")
        self.entries = sop_entries(sop_path)
        self._original_texts = {e["offset"]: e["text"] for e in self.entries}
        self._entries_by_offset = {e["offset"]: e for e in self.entries}

        # The story displays bottom-to-top (newest line at the bottom,
        # scrolling up) - i.e. in FILE order reversed - so show it that
        # way in the tree too. The last entry in file order is always a
        # 1-3 byte non-narrative fragment (alignment padding, not a
        # displayed line - see sop_editor's pinned-entry handling) -
        # hidden from the tree entirely; it stays in self.entries so
        # repack_pool still leaves it untouched in place.
        display_order = [e for e in reversed(self.entries) if not self._is_trailing_filler(e["offset"])]

        root = self.tree_model.invisibleRootItem()
        for e in display_order:
            item = QStandardItem(self._entry_label(e))
            item.setData(e["offset"], ENTRY_LOCATION_ROLE)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._entry_items[e["offset"]] = item
            root.appendRow(item)

        self._update_pool_label()

    def _is_trailing_filler(self, offset):
        return self.build is not None and self.entries and offset == self.entries[-1]["offset"]

    @staticmethod
    def _preview_text(text):
        preview = " ".join(text.split())
        return preview if len(preview) <= 60 else preview[:57] + "..."

    def _entry_label(self, entry):
        return f"[{entry['offset']:#06x}] {self._preview_text(entry['text'])}"

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
        editable = self.build is not None

        self._loading = True
        self.text_edit.setPlainText(current_text)
        self.preview.set_text(current_text)
        self.text_edit.setReadOnly(not editable)
        self._loading = False

        self._update_status(offset, current_text)

    def _on_text_changed(self):
        if self._loading or self._current_entry_item is None or self.build is None:
            return
        offset = self._current_entry_item.data(ENTRY_LOCATION_ROLE)
        if offset is None:
            return

        new_text = self.text_edit.toPlainText()
        self.preview.set_text(new_text)
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
        if self.build is None:
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText("Unrecognized game build - view-only, not editable.")
            return

        try:
            encoded_len = len(encode_bytes(text))
        except MainBinParseError as e:
            self.status_label.setStyleSheet(f"color: {STATUS_WARNING_COLOR}; font-weight: bold;")
            self.status_label.setText(f"Can't encode this text: {e}")
            return

        edited_prefix = "Edited. " if offset in self._edited_offsets else ""
        if encoded_len > SCREEN_CHAR_LIMIT:
            self.status_label.setStyleSheet(f"color: {SCREEN_OVERFLOW_COLOR}; font-weight: bold;")
            self.status_label.setText(
                f"{edited_prefix}{encoded_len} characters - likely to run off the screen "
                f"(fits about {SCREEN_CHAR_LIMIT}, past the grey line above). This is a "
                f"display concern, separate from the save's byte budget."
            )
        elif edited_prefix:
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText("Edited - will be included in the next save.")
        else:
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText("")

    def _on_pool_toggle(self, checked):
        self.pool_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.pool_label.setVisible(checked)

    def _update_pool_label(self):
        if self.build is None:
            self.pool_label.setStyleSheet("color: gray;")
            self.pool_label.setText(
                "Unrecognized game build - text layout hasn't been mapped for "
                "this version, so lines are view-only. No editing or saving."
            )
            return
        state = compute_pool_state(self.entries, self.build, self.pending_edits_for_pool())
        used, capacity, free = state["used"], state["capacity"], state["free"]
        if state["errors"]:
            self.pool_label.setStyleSheet(f"color: {STATUS_WARNING_COLOR};")
            self.pool_label.setText(
                f"Text pool: {len(state['errors'])} line(s) have invalid text - fix "
                f"those before the byte count is meaningful."
            )
        elif free >= 0:
            self.pool_label.setStyleSheet(f"color: {POOL_OK_COLOR};")
            self.pool_label.setText(f"Text pool: {used} / {capacity} bytes used - {free} free")
        else:
            self.pool_label.setStyleSheet(f"color: {POOL_OVER_COLOR};")
            self.pool_label.setText(
                f"Text pool: {used} / {capacity} bytes used - OVER BUDGET by {-free} "
                f"byte(s). Shorten some lines before saving."
            )

    def pending_edits_for_pool(self):
        return {
            offset: self._entries_by_offset[offset]["text"]
            for offset in (self._edited_offsets | self._exported_offsets)
        }

    def pending_edits(self):
        return {offset: self._entries_by_offset[offset]["text"] for offset in self._edited_offsets}

    def all_edits(self):
        return self.pending_edits_for_pool()

    def has_pending_edits(self):
        return bool(self._edited_offsets)

    def pending_state(self):
        """"edited" | "exported" | None for the file as a whole -
        mirrors TXTDViewer/TXT2Viewer's own pending_state(), so a
        parent tree can color SOP.BIN's own row the same way."""
        if self._edited_offsets:
            return "edited"
        if self._exported_offsets:
            return "exported"
        return None

    def pool_overflowing(self):
        if self.build is None:
            return False
        state = compute_pool_state(self.entries, self.build, self.pending_edits_for_pool())
        return state["free"] < 0 or bool(state["errors"])

    def mark_exported(self):
        for offset in list(self._edited_offsets):
            self._edited_offsets.discard(offset)
            self._exported_offsets.add(offset)
            item = self._entry_items.get(offset)
            if item is not None:
                self._set_item_state(item, offset)

    def clear_cache(self):
        self.sop_path = None
        self.build = None
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
        self.pool_toggle.setText("Text pool")

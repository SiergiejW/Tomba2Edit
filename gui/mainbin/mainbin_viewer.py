"""
GUI viewer/editor for MAIN.EXE's string pool (see gui/mainbin/mainbin_parser.py
and gui/mainbin/mainbin_editor.py for the scanning/packing logic this wraps).
Same tree-on-left/text-on-right pattern as TXTDViewer/TXT2Viewer, but
simpler: entries are a flat list (offset, length, text).

Saves go through a fixed-budget repack (mainbin_editor.repack_pool) -
entries in the flowable region get tightly packed on every save; pinned
entries (no known table reference) are never editable.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QFont, QBrush, QColor
from PyQt6.QtWidgets import (QTreeView, QWidget, QVBoxLayout, QSplitter,
                             QTextEdit, QLabel, QToolButton, QCheckBox)

from gui.txtd.txtd_viewer import EntryTextHighlighter, EDITED_ENTRY_COLOR, EXPORTED_ENTRY_COLOR, ENTRY_LOCATION_ROLE
from gui import panel_title
from gui.txtd.font_preview import FontPreview
from gui.mainbin.mainbin_editor import (
    _mainbin_entries, compute_pool_state, detect_build, _is_flowable, UnsupportedExeError,
    categorize_entries, PINNED_CATEGORY,
)
from gui.mainbin import mainbin_parser
from gui.mainbin.mainbin_parser import encode_bytes, MainBinParseError

STATUS_WARNING_COLOR = "#c0392b"
POOL_OK_COLOR = "#1e7d32"
POOL_OVER_COLOR = "#c0392b"


class MainExeViewer(QWidget):
    # Emitted after any edit changes this file's pending-edit state (i.e.
    # after every keystroke) - MainWindow listens to know when to enable
    # Save and how to color this file's own presence in the UI.
    content_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.exe_path = None
        self.build = None
        self.entries = []

        self._current_entry_item = None
        self._loading = False
        self._entry_items = {}
        self._entries_by_offset = {}
        # Category-folder bookkeeping (see load_exe) - lets an entry's
        # edit state mark/unmark a "*" on the folder it lives in, the
        # same way MainWindow marks the DAT Assets/MAIN.EXE/BINs tabs.
        self._folders = {}          # label -> QStandardItem
        self._folder_labels = {}    # label -> "Label (N)" base text (no "*")
        self._category_by_offset = {}  # offset -> label
        # offsets only - text lives in self.entries[i]["text"], mutated in place
        self._edited_offsets = set()    # pending edit, not yet exported (orange)
        self._exported_offsets = set()  # edited and exported since (green)
        # Entries made before the MAIN.EXE codec learned the system-font
        # offset.  They decode normally, but their custom glyph bytes
        # must be rewritten on the next save (0x91 -> 0xB1, etc.).
        self._migration_offsets = set()
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
        tree_panel_layout.addWidget(panel_title.make_panel_title("Entries window"))
        tree_panel_layout.addWidget(self.tree)
        tree_panel.setLayout(tree_panel_layout)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(panel_title.make_panel_title("Editing window"))

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

        # MAIN.EXE is drawn in the small font - see gui/txtd/font_preview.py.
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
        # console_font: the pool is what the console draws, not the game
        # (see gui/mainbin/mainbin_parser) - which on the Japanese disc
        # means a font of the console's own rather than the page's.
        self.preview = FontPreview(big=False, style="notice",
                                   console_font=True)
        # Type a byte, see the glyph that byte draws. Off, a high byte
        # reads as Latin-1, which is right for the stock discs; on, it
        # reads and previews as {$XX} - the only way to use a letter
        # drawn into a spare font-page cell. See mainbin_parser.
        self.glyph_bytes = QCheckBox("Bytes as {$XX} glyphs")
        self.glyph_bytes.setToolTip(
            "Show every non-ASCII byte as {$XX} and preview it as font "
            "page cell 0xXX, instead of reading it as a Latin-1 "
            "character.\n\nThis is how a glyph drawn into a spare cell "
            "gets used: put a letter in cell 160 and type {$A0}. What "
            "gets written to the file is the same byte either way.")
        self.glyph_bytes.toggled.connect(self._on_glyph_bytes)
        preview_side_layout.addWidget(self.glyph_bytes)
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

    def _on_glyph_bytes(self, on):
        """Re-read the pool in the other spelling.

        Decoding is what changes, so the whole pool is scanned again -
        the entries on screen are the old spelling until it is."""
        mainbin_parser.set_glyph_bytes(on)
        self.preview.raw_cells = on
        if self.exe_path:
            self.load_exe(self.exe_path)

    def reload_preview_font(self, cd_folder, glyph_top=None):
        """Reload the font page and redraw the selected entry.

        A Translation-tab save rewrites TOMBA2.IMG underneath an already
        constructed FontSheet.  Without this explicit reload the text
        box has the new Polish character while the preview keeps drawing
        the old cell pixels until the disc is reopened.
        """
        self.preview.set_source(cd_folder, glyph_top)
        item = self._current_entry_item
        offset = None if item is None else item.data(ENTRY_LOCATION_ROLE)
        if offset is not None and offset in self._entries_by_offset:
            self.preview.set_text(self._entries_by_offset[offset]["text"])

    def load_exe(self, exe_path):
        """Scan exe_path's string pool and populate the tree. Safe to call
        again with a new path (e.g. a freshly opened ISO/folder) - fully
        resets prior state first. For a build this tool doesn't know the
        pointer tables for (see BUILDS), falls back to a read-only view
        (self.build stays None) instead of refusing outright - browsing
        doesn't touch any pointer table, only editing needs the mapping."""
        self.clear_cache()
        self.exe_path = exe_path
        try:
            self.build = detect_build(exe_path)
            self.pool_toggle.setText(f"Text pool ({self.build['label']})")
        except UnsupportedExeError:
            self.build = None
            self.pool_toggle.setText("Text pool (unrecognized build)")
        self.entries = _mainbin_entries(exe_path)
        self._original_texts = {e["offset"]: e["text"] for e in self.entries}
        self._entries_by_offset = {e["offset"]: e for e in self.entries}
        self._migration_offsets = self._legacy_glyph_offsets(exe_path)
        if self._migration_offsets:
            # Saving is required even if the translator does not alter
            # visible text: these entries need their stored glyph bytes
            # migrated to the small-font spelling.
            self.content_changed.emit()

        root = self.tree_model.invisibleRootItem()
        categories = categorize_entries(exe_path, self.entries) if self.build is not None else None

        if categories is not None:
            # Grouped by which pointer table references each entry (see
            # mainbin_editor.BUILDS' "tables" and categorize_entries) -
            # purely a browsing aid, has no bearing on what's editable.
            category_order = [t["label"] for t in self.build["tables"]] + [PINNED_CATEGORY]
            folders = {}
            for label in category_order:
                folder = QStandardItem(label)
                folder.setFlags(folder.flags() & ~Qt.ItemFlag.ItemIsEditable)
                folder.setForeground(QBrush(QColor("gray")))
                folders[label] = folder
            for e in self.entries:
                item = QStandardItem(self._entry_label(e))
                item.setData(e["offset"], ENTRY_LOCATION_ROLE)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._entry_items[e["offset"]] = item
                self._category_by_offset[e["offset"]] = categories[e["offset"]]
                folders[categories[e["offset"]]].appendRow(item)
            for label in category_order:
                folder = folders[label]
                if folder.rowCount() > 0:
                    base_label = f"{label} ({folder.rowCount()})"
                    folder.setText(base_label)
                    self._folders[label] = folder
                    self._folder_labels[label] = base_label
                    root.appendRow(folder)
        else:
            for e in self.entries:
                item = QStandardItem(self._entry_label(e))
                item.setData(e["offset"], ENTRY_LOCATION_ROLE)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._entry_items[e["offset"]] = item
                root.appendRow(item)

        #self.tree.expandAll()
        self._update_pool_label()

    @staticmethod
    def _preview_text(text):
        return " ".join(text.split())

    def _entry_label(self, entry):
        pin = "[pinned] " if not _is_flowable(entry["offset"], self.build) else ""
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
        pinned = not _is_flowable(offset, self.build)

        self._loading = True
        self.text_edit.setPlainText(current_text)
        self.preview.set_text(current_text)
        self.text_edit.setReadOnly(pinned)
        self._loading = False

        entry = self._entries_by_offset[offset]
        print(f"selected: MAIN.EXE string @ 0x{offset:X}  "
              f"{entry.get('length', len(current_text))} bytes  "
              f"{len(current_text)} chars")

        self._update_status(offset, current_text)

    def _on_text_changed(self):
        if self._loading or self._current_entry_item is None:
            return
        offset = self._current_entry_item.data(ENTRY_LOCATION_ROLE)
        if offset is None or not _is_flowable(offset, self.build):
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
        self._refresh_category_marker(self._category_by_offset.get(offset))

    def _refresh_category_marker(self, label):
        """Marks a category folder with "*" while it contains at least
        one unsaved (pending, not-yet-exported) edit - mirroring how
        MainWindow marks the DAT Assets/MAIN.EXE/BINs tabs themselves."""
        if label is None:
            return
        folder = self._folders.get(label)
        if folder is None:
            return
        has_unsaved = any(self._category_by_offset.get(off) == label for off in self._edited_offsets)
        base_label = self._folder_labels[label]
        folder.setText(f"{base_label}*" if has_unsaved else base_label)

    def _update_status(self, offset, text):
        if not _is_flowable(offset, self.build):
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
        elif offset in self._migration_offsets:
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText(
                "Legacy Polish glyph byte - will be corrected for the "
                "system font on the next save.")
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
                "Unrecognized game build - pointer tables haven't been mapped for "
                "this version, so entries are view-only. No editing or saving."
            )
            return
        state = compute_pool_state(self.entries, self.build, self.pending_edits_for_pool())
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

    def apply_import(self, texts):
        """Put imported text into the pool.

        Returns (offsets applied, offsets refused as pinned, ids that
        named nothing here). A pinned entry is refused rather than
        written: it has no reference the repacker can move, so its text
        would either be dropped or land on top of something else. A
        build whose tables are not mapped has no flowable entries at
        all, which is what refuses the whole import there."""
        from gui.txtd import translation_io

        changed, missed = translation_io.apply_pool(self.entries, texts)
        applied, pinned = [], []
        for offset, text in sorted(changed.items()):
            if not _is_flowable(offset, self.build):
                pinned.append(offset)
                continue
            self._entries_by_offset[offset]["text"] = text
            if text == self._original_texts.get(offset):
                self._edited_offsets.discard(offset)
            else:
                self._edited_offsets.add(offset)
            self._exported_offsets.discard(offset)
            applied.append(offset)
            item = self._entry_items.get(offset)
            if item is not None:
                self._set_item_state(item, offset)
        if applied:
            self._update_pool_label()
            self.content_changed.emit()
            self._reshow_current(applied)
        return applied, pinned, missed

    def _reshow_current(self, applied):
        """The selected entry is still on screen holding its old text if
        the import changed it."""
        item = self._current_entry_item
        offset = None if item is None else item.data(ENTRY_LOCATION_ROLE)
        if offset is None or offset not in applied:
            return
        self._loading = True
        try:
            text = self._entries_by_offset[offset]["text"]
            self.text_edit.setPlainText(text)
            self.preview.set_text(text)
        finally:
            self._loading = False
        self._update_status(offset, text)

    def pending_edits_for_pool(self):
        """{offset: text} for every flowable entry currently different
        from its original, edited or already-exported alike."""
        return {
            offset: self._entries_by_offset[offset]["text"]
            for offset in (self._edited_offsets | self._exported_offsets
                           | self._migration_offsets)
        }

    def pending_edits(self):
        """{offset: text} for entries dirty since the last export (orange) - UI only, not what a save should pack (see all_edits)."""
        return {offset: self._entries_by_offset[offset]["text"] for offset in self._edited_offsets}

    def all_edits(self):
        """{offset: text} for every entry differing from the on-disk file - what an export must reapply each time."""
        return self.pending_edits_for_pool()

    def _legacy_glyph_offsets(self, exe_path):
        """Entries whose displayed text re-encodes to different bytes.

        The old MAIN.EXE editor stored a custom font cell directly.  The
        game subtracts 0x20 before selecting an 8x8 system glyph, so a
        Polish cell 0x91 must be stored as 0xB1.  mainbin_parser still
        reads the old spelling for compatibility; this marks it for an
        automatic, byte-for-byte-length-preserving migration on Save.
        """
        try:
            with open(exe_path, "rb") as f:
                raw = f.read()
        except OSError:
            return set()
        migrated = set()
        for entry in self.entries:
            try:
                canonical = encode_bytes(entry["text"])
            except MainBinParseError:
                continue
            start = entry["offset"]
            old = raw[start:start + entry["length"]]
            if len(canonical) == len(old) and canonical != old:
                migrated.add(start)
        return migrated

    def has_pending_edits(self):
        return bool(self._edited_offsets or self._migration_offsets)

    def pending_state(self):
        return "edited" if (self._edited_offsets or self._migration_offsets) else None

    def pool_overflowing(self):
        if self.build is None:
            return False
        state = compute_pool_state(self.entries, self.build, self.pending_edits_for_pool())
        return state["free"] < 0 or bool(state["errors"])

    def mark_exported(self):
        """Flip every dirty entry from edited (orange) to exported (green)."""
        for offset in list(self._edited_offsets):
            self._edited_offsets.discard(offset)
            self._exported_offsets.add(offset)
            item = self._entry_items.get(offset)
            if item is not None:
                self._set_item_state(item, offset)

    def project_state(self):
        """JSON-safe edit markers saved with a Translation Project."""
        return {
            "edited": sorted(self._edited_offsets),
            "exported": sorted(self._exported_offsets),
        }

    def restore_project_state(self, state):
        """Restore orange/green rows and category asterisks after reopen."""
        known = set(self._entries_by_offset)
        self._edited_offsets = {
            int(offset) for offset in (state or {}).get("edited", ())
            if int(offset) in known}
        self._exported_offsets = {
            int(offset) for offset in (state or {}).get("exported", ())
            if int(offset) in known} - self._edited_offsets
        for offset, item in self._entry_items.items():
            self._set_item_state(item, offset)
        self._update_pool_label()

    def clear_cache(self):
        """Full reset - called before loading a new exe, and when a new
        ISO/folder is opened with no MAIN.EXE found in it."""
        self.exe_path = None
        self.build = None
        self.entries = []
        self._current_entry_item = None
        self._entry_items = {}
        self._entries_by_offset = {}
        self._edited_offsets = set()
        self._exported_offsets = set()
        self._migration_offsets = set()
        self._original_texts = {}
        self._folders = {}
        self._folder_labels = {}
        self._category_by_offset = {}

        self.tree_model.clear()
        self._loading = True
        self.text_edit.clear()
        self._loading = False
        self.text_edit.setReadOnly(True)
        self.status_label.setStyleSheet("color: gray;")
        self.status_label.setText("")
        self.pool_label.setText("")
        self.pool_toggle.setText("Text pool")

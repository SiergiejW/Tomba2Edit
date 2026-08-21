import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QStandardItem, QStandardItemModel, QFont, QIcon, QBrush, QColor,
    QSyntaxHighlighter, QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QTreeView, QWidget, QVBoxLayout, QSplitter, QMessageBox,
    QTextEdit, QLabel
)
import gui.txtd.txtd as txtd


# Import the necessary icons
from icons.icons import icon_TXTD_master, icon_TXTD_entry

# Custom item-data role used to link a tree row back to its
# (master_index, entry_index) position in self.current_data.
ENTRY_LOCATION_ROLE = Qt.ItemDataRole.UserRole + 10

# --- Entry tree row colors -------------------------------------------------
# Whole-row colors used in the *tree* to flag an entry's edit status. These
# are plain foreground overrides on the QStandardItem (no rich text) - the
# tree only ever shows one of: normal, EDITED (dirty, not yet exported), or
# EXPORTED (was edited and successfully exported at least once since).
EDITED_ENTRY_COLOR = "orange"
EXPORTED_ENTRY_COLOR = "green"

# --- {$COLOR} tag highlighting (edit box only) ------------------------------
# The game's dialogue text embeds control tags like "{$ORANGE}", which set
# the color of everything after them until the next color tag. These only
# get highlighted in the editable text box (self.text_edit) - never in the
# tree previews, which stay plain text.
STATE_DEFAULT = 0  # also what {$WHITE} resets to - left unstyled, since
                    # that's already the edit box's normal text color.
STATE_ORANGE = 1
STATE_BLUE = 2
STATE_PINK = 3
STATE_GREEN = 4

TAG_STATE = {
    "WHITE": STATE_DEFAULT,
    "ORANGE": STATE_ORANGE,
    "BLUE": STATE_BLUE,
    "PINK": STATE_PINK,
    "GREEN": STATE_GREEN,
}
# Colors chosen to read reasonably on both light and dark themes; tweak
# freely if they don't match the actual in-game colors.
STATE_COLOR = {
    STATE_DEFAULT: None,
    STATE_ORANGE: "orange",
    STATE_BLUE: "#3d8bfd",
    STATE_PINK: "#ff69b4",
    STATE_GREEN: "#3ddc84",
}

# Matches specifically the five recognized color/reset tags (these change
# the highlighter's current color state).
_COLOR_TAG_RE = re.compile(r"\{\$(" + "|".join(TAG_STATE) + r")\}")
# Matches ANY {$...} control tag, color-setting or not (e.g. {$END},
# {$FF}) - every one of these always renders gray, regardless of what it
# does. Tokens without the leading "$", like "{TRIANGLE}", are NOT control
# tags and are left as ordinary dialogue text.
_ANY_TAG_RE = re.compile(r"\{\$[^{}]*\}")

# Dim gray used for the literal {$...} tag text itself, so it doesn't
# visually compete with the actual dialogue around it.
TAG_TOKEN_COLOR = "#8a8a8a"


class EntryTextHighlighter(QSyntaxHighlighter):
    """Syntax-highlights a TXTD entry's raw text inside the edit box.

    Every {$...} control tag - whether it sets a color (WHITE/ORANGE/
    BLUE/PINK/GREEN) or not (e.g. {$END}, {$FF}) - is always rendered in
    TAG_TOKEN_COLOR (gray), so the tag syntax itself stays out of the way.
    The five color tags additionally change the color of the dialogue
    text that follows them, until the next color tag - including across
    line breaks, since a color can span multiple lines of an entry.
    {$WHITE} (and anything before the first tag) is left unstyled, which
    is already the edit box's normal default text color.

    Only ever attached to self.text_edit's document - the tree previews
    stay plain text, unaffected by any of this.
    """

    def __init__(self, document):
        super().__init__(document)
        self._tag_format = QTextCharFormat()
        self._tag_format.setForeground(QColor(TAG_TOKEN_COLOR))

        self._state_formats = {}
        for state, color in STATE_COLOR.items():
            if color is None:
                continue
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            fmt.setFontWeight(QFont.Weight.Bold)
            self._state_formats[state] = fmt

    def highlightBlock(self, text):
        state = self.previousBlockState()
        if state < 0:
            state = STATE_DEFAULT

        pos = 0
        for m in _ANY_TAG_RE.finditer(text):
            if m.start() > pos:
                self._apply_state(pos, m.start() - pos, state)

            # The tag token itself is always gray, regardless of state.
            self.setFormat(m.start(), len(m.group(0)), self._tag_format)

            color_match = _COLOR_TAG_RE.fullmatch(m.group(0))
            if color_match:
                state = TAG_STATE[color_match.group(1)]

            pos = m.end()

        if pos < len(text):
            self._apply_state(pos, len(text) - pos, state)

        self.setCurrentBlockState(state)

    def _apply_state(self, start, length, state):
        fmt = self._state_formats.get(state)
        if fmt is not None:
            self.setFormat(start, length, fmt)


class TXTDViewer(QWidget):
    """
    Displays a TXTD's master headers/entries and lets the user edit
    entry text in place. Every edit is written straight back into
    self.current_data as it's typed (no separate "save" step to
    remember), and `content_changed` is emitted so the owning window
    can track which TXTD chunks have pending edits ready to export.
    """

    # (chunk_index, file_index, id_val, dat_start, offset, current_data)
    content_changed = pyqtSignal(int, int, int, int, int, dict)

    def __init__(self):
        super().__init__()
        self.current_data = None
        self.chunk_index = None
        self.file_index = None
        self.id_val = None
        self.dat_start = None
        self.offset = None

        self._current_entry_item = None
        self._loading = False  # guards against textChanged firing during programmatic updates

        # (master_index, entry_index) -> QStandardItem, for quick lookup by
        # mark_exported() below. Reset in load_txtd_data().
        self._entry_items = {}
        # locations edited since the current TXTD was loaded, not yet
        # exported - rendered EDITED_ENTRY_COLOR (orange) in the tree.
        self._edited_locations = set()
        # locations that were edited and have since been successfully
        # exported (see mark_exported()) - rendered EXPORTED_ENTRY_COLOR
        # (green) until touched again, at which point they go back to
        # _edited_locations/orange.
        self._exported_locations = set()

        # (chunk_index, file_index) -> {"data", "edited_locations",
        # "exported_locations"} for every TXTD loaded at least once, so
        # switching away and back preserves edits/coloring instead of
        # re-reading fresh from disk. The *same* dict/set objects are
        # shared between here and the cache entry, so edits and
        # mark_exported() keep it correct with no extra bookkeeping.
        # Cleared via clear_cache() when a new ISO is opened.
        self._file_state_cache = {}

        # location -> original text, so edits reverted back to the
        # original clear the edited/exported marks again.
        self._original_entry_texts = {}

        # Create the main layout
        layout = QVBoxLayout()

        # Create a splitter to divide tree view and text edit
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Create the tree view
        self.tree = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Create a QTextEdit for text input with larger font
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Select an entry to view/edit its text")
        self.text_edit.setReadOnly(True)

        # Configure larger font
        font = QFont("Courier New", 12)  # Font family and size (12pt)
        font.setWeight(QFont.Weight.Bold)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setFont(font)

        # Optional: Increase the minimum width for better readability
        self.text_edit.setMinimumWidth(400)

        # Highlights {$COLOR} tags in the edit box only (see class docstring).
        # Keep a reference so it isn't garbage-collected.
        self._entry_text_highlighter = EntryTextHighlighter(self.text_edit.document())

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")

        right_layout.addWidget(self.text_edit)
        right_layout.addWidget(self.status_label)
        right_panel.setLayout(right_layout)

        # Add widgets to the splitter
        splitter.addWidget(self.tree)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])

        # Add the splitter to the main layout
        layout.addWidget(splitter)
        self.setLayout(layout)

        # Connect selection change signal
        self.tree.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.text_edit.textChanged.connect(self._on_text_changed)

    @staticmethod
    def _entry_preview_text(text):
        """Collapse an entry's raw text into a single readable line for the
        tree - whitespace/newlines collapsed to single spaces, but no
        length limit, so the preview always shows the entry's full text
        (tags included; {$COLOR} tags aren't specially rendered here, only
        in the edit box - see EntryTextHighlighter)."""
        if not text:
            return ""
        return " ".join(text.split())

    def _entry_label_parts(self, entry):
        """Returns (address_str, preview, is_sentinel), the shared pieces
        used to build the tree label."""
        is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)
        addr_str = f"Entry {entry['adr']:04X}"
        if is_sentinel:
            return addr_str, None, True

        text = entry.get("text") or ""
        return addr_str, self._entry_preview_text(text), False

    def _entry_label(self, entry):
        """Plain-text tree label, e.g.
        'Entry 0000 (Hey! You scared me. What are you doing here?)'.
        END-marker sentinels (no text) get a simpler label."""
        addr_str, preview, is_sentinel = self._entry_label_parts(entry)
        if is_sentinel:
            return f"{addr_str} (END marker)"
        if preview:
            return f"{addr_str} ({preview})"
        return addr_str

    def _entry_item_color(self, location):
        """The whole-row color for an entry's tree item: orange while it
        has an unexported edit, green if it was edited and has since been
        exported, or None (normal) if it's never been touched."""
        if location in self._edited_locations:
            return EDITED_ENTRY_COLOR
        if location in self._exported_locations:
            return EXPORTED_ENTRY_COLOR
        return None

    def _set_entry_item_label(self, item, entry, location):
        item.setText(self._entry_label(entry))
        color = self._entry_item_color(location)
        if color:
            item.setForeground(QBrush(QColor(color)))
        else:
            item.setData(None, Qt.ItemDataRole.ForegroundRole)

    def load_txtd_data(self, DAT, datstart, offset, chunk_index=None, file_index=None, id_val=None):
        """
        chunk_index/file_index/id_val identify which SDAT slot this
        TXTD came from (AREA index, file index within that area, and
        its type id). They're needed to export edits back into the
        DAT/IDX later - pass them whenever the caller has them (see
        main_window.py's on_tree_selection_changed).
        """
        self._loading = True
        try:
            cache_key = (chunk_index, file_index) if chunk_index is not None and file_index is not None else None
            cached = self._file_state_cache.get(cache_key) if cache_key is not None else None

            if cached is not None:
                # Seen this file before - reuse its edited text and
                # orange/green state instead of re-reading the original
                # bytes off disk (which would silently discard both).
                print(f"Reusing cached TXTD data for chunk={chunk_index}, file={file_index}")
                self.current_data = cached["data"]
                self._edited_locations = cached["edited_locations"]
                self._exported_locations = cached["exported_locations"]
                self._original_entry_texts = cached.get("original_entry_texts", {})
            else:
                print(f"Loading TXTD data from DAT file: {DAT}, start: {datstart}, offset: {offset}")
                self.current_data = txtd.preview(DAT, datstart + offset)
                print("TXTD data loaded successfully.")
                self._edited_locations = set()
                self._exported_locations = set()
                self._original_entry_texts = {}
                for m_idx, group in enumerate(self.current_data.get("entries", [])):
                    for e_idx, e in enumerate(group.get("entries", [])):
                        self._original_entry_texts[(m_idx, e_idx)] = e["text"]
                if cache_key is not None:
                    self._file_state_cache[cache_key] = {
                        "data": self.current_data,
                        "edited_locations": self._edited_locations,
                        "exported_locations": self._exported_locations,
                        "original_entry_texts": self._original_entry_texts,
                    }

            self.chunk_index = chunk_index
            self.file_index = file_index
            self.id_val = id_val
            self.dat_start = datstart
            self.offset = offset
            self._current_entry_item = None
            self._entry_items = {}

            self.tree_model.clear()
            self.text_edit.clear()
            self.text_edit.setReadOnly(True)
            self.status_label.setText("")

            if not self.current_data:
                print("No data to display")
                return

            master_headers = self.current_data.get("master_headers", [])
            entry_groups = self.current_data.get("entries", [])

            for m_idx, master_header in enumerate(master_headers):
                master_item = QStandardItem(QIcon(icon_TXTD_master), f"Master Header {master_header['adr']:04X}")
                master_item.setFlags(master_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.tree_model.appendRow(master_item)

                entry_group = entry_groups[m_idx] if m_idx < len(entry_groups) else None
                if entry_group is not None:
                    master_item.setText(
                        f"Master Header {master_header['adr']:04X} ({entry_group['entry_amount']} entries)")

                    for e_idx, entry in enumerate(entry_group.get("entries", [])):
                        location = (m_idx, e_idx)
                        entry_item = QStandardItem(QIcon(icon_TXTD_entry), "")
                        self._entry_items[location] = entry_item
                        self._set_entry_item_label(entry_item, entry, location)
                        entry_item.setFlags(entry_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        entry_item.setData(location, ENTRY_LOCATION_ROLE)
                        master_item.appendRow(entry_item)

            self.tree.expandAll()
            print("Tree view populated successfully")

        except Exception as e:
            print(f"Error loading TXTD data: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load TXTD data: {e}")
        finally:
            self._loading = False

    def on_tree_selection_changed(self):
        self._loading = True
        try:
            selected_indexes = self.tree.selectionModel().selectedIndexes()
            if not selected_indexes:
                self._current_entry_item = None
                return

            selected_item = self.tree_model.itemFromIndex(selected_indexes[0])
            location = selected_item.data(ENTRY_LOCATION_ROLE)

            if location is None:
                # A master-header row: show a summary, not editable.
                self._current_entry_item = None
                self.text_edit.setReadOnly(True)
                self.text_edit.setText(f"Master Header with {selected_item.rowCount()} entries")
                self.status_label.setText("")
                return

            m_idx, e_idx = location
            entry = self.current_data["entries"][m_idx]["entries"][e_idx]
            is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)

            self._current_entry_item = selected_item
            self.text_edit.setPlainText(entry["text"])
            self.text_edit.setReadOnly(is_sentinel)
            self.status_label.setText(
                "This is an END marker (no text) - not editable." if is_sentinel else ""
            )
        except Exception as e:
            print(f"Error in on_tree_selection_changed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to handle selection change: {e}")
        finally:
            self._loading = False

    def _on_text_changed(self):
        """Writes every keystroke straight back into current_data, and
        tells the owning window this TXTD now has a pending edit."""
        if self._loading or self._current_entry_item is None:
            return
        location = self._current_entry_item.data(ENTRY_LOCATION_ROLE)
        if location is None:
            return

        m_idx, e_idx = location
        entry = self.current_data["entries"][m_idx]["entries"][e_idx]
        entry["text"] = self.text_edit.toPlainText()

        if entry["text"] == self._original_entry_texts.get(location):
            # back to the original text - no longer dirty
            self._edited_locations.discard(location)
            self._exported_locations.discard(location)
        else:
            self._edited_locations.add(location)
            self._exported_locations.discard(location)

        # Keep the tree label in sync live as the user types.
        self._set_entry_item_label(self._current_entry_item, entry, location)

        if None not in (self.chunk_index, self.file_index, self.id_val, self.dat_start, self.offset):
            self.content_changed.emit(
                self.chunk_index, self.file_index, self.id_val,
                self.dat_start, self.offset, self.current_data
            )
            self.status_label.setText("Edited - will be included in the next Export Files.")

    def mark_exported(self, chunk_index, file_index):
        """Called by the owning window right after a successful export
        (Export Files / Export ISO), once per TXTD file that was actually
        included in it. Every entry that was dirty (orange) for that file
        flips to EXPORTED_ENTRY_COLOR (green) - "edited and saved" - until
        it's edited again, at which point it goes back to orange.

        This updates the file's state whether or not it's the one
        currently displayed: if it *is* currently displayed, the visible
        tree rows are recolored immediately; otherwise the update lands in
        _file_state_cache so the colors are already correct the next time
        that file is loaded (see load_txtd_data())."""
        is_current = (self.chunk_index, self.file_index) == (chunk_index, file_index)

        if is_current:
            edited_locations = self._edited_locations
            exported_locations = self._exported_locations
        else:
            cached = self._file_state_cache.get((chunk_index, file_index))
            if cached is None:
                return
            edited_locations = cached["edited_locations"]
            exported_locations = cached["exported_locations"]

        if not edited_locations:
            return

        newly_exported = set(edited_locations)
        edited_locations.clear()
        exported_locations |= newly_exported

        if not is_current:
            return  # that file's tree rows don't exist right now; the
                     # cache update above is picked up on next load.

        for location in newly_exported:
            item = self._entry_items.get(location)
            if item is None:
                continue
            m_idx, e_idx = location
            entry = self.current_data["entries"][m_idx]["entries"][e_idx]
            self._set_entry_item_label(item, entry, location)

    def pending_state(self):
        """"edited"/"exported"/None for the currently loaded file, based
        on _edited_locations/_exported_locations."""
        if self._edited_locations:
            return "edited"
        if self._exported_locations:
            return "exported"
        return None

    def clear_cache(self):
        """Forgets every cached per-file text/color state and resets the
        viewer to empty. Call this whenever a new ISO is opened - without
        it, (chunk_index, file_index) numbers from the old disc could
        collide with a completely different file on the new one."""
        self._file_state_cache = {}
        self.current_data = None
        self.chunk_index = None
        self.file_index = None
        self.id_val = None
        self.dat_start = None
        self.offset = None
        self._current_entry_item = None
        self._entry_items = {}
        self._edited_locations = set()
        self._exported_locations = set()
        self._original_entry_texts = {}

        self.tree_model.clear()
        self.text_edit.clear()
        self.text_edit.setReadOnly(True)
        self.status_label.setText("")
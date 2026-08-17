from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QFont, QIcon, QBrush, QColor
from PyQt6.QtWidgets import (
    QTreeView, QWidget, QVBoxLayout, QSplitter, QMessageBox,
    QTextEdit, QLabel
)
import gui.txtd.txt2 as txt2

# Reused as-is from the TXTD viewer, so TXT2 automatically gets the exact
# same {$COLOR} tag highlighting (and the same bold Courier New styling)
# in its edit box, and the exact same orange/green entry-row coloring
# convention in its tree - no drift between the two viewers' aesthetics.
from gui.txtd_viewer import (
    EntryTextHighlighter, EDITED_ENTRY_COLOR, EXPORTED_ENTRY_COLOR, ENTRY_LOCATION_ROLE,
)

# Import the necessary icon
from icons.icons import icon_TXT2_entry

# Muted color for entries txt2.is_probably_text() flags as likely NOT
# real dialogue (see that function's docstring - a real, non-truncated
# sample turned up two such entries: a perfectly regular numeric table
# with stale pointers sitting in the message slots, no 0xFFFF sentinel
# in sight). Deliberately distinct from EDITED_ENTRY_COLOR/
# EXPORTED_ENTRY_COLOR so a flagged-but-untouched entry never gets
# confused with a real pending/exported edit; touching one still turns
# it orange/green like any other entry; it's purely an unedited-state
# hint, not a data restriction.
NON_TEXT_ENTRY_COLOR = "gray"


class TXT2Viewer(QWidget):
    """
    Displays a TXT2 file's entries and lets the user edit entry text in
    place, exactly like TXTDViewer - just without the master-header
    grouping layer, since a TXT2 chunk is structurally just ONE flat
    table of entries (see gui/txtd/txt2.py's own docstring for why).
    Every edit is written straight back into self.current_data as it's
    typed (no separate "save" step to remember), and `content_changed`
    is emitted so the owning window can track which TXT2 chunks have
    pending edits ready to export.
    """

    # (chunk_index, file_index, id_val, dat_start, offset, current_data)
    # - same signature as TXTDViewer.content_changed, so MainWindow can
    # handle both with a matching pair of nearly-identical slots.
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

        # entry_index -> QStandardItem, for quick lookup by mark_exported()
        # below. Reset in load_txt2_data(). (TXTD's equivalent is keyed by
        # (master_index, entry_index); TXT2 has no master layer, so a bare
        # entry_index is enough here.)
        self._entry_items = {}
        # entry indices edited since the current TXT2 file was loaded, not
        # yet exported - rendered EDITED_ENTRY_COLOR (orange) in the tree.
        self._edited_locations = set()
        # entry indices that were edited and have since been successfully
        # exported (see mark_exported()) - rendered EXPORTED_ENTRY_COLOR
        # (green) until touched again, at which point they go back to
        # _edited_locations/orange.
        self._exported_locations = set()

        # (chunk_index, file_index) -> {"data", "edited_locations",
        # "exported_locations"} for every TXT2 file that's been loaded at
        # least once - same caching trick as TXTDViewer, so switching to
        # some other file and back doesn't re-read the original bytes off
        # disk and lose edited text / orange-green coloring. Cleared via
        # clear_cache() whenever a new ISO is opened.
        self._file_state_cache = {}

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

        # Configure larger font - matches TXTDViewer exactly.
        font = QFont("Courier New", 12)  # Font family and size (12pt)
        font.setWeight(QFont.Weight.Bold)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setFont(font)

        # Optional: Increase the minimum width for better readability
        self.text_edit.setMinimumWidth(400)

        # Highlights {$COLOR} tags in the edit box only (see
        # EntryTextHighlighter's own docstring in gui/txtd_viewer.py).
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
        used to build the tree label. Gap entries (see txt2.py's
        docstring - real text found between the table and entry_root,
        not addressed by any (adr,extra) pair) have no adr of their own,
        so they get an "Extra text" label instead of "Entry XXXX".

        NOTE: this text still lives entirely inside THIS file, between
        this file's own table and this file's own entry_root - it is
        never read from anywhere else. The label used to say "Shared
        text", which was meant as "shares/reuses bytes with another
        entry's string" (see txt2.py's docstring on tail-sharing) but
        reads, at a glance, like "text shared in from outside this
        file" - the opposite of what's actually happening. Renamed to
        avoid that misreading.
        """
        if entry.get("is_gap"):
            return "Extra text", self._entry_preview_text(entry.get("text") or ""), False

        is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)
        addr_str = f"Entry {entry['adr']:04X}"
        if is_sentinel:
            return addr_str, None, True

        text = entry.get("text") or ""
        return addr_str, self._entry_preview_text(text), False

    def _entry_label(self, entry):
        """Plain-text tree label, e.g.
        'Entry 0000 (Use {$UP} to jump to the back)'.
        END-marker sentinels (no text) get a simpler label, and entries
        txt2.is_probably_text() doesn't trust get a "(?)" flag appended
        so it's obvious at a glance which ones probably aren't real,
        editable dialogue (see that function's docstring)."""
        addr_str, preview, is_sentinel = self._entry_label_parts(entry)
        if is_sentinel:
            return f"{addr_str} (END marker)"
        suspect_flag = "" if txt2.is_probably_text(entry.get("text") or "") else "  [not text?]"
        if preview:
            return f"{addr_str} ({preview}){suspect_flag}"
        return f"{addr_str}{suspect_flag}"

    def _entry_item_color(self, entry, location):
        """The whole-row color for an entry's tree item: orange while it
        has an unexported edit, green if it was edited and has since
        been exported, gray if it's untouched but txt2.is_probably_text()
        doesn't trust its content, or None (normal) otherwise. Edit
        state always wins over the gray "suspect" hint - once you touch
        an entry it's tracked as a real edit like any other, regardless
        of what it looked like before."""
        if location in self._edited_locations:
            return EDITED_ENTRY_COLOR
        if location in self._exported_locations:
            return EXPORTED_ENTRY_COLOR
        is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)
        if not is_sentinel and not txt2.is_probably_text(entry.get("text") or ""):
            return NON_TEXT_ENTRY_COLOR
        return None

    def _set_entry_item_label(self, item, entry, location):
        item.setText(self._entry_label(entry))
        color = self._entry_item_color(entry, location)
        if color:
            item.setForeground(QBrush(QColor(color)))
        else:
            item.setData(None, Qt.ItemDataRole.ForegroundRole)

    @staticmethod
    def _debug_dump_entries(chunk_index, file_index, id_val, entries):
        """Prints every entry in this TXT2 file to stdout, one line each -
        index, raw adr/extra, and the exact decoded text (repr'd, so
        embedded newlines/tags are visible rather than breaking the
        layout). Runs every time a TXT2 file is selected in the main
        tree, whether its data came fresh off disk or from cache, so
        it always reflects what's actually about to be shown/edited.
        This exists to make things like the "chopped into entries
        wrongly" table-overrun bug (see txt2.py's docstring) easy to
        spot by eye - a quick scan for entries whose adr/extra don't
        ascend sensibly, or whose text is an unexpected {$EOF}/{$XX}
        run, points straight at the offending slot instead of having to
        step through the parser. Entries txt2.is_probably_text() flags
        as likely non-dialogue get a "<-- NOT TEXT?" marker here too,
        for the same reason the tree shows them in gray. Gap entries
        (see txt2.py's docstring) have no adr/extra of their own - shown
        as "adr=GAP" instead of a hex address."""
        print(f"=== TXT2 entry dump: chunk={chunk_index} file={file_index} "
              f"id={id_val} ({len(entries)} entries) ===")
        for i, entry in enumerate(entries):
            text = entry.get("text", "")
            flag = "" if txt2.is_probably_text(text) else "  <-- NOT TEXT?"
            if entry.get("is_gap"):
                print(f"  [{i:3d}] adr=GAP  extra=GAP  text={text!r}{flag}")
            else:
                adr = entry.get("adr") or 0
                extra = entry.get("extra") or 0
                print(f"  [{i:3d}] adr={adr:04X} extra={extra:04X} text={text!r}{flag}")
        print(f"=== end TXT2 entry dump ({len(entries)} entries) ===")

    def load_txt2_data(self, DAT, datstart, offset, chunk_index=None, file_index=None, id_val=None):
        """
        chunk_index/file_index/id_val identify which SDAT slot this TXT2
        file came from (AREA index, file index within that area, and its
        type id - 2 or 3). They're needed to export edits back into the
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
                print(f"Reusing cached TXT2 data for chunk={chunk_index}, file={file_index}")
                self.current_data = cached["data"]
                self._edited_locations = cached["edited_locations"]
                self._exported_locations = cached["exported_locations"]
            else:
                print(f"Loading TXT2 data from DAT file: {DAT}, start: {datstart}, offset: {offset}")
                self.current_data = txt2.preview(DAT, datstart + offset)
                print("TXT2 data loaded successfully.")
                self._edited_locations = set()
                self._exported_locations = set()
                if cache_key is not None:
                    self._file_state_cache[cache_key] = {
                        "data": self.current_data,
                        "edited_locations": self._edited_locations,
                        "exported_locations": self._exported_locations,
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

            entries = self.current_data.get("entries", [])
            self._debug_dump_entries(chunk_index, file_index, id_val, entries)

            # No master-header grouping layer for TXT2 - every entry is a
            # top-level row in the tree.
            for e_idx, entry in enumerate(entries):
                location = e_idx
                entry_item = QStandardItem(QIcon(icon_TXT2_entry), "")
                self._entry_items[location] = entry_item
                self._set_entry_item_label(entry_item, entry, location)
                entry_item.setFlags(entry_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                entry_item.setData(location, ENTRY_LOCATION_ROLE)
                self.tree_model.appendRow(entry_item)

            print("Tree view populated successfully")

        except Exception as e:
            print(f"Error loading TXT2 data: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load TXT2 data: {e}")
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
                # Shouldn't normally happen (every row in this tree is an
                # entry row - there's no master-header row to select), but
                # guard anyway rather than assume.
                self._current_entry_item = None
                self.text_edit.setReadOnly(True)
                self.text_edit.clear()
                self.status_label.setText("")
                return

            e_idx = location
            entry = self.current_data["entries"][e_idx]
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
        tells the owning window this TXT2 file now has a pending edit."""
        if self._loading or self._current_entry_item is None:
            return
        location = self._current_entry_item.data(ENTRY_LOCATION_ROLE)
        if location is None:
            return

        e_idx = location
        entry = self.current_data["entries"][e_idx]
        entry["text"] = self.text_edit.toPlainText()

        # A fresh edit always means "dirty" again, even if it was
        # previously exported (green) - touching it moves it back to
        # pending/orange.
        self._edited_locations.add(location)
        self._exported_locations.discard(location)

        # Keep the tree label in sync live as the user types.
        self._set_entry_item_label(self._current_entry_item, entry, location)

        if None not in (self.chunk_index, self.file_index, self.id_val, self.dat_start, self.offset):
            self.content_changed.emit(
                self.chunk_index, self.file_index, self.id_val,
                self.dat_start, self.offset, self.current_data
            )
            self.status_label.setText("Edited - will be included in the next Save IDX/DAT.")

    def mark_exported(self, chunk_index, file_index):
        """Called by the owning window right after a successful export
        (Save IDX/DAT / Save ISO), once per TXT2 file that was actually
        included in it. Every entry that was dirty (orange) for that file
        flips to EXPORTED_ENTRY_COLOR (green) - "edited and saved" - until
        it's edited again, at which point it goes back to orange.

        This updates the file's state whether or not it's the one
        currently displayed: if it *is* currently displayed, the visible
        tree rows are recolored immediately; otherwise the update lands in
        _file_state_cache so the colors are already correct the next time
        that file is loaded (see load_txt2_data())."""
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
            e_idx = location
            entry = self.current_data["entries"][e_idx]
            self._set_entry_item_label(item, entry, location)

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

        self.tree_model.clear()
        self.text_edit.clear()
        self.text_edit.setReadOnly(True)
        self.status_label.setText("")
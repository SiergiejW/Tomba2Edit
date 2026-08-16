from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QFont, QIcon
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

# How many characters of an entry's text to show inline in the tree, before
# truncating with "...". Keeps rows scannable while still being useful for
# telling entries apart at a glance during translation.
ENTRY_PREVIEW_MAX_CHARS = 40


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
        font = QFont("Courier New", 10)  # Font family and size (12pt)
        self.text_edit.setFont(font)

        # Optional: Increase the minimum width for better readability
        self.text_edit.setMinimumWidth(400)

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
    def _entry_preview_text(text, max_len=ENTRY_PREVIEW_MAX_CHARS):
        """Collapse an entry's text down to a single-line snippet suitable
        for showing inline in the tree (whitespace/newlines collapsed,
        truncated with '...' if it's longer than max_len)."""
        if not text:
            return ""
        collapsed = " ".join(text.split())
        if not collapsed:
            return ""
        if len(collapsed) > max_len:
            return collapsed[:max_len].rstrip() + "..."
        return collapsed

    def _entry_label(self, entry):
        """Builds the tree label for one entry, e.g.
        'Entry 0000 (Hey! You scared me. What are you...) (87 characters)'.
        END-marker sentinels (no text) get a simpler label."""
        is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)
        addr_str = f"Entry {entry['adr']:04X}"
        if is_sentinel:
            return f"{addr_str} (END marker)"

        text = entry.get("text") or ""
        char_count = len(text)
        preview = self._entry_preview_text(text)
        if preview:
            return f"{addr_str} ({char_count} chars) ({preview})"
        return f"{addr_str} ({char_count} characters)"

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
            print(f"Loading TXTD data from DAT file: {DAT}, start: {datstart}, offset: {offset}")
            self.current_data = txtd.preview(DAT, datstart + offset)
            print("TXTD data loaded successfully.")

            self.chunk_index = chunk_index
            self.file_index = file_index
            self.id_val = id_val
            self.dat_start = datstart
            self.offset = offset
            self._current_entry_item = None

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
                        entry_item = QStandardItem(QIcon(icon_TXTD_entry), self._entry_label(entry))
                        entry_item.setFlags(entry_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        entry_item.setData((m_idx, e_idx), ENTRY_LOCATION_ROLE)
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

        # Keep the tree label's preview/character-count in sync live as the
        # user types, so it's always an accurate reflection of the text.
        self._current_entry_item.setText(self._entry_label(entry))

        if None not in (self.chunk_index, self.file_index, self.id_val, self.dat_start, self.offset):
            self.content_changed.emit(
                self.chunk_index, self.file_index, self.id_val,
                self.dat_start, self.offset, self.current_data
            )
            self.status_label.setText("Edited - will be included in the next Export Files.")
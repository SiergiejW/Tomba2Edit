from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QFont, QIcon
from PyQt6.QtWidgets import (
    QTreeView, QWidget, QVBoxLayout, QSplitter, QMessageBox, QTextEdit
)
import gui.txtd.txtd as txtd


# Import the necessary icons
from icons.icons import icon_TXTD_master, icon_TXTD_entry

class TXTDViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.current_data = None

        # Create the main layout
        layout = QVBoxLayout()

        # Create a splitter to divide tree view and text edit
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Create the tree view
        self.tree = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)

        # Create a QTextEdit for text input with larger font
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Select an entry to view its text")
        self.text_edit.setReadOnly(True)

        # Configure larger font
        font = QFont("Courier New", 10)  # Font family and size (12pt)
        self.text_edit.setFont(font)

        # Optional: Increase the minimum width for better readability
        self.text_edit.setMinimumWidth(400)

        # Add widgets to the splitter
        splitter.addWidget(self.tree)
        splitter.addWidget(self.text_edit)
        splitter.setSizes([300, 700])

        # Add the splitter to the main layout
        layout.addWidget(splitter)
        self.setLayout(layout)

        # Connect selection change signal
        self.tree.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

    def load_txtd_data(self, DAT, datstart, offset):
        try:
            print(f"Loading TXTD data from DAT file: {DAT}, start: {datstart}, offset: {offset}")
            self.current_data = txtd.preview(DAT, datstart + offset)
            print("TXTD data loaded successfully.")

            self.tree_model.clear()
            self.text_edit.clear()

            if not self.current_data:
                print("No data to display")
                return

            # Process master headers
            for master_header in self.current_data.get("master_headers", []):
                master_item = QStandardItem(QIcon(icon_TXTD_master), f"Master Header {master_header['adr']:04X}")
                master_item.setFlags(master_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.tree_model.appendRow(master_item)

                # Find corresponding entries
                for entry_group in self.current_data.get("entries", []):
                    if entry_group["master_adr"] == master_header["adr"]:
                        master_item.setText(
                            f"Master Header {master_header['adr']:04X} ({entry_group['entry_amount']} entries)")

                        # Add sub-entries
                        for entry in entry_group.get("entries", []):
                            entry_item = QStandardItem(QIcon(icon_TXTD_entry), f"Entry {entry['adr']:04X}")
                            entry_item.setData(entry["text"], Qt.ItemDataRole.UserRole)
                            entry_item.setFlags(entry_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                            master_item.appendRow(entry_item)

            self.tree.expandAll()
            print("Tree view populated successfully")

        except Exception as e:
            print(f"Error loading TXTD data: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load TXTD data: {e}")

    def on_tree_selection_changed(self):
        try:
            selected_indexes = self.tree.selectionModel().selectedIndexes()
            if selected_indexes:
                selected_index = selected_indexes[0]
                selected_item = self.tree_model.itemFromIndex(selected_index)

                if selected_item.hasChildren():
                    # For master headers, show summary
                    self.text_edit.setText(f"Master Header with {selected_item.rowCount()} entries")
                else:
                    # For entries, show the text with original formatting
                    text = selected_item.data(Qt.ItemDataRole.UserRole)
                    if text:
                        # Preserve the original formatting including tabs and newlines
                        self.text_edit.setPlainText(text)
        except Exception as e:
            print(f"Error in on_tree_selection_changed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to handle selection change: {e}")
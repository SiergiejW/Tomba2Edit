import os
import sys
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle
)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TreeView Example")
        self.resize(800, 600)

        # Load icons for file types
        self.folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self.splitter)

        self.tree_view = QTreeView()
        self.splitter.addWidget(self.tree_view)

        self.setup_tree_view()
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

        container_widget = QWidget()
        container_layout = QVBoxLayout()
        toolbar = QToolBar("Main Toolbar")
        container_layout.addWidget(toolbar)
        action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open", self)
        action.triggered.connect(self.open_folder_dialog)
        toolbar.addAction(action)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        self.folder_info_label = QLabel("Select a folder to populate the tree view")
        self.folder_info_label.setWordWrap(True)
        container_layout.addWidget(self.folder_info_label)
        container_widget.setLayout(container_layout)
        self.setMenuWidget(container_widget)

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.folder_info_label.setText(f"Selected Folder: {folder}")
            self.populate_tree_view(folder)

    def populate_tree_view(self, folder_path):
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Name"])
        root_item = model.invisibleRootItem()

        self.add_folder_contents_to_tree(folder_path, root_item)

        # Ensure the model is set correctly
        self.tree_view.setModel(model)
        self.tree_view.expandAll()

        # Connect the signal here again to ensure the handler is connected
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

    def add_folder_contents_to_tree(self, folder_path, parent_item):
        for item_name in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item_name)
            if os.path.isdir(item_path):
                folder_item = QStandardItem(self.folder_icon, item_name)
                folder_item.setFlags(folder_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                parent_item.appendRow(folder_item)
                self.add_folder_contents_to_tree(item_path, folder_item)
            else:
                file_item = QStandardItem(self.file_icon, item_name)
                file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                parent_item.appendRow(file_item)

            print(f"Added Item: {item_name}")  # Debugging print

    def on_tree_selection_changed(self):
        print("Selection changed!")
        selected_indexes = self.tree_view.selectionModel().selectedIndexes()
        if selected_indexes:
            selected_index = selected_indexes[0]
            selected_item = self.tree_view.model().itemFromIndex(selected_index)
            item_name = selected_item.data(Qt.ItemDataRole.DisplayRole)
            print(f"Selected Item Index: {selected_index}")
            print(f"Item Name: {item_name}")

    def setup_tree_view(self):
        self.tree_view.setModel(QStandardItemModel())
        self.tree_view.setHeaderHidden(False)

app = QApplication(sys.argv)
window = MainWindow()
window.show()
app.exec()
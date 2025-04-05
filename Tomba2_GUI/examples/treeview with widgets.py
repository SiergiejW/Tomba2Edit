import sys
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTreeView,
    QWidget,
    QVBoxLayout,
    QLabel,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QStyle,
    QFileDialog,
    QHBoxLayout,
    QFrame,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("TreeView with Icons Example")
        self.resize(800, 600)

        # Main splitter to divide tree view and widgets area
        self.splitter = QSplitter()
        self.splitter.setOrientation(Qt.Orientation.Horizontal)
        self.setCentralWidget(self.splitter)

        # Tree view on the left
        self.tree_view = QTreeView()
        self.splitter.addWidget(self.tree_view)

        # Right-side widget area
        self.widgets_area = QStackedWidget()
        self.splitter.addWidget(self.widgets_area)

        # Initially set the treeview width to 30% of the window size
        initial_treeview_width = int(self.width() * 0.30)  # 30% of window width
        self.splitter.setSizes([initial_treeview_width, self.width() - initial_treeview_width])

        # Allow resizing between tree view and widgets area
        self.splitter.setStretchFactor(0, 0)  # Don't stretch treeview
        self.splitter.setStretchFactor(1, 1)  # Allow widgets area to stretch

        # Populate the tree view
        self.setup_tree_view()

        # Add widgets for folder and file
        self.setup_widgets()

        # Connect tree selection to widget change
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

        # Add a status bar
        self.setStatusBar(QStatusBar(self))

        # Create a container widget to hold toolbar and label
        container_widget = QWidget()
        container_layout = QVBoxLayout()

        # Add a toolbar with a built-in icon
        toolbar = QToolBar("Main Toolbar")
        container_layout.addWidget(toolbar)

        # Create the action with the 'Open' label and the dialog open icon
        action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open", self)
        action.setStatusTip("Open a folder")  # Optional status tip
        action.triggered.connect(self.open_folder_dialog)  # Connect to folder opening function
        toolbar.addAction(action)

        # Set the toolbar to show text below icons
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        # Label below the toolbar to display folder path and instructions
        self.folder_info_label = QLabel("Select Tomba folder (with BIN, CD, MOVIE)")
        self.folder_info_label.setWordWrap(True)
        self.folder_info_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        container_layout.addWidget(self.folder_info_label)

        container_widget.setLayout(container_layout)

        # Set container widget as the central widget or menu widget
        self.setMenuWidget(container_widget)

    def open_folder_dialog(self):
        # Open a folder selection dialog
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")

        if folder:
            # Update the label with the folder path
            self.folder_info_label.setText(f"Selected Folder: {folder}")
            print(f"Selected folder: {folder}")

            # You can add logic here to populate the tree view with files/folders in the selected folder
        else:
            # If no folder is selected, show the initial message
            self.folder_info_label.setText("Select Tomba folder (with BIN, CD, MOVIE)")

    def setup_tree_view(self):
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Name"])
        self.tree_view.setModel(model)

        # Populate the tree view with mock data
        root_item = model.invisibleRootItem()

        # Icons for folders and files
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        # Add folders and files
        for i in range(3):
            folder = QStandardItem(folder_icon, f"Folder {i + 1}")
            folder.setEditable(False)

            for j in range(2):
                file = QStandardItem(file_icon, f"File {i + 1}-{j + 1}.txt")
                file.setEditable(False)
                folder.appendRow(file)

            root_item.appendRow(folder)

        # Initially fold (collapse) the tree view
        self.tree_view.collapseAll()

    def setup_widgets(self):
        # Folder widget
        self.folder_widget = QWidget()
        folder_layout = QVBoxLayout()
        folder_label = QLabel("This is a folder")
        folder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)  # Center align horizontally and vertically
        folder_layout.addWidget(folder_label)
        self.folder_widget.setLayout(folder_layout)

        # File widget
        self.file_widget = QWidget()
        file_layout = QVBoxLayout()
        file_label = QLabel("This is a file")
        file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)  # Center align horizontally and vertically
        file_layout.addWidget(file_label)
        self.file_widget.setLayout(file_layout)

        # Add widgets to the stacked widget
        self.widgets_area.addWidget(self.folder_widget)
        self.widgets_area.addWidget(self.file_widget)

    def on_tree_selection_changed(self):
        # Get the selected item
        selected_indexes = self.tree_view.selectionModel().selectedIndexes()

        if not selected_indexes:
            return

        selected_item = selected_indexes[0].data()

        # Change widget based on item type
        if "Folder" in selected_item:
            self.widgets_area.setCurrentWidget(self.folder_widget)
        elif "File" in selected_item:
            self.widgets_area.setCurrentWidget(self.file_widget)


app = QApplication(sys.argv)

window = MainWindow()
window.show()

app.exec()



import os
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction, QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle,
)
from icons.icons import (icon_window,
                         icon_TXTD, icon_SPRT, icon_TANP, icon_SMST, icon_MDAT,
                         icon_SCLD, icon_BGMP, icon_BETP, icon_ALFD, icon_DRWB
                         )
from main import version
from gui.txtd_viewer import TXTDViewer
from gui.mdat_viewer import MDATViewer
from functions.idx_parser import parse_idx_file

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Tomba2Edit v{version}")
        self.resize(1400, 900)
        self.setWindowIcon(QIcon(icon_window))

        # Proceed with icon loading
        self.txtd_icon = QIcon(icon_TXTD)
        self.sprt_icon = QIcon(icon_SPRT)
        self.tanp_icon = QIcon(icon_TANP)
        self.smst_icon = QIcon(icon_SMST)
        self.scld_icon = QIcon(icon_SCLD)
        self.mdat_icon = QIcon(icon_MDAT)
        self.drwb_icon = QIcon(icon_DRWB)
        self.bgmp_icon = QIcon(icon_BGMP)
        self.betp_icon = QIcon(icon_BETP)
        self.alfd_icon = QIcon(icon_ALFD)

        self.folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self.splitter)

        self.tree_view = QTreeView()
        self.splitter.addWidget(self.tree_view)
        self.widgets_area = QStackedWidget()
        self.splitter.addWidget(self.widgets_area)

        self.setup_tree_view()
        self.setup_widgets()
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.setStatusBar(QStatusBar(self))

        container_widget = QWidget()
        container_layout = QVBoxLayout()
        toolbar = QToolBar("Main Toolbar")
        container_layout.addWidget(toolbar)
        action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open", self)
        action.triggered.connect(self.open_folder_dialog)
        toolbar.addAction(action)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        self.folder_info_label = QLabel("Select Tomba folder (with BIN, CD, MOVIE)")
        self.folder_info_label.setWordWrap(True)
        container_layout.addWidget(self.folder_info_label)
        container_widget.setLayout(container_layout)
        self.setMenuWidget(container_widget)

        initial_treeview_width = int(self.width() * 0.30)
        self.splitter.setSizes([initial_treeview_width, self.width() - initial_treeview_width])

    def id_convert(main_window, DAT, id, pointer_start):
        if id == 0:
            return "SPRT"
        elif id in [2, 3]:
            return "TXT2"
        elif id == 13:
            return "TXTD"
        elif id == 4 or id == 6:
            return "TANP"
        elif id == 7:
            return "SCLD"
        elif id == 8:
            DAT.seek(int(pointer_start, 16) + 4)
            if struct.unpack("<h", DAT.read(2))[0] == -1:
                return "MDAT"
        elif id == 9:
            return "DRWB"
        elif id == 10:
            return "SPRT"
        elif id == 11:
            return "BGMP"
        elif id in [12, 16, 1, 5]:
            return "SMST"
        elif id == 14:
            return "BETP"
        elif id == 17:
            return "ALFD"
        elif id >= 18:
            DAT.seek(int(pointer_start, 16) + 4)
            if struct.unpack("<h", DAT.read(2))[0] == -1:
                return "MDAT"
            DAT.seek(int(pointer_start, 16))
            if struct.unpack("<h", DAT.read(2))[0]:
                return "ALFD"
            else:
                return "SMST"
        else:
            return "NULL"

    def count_items(self, item):
        count = 0
        for i in range(item.rowCount()):
            child = item.child(i)
            if child.hasChildren():
                count += self.count_items(child)
            else:
                count += 1
        return count

    def update_folder_name(self, item):
        if item.hasChildren():
            count = self.count_items(item)
            item.setText(f"{item.text()} ({count})")

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            cd_folder = os.path.join(folder, "CD")
            if os.path.exists(cd_folder):
                required_files = ["TOMBA2.DAT", "TOMBA2.IDX", "TOMBA2.IMG"]
                if all(os.path.exists(os.path.join(cd_folder, f)) for f in required_files):
                    self.folder_info_label.setText(f"Selected Folder: {folder}")
                    parse_idx_file(self, cd_folder)
                else:
                    QMessageBox.critical(self, "Error", "The selected folder does not contain the required TOMBA2 files.")
            else:
                QMessageBox.critical(self, "Error", "The selected folder does not contain a 'CD' folder.")
        else:
            self.folder_info_label.setText("Select Tomba folder (with BIN, CD, MOVIE)")

    def tuplify(self, item):
        dat_id = item >> 24
        dat_ptr = item & 0x00FFFFFF
        return (dat_id, dat_ptr)


    def setup_tree_view(self):
        self.tree_view.setModel(QStandardItemModel())
        self.tree_view.setHeaderHidden(False)

    def setup_widgets(self):
        self.txtd_viewer = TXTDViewer()
        self.mdat_viewer = MDATViewer()  # Create an instance of MDATViewer

        self.widgets = {
            "Folder": QLabel("This is a folder"),
            "SPRT": QLabel("SPRITE Viewer"),
            "TXTD": self.txtd_viewer,
            "MDAT": self.mdat_viewer,  # Use our new MDAT viewer
            "DEFAULT": QLabel("File Viewer"),
        }
        for widget in self.widgets.values():
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter) if isinstance(widget, QLabel) else None
            self.widgets_area.addWidget(widget)

    def on_tree_selection_changed(self):
        try:
            selected_indexes = self.tree_view.selectionModel().selectedIndexes()
            if selected_indexes:
                selected_index = selected_indexes[0]
                selected_item = self.tree_view.model().itemFromIndex(selected_index)
                item_name = selected_item.data(Qt.ItemDataRole.DisplayRole)
                print(f"Selected Item Index: {selected_index}")
                print(f"Item Name: {item_name}")

                # Retrieve the additional data (id, dat_start, offset)
                additional_data = selected_item.data(Qt.ItemDataRole.UserRole)
                if additional_data:
                    id, dat_start, offset = additional_data
                    print(f"ID: {id:X}, DAT Start: {dat_start:X}, Offset: {offset:X}")

                    # Determine file type and get appropriate widget
                    file_type = item_name.split('.')[-1].upper() if '.' in item_name else "DEFAULT"
                    widget = self.widgets.get(file_type, self.widgets["DEFAULT"])
                    self.widgets_area.setCurrentWidget(widget)

                    if widget == self.widgets["TXTD"]:
                        file_path = selected_item.data(Qt.ItemDataRole.UserRole + 1)  # Retrieve stored file path
                        print(f"File path: {file_path}")
                        if file_path:
                            try:
                                if self.dat_file:  # Ensure the DAT file is available
                                    print("Loading TXTD data...")
                                    self.txtd_viewer.load_txtd_data(self.dat_file, dat_start, offset)
                                else:
                                    QMessageBox.critical(self, "Error", "DAT file not loaded.")
                            except Exception as e:
                                print(f"Error loading TXTD file: {e}")
                                QMessageBox.critical(self, "Error", f"Failed to load TXTD file: {e}")

                    elif widget == self.widgets["MDAT"]:
                        try:
                            if self.dat_file:
                                print("Loading MDAT data...")
                                success = self.mdat_viewer.load_mdat_data(self.dat_file, dat_start, offset)
                                if not success:
                                    QMessageBox.critical(self, "Error", "Failed to load MDAT data")
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading MDAT file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load MDAT file: {e}")

                    else:
                        print(f"No specialized viewer for {file_type} files")
                else:
                    print("No additional data found.")
        except Exception as e:
            print(f"Error in on_tree_selection_changed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to handle selection change: {e}")
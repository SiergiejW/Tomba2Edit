import os
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle,
)
from icons.icons import (icon_window,
                         icon_TXTD, icon_SPRT, icon_TANP, icon_SMST, icon_MDAT,
                         icon_SCLD, icon_BGMP, icon_BETP, icon_ALFD, icon_DRWB,
                         icon_VRAM, icon_CVRAM,
                         )
from main import version
from gui.txtd_viewer import TXTDViewer
from gui.mdat_viewer import MDATViewer
from functions.idx_parser import parse_idx_file
from gui.vram_viewer import VRAMViewer
from iso_handler import ISOHandler
from PIL.ImageQt import ImageQt


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Tomba2Edit v{version}")
        self.resize(1400, 900)
        self.setWindowIcon(QIcon(icon_window))

        # Initialize variables
        self.temp_dir = None
        self.dat_file = None
        self.idx_file = None
        self.img_file = None
        self.iso_path = None
        self.iso_handler = ISOHandler()

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
        self.vram_icon = QIcon(icon_VRAM)
        self.cvram_icon = QIcon(icon_CVRAM)

        self.folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self.splitter)

        self.tree_view = QTreeView()
        self.splitter.addWidget(self.tree_view)
        self.widgets_area = QStackedWidget()
        self.splitter.addWidget(self.widgets_area)

        # (chunk_index, file_index) -> {"id", "dat_start", "offset", "data"}
        # for every TXTD that's been edited but not yet exported.
        self.pending_txtd_edits = {}

        self.setup_tree_view()
        self.setup_widgets()
        self.txtd_viewer.content_changed.connect(self.on_txtd_content_changed)
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.setStatusBar(QStatusBar(self))

        container_widget = QWidget()
        container_layout = QVBoxLayout()
        toolbar = QToolBar("Main Toolbar")
        container_layout.addWidget(toolbar)

        open_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open ISO", self)
        export_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Export Bytes", self)
        export_action.triggered.connect(self.export_selected_bytes)
        export_files_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveFDIcon), "Export Files", self)
        export_files_action.setToolTip("Rebuild TOMBA2.DAT and TOMBA2.IDX with all pending TXTD edits applied")
        export_files_action.triggered.connect(self.export_all_files)
        self.export_files_action = export_files_action

        toolbar.addAction(open_action)
        toolbar.addAction(export_action)
        toolbar.addAction(export_files_action)
        open_action.triggered.connect(self.open_iso_dialog)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        self.folder_info_label = QLabel("Select Tomba2 ISO image")
        self.folder_info_label.setWordWrap(True)
        container_layout.addWidget(self.folder_info_label)
        container_widget.setLayout(container_layout)
        self.setMenuWidget(container_widget)

        initial_treeview_width = int(self.width() * 0.30)
        self.splitter.setSizes([initial_treeview_width, self.width() - initial_treeview_width])

        self.statusBar().showMessage("Ready - Select an ISO file")

    def open_iso_dialog(self):
        """Open file dialog to select an ISO image."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Tomba2 ISO image",
            "",
            "ISO files (*.iso *.bin *.img);;All Files (*.*)"
        )
        if not file_path:
            return

        try:
            # Clean up previous temp directory
            self.cleanup_temp_dir()

            # Extract files using ISOHandler
            self.iso_path = file_path
            extracted_files = self.iso_handler.extract_iso(file_path)

            # Get the temp directory
            self.temp_dir = self.iso_handler.get_temp_dir()

            # Set file paths
            self.dat_file = extracted_files.get("TOMBA2.DAT")
            self.idx_file = extracted_files.get("TOMBA2.IDX")
            self.img_file = extracted_files.get("TOMBA2.IMG")

            if not all([self.dat_file, self.idx_file, self.img_file]):
                raise FileNotFoundError("Could not extract all required files")

            # Update UI
            self.folder_info_label.setText(f"Loaded ISO: {os.path.basename(file_path)}")
            self.setWindowTitle(f"Tomba2Edit v{version} – {os.path.basename(file_path)}")

            # Parse the IDX and build the tree
            parse_idx_file(self, self.temp_dir)
            self.statusBar().showMessage(f"Loaded {os.path.basename(file_path)}")

        except Exception as e:
            self.cleanup_temp_dir()
            QMessageBox.critical(self, "Error", f"Failed to open ISO: {e}")

    def cleanup_temp_dir(self):
        """Clean up temporary directory."""
        if self.iso_handler:
            self.iso_handler.cleanup()
        self.temp_dir = None
        self.dat_file = None
        self.idx_file = None
        self.img_file = None
        self.iso_path = None

    def closeEvent(self, event):
        """Clean up temporary files when closing the application."""
        self.cleanup_temp_dir()
        event.accept()

    def id_convert(self, DAT, id, pointer_start):
        """Convert ID to file type string."""
        # ... (keep your existing implementation)
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

    def export_selected_bytes(self):
        # ... (keep your existing implementation)
        pass

    def on_txtd_content_changed(self, chunk_index, file_index, id_val, dat_start, offset, current_data):
        # ... (keep your existing implementation)
        pass

    def export_all_files(self):
        # ... (keep your existing implementation)
        pass

    def count_items(self, item):
        # ... (keep your existing implementation)
        pass

    def update_folder_name(self, item):
        # ... (keep your existing implementation)
        pass

    def setup_tree_view(self):
        # ... (keep your existing implementation)
        pass

    def setup_widgets(self):
        # ... (keep your existing implementation)
        pass

    def on_tree_selection_changed(self):
        # ... (keep your existing implementation)
        pass

    def handle_vram_selection(self, selected_item, item_name):
        # ... (keep your existing implementation)
        pass

    def handle_txtd_selection(self, selected_item, id, dat_start, offset):
        # ... (keep your existing implementation)
        pass

    def handle_mdat_selection(self, selected_item, dat_start, offset):
        # ... (keep your existing implementation)
        pass
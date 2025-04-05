import os
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction, QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle,
)
from icons.icons import (
    icon_TXTD, icon_SPRT, icon_TANP, icon_SMST, icon_MDAT,
    icon_SCLD, icon_BGMP, icon_BETP, icon_ALFD, icon_DRWB
)
from __init__ import version
from gui.txtd_viewer import TXTDViewer
from gui.model_viewer import OpenGLShapeWidget

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Tomba2Edit v{version}")
        self.resize(800, 600)

        self.dat_file = None

        # Load icons for file types #QStyle.StandardPixmap.SP_FileDialogDetailedView for builtin
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
                    self.parse_idx_file(cd_folder)
                else:
                    QMessageBox.critical(self, "Error", "The selected folder does not contain the required TOMBA2 files.")
            else:
                QMessageBox.critical(self, "Error", "The selected folder does not contain a 'CD' folder.")
        else:
            self.folder_info_label.setText("Select Tomba folder (with BIN, CD, MOVIE)")

    def parse_idx_file(self, cd_folder):
        idx_path = os.path.join(cd_folder, "TOMBA2.IDX")
        dat_path = os.path.join(cd_folder, "TOMBA2.DAT")
        img_path = os.path.join(cd_folder, "TOMBA2.IMG")

        IDX = open(idx_path, "rb")
        DAT = open(dat_path, "rb")  # Open the DAT file
        IMG = open(img_path, "rb")

        # Store the DAT file reference for later use
        self.dat_file = dat_path

        chunk_size = 0x800
        trailer = 0x700

        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Name"])
        root_item = model.invisibleRootItem()

        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        for chunk_index in range(int(os.path.getsize(idx_path) / chunk_size)):
            IDX.seek(chunk_index * chunk_size)
            img_start, img_end, dat_start, dat_end, pointer_amount = struct.unpack("<5I", IDX.read(20))
            #if not any([img_start, img_end, dat_start, dat_end, pointer_amount]):
            #    continue

            IMG.seek(img_start)
            imgdata = IMG.read(img_end - img_start)

            DAT.seek(dat_start)
            datdata = DAT.read(dat_end - dat_start)

            sdat_pointers = [self.tuplify(item) for item in
                             struct.unpack("<{:d}I".format(pointer_amount), IDX.read(pointer_amount * 4))]

            IDX.seek(chunk_index * chunk_size + (chunk_size - trailer))
            traildata = struct.unpack("<{:d}I".format(trailer >> 2), IDX.read(trailer))
            trail_list = []
            for t in range(0, len(traildata), 2):
                dat_trail_start, dat_trail_end = traildata[t], traildata[t + 1]
                dat_trail_size = dat_trail_end - dat_trail_start
                if dat_trail_size != 0:
                    trail_list.append((dat_trail_start, dat_trail_end, dat_trail_size))

            area_item = QStandardItem(folder_icon, f"AREA_{chunk_index:02X}")
            area_item.setFlags(area_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            root_item.appendRow(area_item)

            if datdata:
                sdat_item = QStandardItem(folder_icon, f"{chunk_index:02X}_DATA")
                sdat_item.setFlags(sdat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                area_item.appendRow(sdat_item)
                for i in range(len(sdat_pointers)):
                    id = sdat_pointers[i][0]
                    offset = sdat_pointers[i][1]
                    filetype = self.id_convert(DAT, id, hex(dat_start + offset))
                    print(filetype)
                    file_item = QStandardItem(file_icon, f"{id}-{offset:04X}.{filetype}")

                    # Store the additional data in the UserRole (or another role)
                    file_item.setData((id, dat_start, offset), Qt.ItemDataRole.UserRole)

                    file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                    if filetype == "SPRT": #SPRT
                        file_item.setIcon(self.sprt_icon)
                    elif filetype == "TXTD": #txtd
                        file_item.setIcon(self.txtd_icon)
                        file_path = f"{dat_start + offset:08X}.txtd"
                        file_item.setData(file_path, Qt.ItemDataRole.UserRole + 1)  # Store file path in another role
                    elif filetype == "TANP": #TANP
                        file_item.setIcon(self.tanp_icon)
                    elif filetype == "SMST": #TANP
                        file_item.setIcon(self.smst_icon)
                    elif filetype == "SCLD":
                        file_item.setIcon(self.scld_icon)
                    elif filetype == "MDAT":
                        file_item.setIcon(self.mdat_icon)
                    elif filetype == "DRWB":
                        file_item.setIcon(self.drwb_icon)
                    elif filetype == "BGMP":
                        file_item.setIcon(self.bgmp_icon)
                    elif filetype == "BETP":
                        file_item.setIcon(self.betp_icon)
                    elif filetype == "ALFD":
                        file_item.setIcon(self.alfd_icon)
                    sdat_item.appendRow(file_item)

            if imgdata:
                vram_item = QStandardItem(folder_icon, f"{chunk_index:02X}_VRAM")
                vram_item.setFlags(vram_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                area_item.appendRow(vram_item)
                vram_file_item = QStandardItem(file_icon, f"{chunk_index:02X}.vram")
                vram_file_item.setFlags(vram_file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                vram_item.appendRow(vram_file_item)

            if traildata:
                trail_item = QStandardItem(folder_icon, f"{chunk_index:02X}_TRAIL")
                trail_item.setFlags(trail_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                area_item.appendRow(trail_item)
                for i in range(len(trail_list)):
                    adr, end, sz = trail_list[i]
                    trail_file_item = QStandardItem(file_icon, f"{adr:04X}-{end:04X}.bin")
                    trail_file_item.setFlags(trail_file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    trail_item.appendRow(trail_file_item)

            self.update_folder_name(area_item)
            if datdata:
                self.update_folder_name(sdat_item)
            if imgdata:
                self.update_folder_name(vram_item)
            if traildata:
                self.update_folder_name(trail_item)

        self.tree_view.setModel(model)
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

    def tuplify(self, item):
        dat_id = item >> 24
        dat_ptr = item & 0x00FFFFFF
        return (dat_id, dat_ptr)

    def id_convert(self, DAT, id, pointer_start):
        if id == 0:
            return "SPRT"
        elif id in [2,3]:
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

    def setup_tree_view(self):
        self.tree_view.setModel(QStandardItemModel())
        self.tree_view.setHeaderHidden(False)

    def setup_widgets(self):
        self.txtd_viewer = TXTDViewer()  # Create an instance of TXTDViewer

        self.widgets = {
            "Folder": QLabel("This is a folder"),
            "SPRT": QLabel("SPRITE Viewer"),
            "TXTD": self.txtd_viewer,  # Register the TXTD Viewer widget
            "MDAT": QLabel("MODEL Viewer"),
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
                    print(f"ID: {id}, DAT Start: {dat_start}, Offset: {offset}")

                    # Now, pass these parameters to load_txtd_data
                    file_type = item_name.split('.')[-1] if '.' in item_name else "DEFAULT"
                    widget = self.widgets.get(file_type, self.widgets["DEFAULT"])
                    self.widgets_area.setCurrentWidget(widget)

                    if widget == self.widgets["TXTD"]:
                        file_path = selected_item.data(Qt.ItemDataRole.UserRole + 1)  # Retrieve stored file path
                        print(f"File path: {file_path}")
                        if file_path:
                            try:
                                # Pass the DAT file object here instead of a string path
                                if self.dat_file:  # Ensure the DAT file is available
                                    print("Loading TXTD data...")
                                    self.txtd_viewer.load_txtd_data(self.dat_file, dat_start, offset)
                                else:
                                    QMessageBox.critical(self, "Error", "DAT file not loaded.")
                            except Exception as e:
                                print(f"Error loading TXTD file: {e}")
                                QMessageBox.critical(self, "Error", f"Failed to load TXTD file: {e}")
                    else:
                        print("No additional data found.")
        except Exception as e:
            print(f"Error in on_tree_selection_changed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to handle selection change: {e}")
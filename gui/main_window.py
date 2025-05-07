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
from PIL.ImageQt import ImageQt  # Import ImageQt for converting PIL images to QPixmap

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

        self.setup_tree_view()
        self.setup_widgets()
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.setStatusBar(QStatusBar(self))

        container_widget = QWidget()
        container_layout = QVBoxLayout()
        toolbar = QToolBar("Main Toolbar")
        container_layout.addWidget(toolbar)
        open_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open", self)
        export_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Export Bytes", self)
        export_action.triggered.connect(self.export_selected_bytes)

        toolbar.addAction(open_action)
        toolbar.addAction(export_action)
        open_action.triggered.connect(self.open_folder_dialog)
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

    def export_selected_bytes(self):
        selected_indexes = self.tree_view.selectionModel().selectedIndexes()
        if not selected_indexes:
            QMessageBox.warning(self, "Warning", "No item selected.")
            return

        selected_index = selected_indexes[0]
        selected_item = self.tree_view.model().itemFromIndex(selected_index)

        additional_data = selected_item.data(Qt.ItemDataRole.UserRole)
        chunk_file_info = selected_item.data(Qt.ItemDataRole.UserRole + 2)  # NEW
        if not additional_data:
            QMessageBox.warning(self, "Warning", "Selected item does not contain exportable data.")
            return

        try:
            if additional_data[0] == "trail":
                # TRAIL export
                _, offset, size, _ = additional_data
                if chunk_file_info:
                    chunk_index, trail_index = chunk_file_info
                else:
                    chunk_index, trail_index = (0, 0)

                with open(self.dat_file, "rb") as f:
                    f.seek(offset)
                    data = f.read(size)

                save_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save Trail Bytes",
                    f"AREA_{chunk_index:02X}_TRAIL_{trail_index:02X}_OFFSET_{offset:08X}.bin",
                    "Binary Files (*.bin)"
                )

            elif additional_data[0] in ("vram_compressed", "vram_uncompressed"):
                # VRAM handling (no change)
                # (you can leave this part same as before)
                pass

            else:
                # Normal SDAT export
                id, dat_start, offset, size = additional_data
                if chunk_file_info:
                    chunk_index, file_index = chunk_file_info
                else:
                    chunk_index, file_index = (0, 0)

                with open(self.dat_file, "rb") as f:
                    f.seek(dat_start + offset)
                    data = f.read(size)

                save_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save Exported Bytes",
                    f"AREA_{chunk_index:02X}_FILE_{file_index:02X}_ID_{id:X}_OFFSET_{dat_start + offset:08X}.bin",
                    "Binary Files (*.bin)"
                )

            if save_path:
                with open(save_path, "wb") as out_file:
                    out_file.write(data)
                QMessageBox.information(self, "Success", f"Exported bytes to {save_path}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export bytes: {e}")

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
        self.mdat_viewer = MDATViewer()
        self.vram_viewer = VRAMViewer()  # Add this line

        self.widgets = {
            "Folder": QLabel("This is a folder"),
            "SPRT": QLabel("SPRITE Viewer"),
            "TXTD": self.txtd_viewer,
            "MDAT": self.mdat_viewer,
            "VRAM": self.vram_viewer,  # Add this line
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
                print(f"Selected Item: {item_name}")

                # Check if this is a VRAM file
                if item_name.endswith('.VRAM') or item_name.endswith('.CVRAM'):
                    if item_name.endswith('.VRAM'):
                        # Get the parent item to find the AREA index
                        parent = selected_item.parent()
                        if parent:
                            grandparent = parent.parent()
                            if grandparent:
                                area_name = grandparent.text()
                            else:
                                area_name = parent.text()

                            # Extract the area number (handle cases like "AREA_07 (18)")
                            if area_name.startswith('AREA_'):
                                area_part = area_name.split('_')[1].split()[0]  # Gets "07" from "AREA_07 (18)"
                                try:
                                    chunk_index = int(area_part, 16)
                                    # Load the VRAM data
                                    img_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IMG")
                                    with open(img_path, "rb") as IMG:
                                        IDX_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
                                        with open(IDX_path, "rb") as IDX:
                                            chunk_size = 0x800
                                            IDX.seek(chunk_index * chunk_size)
                                            img_start, img_end, _, _, _ = struct.unpack("<5I", IDX.read(20))
                                            IMG.seek(img_start)
                                            imgdata = IMG.read(img_end - img_start)

                                            # Show VRAM viewer
                                            self.widgets_area.setCurrentWidget(self.widgets["VRAM"])
                                            self.vram_viewer.load_vram_data(imgdata)
                                            return
                                except ValueError as e:
                                    print(f"Error parsing area number: {e}")
                                    QMessageBox.critical(self, "Error", f"Failed to parse area number: {e}")
                                    return
                    elif item_name.endswith('.CVRAM'):
                        parent = selected_item.parent()
                        if parent:
                            grandparent = parent.parent()
                            if grandparent:
                                area_name = grandparent.text()
                            else:
                                area_name = parent.text()

                        if area_name.startswith('AREA_'):
                            area_part = area_name.split('_')[1].split()[0]
                            chunk_index = int(area_part, 16)

                            img_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IMG")
                            with open(img_path, "rb") as IMG:
                                idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
                                with open(idx_path, "rb") as IDX:
                                    chunk_size = 0x800
                                    IDX.seek(chunk_index * chunk_size)
                                    img_start, img_end, _, _, _ = struct.unpack("<5I", IDX.read(20))
                                    IMG.seek(img_start)
                                    imgdata = IMG.read(img_end - img_start)

                                    # Instead of decompressing, just load raw CVRAM
                                    self.widgets_area.setCurrentWidget(self.widgets["VRAM"])
                                    self.vram_viewer.load_cvrm_data(imgdata)  # <-- NEW FUNCTION

                    return

                # selection handling code...
                additional_data = selected_item.data(Qt.ItemDataRole.UserRole)
                if additional_data:
                    id = additional_data[0]
                    if isinstance(id, int):
                        dat_start, offset = additional_data[1], additional_data[2]
                        print(f"ID: {id:X}, DAT Start: {dat_start:X}, Offset: {offset:X}")
                    else:
                        print(f"Special file type: {id}")

                    # Determine file type and get appropriate widget
                    file_type = item_name.split('.')[-1].upper() if '.' in item_name else "DEFAULT"
                    widget = self.widgets.get(file_type, self.widgets["DEFAULT"])
                    self.widgets_area.setCurrentWidget(widget)

                    if widget == self.widgets["TXTD"]:
                        file_path = selected_item.data(Qt.ItemDataRole.UserRole + 1)
                        print(f"File path: {file_path}")
                        if file_path:
                            try:
                                if self.dat_file:
                                    print("Loading TXTD data...")
                                    self.txtd_viewer.load_txtd_data(self.dat_file, dat_start, offset)
                                else:
                                    QMessageBox.critical(self, "Error", "DAT file not loaded.")
                            except Exception as e:
                                print(f"Error loading TXTD file: {e}")
                                QMessageBox.critical(self, "Error", f"Failed to load TXTD file: {e}")


                    elif widget == self.widgets["MDAT"]:
                        try:
                            # Step 1: Try to find AREA_XX from parent or grandparent
                            area_name = None
                            parent = selected_item.parent()
                            if parent:
                                grandparent = parent.parent()
                                if grandparent and grandparent.text().startswith("AREA_"):
                                    area_name = grandparent.text()
                                elif parent.text().startswith("AREA_"):
                                    area_name = parent.text()

                            if area_name:
                                area_number = area_name.split("_")[1].split()[0]  # e.g., "04" from "AREA_04 (41)"
                                try:
                                    chunk_index = int(area_number, 16)
                                    img_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IMG")
                                    idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")

                                    with open(img_path, "rb") as IMG, open(idx_path, "rb") as IDX:
                                        chunk_size = 0x800
                                        IDX.seek(chunk_index * chunk_size)
                                        img_start, img_end, _, _, _ = struct.unpack("<5I", IDX.read(20))
                                        IMG.seek(img_start)
                                        imgdata = IMG.read(img_end - img_start)

                                        # Step 2: decode VRAM and send to MDATViewer
                                        from gui.vram_viewer import VRAMViewer  # make sure it's available
                                        vram_img_result = self.vram_viewer.process_vram(imgdata)

                                        if isinstance(vram_img_result, tuple):
                                            vram_img, vram_bytes = vram_img_result
                                        else:
                                            vram_img = vram_img_result
                                            vram_bytes = None

                                        qimage = ImageQt(vram_img).copy()

                                        # Remove second call completely, just do once correctly:
                                        if vram_img.mode != "RGBA":
                                            qimage = QImage(vram_img.tobytes(), vram_img.width, vram_img.height,
                                                            QImage.Format.Format_RGB888)
                                        else:
                                            qimage = QImage(vram_img.tobytes(), vram_img.width, vram_img.height,
                                                            QImage.Format.Format_RGBA8888)

                                        self.mdat_viewer.set_vram_image(qimage, vram_bytes)

                                except Exception as e:
                                    print(f"❌ Could not load VRAM for AREA_{area_number}: {e}")
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
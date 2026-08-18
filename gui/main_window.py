import os
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction, QIcon, QImage, QPixmap, QColor, QBrush
from PyQt6.QtWidgets import (
    QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle,
)
from icons.icons import (icon_window, icon_disc,
                         icon_TXTD, icon_TXT2, icon_SPRT, icon_TANP, icon_SMST, icon_MDAT,
                         icon_SCLD, icon_BGMP, icon_BETP, icon_ALFD, icon_DRWB,
                         icon_VRAM, icon_CVRAM,
                         )
from main import version
from gui.txtd_viewer import TXTDViewer
from gui.txt2_viewer import TXT2Viewer
from gui.mdat_viewer import MDATViewer
from functions.idx_parser import parse_idx_file
from functions.iso_handler import ISOHandler
from gui.vram_viewer import VRAMViewer
from PIL.ImageQt import ImageQt  # Import ImageQt for converting PIL images to QPixmap

# Colors used to flag a TXTD/TXT2 file's row in the main file tree,
# mirroring the entry-level coloring in TXTDViewer/TXT2Viewer: orange
# while it has pending (unexported) text edits, green once those edits
# have been exported.
EDITED_TXTD_ITEM_COLOR = "orange"
EXPORTED_TXTD_ITEM_COLOR = "green"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Tomba2Edit v{version}")
        self.resize(1400, 900)
        self.setWindowIcon(QIcon(icon_window))

        # Proceed with icon loading
        self.txtd_icon = QIcon(icon_TXTD)
        self.txt2_icon = QIcon(icon_TXT2)
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

        # (chunk_index, file_index) -> {"kind", "id", "dat_start", "offset",
        # "data"} for every TXTD/TXT2 file that's been edited but not yet
        # exported. Both file types share this one dict - the "kind" tag
        # ("txtd" or "txt2") is only used by _pack_pending_txtd_edits() to
        # decide whether to call txtd_packer.pack_txtd() or
        # txt2_packer.pack_txt2() for that entry; everything else here
        # (coloring, export bookkeeping) treats both kinds identically.
        self.pending_txtd_edits = {}

        # (chunk_index, file_index) -> the QStandardItem for that TXTD/TXT2
        # file in self.tree_view, so pending edits can be highlighted there
        # too. Populated by idx_parser.parse_idx_file() each time an ISO is
        # opened.
        self.txtd_item_lookup = {}

        # (chunk_index, file_index) -> "edited" | "exported", mirroring the
        # color currently applied to that TXTD/TXT2 file's row. Kept
        # separately from txtd_item_lookup so the enclosing NN_DATA/AREA_NN
        # folder colors can be recomputed by aggregating over every file
        # inside them (see _refresh_folder_state_color) without having to
        # inspect Qt foreground brushes.
        self.txtd_file_states = {}

        # Set once an ISO has been opened and its TOMBA2.DAT/IDX/IMG have
        # been extracted to a temp folder (see open_iso_dialog / ISOHandler).
        self.dat_file = None
        self.iso_handler = None
        # Path to the disc image currently open - kept around so Export ISO
        # has a full original disc to rebuild from (extracted files alone
        # aren't enough, since everything besides DAT/IDX/IMG needs to be
        # carried over from the source image too).
        self.current_iso_path = None

        self.setup_tree_view()
        self.setup_widgets()
        self.txtd_viewer.content_changed.connect(self.on_txtd_content_changed)
        self.txt2_viewer.content_changed.connect(self.on_txt2_content_changed)
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.setStatusBar(QStatusBar(self))

        container_widget = QWidget()
        container_layout = QVBoxLayout()
        toolbar = QToolBar("Main Toolbar")
        container_layout.addWidget(toolbar)
        open_action = QAction(QIcon(icon_disc), "Open ISO", self)
        open_action.setToolTip("Open a Tomba! 2 disc image (.iso/.bin/.img) and browse its contents")

        open_folder_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open CD Folder", self)
        open_folder_action.setToolTip(
            "Open an already-extracted CD folder directly, skipping ISO extraction. "
            "'Save ISO' won't be available - use 'Save IDX/DAT' instead."
        )
        open_folder_action.triggered.connect(self.open_folder_dialog)

        export_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Export File", self)
        export_action.triggered.connect(self.export_selected_bytes)
        export_files_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveFDIcon), "Save IDX/DAT", self)
        export_files_action.setToolTip("Rebuild TOMBA2.DAT and TOMBA2.IDX with all pending TXTD/TXT2 edits applied")
        export_files_action.triggered.connect(self.export_all_files)
        self.export_files_action = export_files_action

        export_iso_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveDVDIcon), "Save ISO", self)
        export_iso_action.setToolTip(
            "Rebuild the opened disc as a new .iso, with any pending TXTD/TXT2 edits applied "
            "(everything else on the disc is carried over unchanged)"
        )
        export_iso_action.triggered.connect(self.export_iso)
        self.export_iso_action = export_iso_action

        toolbar.addAction(open_action)
        toolbar.addAction(open_folder_action)
        toolbar.addAction(export_action)
        toolbar.addAction(export_files_action)
        toolbar.addAction(export_iso_action)
        open_action.triggered.connect(self.open_iso_dialog)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        self.folder_info_label = QLabel("Select a Tomba! 2 ISO file to begin")
        self.folder_info_label.setWordWrap(True)
        container_layout.addWidget(self.folder_info_label)
        container_widget.setLayout(container_layout)
        self.setMenuWidget(container_widget)

        initial_treeview_width = int(self.width() * 0.30)
        self.splitter.setSizes([initial_treeview_width, self.width() - initial_treeview_width])

    def id_convert(main_window, DAT, id, pointer_start):
        if id == 0:
            return "SPRT"
        elif id == 2:
            return "TXT1"
        elif id == 3:
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

    def on_txtd_content_changed(self, chunk_index, file_index, id_val, dat_start, offset, current_data):
        """Called by TXTDViewer every time an entry's text is edited.
        current_data is the SAME dict object the viewer keeps editing in
        place, so this just needs to remember which (area, file) it
        belongs to - no need to copy it defensively here since each
        edited TXTD only ever has one viewer instance touching it at a
        time in this UI."""
        self.pending_txtd_edits[(chunk_index, file_index)] = {
            "kind": "txtd", "id": id_val, "dat_start": dat_start, "offset": offset, "data": current_data,
        }
        self._set_txtd_tree_item_state(chunk_index, file_index, "edited")
        self.statusBar().showMessage(
            f"{len(self.pending_txtd_edits)} file(s) have pending edits - "
            f"use the 'Save IDX/DAT' button when ready.")

    def on_txt2_content_changed(self, chunk_index, file_index, id_val, dat_start, offset, current_data):
        """Called by TXT2Viewer every time an entry's text is edited. Same
        idea as on_txtd_content_changed() above - the two kinds of pending
        edit share self.pending_txtd_edits (see its docstring), tagged with
        "kind" so _pack_pending_txtd_edits() knows which packer to use for
        each one; every other bookkeeping step (tree coloring, export
        counting) doesn't need to distinguish between them at all."""
        self.pending_txtd_edits[(chunk_index, file_index)] = {
            "kind": "txt2", "id": id_val, "dat_start": dat_start, "offset": offset, "data": current_data,
        }
        self._set_txtd_tree_item_state(chunk_index, file_index, "edited")
        self.statusBar().showMessage(
            f"{len(self.pending_txtd_edits)} file(s) have pending edits - "
            f"use the 'Save IDX/DAT' button when ready.")

    def _set_txtd_tree_item_state(self, chunk_index, file_index, state):
        """Colors a TXTD file's row in the main tree (under its NN_DATA
        folder): "edited" (orange) while it has pending edits, "exported"
        (green) once those edits have been exported, or None for the
        tree's normal color (never touched) - so it's obvious at a glance
        which TXTD files were changed and whether that change was saved,
        without having to open each one. Also recomputes the enclosing
        NN_DATA and AREA_NN folder colors from every TXTD file inside
        them, so an edit anywhere shows up all the way up the tree."""
        file_item = self.txtd_item_lookup.get((chunk_index, file_index))
        if file_item is None:
            return

        if state:
            self.txtd_file_states[(chunk_index, file_index)] = state
        else:
            self.txtd_file_states.pop((chunk_index, file_index), None)

        self._apply_tree_item_state_color(file_item, state)

        sdat_item = file_item.parent()
        if sdat_item is not None:
            self._refresh_folder_state_color(sdat_item)
            area_item = sdat_item.parent()
            if area_item is not None:
                self._refresh_folder_state_color(area_item)

    @staticmethod
    def _apply_tree_item_state_color(item, state):
        if state == "edited":
            item.setForeground(QBrush(QColor(EDITED_TXTD_ITEM_COLOR)))
        elif state == "exported":
            item.setForeground(QBrush(QColor(EXPORTED_TXTD_ITEM_COLOR)))
        else:
            item.setData(None, Qt.ItemDataRole.ForegroundRole)

    def _refresh_folder_state_color(self, folder_item):
        """Recomputes an NN_DATA or AREA_NN folder's color by aggregating
        the edit/export state of every TXTD file anywhere underneath it:
        orange if any of them still has pending edits, else green if any
        of them has been edited-and-exported, else back to the tree's
        normal color."""
        self._apply_tree_item_state_color(folder_item, self._aggregate_txtd_state(folder_item))

    def _aggregate_txtd_state(self, item):
        """"edited" if any TXTD file at or below `item` has pending edits,
        else "exported" if any has been edited-and-exported, else None.
        Walks the tree itself (rather than needing a separate index of
        "which files live under this folder"), using the (chunk_index,
        file_index) tuple every TXTD file item already carries in
        UserRole + 2 (see idx_parser.parse_idx_file)."""
        saw_exported = False
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            location = child.data(Qt.ItemDataRole.UserRole + 2)
            state = self.txtd_file_states.get(location) if location else None
            if state is None and child.hasChildren():
                state = self._aggregate_txtd_state(child)

            if state == "edited":
                return "edited"
            if state == "exported":
                saw_exported = True

        return "exported" if saw_exported else None

    def export_all_files(self):
        if not self.pending_txtd_edits:
            QMessageBox.information(self, "Nothing to export",
                                     "No TXTD/TXT2 edits are pending. Edit some entry text first.")
            return

        if not getattr(self, 'dat_file', None):
            QMessageBox.critical(self, "Error", "No TOMBA2 ISO is open.")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "Choose output folder for the modified DAT + IDX")
        if not out_dir:
            return

        from functions.repacker import repack_files

        edits = self._pack_pending_txtd_edits()
        if edits is None:
            return

        original_dir = os.path.dirname(self.dat_file)
        original_idx = os.path.join(original_dir, "TOMBA2.IDX")
        output_dat = os.path.join(out_dir, "TOMBA2.DAT")
        output_idx = os.path.join(out_dir, "TOMBA2.IDX")

        try:
            repack_files(self.dat_file, original_idx, edits, output_dat, output_idx)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Failed to rebuild DAT/IDX: {e}")
            return

        QMessageBox.information(
            self, "Export complete",
            f"Wrote:\n{output_dat}\n{output_idx}\n\n"
            f"({len(edits)} file(s) repacked.)\n\n"
            "Back up your original CD files, then copy these two over them "
            "to test in-game. TOMBA2.IMG is unchanged and doesn't need copying."
        )
        for (chunk_index, file_index), info in self.pending_txtd_edits.items():
            self._set_txtd_tree_item_state(chunk_index, file_index, "exported")
            if info.get("kind") == "txt2":
                self.txt2_viewer.mark_exported(chunk_index, file_index)
            else:
                self.txtd_viewer.mark_exported(chunk_index, file_index)
        self.pending_txtd_edits.clear()

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

    def open_iso_dialog(self):
        """Extract TOMBA2.DAT/IDX/IMG from a disc image into a temp
        folder and populate the tree view. See open_folder_dialog() for
        opening an already-extracted folder instead."""
        iso_path, _ = QFileDialog.getOpenFileName(
            self, "Select Tomba! 2 ISO", "",
            "Disc Images (*.iso *.bin *.img);;All Files (*)"
        )
        if not iso_path:
            return

        if self.pending_txtd_edits:
            proceed = QMessageBox.question(
                self, "Discard pending edits?",
                f"You have {len(self.pending_txtd_edits)} TXTD/TXT2 edit(s) that haven't been "
                "exported yet. Opening a new ISO will discard them.\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        # Starting a fresh ISO always throws away whatever was extracted
        # for the previous one.
        if self.iso_handler:
            self.iso_handler.cleanup()
        self.iso_handler = ISOHandler()
        self.pending_txtd_edits.clear()
        self.txtd_file_states.clear()
        self.txtd_viewer.clear_cache()
        self.txt2_viewer.clear_cache()
        self.current_iso_path = None

        try:
            self.iso_handler.extract_iso(iso_path)
        except Exception as e:
            self.iso_handler.cleanup()
            self.iso_handler = None
            self.folder_info_label.setText("Select a Tomba! 2 ISO file to begin")
            QMessageBox.critical(self, "Error", f"Failed to read ISO:\n\n{e}")
            return

        extracted_dir = self.iso_handler.get_temp_dir()
        try:
            parse_idx_file(self, extracted_dir)
        except Exception as e:
            self.iso_handler.cleanup()
            self.iso_handler = None
            self.dat_file = None
            self.folder_info_label.setText("Select a Tomba! 2 ISO file to begin")
            QMessageBox.critical(self, "Error", f"Failed to parse TOMBA2.IDX from this ISO:\n\n{e}")
            return

        self.current_iso_path = iso_path
        self.folder_info_label.setText(f"Loaded ISO: {iso_path}")

    def open_folder_dialog(self):
        """Open an already-extracted CD folder directly, no ISO needed.
        Accepts either the parent folder (with a CD subfolder) or the CD
        folder itself."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select a Tomba! 2 folder (containing a CD folder, or the CD folder itself)"
        )
        if not folder:
            return

        required_files = ("TOMBA2.DAT", "TOMBA2.IDX", "TOMBA2.IMG")

        def has_required_files(path):
            return all(os.path.exists(os.path.join(path, name)) for name in required_files)

        nested_cd = os.path.join(folder, "CD")
        if has_required_files(nested_cd):
            cd_folder = nested_cd
        elif has_required_files(folder):
            cd_folder = folder
        else:
            QMessageBox.critical(
                self, "Error",
                "Couldn't find TOMBA2.DAT, TOMBA2.IDX and TOMBA2.IMG in this folder "
                "or in a CD subfolder inside it. Select the folder that was extracted "
                "from a Tomba! 2 disc image (with a CD subfolder), or that CD folder "
                "directly."
            )
            return

        if self.pending_txtd_edits:
            proceed = QMessageBox.question(
                self, "Discard pending edits?",
                f"You have {len(self.pending_txtd_edits)} TXTD/TXT2 edit(s) that haven't been "
                "exported yet. Opening a new folder will discard them.\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        # no ISO backs this folder - clear iso_handler so export_iso() refuses
        if self.iso_handler:
            self.iso_handler.cleanup()
        self.iso_handler = None
        self.current_iso_path = None
        self.pending_txtd_edits.clear()
        self.txtd_file_states.clear()
        self.txtd_viewer.clear_cache()
        self.txt2_viewer.clear_cache()

        try:
            parse_idx_file(self, cd_folder)
        except Exception as e:
            self.dat_file = None
            self.folder_info_label.setText("Select a Tomba! 2 ISO file to begin")
            QMessageBox.critical(self, "Error", f"Failed to parse TOMBA2.IDX from this folder:\n\n{e}")
            return

        self.folder_info_label.setText(f"Loaded folder: {cd_folder}")

    def _pack_pending_txtd_edits(self):
        """Turn self.pending_txtd_edits into the `edits` list repack_files()
        expects. Returns None (after showing an error dialog) if any entry
        fails to encode.

        Branches on each pending edit's "kind" tag (see
        on_txtd_content_changed / on_txt2_content_changed) to call the
        right packer - txt2_packer.Txt2PackError subclasses
        txtd_packer.TxtdPackError, so one except clause below catches
        either. This is the single place that packs BOTH file types, and
        is now used by both export_all_files() and export_iso() so the
        logic only exists once."""
        from functions import txtd_packer
        from functions import txt2_packer
        edits = []
        try:
            for (chunk_index, file_index), info in self.pending_txtd_edits.items():
                if info.get("kind") == "txt2":
                    packed_bytes = txt2_packer.pack_txt2(info["data"])
                else:
                    packed_bytes = txtd_packer.pack_txtd(info["data"])
                edits.append({"area": chunk_index, "file_idx": file_index, "data": packed_bytes})
        except txtd_packer.TxtdPackError as e:
            QMessageBox.critical(self, "Text encoding error",
                                  f"Couldn't encode an entry's text:\n\n{e}\n\n"
                                  "Fix that entry and try exporting again.")
            return None
        return edits

    def export_iso(self):
        """Rebuild the currently opened disc as a new .iso, applying any
        pending TXTD edits to TOMBA2.DAT/IDX along the way. Every other
        file and folder on the disc (TOMBA2.IMG, the executable, movies,
        etc.) is carried over from the original image unchanged, so this
        produces a complete, ready-to-play image instead of the two loose
        files 'Export Files' hands back."""
        if not self.current_iso_path or not self.iso_handler or not getattr(self, 'dat_file', None):
            QMessageBox.critical(self, "Error", "No TOMBA2 ISO is open.")
            return

        edits = []
        if self.pending_txtd_edits:
            edits = self._pack_pending_txtd_edits()
            if edits is None:
                return

        default_name = os.path.splitext(os.path.basename(self.current_iso_path))[0]
        default_name += "_edited.iso" if edits else "_copy.iso"
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Save rebuilt ISO", default_name, "ISO Image (*.iso)"
        )
        if not output_path:
            return

        import shutil
        import tempfile
        from functions.repacker import repack_files
        from functions.iso_builder import build_iso

        replacements = {}
        tmp_repack_dir = None
        try:
            if edits:
                tmp_repack_dir = tempfile.mkdtemp(prefix="tomba2edit_repack_")
                original_idx = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
                tmp_dat = os.path.join(tmp_repack_dir, "TOMBA2.DAT")
                tmp_idx = os.path.join(tmp_repack_dir, "TOMBA2.IDX")
                repack_files(self.dat_file, original_idx, edits, tmp_dat, tmp_idx)
                with open(tmp_dat, "rb") as f:
                    replacements["TOMBA2.DAT"] = f.read()
                with open(tmp_idx, "rb") as f:
                    replacements["TOMBA2.IDX"] = f.read()

            build_iso(self.current_iso_path, replacements, output_path)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Failed to rebuild ISO:\n\n{e}")
            return
        finally:
            if tmp_repack_dir:
                shutil.rmtree(tmp_repack_dir, ignore_errors=True)

        summary = (
            f"({len(edits)} file(s) repacked into it.)"
            if edits else
            "(No pending edits - this is an unmodified copy of the opened disc.)"
        )
        QMessageBox.information(
            self, "ISO export complete",
            f"Wrote:\n{output_path}\n\n{summary}\n\n"
            "Note: this is a single-track, data-only ISO. If you opened a "
            "multi-track BIN/CUE with CD audio, that audio isn't part of "
            "this file - keep using the BIN/CUE for in-game music, and use "
            "this ISO to check the edits landed correctly."
        )
        if edits:
            for (chunk_index, file_index), info in self.pending_txtd_edits.items():
                self._set_txtd_tree_item_state(chunk_index, file_index, "exported")
                if info.get("kind") == "txt2":
                    self.txt2_viewer.mark_exported(chunk_index, file_index)
                else:
                    self.txtd_viewer.mark_exported(chunk_index, file_index)
            self.pending_txtd_edits.clear()

    def closeEvent(self, event):
        if self.iso_handler:
            self.iso_handler.cleanup()
        super().closeEvent(event)

    def tuplify(self, item):
        dat_id = item >> 24
        dat_ptr = item & 0x00FFFFFF
        return (dat_id, dat_ptr)


    def setup_tree_view(self):
        self.tree_view.setModel(QStandardItemModel())
        self.tree_view.setHeaderHidden(False)

    def setup_widgets(self):
        self.txtd_viewer = TXTDViewer()
        self.txt2_viewer = TXT2Viewer()
        self.mdat_viewer = MDATViewer()
        self.vram_viewer = VRAMViewer()  # Add this line

        self.widgets = {
            "Folder": QLabel("This is a folder"),
            "SPRT": QLabel("SPRITE Viewer"),
            "TXTD": self.txtd_viewer,
            "TXT1": self.txt2_viewer,  # same layout as TXT2, shares the viewer
            "TXT2": self.txt2_viewer,
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
                        # This SDAT entry's own byte length (idx_parser.py's
                        # `size`) - named distinctly from the unrelated
                        # `chunk_size` (the fixed 0x800 IDX record stride)
                        # used elsewhere in this same method for VRAM.
                        entry_size = additional_data[3] if len(additional_data) > 3 else None
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
                                    txtd_chunk_info = selected_item.data(Qt.ItemDataRole.UserRole + 2)
                                    if txtd_chunk_info:
                                        txtd_chunk_index, txtd_file_index = txtd_chunk_info
                                    else:
                                        txtd_chunk_index, txtd_file_index = (0, 0)
                                    self.txtd_viewer.load_txtd_data(
                                        self.dat_file, dat_start, offset,
                                        chunk_index=txtd_chunk_index, file_index=txtd_file_index, id_val=id
                                    )
                                else:
                                    QMessageBox.critical(self, "Error", "DAT file not loaded.")
                            except Exception as e:
                                print(f"Error loading TXTD file: {e}")
                                QMessageBox.critical(self, "Error", f"Failed to load TXTD file: {e}")

                    elif widget == self.widgets["TXT2"]:  # also handles TXT1 rows
                        file_path = selected_item.data(Qt.ItemDataRole.UserRole + 1)
                        print(f"File path: {file_path}")
                        if file_path:
                            try:
                                if self.dat_file:
                                    print("Loading TXT2 data...")
                                    txt2_chunk_info = selected_item.data(Qt.ItemDataRole.UserRole + 2)
                                    if txt2_chunk_info:
                                        txt2_chunk_index, txt2_file_index = txt2_chunk_info
                                    else:
                                        txt2_chunk_index, txt2_file_index = (0, 0)
                                    self.txt2_viewer.load_txt2_data(
                                        self.dat_file, dat_start, offset,
                                        chunk_index=txt2_chunk_index, file_index=txt2_file_index, id_val=id,
                                        size=entry_size
                                    )
                                else:
                                    QMessageBox.critical(self, "Error", "DAT file not loaded.")
                            except Exception as e:
                                print(f"Error loading TXT2 file: {e}")
                                QMessageBox.critical(self, "Error", f"Failed to load TXT2 file: {e}")

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
import os
import struct
import numpy as np
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction, QActionGroup, QIcon, QImage, QPixmap, QColor, QBrush
from PyQt6.QtWidgets import (
    QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle,
    QTabWidget, QApplication, QAbstractItemView, QMenu,
)
from icons.icons import (icon_window, icon_disc,
                         icon_TXTD, icon_TXT2, icon_SPRT, icon_TANP, icon_SMST, icon_MDAT,
                         icon_SCLD, icon_BGMP, icon_BETP, icon_ALFD, icon_DRWB,
                         icon_VRAM, icon_CVRAM,
                         )
from main import version
from gui.txtd.txtd_viewer import TXTDViewer
from gui.txtd.txt2_viewer import TXT2Viewer
from gui.mdat.mdat_viewer import MDATViewer
from gui.drwa.drwa_viewer import DRWAViewer
from gui.drwb.drwb_viewer import DRWBViewer
from gui.scld.scld_viewer import SCLDViewer, SCLDDebugPanel
from gui.scld.scld_parser import find_area_scld_location
from gui.anmp.anmp_viewer import ANMPViewer
from gui.smst.smst_viewer import SMSTViewer, SMSTPanel
from gui.sprt.sprt_viewer import SPRTViewer
from gui.bgmp.bgmp_viewer import BGMPViewer
from gui.mainbin.mainbin_viewer import MainExeViewer
from gui.bins.bins_viewer import BinsViewer
from gui import theme
from gui import panel_title
from functions import labels as labels_module
from functions import fontpage
from functions import codeuse
from functions import voice
from gui.txtd.font_editor import FontEditor
from gui.txtd.voice_panel import VoicePanel
from gui.music_panel import MusicPanel
from functions.idx_parser import (
    parse_idx_file, apply_labels, row_label_data, area_index_of,
    LabelNameDelegate)
from functions.iso_handler import ISOHandler
from gui.mainbin.mainbin_editor import repack_pool as mainbin_repack_pool, MainBinEditError
from gui.bins.sop_editor import repack_pool as sop_repack_pool, SopEditError
from gui.vram_viewer import VRAMViewer, decode_vram_bytes, vram_index_image
from PIL.ImageQt import ImageQt  # Import ImageQt for converting PIL images to QPixmap

# Colors used to flag a file's row in the main file tree,
# mirroring the entry-level coloring in TXTDViewer/TXT2Viewer: orange
# while it has pending (unexported) text edits, green once those edits
# have been exported.
EDITED_TXTD_ITEM_COLOR = "orange"
EXPORTED_TXTD_ITEM_COLOR = "green"

# The area whose VRAM chunk holds the art every area shares - the
# character models' texture pages live only here. See
# _load_area_vram_bytes(merge_common=True).
COMMON_VRAM_AREA = 1

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

        self.tree_view = QTreeView()
        tree_panel = QWidget()
        tree_panel_layout = QVBoxLayout()
        tree_panel_layout.setContentsMargins(0, 0, 0, 0)
        tree_panel_layout.setSpacing(0)
        tree_panel_layout.addWidget(panel_title.make_panel_title("Main tree view"))
        tree_panel_layout.addWidget(self.tree_view)
        tree_panel.setLayout(tree_panel_layout)
        self.splitter.addWidget(tree_panel)
        self.widgets_area = QStackedWidget()
        self.splitter.addWidget(self.widgets_area)

        # Tab 1 (default): the existing tree + per-file viewer splitter.
        # Tab 2: MAIN.EXE's own string-pool editor (gui/mainbin_viewer.py) -
        # populated whenever a MAIN.EXE is found alongside the opened
        # ISO/folder (see open_iso_dialog/open_folder_dialog), left empty
        # otherwise.
        self.mainexe_viewer = MainExeViewer()
        self.bins_viewer = BinsViewer()
        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(self.splitter, "DAT Assets")
        self.main_tabs.addTab(self.mainexe_viewer, "MAIN.EXE")
        self.main_tabs.addTab(self.bins_viewer, "BINs")
        # Its own tab rather than beside the text: the voice track has to
        # be opened from a raw BIN, which is a different file from the
        # disc the rest of the tool is working on.
        self.voice_panel = VoicePanel()
        # Opening a disc there arms the TXTD viewer's Play button too,
        # whichever order the user does the two things in.
        self.voice_panel.image_opened.connect(
            lambda path: self.txtd_viewer.set_voice_source(path, None))
        self.main_tabs.addTab(self.voice_panel, "Dialogues")
        self.music_panel = MusicPanel()
        self.main_tabs.addTab(self.music_panel, "Music")
        self.setCentralWidget(self.main_tabs)

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

        # The names on the tree's file rows, and where they came from.
        # `labels` is whichever labels file is in force; `labels_override`
        # is set only when the user loaded one by hand, and then it stays
        # in force across opening another disc instead of being replaced
        # by whatever auto-detection would have picked (see
        # load_labels_for_disc).
        self.labels = None
        self.labels_override = None
        # Set when a row has been renamed and not exported since.
        self.labels_dirty = False

        self.setup_tree_view()
        self.setup_widgets()
        self.txtd_viewer.content_changed.connect(self.on_txtd_content_changed)
        self.txt2_viewer.content_changed.connect(self.on_txt2_content_changed)
        self.mainexe_viewer.content_changed.connect(self.on_mainexe_content_changed)
        self.bins_viewer.content_changed.connect(self.on_bins_content_changed)
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.setStatusBar(QStatusBar(self))

        toolbar = QToolBar("Main Toolbar")
        # The BIN data track is the one that carries everything - an ISO
        # cannot hold the Form 2 voice sectors and a CD folder's copy of
        # them is already truncated - so it is the only opener on the
        # toolbar. The other two stay in the File menu for when they are
        # genuinely wanted.
        open_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveDVDIcon), "Open BIN", self)
        open_action.setToolTip(
            "Open the disc's data track (Track 1 of a bin/cue). This is the "
            "only source that carries the voice track intact")

        open_folder_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open extracted disc folder", self)
        open_folder_action.setToolTip(
            "Open an already-extracted CD folder directly, skipping ISO extraction. "
            "'Save ISO' won't be available - use 'Save IDX/DAT' instead."
        )
        open_folder_action.triggered.connect(self.open_folder_dialog)

        export_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Export raw binary", self)
        export_action.setToolTip(
            "Write the selected row's bytes out as they sit in the DAT - "
            "one file, unpacked and unchanged")
        export_action.triggered.connect(self.export_selected_bytes)
        export_files_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveFDIcon), "Save IDX/DAT", self)
        export_files_action.setToolTip("Rebuild TOMBA2.DAT and TOMBA2.IDX with all pending TXTD/TXT2 edits applied")
        export_files_action.triggered.connect(self.export_all_files)
        self.export_files_action = export_files_action

        export_bin_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon), "Save BIN", self)
        export_bin_action.setToolTip(
            "Write the edits into a copy of the disc's data track. Only the "
            "edited files' sectors change, so the XA music and voice survive "
            "- this is the one that stays playable")
        export_bin_action.triggered.connect(self.export_bin)
        self.export_bin_action = export_bin_action

        export_iso_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveDVDIcon), "Save ISO", self)
        export_iso_action.setToolTip(
            "Rebuild the opened disc as a new .iso, with any pending TXTD/TXT2 edits applied. "
            "Note this writes 2048-byte sectors, so the XA music and voice do not survive it - "
            "for a playable disc keep using the bin/cue"
        )
        export_iso_action.triggered.connect(self.export_iso)
        self.export_iso_action = export_iso_action
        open_action.triggered.connect(lambda: self.open_iso_dialog())

        open_iso_action = QAction("Open ISO...", self)
        open_iso_action.setToolTip(
            "Open a 2048-byte ISO. Everything except the streamed audio "
            "works; the XA music and voice are not in an ISO to begin with")
        open_iso_action.triggered.connect(
            lambda: self.open_iso_dialog(iso_only=True))

        # Same QAction instances go in both the toolbar and the File menu -
        # Qt keeps them in sync automatically, no separate menu-only copies.
        # Open, then the two things you do with what is open: pull one
        # file out, or write the whole track back. Save IDX/DAT is the
        # older route and lives in the File menu.
        toolbar.addAction(open_action)
        toolbar.addAction(export_action)
        toolbar.addAction(export_bin_action)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        import_labels_action = QAction("Import Labels...", self)
        import_labels_action.setToolTip(
            "Import a labels file - the JSON that names this build's files "
            "in the tree. One ships for each build the tool knows; this is "
            "for one of your own."
        )
        import_labels_action.triggered.connect(self.load_labels_dialog)

        export_labels_action = QAction("Export Labels...", self)
        export_labels_action.setToolTip(
            "Write the names now on the tree out as a labels file. Rename a "
            "row with F2 or the right-click menu; only the name changes - "
            "the address and the type stay as they are."
        )
        export_labels_action.triggered.connect(self.export_labels_dialog)

        builtin_labels_action = QAction("Use Built-in Labels", self)
        builtin_labels_action.setToolTip(
            "Go back to the labels file that matches the open disc"
        )
        builtin_labels_action.triggered.connect(self.use_builtin_labels)
        self.builtin_labels_action = builtin_labels_action
        builtin_labels_action.setEnabled(False)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(open_action)
        file_menu.addAction(open_iso_action)
        file_menu.addAction(open_folder_action)
        file_menu.addSeparator()
        file_menu.addAction(import_labels_action)
        file_menu.addAction(export_labels_action)
        file_menu.addAction(builtin_labels_action)
        file_menu.addSeparator()
        file_menu.addAction(export_action)
        file_menu.addAction(export_bin_action)
        file_menu.addAction(export_files_action)
        file_menu.addAction(export_iso_action)

        font_menu = self.menuBar().addMenu("F&ont Page")
        export_font_action = QAction("Export Font Page...", self)
        export_font_action.setToolTip(
            "Write chunk 0 - the font and menu page - out as an indexed PNG")
        export_font_action.triggered.connect(self.export_font_page)
        font_menu.addAction(export_font_action)
        import_font_action = QAction("Import Font Page...", self)
        import_font_action.setToolTip(
            "Read an edited page back in, re-compressing it in place")
        import_font_action.triggered.connect(self.import_font_page)
        font_menu.addAction(import_font_action)
        font_menu.addSeparator()
        translate_action = QAction("Font && Translation...", self)
        translate_action.setToolTip(
            "Draw glyphs and say what each code means, for a translation")
        translate_action.triggered.connect(self.open_font_editor)
        font_menu.addAction(translate_action)

        settings_menu = self.menuBar().addMenu("&Settings")
        theme_menu = settings_menu.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self._theme_settings = QSettings("Tomba2Edit", "Tomba2Edit")
        current_theme = self._theme_settings.value("theme", theme.DEFAULT_THEME)
        if current_theme not in theme.THEMES:
            current_theme = theme.DEFAULT_THEME  # stale value from an older theme key set
        theme_labels = {"dark": "Dark (default)", "bright": "Bright"}
        for theme_name in theme.THEMES:
            action = QAction(theme_labels[theme_name], self, checkable=True)
            action.setChecked(theme_name == current_theme)
            action.triggered.connect(lambda checked, t=theme_name: self._apply_and_save_theme(t))
            theme_group.addAction(action)
            theme_menu.addAction(action)
        theme.apply_theme(QApplication.instance(), current_theme)

        self.folder_info_label = QLabel("Select a Tomba! 2 ISO file to begin")
        self.statusBar().addPermanentWidget(self.folder_info_label)

        # Fixed to match the left-pane width used by MainExeViewer/BinsViewer
        # (gui/mainbin_viewer.py, gui/bins_viewer.py) so the tree/entries
        # pane doesn't visibly jump width when switching tabs.
        initial_treeview_width = 350
        self.splitter.setSizes([initial_treeview_width, self.width() - initial_treeview_width])

    def load_labels_for_disc(self, idx_path):
        """Pick the names for the disc that has just been opened and put
        them on the tree. Called by idx_parser.parse_idx_file once the
        tree is built.

        A labels file loaded by hand stays in force - someone working on
        their own map doesn't want reopening the ISO to throw it away -
        but it's still scored against the disc, so a set of names that
        doesn't fit says so instead of quietly landing on the wrong
        files."""
        try:
            if self.labels_override is not None:
                self.labels = self.labels_override
                score = self.labels.score(labels_module.idx_addresses(idx_path))
                source = "loaded"
            else:
                self.labels, score = labels_module.choose(idx_path)
                source = "built-in"
        except Exception as e:
            print(f"Could not load labels: {e}")
            self.labels = None
            score, source = 0.0, "built-in"

        named = apply_labels(self)
        # Relabel, never reload - the BINs tab may be holding a SOP.BIN
        # path from a disc that has since been closed and cleaned up.
        self.bins_viewer.set_descriptions(self.labels.bins if self.labels else None)
        self.builtin_labels_action.setEnabled(self.labels_override is not None)

        if self.labels is None:
            self.statusBar().showMessage(
                "No labels file matches this disc - files are listed by "
                f"address only (best match {score:.0%})", 15000)
            return
        fit = "" if score >= 0.999 else f", {score:.0%} of it found on this disc"
        # Rows, not files: the trail lists the same handful of files under
        # nearly every area, so 45 named files fill some 700 rows.
        self.statusBar().showMessage(
            f"Named {named} rows from the {source} labels for "
            f"\"{self.labels.name}\" ({self.labels.named} names){fit}", 15000)

    def _smst_candidates(self, limit=400):
        """Every SMST on the disc, as (label, address, size) - what the
        animation viewer offers as models to pose. Taken off the tree,
        which has already typed and named everything, with the trail's
        repeated copies listed once."""
        model = self.tree_view.model()
        if model is None:
            return []
        found = []
        seen = set()

        def walk(item):
            for row in range(item.rowCount()):
                child = item.child(row)
                if child.hasChildren():
                    walk(child)
                    continue
                data = row_label_data(child)
                if not data or data[1] != "SMST":
                    continue
                address = data[2]
                if address in seen:
                    continue
                entry = child.data(Qt.ItemDataRole.UserRole) or ()
                size = entry[3] if len(entry) > 3 else 0
                if isinstance(entry[0], str):        # a trail row
                    size = entry[2]
                if not size:
                    continue
                seen.add(address)
                found.append((child.text(), address, size))

        walk(model.invisibleRootItem())
        return found[:limit]

    def rename_row(self, item, name):
        """A row was renamed in the tree. Names live in the labels file,
        not in the tree, so this writes it there and lets apply_labels
        redraw - which is also what makes the name survive switching to
        another file and back.

        A disc with no labels of its own gets an empty set made for it
        on the first rename. That is the way to start naming a build
        nobody has mapped: open it, type names in, File > Export
        Labels."""
        if self.labels is None:
            self.labels = labels_module.LabelSet(
                name=os.path.basename(os.path.dirname(self.dat_file or "")) or "Untitled",
                build="custom")
            self.labels_override = self.labels

        data = row_label_data(item)
        if data:
            stem, filetype, address, _detail = data
            size = (item.data(Qt.ItemDataRole.UserRole) or (None,) * 4)[3]
            end = address + size - 1 if isinstance(size, int) and size else 0
            self.labels.rename(address, name, kind=filetype, end=end)
        else:
            index = area_index_of(item)
            if index is None:
                return
            self.labels.rename_area(index, name)

        self.labels_dirty = True
        apply_labels(self)
        self._refresh_edit_status()

    def export_labels_dialog(self):
        """Write the names currently on the tree out as a labels file."""
        if self.labels is None:
            QMessageBox.information(
                self, "Nothing to export",
                "No names are loaded or typed in yet. Rename something in the "
                "tree first, or import a labels file.")
            return
        suggested = os.path.join(
            labels_module.labels_dir(),
            f"{self.labels.build or 'custom'}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export labels", suggested, "Labels files (*.json)")
        if not path:
            return
        try:
            labels_module.save(self.labels, path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        self.labels.path = path
        self.labels_dirty = False
        self._refresh_edit_status()
        self.statusBar().showMessage(
            f"Exported {self.labels.named} names ({len(self.labels)} entries) "
            f"to {os.path.basename(path)}", 15000)

    def load_labels_dialog(self):
        """Load a labels file the user points at, and keep it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load labels file", labels_module.labels_dir(),
            "Labels files (*.json);;All files (*)")
        if not path:
            return
        try:
            label_set = labels_module.load(path)
        except labels_module.LabelError as e:
            QMessageBox.critical(self, "Not a labels file",
                                 f"Couldn't read that as a labels file:\n\n{e}")
            return

        self.labels_override = label_set
        self.labels_dirty = False
        if not self.dat_file:
            # Nothing open yet - it'll be applied when a disc is.
            self.statusBar().showMessage(
                f"Labels \"{label_set.name}\" loaded - open a disc to use them", 15000)
            self.builtin_labels_action.setEnabled(True)
            return
        idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
        self.load_labels_for_disc(idx_path)
        if self.labels.score(labels_module.idx_addresses(idx_path)) < labels_module.MATCH_THRESHOLD:
            QMessageBox.warning(
                self, "Labels don't fit this disc",
                f"\"{label_set.name}\" names {len(label_set)} addresses and "
                "hardly any of them are in this disc's IDX. The names have "
                "been applied anyway - they are probably for a different "
                "build, or for one that hasn't been repacked yet.")

    def use_builtin_labels(self):
        """Drop a hand-loaded labels file and go back to whichever
        built-in one matches the open disc."""
        self.labels_override = None
        if not self.dat_file:
            self.labels = None
            self.builtin_labels_action.setEnabled(False)
            return
        self.load_labels_for_disc(
            os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX"))

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
        state = self.txtd_viewer.pending_state()
        if state == "edited":
            self.pending_txtd_edits[(chunk_index, file_index)] = {
                "kind": "txtd", "id": id_val, "dat_start": dat_start, "offset": offset, "data": current_data,
            }
        else:
            self.pending_txtd_edits.pop((chunk_index, file_index), None)
        self._set_txtd_tree_item_state(chunk_index, file_index, state)
        self._refresh_edit_status()

    def on_txt2_content_changed(self, chunk_index, file_index, id_val, dat_start, offset, current_data):
        """Called by TXT2Viewer every time an entry's text is edited. Same
        idea as on_txtd_content_changed() above - the two kinds of pending
        edit share self.pending_txtd_edits (see its docstring), tagged with
        "kind" so _pack_pending_txtd_edits() knows which packer to use for
        each one; every other bookkeeping step (tree coloring, export
        counting) doesn't need to distinguish between them at all."""
        state = self.txt2_viewer.pending_state()
        if state == "edited":
            self.pending_txtd_edits[(chunk_index, file_index)] = {
                "kind": "txt2", "id": id_val, "dat_start": dat_start, "offset": offset, "data": current_data,
            }
        else:
            self.pending_txtd_edits.pop((chunk_index, file_index), None)
        self._set_txtd_tree_item_state(chunk_index, file_index, state)
        self._refresh_edit_status()

    def on_mainexe_content_changed(self):
        """Called by MainExeViewer every time an entry's text is edited.
        Its own pending edits are tracked entirely inside the viewer
        (self.mainexe_viewer.pending_edits()/has_pending_edits())."""
        self._refresh_edit_status()

    def on_bins_content_changed(self):
        """Same as on_mainexe_content_changed, for SOP.BIN's own pending
        edits (self.bins_viewer.pending_edits()/has_pending_edits())."""
        self._refresh_edit_status()

    def _refresh_edit_status(self):
        """Status bar text AND the tabs' own "*" unsaved-marker, both
        reflecting every pending-edit source at once - called after
        each edit AND after a successful export, so neither goes stale
        showing "pending" once everything's just been saved
        (mark_exported() doesn't emit content_changed, so nothing else
        would refresh this)."""
        n_txtd = len(self.pending_txtd_edits)
        n_mainexe = len(self.mainexe_viewer.pending_edits())
        n_sop = len(self.bins_viewer.pending_edits())

        self.main_tabs.setTabText(0, "DAT Assets*" if n_txtd else "DAT Assets")
        self.main_tabs.setTabText(1, "MAIN.EXE*" if n_mainexe else "MAIN.EXE")
        self.main_tabs.setTabText(2, "BINs*" if n_sop else "BINs")

        renamed = " Names have been changed - File > Export Labels to keep them."             if getattr(self, "labels_dirty", False) else ""

        if n_txtd == 0 and n_mainexe == 0 and n_sop == 0:
            self.statusBar().showMessage(
                ("No pending edits." + renamed) if renamed else "No pending edits.")
        else:
            self.statusBar().showMessage(
                f"{n_txtd} disc file(s), {n_mainexe} MAIN.EXE entry(ies), and {n_sop} "
                f"SOP.BIN line(s) have pending edits - use the 'Save ISO' button "
                f"when ready.{renamed}"
            )

    def _font_page_folder(self):
        """The CD folder of the disc that is open, or None with a note."""
        if not self.dat_file:
            QMessageBox.information(self, "No disc open",
                                    "Open an ISO or a CD folder first.")
            return None
        return os.path.dirname(self.dat_file)

    # Overlay ids run six ahead of the IDX chunk they belong to: chunk 0
    # is START.BIN's id 6, so chunk 4 is id 10, which is A00.BIN. Every
    # one of the 22 chunks carrying a TXTD lines up with an Axx.BIN this
    # way, and the four ids with no area (START, GAME, SOP, CRD) land on
    # exactly the four chunks that have no DAT range.
    OVERLAY_NAMES = {6: "START.BIN", 7: "DEMO.BIN", 8: "GAME.BIN",
                     32: "SOP.BIN", 34: "OPN.BIN", 35: "CRD.BIN"}
    for _i in range(22):
        OVERLAY_NAMES[10 + _i] = f"A0{'0123456789ABCDEFGHIJKL'[_i]}.BIN"

    def voice_image_path(self):
        """The disc to read voice from, without asking for it again.

        If the disc was opened as an image it is already the right file,
        so use it. Only a folder-opened disc has nothing to offer here,
        because the voice track does not survive being extracted."""
        opened = getattr(self, "current_iso_path", None)
        if opened and os.path.exists(opened):
            return opened
        return getattr(self.voice_panel, "image", None)

    # An area's purified form is a chunk of its own, 22 further along,
    # with its own dialogue but the same overlay: AREA_1F's 18 masters
    # are A05's, AREA_20's 16 are A06's, and so on for 1B, 1E, 21 and 22.
    PURIFIED_OFFSET = 22

    def overlay_for_area(self, chunk_index):
        """The Axx.BIN belonging to an area, or None if there isn't one
        or the disc was opened somewhere without a BIN folder."""
        chunk = chunk_index or 0
        name = self.OVERLAY_NAMES.get(chunk + 6)
        if not name:
            # A purified area has no overlay of its own; it runs on the
            # one belonging to the area it is a copy of.
            name = self.OVERLAY_NAMES.get(chunk - self.PURIFIED_OFFSET + 6)
        if not name or not self.dat_file:
            return None
        root = os.path.dirname(os.path.dirname(os.path.dirname(self.dat_file)))
        for folder in (os.path.join(root, "BIN"),
                       os.path.join(os.path.dirname(
                           os.path.dirname(self.dat_file)), "BIN")):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return path
        # A disc opened as an image has no BIN folder on disk - the
        # overlays stay inside it - so take this one out and keep it.
        image = getattr(self, "current_iso_path", None)
        if image and self.iso_handler and self.iso_handler.get_temp_dir():
            cached = os.path.join(self.iso_handler.get_temp_dir(), name)
            if os.path.exists(cached):
                return cached
            data = voice.extract_file(image, name)
            if data:
                with open(cached, "wb") as f:
                    f.write(data)
                return cached
        return None

    def open_font_editor(self):
        """Open the window where glyphs are drawn and codes are named.

        The codes the disc's own text uses are measured from the text
        files themselves rather than assumed, so the window can say which
        cells a translation is free to take. The window does that
        measuring itself, in the background, since it takes a while."""
        cd_folder = self._font_page_folder()
        if cd_folder is None:
            return
        if getattr(self, "font_editor", None) is None:
            self.font_editor = FontEditor()
        self.font_editor.set_source(cd_folder, self.dat_file)
        self.font_editor.show()
        self.font_editor.raise_()

    def export_font_page(self):
        """Write the font/menu page out as an indexed PNG.

        The palette put on the file is one of the page's own CLUTs, so
        it opens looking the way the game draws dialogue rather than as
        a black square. The pixels are the 4-bit indices either way."""
        cd_folder = self._font_page_folder()
        if cd_folder is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export font page", "fontpage.png", "PNG (*.png)")
        if not path:
            return
        try:
            cluts = fontpage.read_cluts(cd_folder)
            # The greyscale ramp the dialogue font uses, where there is one.
            clut = next((pal for _row, _slot, pal in cluts
                         if pal[2][:3] == (255, 255, 255)), None)
            fontpage.export_png(cd_folder, path, clut)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Font page written to {path}", 8000)

    def import_font_page(self):
        """Read an edited page back into TOMBA2.IMG.

        Refused, with nothing written, if the edit no longer fits the
        room the shard was given."""
        cd_folder = self._font_page_folder()
        if cd_folder is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import font page", "", "PNG (*.png)")
        if not path:
            return
        try:
            count = fontpage.import_png(cd_folder, path)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        QMessageBox.information(
            self, "Font page imported",
            f"Rewrote {count} shard(s) in TOMBA2.IMG.\n\n"
            "Reopen the disc to see it in the VRAM view.")
        self.statusBar().showMessage(f"Font page imported from {path}", 8000)

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

    def export_bin(self):
        """Write the edits into a copy of the disc's data track.

        Unlike Save ISO this keeps the disc playable: the track is
        copied byte for byte and only the sectors of the edited files
        are rewritten, so the Form 2 sectors carrying the XA music and
        voice are never touched. The audio track beside it and the cue
        sheet are copied and written to match."""
        import tempfile

        from functions import bin_writer

        source = getattr(self, "current_iso_path", None)
        if not source or not os.path.exists(source):
            QMessageBox.critical(
                self, "No track open",
                "Save BIN patches the disc's data track, so the disc has to "
                "have been opened as one (File > Open BIN).")
            return
        mainexe_edits = self.mainexe_viewer.all_edits()
        sop_edits = self.bins_viewer.all_edits()
        if not self.pending_txtd_edits and not mainexe_edits and not sop_edits:
            QMessageBox.information(self, "Nothing to save",
                                    "No edits are pending.")
            return
        default = os.path.splitext(os.path.basename(source))[0] + " (edited).bin"
        target, _ = QFileDialog.getSaveFileName(
            self, "Save patched data track", default, "Disc track (*.bin)")
        if not target:
            return

        edits = self._pack_pending_txtd_edits()
        if edits is None:
            return
        replacements = {}
        with tempfile.TemporaryDirectory(prefix="tomba2bin_") as work:
            try:
                from functions.repacker import repack_files
                dat = os.path.join(work, "TOMBA2.DAT")
                idx = os.path.join(work, "TOMBA2.IDX")
                repack_files(self.dat_file,
                             os.path.join(os.path.dirname(self.dat_file),
                                          "TOMBA2.IDX"),
                             edits, dat, idx)
                replacements["TOMBA2.DAT"] = open(dat, "rb").read()
                replacements["TOMBA2.IDX"] = open(idx, "rb").read()
                if mainexe_edits:
                    exe = os.path.join(work, "MAIN.EXE")
                    mainbin_repack_pool(self.mainexe_viewer.exe_path,
                                        self.mainexe_viewer.entries,
                                        mainexe_edits, exe)
                    replacements["MAIN.EXE"] = open(exe, "rb").read()
                if sop_edits:
                    sop = os.path.join(work, "SOP.BIN")
                    sop_repack_pool(self.bins_viewer.sop_viewer.sop_path,
                                    self.bins_viewer.sop_viewer.entries,
                                    sop_edits, sop)
                    replacements["SOP.BIN"] = open(sop, "rb").read()
            except Exception as exc:
                QMessageBox.critical(self, "Save failed",
                                     f"Could not rebuild the files: {exc}")
                return

            self.statusBar().showMessage("Copying the track...", 0)
            QApplication.processEvents()
            try:
                notes = bin_writer.patch_track(source, target, replacements)
            except Exception as exc:
                QMessageBox.critical(self, "Save failed", str(exc))
                self.statusBar().clearMessage()
                return

        extra = self._copy_audio_track(source, target)
        self.statusBar().clearMessage()
        QMessageBox.information(
            self, "Saved",
            "Wrote:\n" + target + "\n\n" + "\n".join(notes) +
            ("\n\n" + extra if extra else "") +
            "\n\nEvery other sector is byte for byte as it was, so the "
            "music and voice are untouched.")
        for (chunk_index, file_index), info in self.pending_txtd_edits.items():
            self._set_txtd_tree_item_state(chunk_index, file_index, "exported")
            if info.get("kind") == "txt2":
                self.txt2_viewer.mark_exported(chunk_index, file_index)
            else:
                self.txtd_viewer.mark_exported(chunk_index, file_index)
        self.pending_txtd_edits.clear()
        if mainexe_edits:
            self.mainexe_viewer.mark_exported()
        if sop_edits:
            self.bins_viewer.mark_exported()
        self._refresh_edit_status()

    def _copy_audio_track(self, source, target):
        """Bring the disc's audio track and a cue sheet along.

        A bin/cue names its tracks in separate files, so the second one
        needs copying beside the patched first and a cue written over
        both - otherwise the music track is simply missing."""
        from functions import bin_writer

        folder = os.path.dirname(source)
        stem = os.path.basename(source)
        audio = None
        if "Track 1" in stem:
            candidate = os.path.join(folder, stem.replace("Track 1", "Track 2"))
            if os.path.exists(candidate):
                audio = candidate
        out_dir = os.path.dirname(target)
        out_stem = os.path.splitext(os.path.basename(target))[0]
        tracks = [(os.path.basename(target), "MODE2/2352")]
        note = ""
        if audio:
            copied = os.path.join(out_dir, out_stem + " (Track 2).bin")
            if os.path.abspath(copied) != os.path.abspath(audio):
                shutil.copyfile(audio, copied)
            tracks.append((os.path.basename(copied), "AUDIO"))
            note = "The audio track was copied beside it."
        cue = bin_writer.write_cue(os.path.join(out_dir, out_stem + ".cue"),
                                   tracks)
        return (note + f" Cue sheet: {os.path.basename(cue)}").strip()

    def export_all_files(self):
        # all_edits(), not pending_edits() - every export runs against
        # the untouched source exe fresh, so it must reapply every
        # change made since load (edited AND already-exported alike),
        # not just what's newly dirty, or a previous export's changes
        # would silently get dropped from this one.
        mainexe_edits = self.mainexe_viewer.all_edits()
        sop_edits = self.bins_viewer.all_edits()
        if not self.pending_txtd_edits and not mainexe_edits and not sop_edits:
            QMessageBox.information(self, "Nothing to export",
                                     "No TXTD/TXT2/MAIN.EXE/SOP.BIN edits are pending. Edit some entry text first.")
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

        output_paths = [output_dat, output_idx]
        if mainexe_edits:
            output_exe = os.path.join(out_dir, "MAIN.EXE")
            try:
                mainbin_repack_pool(
                    self.mainexe_viewer.exe_path, self.mainexe_viewer.entries,
                    mainexe_edits, output_exe,
                )
            except MainBinEditError as e:
                QMessageBox.critical(self, "Export failed", f"Failed to rebuild MAIN.EXE: {e}")
                return
            output_paths.append(output_exe)

        if sop_edits:
            output_sop = os.path.join(out_dir, "SOP.BIN")
            try:
                sop_repack_pool(
                    self.bins_viewer.sop_viewer.sop_path, self.bins_viewer.sop_viewer.entries,
                    sop_edits, output_sop,
                )
            except SopEditError as e:
                QMessageBox.critical(self, "Export failed", f"Failed to rebuild SOP.BIN: {e}")
                return
            output_paths.append(output_sop)

        extras = []
        if mainexe_edits:
            extras.append("MAIN.EXE")
        if sop_edits:
            extras.append("SOP.BIN")
        extras_suffix = "".join(f" + {name}" for name in extras)
        QMessageBox.information(
            self, "Export complete",
            "Wrote:\n" + "\n".join(output_paths) + "\n\n"
            f"({len(edits)} disc file(s){extras_suffix} repacked.)\n\n"
            "Back up your original CD files, then copy these over them "
            "to test in-game. TOMBA2.IMG is unchanged and doesn't need copying."
        )
        for (chunk_index, file_index), info in self.pending_txtd_edits.items():
            self._set_txtd_tree_item_state(chunk_index, file_index, "exported")
            if info.get("kind") == "txt2":
                self.txt2_viewer.mark_exported(chunk_index, file_index)
            else:
                self.txtd_viewer.mark_exported(chunk_index, file_index)
        self.pending_txtd_edits.clear()
        if mainexe_edits:
            self.mainexe_viewer.mark_exported()
        if sop_edits:
            self.bins_viewer.mark_exported()
        self._refresh_edit_status()

    @staticmethod
    def _area_chunk_index(item):
        """The AREA_NN number a file row sits under (its folder's parent,
        or the folder itself), or None if it isn't under one. The label
        carries a count - "AREA_04 (41)" - so only the first word of the
        number is parsed."""
        parent = item.parent()
        if parent is None:
            return None
        for candidate in (parent.parent(), parent):
            if candidate is not None and candidate.text().startswith("AREA_"):
                try:
                    return int(candidate.text().split("_")[1].split()[0], 16)
                except ValueError:
                    return None
        return None

    def _load_area_vram_bytes(self, chunk_index, merge_common=False):
        """This area's TOMBA2.IMG chunk, decompressed to raw VRAM - what
        SPRT pieces are cut out of. None (with a printed reason) if the
        area has no VRAM or it can't be read; callers are expected to
        carry on without it.

        `merge_common` fills in what AREA_01 holds wherever this area's
        own VRAM is empty. The character models sample texture pages
        that are only ever in AREA_01's chunk - it is loaded once and
        stays resident - and the two never overlap by a byte on the
        retail disc, so the merge adds their art without touching the
        area's own."""
        if chunk_index is None or not self.dat_file:
            return None
        cd_folder = os.path.dirname(self.dat_file)
        try:
            with open(os.path.join(cd_folder, "TOMBA2.IDX"), "rb") as IDX, \
                    open(os.path.join(cd_folder, "TOMBA2.IMG"), "rb") as IMG:
                IDX.seek(chunk_index * 0x800)
                img_start, img_end, _, _, _ = struct.unpack("<5I", IDX.read(20))
                if img_end <= img_start:
                    vram = None
                else:
                    IMG.seek(img_start)
                    vram = decode_vram_bytes(IMG.read(img_end - img_start))
        except Exception as e:
            print(f"Could not load VRAM for AREA_{chunk_index:02X}: {e}")
            return None

        if not merge_common or chunk_index == COMMON_VRAM_AREA:
            return vram
        common = self._load_area_vram_bytes(COMMON_VRAM_AREA)
        if common is None:
            return vram
        if vram is None:
            return common
        own = np.frombuffer(bytes(vram), dtype=np.uint8).copy()
        shared = np.frombuffer(bytes(common), dtype=np.uint8)
        empty = own == 0
        own[empty] = shared[:own.size][empty]
        return bytearray(own.tobytes())

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

    def _load_mainexe(self, exe_path):
        """Load exe_path into the MAIN.EXE tab, or clear it with a clear
        reason if that's not possible - either no file was found (None)
        or it's not the specific build mainbin_editor.py's pointer tables
        were mapped against (MainBinEditError, most likely
        UnsupportedExeError - see verify_supported())."""
        if not exe_path:
            self.mainexe_viewer.clear_cache()
            return
        try:
            self.mainexe_viewer.preview.set_source(
                os.path.dirname(self.dat_file) if self.dat_file else None)
            self.mainexe_viewer.load_exe(exe_path)
        except MainBinEditError as e:
            self.mainexe_viewer.clear_cache()
            QMessageBox.warning(
                self, "MAIN.EXE not editable",
                f"Found a MAIN.EXE, but couldn't load it for editing:\n\n{e}"
            )

    def _load_bins(self, overlays, sop_path):
        """Populate the BINs tab - overlays: [{"name", "size"}, ...] for
        every file in the disc's BIN/ folder, sop_path: extracted
        SOP.BIN path or None. Never refuses - an unrecognized SOP.BIN
        build falls back to a read-only view instead (see BinsViewer).

        The overlay names are opaque - A0F.BIN is the Last Pig Boss -
        so what each one is comes from the open labels file's "bins"
        section, if it has one."""
        self.bins_viewer.set_font_source(
            os.path.dirname(self.dat_file) if self.dat_file else None)
        self.bins_viewer.load_overlays(
            overlays, sop_path,
            self.labels.bins if self.labels else None)

    def open_iso_dialog(self, _checked=False, iso_only=False):
        """Extract TOMBA2.DAT/IDX/IMG from a disc image into a temp
        folder and populate the tree view. See open_folder_dialog() for
        opening an already-extracted folder instead.

        `iso_only` is the File > Open ISO route. The toolbar's opener
        asks for a BIN first, because that is the one that carries the
        voice track; an ISO cannot."""
        if iso_only:
            title = "Select a Tomba! 2 ISO"
            filters = "Disc image (*.iso *.img);;All files (*)"
        else:
            title = "Select the disc's data track (Track 1)"
            filters = ("Disc data track (*.bin);;Disc image "
                       "(*.iso *.img);;All files (*)")
        iso_path, _ = QFileDialog.getOpenFileName(self, title, "", filters)
        if not iso_path:
            return

        if self.pending_txtd_edits or self.mainexe_viewer.has_pending_edits() or self.bins_viewer.has_pending_edits():
            proceed = QMessageBox.question(
                self, "Discard pending edits?",
                f"You have {len(self.pending_txtd_edits)} TXTD/TXT2 edit(s), "
                f"{len(self.mainexe_viewer.pending_edits())} MAIN.EXE edit(s), and "
                f"{len(self.bins_viewer.pending_edits())} SOP.BIN edit(s) that haven't been "
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
        self.mainexe_viewer.clear_cache()
        self.bins_viewer.clear_cache()
        self.current_iso_path = None
        self._refresh_edit_status()

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

        self._load_mainexe(self.iso_handler.extracted_files.get("MAIN.EXE"))
        self._load_bins(self.iso_handler.bin_overlays, self.iso_handler.extracted_files.get("SOP.BIN"))

        self.current_iso_path = iso_path
        self.folder_info_label.setText(f"Loaded ISO: {iso_path}")
        # The Dialogues tab reads its audio out of the same track, so
        # opening the disc is enough - it should not have to be opened a
        # second time over there. A 2048-byte ISO simply has no voice in
        # it, and the panel says so itself.
        self.voice_panel.set_image(iso_path)
        self.music_panel.set_image(iso_path)

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

        if self.pending_txtd_edits or self.mainexe_viewer.has_pending_edits() or self.bins_viewer.has_pending_edits():
            proceed = QMessageBox.question(
                self, "Discard pending edits?",
                f"You have {len(self.pending_txtd_edits)} TXTD/TXT2 edit(s), "
                f"{len(self.mainexe_viewer.pending_edits())} MAIN.EXE edit(s), and "
                f"{len(self.bins_viewer.pending_edits())} SOP.BIN edit(s) that haven't been "
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
        self.mainexe_viewer.clear_cache()
        self.bins_viewer.clear_cache()
        self._refresh_edit_status()

        try:
            parse_idx_file(self, cd_folder)
        except Exception as e:
            self.dat_file = None
            self.folder_info_label.setText("Select a Tomba! 2 ISO file to begin")
            QMessageBox.critical(self, "Error", f"Failed to parse TOMBA2.IDX from this folder:\n\n{e}")
            return

        # MAIN.EXE sits alongside the CD folder (one level up from
        # TOMBA2.DAT/IDX/IMG), not inside it - confirmed against a real
        # extracted Tomba! 2 folder layout - but also check cd_folder
        # itself in case some other extraction puts it there instead.
        mainexe_path = None
        for candidate_dir in (folder, cd_folder):
            candidate = os.path.join(candidate_dir, "MAIN.EXE")
            if os.path.exists(candidate):
                mainexe_path = candidate
                break
        self._load_mainexe(mainexe_path)

        # BIN/ sits alongside MAIN.EXE, same two candidate locations.
        bin_dir = None
        for candidate_dir in (folder, cd_folder):
            candidate = os.path.join(candidate_dir, "BIN")
            if os.path.isdir(candidate):
                bin_dir = candidate
                break
        overlays = []
        sop_path = None
        if bin_dir:
            for name in os.listdir(bin_dir):
                full = os.path.join(bin_dir, name)
                if os.path.isfile(full):
                    overlays.append({"name": name.upper(), "size": os.path.getsize(full)})
                    if name.upper() == "SOP.BIN":
                        sop_path = full
        self._load_bins(overlays, sop_path)

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
        from gui.txtd import txtd_packer
        from gui.txtd import txt2_packer
        edits = []
        try:
            for (chunk_index, file_index), info in self.pending_txtd_edits.items():
                if info.get("kind") == "txt2":
                    if info.get("id") == 3:
                        packed_bytes = txt2_packer.pack_txt2_simple(info["data"])
                    else:
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
        # all_edits(), not pending_edits() - see export_all_files()'s
        # own comment on this for why.
        mainexe_edits = self.mainexe_viewer.all_edits()
        sop_edits = self.bins_viewer.all_edits()
        has_any_edits = bool(edits) or bool(mainexe_edits) or bool(sop_edits)

        default_name = os.path.splitext(os.path.basename(self.current_iso_path))[0]
        default_name += "_edited.iso" if has_any_edits else "_copy.iso"
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
            if edits or mainexe_edits or sop_edits:
                tmp_repack_dir = tempfile.mkdtemp(prefix="tomba2edit_repack_")

            if edits:
                original_idx = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
                tmp_dat = os.path.join(tmp_repack_dir, "TOMBA2.DAT")
                tmp_idx = os.path.join(tmp_repack_dir, "TOMBA2.IDX")
                repack_files(self.dat_file, original_idx, edits, tmp_dat, tmp_idx)
                with open(tmp_dat, "rb") as f:
                    replacements["TOMBA2.DAT"] = f.read()
                with open(tmp_idx, "rb") as f:
                    replacements["TOMBA2.IDX"] = f.read()

            if mainexe_edits:
                tmp_exe = os.path.join(tmp_repack_dir, "MAIN.EXE")
                try:
                    mainbin_repack_pool(
                        self.mainexe_viewer.exe_path, self.mainexe_viewer.entries,
                        mainexe_edits, tmp_exe,
                    )
                except MainBinEditError as e:
                    QMessageBox.critical(self, "Export failed", f"Failed to rebuild MAIN.EXE:\n\n{e}")
                    return
                with open(tmp_exe, "rb") as f:
                    replacements["MAIN.EXE"] = f.read()

            if sop_edits:
                tmp_sop = os.path.join(tmp_repack_dir, "SOP.BIN")
                try:
                    sop_repack_pool(
                        self.bins_viewer.sop_viewer.sop_path, self.bins_viewer.sop_viewer.entries,
                        sop_edits, tmp_sop,
                    )
                except SopEditError as e:
                    QMessageBox.critical(self, "Export failed", f"Failed to rebuild SOP.BIN:\n\n{e}")
                    return
                with open(tmp_sop, "rb") as f:
                    replacements["SOP.BIN"] = f.read()

            build_iso(self.current_iso_path, replacements, output_path)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Failed to rebuild ISO:\n\n{e}")
            return
        finally:
            if tmp_repack_dir:
                shutil.rmtree(tmp_repack_dir, ignore_errors=True)

        extras = []
        if mainexe_edits:
            extras.append("MAIN.EXE")
        if sop_edits:
            extras.append("SOP.BIN")
        extras_suffix = "".join(f" + {name}" for name in extras)
        summary = (
            f"({len(edits)} disc file(s){extras_suffix} repacked into it.)"
            if has_any_edits else
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
        if mainexe_edits:
            self.mainexe_viewer.mark_exported()
        if sop_edits:
            self.bins_viewer.mark_exported()
        self._refresh_edit_status()

    def closeEvent(self, event):
        if self.iso_handler:
            self.iso_handler.cleanup()
        super().closeEvent(event)

    def tuplify(self, item):
        dat_id = item >> 24
        dat_ptr = item & 0x00FFFFFF
        return (dat_id, dat_ptr)


    def _apply_and_save_theme(self, theme_name):
        theme.apply_theme(QApplication.instance(), theme_name)
        self._theme_settings.setValue("theme", theme_name)

    def setup_tree_view(self):
        self.tree_view.setModel(QStandardItemModel())
        self.tree_view.setHeaderHidden(False)
        # Renaming a row edits only the name part of it - the address and
        # the type aren't anyone's to change (see LabelNameDelegate).
        self.tree_view.setItemDelegate(LabelNameDelegate(self.rename_row, self))
        # F2 only. Qt's default triggers include DoubleClicked and
        # SelectedClicked, and this tree is browsed by clicking - double
        # click is how a folder opens and how a file gets looked at, so
        # either of those would put a text box where the user wanted the
        # thing they clicked on. Right-click offers Rename as well, since
        # a keyboard shortcut on its own is undiscoverable.
        self.tree_view.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._tree_context_menu)

    def _tree_context_menu(self, position):
        """Right-click menu for the tree: rename, and export the bytes
        of whatever was clicked."""
        index = self.tree_view.indexAt(position)
        if not index.isValid():
            return
        item = self.tree_view.model().itemFromIndex(index)
        if item is None:
            return

        menu = QMenu(self)
        renameable = bool(item.flags() & Qt.ItemFlag.ItemIsEditable)
        rename = menu.addAction("Rename\tF2")
        rename.setEnabled(renameable)
        rename.triggered.connect(lambda: self.tree_view.edit(index))
        if not renameable:
            rename.setToolTip(
                "Only AREA folders and the files in them carry names")

        if item.data(Qt.ItemDataRole.UserRole):
            menu.addSeparator()
            export = menu.addAction("Export File...")
            export.triggered.connect(self.export_selected_bytes)

        menu.exec(self.tree_view.viewport().mapToGlobal(position))

    def setup_widgets(self):
        self.txtd_viewer = TXTDViewer()
        self.txt2_viewer = TXT2Viewer()
        self.mdat_viewer = MDATViewer()
        # A DRWA isn't a file of its own - it's the head of an MDAT
        # entry, and the pointers in it are what reach that entry's
        # geometry (see gui/drwa/drwa_parser.py). So it hangs off the
        # MDAT rows as a second tab rather than getting a tree row.
        self.drwa_viewer = DRWAViewer()
        self.mdat_tabs = QTabWidget()
        self.mdat_tabs.addTab(self.mdat_viewer, "3D View")
        self.mdat_tabs.addTab(self.drwa_viewer, "Drawmap (DRWA)")
        # DRWB, unlike DRWA, IS a file of its own - four of them - so
        # it gets the tree rows the IDX already labels DRWB.
        self.drwb_viewer = DRWBViewer()
        # An SMST is the same polygons as an MDAT with no drawmap over
        # them - a model's parts rather than a level - so it gets its
        # own 3D view with a part list beside it.
        self.smst_viewer = SMSTViewer()
        self.smst_panel = SMSTPanel(self.smst_viewer)
        # TANP/BETP/ALFD/MDAP are all the same animation container, so
        # one viewer serves every name a labels file may give them.
        self.anmp_viewer = ANMPViewer()
        self.scld_viewer = SCLDViewer()
        self.scld_panel = SCLDDebugPanel(self.scld_viewer)
        self.sprt_viewer = SPRTViewer()
        self.bgmp_viewer = BGMPViewer()
        self.vram_viewer = VRAMViewer()  # Add this line

        self.widgets = {
            "Folder": QLabel("This is a folder"),
            "SPRT": self.sprt_viewer,
            "BGMP": self.bgmp_viewer,
            "TXTD": self.txtd_viewer,
            "TXT1": self.txt2_viewer,  # same layout as TXT2, shares the viewer
            "TXT2": self.txt2_viewer,
            "MDAT": self.mdat_tabs,
            "SMST": self.smst_panel,
            "ANMP": self.anmp_viewer,
            "TANP": self.anmp_viewer,
            "BETP": self.anmp_viewer,
            "ALFD": self.anmp_viewer,
            "ALFP": self.anmp_viewer,
            "MDAP": self.anmp_viewer,
            "DRWB": self.drwb_viewer,
            "SCLD": self.scld_panel,
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
                    elif id == "trail":
                        # A trail file is named by an absolute address
                        # rather than an area-relative offset, and has no
                        # id at all - its type was read out of its bytes
                        # (functions/idx_parser._trail_type). Presented to
                        # the viewers below the same way an SDAT entry is.
                        _, address, entry_size, _ = additional_data
                        dat_start, offset, id = address, 0, None
                        print(f"Trail file at {address:X}, {entry_size} bytes")
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
                                    self.txtd_viewer.preview.set_source(
                                        os.path.dirname(self.dat_file))
                                    self.txtd_viewer.load_txtd_data(
                                        self.dat_file, dat_start, offset,
                                        chunk_index=txtd_chunk_index, file_index=txtd_file_index, id_val=id
                                    )
                                    self.txtd_viewer.set_voice_source(
                                        self.voice_image_path(),
                                        self.overlay_for_area(txtd_chunk_index),
                                        replace_overlay=True)
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
                                    self.txt2_viewer.preview.set_source(
                                        os.path.dirname(self.dat_file))
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

                            chunk_index = None
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

                                # The drawmap at the head of this same
                                # entry - never fatal, since the 3D view
                                # stands on its own if it won't parse.
                                self.drwa_viewer.load_drwa_data(
                                    self.dat_file, dat_start, offset, entry_size,
                                    chunk_index=chunk_index)

                                self.mdat_viewer.load_collision_data(None, None, None, None)
                                if chunk_index is not None:
                                    try:
                                        idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
                                        scld_location = find_area_scld_location(idx_path, chunk_index)
                                        if scld_location:
                                            scld_dat_start, scld_offset, scld_size = scld_location
                                            self.mdat_viewer.load_collision_data(
                                                self.dat_file, scld_dat_start, scld_offset, scld_size)
                                    except Exception as e:
                                        print(f"Could not load collision data for AREA_{chunk_index:02X}: {e}")
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading MDAT file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load MDAT file: {e}")

                    elif widget == self.widgets["SMST"]:
                        # The VRAM has to be in place before the model
                        # is parsed - the CLUTs are cut out of it while
                        # the buffers are built - and it needs AREA_01
                        # merged in, which is where every character
                        # model's texture pages actually live.
                        try:
                            if self.dat_file:
                                print("Loading SMST data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                vram_bytes = self._load_area_vram_bytes(
                                    chunk_index, merge_common=True)
                                self.smst_viewer.set_vram(
                                    vram_bytes,
                                    vram_index_image(vram_bytes) if vram_bytes else None)
                                success = self.smst_viewer.load_smst_data(
                                    self.dat_file, dat_start + offset, entry_size)
                                self.smst_panel.populate_table()
                                if not success:
                                    QMessageBox.critical(
                                        self, "Error",
                                        "Failed to load SMST data - see the console "
                                        "for what didn't read.")
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading SMST file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load SMST file: {e}")

                    elif widget == self.widgets["ANMP"]:
                        # An animation names no model, so the viewer is
                        # handed every SMST on the disc to choose from
                        # (see ANMPViewer.load_anmp_data).
                        try:
                            if self.dat_file:
                                print("Loading ANMP data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                vram_bytes = self._load_area_vram_bytes(
                                    chunk_index, merge_common=True)
                                self.anmp_viewer.load_anmp_data(
                                    self.dat_file, dat_start + offset, entry_size,
                                    candidates=self._smst_candidates(),
                                    vram_bytes=vram_bytes,
                                    vram_image=(vram_index_image(vram_bytes)
                                                if vram_bytes else None))
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading ANMP file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load animation: {e}")

                    elif widget == self.widgets["SCLD"]:
                        try:
                            if self.dat_file:
                                print("Loading SCLD data...")
                                area_name = None
                                parent = selected_item.parent()
                                if parent:
                                    grandparent = parent.parent()
                                    if grandparent and grandparent.text().startswith("AREA_"):
                                        area_name = grandparent.text()
                                    elif parent.text().startswith("AREA_"):
                                        area_name = parent.text()
                                chunk_index = None
                                if area_name:
                                    area_number = area_name.split("_")[1].split()[0]
                                    try:
                                        chunk_index = int(area_number, 16)
                                    except ValueError:
                                        chunk_index = None
                                success = self.scld_viewer.load_scld_data(
                                    self.dat_file, dat_start, offset, entry_size, chunk_index=chunk_index)
                                if success:
                                    self.scld_panel.populate_table()
                                else:
                                    QMessageBox.critical(self, "Error", "Failed to load SCLD data")
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading SCLD file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load SCLD file: {e}")

                    elif widget == self.widgets["DRWB"]:
                        # Needs the IDX as well as the DAT: the map is
                        # compared against its area's MDATs, and which
                        # of them it belongs to is measured rather than
                        # assumed (see DRWBViewer._match_level).
                        try:
                            if self.dat_file:
                                print("Loading DRWB data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                idx_path = os.path.join(
                                    os.path.dirname(self.dat_file), "TOMBA2.IDX")
                                self.drwb_viewer.load_drwb_data(
                                    self.dat_file, dat_start, offset, entry_size,
                                    chunk_index=chunk_index, idx_path=idx_path)
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading DRWB file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load DRWB file: {e}")

                    elif widget in (self.widgets["SPRT"], self.widgets["BGMP"]):
                        # Both formats are nothing but references into the
                        # area's VRAM, and both still parse and lay out
                        # without it - a missing or unreadable VRAM only
                        # costs the artwork, so it's never fatal here.
                        kind = "SPRT" if widget == self.widgets["SPRT"] else "BGMP"
                        try:
                            if self.dat_file:
                                print(f"Loading {kind} data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                vram_bytes = self._load_area_vram_bytes(chunk_index)
                                loader = (self.sprt_viewer.load_sprt_data if kind == "SPRT"
                                          else self.bgmp_viewer.load_bgmp_data)
                                loader(self.dat_file, dat_start, offset, entry_size,
                                       chunk_index=chunk_index, vram_bytes=vram_bytes)
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading {kind} file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load {kind} file: {e}")

                    else:
                        print(f"No specialized viewer for {file_type} files")
                else:
                    print("No additional data found.")
        except Exception as e:
            print(f"Error in on_tree_selection_changed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to handle selection change: {e}")
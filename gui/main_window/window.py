"""The application window: its tabs, its menus, and what it opens.

The work itself lives in the mixins beside this file - one per
concern, listed in the bases below. What is left here is the window:
building it, loading a disc or a project into it, and closing it.
"""
import os
import re
import shutil

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QTabWidget,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget)
from formats.animation.anmp_viewer import ANMPViewer
from formats.archive.idx_parser import LabelNameDelegate, area_index_of
from formats.audio import snd_edit
from formats.audio.music_panel import MusicPanel
from formats.audio.sfx_panel import SfxPanel
from formats.audio.voice_edit import VoiceEditStore
from formats.background.bgmp_viewer import BGMPViewer
from formats.collision.scld_viewer import SCLDDebugPanel, SCLDViewer
from formats.drawmaps.drwa_viewer import DRWAViewer
from formats.drawmaps.drwb_viewer import DRWBViewer
from formats.executable.bins_viewer import BinsViewer
from formats.executable.mainbin_editor import MainBinEditError
from formats.executable.mainbin_viewer import MainExeViewer
from formats.geometry.mdat_panel import MDATPanel
from formats.geometry.mdat_viewer import MDATViewer
from formats.images.img_browser import IMGBrowser
from formats.images.img_viewer import IMGViewer
from formats.models import smst_parser
from formats.models.smst_viewer import SMSTPanel, SMSTViewer
from formats.movie.movie_panel import MoviePanel
from formats.sprites.sprt_viewer import SPRTViewer
from formats.text.font_page_view import FontPageView
from formats.text.txt2_viewer import TXT2Viewer
from formats.text.txtd_viewer import TXTDViewer
from formats.text.voice_panel import VoicePanel
from game import game_build
from gui import theme
from gui.level.level_panel import LevelEditorPanel
from gui.widgets import panel_title
from icons.icons import (
    icon_ALFD,
    icon_BETP,
    icon_BGMP,
    icon_CVRAM,
    icon_DRWB,
    icon_MDAT,
    icon_SCLD,
    icon_SMST,
    icon_SPRT,
    icon_TANP,
    icon_TXT2,
    icon_TXTD,
    icon_VRAM,
    icon_window)
from main import version
from psx.vram_viewer import VRAMViewer
from gui.main_window.common import _panel_player
from gui.main_window.tree import TreeMixin
from gui.main_window.viewers import ViewerDispatchMixin
from gui.main_window.labels import LabelsMixin
from gui.main_window.models import ModelsMixin
from gui.main_window.files import FileEditsMixin
from gui.main_window.textures import TextureMigrationMixin
from gui.main_window.text import TextMixin
from gui.main_window.vram import VramMixin
from gui.main_window.disc import DiscMixin
from gui.main_window.project import ProjectMixin


class MainWindow(TreeMixin, ViewerDispatchMixin, LabelsMixin, ModelsMixin, FileEditsMixin, TextureMigrationMixin, TextMixin, VramMixin, DiscMixin, ProjectMixin, QMainWindow):
    # Overlay ids run six ahead of the IDX chunk they belong to: chunk 0
    # is START.BIN's id 6, so chunk 4 is id 10, which is A00.BIN. Every
    # one of the 22 chunks carrying a TXTD lines up with an Axx.BIN this
    # way, and the four ids with no area (START, GAME, SOP, CRD) land on
    # exactly the four chunks that have no DAT range.
    # AREA_03, the intro, runs on SOP.BIN - see game.placement for
    # what says so. It mapped to nothing before, so its characters had
    # only MAIN.EXE to be posed from.
    OVERLAY_NAMES = {6: "START.BIN", 7: "DEMO.BIN", 8: "GAME.BIN",
                     9: "SOP.BIN",
                     32: "SOP.BIN", 34: "OPN.BIN", 35: "CRD.BIN"}

    for _i in range(22):
        OVERLAY_NAMES[10 + _i] = f"A0{'0123456789ABCDEFGHIJKL'[_i]}.BIN"

    # ------------------------------------------------------------------
    # The disc behind the project
    # ------------------------------------------------------------------
    #
    # The game's files are not the disc, and three things need the real
    # image: building a playable disc (the CD audio and the XA music
    # only survive being patched in place - see formats/archive/bin_writer),
    # the spoken dialogue, and an area's overlay. Each of those used to
    # read current_iso_path and give up when it was None, which is every
    # project, because opening one cleared it and nothing ever set it
    # again. They ask here instead, and here knows how to find the disc
    # or who to ask for it.

    DISC_SETTING = "source_disc/recent"

    PROJECT_SETTING = "project/recent"

    # An area's purified form is a chunk of its own, 22 further along,
    # with its own dialogue but the same overlay: AREA_1F's 18 masters
    # are A05's, AREA_20's 16 are A06's, and so on for 1B, 1E, 21 and 22.
    PURIFIED_OFFSET = 22

    # ------------------------------------------------------------------
    # Text out to be translated, and back in again
    # ------------------------------------------------------------------
    #
    # The whole script leaves as one file and comes back as one file,
    # because that is how a translation is actually done - somewhere
    # else, on all of it at once. formats/text/translation_io owns the
    # format; what lives here is finding every text file on the disc,
    # and what to do about characters the build has never had.

    TEXT_FILTER = ("JSON (*.json);;Plain text (*.txt);;All files (*)")

    WORK_PREFIX = "tomba2project-"

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
        self.tree_search = QLineEdit()
        self.tree_search.setPlaceholderText(
            "Search by name or offset (e.g. 55F54, 19-11Da4)...")
        self.tree_search.setClearButtonEnabled(True)
        self.tree_search.textChanged.connect(self._filter_tree)
        tree_panel = QWidget()
        tree_panel_layout = QVBoxLayout()
        tree_panel_layout.setContentsMargins(0, 0, 0, 0)
        tree_panel_layout.setSpacing(0)
        tree_panel_layout.addWidget(panel_title.make_panel_title("Main tree view"))
        tree_panel_layout.addWidget(self.tree_search)
        tree_panel_layout.addWidget(self.tree_view)
        tree_panel.setLayout(tree_panel_layout)
        self.splitter.addWidget(tree_panel)
        self.widgets_area = QStackedWidget()
        self.splitter.addWidget(self.widgets_area)

        # Data View: one row per DAT address instead of the Indexed
        # View's one row per area that reaches it - see
        # idx_parser.build_dat_view(). It has no preview pane of its
        # own; opening a row jumps to the same address in the Indexed
        # View instead, which is what actually knows how to show each
        # file type - see _open_from_dat_view.
        self.dat_view = QTreeView()
        self.dat_view.setHeaderHidden(True)
        self.dat_view.doubleClicked.connect(self._open_from_dat_view)
        self.dat_view.setItemDelegate(LabelNameDelegate(self.rename_row, self.dat_view))
        # The same right-click menu the Indexed View has - see
        # install_file_menu.
        self.install_file_menu(self.dat_view)
        self.dat_search = QLineEdit()
        self.dat_search.setPlaceholderText(
            "Search by name or offset (e.g. 55F54, 19-11Da4)...")
        self.dat_search.setClearButtonEnabled(True)
        self.dat_search.textChanged.connect(self._filter_dat_view)
        dat_panel = self.dat_panel = QWidget()
        dat_panel_layout = QVBoxLayout()
        dat_panel_layout.setContentsMargins(0, 0, 0, 0)
        dat_panel_layout.setSpacing(0)
        dat_panel_layout.addWidget(panel_title.make_panel_title(
            "Every file in TOMBA2.DAT, - double-click to open it "
            "in the Indexed View"))
        dat_panel_layout.addWidget(self.dat_search)
        dat_panel_layout.addWidget(self.dat_view)
        dat_panel.setLayout(dat_panel_layout)

        # Tab 1 (default): the existing tree + per-file viewer splitter.
        # Tab 2: MAIN.EXE's own string-pool editor (gui/mainbin_viewer.py) -
        # populated whenever a MAIN.EXE is found alongside the opened
        # ISO/folder (see open_iso_dialog/open_folder_dialog), left empty
        # otherwise.
        self.mainexe_viewer = MainExeViewer()
        self.bins_viewer = BinsViewer()
        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(self.splitter, "Indexed View (IDX)")
        self.main_tabs.addTab(dat_panel, "Data View (DAT)")
        self.img_browser = IMGBrowser()
        self.main_tabs.addTab(self.img_browser, "Image View (IMG)")
        self.main_tabs.addTab(self.mainexe_viewer, "MAIN.EXE")
        self.main_tabs.addTab(self.bins_viewer, "BINs")
        # A whole area at once rather than a file at a time: the room,
        # its background, and everything the area's overlay stands in
        # it - see gui/level/.
        self.level_panel = LevelEditorPanel()
        self.main_tabs.addTab(self.level_panel, "Level Editor")
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
        self.sfx_panel = SfxPanel()
        self.main_tabs.addTab(self.sfx_panel, "SFX")
        # The three STR movies. Beside Music and SFX rather than in the
        # file tree because they are not in TOMBA2.DAT at all - they sit
        # in the disc's own MOVIE folder, which the tree never sees.
        self.movie_panel = MoviePanel()
        self.main_tabs.addTab(self.movie_panel, "Movies")

        # Translation: the font page, whole, and everything selected
        # from it. One view rather than two - the page IS the index, so
        # a separate list of glyphs beside it would only be a second way
        # of saying where to look.
        self.font_page_view = FontPageView()
        # A saved glyph changes chunk 0's VRAM, so anything already
        # showing VRAM is looking at a stale copy of it.
        # Whether the extracted TOMBA2.IMG has been written to since the
        # disc was opened - by a font/title page save, or by a texture
        # migration. Exports carry every other file over from the
        # ORIGINAL image, so without this the edit reaches the copy on
        # disc and never reaches the disc built from it.
        #
        # A migration MUST set this. It rewrites TOMBA2.IDX as well, and
        # the repacker carries those new img_start/img_end straight
        # through (formats/archive/repacker.write_new_idx) - so shipping the
        # new IDX beside the original IMG points every chunk at the
        # wrong offset and the whole disc's artwork comes out jumbled.
        # Which disc is open - see apply_build_table.
        self.build = ""
        self.img_dirty = False
        self.font_page_view.saved.connect(self._font_page_saved)

        # address -> the addresses of byte-identical copies of it, built
        # by idx_parser.parse_idx_file. Empty until a disc is open.
        self.txtd_twins = {}
        # Whether an edit is applied to those copies as well. The
        # viewer that owns the checkbox is built further down, so the
        # connection is made where it is created.
        self.twin_edits = True
        # Addresses an export has actually written, twins included. Kept
        # because turning twin-following off has to put a twin's row back
        # to what is true of it, and "was written by an earlier export"
        # is not the same as "never touched" - see _set_twin_edits.
        self.exported_addresses = set()
        self.translation_tab = self.font_page_view
        self.main_tabs.addTab(self.translation_tab, "Translation")
        # Loaded when the tab is first looked at rather than when a disc
        # opens: working out which codes the disc's own text uses means
        # reading every text file, which takes about a minute, and most
        # sessions never go near this tab.
        self._translation_loaded = False
        self.main_tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.main_tabs)

        # A tab whose player is actually making sound gets a note added
        # to its name - the audio equivalent of the "*" the other tabs
        # use for unsaved edits, since walking away from the Dialogues
        # tab with a channel still playing is otherwise easy to forget.
        self._playback_tabs = (("Dialogues", self.voice_panel),
                               ("Music", self.music_panel),
                               ("SFX", self.sfx_panel),
                               ("Movies", self.movie_panel))
        for _label, panel in self._playback_tabs:
            _panel_player(panel).playbackStateChanged.connect(
                self._refresh_playback_status)
        self._refresh_playback_status()

        # (chunk_index, file_index) -> {"kind", "id", "dat_start", "offset",
        # "data"} for every TXTD/TXT2 file that's been edited but not yet
        # exported. Both file types share this one dict - the "kind" tag
        # ("txtd" or "txt2") is only used by _pack_pending_txtd_edits() to
        # decide whether to call txtd_packer.pack_txtd() or
        # txt2_packer.pack_txt2() for that entry; everything else here
        # (coloring, export bookkeeping) treats both kinds identically.
        self.pending_txtd_edits = {}

        # (chunk_index, file_index) -> {"data", "label", "size"} for a
        # whole SDAT entry whose bytes are being replaced outright -
        # imported from a file, or copied from another entry to swap one
        # model for another. Kept apart from pending_txtd_edits because
        # nothing packs these: what is staged IS what gets written, and
        # formats/archive/repacker.py resizes the DAT and rewrites every
        # pointer around it (see _apply_single_replacement).
        self.pending_file_edits = {}
        # So every view that loads an SMST sees a staged paste
        # rather than the disc's version - see the method.
        smst_parser.set_pending_source(self._pending_smst_blob)

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
        # The disc's MAIN.EXE, once one has been found beside it. The
        # Level Editor reads the object handlers out of it - see
        # game/handler_models.py.
        self.mainexe_path = None
        self.iso_handler = None
        # Path to the disc image currently open - kept around so Export ISO
        # has a full original disc to rebuild from (extracted files alone
        # aren't enough, since everything besides DAT/IDX/IMG needs to be
        # carried over from the source image too).
        self.current_iso_path = None
        # What the disc behind this session IS, not just where it was -
        # see disc/source_disc. A project stores this so the track
        # can be found again after it moves, and everything that needs
        # the real image (building a disc, the spoken dialogue, an
        # area's overlay) goes through require_source_disc() rather than
        # reading current_iso_path and giving up when it is None.
        self.source_disc = None
        # Set once a silent search for the disc has come up empty, so
        # playback stops re-scanning folders it has already looked in.
        self._disc_search_failed = False
        self._project_snapshot_path = None
        # The .t2p this session was opened from or last saved to, and
        # the directory it is unpacked into while it is open.
        self._project_file_path = None
        self._project_work = None
        # TOMBA2.SND - the music and the sound effects - held the way
        # MAIN.EXE and SOP.BIN are, so a sequence or a swapped sound can
        # be edited and saved with no disc attached.
        self.snd_edits = snd_edit.SndEdits()
        # The Music tab edits sequences straight into that store, so an
        # imported MIDI is kept by a project save like any other edit.
        self.music_panel.snd_edits = self.snd_edits
        self.sfx_panel.snd_edits = self.snd_edits
        # An edited sequence or a swapped sound is a pending edit like
        # any other, so the status line and the tab markers have to
        # hear about it.
        self.music_panel.edits_changed.connect(self._refresh_edit_status)
        self.sfx_panel.edits_changed.connect(self._refresh_edit_status)
        self._sweep_stale_work_dirs()

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
        # One store for both a per-channel import (Dialogues) and a
        # per-line one (TXTD), so either can trigger the same Export
        # patched BIN and neither loses the other's staged sectors.
        self.voice_edits = VoiceEditStore()
        self.voice_panel.set_edit_store(self.voice_edits)
        self.txtd_viewer.set_edit_store(self.voice_edits)
        self.txtd_viewer.content_changed.connect(self.on_txtd_content_changed)
        self.txtd_viewer.twin_edits_toggled.connect(self._set_twin_edits)
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

        open_folder_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open Project Folder / Game Folder...", self)
        open_folder_action.setToolTip(
            "Resume a project saved as a folder, or open an already-extracted "
            "game-files folder directly. 'Save ISO' is unavailable for folders."
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

        open_project_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
            "Open Project...", self)
        open_project_action.setShortcut("Ctrl+O")
        open_project_action.setToolTip(
            "Open a .t2p project - one file holding every edit made here: "
            "text, font page, swapped models and textures, MAIN.EXE")
        open_project_action.triggered.connect(lambda: self.open_project_file())

        save_project_file_action = QAction("Save Project", self)
        save_project_file_action.setShortcut("Ctrl+S")
        save_project_file_action.setToolTip(
            "Save every edit back to the project it came from")
        save_project_file_action.triggered.connect(self.save_project)

        save_project_as_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
            "Save Project As...", self)
        save_project_as_action.setShortcut("Ctrl+Shift+S")
        save_project_as_action.setToolTip(
            "Save everything - text, font page, swapped models and textures, "
            "MAIN.EXE - as a single .t2p file, small enough to send")
        save_project_as_action.triggered.connect(
            lambda: self.save_project_file())

        save_project_action = QAction("Save as Project Folder...", self)
        save_project_action.setToolTip(
            "Create a persistent working folder containing the current "
            "text, font page, MAIN.EXE and character assignments")
        save_project_action.triggered.connect(
            lambda: self.save_translation_project())

        attach_disc_action = QAction("Attach Disc Image...", self)
        attach_disc_action.setToolTip(
            "Point the editor at the disc this project was made from. "
            "Needed for the spoken dialogue, the CD music and Build Disc, "
            "none of which survive being extracted to loose files. Open the "
            ".cue if there is one - it names both tracks")
        attach_disc_action.triggered.connect(self.attach_disc_dialog)
        self.attach_disc_action = attach_disc_action

        export_bin_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon), "Build Disc (BIN/CUE)...", self)
        export_bin_action.setToolTip(
            "Write the edits into a copy of the disc's data track and put a "
            "cue sheet and the audio track beside it. Only the edited files' "
            "sectors change, so the XA music and voice survive - this is the "
            "one that stays playable")
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

        open_iso_action = QAction("Open ISO/IMG...", self)
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
        # Opening comes first and the project comes before the disc,
        # because a project is what you have open nearly all the time
        # and the disc only at the start and the end. The two on the
        # right are the two things worth producing.
        toolbar.addAction(open_action)
        toolbar.addAction(open_project_action)

        toolbar.addSeparator()
        toolbar.addAction(export_action)
        toolbar.addAction(export_bin_action)
        toolbar.addAction(save_project_as_action)
        # Toolbar buttons get their own shorter captions - the menu has
        # room for "Save Project As..." and a row of icons does not.
        for action, caption in ((open_project_action, "Open Project"),
                                (open_action, "Open BIN"),
                                (export_action, "Export raw binary"),
                                (export_bin_action, "Build Disc"),
                                (save_project_as_action, "Export Project")):
            action.setIconText(caption)
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

        # Opening, then the disc, then the two things worth producing.
        # The rest are ways of writing out one piece of a disc and only
        # make sense once you know why you want just that piece, so they
        # sit under Advanced rather than beside the two that most people
        # need - and "Save ISO" in particular is easy to reach for and
        # quietly drops the soundtrack.
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(open_project_action)
        file_menu.addSeparator()
        file_menu.addAction(open_action)
        file_menu.addAction(open_iso_action)
        file_menu.addAction(open_folder_action)
        file_menu.addSeparator()
        file_menu.addAction(attach_disc_action)
        file_menu.addSeparator()
        file_menu.addAction(save_project_file_action)
        file_menu.addAction(save_project_as_action)
        file_menu.addAction(export_bin_action)
        file_menu.addSeparator()
        file_menu.addAction(import_labels_action)
        file_menu.addAction(export_labels_action)
        file_menu.addAction(builtin_labels_action)
        file_menu.addSeparator()
        advanced_menu = file_menu.addMenu("Advanced")
        advanced_menu.addAction(save_project_action)
        advanced_menu.addSeparator()
        advanced_menu.addAction(export_files_action)
        advanced_menu.addAction(export_action)
        advanced_menu.addAction(export_iso_action)

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

        # The script leaves as one file to be translated elsewhere and
        # comes back as one file - see import_text() and
        # formats/text/translation_io.
        text_menu = self.menuBar().addMenu("T&ranslation")
        export_text_menu = text_menu.addMenu("Export Text")
        for label, tip, slot in (
                ("Selected Entry...",
                 "The one entry picked in the text file on screen",
                 self.export_text_entry),
                ("Current File...",
                 "Every entry in the text file on screen",
                 self.export_text_file),
                ("Whole Script...",
                 "Every TXTD, TXT1 and TXT2 on the disc, plus MAIN.EXE "
                 "and SOP.BIN",
                 self.export_text_all)):
            action = QAction(label, self)
            action.setToolTip(tip)
            action.triggered.connect(slot)
            export_text_menu.addAction(action)
        import_text_action = QAction("Import Translated Text...", self)
        import_text_action.setToolTip(
            "Read a translated export back in as pending edits")
        import_text_action.triggered.connect(self.import_text)
        text_menu.addAction(import_text_action)
        text_menu.addSeparator()
        export_letters_action = QAction("Export Letter Assignments...", self)
        export_letters_action.setToolTip(
            "Which cell each new letter was given, as a file you can keep "
            "or carry to another disc")
        export_letters_action.triggered.connect(self.export_letters)
        text_menu.addAction(export_letters_action)
        import_letters_action = QAction("Import Letter Assignments...", self)
        import_letters_action.setToolTip(
            "Take a saved set of letter-to-cell assignments")
        import_letters_action.triggered.connect(self.import_letters)
        text_menu.addAction(import_letters_action)

        settings_menu = self.menuBar().addMenu("&Settings")
        theme_menu = settings_menu.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self._theme_settings = QSettings("Tomba2Edit", "Tomba2Edit")
        current_theme = self._theme_settings.value("theme", theme.DEFAULT_THEME)
        if current_theme not in theme.THEMES:
            current_theme = theme.DEFAULT_THEME  # stale value from an older theme key set
        for theme_name in theme.THEMES:
            action = QAction(theme.LABELS[theme_name], self, checkable=True)
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

    def _load_mainexe(self, exe_path):
        # Kept for the Level Editor: the code that decides which model
        # each placed object draws with is in here (see
        # game/handler_models.py), not in any file on the disc.
        self.mainexe_path = exe_path
        # Which build this is decides where every routine and variable the
        # level code knows by address has moved to - game/game_build.py.
        build = game_build.use(exe_path)
        print(f"Code build: {build.label}")
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
                os.path.dirname(self.dat_file) if self.dat_file else None,
                self.preview_glyph_top())
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
            os.path.dirname(self.dat_file) if self.dat_file else None,
            self.preview_glyph_top())
        self.bins_viewer.load_overlays(
            overlays, sop_path,
            self.labels.bins if self.labels else None)

    def _area_names(self):
        """{chunk index: the name its folder carries in the tree}. The
        AREA folders are already named after the level inside them (see
        idx_parser.apply_labels), so the Level Editor's area list says
        the same thing the tree does, minus the file count."""
        model = self.tree_view.model()
        names = {}
        if model is None:
            return names
        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            text = root.child(row).text()
            index = area_index_of(root.child(row))
            if index is None:
                continue
            names[index] = re.sub(r"\s*\(\d+\)$", "", text)
        return names

    def _load_level_editor(self):
        """Point the Level Editor at whatever disc has just opened."""
        if not self.dat_file:
            return
        cd_folder = os.path.dirname(self.dat_file)
        self.level_panel.set_disc(
            self.dat_file, os.path.join(cd_folder, "TOMBA2.IDX"),
            self.overlay_for_area,
            # The character models' texture pages are only ever in
            # AREA_01's chunk, so a level's VRAM needs it merged in for
            # the people standing in the level to be textured at all.
            lambda chunk: self._load_area_vram_bytes(chunk, merge_common=True),
            self._area_names(), self.mainexe_path)

    def closeEvent(self, event):
        if not self._confirm_project_before_close(event):
            event.ignore()
            return
        self.voice_panel.reset_disc()
        if self.iso_handler:
            self.iso_handler.cleanup()
        # An open .t2p is unpacked into a temp directory; it has been
        # packed back up by any save that happened, so what is left is
        # scratch nobody will look for again. It often cannot go yet:
        # the DAT, IDX and IMG are still open (idx_parser holds them for
        # the life of the window) and Windows will not delete an open
        # file. Whatever is left behind is swept on the next start
        # instead - see _sweep_stale_work_dirs.
        if self._project_work:
            shutil.rmtree(self._project_work, ignore_errors=True)
            self._project_work = None
        # The Movies tab has a decoder thread of its own, and Qt takes
        # the process down noisily if it is still running.
        self.movie_panel.close()
        super().closeEvent(event)

    def _apply_and_save_theme(self, theme_name):
        theme.apply_theme(QApplication.instance(), theme_name)
        self._theme_settings.setValue("theme", theme_name)

    def setup_widgets(self):
        self.txtd_viewer = TXTDViewer()
        self.txt2_viewer = TXT2Viewer()
        self.mdat_viewer = MDATViewer()
        # A DRWA isn't a file of its own - it's the head of an MDAT
        # entry, and the pointers in it are what reach that entry's
        # geometry (see formats/drawmaps/drwa_parser.py). So it hangs off the
        # MDAT rows as a second tab rather than getting a tree row.
        self.drwa_viewer = DRWAViewer()
        # The 3D view sits inside a panel listing the drawmap's entries
        # and their polygons, so a face on screen can be traced back to
        # the record it was read from - see formats/geometry/mdat_panel.py.
        self.mdat_panel = MDATPanel(self.mdat_viewer)
        self.mdat_tabs = QTabWidget()
        self.mdat_tabs.addTab(self.mdat_panel, "3D View")
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
        self.img_viewer = IMGViewer()

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
            "IMG": self.img_viewer,
            "DEFAULT": QLabel("File Viewer"),
        }
        for widget in self.widgets.values():
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter) if isinstance(widget, QLabel) else None
            self.widgets_area.addWidget(widget)

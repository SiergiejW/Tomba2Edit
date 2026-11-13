"""The Level Editor tab: pick an area, see what stands in it, move it.

The list beside the view is one row per instance - the room, then every
object the area's overlay places, then the parts of the asset pack that
are already standing where they belong. Selecting a row outlines it in
the view and fills the boxes below, and picking something in the view
selects its row; they are two ways at the same list.

An object whose model is not known is shown as a marker (see
gui/level/level_scene.py). What everything is drawn with comes off the
disc - the objects' own code, run - and a model picked by hand here can
be kept in labels/placements.json.
"""
import json
import os
import re
import time
from math import gcd

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
    QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from formats.animation import clut_anim
from game import placement as placement_module
from formats.background import bgmp_render
from formats.background.bgmp_parser import PALETTE_STRIDE, load_bgmp
from formats.animation.clut_animation import TICK_HZ
from gui.level import level_scene
from gui.level.level_scene import (
    BOTH, EVENTS_DONE, FRESH, LevelScene,
    area_files, instance_color, instance_key, room_entries)
from gui.widgets.dot_delegate import DOT_COLOR
from gui.level import row_delegate
from gui.level.row_delegate import ROW_HEIGHT, RowDelegate
from gui.level import pickup_sprites
from gui.level.level_viewer import LevelViewer
from gui.widgets.panel_title import make_panel_title
from gui.level.placement_palette import PlacementPalette

COLUMNS = ["Instance", "Model", "X", "Y", "Z", "Angle"]
# How many of an interior's rows the Rooms box names.
ROOM_NAMES = 3


def room_row_name(instance):
    """A short name for what a room row is: a chest's contents, a model's
    given name, else its handler's decomp name made readable."""
    if instance.pickup is not None:
        return instance.label.replace("⧖ ", "")
    label = instance.label.replace("⧖ ", "").replace(" (draws nothing)", "")
    label = re.sub(r"interior \d+: ", "", label)
    handler = re.search(r"\bf_(?:Update|Handle)?([A-Za-z0-9]+?)(?:Actor)?\b", label)
    if handler:
        return re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", handler.group(1)).lower()
    model = re.match(r"(.+?) \((.+)\)$", label)
    if model and not model.group(1).startswith(("id ", "trail ")):
        return model.group(1)           # a model somebody named
    if model:
        inner = re.search(r"(0x[0-9A-F]{8})", model.group(2))
        return f"{model.group(1)}" + (f" {inner.group(1)}" if inner else "")
    return label


ROLE = Qt.ItemDataRole.UserRole

# A chest nests under the moving thing it stands in or on: the thing is no
# bigger than this across (so a room never is one), and the chest may sit
# this far above or below its box.
NEST_MAX_SIZE = 4000.0
NEST_SLACK = 150.0

# A part of an instance gets a row of its own under it: a door in the
# interior it is built into. Its key is negative, so it never collides
# with an instance index.
PART_STRIDE = 4096


class PartRow:
    """One source of a multi-part instance, listed beneath it."""
    flip = None
    role = "part"

    def __init__(self, instance, part, label):
        self.instance, self.part, self.label = instance, part, label
        self.index = -(1 + instance.index * PART_STRIDE + part)


def part_key(instance_index, part):
    return -(1 + instance_index * PART_STRIDE + part)


def part_of_key(key):
    """(instance index, part number) of a part row's key."""
    n = -key - 1
    return n // PART_STRIDE, n % PART_STRIDE

# How many frames of a moving background to render at most. Each one is
# the whole map cut tile by tile, so this is a bound on how long opening
# an area can take rather than anything the data asks for.
MAX_PHASES = 24


class LevelEditorPanel(QWidget):
    """An area, whole."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dat_path = None
        self.idx_path = None
        self.chunk = None
        self.overlay_for_area = lambda _chunk: None
        self.vram_for_area = lambda _chunk: None
        # MAIN.EXE, where the routines that decide what each object is
        # drawn with live - see game/handler_models.py.
        self.exe_path = None
        self.scene = None
        self._filling = False
        self._background_cache = {}
        self._sprite_cache = {}
        self._scene_memory_cache = {}
        self._placement_dirty = False
        self._placement_files = {}

        # Pre-rendered phases of the background, flipped by a timer -
        # the same trick the BGMP viewer uses, since re-rendering a
        # 1000x300 map of 16x16 tiles every frame is not free.
        self._phases = []
        self._phase = 0
        self._phase_timer = QTimer(self)
        self._phase_timer.timeout.connect(self._next_phase)

        # The pickups animate on their own clock: their steps are in the
        # game's own ticks, and unlike the background's phases every one
        # of them is already on the card.
        self._sprite_timer = QTimer(self)
        self._sprite_timer.timeout.connect(self._next_sprite_tick)

        self.viewer = LevelViewer(self)
        self.viewer.animate_action.setToolTip(
            "Animate textures, backgrounds, sprites, actor effects, and idle poses")
        self.viewer.animate_action.toggled.connect(self._sync_motion_animation)
        self.viewer.selection_changed.connect(self._on_view_selection)
        self.viewer.instance_moved.connect(self._on_instance_moved)
        self.viewer.part_changed.connect(self._on_part_selected)
        self.viewer.pickup_dropped.connect(self._add_pickup)
        self.viewer.mode_changed.connect(self._on_mode_changed)

        self.area_box = QComboBox(self)
        self.area_box.setMinimumWidth(340)
        self.area_box.setMinimumContentsLength(30)
        self.area_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.area_box.currentIndexChanged.connect(self._on_area_changed)

        # Area or one of its rooms. Inside, the game never draws the
        # area's own mesh - a room is only the actors its scene table
        # spawns (game/actor_sim.py) - so a room is shown alone.
        self._filling_views = False
        self.view_box = QComboBox(self)
        self.view_box.setMinimumWidth(300)
        self.view_box.setMinimumContentsLength(26)
        self.view_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.view_box.setToolTip(
            "Which part of the area to show. The area is the level Tomba "
            "walks around; a room is what the game draws once he walks "
            "through a door - the actors its scene table spawns, and the "
            "chests that only appear in there.")
        self.view_box.currentIndexChanged.connect(self._apply_view)
        self.view_box.activated.connect(self._on_view_chosen)

        # Which game the actors run in - see LevelScene.load.
        self.progress_box = QComboBox(self)
        self.progress_box.setMinimumWidth(170)
        self.progress_box.setMinimumContentsLength(14)
        self.progress_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for label, value in (("Both", BOTH), ("Fresh game", FRESH),
                             ("Events done", EVENTS_DONE)):
            self.progress_box.addItem(label, value)
        self.progress_box.setToolTip(
            "What the area's actors are run from.\n\n"
            "Fresh game: New Game's save, with the intro played.\n"
            "Events done: every save byte the area's code reads set to 0xFF, "
            "the way the game marks a finished event.\n"
            "Both: the fresh game, plus whatever only stands once events are "
            "done, marked ⧖. Runs the area twice.")
        self.progress_box.currentIndexChanged.connect(self._on_progress_changed)

        self.summary = QLabel("Open a disc to pick an area.", self)
        self.summary.setWordWrap(True)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        # The name takes whatever width the numbers leave, so the list always
        # fills its pane.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self._collapsed, self._view_hidden = set(), set()
        self._parents, self._children, self._depth = {}, {}, {}
        from PyQt6.QtGui import QKeySequence, QShortcut
        for key, fold in (("Left", True), ("Right", False)):
            fold_key = QShortcut(QKeySequence(key), self.table)
            fold_key.setContext(Qt.ShortcutContext.WidgetShortcut)
            fold_key.activated.connect(lambda f=fold: self._step_fold(f))
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setItemDelegateForColumn(0, RowDelegate(
            self.table, on_fold=lambda index: self._toggle_fold(index.data(ROLE))))
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setMouseTracking(True)

        self.details = QLabel("Click something in the view, or pick a row.", self)
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(72)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        self.model_box = QComboBox(self)
        self.model_box.setToolTip(
            "Which part of the area's models this object is drawn with.\n\n"
            "Read out of the code that draws it, and yours to change. A "
            "class that attaches several models over its life is left for "
            "you to pick from.")
        self.model_box.currentIndexChanged.connect(self._on_model_changed)

        self.boxes = {}
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Model", self.model_box)
        for name, limit, step in (("X", 32767, 16.0), ("Y", 32767, 16.0),
                                  ("Z", 32767, 16.0), ("Angle", 360, 5.0)):
            box = QDoubleSpinBox(self)
            box.setRange(-limit, limit)
            box.setDecimals(0)
            box.setSingleStep(step)
            box.valueChanged.connect(self._on_box_changed)
            self.boxes[name] = box
            form.addRow(name, box)

        edit = QGroupBox("Selected", self)
        edit_layout = QVBoxLayout(edit)
        edit_layout.setContentsMargins(6, 6, 6, 6)
        edit_layout.addWidget(self.details)
        edit_layout.addLayout(form)
        self.events_button = QPushButton('Events / actions…', self)
        self.events_button.setEnabled(False)
        self.events_button.clicked.connect(self._open_events)
        edit_layout.addWidget(self.events_button)
        # Scripts that belong to the area rather than to anything placed:
        # the conversations area code starts (f_HandleAreaScriptActor).
        self.area_scripts_button = QPushButton('Area scripts…', self)
        self.area_scripts_button.setEnabled(False)
        self.area_scripts_button.setToolTip('Event pages the area itself runs - conversations and cutscenes its own '
                                            'code starts, on the area script actor, which is not placed in the level.')
        self.area_scripts_button.clicked.connect(self._open_area_scripts)
        edit_layout.addWidget(self.area_scripts_button)

        self.pickup_options = QGroupBox("Pickup", self)
        pickup_form = QFormLayout(self.pickup_options)
        self.pickup_kind = QComboBox(self)
        self.pickup_reward = QComboBox(self)
        self.pickup_category = QComboBox(self)
        self.pickup_category.setEnabled(False)
        self.pickup_kind.currentIndexChanged.connect(self._on_pickup_option)
        self.pickup_reward.currentIndexChanged.connect(self._on_pickup_option)
        pickup_form.addRow("Type", self.pickup_kind)
        pickup_form.addRow("Contents / item", self.pickup_reward)
        pickup_form.addRow("Item category", self.pickup_category)
        edit_layout.addWidget(self.pickup_options)
        self.pickup_options.hide()

        self.keep_button = QPushButton("Keep models", self)
        self.keep_button.setToolTip(
            "Write the models picked by hand into labels/placements.json, "
            "so this area opens with them next time.")
        self.keep_button.clicked.connect(self._keep_models)
        self.name_button = QPushButton("Name model...", self)
        self.name_button.setToolTip(
            "Call the selected object's model something.\n\n"
            "A whole character is named under its file id, so every "
            "object drawn from it reads the same; a single part of an "
            "asset pack is named under its own group. Kept in "
            "labels/placements.json beside the bindings.")
        self.name_button.clicked.connect(self._name_model)
        self.gif_button = QPushButton("Save GIF...", self)
        self.gif_button.setToolTip(
            "Record the selected row - an effect, a sprite, anything with an "
            "animated texture - alone and framed, as an animated GIF over one "
            "loop of it. With nothing selected, the whole view.")
        self.gif_button.clicked.connect(lambda: self.viewer.save_gif())
        from gui.level.level_undo import LevelUndo
        from PyQt6.QtGui import QKeySequence, QShortcut
        self.undo = LevelUndo(self)
        self.undo_button = QPushButton("Undo", self)
        self.undo_button.setToolTip("Undo the last level edit (Ctrl+Z).")
        self.undo_button.clicked.connect(self.undo.undo)
        self.redo_button = QPushButton("Redo", self)
        self.redo_button.setToolTip("Redo (Ctrl+Y or Ctrl+Shift+Z).")
        self.redo_button.clicked.connect(self.undo.redo)
        for keys, slot in (("Ctrl+Z", self.undo.undo), ("Ctrl+Y", self.undo.redo),
                           ("Ctrl+Shift+Z", self.undo.redo)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self.undo.changed = self._undo_changed
        self._undo_changed()
        self.viewer.edit_starting.connect(lambda: self.undo.remember())
        self.viewer.edit_cancelled.connect(self.undo.forget_last)
        self.apply_button = QPushButton("Apply changes", self)
        self.apply_button.setToolTip("Apply level changes to the working game files for project/disc saving.")
        self.apply_button.clicked.connect(lambda: self.apply_changes(announce=True))
        self.save_button = QPushButton("Export overlay...", self)
        self.save_button.setToolTip(
            "Write a copy of this area's Axx.BIN with the positions and "
            "pickup options as they are here. Apply changes first when adding records.")
        self.save_button.clicked.connect(self._save_overlay)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.redo_button)
        buttons.addWidget(self.keep_button)
        buttons.addWidget(self.name_button)
        buttons.addWidget(self.gif_button)
        buttons.addWidget(self.save_button)
        edit_layout.addWidget(self.apply_button)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        # Area on the left, Progress in the middle, Rooms on the right.
        top.addWidget(QLabel("Area", self))
        top.addWidget(self.area_box, 3)
        top.addStretch(1)
        top.addWidget(QLabel("Progress", self))
        top.addWidget(self.progress_box, 1)
        top.addStretch(1)
        top.addWidget(QLabel("Rooms", self))
        top.addWidget(self.view_box, 3)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.summary)
        left_layout.addWidget(self.table, 1)
        left_layout.addWidget(edit)
        left_layout.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        self.palette = PlacementPalette(self)
        self.palette.setEnabled(False)
        palette_panel = QWidget(self)
        palette_layout = QVBoxLayout(palette_panel)
        palette_layout.setContentsMargins(6, 3, 6, 0)
        self.palette_label = QLabel("Add objects — drag into the level", self)
        palette_layout.addWidget(self.palette_label)
        palette_layout.addWidget(self.palette)
        viewport_splitter = QSplitter(Qt.Orientation.Vertical, self)
        viewport_splitter.addWidget(self.viewer)
        viewport_splitter.addWidget(palette_panel)
        viewport_splitter.setStretchFactor(0, 1)
        viewport_splitter.setStretchFactor(1, 0)
        viewport_splitter.setSizes([700, 150])
        splitter.addWidget(viewport_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 900])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(make_panel_title(
            "A whole area and its rooms: the level, its background, and "
            "everything the game stands in it - click an object to select "
            "it, click again for the part under the cursor"))
        # Across the top of both: what is open belongs to the whole tab, not
        # to the list on the left, and the view gets the width back.
        top.setContentsMargins(6, 4, 6, 4)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        self._enable(False)

    # --- the disc -----------------------------------------------------

    def set_disc(self, dat_path, idx_path, overlay_for_area, vram_for_area,
                 area_names=None, exe_path=None):
        """Point the tab at an open disc. `overlay_for_area` and
        `vram_for_area` are MainWindow's - the Level Editor has no
        business working out where an Axx.BIN lives or how to decompress
        a VRAM chunk when the window already knows both."""
        self._cancel_load()
        self.scene = None
        self.dat_path = dat_path
        self.idx_path = idx_path
        self.overlay_for_area = overlay_for_area
        self.vram_for_area = vram_for_area
        self.exe_path = exe_path
        self._background_cache.clear()
        self._sprite_cache.clear()
        self._scene_memory_cache.clear()
        self._placement_dirty = False
        self._placement_files.clear()
        self._saved_files = {}
        self._repair_legacy_pickups()
        self._filling = True
        self.area_box.clear()
        rooms = self._areas_with_rooms()
        for chunk in rooms:
            name = (area_names or {}).get(chunk)
            self.area_box.addItem(name or f"AREA_{chunk:02X}", chunk)
        self._filling = False
        self._enable(bool(rooms))
        if rooms:
            self.area_box.setCurrentIndex(0)
            self._on_area_changed(0)
        else:
            self.summary.setText("This disc holds no areas with a level in them.")

    def _areas_with_rooms(self):
        """Which chunks are worth opening.

        A room MDAT is the usual reason, but not the only one: the Water
        Temple's chunk has no room in it at all and still holds a
        140K asset pack and a table saying where its contents stand, so
        an asset pack counts too. What is left out is the menus, the
        cutscenes and the empty slots, which have neither."""
        out = []
        if not self.idx_path or not os.path.exists(self.idx_path):
            return out
        for chunk in range(os.path.getsize(self.idx_path) // 0x800):
            try:
                _start, files = area_files(self.idx_path, chunk)
            except (OSError, ValueError):
                continue
            pack = any(file_id == level_scene.ASSET_PACK_ID and size > 0
                       for _i, file_id, _o, size in files)
            if pack or room_entries(self.idx_path, self.dat_path, chunk):
                out.append(chunk)
        return out

    def _enable(self, on):
        for widget in (self.table, self.model_box,
                       self.keep_button, self.save_button,
                       *self.boxes.values()):
            widget.setEnabled(on)
        self.progress_box.setEnabled(on and not self.viewer.edit_mode)
        self.palette.setEnabled(on and self.viewer.edit_mode
                                and not getattr(self, "_loading", False))
        self.apply_button.setEnabled(on)

    def _on_mode_changed(self, edit):
        """Preview <-> Edit: drop whatever the other mode was loading and
        reload this area its way, same camera, same room."""
        self.progress_box.setEnabled(not edit and self.scene is not None)
        self.palette_label.setText(
            "Add objects — drag into the level" if edit
            else "Add objects — switch to Edit mode to place them")
        self.palette.setEnabled(edit and self.scene is not None
                                and not getattr(self, "_loading", False))
        if self.chunk is not None and self.dat_path:
            self.load_area(self.chunk, keep_camera=True,
                           keep_view=self.view_box.currentData())

    # --- loading an area ----------------------------------------------

    def _on_area_changed(self, _index):
        if self._filling:
            return
        chunk = self.area_box.currentData()
        if chunk is None or not self.dat_path:
            return
        self.load_area(chunk)

    def _on_progress_changed(self, _index):
        # The camera and the Rooms choice stay put: the same shot of the same
        # place under the other progress is what makes the two comparable.
        if not self._filling and self.chunk is not None and self.dat_path:
            self.load_area(self.chunk, keep_camera=True,
                           keep_view=self.view_box.currentData())

    def _cancel_load(self):
        self._load_generation = getattr(self, "_load_generation", 0) + 1
        for loader in getattr(self, "_loaders", ()):
            loader.cancel()

    def load_area(self, chunk, keep_camera=False, keep_view="all"):
        """Start a cancellable background load; the GUI remains navigable."""
        if not self.idx_path or not os.path.exists(self.idx_path) or not self.dat_path or not os.path.exists(self.dat_path):
            # No disc (or its files are gone): say so, instead of a worker traceback.
            self.summary.setText("No disc is open for the Level Editor (TOMBA2.IDX / TOMBA2.DAT not found). "
                                 "Open the disc or project again, then pick the area.")
            return
        if not self.apply_changes():
            return
        self._close_events()
        self.undo.clear()
        from gui.level.progressive_load import LevelLoad
        from PyQt6.QtWidgets import QApplication
        self._cancel_load()
        self._stop_cycling()
        self._sprite_timer.stop()
        # The first progressive snapshot intentionally contains only rooms
        # and markers.  It cannot yet list an interior's actors, so its Rooms
        # combo may temporarily fall back to Area.  Keep the user's requested
        # room separately until all later snapshots have had a chance to
        # provide it; never promote that temporary fallback into the request.
        self._requested_view = keep_view
        self._keep_view = keep_view
        # The scene on screen, not self.chunk: a switch made before this
        # area's first stage still shows the last area, with its VRAM.
        same = self.scene is not None and self.scene.chunk_index == chunk
        keep_camera = keep_camera and same
        # A Progress switch keeps the scene in hand - and the room it shows -
        # until the other one is complete, rather than starting from markers.
        self._hold_stages = keep_camera
        self.chunk = chunk
        self._loading = True
        self._stage_seen = self._hold_stages
        self._stage_keep_camera = keep_camera
        self._stage_overlay = self.overlay_for_area(chunk)
        self._stage_fast = self.viewer.edit_mode
        self._stage_progress = FRESH if self._stage_fast else self.progress_box.currentData()
        self._stage_started = time.perf_counter()
        generation = self._load_generation
        self.summary.setText(f"AREA_{chunk:02X}: loading room and background…")
        self.viewer.show_status(f"AREA_{chunk:02X}: loading room and background…")
        # Navigation, room filtering and selection remain available. Editing
        # starts once the authoritative result arrives, so a later stage can
        # never overwrite a user's model/position change.
        self.viewer.loading = True
        self.palette.setEnabled(False)
        self.apply_button.setEnabled(False)
        for button in (self.keep_button, self.name_button, self.save_button,
                       self.gif_button):
            button.setEnabled(False)
        for box in self.boxes.values():
            box.setEnabled(False)
        self.model_box.setEnabled(False)
        cached = self._scene_memory_cache.get(
            (chunk, self._stage_progress, self._stage_fast))
        if cached is not None:
            QTimer.singleShot(0, lambda: self._accept_stage(generation, cached))
            return
        loader = LevelLoad((self.dat_path, self.idx_path, chunk,
                            self._stage_overlay, self.exe_path,
                            self._stage_progress, self._stage_fast), self)
        if not hasattr(self, "_loaders"):
            self._loaders = []
        self._loaders.append(loader)
        loader.stage.connect(lambda payload, g=generation: self._accept_stage(g, payload))
        loader.status.connect(lambda text, g=generation: self._load_status(g, text))
        loader.failed.connect(lambda error, g=generation: self._load_failed(g, error))
        loader.finished.connect(lambda job=loader: self._release_loader(job))
        if not getattr(self, "_shutdown_hook", False):
            QApplication.instance().aboutToQuit.connect(self.shutdown_loaders)
            self._shutdown_hook = True
        loader.start()

    def _release_loader(self, loader):
        if loader in self._loaders:
            self._loaders.remove(loader)
        loader.deleteLater()

    def shutdown_loaders(self):
        """Stop the level-load workers and wait for their threads.

        A level load runs in a thread of its own for around twenty
        seconds, and Qt takes the whole process down - abort(), a crash
        report, no clean exit - if a running QThread is destroyed with
        the window. Called from the window's closeEvent and from
        aboutToQuit, whichever comes first."""
        self._cancel_load()
        for loader in list(getattr(self, "_loaders", ())):
            loader.wait()

    def _load_failed(self, generation, error):
        if generation != self._load_generation:
            return
        self._loading = False
        self.viewer.loading = False
        self.summary.setText("Level loading failed. The last completed stage is still "
                             "available for viewing.\n" + error)
        self.viewer.show_status(f"AREA_{self.chunk:02X}: loading failed", done=True)
        print(error, flush=True)

    def _on_view_chosen(self, _row):
        """The user's pick, which later load stages keep."""
        self._requested_view = self._keep_view = self.view_box.currentData()

    def _load_status(self, generation, text):
        if generation == self._load_generation and self._loading:
            self.viewer.show_status(text)

    @staticmethod
    def _stage_key(instance):
        key = instance_key(instance)
        if key is not None:
            return (instance.role, instance.scene, repr(key))
        return (instance.role, instance.scene, instance.label,
                round(instance.x), round(instance.y), round(instance.z))

    def _accept_stage(self, generation, payload):
        if generation != self._load_generation:
            return
        state, vram, phases, sprites, prepared, title, complete = payload
        if self._hold_stages and not complete:
            return
        # Each stage blocks the UI while it is applied: timed, to see which.
        marks = [("start", time.perf_counter())]
        hidden = set()
        selected = None
        if self._stage_seen and self.scene is not None:
            # The room asked for - by load_area, or picked from the combo
            # meanwhile (_on_view_chosen) - never a stage's own fallback.
            self._keep_view = self._requested_view
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                old = self._instance(item.data(ROLE)) if item else None
                if old is not None and item.checkState() != Qt.CheckState.Checked:
                    hidden.add(self._stage_key(old))
            old = self._instance(self.viewer.selected)
            if old is not None:
                selected = self._stage_key(old)
        scene = LevelScene()
        scene.__dict__.update(state)
        self.scene = scene
        self._update_area_scripts()
        # Textured VRAM colours what this stage saw drawn.
        from psx import vram_preview
        vram_preview.publish_level(self.chunk, vram_preview.scene_regions(scene))
        from psx.vram_viewer import vram_index_image
        if not self._stage_seen:
            self.viewer.set_vram(vram, vram_index_image(vram) if vram else None)
            self.viewer.export_name = f"AREA_{self.chunk:02X}"
        self.viewer.load_scene(scene,
            frame=not self._stage_seen and not self._stage_keep_camera,
            prepared=prepared[:4])
        marks.append(("scene", time.perf_counter()))
        self.viewer.load_animations(self._stage_overlay, prepared=prepared[4])
        self._phases, self._phase = phases, 0
        self.viewer.set_background(phases[0][0] if phases else None)
        self.viewer.set_sprites(*sprites)
        marks.append(("animations+sprites", time.perf_counter()))
        self._loading = not complete
        self.viewer.loading = not complete
        self._keep_view = getattr(self, "_requested_view", self._keep_view)
        self._populate()
        self._filling = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            instance = self._instance(item.data(ROLE))
            if instance is None:
                continue
            key = self._stage_key(instance)
            if key in hidden:
                item.setCheckState(Qt.CheckState.Unchecked)
            if key == selected:
                self.table.selectRow(row)
                self.viewer.select(instance.index)
        self._filling = False
        marks.append(("table", time.perf_counter()))
        self._apply_view(frame=False)
        marks.append(("view", time.perf_counter()))
        print(f"stage '{title}' applied in {marks[-1][1] - marks[0][1]:.2f}s: "
              + ", ".join(f"{name} {t - marks[n][1]:.2f}s"
                          for n, (name, t) in enumerate(marks[1:])))
        has_motion = self.viewer.animating or len(phases) > 1
        self.viewer.animate_action.setEnabled(
            self.viewer.animate_action.isEnabled() or has_motion)
        if has_motion and self.viewer.animate_wanted:
            self.viewer.animate_action.setChecked(True)
        self._sync_motion_animation(self.viewer.animate_action.isChecked())
        self._stage_seen = True
        count = sum(i.flip is None for i in scene.instances)
        self.summary.setText(f"AREA_{self.chunk:02X}: {title} — {count} objects. "
                            + (f"Loaded in {time.perf_counter() - self._stage_started:.1f}s."
                               if complete else "You can explore while loading continues."))
        if complete:
            self.palette.setEnabled(self.viewer.edit_mode)
            self.apply_button.setEnabled(True)
            self.viewer.show_status(
                f"AREA_{self.chunk:02X} loaded in "
                f"{time.perf_counter() - self._stage_started:.1f}s", done=True)
            self._remember(self._scene_memory_cache,
                           (self.chunk, self._stage_progress, self._stage_fast),
                           payload, keep=6)
            for button in (self.keep_button, self.name_button, self.save_button,
                           self.gif_button):
                button.setEnabled(True)
        self._show_details(self.viewer.selected)

    def _load_sprites(self, scene, vram):
        """Cut every frame this level's pickups need and hang them.

        A crystal is a sprite out of the bank every area shares, not a
        model, so it cannot come through the scene's geometry - see
        gui/level/pickup_sprites.py."""
        self.viewer.set_sprites(None, ())
        self._sprite_timer.stop()
        if not vram:
            return
        cache_key = (scene.chunk_index, scene.progress, id(vram))
        cached = self._sprite_cache.get(cache_key)
        if cached is not None:
            self.viewer.set_sprites(*cached)
            return
        try:
            # Keep captured particles as deduplicated native cards, using
            # the same compact atlas as the progressive area loader.
            from gui.level.load_worker import sprites
            atlas, quads = sprites(scene, vram)
        except Exception as e:
            scene.notes.append(f"couldn't cut the pickup sprites: {e}")
            return
        if atlas is None or not quads:
            return
        self._remember(self._sprite_cache, cache_key, (atlas, quads))
        self.viewer.set_sprites(atlas, quads)

    def _next_sprite_tick(self):
        self.viewer.advance_sprites()

    def _sync_motion_animation(self, checked):
        """Make Animate the master switch for every kind of level motion."""
        if checked:
            if self.viewer.animating:
                self._sprite_timer.start(max(20, round(1000 / TICK_HZ)))
            if len(self._phases) > 1:
                hold = self._phases[self._phase][1]
                self._phase_timer.start(max(20, hold))
            return
        self._sprite_timer.stop()
        self._phase_timer.stop()
        self.viewer._sprite_tick = 0
        self.viewer._sprite_dirty = True
        if self._phases:
            self._phase = 0
            self.viewer.set_background(self._phases[0][0])
        self.viewer.update()

    def _load_background(self, scene, vram, overlay):
        """Render the area's BGMP, and every frame of it that moves.

        What moves is read out of the area's overlay - the same table
        the room's own animated palettes come from (see
        formats/animation/clut_anim.py) - rather than guessed at from the
        colours. Guessing was wrong in both directions: it had AREA_09's
        sky rippling when the game holds it still, and it found one of
        AREA_04's three moving palettes."""
        self._phases = []
        self._phase = 0
        cache_key = (scene.chunk_index, scene.progress, id(vram), overlay)
        cached = self._background_cache.get(cache_key)
        if cached is not None:
            self._phases = cached
            self.viewer.set_background(
                self._phases[0][0] if self._phases else None)
            if len(self._phases) > 1 and self.viewer.animate_action.isChecked():
                self._phase_timer.start(self._phases[0][1])
            return
        entry = scene.by_id.get(level_scene.BACKGROUND_ID)
        if not entry or not entry[1]:
            from game import sky_gradient
            sky = sky_gradient.image(overlay)
            if sky is not None:
                scene.notes.append(
                    "no background picture: the sky is the gradient "
                    + sky_gradient.DRAWERS[os.path.basename(overlay)[:3].upper()]
                    + " draws")
                self.viewer.set_background(sky)
                return
            scene.notes.append(
                "this area holds no background - some levels are indoors "
                "and simply have none")
            self.viewer.set_background(None)
            return
        try:
            background = load_bgmp(self.dat_path, scene.dat_start,
                                   entry[0], entry[1])
            textures = bgmp_render.BackgroundTextures(vram) if vram else None
            offset = bgmp_render.detect_page_y_offset(background, textures)
            self._phases = self._background_frames(
                background, vram, offset, overlay)
        except Exception as e:
            scene.notes.append(f"the background wouldn't draw: {e}")
            self.viewer.set_background(None)
            return
        # An area that draws a sky gradient at the back of the ordering
        # table shows it through every transparent texel of its picture -
        # the water pig boss's picture is nothing else.
        from game import sky_gradient
        sky = sky_gradient.image(overlay)
        if sky is not None and self._phases:
            self._phases = [(sky_gradient.under(picture, sky), ms)
                            for picture, ms in self._phases]
        self.viewer.set_background(self._phases[0][0] if self._phases else None)
        self._remember(self._background_cache, cache_key, self._phases)
        if len(self._phases) > 1 and self.viewer.animate_action.isChecked():
            self._phase_timer.start(self._phases[0][1])

    @staticmethod
    def _remember(cache, key, value, keep=8):
        """Small in-session LRU-ish cache; arrays and sprite atlases are big."""
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > keep:
            cache.pop(next(iter(cache)))

    def _background_frames(self, background, vram, offset, overlay):
        """[(picture, milliseconds), ...] over one loop of the
        background, which for most areas is one still frame.

        A palette the overlay animates is written into a copy of the
        area's VRAM before the tiles are cut from it, exactly as the
        game writes it into the real thing - so what comes out is the
        disc's own colours rather than a rotation of the ones it starts
        with. Frames that come out identical are held rather than
        repeated, which is what keeps the count down to something worth
        rendering."""
        still = [(np.asarray(bgmp_render.render_background(
            background, bgmp_render.BackgroundTextures(vram) if vram else None,
            offset).convert("RGB"), dtype=np.uint8), 0)]
        if not vram or not overlay:
            return still
        try:
            _base, found = clut_anim.load_animations(overlay)
        except Exception:
            return still
        # Only the palettes this background actually draws through.
        wanted = {background.clut_address + index * PALETTE_STRIDE
                  for index in background.palettes_used}
        moving = [a for a in found if a.address in wanted]
        if not moving:
            return still

        period = 1
        for animation in moving:
            period = period * animation.loop_ticks // gcd(period,
                                                          animation.loop_ticks)
        runs = []
        for tick in range(period):
            state = tuple(a.frame_at(tick) for a in moving)
            if not runs or runs[-1][0] != state:
                if len(runs) >= MAX_PHASES:
                    break
                runs.append([state, 0])
            runs[-1][1] += 1

        frames = []
        for state, ticks in runs:
            patched = bytearray(vram)
            for animation, frame in zip(moving, state):
                at = animation.address
                patched[at:at + len(animation.frames[frame])] = \
                    animation.frames[frame]
            picture = bgmp_render.render_background(
                background, bgmp_render.BackgroundTextures(patched), offset)
            frames.append((np.asarray(picture.convert("RGB"), dtype=np.uint8),
                           max(20, round(ticks * 1000 / TICK_HZ))))
        return frames

    def _stop_cycling(self):
        self._phase_timer.stop()

    def _next_phase(self):
        if len(self._phases) < 2:
            self._phase_timer.stop()
            return
        self._phase = (self._phase + 1) % len(self._phases)
        picture, hold = self._phases[self._phase]
        self.viewer.set_background(picture)
        self._phase_timer.start(hold)

    # --- the list -----------------------------------------------------

    # --- nesting ------------------------------------------------------

    def _parent_map(self, instances):
        """{index: parent index} for what rides, spawns or rests on another
        row: a pickup or sprite its spawner carries (follow), a chest's
        contents (pickup_parent), and a chest standing on a moving thing -
        a platform - judged by where it is. The smallest thing it is
        inside wins, so a chest on a lift nests under the lift rather than
        under the room round both."""
        by_index = {i.index: i for i in instances}
        parents = {}
        for instance in instances:
            link = (instance.follow[0] if instance.follow
                    else instance.pickup_parent)
            if link in by_index and link != instance.index:
                parents[instance.index] = link
        carriers = []
        positions = self.viewer._positions()
        for candidate in instances:
            if (candidate.role == "room" or candidate.assembly is None
                    or not candidate.vertex_count or candidate.index in parents):
                continue
            box = self.viewer._instance_box(candidate, positions)
            if box is None:
                continue
            x0, x1, y0, y1, z0, z1 = box
            if max(x1 - x0, y1 - y0, z1 - z0) > NEST_MAX_SIZE:
                continue
            carriers.append((candidate, box, (x1 - x0) * (y1 - y0) * (z1 - z0)))
        for instance in instances:
            if (instance.index in parents or instance.role == "room"
                    or not (instance.pickup is not None
                            or "chest" in instance.label.lower())):
                continue
            best = None
            for candidate, box, volume in carriers:
                x0, x1, y0, y1, z0, z1 = box
                if (candidate.index == instance.index
                        or candidate.scene != instance.scene
                        or not x0 <= instance.x <= x1
                        or not z0 <= instance.z <= z1
                        or not y0 - NEST_SLACK <= instance.y <= y1 + NEST_SLACK):
                    continue
                if best is None or volume < best[1]:
                    best = (candidate.index, volume)
            if best is not None:
                parents[instance.index] = best[0]
        # Follow links can loop (A follows B follows A): cut each loop open.
        for start in list(parents):
            seen, at = {start}, parents.get(start)
            while at is not None and at in parents:
                if at in seen:
                    del parents[start]
                    break
                seen.add(at)
                at = parents[at]
        return parents

    def _part_rows(self, instance):
        """Rows for the parts an instance is built from - none unless it has
        several, each with its own span of vertices."""
        spans = instance.spans or ()
        if (instance.role == "room" or len(instance.sources) < 2
                or len(spans) != len(instance.sources)):
            return []
        owners = getattr(instance.assembly, "owners", None) or ()
        rows = []
        for part, span in enumerate(spans):
            if not span or part >= PART_STRIDE:
                continue
            who = f"{owners[part][1]}: " if part < len(owners) else ""
            rows.append(PartRow(instance, part,
                                f"part {part} - {who}{self._source_text(instance.sources[part])}"))
        return rows if len(rows) > 1 else []

    def _nest(self, instances):
        """The rows in tree order - each parent followed by what is under it
        - and how deep each one sits."""
        parents = self._parent_map(instances)
        children = {}
        for index, parent in parents.items():
            children.setdefault(parent, []).append(index)
        by_index = {i.index: i for i in instances}
        # An instance's parts come first under it, before what rests on it.
        for instance in instances:
            rows = self._part_rows(instance)
            for row in rows:
                by_index[row.index] = row
                parents[row.index] = instance.index
            if rows:
                children[instance.index] = ([r.index for r in rows]
                                            + children.get(instance.index, []))
        self._parents, self._children = parents, children
        ordered, depth, seen = [], {}, set()

        def visit(instance, level):
            if instance.index in seen:
                return
            seen.add(instance.index)
            ordered.append(instance)
            depth[instance.index] = level
            for child in children.get(instance.index, ()):
                visit(by_index[child], level + 1)

        for instance in instances:
            if instance.index not in parents:
                visit(instance, 0)
        for instance in instances:       # a cycle would leave rows out
            visit(instance, 0)
        return ordered, depth

    def _tree_data(self, instance):
        """What the row delegate draws the tree from."""
        index = instance.index
        parent = self._parents.get(index)
        siblings = self._children.get(parent, ())
        # Per ancestor below the top: does its line go on past this row.
        goes_on, above, seen = [], parent, {index}
        while above is not None and above not in seen and self._parents.get(above) is not None:
            seen.add(above)
            up = self._parents[above]
            goes_on.append(self._children[up][-1] != above)
            above = up
        return {row_delegate.DEPTH: self._depth.get(index, 0),
                row_delegate.HAS_CHILDREN: bool(self._children.get(index)),
                row_delegate.FOLDED: index in self._collapsed,
                row_delegate.LAST: bool(siblings) and siblings[-1] == index,
                row_delegate.CONTINUES: tuple(reversed(goes_on))}

    def _set_tree_data(self, item, instance):
        for role, value in self._tree_data(instance).items():
            item.setData(role, value)

    def _folded_away(self, index):
        """Whether an ancestor of this row is folded."""
        seen = set()
        parent = self._parents.get(index)
        while parent is not None and parent not in seen:
            if parent in self._collapsed:
                return True
            seen.add(parent)
            parent = self._parents.get(parent)
        return False

    def _apply_folds(self):
        """Hide the rows the chosen view or a folded parent takes out."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            index = item.data(ROLE)
            instance = self._instance(index)
            self.table.setRowHidden(row, index in self._view_hidden
                                    or (index < 0 and self._parents.get(index) in self._view_hidden)
                                    or self._folded_away(index)
                                    or (instance is not None and instance.flip is not None))

    def _set_folded(self, index, folded):
        if not self._children.get(index) or (index in self._collapsed) == folded:
            return
        (self._collapsed.add if folded else self._collapsed.discard)(index)
        row = self._row_of(index)
        if row is not None:
            before, self._filling = self._filling, True
            try:
                self._set_tree_data(self.table.item(row, 0), self._instance(index))
            finally:
                self._filling = before
        self._apply_folds()

    def _reveal(self, index):
        """Unfold whatever hides this row."""
        parent = self._parents.get(index)
        seen = set()
        while parent is not None and parent not in seen:
            seen.add(parent)
            self._set_folded(parent, False)
            parent = self._parents.get(parent)

    def _toggle_fold(self, index):
        self._set_folded(index, index not in self._collapsed)

    def _step_fold(self, fold):
        """Left folds the selected row, or steps to its parent; Right unfolds."""
        index = self.viewer.selected
        if index is None:
            return
        if fold:
            if self._children.get(index) and index not in self._collapsed:
                self._set_folded(index, True)
            elif self._parents.get(index) is not None:
                self.viewer.select(self._parents[index])
        else:
            self._set_folded(index, False)

    def _populate(self):
        self._filling = True
        instances = [i for i in self.scene.instances if i.flip is None] if self.scene else []
        self._collapsed = set()
        self._view_hidden = set()
        self._parents, self._children, self._depth = {}, {}, {}
        if instances:
            instances, self._depth = self._nest(instances)
            # Everything with something under it opens folded.
            self._collapsed = set(self._children)
        # A ResizeToContents column re-measures every row on each setItem:
        # 2.3s of a stalled window for AREA_0A's 329 rows. Measure once.
        header = self.table.horizontalHeader()
        fitted = [c for c in range(self.table.columnCount())
                  if header.sectionResizeMode(c) == QHeaderView.ResizeMode.ResizeToContents]
        for column in fitted:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(instances))
            for row, instance in enumerate(instances):
                if isinstance(instance, PartRow):
                    name = QTableWidgetItem(instance.label)
                    self._set_tree_data(name, instance)
                    name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    name.setCheckState(Qt.CheckState.Checked)
                    name.setData(ROLE, instance.index)
                    name.setData(DOT_COLOR, instance_color(instance.instance))
                    self.table.setItem(row, 0, name)
                    self._fill_part_row(row, instance)
                    continue
                name = QTableWidgetItem(instance.label)
                self._set_tree_data(name, instance)
                name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                name.setCheckState(Qt.CheckState.Checked)
                name.setData(ROLE, instance.index)
                # The modern theme's dot: the colour it is marked in in the view.
                name.setData(DOT_COLOR, instance_color(instance))
                self.table.setItem(row, 0, name)
                self._fill_row(row, instance)
        finally:
            for column in fitted:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            self.table.setUpdatesEnabled(True)
        self._filling = False
        self.table.clearSelection()
        self.viewer.set_hidden_groups(())
        self.model_box.clear()
        self._show_details(None)
        self._fill_views()
        self._apply_view(frame=False)

    # --- area or room -------------------------------------------------

    def _fill_views(self):
        """Area, then every interior by the game's own number (src_Interior,
        its scene index less one). One whose scene table is missing or empty,
        or whose actors draw nothing, is listed too, saying so."""
        # An area opens with everything in it, rooms included; a Progress
        # switch keeps what was chosen (see load_area).
        current = getattr(self, "_keep_view", "all")
        self._filling_views = True
        self.view_box.clear()
        self.view_box.addItem("Area", None)
        instances = self.scene.instances if self.scene else []
        tables = getattr(self.scene, "room_tables", {}) or {}
        found = {i.scene for i in instances if i.scene is not None}
        # The gaps between rooms with something in them; an area whose only
        # table is an empty one (the bosses) has no rooms at all.
        last = max(found, default=0)
        scenes = sorted(found | {s for s in tables if s <= last})
        for scene in scenes:
            rows = [i for i in instances if i.scene == scene and i.flip is None]
            names = [room_row_name(i) for i in rows]
            if tables.get(scene, 0) is None and not rows:
                what = "no scene table - no door leads here"
            elif tables.get(scene) == 0 and not rows:
                what = "empty scene table"
            else:
                shown = [n for n in dict.fromkeys(names) if n][:ROOM_NAMES]
                more = len(set(names)) - len(shown)
                what = (f"{len(rows)} row(s): " + ", ".join(shown)
                        + (f" +{more} more" if more > 0 else ""))
            self.view_box.addItem(f"Interior {scene - 1} - {what}", scene)
            self.view_box.setItemData(self.view_box.count() - 1,
                                      "\n".join(sorted(set(names))) or what,
                                      Qt.ItemDataRole.ToolTipRole)
        if scenes:
            self.view_box.addItem("Area and every room", "all")
        for row in range(self.view_box.count()):
            if self.view_box.itemData(row) == current:
                self.view_box.setCurrentIndex(row)
                break
        else:
            self.view_box.setCurrentIndex(self.view_box.count() - 1)
        self._filling_views = False

    def _apply_view(self, *_args, frame=True):
        """Hide what is not in the chosen area or room - rows and view both
        - on top of whatever rows are unticked."""
        if self.scene is None or self._filling_views:
            return
        view = self.view_box.currentData()
        filtered = set()
        for instance in self.scene.instances:
            if view == "all":
                shown = True
            elif view is None:
                shown = instance.scene is None
            else:
                shown = instance.scene == view
            if not shown:
                filtered.add(instance.index)
        unchecked = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            if (item.data(Qt.ItemDataRole.CheckStateRole) is not None
                    and item.data(ROLE) >= 0
                    and item.checkState() != Qt.CheckState.Checked):
                unchecked.add(item.data(ROLE))
        # A recorded effect's later frames have no row of their own.
        self._view_hidden = filtered
        self._apply_folds()
        self.viewer.set_view(view)
        self.viewer.set_hidden_groups(filtered | unchecked)
        self._sync_hidden_parts()
        # Only an interior is framed: it is somewhere else entirely. The area,
        # alone or with its rooms, keeps the shot the user had.
        if frame and view not in (None, "all"):
            self.viewer.frame_visible()

    def _sync_part_row(self, index, part):
        """Highlight the row of the part picked in the view."""
        if self._filling or index is None:
            return
        key = index if part is None else part_key(index, part)
        if self._row_of(key) is None:
            key = index
        row = self._row_of(key)
        if row is None:
            return
        self._filling = True
        try:
            self._reveal(key)
            self.table.clearSelection()
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))
        finally:
            self._filling = False

    def _on_part_selected(self, index, part):
        instance = self._instance(index)
        if instance is None:
            return
        self._sync_part_row(index, part)
        self._show_details(index)
        if part is None or not 0 <= part < len(instance.sources):
            return
        file_id, group = instance.sources[part]
        named = self.scene.named(((file_id, group),))
        text = (f"<b>part {part}</b>: id {file_id} group {group}"
                + (f" - {named}" if named else ""))
        owners = getattr(instance.assembly, "owners", None) or ()
        if 0 <= part < len(owners):
            # The whole actor that drew it is what the box goes round.
            number, handler, position = owners[part]
            members = self.viewer.sub_object(instance, part)
            parts = ", ".join(f"id {f} g{g}" for f, g in
                              (instance.sources[n] for n in members))
            where = ", ".join(f"{v:.0f}" for v in position)
            text = (f"<b>{handler}</b> (actor {number}, at {where}): "
                    f"{len(members)} part(s) - {parts}<br>" + text)
        self.details.setText(text + "<br>" + self.details.text())
        print(f"selected part: '{instance.label}' part {part} = id {file_id} "
              f"group {group}" + (f" '{named}'" if named else ""))

    def _fill_part_row(self, row, part_row):
        instance, part = part_row.instance, part_row.part
        owners = getattr(instance.assembly, "owners", None) or ()
        where = (tuple(f"{v:.0f}" for v in owners[part][2])
                 if part < len(owners) else ("", "", ""))
        for column, text in ((1, self._source_text(instance.sources[part])),
                             (2, where[0]), (3, where[1]), (4, where[2]), (5, "")):
            self.table.setItem(row, column, QTableWidgetItem(text))

    def _fill_row(self, row, instance):
        if instance.assembly is not None and len(instance.sources) > 1:
            model = f"{instance.name} ({len(instance.sources)} parts)"
        elif instance.sources:
            model = " + ".join(self._source_text(s) for s in instance.sources)
        elif instance.drawn_as_sprite:
            model = "sprite"
        else:
            model = "-" if instance.role == "room" else "unknown"
        gx, gy, gz = instance.game_position
        for column, text in ((1, model), (2, f"{gx:.0f}"),
                             (3, f"{gy:.0f}"), (4, f"{gz:.0f}"),
                             (5, f"{instance.angle:.0f}")):
            item = self.table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, column, item)
            item.setText(text)

    def _row_of(self, index):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(ROLE) == index:
                return row
        return None

    def _sync_hidden_parts(self):
        """Hand the viewer the parts whose eye is closed."""
        parts = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            key = item.data(ROLE) if item is not None else None
            if (key is not None and key < 0
                    and item.checkState() != Qt.CheckState.Checked):
                parts.add(part_of_key(key))
        self.viewer.set_hidden_parts(parts)

    def _on_item_changed(self, item):
        if self._filling or item.column() != 0:
            return
        if item.data(ROLE) < 0:
            self._sync_hidden_parts()
            return
        self.viewer.set_group_hidden(item.data(ROLE),
                                     item.checkState() != Qt.CheckState.Checked)

    def _on_row_selected(self):
        if self._filling:
            return
        rows = self.table.selectionModel().selectedRows()
        index = (self.table.item(rows[0].row(), 0).data(ROLE) if rows else None)
        if index is not None and index < 0:
            # A part row: its instance, then exactly that part.
            owner, part = part_of_key(index)
            self._filling = True
            try:
                if self.viewer.selected != owner:
                    self.viewer.select(owner)
                self.viewer.select_part(part, exact=True)
            finally:
                self._filling = False
            return
        self.viewer.select(index)

    def _on_row_double_clicked(self, item):
        """Frame the row's object, the way F does."""
        index = self.table.item(item.row(), 0).data(ROLE)
        if index is None:
            return
        if index < 0:
            self._on_row_selected()
        else:
            self.viewer.select(index)
        self.viewer.frame_selection()

    def _on_view_selection(self, index):
        self._show_details(index)
        if self._filling:
            return
        self._filling = True
        try:
            self.table.clearSelection()
            if index is not None:
                self._reveal(index)
            row = self._row_of(index) if index is not None else None
            if row is not None:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
        finally:
            self._filling = False

    def _on_instance_moved(self, index):
        self._scene_memory_cache.clear()
        self._placement_dirty = True
        instance = self._instance(index)
        row = self._row_of(index)
        if instance is None or row is None:
            return
        self._filling = True
        self._fill_row(row, instance)
        self._fill_boxes(instance)
        self._filling = False

    # --- the selected instance ----------------------------------------

    def _instance(self, index):
        instances = self.scene.instances if self.scene else []
        if index is None or not 0 <= index < len(instances):
            return None
        return instances[index]

    def _source_text(self, source):
        """'id 12 g17' - with the part's name, given in the SMST viewer, if
        it has one."""
        file_id, group = source
        name = self.scene.named((source,)) if self.scene is not None else ""
        return f"id {file_id} g{group}" + (f" '{name}'" if name else "")

    def _print_selection(self, instance):
        """Name what was picked in the level by where it is written.

        An object is a record in an overlay's placement table, so that -
        table, record number, file offset - is what identifies it; the
        model it draws with is named by the DAT ids it comes from."""
        if instance is None:
            self.pickup_options.hide()
            print("selected: nothing")
            return
        area = f"AREA_{self.chunk:02X}" if self.chunk is not None else "level"
        bits = [f"{area} {instance.role} #{instance.index}",
                f"'{instance.label}'"]
        if instance.pickup is not None:
            bits.append(instance.pickup.describe())
            bits.append(f"record {instance.pickup.index} of pickup table "
                        f"{instance.pickup.table}"
                        f" @ 0x{instance.pickup.offset:X} in the overlay")
        placement = instance.placement
        if placement is not None:
            bits.append(f"kind {placement.kind} slot {placement.slot}")
            bits.append(f"handler 0x{placement.handler:X}"
                        if isinstance(placement.handler, int)
                        else f"handler {placement.handler}")
            bits.append(f"record {placement.index} of table {placement.table}"
                        f" @ 0x{placement.offset:X} in the overlay")
        if instance.room is not None:
            bits.append(f"room MDAT {instance.room}")
        if instance.sources:
            bits.append("model " + ", ".join(self._source_text(s)
                                             for s in instance.sources))
        bits.append("at ({:.0f}, {:.0f}, {:.0f})".format(*instance.game_position)
                    + f" turned {instance.angle:.0f} deg")
        print("selected: " + "  ".join(bits))

    def _show_details(self, index):
        instance = self._instance(index)
        self.events_button.setEnabled(instance is not None and bool(self.exe_path)
                                      and bool(self.scene and self.scene.overlay_path)
                                      and not getattr(self, '_loading', False))
        self._update_area_scripts()
        self._print_selection(instance)
        if instance is None:
            self.details.setText("Click something in the view, or pick a row.")
            for box in self.boxes.values():
                box.setEnabled(False)
            self.model_box.setEnabled(False)
            return
        key = instance_key(instance)
        where = (self.scene.binding_source.get(key)
                 if key is not None else None)
        told = {"code": "read out of the handler's own code",
                "corrected": "corrected by hand"}.get(where)
        self.details.setText(instance.describe()
                             + (f"<br><i>model {told}</i>" if told else ""))
        self._filling = True
        self.model_box.clear()
        if instance.role == "room":
            self.model_box.setEnabled(False)
        else:
            for label, source in self.scene.model_choices():
                self.model_box.addItem(label, source)
            # Walked rather than findData()'d: the data is a Python
            # tuple, and findData compares the variants it is wrapped in
            # rather than the tuples themselves, so it never matches.
            for row in range(self.model_box.count()):
                if self.model_box.itemData(row) == instance.source:
                    self.model_box.setCurrentIndex(row)
                    break
            self.model_box.setEnabled(not getattr(self, "_loading", False))
        self._fill_boxes(instance)
        for box in self.boxes.values():
            box.setEnabled(instance.movable and not instance.authored
                           and not getattr(self, "_loading", False))
        if instance.pickup is not None:
            self.boxes["Angle"].setEnabled(
                self.scene.can_rotate_pickup(instance.pickup)
                and not getattr(self, "_loading", False))
            self.boxes["Angle"].setToolTip(
                "Town chest rotation uses the game's 256 direction steps."
                if self.scene.can_rotate_pickup(instance.pickup) else
                "The game determines this pickup's rotation from collision.")
            self.model_box.setEnabled(False)
        if getattr(instance, 'native_scene_offset', None) is not None:
            # Scene records store position only; rotation is the NPC's code.
            self.boxes['Angle'].setEnabled(False)
            self.boxes['Angle'].setToolTip('This NPC’s scene record stores position; rotation is controlled by its native actions.')
            self.model_box.setEnabled(False)
        self._fill_pickup_options(instance)
        self._filling = False

    def _fill_boxes(self, instance):
        """The boxes hold the game's own axes - the numbers in its records
        and RAM - not the viewer's."""
        for name, value in zip(("X", "Y", "Z"), instance.game_position):
            self.boxes[name].setValue(value)
        self.boxes["Angle"].setValue(instance.angle)

    def _open_events(self):
        instance = self._instance(self.viewer.selected)
        if instance is None or not self.apply_changes():
            return
        from gui.level.event_editor import EventEditor
        # Not modal: its previews play in this panel's viewport, which has
        # to stay visible beside it. One at a time.
        self._close_events()
        try:
            editor = EventEditor(self, instance)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Could not read native events', str(exc))
            return
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.destroyed.connect(lambda *_: setattr(self, '_event_editor', None))
        self._event_editor = editor
        editor.show()

    def _update_area_scripts(self):
        from game import event_script
        scene = self.scene
        self.area_scripts_button.setEnabled(
            bool(self.exe_path) and bool(scene and getattr(scene, 'overlay_path', None))
            and getattr(scene, 'area_index', None) in event_script.CONVERSATION_COUNT
            and not getattr(self, '_loading', False))

    def _open_area_scripts(self):
        """The Event Editor on the area script actor."""
        if self.scene is None or not self.apply_changes():
            return
        from types import SimpleNamespace
        from game import event_script
        from gui.level.event_editor import EventEditor
        self._close_events()
        area = SimpleNamespace(label=f"Area scripts - AREA_{self.chunk:02X}", placement=None,
                               pickup=None, index=-1, scene=None, angle=0.0)
        try:
            editor = EventEditor(self, area, handler=event_script.AREA_SCRIPT_ACTOR)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Could not read native events', str(exc))
            return
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.destroyed.connect(lambda *_: setattr(self, '_event_editor', None))
        self._event_editor = editor
        editor.show()

    def _undo_changed(self):
        self.undo_button.setEnabled(bool(self.undo.undo_stack))
        self.redo_button.setEnabled(bool(self.undo.redo_stack))

    def _close_events(self):
        """Close the Events window, putting back anything it previewed."""
        editor = getattr(self, '_event_editor', None)
        self._event_editor = None
        if editor is not None:
            try:
                editor.stop_preview()
                editor.close()
            except RuntimeError:
                pass

    def stop_event_preview(self):
        editor = getattr(self, '_event_editor', None)
        if editor is not None:
            try:
                editor.stop_preview()
            except RuntimeError:
                self._event_editor = None

    def _on_box_changed(self, _value):
        if self._filling:
            return
        self._scene_memory_cache.clear()
        instance = self._instance(self.viewer.selected)
        if instance is None or not instance.movable:
            return
        self.undo.remember(("box", instance.index))
        try:
            if instance.pickup is not None:
                requested = tuple(self.boxes[n].value() for n in ("X", "Y", "Z"))
                previous = tuple(round(v) for v in instance.game_position)
                changed = {at for at, (old, new) in enumerate(zip(previous, requested))
                           if old != new}
                if changed:
                    # A height edit must not choose another collision lane.
                    # Rotation-only edits likewise leave the position alone.
                    axis = ("vertical" if changed == {1} else
                            "horizontal" if 1 not in changed else "free")
                    point = level_scene.view_point(requested)
                    self.scene.move_pickup(instance, point, axis=axis)
                if self.scene.can_rotate_pickup(instance.pickup):
                    self.scene.rotate_pickup(instance, self.boxes["Angle"].value())
            else:
                instance.game_position = tuple(self.boxes[n].value() for n in ("X", "Y", "Z"))
                instance.angle = self.boxes["Angle"].value()
                instance.to_record()
        except ValueError as exc:
            self._filling = True
            self._fill_boxes(instance)
            self._filling = False
            self.viewer.show_status(str(exc), done=True)
            return
        self._placement_dirty = True
        self.viewer.refresh_instance(instance.index)
        row = self._row_of(instance.index)
        self._filling = True
        try:
            self._fill_boxes(instance)
            if row is not None:
                self._fill_row(row, instance)
        finally:
            self._filling = False

    def _on_model_changed(self, _index):
        if self._filling or self.scene is None:
            return
        self._scene_memory_cache.clear()
        instance = self._instance(self.viewer.selected)
        if instance is None or instance.role == "room":
            return
        source = self.model_box.currentData()
        if source == instance.source:
            return
        self.undo.remember()
        instance.source = source
        key = instance_key(instance)
        if key is not None:
            self.scene.bindings[key] = source
        # The scene's arrays are laid out instance by instance, so a
        # different model means different geometry in the middle of
        # them: everything downstream of it moves, and the whole scene
        # is rebuilt rather than patched.
        self._rebuild()

    def _rebuild(self, frame=False):
        """Rebuild the scene around a change, leaving the camera, the
        selection and the hidden rows where the user had them."""
        self.scene.__dict__.pop("_built_model", None)
        selected = self.viewer.selected
        unchecked = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if (item is not None and item.data(Qt.ItemDataRole.CheckStateRole) is not None
                    and item.checkState() != Qt.CheckState.Checked):
                unchecked.add(item.data(ROLE))
        self._keep_view = self.view_box.currentData()
        self.viewer.load_scene(self.scene, frame=frame)
        self._populate()
        self._filling = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if (item.data(ROLE) in unchecked
                    and item.data(Qt.ItemDataRole.CheckStateRole) is not None):
                item.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False
        self._apply_view(frame=False)
        if selected is not None:
            self.viewer.select(selected)

    # --- saving -------------------------------------------------------

    def _catalog(self):
        from game import pickup_edit
        return pickup_edit.reward_catalog(
            self.exe_path, self.scene.overlay_path, self.scene.area_index)

    def _fill_pickup_options(self, instance):
        record = instance.pickup
        self.pickup_options.setVisible(record is not None)
        if record is None:
            return
        self.pickup_kind.clear()
        self.pickup_reward.clear()
        self.pickup_category.clear()
        category = "chest" if record.chest else self._pickup_category(record)
        if record.chest:
            for value, label in placement_module.CHEST_KINDS.items():
                self.pickup_kind.addItem(label.capitalize(), value)
            self.pickup_kind.setCurrentIndex(self.pickup_kind.findData(record.art_reward))
        else:
            self.pickup_kind.addItem(category.capitalize(), category)
        self.pickup_kind.setEnabled(record.chest and not getattr(self, "_loading", False))
        selected = record.contents if record.chest else record.art_reward
        for entry in self._catalog():
            if not record.chest and entry.category != category:
                continue
            row = self.pickup_reward.count()
            self.pickup_reward.addItem(entry.label, entry.reward)
            if not entry.available:
                self.pickup_reward.model().item(row).setEnabled(False)
                self.pickup_reward.setItemData(row, entry.reason, Qt.ItemDataRole.ToolTipRole)
        match = self.pickup_reward.findData(selected)
        if match < 0:
            self.pickup_reward.addItem(f"Reward {selected}", selected)
            match = self.pickup_reward.count() - 1
        self.pickup_reward.setCurrentIndex(match)
        entry = next((e for e in self._catalog() if e.reward == selected), None)
        self.pickup_category.addItem(entry.item_type if entry else "Native pickup")
        self.pickup_reward.setEnabled(not getattr(self, "_loading", False))

    def _pickup_category(self, record):
        if record.chest:
            return "chest"
        entry = next((e for e in self._catalog() if e.reward == record.art_reward), None)
        return entry.category if entry else "item"

    def _on_pickup_option(self, _index):
        if self._filling or self.scene is None or getattr(self, "_loading", False):
            return
        instance = self._instance(self.viewer.selected)
        if instance is None or instance.pickup is None:
            return
        record = instance.pickup
        reward = self.pickup_reward.currentData()
        if reward is None:
            return
        try:
            reward = self._resolve_reward(reward)
        except (ValueError, OSError) as exc:
            QMessageBox.information(self, "Couldn't select this item", str(exc))
            self._show_details(instance.index)
            return
        self.undo.remember()
        if record.chest:
            kind = self.pickup_kind.currentData()
            if kind is not None:
                record.reward = (record.reward & 0x80) | kind
            record.config = (record.config & ~placement_module.CONTENTS_MASK) | reward
        else:
            record.reward = (record.reward & 0x80) | reward
        self.scene.refresh_pickup(instance)
        self._placement_dirty = True
        self._refresh_edited_scene(instance.index)

    def _refresh_edited_scene(self, selected):
        self._scene_memory_cache.clear()
        self._sprite_cache.clear()
        self._rebuild()
        self._load_sprites(self.scene, self.viewer.vram_raw_bytes)
        self.viewer.select(selected)
        self._show_details(selected)
        self._sync_motion_animation(self.viewer.animate_action.isChecked())

    def _add_pickup(self, category, point):
        if (self.scene is None or not self.viewer.edit_mode
                or getattr(self, "_loading", False)):
            return
        from game import pickup_edit
        try:
            overlays = self._all_overlay_paths()
            reward = pickup_edit.default_reward(category, self._catalog())
            # The active scene can contain unsaved additions, so it also
            # participates in native persistence-bit allocation.
            record = pickup_edit.new_pickup(
                category, self.scene.pickups, self.exe_path,
                overlay_paths=overlays,
                table=self.scene.pickup_table_for_view(self.view_box.currentData()),
                overlay_path=self.scene.overlay_path, area=self.scene.area_index,
                reward=4 if reward >= 256 else reward)
            old_instances = len(self.scene.instances)
            self.undo.remember()
            instance = self.scene.add_pickup(
                record, point=point, view=self.view_box.currentData())
            try:
                resolved = self._resolve_reward(reward)
                if record.chest:
                    record.config = (record.config & ~placement_module.CONTENTS_MASK) | resolved
                else:
                    record.reward = resolved
                self.scene.refresh_pickup(instance)
            except (ValueError, OSError):
                self.scene.pickups.remove(record)
                del self.scene.instances[old_instances:]
                self.scene.__dict__.pop("_built_model", None)
                self.undo.forget_last()
                raise
        except (ValueError, OSError) as exc:
            QMessageBox.information(self, "Couldn't place this object", str(exc))
            return
        self._placement_dirty = True
        self._refresh_edited_scene(instance.index)
        self.viewer.show_status("Placed. Change its type or contents in Selected.", done=True)

    def has_unsaved_changes(self):
        """Level edits not yet in a saved project. Staged files stay in
        _placement_files for the whole session (Build Disc needs them), so
        their mere presence says nothing about whether they were saved."""
        return bool(self._placement_dirty
                    or self._placement_files != getattr(self, "_saved_files", {}))

    def mark_saved(self):
        self._saved_files = dict(self._placement_files)

    def unsaved_summary(self):
        """'' or a phrase naming the unsaved level files, for a prompt."""
        if not self.has_unsaved_changes():
            return ""
        saved = getattr(self, "_saved_files", {})
        names = sorted(n for n, data in self._placement_files.items() if saved.get(n) != data)
        if self._placement_dirty:
            return "unapplied changes to the current level"
        return f"{len(names)} edited level file(s) ({', '.join(names[:4])}{'...' if len(names) > 4 else ''})"

    def _repair_legacy_pickups(self):
        """Move a project saved with the first, crashing item layout into
        the free RAM window (game/pickup_edit.py)."""
        if not self.exe_path or not os.path.isfile(self.exe_path):
            return
        from game import pickup_edit
        overlays = self._all_overlay_paths()
        try:
            files = pickup_edit.repair_legacy_layout(self.exe_path, overlays)
            if files:
                self._write_placement_files(files)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(
                self, "Item placements need attention",
                f"This project's added items use an old layout that crashes the "
                f"game, and it couldn't be repaired automatically:\n\n{exc}")
            return
        if not files:
            # Older custom tables drew every added item with the shared icon;
            # give it the area's own ground sprite where the area has one.
            try:
                art = pickup_edit.refresh_custom_presentation(self.exe_path, overlays)
                if art:
                    self._write_placement_files(art)
            except (ValueError, OSError):
                return
            for path, data in art.items():
                self._placement_files[os.path.basename(path)] = data
            return
        for path, data in files.items():
            self._placement_files[os.path.basename(path)] = data
        QMessageBox.information(
            self, "Item placements repaired",
            "This project's added items were stored in RAM the game also "
            "uses, which made levels crash on load. They have been moved to "
            "free memory; nothing you placed was lost.\n\n"
            "Save the project and rebuild the disc to use the fix.")

    def _all_overlay_paths(self):
        return list(dict.fromkeys(filter(None, (
            self.overlay_for_area(chunk) for chunk in range(
                level_scene.FIRST_AREA_CHUNK,
                level_scene.FIRST_AREA_CHUNK + placement_module.AREA_COUNT)))))

    @staticmethod
    def _write_placement_files(files):
        """Stage every file first and restore originals if a replace fails."""
        import tempfile
        staged, committed = [], []
        try:
            for path, data in files.items():
                with open(path, "rb") as source:
                    old = source.read()
                with tempfile.NamedTemporaryFile(dir=os.path.dirname(path),
                        prefix=".placement-", delete=False) as output:
                    output.write(data)
                    staged.append((path, output.name, old))
            for path, temporary, old in staged:
                os.replace(temporary, path)
                committed.append((path, old))
        except OSError:
            for path, old in committed:
                with open(path, "wb") as output:
                    output.write(old)
            raise
        finally:
            for path, temporary, old in staged:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def _resolve_reward(self, reward):
        """Virtual inventory choices become native selectors in a saved bundle."""
        if reward < 256:
            return reward
        from game import pickup_edit
        from formats.sprites import pickup_art
        bundle = pickup_edit.ensure_custom_rewards(
            self.exe_path, self._all_overlay_paths(), item_ids=(reward - 256,))
        self._write_placement_files(bundle.files)
        for path, data in bundle.files.items():
            self._placement_files[os.path.basename(path)] = data
        with open(self.scene.overlay_path, "rb") as source:
            self.scene.overlay_data = source.read()
        self.scene.reward_art = pickup_art.reward_art(
            self.exe_path, overlay=self.scene.overlay_data, area=self.scene.area_index)
        self._scene_memory_cache.clear()
        return bundle.item_rewards[reward - 256]

    def disc_replacements(self):
        """Current staged files, including edits restored from a project."""
        replacements = dict(self._placement_files)
        # Folder projects hold the full overlay files; their source image
        # still has the unedited originals. Include those files on rebuild.
        if self.dat_path:
            for root in (os.path.dirname(self.dat_path),
                         os.path.dirname(os.path.dirname(self.dat_path))):
                folder = os.path.join(root, "BIN")
                if os.path.isdir(folder):
                    for name in os.listdir(folder):
                        path = os.path.join(folder, name)
                        if name.upper().endswith(".BIN") and os.path.isfile(path):
                            with open(path, "rb") as source:
                                replacements[name] = source.read()
        if self.exe_path and os.path.isfile(self.exe_path):
            with open(self.exe_path, "rb") as source:
                replacements["MAIN.EXE"] = source.read()
        return replacements

    def apply_changes(self, announce=False):
        """Stage native records before an area switch, project save or export."""
        # A playing event preview has moved an NPC on screen only; put it
        # back before anything reads positions to write them.
        self.stop_event_preview()
        if not self._placement_dirty or self.scene is None:
            return True
        from game import pickup_edit
        try:
            overlay_path = self.scene.overlay_path
            with open(overlay_path, "rb") as source:
                old_overlay = source.read()
            with open(self.exe_path, "rb") as source:
                old_exe = source.read()
            overlay = placement_module.patch(old_overlay, self.scene.placements)
            import struct
            native=bytearray(overlay)
            for instance in self.scene.instances:
                offset=getattr(instance,'native_scene_offset',None)
                if offset is None:continue
                if offset<0 or offset+16>len(native):raise ValueError('Scene placement offset is outside the area file.')
                expected=getattr(self.scene,'event_handlers',{}).get(instance.index)
                if expected is not None and struct.unpack_from('<I',native,offset+12)[0]!=expected:
                    raise ValueError('The NPC scene binding changed. Reload the area before applying its position.')
                struct.pack_into('<hhh',native,offset+4,*(int(round(v)) for v in instance.game_position))
            overlay=bytes(native)
            overlay, exe, records = pickup_edit.stage_pickups(
                overlay, old_exe, self.scene.pickups)
            self._write_placement_files({overlay_path: overlay, self.exe_path: exe})
            updated = {(r.table, r.index): r for r in records}
            for before in self.scene.pickups:
                before.__dict__.update(updated[before.table, before.index].__dict__)
            self.scene.overlay_data = overlay
            self._placement_files[os.path.basename(overlay_path)] = overlay
            self._placement_files["MAIN.EXE"] = exe
            self._placement_dirty = False
            self._scene_memory_cache.clear()
            if announce:
                self.viewer.show_status("Level changes applied. Save Project or Build Disc to keep them.", done=True)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Couldn't apply level changes", str(exc))
            return False
        return True

    def _keep_models(self):
        """Write the models the objects are set to into
        labels/placements.json.

        Taken off the instances rather than out of `bindings`: what the
        combo box changes is the object on screen, and this is what
        makes that stick."""
        if self.scene is None or not self.scene.overlay_path:
            QMessageBox.information(
                self, "Nothing to keep",
                "This area has no overlay, so there are no objects to "
                "remember models for.")
            return
        for instance in self.scene.instances:
            key = instance_key(instance)
            if key is not None:
                self.scene.bindings[key] = instance.sources
        if self._store_bindings():
            name = os.path.basename(self.scene.overlay_path)
            QMessageBox.information(
                self, "Kept",
                f"{len(placement_module.load_bindings(name, section=placement_module.CORRECTED))}"
                f" correction(s) for this area are now in "
                f"labels/placements.json.")

    def _store_bindings(self):
        """Put this area's models in labels/placements.json, leaving
        every other area's alone. True if it was written.

        Only what differs from what the code says is kept, so the file
        stays a list of what somebody actually put right."""
        name = os.path.basename(self.scene.overlay_path)
        code = getattr(self.scene, "code_bindings", {}) or {}
        path = placement_module.bindings_path()
        corrections = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for overlay in (json.load(f).get(placement_module.CORRECTED) or {}):
                    corrections[overlay] = placement_module.load_bindings(
                        overlay, section=placement_module.CORRECTED)
        except (OSError, ValueError):
            pass
        # One model per record, which is what the file holds and what
        # picking one in the box means. A record left with the several
        # the code named is not a correction, so it is not written.
        corrections[name] = {key: models[0]
                             for key, models in self.scene.bindings.items()
                             if len(models or ()) == 1
                             and tuple(code.get(key, ())) != tuple(models)}
        try:
            placement_module.save_bindings(
                corrections, section=placement_module.CORRECTED)
        except OSError as e:
            QMessageBox.warning(self, "Couldn't write that",
                                f"labels/placements.json wouldn't save:\n\n{e}")
            return False
        return True

    def _name_model(self):
        """Put a name on what the selected object is drawn with."""
        instance = self._instance(self.viewer.selected)
        if instance is None or not instance.sources:
            QMessageBox.information(
                self, "Nothing to name",
                "Pick something with a model first - a marker has none "
                "to call anything.")
            return
        file_id, group = instance.sources[0]
        whole = len({f for f, _g in instance.sources}) == 1 and len(instance.sources) > 1
        key = str(file_id) if whole else f"{file_id}:{group}"
        what = (f"every part of id {file_id}" if whole
                else f"id {file_id} group {group}")
        names = placement_module.load_model_names()
        text, ok = QInputDialog.getText(
            self, "Name this model", f"What is {what}?",
            text=names.get(key, ""))
        if not ok:
            return
        text = text.strip()
        if text:
            names[key] = text
        else:
            names.pop(key, None)
        placement_module.save_model_names(names)
        self.scene.model_names = names
        self._rebuild()

    def _save_overlay(self):
        if self.scene is None or not self.scene.overlay_path:
            QMessageBox.information(self, "No overlay",
                                    "This area has no overlay to write.")
            return
        name = os.path.basename(self.scene.overlay_path)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the patched overlay", name, "Overlay (*.BIN);;All files (*)")
        if not path:
            return
        if not self.apply_changes():
            return
        try:
            with open(self.scene.overlay_path, "rb") as f:
                data = f.read()
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            QMessageBox.critical(self, "Couldn't write it", str(e))
            return
        QMessageBox.information(
            self, "Saved",
            f"Wrote {len(self.scene.placements)} object record(s) and "
            f"{len(self.scene.pickups)} pickup(s) into a copy of {name}. "
            "New pickup tables also require the updated MAIN.EXE; use Save Project or Build Disc to keep both.")

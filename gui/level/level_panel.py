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
import sys
import tempfile
import time
from math import gcd

import numpy as np
from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
    QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from functions import clut_anim
from functions import placement as placement_module
from gui.bgmp import bgmp_render
from gui.bgmp.bgmp_parser import PALETTE_STRIDE, load_bgmp
from gui.clut_animation import TICK_HZ
from gui.level.level_scene import (
    ASSET_PACK_ID, BACKGROUND_ID, BOTH, EVENTS_DONE, FRESH, LevelScene,
    area_files, instance_color, instance_key, room_entries)
from gui.dot_delegate import DOT_COLOR, DotDelegate
from gui.level import pickup_sprites
from gui.level.level_viewer import LevelViewer
from gui.panel_title import make_panel_title

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
        # drawn with live - see functions/handler_models.py.
        self.exe_path = None
        self.scene = None
        self._filling = False
        self._cache_process = None
        self._cache_manifest = None
        self._cache_output_tail = ""
        self._background_cache = {}
        self._sprite_cache = {}

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

        self.area_box = QComboBox(self)
        self.area_box.setMinimumWidth(340)
        self.area_box.setMinimumContentsLength(30)
        self.area_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.area_box.currentIndexChanged.connect(self._on_area_changed)

        # Area or one of its rooms. Inside, the game never draws the
        # area's own mesh - a room is only the actors its scene table
        # spawns (functions/actor_sim.py) - so a room is shown alone.
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
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setItemDelegateForColumn(0, DotDelegate(self.table))
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
        self.save_button = QPushButton("Save overlay as...", self)
        self.save_button.setToolTip(
            "Write a copy of this area's Axx.BIN with the positions and "
            "angles as they are here. Only those bytes change.")
        self.save_button.clicked.connect(self._save_overlay)
        self.cache_button = QPushButton("Pre-cache levels", self)
        self.cache_button.setToolTip(
            "Build every area's exact Fresh, Events done, and Both scene in "
            "two background processes. This is a one-time calculation for "
            "this disc; afterwards those Level Editor loads are cache reads.")
        self.cache_button.clicked.connect(self._precache_levels)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.keep_button)
        buttons.addWidget(self.name_button)
        buttons.addWidget(self.gif_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cache_button)

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
        splitter.addWidget(self.viewer)
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
        self.dat_path = dat_path
        self.idx_path = idx_path
        self.overlay_for_area = overlay_for_area
        self.vram_for_area = vram_for_area
        self.exe_path = exe_path
        self._background_cache.clear()
        self._sprite_cache.clear()
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
            pack = any(file_id == ASSET_PACK_ID and size > 0
                       for _i, file_id, _o, size in files)
            if pack or room_entries(self.idx_path, self.dat_path, chunk):
                out.append(chunk)
        return out

    def _enable(self, on):
        for widget in (self.table, self.model_box, self.progress_box,
                       self.keep_button, self.save_button, self.cache_button,
                       *self.boxes.values()):
            widget.setEnabled(on)

    def _precache_levels(self):
        """Compute every exact scene off the UI thread, two areas at once."""
        if self._cache_process is not None:
            return
        jobs = []
        for row in range(self.area_box.count()):
            chunk = self.area_box.itemData(row)
            overlay = self.overlay_for_area(chunk)
            if overlay and self.exe_path:
                jobs.append((self.dat_path, self.idx_path, chunk,
                             overlay, self.exe_path))
        if not jobs:
            return
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="tomba2-level-cache-",
            encoding="utf-8", delete=False)
        try:
            json.dump(jobs, handle)
        finally:
            handle.close()
        self._cache_manifest = handle.name
        self._cache_output_tail = ""
        process = QProcess(self)
        self._cache_process = process
        process.setWorkingDirectory(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._cache_output)
        process.finished.connect(self._cache_finished)
        self.cache_button.setEnabled(False)
        self.cache_button.setText(f"Caching 0/{len(jobs)}...")
        process.start(sys.executable, ["-m", "functions.scene_prewarm",
                                       self._cache_manifest])

    def _cache_output(self):
        process = self._cache_process
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace")
        print(output, end="", flush=True)
        combined = self._cache_output_tail + output
        lines = combined.splitlines(keepends=True)
        self._cache_output_tail = (lines.pop() if lines and
                                   not lines[-1].endswith(("\n", "\r")) else "")
        matches = re.findall(r"CACHE (\d+) (\d+) AREA_[0-9A-F]+",
                             "".join(lines))
        if matches:
            done, total = matches[-1]
            self.cache_button.setText(f"Caching {done}/{total}...")

    def _cache_finished(self, exit_code, _status):
        process, self._cache_process = self._cache_process, None
        if process is not None:
            process.deleteLater()
        if self._cache_manifest:
            try:
                os.unlink(self._cache_manifest)
            except OSError:
                pass
        self._cache_manifest = None
        self._cache_output_tail = ""
        self.cache_button.setText(
            "Levels cached" if exit_code == 0 else "Pre-cache levels")
        self.cache_button.setEnabled(bool(self.dat_path))

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

    def load_area(self, chunk, keep_camera=False, keep_view="all"):
        """Open an area. `keep_camera` leaves the view where it is, so
        switching Progress shows the same shot of the same place; `keep_view`
        is the Rooms choice to open on, if the area has it."""
        self._stop_cycling()
        self._keep_view = keep_view
        # Kept so a printed selection can say which area it is in.
        self.chunk = chunk
        overlay = self.overlay_for_area(chunk)
        progress = self.progress_box.currentData()
        started = time.perf_counter()
        # An area's own code is run to build it, which takes seconds the
        # first time - say so, so a quiet window doesn't read as a hang.
        print(f"AREA_{chunk:02X}: loading ({progress})...", flush=True)
        # The VRAM has to be in place before the scene is prepared - the
        # palettes are cut out of it while the buffers are built - and it
        # needs AREA_01 merged in, which is where the character models'
        # texture pages live (see gui/smst/smst_parser.py).
        vram = self.vram_for_area(chunk)
        scene = LevelScene().load(self.dat_path, self.idx_path, chunk, overlay,
                                  self.exe_path, progress=progress)
        self.scene = scene
        print(f"AREA_{chunk:02X}: {len(scene.instances)} row(s) in "
              f"{time.perf_counter() - started:.1f}s, drawing...", flush=True)

        from gui.vram_viewer import vram_index_image
        self.viewer.set_vram(vram, vram_index_image(vram) if vram else None)
        self.viewer.export_name = f"AREA_{chunk:02X}"
        self.viewer.load_scene(scene, frame=not keep_camera)
        self.viewer.load_animations(overlay)
        self._load_background(scene, vram, overlay)
        self._load_sprites(scene, vram)
        # The one Animate button owns palette/UV, background, sprite, effect,
        # and skeletal-pose playback together.
        has_motion = self.viewer.animating or len(self._phases) > 1
        self.viewer.animate_action.setEnabled(
            self.viewer.animate_action.isEnabled() or has_motion)
        if has_motion and self.viewer.animate_wanted:
            self.viewer.animate_action.setChecked(True)
        self._sync_motion_animation(self.viewer.animate_action.isChecked())
        self._populate()
        print(f"AREA_{chunk:02X}: ready in {time.perf_counter() - started:.1f}s",
              flush=True)

        placed = sum(1 for i in scene.instances
                     if i.role == "object" and i.face_count)
        objects = sum(1 for i in scene.instances if i.role == "object")
        pickups = sum(1 for i in scene.instances if i.role == "pickup")
        drawn = sum(1 for i in scene.instances
                    if i.role == "pickup" and i.face_count)
        lines = [f"{objects} object(s) placed by "
                 f"{os.path.basename(overlay) if overlay else 'no overlay'}, "
                 f"{placed} of them with a known model."]
        if pickups:
            lines.append(f"{pickups} crystal(s) and apple(s) from MAIN.EXE's "
                         f"own table, {drawn} of them with a known model.")
        rooms = {i.scene for i in scene.instances if i.scene is not None}
        empty = [s for s in scene.room_tables
                 if s not in rooms and s <= max(rooms, default=0)]
        if rooms or empty:
            lines.append(f"{len(rooms)} interior(s) with something in them, found "
                         f"by running the area's scene spawner - pick one under "
                         f"Rooms." + (f" {len(empty)} more with nothing to draw "
                                      f"are listed there too." if empty else ""))
        lines.extend(scene.notes)
        self.summary.setText("\n".join(lines))

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
        # Two banks: the one every area shares, and the area's own -
        # a handful of rewards are drawn out of the second.
        wheres = {"resident": scene.resident.get(
                      pickup_sprites.RESIDENT_SPRT_ID)}
        own = scene.by_id.get(pickup_sprites.AREA_SPRT_ID)
        if own is not None:
            wheres["area"] = (scene.dat_start, own)
        try:
            banks = {}
            for name, where in wheres.items():
                if where is None:
                    continue
                start, (offset, size) = where
                banks[name] = pickup_sprites.SpriteBank(
                    scene.dat_path, start, offset, size, vram)
            wanted = pickup_sprites.wanted_frames(scene.instances)
            captured, captured_steps, captured_units = [], {}, {}
            if scene.captured_billboards:
                from functions import sprite_rip
                from gui.level.level_scene import CLIP_HZ
                for index, polygons in scene.captured_billboards.items():
                    ripped = sprite_rip.rip_polygons(
                        polygons, vram, 1000.0 / CLIP_HZ, return_scale=True)
                    if not ripped:
                        continue
                    frames, units = ripped
                    keys = []
                    for frame, (image, _ms) in enumerate(frames):
                        key = ("captured", index, frame)
                        pixels = np.asarray(image, dtype=np.uint8)
                        captured.append((key, pixels, image.width / 2,
                                         image.height / 2))
                        keys.append(key)
                    captured_steps[index] = keys
                    captured_units[index] = units
            atlas, placed = pickup_sprites.build_atlas(
                banks, wanted, extra=captured)
            quads = pickup_sprites.billboards(scene.instances, placed)
            for index, keys in captured_steps.items():
                instance = scene.instances[index]
                steps = tuple((placed[key], 1) for key in keys if key in placed)
                if steps:
                    quads.append(pickup_sprites.Billboard(
                        index=index, x=instance.x, y=instance.y, z=instance.z,
                        steps=steps, loops=True,
                        units=float(captured_units[index])))
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
        functions/clut_anim.py) - rather than guessed at from the
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
        entry = scene.by_id.get(BACKGROUND_ID)
        if not entry or not entry[1]:
            from functions import sky_gradient
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
        from functions import sky_gradient
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

    def _populate(self):
        self._filling = True
        instances = self.scene.instances if self.scene else []
        self.table.setRowCount(len(instances))
        for row, instance in enumerate(instances):
            name = QTableWidgetItem(instance.label)
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(Qt.CheckState.Checked)
            name.setData(ROLE, instance.index)
            # The modern theme's dot: the colour it is marked in in the view.
            name.setData(DOT_COLOR, instance_color(instance))
            self.table.setItem(row, 0, name)
            self._fill_row(row, instance)
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
            if item.checkState() != Qt.CheckState.Checked:
                unchecked.add(item.data(ROLE))
            # A recorded effect's later frames have no row of their own.
            instance = self._instance(item.data(ROLE))
            self.table.setRowHidden(row, item.data(ROLE) in filtered or (
                instance is not None and instance.flip is not None))
        self.viewer.set_view(view)
        self.viewer.set_hidden_groups(filtered | unchecked)
        # Only an interior is framed: it is somewhere else entirely. The area,
        # alone or with its rooms, keeps the shot the user had.
        if frame and view not in (None, "all"):
            self.viewer.frame_visible()

    def _on_part_selected(self, index, part):
        instance = self._instance(index)
        if instance is None:
            return
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
              f"group {group}")

    def _fill_row(self, row, instance):
        if instance.assembly is not None and len(instance.sources) > 1:
            model = f"{instance.name} ({len(instance.sources)} parts)"
        elif instance.sources:
            model = " + ".join(f"id {f} g{g}" for f, g in instance.sources)
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

    def _on_item_changed(self, item):
        if self._filling or item.column() != 0:
            return
        self.viewer.set_group_hidden(item.data(ROLE),
                                     item.checkState() != Qt.CheckState.Checked)

    def _on_row_selected(self):
        if self._filling:
            return
        rows = self.table.selectionModel().selectedRows()
        index = (self.table.item(rows[0].row(), 0).data(ROLE) if rows else None)
        self.viewer.select(index)

    def _on_row_double_clicked(self, item):
        """Frame the row's object, the way F does."""
        index = self.table.item(item.row(), 0).data(ROLE)
        if index is None:
            return
        self.viewer.select(index)
        self.viewer.frame_selection()

    def _on_view_selection(self, index):
        self._show_details(index)
        if self._filling:
            return
        self._filling = True
        try:
            self.table.clearSelection()
            row = self._row_of(index) if index is not None else None
            if row is not None:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
        finally:
            self._filling = False

    def _on_instance_moved(self, index):
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

    def _print_selection(self, instance):
        """Name what was picked in the level by where it is written.

        An object is a record in an overlay's placement table, so that -
        table, record number, file offset - is what identifies it; the
        model it draws with is named by the DAT ids it comes from."""
        if instance is None:
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
            bits.append("model " + ", ".join(f"id {f} g{g}"
                                             for f, g in instance.sources))
        bits.append("at ({:.0f}, {:.0f}, {:.0f})".format(*instance.game_position)
                    + f" turned {instance.angle:.0f} deg")
        print("selected: " + "  ".join(bits))

    def _show_details(self, index):
        instance = self._instance(index)
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
            self.model_box.setEnabled(True)
        self._fill_boxes(instance)
        for box in self.boxes.values():
            box.setEnabled(instance.movable and not instance.authored)
        self._filling = False

    def _fill_boxes(self, instance):
        """The boxes hold the game's own axes - the numbers in its records
        and RAM - not the viewer's."""
        for name, value in zip(("X", "Y", "Z"), instance.game_position):
            self.boxes[name].setValue(value)
        self.boxes["Angle"].setValue(instance.angle)

    def _on_box_changed(self, _value):
        if self._filling:
            return
        instance = self._instance(self.viewer.selected)
        if instance is None or not instance.movable:
            return
        instance.game_position = tuple(self.boxes[n].value() for n in ("X", "Y", "Z"))
        instance.angle = self.boxes["Angle"].value()
        instance.to_record()
        self.viewer.refresh_instance(instance.index)
        row = self._row_of(instance.index)
        if row is not None:
            self._filling = True
            self._fill_row(row, instance)
            self._filling = False

    def _on_model_changed(self, _index):
        if self._filling or self.scene is None:
            return
        instance = self._instance(self.viewer.selected)
        if instance is None or instance.role == "room":
            return
        source = self.model_box.currentData()
        if source == instance.source:
            return
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
        selected = self.viewer.selected
        unchecked = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() != Qt.CheckState.Checked:
                unchecked.add(item.data(ROLE))
        self._keep_view = self.view_box.currentData()
        self.viewer.load_scene(self.scene, frame=frame)
        self._populate()
        self._filling = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.data(ROLE) in unchecked:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False
        self._apply_view(frame=False)
        if selected is not None:
            self.viewer.select(selected)

    # --- saving -------------------------------------------------------

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
        try:
            with open(self.scene.overlay_path, "rb") as f:
                data = f.read()
            patched = placement_module.patch(data, self.scene.placements)
            patched = placement_module.patch_pickups(patched,
                                                     self.scene.pickups)
            with open(path, "wb") as f:
                f.write(patched)
        except OSError as e:
            QMessageBox.critical(self, "Couldn't write it", str(e))
            return
        QMessageBox.information(
            self, "Saved",
            f"Wrote {len(self.scene.placements)} object record(s) and "
            f"{len(self.scene.pickups)} pickup(s) into a copy of {name}. "
            f"Only the positions and angles differ from the original.")

"""The Level Editor tab: pick an area, see what stands in it, move it.

The list beside the view is one row per instance - the room, then every
object the area's overlay places, then the parts of the asset pack that
are already standing where they belong. Selecting a row outlines it in
the view and fills the boxes below, and picking something in the view
selects its row; they are two ways at the same list.

An object whose model is not known is shown as a marker (see
gui/level/level_scene.py). "Learn from savestate" is how one stops
being a marker: point it at a PCSX state taken in this area and the
objects standing still in it get bound to what the game was drawing
them with, which is knowledge nothing on the disc carries. See
functions/placement.py.
"""
import os

import json

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from functions import placement as placement_module
from gui.bgmp import bgmp_render
from gui.bgmp.bgmp_parser import load_bgmp
from gui.level.level_scene import (
    ASSET_PACK_ID, BACKGROUND_ID, LevelScene, area_files, room_entries)
from gui.level.level_viewer import LevelViewer
from gui.panel_title import make_panel_title

COLUMNS = ["Instance", "Model", "X", "Y", "Z", "Angle"]
ROLE = Qt.ItemDataRole.UserRole

# How fast the background's cycling palettes are stepped. The same rate
# gui/bgmp/bgmp_viewer.py plays them at, and just as much a guess: how
# often the game rotates a palette is in code, not in the file.
CYCLE_FPS = 12

# A background whose loop is longer than this is left still. Every one
# on the disc loops in eight phases or fewer; the cap is only here so an
# odd file cannot make this render for a minute.
MAX_PHASES = 24


class LevelEditorPanel(QWidget):
    """An area, whole."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dat_path = None
        self.idx_path = None
        self.overlay_for_area = lambda _chunk: None
        self.vram_for_area = lambda _chunk: None
        self.scene = None
        self._filling = False

        # Pre-rendered phases of the background, flipped by a timer -
        # the same trick the BGMP viewer uses, since re-rendering a
        # 1000x300 map of 16x16 tiles every frame is not free.
        self._phases = []
        self._phase = 0
        self._phase_timer = QTimer(self)
        self._phase_timer.timeout.connect(self._next_phase)

        self.viewer = LevelViewer(self)
        self.viewer.selection_changed.connect(self._on_view_selection)
        self.viewer.instance_moved.connect(self._on_instance_moved)

        self.area_box = QComboBox(self)
        self.area_box.setMinimumWidth(240)
        self.area_box.currentIndexChanged.connect(self._on_area_changed)

        self.summary = QLabel("Open a disc to pick an area.", self)
        self.summary.setWordWrap(True)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.itemChanged.connect(self._on_item_changed)

        self.details = QLabel("Click something in the view, or pick a row.", self)
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(72)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        self.model_box = QComboBox(self)
        self.model_box.setToolTip(
            "Which part of the area's models this object is drawn with. "
            "Nothing on the disc says, so this starts at whatever "
            "labels/placements.json has been taught and is yours to change.")
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

        self.learn_button = QPushButton("Learn from savestate...", self)
        self.learn_button.setToolTip(
            "Read a PCSX savestate taken in this area and bind the objects "
            "standing still in it to the models the game was drawing them "
            "with.")
        self.learn_button.clicked.connect(self._learn)
        self.keep_button = QPushButton("Keep models", self)
        self.keep_button.setToolTip(
            "Write the model each object is set to into "
            "labels/placements.json, so this area opens with them next "
            "time. What a savestate teaches is a good start and not always "
            "right - two objects standing on the same spot are told apart "
            "by eye, not by matching - so a correction made here is worth "
            "keeping.")
        self.keep_button.clicked.connect(self._keep_models)
        self.save_button = QPushButton("Save overlay as...", self)
        self.save_button.setToolTip(
            "Write a copy of this area's Axx.BIN with the positions and "
            "angles as they are here. Only those bytes change.")
        self.save_button.clicked.connect(self._save_overlay)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.learn_button)
        buttons.addWidget(self.keep_button)
        buttons.addWidget(self.save_button)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(QLabel("Area", self))
        top.addWidget(self.area_box, 1)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(top)
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
            "A whole area: its room, its background, and everything the "
            "overlay places in it - click an object to select it, drag it "
            "to move it"))
        layout.addWidget(splitter, 1)
        self._enable(False)

    # --- the disc -----------------------------------------------------

    def set_disc(self, dat_path, idx_path, overlay_for_area, vram_for_area,
                 area_names=None):
        """Point the tab at an open disc. `overlay_for_area` and
        `vram_for_area` are MainWindow's - the Level Editor has no
        business working out where an Axx.BIN lives or how to decompress
        a VRAM chunk when the window already knows both."""
        self.dat_path = dat_path
        self.idx_path = idx_path
        self.overlay_for_area = overlay_for_area
        self.vram_for_area = vram_for_area
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
        for widget in (self.table, self.model_box, self.learn_button,
                       self.keep_button, self.save_button, *self.boxes.values()):
            widget.setEnabled(on)

    # --- loading an area ----------------------------------------------

    def _on_area_changed(self, _index):
        if self._filling:
            return
        chunk = self.area_box.currentData()
        if chunk is None or not self.dat_path:
            return
        self.load_area(chunk)

    def load_area(self, chunk):
        self._stop_cycling()
        overlay = self.overlay_for_area(chunk)
        scene = LevelScene().load(self.dat_path, self.idx_path, chunk, overlay)
        self.scene = scene

        # The VRAM has to be in place before the scene is prepared - the
        # palettes are cut out of it while the buffers are built - and it
        # needs AREA_01 merged in, which is where the character models'
        # texture pages live (see gui/smst/smst_parser.py).
        vram = self.vram_for_area(chunk)
        from gui.vram_viewer import vram_index_image
        self.viewer.set_vram(vram, vram_index_image(vram) if vram else None)
        self.viewer.export_name = f"AREA_{chunk:02X}"
        self.viewer.load_scene(scene)
        self.viewer.load_animations(overlay)
        self._load_background(scene, vram)
        self._populate()

        placed = sum(1 for i in scene.instances
                     if i.role == "object" and i.face_count)
        objects = sum(1 for i in scene.instances if i.role == "object")
        lines = [f"{objects} object(s) placed by "
                 f"{os.path.basename(overlay) if overlay else 'no overlay'}, "
                 f"{placed} of them with a known model."]
        lines.extend(scene.notes)
        self.summary.setText("\n".join(lines))

    def _load_background(self, scene, vram):
        """Render the area's BGMP, every phase of its palette cycling."""
        self._phases = []
        self._phase = 0
        entry = scene.by_id.get(BACKGROUND_ID)
        if not entry or not entry[1]:
            self.viewer.set_background(None)
            return
        try:
            background = load_bgmp(self.dat_path, scene.dat_start,
                                   entry[0], entry[1])
            textures = bgmp_render.BackgroundTextures(vram) if vram else None
            offset = bgmp_render.detect_page_y_offset(background, textures)
            palettes = background.palettes_used
            length = (textures.cycle_length(background, palettes)
                      if textures else 1)
            length = length if 1 < length <= MAX_PHASES else 1
            self._phases = [
                np.asarray(bgmp_render.render_background(
                    background, textures, offset, phase
                ).convert("RGB"), dtype=np.uint8)
                for phase in range(length)]
        except Exception as e:
            scene.notes.append(f"the background wouldn't draw: {e}")
            self.viewer.set_background(None)
            return
        self.viewer.set_background(self._phases[0] if self._phases else None)
        if len(self._phases) > 1:
            self._phase_timer.start(max(1000 // CYCLE_FPS, 1))

    def _stop_cycling(self):
        self._phase_timer.stop()

    def _next_phase(self):
        if len(self._phases) < 2:
            self._phase_timer.stop()
            return
        self._phase = (self._phase + 1) % len(self._phases)
        self.viewer.set_background(self._phases[self._phase])

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
            self.table.setItem(row, 0, name)
            self._fill_row(row, instance)
        self._filling = False
        self.table.clearSelection()
        self.viewer.set_hidden_groups(())
        self.model_box.clear()
        self._show_details(None)

    def _fill_row(self, row, instance):
        model = (f"id {instance.source[0]} g{instance.source[1]}"
                 if instance.source else
                 ("-" if instance.role == "room" else "unknown"))
        for column, text in ((1, model), (2, f"{instance.x:.0f}"),
                             (3, f"{instance.y:.0f}"), (4, f"{instance.z:.0f}"),
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
        for name, value in (("X", instance.x), ("Y", instance.y),
                            ("Z", instance.z), ("Angle", instance.angle)):
            self.boxes[name].setValue(value)
        self._filling = False

    # --- the selected instance ----------------------------------------

    def _instance(self, index):
        instances = self.scene.instances if self.scene else []
        if index is None or not 0 <= index < len(instances):
            return None
        return instances[index]

    def _show_details(self, index):
        instance = self._instance(index)
        if instance is None:
            self.details.setText("Click something in the view, or pick a row.")
            for box in self.boxes.values():
                box.setEnabled(False)
            self.model_box.setEnabled(False)
            return
        self.details.setText(instance.describe())
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
        for name, value in (("X", instance.x), ("Y", instance.y),
                            ("Z", instance.z), ("Angle", instance.angle)):
            self.boxes[name].setValue(value)
            self.boxes[name].setEnabled(
                instance.movable and not instance.authored)
        self._filling = False

    def _on_box_changed(self, _value):
        if self._filling:
            return
        instance = self._instance(self.viewer.selected)
        if instance is None or not instance.movable:
            return
        instance.x = self.boxes["X"].value()
        instance.y = self.boxes["Y"].value()
        instance.z = self.boxes["Z"].value()
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
        if instance.placement is not None:
            self.scene.bindings[instance.placement.key()] = source
        # The scene's arrays are laid out instance by instance, so a
        # different model means different geometry in the middle of
        # them: everything downstream of it moves, and the whole scene
        # is rebuilt rather than patched.
        self._rebuild()

    def _rebuild(self, frame=False):
        """Rebuild the scene around a change, leaving the camera, the
        selection and the hidden rows where the user had them."""
        selected = self.viewer.selected
        hidden = set(self.viewer.hidden_groups)
        self.viewer.load_scene(self.scene, frame=frame)
        self._populate()
        self.viewer.set_hidden_groups(hidden)
        self._filling = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.data(ROLE) in hidden:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False
        if selected is not None:
            self.viewer.select(selected)

    # --- learning and saving ------------------------------------------

    def _learn(self):
        if self.scene is None or not self.scene.overlay_path:
            QMessageBox.information(
                self, "Nothing to learn against",
                "This area has no overlay, so there is no object table to "
                "bind anything to.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a PCSX savestate taken in this area", "",
            "Savestate (*.000 *.001 *.002 *.003 *.004 *.state *.gz);;"
            "All files (*)")
        if not path:
            return
        files = [(file_id, offset, size)
                 for _i, file_id, offset, size in self.scene.files]
        try:
            learned = placement_module.bindings_from_state(
                path, self.dat_path, self.scene.dat_start, self.scene.dat_end,
                files, self.scene.placements, self.scene.overlay_path)
        except placement_module.PlacementError as e:
            QMessageBox.warning(self, "Couldn't read that state", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Couldn't read that state", f"{e}")
            return
        if not learned:
            QMessageBox.information(
                self, "Nothing learned",
                "Nothing in that state was standing exactly where a record "
                "puts it.\n\nObjects that walk about never will, and a state "
                "taken while the level was still loading has none of them "
                "standing anywhere yet. Try one taken in the room, near this "
                "area's fixed scenery.")
            return
        fresh = {k: v for k, v in learned.items()
                 if self.scene.bindings.get(k) != v}
        self.scene.bindings.update(learned)
        changed = self.scene.apply_bindings()
        self._rebuild()
        keep = QMessageBox.question(
            self, "Learned",
            f"Bound {len(learned)} object(s), {len(fresh)} of them new - "
            f"{changed} object(s) on screen changed.\n\n"
            f"Keep them in labels/placements.json, so this area opens with "
            f"them next time?")
        if keep == QMessageBox.StandardButton.Yes:
            self._store_bindings()

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
            if instance.role == "object" and instance.placement is not None:
                self.scene.bindings[instance.placement.key()] = instance.source
        if self._store_bindings():
            name = os.path.basename(self.scene.overlay_path)
            QMessageBox.information(
                self, "Kept",
                f"{len(placement_module.load_bindings(name, section=placement_module.CORRECTED))}"
                f" correction(s) for this area are now in "
                f"labels/placements.json, over the "
                f"{len(placement_module.load_bindings(name, section=placement_module.LEARNED))}"
                f" binding(s) read out of savestates.")

    def _store_bindings(self):
        """Put this area's models in labels/placements.json, leaving
        every other area's alone. True if it was written.

        They go in the corrections section: what a person settles on by
        looking at the room is worth more than what matching a savestate
        worked out, and only that section survives the correlation being
        run again. Only what differs from what was learned is kept, so
        the file stays a list of what somebody actually put right."""
        name = os.path.basename(self.scene.overlay_path)
        learned = placement_module.load_bindings(
            name, section=placement_module.LEARNED)
        path = placement_module.bindings_path()
        corrections = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for overlay in (json.load(f).get(placement_module.CORRECTED) or {}):
                    corrections[overlay] = placement_module.load_bindings(
                        overlay, section=placement_module.CORRECTED)
        except (OSError, ValueError):
            pass
        corrections[name] = {key: source
                             for key, source in self.scene.bindings.items()
                             if source is not None and learned.get(key) != source}
        try:
            placement_module.save_bindings(
                corrections, section=placement_module.CORRECTED)
        except OSError as e:
            QMessageBox.warning(self, "Couldn't write that",
                                f"labels/placements.json wouldn't save:\n\n{e}")
            return False
        return True

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
            with open(path, "wb") as f:
                f.write(placement_module.patch(data, self.scene.placements))
        except OSError as e:
            QMessageBox.critical(self, "Couldn't write it", str(e))
            return
        QMessageBox.information(
            self, "Saved",
            f"Wrote {len(self.scene.placements)} record(s) into a copy of "
            f"{name}. Only the positions and angles differ from the "
            f"original.")

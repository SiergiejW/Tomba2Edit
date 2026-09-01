"""ANMP viewer - the animation tables, played on a model.

Left, the frames: one row each, with the shape its tag declares. Right,
an SMST posed by whichever frame is selected, and a transport under it
to scrub or play through them.

An ANMP does not say which model it animates - nothing in the file
points at an SMST - so the model is chosen from a list of the ones on
the disc, defaulting to the first whose group count can carry the
frames' limbs. For Tomba's TANP that is his own model, whose 21 groups
cover the 17 limbs the frames rotate with four spares (see
gui/anmp/skeleton.py).
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QCompleter, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QSlider, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui import panel_title
from gui.anmp.anmp_parser import ANMPError, blend, load_anmp
from gui.anmp.skeleton import (
    SPARES, hierarchy_for, pose_transforms, rest_pivots, rest_pose)
from gui.anmp import game_rest
from gui.smst.smst_parser import load_smst
from gui.smst.smst_viewer import SMSTViewer

# The game runs at 30fps; the transport defaults there.
DEFAULT_FPS = 30

# Poses rendered per table frame. The game eases between frames rather
# than snapping, but nothing in the file says by how much - the frames
# are the whole of it - so this is a viewing aid with a mild default
# rather than a measurement. 1 turns it off and shows exactly what is
# stored.
DEFAULT_STEPS = 3

# How much of an animation's limbs a model has to have parts for before
# a pairing made on packing order alone is believed - see _fill_models.
GROUP_RATIO = 0.8


class ANMPViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.anmp = None
        self.model = None                 # the posed SMST's model dict
        self._candidates = []             # [(label, address, size), ...]
        self._pivots = None
        self._hierarchy = ()
        self._named_hierarchy = False
        self._measured_rest = False
        self._where = 0.0                 # position in table frames
        self._source = None               # (dat_path, address, size)
        self._skeletons = []              # (label, bytes) to read trees from
        self._area_membership = {}        # address -> {chunk_index, ...}
        self._current_area = None
        self._preferred = []              # (label, address, size), best first

        self.viewer = SMSTViewer()
        self.viewer.spread_action.setChecked(False)

        self.frames_table = QTableWidget(0, 4)
        self.frames_table.setHorizontalHeaderLabels(["Frame", "Limbs", "Tag", "Offset"])
        self.frames_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.frames_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.frames_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.frames_table.verticalHeader().setVisible(False)
        self.frames_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.frames_table.horizontalHeader().setStretchLastSection(True)
        self.frames_table.itemSelectionChanged.connect(self._on_frame_selected)

        self.limbs_table = QTableWidget(0, 4)
        self.limbs_table.setHorizontalHeaderLabels(["Limb", "X", "Y", "Z"])
        self.limbs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.limbs_table.verticalHeader().setVisible(False)
        self.limbs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.limbs_table.horizontalHeader().setStretchLastSection(True)

        self.model_box = QComboBox()
        self.model_box.setToolTip(
            "Which model to pose. An ANMP doesn't name one, so this is a "
            "choice, not a fact - the default is the model this ANMP's "
            "own area actually uses, or otherwise the first with enough "
            "groups for the frames' limbs. Type to search by name or "
            "offset.")
        # Editable + a substring completer turns the plain dropdown into
        # a search box without adding a second widget: typing "55F54"
        # or "zippo" narrows the popup the same way the tree's search
        # does, and choosing a match still fires currentIndexChanged
        # exactly as picking one with the mouse always did.
        self.model_box.setEditable(True)
        self.model_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = QCompleter(self.model_box.model(), self.model_box)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        self.model_box.setCompleter(completer)
        self.model_box.currentIndexChanged.connect(self._on_model_changed)

        self.info_label = panel_title.make_info_label("No animation loaded")

        # --- transport ---
        self.play_button = QPushButton("Play")
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._on_play_toggled)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self.show_position)

        self.fps_box = QSpinBox()
        self.fps_box.setRange(1, 60)
        self.fps_box.setValue(DEFAULT_FPS)
        self.fps_box.setSuffix(" fps")
        self.fps_box.valueChanged.connect(self._retime)

        self.steps_box = QSpinBox()
        self.steps_box.setRange(1, 16)
        self.steps_box.setValue(DEFAULT_STEPS)
        self.steps_box.setPrefix("blend x")
        self.steps_box.setToolTip(
            "How many poses to render between one table frame and the next, "
            "easing between them instead of snapping. The game does ease; "
            "how much isn't in the file, so this is a choice. 1 shows the "
            "frames exactly as stored.")
        self.steps_box.valueChanged.connect(self._on_steps_changed)

        self.frame_label = QLabel("-")
        self.frame_label.setMinimumWidth(130)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        self.rest_button = QPushButton("Reset pose")
        self.rest_button.setToolTip(
            "Show the model in its rest pose - the skeleton with no "
            "rotation applied, which is what the animation moves from.")
        self.rest_button.clicked.connect(self.show_rest)

        transport = QHBoxLayout()
        transport.setContentsMargins(8, 4, 8, 4)
        transport.addWidget(self.play_button)
        transport.addWidget(self.rest_button)
        transport.addWidget(self.slider, 1)
        transport.addWidget(self.frame_label)
        transport.addWidget(self.steps_box)
        transport.addWidget(self.fps_box)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(panel_title.make_panel_title("Frames"))
        left_layout.addWidget(self.frames_table, 3)
        left_layout.addWidget(panel_title.make_panel_title("This frame's limbs (degrees)"))
        left_layout.addWidget(self.limbs_table, 2)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(8, 4, 8, 0)
        model_row.addWidget(QLabel("Model:"))
        model_row.addWidget(self.model_box, 1)
        right_layout.addLayout(model_row)
        right_layout.addWidget(self.viewer, 1)
        right_layout.addLayout(transport)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 900])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 4, 10, 4)
        bottom.addWidget(self.info_label)
        layout.addLayout(bottom)

    # --- loading -----------------------------------------------------

    def set_skeleton_sources(self, sources):
        """Where to look for the game's own bone trees: [(label, bytes)].

        The area's overlay carries its characters' trees and MAIN.EXE
        carries the player's, so the owner supplies whichever it has -
        without them the viewer falls back to the measured rest pose."""
        self._skeletons = sources or []

    def load_anmp_data(self, dat_file_path, address, size, candidates=None,
                       vram_bytes=None, vram_image=None,
                       area_membership=None, current_area=None,
                       preferred=None):
        """Parse the animation at `address` and show it.

        `candidates` is [(label, address, size), ...] of the SMSTs on the
        disc, for the model chooser - see the module docstring on why
        this has to be offered rather than looked up. `preferred` is the
        same shape, best guess first, and is what the auto-pick actually
        goes on; see MainWindow._preferred_models for where that order
        comes from. `area_membership` ({address: {chunk_index, ...}})
        and `current_area` only matter for the fallback used when
        nothing is preferred or none of it loads."""
        self.play_button.setChecked(False)
        try:
            self.anmp = load_anmp(dat_file_path, address, size)
        except (ANMPError, OSError) as e:
            self.anmp = None
            self._clear(f"Not readable as an animation table: {e}")
            return False

        self._source = (dat_file_path, address, size)
        self._area_membership = area_membership or {}
        self._current_area = current_area
        self._preferred = list(preferred or [])
        self.viewer.set_vram(vram_bytes, vram_image)
        self._candidates = list(candidates or [])
        self._fill_frames()
        self._fill_models()
        self._rescale_slider()
        self.slider.setValue(0)
        # Opens on the rest pose rather than on frame 0. The rest pose
        # is the model as the skeleton alone lays it out, so it is the
        # thing to look at first: whether the right model got paired
        # with the right skeleton is visible in it directly, without an
        # animation on top to confuse a bad pairing with a badly
        # applied rotation. Picking any frame, or Play, leaves it.
        self.show_rest()
        self._update_info()
        return True

    def _clear(self, message):
        self.frames_table.setRowCount(0)
        self.limbs_table.setRowCount(0)
        self.slider.setMaximum(0)
        panel_title.set_info(self.info_label, message)

    def _fill_frames(self):
        self.frames_table.blockSignals(True)
        self.frames_table.setRowCount(len(self.anmp))
        for row, f in enumerate(self.anmp.frames):
            self.frames_table.setItem(row, 0, QTableWidgetItem(str(f.index)))
            limbs = f"{f.limb_count}" + (" + root" if f.root else "")
            self.frames_table.setItem(row, 1, QTableWidgetItem(limbs))
            tag = f"0x{f.tag:02X}" + ("  *" if f.flagged else "")
            item = QTableWidgetItem(tag)
            if f.flagged:
                item.setToolTip("bit 6 is set on this frame's pointer - what "
                                "it means isn't decoded")
            self.frames_table.setItem(row, 2, item)
            self.frames_table.setItem(row, 3, QTableWidgetItem(f"0x{f.offset:X}"))
        self.frames_table.blockSignals(False)

    def _fill_models(self):
        """Offer every SMST, and pick the one this animation belongs to.

        An ANMP does not say which model it animates, so the auto-pick
        is inference - see MainWindow._preferred_models, which works it
        out from the labels and from the order an area packs its files
        in, and hands the answers down in `preferred`.

        A pairing made on the names is taken as given. One made on the
        packing order gets a sanity check first, because an area packs
        plenty of things next to each other that are not a character
        and its animation: the shared NPC animation in the pipe area
        sits just below a five-group sea anemone, and without this
        would be posed on it. The check is deliberately loose - a model
        needs GROUP_RATIO of the limbs the frames usually rotate, not
        all of them. Across the 92 pairs this disc's labels can be
        checked against, the right model never has fewer than 0.89 of
        them, and demanding all of them costs three correct answers,
        while the anemone mispairing sits far below at 0.33.

        The group-count ranking below is only the fallback for when
        nothing was preferred, or none of it could be read."""
        self.model_box.blockSignals(True)
        self.model_box.clear()
        needed = max((f.limb_count for f in self.anmp.frames), default=0)
        counts = self.anmp.limb_counts
        usual = counts.most_common(1)[0][0] if counts else 0
        for label, address, size in self._candidates:
            self.model_box.addItem(label, (address, size))
        self.model_box.blockSignals(False)

        print(f"[ANMP] {len(self.anmp)} frames, largest frame needs {needed} "
              f"limbs. {len(self._candidates)} SMST candidate(s), "
              f"{len(self._preferred)} preferred.")

        for label, address, size, trusted in self._preferred:
            try:
                model = load_smst(self._source[0], address, size)
            except Exception:
                continue
            parts = len(model["groups"])
            if not trusted and parts < usual * GROUP_RATIO:
                print(f"[ANMP] skipping {label} - packed beside this "
                      f"animation but only {parts} groups for {usual} limbs")
                continue
            print(f"[ANMP] picked {label} (0x{address:X}, {parts} groups) - "
                  + ("named to match this animation" if trusted
                     else "packed with this animation"))
            self._select_model(label, address, size)
            self._use_model(model)
            return

        same_area, rest = [], []
        for entry in self._candidates:
            _label, address, _size = entry
            areas = self._area_membership.get(address, ())
            (same_area if self._current_area in areas else rest).append(entry)
        ranked = []
        for label, address, size in same_area:
            try:
                model = load_smst(self._source[0], address, size)
            except Exception:
                continue
            ranked.append((len(model["groups"]), label, address, size, model))
        ranked.sort(key=lambda r: r[0])

        for _groups, label, address, size, model in ranked:
            if len(model["groups"]) >= needed:
                print(f"[ANMP] picked {label} (0x{address:X}, "
                      f"{len(model['groups'])} groups) - fallback: used in "
                      "this area and big enough")
                self._select_model(label, address, size)
                self._use_model(model)
                return

        for label, address, size in rest:
            try:
                model = load_smst(self._source[0], address, size)
            except Exception:
                continue
            if len(model["groups"]) >= needed:
                print(f"[ANMP] picked {label} (0x{address:X}, "
                      f"{len(model['groups'])} groups) - fallback: not used "
                      "here, first elsewhere on disc that fits")
                self._select_model(label, address, size)
                self._use_model(model)
                return

        if self._candidates:
            print("[ANMP] nothing offered enough groups - showing the "
                  "first candidate anyway")
            self.model_box.setCurrentIndex(0)
            self._on_model_changed(0)

    def _select_model(self, label, address, size):
        """Show `address` as the chosen entry in the model list.

        The list is deduplicated by content, so it holds one row per
        distinct model and that row's address is whichever area's copy
        the tree walk reached first. A preferred model is a specific
        area's copy, so its address often is not the one listed even
        though the same bytes are - hence matching on the address and,
        when that fails, adding the row rather than leaving the box
        showing something other than what is on screen."""
        for index in range(self.model_box.count()):
            data = self.model_box.itemData(index)
            if data and data[0] == address:
                self.model_box.blockSignals(True)
                self.model_box.setCurrentIndex(index)
                self.model_box.blockSignals(False)
                return
        self.model_box.blockSignals(True)
        self.model_box.insertItem(0, label, (address, size))
        self.model_box.setCurrentIndex(0)
        self.model_box.blockSignals(False)

    def _on_model_changed(self, index):
        data = self.model_box.itemData(index)
        if not data or not self._source:
            return
        address, size = data
        try:
            self._use_model(load_smst(self._source[0], address, size))
        except Exception as e:
            print(f"Could not load the model to pose: {e}")

    def _use_model(self, model):
        self.model = model
        self.viewer.spread = False
        # Which skeleton is this character's is decided by how well each
        # candidate fits THIS model, not by bone count alone - an area's
        # overlay holds one per character and plenty are the same size,
        # so counting bones picks a stranger's proportions about as
        # often as not. See game_rest.fit for what "fits" measures.
        counts = self.anmp.limb_counts
        by_frequency = [count for count, _n in counts.most_common()] if counts else []
        chosen = game_rest.best_for(self._skeletons, model, by_frequency)
        if chosen:
            label, offset, bones, limbs, grade, seen = chosen
            print(f"[ANMP] skeleton: {seen} candidate(s) across limb counts "
                  f"{by_frequency} - using {label} 0x{offset:X} at {limbs} "
                  f"limbs, fit {grade:.2f}")
        else:
            bones, limbs = None, (by_frequency[0] if by_frequency else 0)
            print(f"[ANMP] skeleton: nothing matched limb counts "
                  f"{by_frequency} - falling back to the measured/flat rest pose")
        if bones is not None:
            self._hierarchy, self._named_hierarchy = game_rest.hierarchy(bones), True
        else:
            self._hierarchy, self._named_hierarchy = hierarchy_for(limbs)

        # Stand the model up first if its rest pose has been measured -
        # an SMST is packed, not assembled, so without this the limbs
        # would rotate about each other in a heap.
        # The spare parts - Tomba's mouth-open head and open hands - stand
        # in for a limb rather than joining it, so the game draws one or
        # the other. Hidden here, and switchable from the part list.
        self.viewer.hidden_groups = set(range(len(self._hierarchy),
                                              len(model["groups"])))
        if bones is not None:
            standing = game_rest.rest_pose(model, bones,
                                           SPARES.get(len(bones)))
        else:
            standing = (rest_pose(model, self._hierarchy)
                        if self._named_hierarchy else None)
        self._measured_rest = standing is not None
        if standing is not None:
            vertices, self._pivots = standing
            model = dict(model, vertices=vertices.tolist())
        else:
            self._pivots = rest_pivots(model["groups"], self._hierarchy)
        self.viewer.model_data = model
        self.viewer.prepare_buffers()
        self.viewer.frame_model()
        self.show_frame(self.slider.value())
        self._update_info()

    # --- transport ---------------------------------------------------

    @property
    def steps(self):
        return self.steps_box.value()

    def _rescale_slider(self):
        """The slider counts sub-steps, so changing the blend keeps the
        place in the animation rather than jumping.

        Off self._where rather than the slider: by the time this runs the
        step count has already changed, so reading the slider would
        divide by the new one and land somewhere else entirely."""
        if not self.anmp:
            return
        where = self._where
        self.slider.blockSignals(True)
        self.slider.setMaximum(max((len(self.anmp) - 1) * self.steps, 0))
        self.slider.setValue(int(round(where * self.steps)))
        self.slider.blockSignals(False)

    def position(self):
        """Where the transport is, in table frames, as a float."""
        return self._where

    def show_frame(self, index):
        """Jump to a whole frame - what the frame list selects."""
        self.slider.setValue(int(index) * self.steps)

    def show_rest(self):
        """Drop the animation and show the model in its rest pose - the
        skeleton laid out with no rotation anywhere.

        This is what an animation is applied ON TOP of, so it is worth
        being able to get back to on its own: a model that looks wrong
        here is wrong in the skeleton or the pairing, and one that looks
        right here but wrong once it moves is a rotation being applied
        wrongly. Telling those two apart otherwise means guessing."""
        if not self.model or self._pivots is None:
            return
        self.play_button.setChecked(False)
        still = [(0.0, 0.0, 0.0)] * len(self._hierarchy)
        transforms = pose_transforms(still, (0.0, 0.0, 0.0),
                                     self._hierarchy, self._pivots)
        self.viewer.set_pose(transforms, self._pivots)
        self.frames_table.clearSelection()
        self.frame_label.setText(f"rest / {len(self.anmp) if self.anmp else 0}")
        self.limbs_table.setRowCount(0)

    def show_position(self, sub):
        """Pose at `sub` sub-steps in: between two frames when the blend
        is on, exactly on one when it isn't."""
        if not self.anmp or not self.model or self._pivots is None:
            return
        steps = max(self.steps, 1)
        index, part = divmod(int(sub), steps)
        index = max(0, min(index, len(self.anmp) - 1))
        amount = part / steps
        frame = self.anmp.frames[index]
        following = (self.anmp.frames[index + 1]
                     if amount and index + 1 < len(self.anmp) else None)
        rotations, translation = (blend(frame, following, amount) if amount
                                  else (frame.rotations(), frame.translation()))
        transforms = pose_transforms(rotations, translation,
                                     self._hierarchy, self._pivots)
        self.viewer.set_pose(transforms, self._pivots)
        self._where = index + amount
        between = f" + {part}/{steps}" if part else ""
        self.frame_label.setText(f"{index + 1}{between} / {len(self.anmp)}")
        self._fill_limbs(frame, rotations, translation)

    def _on_steps_changed(self):
        self._rescale_slider()
        self._retime()
        self.show_position(self.slider.value())

    def _fill_limbs(self, frame, rotations=None, translation=None):
        import math
        rotations = frame.rotations() if rotations is None else rotations
        translation = frame.translation() if translation is None else translation
        rows = []
        if frame.root:
            x, y, z = translation
            rows.append(("root (move)", f"{x:.1f}", f"{y:.1f}", f"{z:.1f}"))
        for i, (x, y, z) in enumerate(rotations):
            name = self._hierarchy[i][0] if i < len(self._hierarchy) else f"limb {i}"
            rows.append((name, f"{math.degrees(x):7.1f}",
                         f"{math.degrees(y):7.1f}", f"{math.degrees(z):7.1f}"))
        self.limbs_table.setRowCount(len(rows))
        for r, cells in enumerate(rows):
            for c, text in enumerate(cells):
                self.limbs_table.setItem(r, c, QTableWidgetItem(text))

    def _on_frame_selected(self):
        rows = self.frames_table.selectionModel().selectedRows()
        if rows:
            self.show_frame(rows[0].row())

    def _on_play_toggled(self, playing):
        self.play_button.setText("Pause" if playing else "Play")
        if playing:
            self._retime()
        else:
            self._timer.stop()

    def _retime(self):
        # fps is table frames per second; the blend subdivides each of
        # them, so the tick rate scales with it and the animation still
        # takes the same time to play through.
        if self.play_button.isChecked():
            rate = self.fps_box.value() * max(self.steps, 1)
            self._timer.start(max(1000 // rate, 1))

    def _advance(self):
        if not self.anmp:
            return
        end = self.slider.maximum()
        self.slider.setValue(self.slider.value() + 1 if self.slider.value() < end else 0)

    # --- status ------------------------------------------------------

    def _update_info(self):
        if not self.anmp:
            return
        counts = self.anmp.limb_counts
        shape = ", ".join(f"{n} limbs x{c}" for n, c in sorted(counts.items()))
        flagged = sum(1 for f in self.anmp.frames if f.flagged)
        parts = [f"{len(self.anmp)} frames", shape]
        if flagged:
            parts.append(f"{flagged} with the undecoded bit 6 set")
        if self.model:
            parts.append(f"{len(self.model['groups'])} groups in the model")
            if self._measured_rest:
                parts.append("rest pose and joints measured")
            elif self._named_hierarchy:
                parts.append("hierarchy known, joints estimated")
            else:
                parts.append("no hierarchy - parts turn independently")
        panel_title.set_info(self.info_label, "  |  ".join(parts))

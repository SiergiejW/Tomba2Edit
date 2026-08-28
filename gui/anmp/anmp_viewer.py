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
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSlider, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui import panel_title
from gui.anmp.anmp_parser import ANMPError, load_anmp
from gui.anmp.skeleton import (
    hierarchy_for, pose_transforms, rest_pivots, rest_pose)
from gui.smst.smst_parser import load_smst
from gui.smst.smst_viewer import SMSTViewer

# The game runs at 30fps; the transport defaults there.
DEFAULT_FPS = 17


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
        self._source = None               # (dat_path, address, size)

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
            "choice, not a fact - the default is the first model with "
            "enough groups for the frames' limbs.")
        self.model_box.currentIndexChanged.connect(self._on_model_changed)

        self.info_label = panel_title.make_info_label("No animation loaded")

        # --- transport ---
        self.play_button = QPushButton("Play")
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._on_play_toggled)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self.show_frame)

        self.fps_box = QSpinBox()
        self.fps_box.setRange(1, 60)
        self.fps_box.setValue(DEFAULT_FPS)
        self.fps_box.setSuffix(" fps")
        self.fps_box.valueChanged.connect(self._retime)

        self.frame_label = QLabel("-")
        self.frame_label.setMinimumWidth(110)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        transport = QHBoxLayout()
        transport.setContentsMargins(8, 4, 8, 4)
        transport.addWidget(self.play_button)
        transport.addWidget(self.slider, 1)
        transport.addWidget(self.frame_label)
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

    def load_anmp_data(self, dat_file_path, address, size, candidates=None,
                       vram_bytes=None, vram_image=None):
        """Parse the animation at `address` and show it.

        `candidates` is [(label, address, size), ...] of the SMSTs on the
        disc, for the model chooser - see the module docstring on why
        this has to be offered rather than looked up."""
        self.play_button.setChecked(False)
        try:
            self.anmp = load_anmp(dat_file_path, address, size)
        except (ANMPError, OSError) as e:
            self.anmp = None
            self._clear(f"Not readable as an animation table: {e}")
            return False

        self._source = (dat_file_path, address, size)
        self.viewer.set_vram(vram_bytes, vram_image)
        self._candidates = list(candidates or [])
        self._fill_frames()
        self._fill_models()
        self.slider.setMaximum(max(len(self.anmp) - 1, 0))
        self.slider.setValue(0)
        if self.frames_table.rowCount():
            self.frames_table.selectRow(0)
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
        """Offer every SMST, best fit first - the models with enough
        groups for these frames' limbs, largest limb count first."""
        self.model_box.blockSignals(True)
        self.model_box.clear()
        needed = max((f.limb_count for f in self.anmp.frames), default=0)
        for label, address, size in self._candidates:
            self.model_box.addItem(label, (address, size))
        self.model_box.blockSignals(False)

        # Pick the first that can carry the limbs.
        for i, (_label, address, size) in enumerate(self._candidates):
            try:
                model = load_smst(self._source[0], address, size)
            except Exception:
                continue
            if len(model["groups"]) >= needed:
                self.model_box.setCurrentIndex(i)
                self._use_model(model)
                return
        if self._candidates:
            self.model_box.setCurrentIndex(0)
            self._on_model_changed(0)

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
        # The shape most of the frames have, not the largest: Tomba's
        # table is 1147 seventeen-limb frames and five nineteen-limb
        # ones, and it is the seventeen that his skeleton is.
        counts = self.anmp.limb_counts
        limbs = counts.most_common(1)[0][0] if counts else 0
        self._hierarchy, self._named_hierarchy = hierarchy_for(limbs)

        # Stand the model up first if its rest pose has been measured -
        # an SMST is packed, not assembled, so without this the limbs
        # would rotate about each other in a heap.
        # The spare parts - Tomba's mouth-open head and open hands - stand
        # in for a limb rather than joining it, so the game draws one or
        # the other. Hidden here, and switchable from the part list.
        self.viewer.hidden_groups = set(range(len(self._hierarchy),
                                              len(model["groups"])))
        standing = rest_pose(model, self._hierarchy) if self._named_hierarchy else None
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

    def show_frame(self, index):
        if not self.anmp or not self.model or self._pivots is None:
            return
        if not 0 <= index < len(self.anmp):
            return
        frame = self.anmp.frames[index]
        transforms = pose_transforms(frame, self._hierarchy, self._pivots)
        self.viewer.set_pose(transforms, self._pivots)
        self.frame_label.setText(f"{index + 1} / {len(self.anmp)}")
        self._fill_limbs(frame)
        if self.slider.value() != index:
            self.slider.blockSignals(True)
            self.slider.setValue(index)
            self.slider.blockSignals(False)

    def _fill_limbs(self, frame):
        import math
        rows = []
        if frame.root:
            x, y, z = frame.translation()
            rows.append(("root (move)", f"{x:.0f}", f"{y:.0f}", f"{z:.0f}"))
        for i, (x, y, z) in enumerate(frame.rotations()):
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
        if self.play_button.isChecked():
            self._timer.start(max(1000 // self.fps_box.value(), 1))

    def _advance(self):
        if not self.anmp:
            return
        self.show_frame((self.slider.value() + 1) % len(self.anmp))

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

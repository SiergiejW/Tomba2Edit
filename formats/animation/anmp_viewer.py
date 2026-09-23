"""ANMP viewer - the animation tables, played on a model.

Left, either the raw poses or steps of a decoded game animation. Right,
an SMST posed by the selected step and a transport to scrub or play it.
sequences.py reads clip boundaries, timing and links from MAIN.EXE or
area overlays; raw ANMP order remains available as a separate mode.

An ANMP does not say which model it animates - nothing in the file
points at an SMST - so the model is chosen from a list of the ones on
the disc, defaulting to the first whose group count can carry the
frames' limbs. For Tomba's TANP that is his own model, whose 21 groups
cover the 17 limbs the frames rotate with four spares (see
formats/animation/anmp_skeleton.py).
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QSlider, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from formats.models import gltf_export
from formats.animation import pairings
from formats.animation import skeleton_tree as skeleton
from gui.widgets import export_dialog
from gui.widgets import panel_title
from gui.widgets import view_gif
from formats.audio.transport_icons import set_glyph
from formats.animation.anmp_parser import (
    BITS_PER_VALUE, VALUES_PER_LIMB, WIDE_SLOT_BYTES, ANMPError, blend,
    load_anmp)
from formats.animation.anmp_skeleton import (
    SPARES, hierarchy_for, pose_transforms, rest_pivots, rest_pose)
from formats.animation import game_rest
from formats.animation import sequences
from formats.models.smst_parser import load_smst
from formats.models.smst_viewer import SMSTViewer
import struct
import time

# HOW FAST AN ANIMATION REALLY RUNS
#
# There is no single answer, and the disc says so. An animation is a
# sequence of 8-byte entries - a u16 frame index at +0, and at +6 a
# halfword whose low 12 bits are how many TICKS to hold that frame.
# f_AdvanceActorSkeletalAnimation counts that down one a tick, and while
# it is above zero it steps the tween instead of loading a new pose. The
# main scheduler waits two vblanks per game update (DAT_1f800235 = 2),
# so those ticks run at 30 Hz NTSC or 25 Hz PAL.
#
# Read off the ghost guard's own table (A06.BIN 0x44B5C, the address its
# code hands to f_StartActorSkeletalAnimationBlend), and off the other
# characters' candidates:
#
#     hold  rate   where
#      1t   30fps  the ghost guard's attacks - a new pose every update
#      2t   15fps
#      3t   10fps  Tabby, all 44 of her entries
#      4t   7.5fps the commonest by far - 117 of 130 in one table,
#                  135 of 159 in the anemone's, 89 of 105 in a koma pig's
#      6t    5fps  the ghost guard's slow idles
#     8-12t        rare, long holds
#
# So the pair below is one point on a curve rather than a constant, and
# what stays fixed is their PRODUCT: fps x blend = 30 on NTSC, because the blend
# is spread across exactly the ticks the frame is held (FUN_80075ff8
# divides the step by the tick count, FUN_80075f0c adds one a tick).
# 7.5 x 4 is the disc's commonest hold. 5 x 6 is right for a slow idle,
# 30 x 1 for an attack - and at a
# 1-tick hold there is no tween at all.
DEFAULT_FPS = 10
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
        self._export_model = None     # the model as the file has it
        self._export_bones = None     # the skeleton it is posed on
        self.export_name = None       # the tree row's name, for the dialog
        self._skeleton_choices = []   # every table that could fit, ranked
        self._type_names = {}         # signature -> "Type A"
        # Pairings already judged by eye. Filled in by MainWindow once
        # the disc is open, since resolving them to bone tables needs
        # the overlays - see formats/animation/pairings.py.
        self._approvals = {}
        self._variations = {}         # spare group -> the limb it replaces
        self._spanning = {}           # bone -> the group long enough for it
        self._hierarchy = ()
        self._named_hierarchy = False
        self._measured_rest = False
        self._where = 0.0                 # position in table frames
        self._source = None               # (dat_path, address, size)
        self._skeletons = []              # (label, bytes) to read trees from
        self._area_membership = {}        # address -> {chunk_index, ...}
        self._current_area = None
        self._preferred = []              # (label, address, size), best first
        self._model_skeletons = {}        # SMST address -> code-derived bindings
        self._banks = []
        self._clip = None
        self._frames_by_id = {}
        self._sequence_base = None
        self._loading_anmp = False
        self._model_vram_provider = None
        self._clip_scales = []
        self._automatic_parts = {}        # bone -> group, from actor code
        self._selected_model_address = None
        self._sea_variants = {}
        self._sea_variant_mode = None
        self._switching_variant = False
        self._switching_actor_rig = False
        self._base_actor_bones = None
        self._inherit_scales = True
        self._clock_last = None

        self.viewer = SMSTViewer()
        self.viewer.spread_action.setChecked(False)
        # The export lives on the embedded viewer's toolbar, the same
        # icon in the same place as every other view has it - but wired
        # to this one's export, which writes the skeleton and the
        # animation as well as the model.
        self.viewer.export_action.triggered.disconnect()
        self.viewer.export_action.triggered.connect(self.export_gltf)
        self.viewer.export_action.setToolTip(
            "Write the posed model out as a rigged, animated glTF - the "
            "selected game clip (one traversal, with recorded timing), or "
            "all poses in raw mode, with the palettes baked into textures.")

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

        self.bank_box = QComboBox()
        self.bank_box.currentIndexChanged.connect(self._on_bank_changed)
        self.clip_box = QComboBox()
        self.clip_box.currentIndexChanged.connect(self._on_clip_changed)
        self.clip_box.setEnabled(False)
        self.sequence_info = QLabel("Open an ANMP to browse its poses.")
        self.sequence_info.setWordWrap(True)
        self.find_sequences_button = QPushButton("Find NPC sequence candidates")
        self.find_sequences_button.setToolTip(
            "Search this area's overlay for sequence tables compatible with "
            "these poses. Compatibility does not prove which NPC owns a table.")
        self.find_sequences_button.clicked.connect(self._find_sequences)
        self.find_sequences_button.setEnabled(False)

        self.limbs_table = QTableWidget(0, 4)
        self.limbs_table.setHorizontalHeaderLabels(["Limb", "X", "Y", "Z"])
        self.limbs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.limbs_table.verticalHeader().setVisible(False)
        # NOT ResizeToContents. This table is rewritten once per frame,
        # and that mode re-measures every column across every row on each
        # cell written - see _fill_limbs for what it cost.
        self.limbs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.limbs_table.horizontalHeader().setStretchLastSection(True)
        self.limbs_table.itemSelectionChanged.connect(self._on_limb_selected)
        # The frame the limb rows were filled from, so a picked
        # row can be named by where its angles live in the DAT.
        self._limbs_frame = None

        self.model_box = QComboBox()
        self.model_box.setToolTip(
            "Models associated with this animation by labels, packing, or "
            "the actor-initialization code. Unrelated SMST files are omitted.")
        self.model_box.currentIndexChanged.connect(self._on_model_changed)

        self.skeleton_box = QComboBox()
        self.skeleton_box.setToolTip(
            "Which bone table to pose on. When game-code bindings are "
            "available this contains only tables that the game actually "
            "passes with the selected model; older builds fall back to "
            "geometric matching.")
        self.skeleton_box.currentIndexChanged.connect(self._on_skeleton_changed)

        self.variation_box = QComboBox()
        self.variation_box.setToolTip(
            "Spare parts the model carries but the animation never "
            "moves - Tomba's mouth-open head and open hands, the ghost "
            "guard's second set of tongue segments. The game draws one "
            "or the other, so picking one here hides the part it stands "
            "in for.")
        self.variation_box.currentIndexChanged.connect(self._on_variation_changed)

        self.first_group_box = QSpinBox()
        self.first_group_box.setPrefix("from part ")
        self.first_group_box.setRange(0, 0)
        self.first_group_box.setToolTip(
            "Which part of the model the animation's first limb drives. "
            "Normally 0 - limb 0 is part 0 - but an animation can drive "
            "one object inside an asset pack: the Machine Animation's "
            "limbs are an oven's base and lid, parts 12 and 13 of a "
            "twenty-part room, and at 0 it animates the walls instead.")
        self.first_group_box.valueChanged.connect(self._on_first_group_changed)

        self.info_label = panel_title.make_info_label("No animation loaded")

        # --- transport ---
        self.play_button = QPushButton()
        set_glyph(self.play_button, "play")
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._on_play_toggled)

        self.autoplay_box = QCheckBox("Autoplay")
        self.autoplay_box.setChecked(True)
        self.autoplay_box.setToolTip(
            "Start playback when an ANMP opens or another animation is "
            "selected. Turning this off does not interrupt a clip already "
            "playing.")

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self.show_position)
        self.slider.sliderPressed.connect(self._reset_clock)
        self.slider.sliderReleased.connect(self._reset_clock)

        self.fps_box = QSpinBox()
        self.fps_box.setRange(1, 60)
        self.fps_box.setValue(DEFAULT_FPS)
        self.fps_box.setSuffix(" fps")
        self.fps_box.setToolTip(
            "Table frames a second - how often a NEW pose is loaded, not "
            "how often the screen is drawn.\n\n"
            "The game updates once per two vblanks, so the real NTSC rates "
            "are 30 divided by the hold: 30, 15, 10, 7.5, 5. Its "
            "commonest hold is 4 ticks, or 7.5 new poses/second. Set blend "
            "to match (fps x blend = 30) for raw playback with the same "
            "update cadence as the game.")
        self.fps_box.valueChanged.connect(self._retime)

        self.steps_box = QSpinBox()
        self.steps_box.setRange(1, 16)
        self.steps_box.setValue(DEFAULT_STEPS)
        self.steps_box.setPrefix("blend x")
        self.steps_box.setToolTip(
            "How many poses to render between one table frame and the next, "
            "easing between them instead of snapping.\n\n"
            "The game holds each frame for a number of ticks written beside "
            "it and eases across exactly those game updates - so keep blend "
            "x fps = 30 on NTSC. 15fps x2 is a common hold, 5fps x6 a slow "
            "idle, 30fps x1 an attack (no easing at "
            "all). 1 shows the frames exactly as stored.")
        self.steps_box.valueChanged.connect(self._on_steps_changed)

        self.frame_label = QLabel("-")
        self.frame_label.setMinimumWidth(130)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)

        self.rest_button = QPushButton("Reset pose")
        self.rest_button.setToolTip(
            "Show the model in its rest pose - the skeleton with no "
            "rotation applied, which is what the animation moves from.")
        self.rest_button.clicked.connect(self.show_rest)
        self.gif_button = QPushButton("Save GIF...")
        self.gif_button.setToolTip(
            "Record the selected game animation with its real tick holds and "
            "part swaps, or all raw ANMP poses at the Raw playback rate.")
        self.gif_button.clicked.connect(self.save_gif)

        transport = QVBoxLayout()
        transport.setContentsMargins(8, 4, 8, 4)
        transport.setSpacing(3)
        timeline = QHBoxLayout()
        timeline.setContentsMargins(0, 0, 0, 0)
        timeline.addWidget(self.play_button)
        timeline.addWidget(self.autoplay_box)
        timeline.addWidget(self.rest_button)
        timeline.addWidget(self.gif_button)
        timeline.addWidget(self.slider, 1)
        timeline.addWidget(self.frame_label)
        details = QHBoxLayout()
        details.setContentsMargins(0, 0, 0, 0)
        details.addWidget(self.steps_box)
        details.addWidget(self.fps_box)
        details.addWidget(self.info_label, 1)
        transport.addLayout(timeline)
        transport.addLayout(details)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(panel_title.make_panel_title("Animations / poses"))
        left_layout.addWidget(self.bank_box)
        left_layout.addWidget(self.clip_box)
        left_layout.addWidget(self.sequence_info)
        # Sequence discovery is automatic.  Keep the diagnostic rescan
        # action available to code, but do not make normal users click it
        # every time merely to reveal data that was already found.
        self.find_sequences_button.setVisible(False)
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
        rig_row = QHBoxLayout()
        rig_row.setContentsMargins(8, 4, 8, 0)
        rig_row.addWidget(QLabel("Skeleton:"))
        rig_row.addWidget(self.skeleton_box, 2)
        rig_row.addWidget(QLabel("Variation:"))
        rig_row.addWidget(self.variation_box, 1)
        rig_row.addWidget(self.first_group_box)
        right_layout.addLayout(rig_row)
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

    # --- loading -----------------------------------------------------

    def set_approvals(self, approved):
        """{(animation, model): (bones, table)} already judged by eye."""
        self._approvals = dict(approved or {})

    def set_skeleton_types(self, names):
        """{signature: "Type A"} - the disc's shape catalogue, so the
        chooser can name a skeleton by what kind of rig it is rather
        than only by where it sits."""
        self._type_names = dict(names or {})

    def set_skeleton_sources(self, sources):
        """Where to look for the game's own bone trees: [(label, bytes)].

        The area's overlay carries its characters' trees and MAIN.EXE
        carries the player's, so the owner supplies whichever it has -
        without them the viewer falls back to the measured rest pose."""
        self._skeletons = sources or []

    def load_anmp_data(self, dat_file_path, address, size, candidates=None,
                       vram_bytes=None, vram_image=None,
                       area_membership=None, current_area=None,
                       preferred=None, resource_id=None, overlay_base=None,
                       overlay_path=None, model_skeletons=None,
                       tick_rate=30, model_vram_provider=None):
        """Parse the animation at `address` and show it.

        `candidates` is [(label, address, size), ...] of the SMSTs on the
        disc, for the model chooser - see the module docstring on why
        this has to be offered rather than looked up. `preferred` is the
        same shape, best guess first, and is what the auto-pick actually
        goes on; see MainWindow._preferred_models for where that order
        comes from. `area_membership` ({address: {chunk_index, ...}})
        and `current_area` only matter for the fallback used when
        nothing is preferred or none of it loads."""
        self._loading_anmp = True
        self.play_button.setChecked(False)
        self._clip = None
        self._where = 0.0
        self.model = None
        self._pivots = None
        self._sequence_base = overlay_base
        self._sequence_overlay_path = overlay_path
        self._tick_rate = tick_rate
        try:
            self.anmp = load_anmp(dat_file_path, address, size)
        except (ANMPError, OSError) as e:
            self.anmp = None
            self._loading_anmp = False
            self._clear(f"Not readable as an animation table: {e}")
            return False

        # Stop the transport first. Loading while it runs frames the
        # new model against a pose the timer is still moving, which is
        # what made Frame Model land slightly off on the next
        # animation opened.
        self.play_button.setChecked(False)
        self._timer.stop()
        self._source = (dat_file_path, address, size)
        self._area_membership = area_membership or {}
        self._current_area = current_area
        self._preferred = list(preferred or [])
        self._model_skeletons = dict(model_skeletons or {})
        self._model_vram_provider = model_vram_provider
        self._resource_id = resource_id
        self.viewer.set_vram(vram_bytes, vram_image)
        self._candidates = list(candidates or [])
        self._frames_by_id = {f.index: f for f in self.anmp.frames}
        verified_bank = sequences.tomba_bank(
            self._skeletons, self.anmp, resource_id)
        self._banks = ([verified_bank] if verified_bank else
                       self._sequence_candidates())
        self.bank_box.blockSignals(True)
        self.bank_box.clear()
        for candidate_bank in self._banks:
            self.bank_box.addItem(candidate_bank.label, candidate_bank)
        if not self._banks:
            self.bank_box.addItem("No decoded animation table", None)
        self.bank_box.blockSignals(False)
        self.find_sequences_button.setText("Rescan animation tables")
        self.find_sequences_button.setEnabled(
            not verified_bank and self._sequence_base is not None)
        self._on_bank_changed(0)
        self._fill_frames()
        self._fill_models()
        self._rescale_slider()
        self.slider.setValue(0)
        # Raw ANMP is deliberately the initial view. Sequence discovery is
        # useful, but opening a resource must first expose the file itself
        # rather than silently choosing one inferred clip table.
        self.show_rest()
        self._loading_anmp = False
        if self.autoplay_box.isChecked():
            self.play_button.setChecked(True)
        self._update_info()
        return True

    def _clear(self, message):
        self._clip = None
        self._frames_by_id = {}
        self.bank_box.blockSignals(True)
        self.bank_box.clear()
        self.bank_box.addItem("No decoded animation table", None)
        self.bank_box.blockSignals(False)
        self.clip_box.clear()
        self.clip_box.setEnabled(False)
        self.find_sequences_button.setEnabled(False)
        self.sequence_info.setText(message)
        self.frames_table.setRowCount(0)
        self.limbs_table.setRowCount(0)
        self.slider.setMaximum(0)
        self._loading_anmp = False
        panel_title.set_info(self.info_label, message)

    def _fill_frames(self):
        self.frames_table.blockSignals(True)
        clip = self._clip
        frames = ([self._frames_by_id[s.pose] for s in clip.steps]
                  if clip else self.anmp.frames)
        self.frames_table.setColumnCount(6 if clip else 4)
        self.frames_table.setHorizontalHeaderLabels(
            ["Pose", "Limbs", "Tag", "Offset", "Ticks", "Transition"]
            if clip else ["Pose", "Limbs", "Tag", "Offset"])
        self.frames_table.setRowCount(len(frames))
        for row, f in enumerate(frames):
            self.frames_table.setItem(row, 0, QTableWidgetItem(str(f.index)))
            limbs = f"{f.limb_count}" + (" + root" if f.root else "")
            self.frames_table.setItem(row, 1, QTableWidgetItem(limbs))
            tag = f"0x{f.tag:02X}" + ("  *" if f.flagged else "")
            item = QTableWidgetItem(tag)
            if f.flagged:
                item.setToolTip("bit 6 is set: this frame carries a scale "
                                "for every limb as well as a rotation")
            self.frames_table.setItem(row, 2, item)
            self.frames_table.setItem(row, 3, QTableWidgetItem(f"0x{f.offset:X}"))
            if clip:
                step = clip.steps[row]
                self.frames_table.setItem(row, 4, QTableWidgetItem(str(step.ticks)))
                kind = "Tween" if step.blend else "Hold"
                if row == len(clip.steps) - 1:
                    kind += (f" / loop to step {clip.loop_start + 1}"
                             if clip.loop_start is not None else " / end")
                item = QTableWidgetItem(kind)
                item.setToolTip(
                    f"Step {row + 1} @ 0x{step.address:08X}\n"
                    f"Flags 0x{step.flags:04X}; payloads "
                    f"0x{step.payload2:04X}, 0x{step.payload4:04X}")
                self.frames_table.setItem(row, 5, item)
        self.frames_table.blockSignals(False)

    def _on_bank_changed(self, index):
        self.play_button.setChecked(False)
        bank = self.bank_box.itemData(index)
        self.clip_box.blockSignals(True)
        self.clip_box.clear()
        self.clip_box.addItem("All poses (raw ANMP)", None)
        if bank:
            for clip in bank.clips:
                name = f" — {clip.name}" if clip.name else ""
                poses = len({step.pose for step in clip.steps})
                self.clip_box.addItem(
                    f"0x{clip.id:02X}{name} ({len(clip.steps)} steps) "
                    f"({poses} poses)", clip)
        self.clip_box.blockSignals(False)
        self.clip_box.setEnabled(True)
        # Raw is deliberately first on initial load.  When the user changes
        # table, keep that mode until they pick a named animation; the table's
        # clips remain visible directly below it rather than disappearing.
        self.clip_box.setCurrentIndex(0)
        self._on_clip_changed(0)

    def _on_clip_changed(self, index):
        self.play_button.setChecked(False)
        self._clip = self.clip_box.itemData(index) if index >= 0 else None
        if self._clip is None:
            self._restore_selected_sea_model()
        self._automatic_parts = self._parts_for_clip(self._clip)
        self._apply_clip_rig()
        self._refresh_visible_parts()
        self._clip_scales = (sequences.scale_states(
            self._clip, self._frames_by_id) if self._clip else [])
        self._where = 0.0
        self.steps_box.setEnabled(self._clip is None)
        self.fps_box.setEnabled(self._clip is None)
        if self._clip:
            bank = self.bank_box.currentData()
            clip = self._clip
            ending = (f"loops to step {clip.loop_start + 1}"
                      if clip.loop_start is not None else "ends on its last pose")
            caution = "" if bank.verified else "Candidate: NPC association unverified. "
            self.sequence_info.setText(
                f"{caution}{len(clip.steps)} steps, {clip.duration} ticks; {ending}. "
                f"Playback: {self._tick_rate} ticks/s.")
            self.sequence_info.setToolTip(
                f"Pointer table @ 0x{bank.address:08X}; first step @ "
                f"0x{clip.steps[0].address:08X}.\n"
                "Uses recorded pose IDs, holds, tween flags and links. "
                "Actor events and gameplay-driven transitions are not simulated. "
                "Candidate IDs are relative to the displayed table start.")
        else:
            self.sequence_info.setText(
                "All stored poses. No clip boundaries are implied by this order.")
            self.sequence_info.setToolTip("")
        if self.anmp:
            self._fill_frames()
            self._rescale_slider()
            self.slider.setValue(0)
            self.show_position(0)
            if not self._loading_anmp and self.autoplay_box.isChecked():
                self.play_button.setChecked(True)

    def _parts_for_clip(self, clip):
        """Exact actor-code part choices known for the selected model.

        Tomba's mapping is read from MAIN.EXE by sequences.tomba_bank.
        The Ghost Guard's A06 actor switches its nine tongue bones from
        groups 7..15 to 16..24 for animations 10..13.
        """
        label = self.model_box.currentText().casefold()
        # Raw mode still needs the normal short tongue selected explicitly;
        # otherwise the spanning heuristic can expose the long attack tongue.
        if "ghost guard" in label:
            start = 16 if clip is not None and 10 <= clip.id <= 13 else 7
            return {bone: start + bone - 7 for bone in range(7, 16)}
        if clip is None:
            return {}
        if "tomba" in label and clip.parts:
            return dict(clip.parts)
        return {}

    def _ghost_long_bones(self):
        """A06's alternate nine-bone layout for the extended tongue."""
        base = self._base_actor_bones
        if ("ghost guard" not in self.model_box.currentText().casefold()
                or self._current_area != 6 or not base or len(base) != 16):
            return None
        from formats.animation import skeleton_tree as skeleton_reader
        for label, data in self._skeletons:
            if label == "MAIN.EXE":
                continue
            try:
                short = skeleton_reader.read_table(data, 0x3923C, 16)
                if tuple(map(tuple, short)) != tuple(map(tuple, base)):
                    continue
                long_tail = skeleton_reader.read_table(data, 0x391F4, 9)
                return [tuple(row) for row in base[:7]] + [
                    tuple(row) for row in long_tail]
            except (IndexError, struct.error):
                continue
        return None

    def _apply_clip_rig(self):
        """Switch actor-specific rest layouts which accompany part swaps."""
        if not self.model or not self._base_actor_bones:
            return
        label = self.model_box.currentText().casefold()
        if "ghost guard" not in label:
            return
        long = bool(self._clip and 10 <= self._clip.id <= 13)
        target = self._ghost_long_bones() if long else self._base_actor_bones
        if not target or target == self._export_bones:
            return
        self._switching_actor_rig = True
        try:
            self._pose_on(self.model, target, len(target),
                          frame_model=False, show_position=False)
        finally:
            self._switching_actor_rig = False

    def _refresh_visible_parts(self):
        if not self.model:
            return
        total = len(self.model["groups"])
        first = self.viewer.pose_first_group
        keep = self._keep_groups(first)
        spare = self.variation_box.currentData()
        if spare is not None and spare in self._variations:
            keep.add(spare)
            keep.discard(first + self._variations[spare])
        self.viewer.hidden_groups = set(range(total)) - keep
        self.viewer.prepare_buffers()
        self.viewer.update()

    def _sequence_candidates(self):
        """Automatically rank the overlay's sequence tables for this ANMP.

        A real table is a contiguous run of pointers to complete eight-byte
        sequences. Compatibility alone can find several actors sharing one
        pose archive, so the table using the greatest number of this ANMP's
        poses leads. The remaining compatible tables stay available rather
        than being discarded.
        """
        if self._sequence_base is None and self._sequence_overlay_path:
            import os
            from formats.animation import clut_anim
            self._sequence_base = clut_anim.folder_base(
                os.path.dirname(self._sequence_overlay_path))
        if self._sequence_base is None:
            return []
        poses = set(self._frames_by_id)
        found = []
        sources = list(self._skeletons)
        if (self._sequence_overlay_path
                and not any(label == "overlay" for label, _data in sources)):
            try:
                with open(self._sequence_overlay_path, "rb") as source:
                    sources.insert(0, ("overlay", source.read()))
            except OSError:
                pass
        for label, data in sources:
            source_base = None
            if label == "overlay":
                source_base = self._sequence_base
            elif label == "MAIN.EXE" and data.startswith(b"PS-X EXE"):
                import struct
                source_base = struct.unpack_from("<I", data, 0x18)[0] - 0x800
            if source_base is None:
                continue
            candidates = sequences.candidate_banks(
                data, source_base, poses, label=label,
                # Four-pose robe banks genuinely have one animation.
                # Larger ANMPs require a run of at least three pointers to
                # keep isolated coincidental words out of the chooser.
                min_clips=1 if len(poses) <= 4 else 3)
            for candidate in candidates:
                candidate.source = label
            found.extend(candidates)

        def score(bank):
            used = {step.pose for clip in bank.clips for step in clip.steps}
            steps = sum(len(clip.steps) for clip in bank.clips)
            local = getattr(bank, "source", "") == "overlay"
            return len(used), int(local), len(bank.clips), steps

        found.sort(key=lambda bank: (*score(bank), -bank.address), reverse=True)
        for index, candidate in enumerate(found):
            used, _local, clips, _steps = score(candidate)
            marker = " — best match" if index == 0 else ""
            source = getattr(candidate, "source", "Overlay")
            candidate.label = (f"{source} animations @ 0x{candidate.address:08X} — "
                               f"{clips} clips, {used}/{len(poses)} poses"
                               f"{marker}")
        return found

    def _find_sequences(self):
        if not self.anmp:
            return
        self.play_button.setChecked(False)
        found = self._sequence_candidates()
        if self._sequence_base is None:
            self.sequence_info.setText(
                "Could not identify the overlay's load address; raw poses are available.")
            return
        self.find_sequences_button.setEnabled(False)
        self._banks = found
        self.bank_box.blockSignals(True)
        self.bank_box.clear()
        for bank in found:
            self.bank_box.addItem(bank.label, bank)
        if not found:
            self.bank_box.addItem("No decoded animation table", None)
        self.bank_box.blockSignals(False)
        if found:
            self.bank_box.setCurrentIndex(0)
            self._on_bank_changed(0)
        else:
            self.sequence_info.setText(
                "No compatible sequence table found in this area's overlay. "
                "The raw poses remain available.")

    def _fill_models(self):
        """Offer plausible SMSTs and select the strongest association.

        Named/approved associations lead. After those, a candidate is
        offered only when the area's executable code initializes that
        exact model file with a limb count present in this ANMP. Packing
        adjacency remains a fallback for assets (notably trail files)
        that the area file table cannot name.
        """
        self.model_box.blockSignals(True)
        self.model_box.clear()
        needed = max((f.limb_count for f in self.anmp.frames), default=0)
        counts = self.anmp.limb_counts
        usual = counts.most_common(1)[0][0] if counts else 0
        limb_counts = set(counts)
        exact_addresses = {
            address for address, bindings in self._model_skeletons.items()
            if any(binding.limb_count in limb_counts for binding in bindings)
        }
        offered = []
        seen = set()

        def offer(label, address, size):
            if address not in seen:
                seen.add(address)
                offered.append((label, address, size))

        # Preserve all explicit/name matches, then add only code-compatible
        # models from this area. This turns a disc-wide random list into a
        # short list of actual game associations.
        trusted_preferred = [entry for entry in self._preferred if entry[3]]
        for label, address, size, _trusted in (
                trusted_preferred or self._preferred):
            offer(label, address, size)
        for label, address, size in self._candidates:
            if address in exact_addresses:
                offer(label, address, size)
        if not offered:
            for label, address, size in self._candidates:
                if self._current_area in self._area_membership.get(address, ()):
                    offer(label, address, size)
        for label, address, size in offered:
            self.model_box.addItem(label, (address, size))
            source = ("Linked to its skeleton by actor initialization code."
                      if address in exact_addresses else
                      "Associated by the resource labels or packing order.")
            self.model_box.setItemData(
                self.model_box.count() - 1, source,
                Qt.ItemDataRole.ToolTipRole)
        self.model_box.blockSignals(False)

        print(f"[ANMP] {len(self.anmp)} frames, largest frame needs {needed} "
              f"limbs. {len(offered)} relevant SMST candidate(s), "
              f"{len(exact_addresses)} linked by actor code.")

        ordered = []
        # Name/approved pairings are definitive; among structural matches,
        # prefer one that is also present in an initialization call.
        ordered.extend(p for p in self._preferred if p[3])
        ordered.extend(p for p in self._preferred
                       if not p[3] and p[1] in exact_addresses)
        candidate_by_address = {address: (label, address, size, False)
                                for label, address, size in offered}
        ordered.extend(candidate_by_address[address]
                       for _label, address, _size in offered
                       if address in exact_addresses)
        ordered.extend(p for p in self._preferred
                       if not p[3] and p[1] not in exact_addresses)
        tried = set()
        for label, address, size, trusted in ordered:
            if address in tried:
                continue
            tried.add(address)
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
                  + ("named to match this animation" if trusted else
                     "paired with its skeleton by actor code" if
                     address in exact_addresses else
                     "packed with this animation"))
            self._select_model(label, address, size)
            self._use_model(model, address)
            return

        ranked = []
        for label, address, size in offered:
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
                self._use_model(model, address)
                return

        if offered:
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
            self._use_model(load_smst(self._source[0], address, size), address)
        except Exception as e:
            print(f"Could not load the model to pose: {e}")

    def reload_model(self, address=None):
        """Read the posed model again.

        Called when an SMST is edited elsewhere - a part pasted in the
        SMST tab - so the animation shows the change without the model
        having to be picked out of the box a second time. `address`
        limits it to that model; None reloads whatever is showing.
        Returns whether anything was reloaded."""
        index = self.model_box.currentIndex()
        data = self.model_box.itemData(index)
        if not data or not self._source:
            return False
        if address is not None and data[0] != address:
            return False
        self._on_model_changed(index)
        return True

    def _use_model(self, model, address=None):
        self.model = model
        self._model_address = address
        if not self._switching_variant:
            self._selected_model_address = address
            self._sea_variant_mode = None
            self._find_sea_variants()
        self._automatic_parts = self._parts_for_clip(self._clip)
        self.viewer.spread = False
        if self._model_vram_provider is not None and address is not None:
            try:
                supplied = self._model_vram_provider(address)
                if supplied:
                    vram_bytes, vram_image, area = supplied
                    self.viewer.set_vram(vram_bytes, vram_image)
                    print(f"[ANMP] model textures: AREA_{area:02X} VRAM")
            except Exception as error:
                # A missing texture chunk must not make the geometry unusable.
                print(f"[ANMP] could not load this model's area VRAM: {error}")
        # Which skeleton is this character's is decided by how well each
        # candidate fits THIS model, not by bone count alone - an area's
        # overlay holds one per character and plenty are the same size,
        # so counting bones picks a stranger's proportions about as
        # often as not. See game_rest.fit for what "fits" measures.
        counts = self.anmp.limb_counts
        by_frequency = [count for count, _n in counts.most_common()] if counts else []

        # This is the authoritative path: model archive and skeleton table
        # appear together as arguments to the game's actor initializer.
        # Some asset archives are genuinely initialized with several rigs;
        # keep those few alternatives, but never mix in unrelated tables.
        bound = [binding for binding in self._model_skeletons.get(address, ())
                 if binding.limb_count in by_frequency]
        if bound:
            choices = []
            blocks = game_rest.mesh_blocks(model)
            for binding in bound:
                bones = [tuple(row) for row in binding.bones]
                grade = game_rest.fit(bones, model, blocks)
                choices.append((binding.source, binding.offset, bones,
                                binding.limb_count,
                                grade if grade is not None else float("inf")))
            choices.sort(key=lambda row: (row[4], row[1]))
            approved = self._approved(model, by_frequency)
            chosen = None
            if approved:
                wanted = tuple(tuple(int(v) for v in row)
                               for row in approved[2])
                chosen = next((row for row in choices
                               if tuple(tuple(v for v in bone)
                                        for bone in row[2]) == wanted), None)
            chosen = chosen or choices[0]
            label, offset, bones, limbs, grade = chosen
            self._inherit_scales = not (
                "sea anemone" in self.model_box.currentText().casefold()
                and label.upper().startswith("A04") and limbs == 5)
            if not self._inherit_scales:
                print("[ANMP] transform: Sea Anemone custom scale path "
                      "(scaled joint placement, non-inherited mesh width)")
            print(f"[ANMP] skeleton: EXACT game-code binding - {label} "
                  f"0x{offset:X}, {limbs} bones, fit {grade:.2f}")
            self._fill_skeletons(model, by_frequency, bones,
                                 exact_choices=choices)
            self._pose_on(model, bones, limbs)
            self._apply_clip_rig()
            return

        # A judgement already made by eye beats any measurement. See
        # formats/animation/pairings.py - fit is right about three quarters of
        # the time, and the quarter it misses is not something anything
        # in the files can settle.
        settled = self._approved(model, by_frequency)
        if settled:
            self._inherit_scales = True
            label, offset, bones, limbs = settled
            kind = self._type_names.get(game_rest.signature(bones), "Type ?")
            print(f"[ANMP] skeleton: APPROVED pairing - {kind}, {label} "
                  f"0x{offset:X} at {limbs} limbs, {game_rest.describe(bones)}")
            grade = game_rest.fit(bones, model)
            self._fill_skeletons(
                model, by_frequency, bones,
                exact_choices=[(label, offset, bones, limbs,
                                grade if grade is not None else float("inf"))],
                choice_origin="approved pairing")
            self._pose_on(model, bones, limbs)
            self._apply_clip_rig()
            return

        chosen = game_rest.best_for(self._skeletons, model, by_frequency)
        self._inherit_scales = True
        if chosen:
            label, offset, bones, limbs, grade, seen = chosen
            kind = self._type_names.get(game_rest.signature(bones), "Type ?")
            print(f"[ANMP] skeleton: {seen} candidate(s) across limb counts "
                  f"{by_frequency} - using {kind}, {label} 0x{offset:X} at "
                  f"{limbs} limbs, {game_rest.describe(bones)}, fit {grade:.2f}")
        else:
            bones, limbs = None, (by_frequency[0] if by_frequency else 0)
            print(f"[ANMP] skeleton: nothing matched limb counts "
                  f"{by_frequency} - falling back to the measured/flat rest pose")
        self._fill_skeletons(model, by_frequency, bones)
        self._pose_on(model, bones, limbs)
        self._apply_clip_rig()

    def _approved(self, model, limb_counts):
        """The skeleton someone signed off for this pairing, or None.

        Matched by the table's content rather than by where it sat when
        it was judged - see pairings.resolve. If this area has no table
        holding those records, nothing is returned and the measurement
        takes over, rather than posing on whatever occupies that offset
        here."""
        answer = pairings.find(self._approvals, self.export_name or "",
                               self.model_box.currentText())
        if not answer:
            return None
        bones, wanted = answer
        for label, data in self._skeletons or ():
            for offset in skeleton.tables_of_size(data, bones):
                table = skeleton.read_table(data, offset, bones)
                if tuple(tuple(int(v) for v in row) for row in table) != wanted:
                    continue
                return label, offset, table, bones
        return None

    def _fill_skeletons(self, model, limb_counts, chosen,
                        exact_choices=None, choice_origin=None):
        """List every table that could be this model's, best fit first.

        The automatic pick is a measurement and it is wrong sometimes -
        it cannot separate a character's costume variants, and the
        armadillo's own table fits worse than a stranger's - so the
        whole pool is offered rather than only the winner."""
        self._skeleton_choices = (list(exact_choices)
                                  if exact_choices is not None else
                                  game_rest.ranked(
                                      self._skeletons, model, limb_counts))
        self.skeleton_box.blockSignals(True)
        self.skeleton_box.clear()
        at = 0
        for i, (label, offset, bones, limbs, grade) in enumerate(
                self._skeleton_choices):
            mark = ""
            if chosen is not None and bones == chosen:
                mark, at = "* ", i
            score = "-" if grade == float("inf") else f"{grade:.2f}"
            # The type leads, because it is the part that says what kind
            # of thing this is - two candidates sharing one are the same
            # rig at different sizes, and choosing between those is a
            # different question from choosing between shapes.
            kind = self._type_names.get(game_rest.signature(bones), "Type ?")
            origin = (choice_origin or
                      ("game code" if exact_choices is not None else "inferred"))
            self.skeleton_box.addItem(
                f"{mark}{kind} - {limbs} bones, {game_rest.describe(bones)}"
                f", fit {score} - {label} 0x{offset:X} — {origin}", i)
        if not self._skeleton_choices:
            self.skeleton_box.addItem("no bone table found", -1)
        self.skeleton_box.setCurrentIndex(at)
        self.skeleton_box.blockSignals(False)

    def _on_skeleton_changed(self, index):
        """Re-pose on a table picked by hand instead of by fit."""
        which = self.skeleton_box.itemData(index)
        if which is None or which < 0 or not self.model:
            return
        label, offset, bones, limbs, grade = self._skeleton_choices[which]
        kind = self._type_names.get(game_rest.signature(bones), "Type ?")
        print(f"[ANMP] skeleton: chosen by hand - {kind}, {label} "
              f"0x{offset:X} at {limbs} bones, "
              f"{game_rest.describe(bones)}, fit {grade:.2f}")
        self._pose_on(self.model, bones, limbs)
        self._apply_clip_rig()

    def _fill_variations(self, model, animated):
        """The spare parts, and which limb each stands in for.

        A model carries more groups than the animation moves: Tomba has
        a mouth-open head and a pair of open hands, and the ghost guard
        has a whole second run of tongue segments. The game draws one or
        the other, so they are hidden by default and swapped in from
        here.

        Which limb a spare replaces is not written down, so it is found
        by shape - a spare sits where the part it stands in for sits,
        both being modelled around their own origin, so the limb whose
        bounding box is nearest the spare's is the one it replaces.
        That reads the ghost's second tongue onto its first without
        anything being told about ghosts."""
        groups = model.get("groups") or ()
        spares = [g for g in groups[animated:] if g.vertex_count and g.bounds]
        self.variation_box.blockSignals(True)
        self.variation_box.clear()
        self.variation_box.addItem("Default", None)
        self._variations = {}
        def extent(g):
            x0, x1, y0, y1, z0, z1 = g.bounds
            return (x1 - x0, y1 - y0, z1 - z0)

        # A spare that spanning_groups already spoke for is that limb's,
        # measured against the bone table rather than guessed from shape.
        chosen = {group: bone for bone, group in
                  getattr(self, "_spanning", {}).items()}

        for spare in spares:
            best, score = chosen.get(spare.index), None
            for limb in groups[:animated] if best is None else ():
                if not limb.vertex_count or not limb.bounds:
                    continue
                gap = (sum(abs(a - b) for a, b in zip(spare.centre, limb.centre))
                       + sum(abs(a - b)
                             for a, b in zip(extent(spare), extent(limb))))
                if score is None or gap < score:
                    best, score = limb.index, gap
            if best is None:
                continue
            name = (self._hierarchy[best][0]
                    if best < len(self._hierarchy) else f"limb {best}")
            self._variations[spare.index] = best
            if spare.index in chosen:
                continue        # already drawn - it is this limb's part
            self.variation_box.addItem(
                f"part {spare.index} instead of {name}", spare.index)
        self.variation_box.setEnabled(self.variation_box.count() > 1)
        self.variation_box.setCurrentIndex(0)
        self.variation_box.blockSignals(False)

    def _keep_groups(self, first):
        """Which groups are drawn: one per animated bone.

        Normally bone i is group first+i, but where the model carries the
        same part at another length and only the longer one reaches the
        next joint, that one is drawn instead - see
        game_rest.spanning_groups."""
        keep = set()
        for bone in range(len(self._hierarchy)):
            automatic = getattr(self, "_automatic_parts", {}).get(bone)
            spanning = getattr(self, "_spanning", {}).get(bone)
            keep.add(automatic if automatic is not None else
                     first + bone if spanning is None else spanning)
        return keep

    def _on_first_group_changed(self, first):
        """Re-pose with the animation driving a different run of parts."""
        if not self.model:
            return
        self.viewer.pose_first_group = first
        self._pose_on(self.model, self._export_bones,
                      len(self._hierarchy) or 0)

    def _on_variation_changed(self, index):
        """Show a spare part in place of the limb it stands in for.

        Built from the same window _pose_on uses, so this agrees with
        the First part offset instead of assuming the animation starts
        at part 0."""
        if not self.model:
            return
        self._refresh_visible_parts()

    def _pose_on(self, model, bones, limbs, *, frame_model=True,
                 show_position=True):
        """Stand `model` up on `bones` and show it.

        Split out of _use_model so the skeleton chooser can put a
        different table under the same model without reloading it."""
        self.model = model
        if not self._switching_variant and not self._switching_actor_rig:
            self._base_actor_bones = bones
        if bones is not None:
            self._hierarchy, self._named_hierarchy = game_rest.hierarchy(bones), True
        else:
            self._hierarchy, self._named_hierarchy = hierarchy_for(limbs)

        # Stand the model up first if its rest pose has been measured -
        # an SMST is packed, not assembled, so without this the limbs
        # would rotate about each other in a heap.
        # The spare parts - Tomba's mouth-open head and open hands - stand
        # in for a limb rather than joining it, so the game draws one or
        # the other. Hidden here, and switchable from the Variation box.
        # A model can carry the same part at two lengths and only one of
        # them fits this skeleton - the ghost guard's tongue. Where the
        # default cannot reach its own joint, take the one that can.
        self._spanning = (game_rest.spanning_groups(model, bones)
                          if bones is not None else {})
        self._fill_variations(model, len(self._hierarchy))
        # Only the run of parts the animation actually drives is shown.
        # With no offset that is the front of the model and the spares
        # behind it; with one it is the object inside an asset pack that
        # this animation belongs to, and the rest of the room stays out
        # of the way.
        first = self.viewer.pose_first_group
        total = len(model["groups"])
        self.first_group_box.blockSignals(True)
        self.first_group_box.setRange(0, max(0, total - 1))
        self.first_group_box.setValue(min(first, max(0, total - 1)))
        self.first_group_box.blockSignals(False)
        first = self.first_group_box.value()
        self.viewer.pose_first_group = first
        self.viewer.pose_spares = dict(self._variations)
        self._automatic_parts = self._parts_for_clip(self._clip)
        keep = self._keep_groups(first)
        self.viewer.hidden_groups = set(range(total)) - keep
        if bones is not None:
            # The spare-to-limb map is worked out from the model rather
            # than looked up: skeleton.SPARES only ever knew Tomba's
            # four, and reading it off the shapes gives the same answer
            # for him - 17 and 18 to the head, 19 and 20 to the hands -
            # while also covering every other character that carries
            # alternates.
            standing = game_rest.rest_pose(model, bones, self._variations,
                                           first=self.viewer.pose_first_group)
        else:
            standing = (rest_pose(model, self._hierarchy)
                        if self._named_hierarchy else None)
        self._measured_rest = standing is not None
        # Export wants the model as the file has it - each part around
        # its own origin - because that is what a glTF skin binds to.
        # What goes to the viewer below is the standing pose instead,
        # with every part already moved onto its joint, so the two are
        # kept apart here rather than one being derived from the other.
        self._export_model = model
        self._export_bones = bones
        if standing is not None:
            vertices, self._pivots = standing
            model = dict(model, vertices=vertices.tolist())
        else:
            self._pivots = rest_pivots(model["groups"], self._hierarchy)
        self.viewer.model_data = model
        self.viewer.prepare_buffers()
        if frame_model:
            self.viewer.frame_model()
        if show_position:
            self.show_position(self.slider.value())
        self._update_info()

    def _find_sea_variants(self):
        """Locate the three SMST archives selected by A04's payload mode."""
        self._sea_variants = {}
        label = self.model_box.currentText().casefold()
        if "sea anemone" not in label:
            return
        color = "pink" if "pink" in label else "blue" if "blue" in label else ""
        for candidate_label, address, size in self._candidates:
            folded = candidate_label.casefold()
            if "sea anemone" not in folded or (color and color not in folded):
                continue
            mode = (0 if "mouth closed" in folded else
                    1 if "opened var 1" in folded else
                    2 if "opened var 2" in folded else None)
            if mode is not None:
                self._sea_variants[mode] = (candidate_label, address, size)

    def _restore_selected_sea_model(self):
        if (not self._sea_variants or self._sea_variant_mode is None
                or self._selected_model_address is None or not self._source):
            return
        selected = next((entry for entry in self._candidates
                         if entry[1] == self._selected_model_address), None)
        if selected is None:
            return
        _label, address, size = selected
        self._switching_variant = True
        try:
            self._use_model(load_smst(self._source[0], address, size), address)
        except Exception as error:
            print(f"[ANMP] could not restore Sea Anemone model: {error}")
        finally:
            self._switching_variant = False
            self._sea_variant_mode = None

    def _apply_actor_parts(self, step):
        """Apply actor-code model choices attached to one sequence step."""
        if not self._clip:
            return
        if not self._sea_variants:
            wanted = self._parts_for_clip(self._clip)
            if wanted != self._automatic_parts:
                self._automatic_parts = wanted
                self._refresh_visible_parts()
            return

        # FUN_A04__80129744 uses payload4's low nibble to choose the
        # closed/open archive.  Mode 2 additionally uses the high nibble as
        # the mouth-part group installed on node 2 (actor + 0xC8).
        mode = step.payload4 & 0xF
        if mode not in self._sea_variants:
            return
        if mode != self._sea_variant_mode:
            _label, address, size = self._sea_variants[mode]
            try:
                variant = load_smst(self._source[0], address, size)
                bones = self._export_bones
                self._switching_variant = True
                self._pose_on(variant, bones, len(self._hierarchy),
                              frame_model=False, show_position=False)
                self._model_address = address
                if self._model_vram_provider is not None:
                    supplied = self._model_vram_provider(address)
                    if supplied:
                        vram_bytes, vram_image, _area = supplied
                        self.viewer.set_vram(vram_bytes, vram_image)
                self._sea_variant_mode = mode
            except Exception as error:
                print(f"[ANMP] could not switch Sea Anemone variant: {error}")
            finally:
                self._switching_variant = False
        mouth = step.payload4 >> 4
        wanted = ({2: mouth} if mode == 2
                  and 0 <= mouth < len(self.model.get("groups", ())) else {})
        if wanted != self._automatic_parts:
            self._automatic_parts = wanted
            self._refresh_visible_parts()

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
        if self._clip:
            self.slider.setMaximum(max(self._clip.duration - 1, 0))
            self.slider.setValue(int(where))
        else:
            self.slider.setMaximum(max((len(self.anmp) - 1) * self.steps, 0))
            self.slider.setValue(int(round(where * self.steps)))
        self.slider.blockSignals(False)

    def position(self):
        """Raw table frames, or recorded game ticks in clip mode."""
        return self._where

    def show_frame(self, index):
        """Jump to a row: a raw pose or a recorded clip step."""
        value = (self._clip.starts[index] if self._clip
                 else int(index) * self.steps)
        self.slider.setValue(value)
        # Selecting the already-current row must leave the rest pose too.
        self.show_position(value)

    def export_gltf(self):
        """Write model + skeleton + this animation out as one file."""
        model = getattr(self, "_export_model", None)
        if not model or not self.anmp:
            QMessageBox.warning(self, "Nothing to export",
                                "Load an animation and a model first.")
            return
        if self._export_bones is None:
            QMessageBox.warning(
                self, "No skeleton",
                "No bone table was found for this model, so there is "
                "nothing to rig the animation to. The model can still be "
                "exported on its own from the SMST view.")
            return
        export_clip = bool(self._clip)
        if self._clip:
            choice = QMessageBox(self)
            choice.setWindowTitle("Export animation scope")
            choice.setIcon(QMessageBox.Icon.Question)
            choice.setText("Which animation data should the GLB contain?")
            clip_button = choice.addButton(
                "Selected game animation", QMessageBox.ButtonRole.AcceptRole)
            raw_button = choice.addButton(
                "All raw ANMP poses", QMessageBox.ButtonRole.ActionRole)
            choice.addButton(QMessageBox.StandardButton.Cancel)
            choice.setDefaultButton(clip_button)
            choice.exec()
            clicked = choice.clickedButton()
            if clicked is clip_button:
                export_clip = True
            elif clicked is raw_button:
                export_clip = False
            else:
                return

        path, unlit = export_dialog.ask_model_path(
            self, "Save animated model", self.export_name or "animation")
        if not path:
            return
        try:
            write = (gltf_export.write_gltf if path.lower().endswith(".gltf")
                     else gltf_export.write_glb)
            frames, fps = self.anmp.frames, self.fps_box.value()
            if export_clip:
                # Bake recorded holds/tweens for export; raw-table FPS
                # must not leak into a game-timed sequence.
                from formats.animation.anmp_parser import Frame
                import math
                frames = []
                for tick in range(self._clip.duration):
                    row, nxt, amount = self._clip.sample(tick)
                    first = self._frames_by_id[self._clip.steps[row].pose]
                    second = (self._frames_by_id[self._clip.steps[nxt].pose]
                              if nxt is not None else None)
                    rotations, root, scales = blend(first, second, amount)
                    scales = self._clip_scales[row]
                    frames.append(Frame(
                        index=tick, offset=first.offset, tag=first.tag,
                        limbs=[tuple(v / math.tau * 4096 for v in r)
                               for r in rotations],
                        root=tuple(int(round(v)) & 0xFFF for v in root),
                        scales=[tuple(v * 4096 for v in s) for s in scales]))
                fps = self._tick_rate
            write(path, model, self.viewer.vram_raw_bytes,
                  groups=model["groups"], bones=self._export_bones,
                  frames=frames, fps=fps,
                  name=self.model_box.currentText() or "model",
                  spares=self._variations,
                  skip=self.viewer.hidden_groups, unlit=unlit,
                  flat_bones=not self._inherit_scales)
        except Exception as e:
            QMessageBox.critical(self, "Export failed",
                                 f"Couldn't write it:\n\n{e}")
            return
        QMessageBox.information(
            self, "Exported",
            f"Wrote {len(self._export_bones)} bones and "
            f"{len(frames)} frames at {fps}fps.")

    def save_gif(self):
        """Record the rendered ANMP, retaining the selected playback scope.

        A sequence's duration is in game ticks, whereas raw ANMP data has
        only stored poses.  The two paths deliberately use their matching
        clocks: this is important for held poses, tweening and actor-driven
        part substitutions such as Tomba's hands and heads.
        """
        if not self.anmp or not self.model or self._pivots is None:
            QMessageBox.warning(self, "Nothing to record",
                                "Load an animation and a model first.")
            return
        export_clip = bool(self._clip)
        if self._clip:
            choice = QMessageBox(self)
            choice.setWindowTitle("GIF animation scope")
            choice.setIcon(QMessageBox.Icon.Question)
            choice.setText("Which animation should the GIF record?")
            clip_button = choice.addButton(
                "Selected game animation", QMessageBox.ButtonRole.AcceptRole)
            raw_button = choice.addButton(
                "All raw ANMP poses", QMessageBox.ButtonRole.ActionRole)
            choice.addButton(QMessageBox.StandardButton.Cancel)
            choice.setDefaultButton(clip_button)
            choice.exec()
            if choice.clickedButton() is clip_button:
                export_clip = True
            elif choice.clickedButton() is raw_button:
                export_clip = False
            else:
                return
        ticks = (self._clip.duration if export_clip
                 else len(self.anmp) * max(self.steps, 1))
        rate = (self._tick_rate if export_clip
                else self.fps_box.value() * max(self.steps, 1))
        if ticks > view_gif.MAX_FRAMES:
            answer = QMessageBox.question(
                self, "Long GIF capture",
                f"This export contains {ticks} rendered frames (about "
                f"{ticks / max(rate, 1):.1f} seconds). It may take a while "
                "and create a large GIF. Record the complete animation?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
        position = self.slider.value()
        was_playing = self.play_button.isChecked()
        if was_playing:
            self.play_button.setChecked(False)
        selected_clip = self._clip
        # A clip can be selected while the user explicitly asks for raw
        # data.  `show_position` normally follows the selected clip, so make
        # that scope switch real for the recorder rather than accidentally
        # capturing the clip again.
        if not export_clip:
            self._clip = None
        self.viewer.transparent_background = True
        self.viewer.update()
        try:
            frames = view_gif.record(
                self.viewer, ticks, self.show_position, rate=rate, limit=ticks,
                transparent=True)
        finally:
            self.viewer.transparent_background = False
            self.viewer.update()
            self._clip = selected_clip
            self.slider.setValue(position)
            self.show_position(position)
            if was_playing:
                self.play_button.setChecked(True)
        scope = "pose animation" if export_clip else "raw poses"
        view_gif.save(self, frames,
                      f"{self.export_name or 'animation'}-{scope}",
                      transparent=True)

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
        if self._clip:
            self._show_clip_position(sub)
            return
        steps = max(self.steps, 1)
        index, part = divmod(int(sub), steps)
        index = max(0, min(index, len(self.anmp) - 1))
        amount = part / steps
        frame = self.anmp.frames[index]
        following = (self.anmp.frames[index + 1]
                     if amount and index + 1 < len(self.anmp) else None)
        if amount:
            rotations, translation, _ignored_scale_lerp = blend(
                frame, following, amount)
        else:
            rotations, translation = frame.rotations(), frame.translation()
        # Raw mode has no actor/sequence history. Treat each stored pose as
        # self-contained, but do not invent a scale tween the engine lacks.
        scales = frame.scaling()
        transforms = pose_transforms(rotations, translation,
                                     self._hierarchy, self._pivots,
                                     scales=scales,
                                     inherit_scales=self._inherit_scales)
        self.viewer.set_pose(transforms, self._pivots)
        self._where = index + amount
        between = f" + {part}/{steps}" if part else ""
        self.frame_label.setText(f"{index + 1}{between} / {len(self.anmp)}")
        self._fill_limbs(frame, rotations, translation, scales)

    def _show_clip_position(self, tick):
        clip = self._clip
        tick = max(0, min(int(tick), clip.duration - 1))
        row, nxt, amount = clip.sample(tick)
        self._apply_actor_parts(clip.steps[row])
        frame = self._frames_by_id[clip.steps[row].pose]
        following = (self._frames_by_id[clip.steps[nxt].pose]
                     if nxt is not None else None)
        rotations, translation, _blended_scales = blend(
            frame, following, amount)
        scales = self._clip_scales[row]
        transforms = pose_transforms(rotations, translation,
                                     self._hierarchy, self._pivots,
                                     scales=scales,
                                     inherit_scales=self._inherit_scales)
        self.viewer.set_pose(transforms, self._pivots)
        self._where = tick
        self.frame_label.setText(
            f"Pose {frame.index} · step {row + 1}/{len(clip.steps)} · "
            f"tick {tick + 1}/{clip.duration}")
        self.frames_table.blockSignals(True)
        self.frames_table.selectRow(row)
        self.frames_table.blockSignals(False)
        self._fill_limbs(frame, rotations, translation, scales)

    def _on_steps_changed(self):
        self._rescale_slider()
        self._retime()
        self.show_position(self.slider.value())

    def _fill_limbs(self, frame, rotations=None, translation=None,
                    scales=None):
        """The frame's angles, written into the table beside the model.

        This runs once per displayed frame, so it has to be cheap, and it
        was not. The header used to be on ResizeToContents, which
        re-measures every column across every row each time a cell is
        written - the square of the row count:

            rows   ResizeToContents   Interactive
               8         4.6 ms          0.4 ms
              17        15.8 ms          0.5 ms
              32        57.7 ms          0.7 ms
              64       223.3 ms          0.9 ms

        against 0.8 ms for the pose and all the vertices together. That
        is what made the ghost guard fall off a cliff at frame 19, where
        its frames go from 7 limbs to 16, on a machine with nothing
        whatever wrong with it - and it would have been far worse on the
        63-limb frames elsewhere on the disc.

        So the header is Interactive and the columns are sized only when
        the shape of the table changes. Reusing the cells rather than
        building new ones saves the allocations on top, though it is the
        header mode that was doing the damage: with ResizeToContents,
        reuse alone still cost 13 ms a frame at 16 limbs."""
        import math
        rotations = frame.rotations() if rotations is None else rotations
        translation = frame.translation() if translation is None else translation
        # A bit-6 frame carries a scale per limb as well, and that is
        # half of what some animations do - the sea anemone stretches on
        # 190 of its 192 frames and barely rotates - so it is shown
        # rather than left invisible.
        sizes = frame.scaling() if scales is None else scales
        stretchy = bool(frame.scales) or any(
            any(abs(axis - 1.0) > 1e-6 for axis in size)
            for size in (scales or ()))
        columns = 7 if stretchy else 4
        rows = []
        if frame.root:
            x, y, z = translation
            rows.append(("root (move)", f"{x:.1f}", f"{y:.1f}", f"{z:.1f}")
                        + (("", "", "") if stretchy else ()))
        for i, (x, y, z) in enumerate(rotations):
            name = self._hierarchy[i][0] if i < len(self._hierarchy) else f"limb {i}"
            cells = (name, f"{math.degrees(x):7.1f}",
                     f"{math.degrees(y):7.1f}", f"{math.degrees(z):7.1f}")
            if stretchy:
                sx, sy, sz = sizes[i] if i < len(sizes) else (1.0, 1.0, 1.0)
                cells += (f"{sx:5.2f}", f"{sy:5.2f}", f"{sz:5.2f}")
            rows.append(cells)
        reshaped = (self.limbs_table.rowCount() != len(rows)
                    or self.limbs_table.columnCount() != columns)
        if reshaped:
            self.limbs_table.setColumnCount(columns)
            self.limbs_table.setHorizontalHeaderLabels(
                ["Limb", "X", "Y", "Z"]
                + (["SX", "SY", "SZ"] if stretchy else []))
            self.limbs_table.setRowCount(len(rows))
        for r, cells in enumerate(rows):
            for c, text in enumerate(cells):
                item = self.limbs_table.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.limbs_table.setItem(r, c, item)
                if item.text() != text:
                    item.setText(text)
        # The angles are all the same width frame to frame, so the
        # columns only need measuring when the table changes shape.
        if reshaped:
            self.limbs_table.resizeColumnsToContents()
        # So a picked row can be named by where its angles live.
        self._limbs_frame = frame

    def _on_limb_selected(self):
        """Say which limb was picked, and where its three 12-bit angles
        sit in the DAT.

        A slot is three 12-bit values - 36 bits - packed back to back
        from the frame's own offset, so slot n starts 4.5 bytes in and
        lands on a nibble boundary every other limb. The root, when the
        tag bit says there is one, is slot 0 and the limbs follow it.

        A frame with bit 6 set carries a scale beside each rotation, six
        values to a slot, which comes to a whole nine bytes - so those
        never land on a nibble."""
        frame = getattr(self, "_limbs_frame", None)
        rows = self.limbs_table.selectionModel().selectedRows()
        if frame is None or not self.anmp or not rows:
            return
        row = rows[0].row()
        name = self.limbs_table.item(row, 0)
        if frame.flagged:
            bit = row * WIDE_SLOT_BYTES * 8
        else:
            bit = row * VALUES_PER_LIMB * BITS_PER_VALUE
        at = self.anmp.address + frame.offset + bit // 8
        # Which SMST part this limb drives, which is the number that
        # makes it addressable against the model rather than the ANMP.
        limb = row - (1 if frame.root else 0)
        drives = ("-" if limb < 0
                  else str(self.viewer.pose_first_group + limb))
        print(f"selected: ANMP @ 0x{self.anmp.address:X}  frame "
              f"{frame.index} (@ 0x{self.anmp.address + frame.offset:X}, "
              f"tag 0x{frame.tag:02X})  row {row} "
              f"'{name.text() if name else ''}'  "
              f"slot @ 0x{at:X}{'+4bits' if bit % 8 else ''}  "
              f"drives SMST group {drives}")

    def _on_frame_selected(self):
        rows = self.frames_table.selectionModel().selectedRows()
        if rows:
            self.show_frame(rows[0].row())
            frame = (self._frames_by_id[self._clip.steps[rows[0].row()].pose]
                     if self._clip else
                     self.anmp.frames[rows[0].row()] if self.anmp else None)
            if frame is not None:
                print(f"selected: ANMP @ 0x{self.anmp.address:X}  frame "
                      f"{frame.index} @ 0x{self.anmp.address + frame.offset:X}"
                      f"  tag 0x{frame.tag:02X}  {frame.limb_count} limb(s)"
                      f"{' + root' if frame.root else ''}")

    def _on_play_toggled(self, playing):
        set_glyph(self.play_button, "pause" if playing else "play")
        if playing:
            # Play after Reset pose must apply the selected step immediately.
            if self._clip and self._clip.advance(self.slider.value()) is None:
                self.slider.setValue(0)
            self.show_position(self.slider.value())
            self._retime()
        else:
            self._timer.stop()
            self._clock_last = None

    def _reset_clock(self):
        """Make the next timer interval start at the current slider pose."""
        self._clock_last = time.perf_counter()

    def _retime(self):
        # QTimer intervals are integer milliseconds: 1000 // 60 used to
        # run at 62.5 Hz, and delayed callbacks made animation speed depend
        # on UI load. The timer now only wakes us; perf_counter decides how
        # many game ticks really elapsed.
        if self.play_button.isChecked():
            rate = (self._tick_rate if self._clip else
                    self.fps_box.value() * max(self.steps, 1))
            self._play_rate = max(rate, 1)
            self._clock_last = time.perf_counter()
            self._timer.start(max(min(1000 // (self._play_rate * 2), 16), 1))

    def _advance(self):
        if not self.anmp:
            return
        now = time.perf_counter()
        if self._clock_last is None:
            self._clock_last = now
            return
        elapsed = now - self._clock_last
        count = int(elapsed * self._play_rate)
        if count < 1:
            return
        self._clock_last += count / self._play_rate
        value = self.slider.value()
        if self._clip:
            for _ in range(count):
                value = self._clip.advance(value)
                if value is None:
                    self.play_button.setChecked(False)
                    return
            self.slider.setValue(value)
        else:
            end = self.slider.maximum() + 1
            self.slider.setValue((value + count) % end if end else 0)

    # --- status ------------------------------------------------------

    def _update_info(self):
        if not self.anmp:
            return
        counts = self.anmp.limb_counts
        shape = ", ".join(f"{n} limbs x{c}" for n, c in sorted(counts.items()))
        flagged = sum(1 for f in self.anmp.frames if f.flagged)
        parts = [f"{len(self.anmp)} frames", shape]
        if flagged:
            parts.append(f"{flagged} carrying a per-limb scale")
        if self.model:
            parts.append(f"{len(self.model['groups'])} groups in the model")
            if self._measured_rest:
                parts.append("rest pose and joints measured")
            elif self._named_hierarchy:
                parts.append("hierarchy known, joints estimated")
            else:
                parts.append("no hierarchy - parts turn independently")
        panel_title.set_info(self.info_label, "  |  ".join(parts))

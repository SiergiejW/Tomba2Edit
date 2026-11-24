"""Play as another character: pick a body (from the disc or from Blender),
the skeleton it moves on, the costumes it replaces and how its bones map
onto Tomba's; watch his animations on it; install."""
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

from formats.animation import game_rest, skeleton_tree
from formats.archive import repacker
from game import character_swap as cs
from gui.widgets.import_worker import run_import

KEEP_LABEL = "Keep the costume's own part"
HIDE_LABEL = "Hide"


@dataclass
class FileBody:
    """A body from an OBJ/GLB; cut onto a skeleton when one is chosen."""
    path: Path
    faces: list
    best: object            # the best-fitting Skeleton
    fits: dict              # skeleton name -> fit


class CharacterSwapDialog(QDialog):
    def __init__(self, host, paths, sources, skeletons=()):
        super().__init__(host)
        self.host = host
        self.paths = paths
        self.sources = list(sources)
        self.skeletons = list(skeletons)
        self._cuts = {}
        self.states = []
        self.installed = False
        self.setWindowTitle("Play as another character")
        self.resize(1250, 780)

        self.exe = paths["exe"].read_bytes()
        self.dat = paths["dat"].read_bytes()
        self.idx = paths["idx"].read_bytes()
        self.trail = repacker.parse_idx_bytes(self.idx)[cs.SYSTEM_CHUNK]["trail_ranges"]
        self.anmp, self.bank = cs.tomba_animations(self.exe, self.dat, self.idx)
        self._vram = {}
        self._frames = None
        self._tick = 0

        # --- body and skeleton ---
        self.source_box = QComboBox()
        self.source_box.currentIndexChanged.connect(self._source_changed)
        self.skeleton_box = QComboBox()
        self.skeleton_box.addItem("Its own (a file: the best fit)", None)
        for skeleton in self.skeletons:
            self.skeleton_box.addItem(skeleton.name, skeleton)
        self.skeleton_box.setToolTip(
            "The rig the body moves on: its bone lengths go into MAIN.EXE. Any body can "
            "use any rig - a Blender edit of Tabby can still take Tabby's.")
        self.skeleton_box.currentIndexChanged.connect(self._source_changed)
        import_file = QPushButton("Import from Blender…")
        import_file.setToolTip(
            "An OBJ or GLB of a whole character standing at rest. Parts named part_NN "
            "go to that bone; anything else goes to the bone of the nearest surface of "
            "the chosen skeleton's own body.")
        import_file.clicked.connect(self._import_file)
        export_file = QPushButton("Export for Blender…")
        export_file.setToolTip("The body standing on its skeleton, one object per part, "
                               "with its texture pages as PNGs.")
        export_file.clicked.connect(self._export_file)
        files = QHBoxLayout()
        files.addWidget(import_file)
        files.addWidget(export_file)
        self.area_box = QComboBox()
        names = host._area_names() if hasattr(host, "_area_names") else {}
        for chunk in range(4, 26):
            if host.overlay_for_area(chunk):
                self.area_box.addItem(names.get(chunk, f"AREA_{chunk:02X}"), chunk)
        find = QPushButton("Find characters")
        find.setToolTip("Run the area's own code and list every character it builds "
                        "with a verified skeleton.")
        find.clicked.connect(self._find_characters)
        more = QHBoxLayout()
        more.addWidget(self.area_box, 1)
        more.addWidget(find)
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)

        # --- costumes ---
        self.costumes = QListWidget()
        for group in cs.table_groups():
            item = QListWidgetItem(" + ".join(c.name for c in group))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, [c.slot for c in group])
            if len(group) > 1:
                item.setToolTip("These share one skeleton in MAIN.EXE, so they change together.")
            self.costumes.addItem(item)
        self.costumes.setMaximumHeight(150)
        costumes_note = QLabel(
            "Tomba shares most of his art with the suits, so the body's textures "
            "only fit when the costumes go together. Abilities stay with each "
            "costume either way.")
        costumes_note.setWordWrap(True)

        # --- bones ---
        self.bones = QTableWidget(17, 2)
        self.bones.setHorizontalHeaderLabels(["Tomba's bone", "Becomes"])
        self.bones.verticalHeader().setVisible(False)
        self.bones.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.bones.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for bone in range(17):
            label = _read_only(f"{bone}  {cs.BONE_NAMES[bone]}")
            self.bones.setItem(bone, 0, label)
            combo = QComboBox()
            combo.currentIndexChanged.connect(self._refresh_preview)
            self.bones.setCellWidget(bone, 1, combo)
        self.merge = QCheckBox("Carry unmapped parts (tails, ears…) on their parent bone")
        self.merge.setChecked(True)
        self.merge.toggled.connect(self._refresh_preview)

        # --- savestates ---
        states = QPushButton("Add savestate(s)…")
        states.setToolTip("Optional. US savestates show what the game uploads into VRAM "
                          "at runtime, which nothing on the disc records.")
        states.clicked.connect(self._add_states)
        self.states_label = QLabel("No savestates.")
        self.states_label.setWordWrap(True)

        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.addRow("Body", self.source_box)
        form.addRow("Skeleton", self.skeleton_box)
        form.addRow("From a file", files)
        form.addRow("From an area", more)
        lay.addLayout(form)
        lay.addWidget(self.source_note)
        box = QGroupBox("Costumes it replaces")
        bl = QVBoxLayout(box)
        bl.addWidget(self.costumes)
        bl.addWidget(costumes_note)
        lay.addWidget(box)
        box = QGroupBox("Bone map")
        bl = QVBoxLayout(box)
        bl.addWidget(self.bones, 1)
        bl.addWidget(self.merge)
        lay.addWidget(box, 1)
        row = QHBoxLayout()
        row.addWidget(states)
        row.addWidget(self.states_label, 1)
        lay.addLayout(row)

        # --- preview ---
        from formats.models.smst_viewer import SMSTViewer
        self.viewer = SMSTViewer(self)
        self.preview_costume = QComboBox()
        for c in cs.COSTUMES:
            self.preview_costume.addItem(c.name, c.slot)
        self.preview_costume.currentIndexChanged.connect(self._refresh_preview)
        self.clip_box = QComboBox()
        for clip in self.bank.clips:
            self.clip_box.addItem(f"0x{clip.id:02X}  {clip.name or ''}".rstrip(), clip.id)
        idle = self.clip_box.findData(2)
        self.clip_box.setCurrentIndex(max(idle, 0))
        self.clip_box.currentIndexChanged.connect(self._clip_changed)
        self.play = QCheckBox("Play")
        self.play.setChecked(True)
        self.play.toggled.connect(lambda on: self.timer.start() if on and self._frames else self.timer.stop())
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._step)
        self.preview_note = QLabel()
        self.preview_note.setWordWrap(True)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Costume"))
        controls.addWidget(self.preview_costume)
        controls.addWidget(QLabel("Animation"))
        controls.addWidget(self.clip_box, 1)
        controls.addWidget(self.play)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(controls)
        rl.addWidget(self.viewer, 1)
        rl.addWidget(self.preview_note)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([480, 770])

        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(140)
        self.report.setPlaceholderText("Check works out where everything goes without writing anything.")
        check = QPushButton("Check")
        check.clicked.connect(lambda: self._prepare(install=False))
        self.apply_button = QPushButton("Install")
        self.apply_button.setToolTip("Writes the working project's DAT, IDX, IMG and MAIN.EXE.")
        self.apply_button.clicked.connect(lambda: self._prepare(install=True))
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(check)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addWidget(self.report)
        layout.addLayout(buttons)

        for source in self.sources:
            self.source_box.addItem(source.name, source)
        if not self.sources:
            self.source_note.setText("Import a body from Blender, or find characters in an area.")

    # --- state ------------------------------------------------------------

    def source(self):
        """The chosen body on the chosen skeleton, as one Source."""
        body = self.source_box.currentData()
        skeleton = self.skeleton_box.currentData()
        if isinstance(body, FileBody):
            skeleton = skeleton or body.best
            key = (str(body.path), skeleton.name, id(skeleton))
            if key not in self._cuts:
                self._cuts[key] = cs.body_from_file(body.path, skeleton, faces=body.faces)
            return self._cuts[key]
        if body is None or skeleton is None:
            return body
        return cs.on_skeleton(body, skeleton)

    def _add_skeleton(self, skeleton):
        if any(s.bones == skeleton.bones for s in self.skeletons):
            return
        self.skeletons.append(skeleton)
        self.skeleton_box.addItem(skeleton.name, skeleton)

    def slots(self):
        out = []
        for row in range(self.costumes.count()):
            item = self.costumes.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                out += item.data(Qt.ItemDataRole.UserRole)
        return out

    def plan_for(self, costume):
        choices = [_choice(self.bones.cellWidget(b, 1).currentData()) for b in range(17)]
        choices += [(cs.KEEP,)] * (costume.bones - 17)
        return cs.Plan(choices, self.merge.isChecked())

    def costume_parts(self, costume):
        start, end = self.trail[costume.slot]
        rows = skeleton_tree.read_table(self.exe, skeleton_tree.exe_offset(costume.table), costume.bones)
        return self.dat[start:end], [tuple(r) for r in rows]

    # --- body -------------------------------------------------------------

    def _source_changed(self):
        try:
            source = self.source()
        except Exception as error:
            self.source_note.setText(f"Cannot use this body on that skeleton: {error}")
            return
        if source is None:
            return
        names = [n for n, _p in game_rest.hierarchy(source.bones)]
        default = cs.default_plan(source, cs.COSTUMES[0])
        for bone in range(17):
            combo = self.bones.cellWidget(bone, 1)
            combo.blockSignals(True)
            combo.clear()
            for k, name in enumerate(names):
                combo.addItem(f"{source.name}: {k}  {name.replace('_', ' ')}", _key((cs.SOURCE, k)))
            combo.addItem(KEEP_LABEL, _key((cs.KEEP,)))
            combo.addItem(HIDE_LABEL, _key((cs.EMPTY,)))
            combo.setCurrentIndex(max(combo.findData(_key(default.choices[bone])), 0))
            combo.blockSignals(False)
        mapped = sum(1 for c in default.choices if c[0] == cs.SOURCE)
        body = self.source_box.currentData()
        rig = ""
        if isinstance(body, FileBody):
            ranked = ", ".join(f"{n} {v:.1f}" for n, v in sorted(body.fits.items(), key=lambda i: i[1]))
            rig = f" Fit (lower is closer): {ranked}."
        self.source_note.setText(
            f"{source.note}. {len(source.bones)} bones; {mapped} map onto Tomba's "
            f"17 automatically - check the table, every row can be changed.{rig}")
        self._refresh_preview()

    def _find_characters(self):
        chunk = self.area_box.currentData()
        if chunk is None:
            return
        try:
            recipes, _rejected = self.host.npc_catalogue_recipes(chunk, self)
        except Exception as error:
            QMessageBox.warning(self, "Find characters", str(error))
            return
        added = 0
        for recipe in recipes:
            source = cs.from_recipe(recipe)
            if any(getattr(s, "model", None) == source.model and s.bones == source.bones
                   for s in self.sources):
                continue
            self.sources.append(source)
            self.source_box.addItem(source.name, source)
            self._add_skeleton(cs.skeleton_of(source))
            added += 1
        self.source_note.setText(f"{added} character(s) added from {self.area_box.currentText()}.")
        if added:
            self.source_box.setCurrentIndex(self.source_box.count() - added)

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "A whole character from Blender", "",
            "Blender exports (*.glb *.gltf *.obj);;glTF 2.0 (*.glb *.gltf);;Wavefront OBJ (*.obj)")
        if not path:
            return
        try:
            faces = cs.read_body_faces(path)
            if not self.skeletons:
                raise cs.SwapError("No skeleton to fit it to.")
            fits = {s.name: cs.fit(faces, s) for s in self.skeletons}
            best = min(self.skeletons, key=lambda s: fits[s.name])
            body = FileBody(Path(path), faces, best, fits)
            cs.body_from_file(body.path, best, faces=faces)        # errors now, not later
        except Exception as error:
            QMessageBox.warning(self, "Import from Blender", str(error))
            return
        self.sources.append(body)
        self.source_box.addItem(f"{Path(path).name} (file)", body)
        self.skeleton_box.setCurrentIndex(0)
        self.source_box.setCurrentIndex(self.source_box.count() - 1)

    def _export_file(self):
        source = self.source()
        if source is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export the body for Blender",
                                              f"{source.name}.obj", "Wavefront OBJ (*.obj)")
        if not path:
            return
        try:
            vram, _image = self._source_vram(source)
            cs.export_body(path, source, vram)
        except Exception as error:
            QMessageBox.warning(self, "Export for Blender", str(error))
            return
        self.source_note.setText(
            f"Exported {Path(path).name}. Blender: import Wavefront, Forward -Z, Up Y. Edit, "
            f"then export GLB (keeps the pictures) and use Import from Blender. Keeping the "
            f"part_NN object names pins each part to its bone.")

    def _add_states(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "US savestates", "",
            "Emulator savestates (*.sav *.state *.sstate* *.000 *.001 *.002 *.003 *.004 *.005 *.006);;All files (*)")
        if paths:
            self.states += paths
            self.states_label.setText(f"{len(self.states)} savestate(s).")

    # --- preview ----------------------------------------------------------

    def _source_vram(self, source):
        if source.vram is not None:
            from psx.vram_viewer import vram_index_image
            key = id(source.vram)
            if key not in self._vram:
                self._vram[key] = (bytes(source.vram), vram_index_image(bytes(source.vram)))
            return self._vram[key]
        chunk = source.chunk
        if chunk not in self._vram:
            from psx import vram_map
            from psx.vram_viewer import vram_index_image
            img = self.paths["img"].read_bytes()
            shards = vram_map.chunk_shards(str(self.paths["idx"]), str(self.paths["img"]))
            vram = vram_map.loaded_vram(shards, cs._vram_reader(self.idx, img), chunk)
            self._vram[chunk] = (bytes(vram), vram_index_image(bytes(vram)))
        return self._vram[chunk]

    def _clip_changed(self):
        self._refresh_preview(keep_view=True)

    def _refresh_preview(self, *_args, keep_view=False):
        try:
            source = self.source()
        except Exception:
            return
        if source is None or self.bones.cellWidget(0, 1).count() == 0:
            return
        costume = cs.COSTUMES[self.preview_costume.currentData()]
        clip = next((c for c in self.bank.clips if c.id == self.clip_box.currentData()), None)
        try:
            model, rows = self.costume_parts(costume)
            blob, new_rows = cs.draft(source, costume, model, rows, self.plan_for(costume))
            result = cs.posed(blob, costume, new_rows, self.anmp, clip)
        except Exception as error:
            self.preview_note.setText(f"Cannot preview this: {error}")
            self.timer.stop()
            return
        vram, image = self._source_vram(source)
        self.viewer.set_vram(vram, image)
        first = self.viewer.model_data is None
        self.viewer.model_data = result["model"]
        self.viewer.hidden_groups = set(result["hidden"])
        self.viewer.pose_first_group = 0
        self.viewer.pose_spares = dict(result["spares"])
        self.viewer.prepare_buffers()
        self.viewer.set_pose(result["frames"][0], result["pivots"])
        if first or not keep_view:
            self.viewer.frame_model()
        self._frames, self._pivots, self._loop = result["frames"], result["pivots"], result["loop"]
        self._tick = 0
        self.preview_note.setText(
            f"{costume.name}, {len(blob)} of {cs.MAX_MODEL} bytes. Textures show as they are "
            f"in {source.name}'s own area until installed.")
        if self.play.isChecked() and len(self._frames) > 1:
            self.timer.start()

    def _step(self):
        if not self._frames:
            return
        self._tick += 1
        if self._tick >= len(self._frames):
            self._tick = self._loop if self._loop is not None else 0
        self.viewer.set_pose(self._frames[self._tick], self._pivots)

    # --- install ----------------------------------------------------------

    def _prepare(self, install):
        source = self.source()
        if source is None:
            return
        slots = self.slots()
        plans = {c.slot: self.plan_for(c) for c in cs.COSTUMES}
        paths, states = self.paths, list(self.states)

        def task(progress, cancelled):
            return cs.prepare(paths["exe"], paths["dat"], paths["idx"], paths["img"],
                              source, slots, plans, state_paths=states, progress=progress)
        try:
            prepared = run_import(task, self, reporting=True)
        except Exception as error:
            self.report.setPlainText(f"Not possible as set up: {error}\n\nNothing was written.")
            return
        self.report.setPlainText(prepared.report)
        if not install:
            return
        answer = QMessageBox.question(
            self, "Install?",
            f"{source.name} replaces {len(slots)} costume(s) in the working project:\n\n"
            f"{prepared.report}\n\nTOMBA2.DAT, .IDX, .IMG and MAIN.EXE are rewritten. "
            f"Save the project afterwards to keep it.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.host.install_character_swap(prepared)
        except Exception as error:
            QMessageBox.critical(self, "Install failed", f"Nothing was kept:\n\n{error}")
            return
        self.installed = True
        self.accept()


def _read_only(text):
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


# Qt hands tuples back as lists, so a choice travels as a string.
def _key(choice):
    return ":".join(str(part) for part in choice)


def _choice(key):
    kind, _sep, bone = key.partition(":")
    return (kind, int(bone)) if bone else (kind,)

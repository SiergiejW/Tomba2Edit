"""Put a model's textures somewhere every area it is used can reach.

WHAT THIS IS FOR

A packet points at a texture page and a palette address; VRAM is
whatever the current area loaded there. So a model whose art lives in
one level's IMG chunk draws wrong everywhere else - swap Tuxedo Tomba in
for the standard model and it takes its colours from whatever the room
happens to have at page 24.

Fixing that means choosing, and the choices are real ones this cannot
make for you:

    which areas   The model has to work in the areas you will use it in,
                  and space is only free if none of them writes there.
                  Tick them; the free map is recomputed from the union.
    where         Which page, and where inside it. Auto-place finds the
                  first fit; the boxes below let you put it anywhere and
                  the preview shows what you would be landing on.

WHAT THE PREVIEW SHOWS

The VRAM of the area highlighted in the list, exactly as the game has
it: chunks 0, 1 and 2 - which are loaded whatever room you are in - plus
that area's own. Red rectangles are where the textures would go. If a
red rectangle sits on top of artwork, that artwork is what you would be
destroying.

WHAT IS KNOWN, AND WHAT IS NOT

Known from the disc, and enforced: which chunks are resident (0, 1, 2
plus the area), what every chunk writes, and where the display buffers
are. A clash with any of those stops Apply.

Not knowable from the disc: what the game uploads into VRAM itself
while running. It is in no shard table and nothing references it -
checked, 0 of 244 CLUT addresses used by the sprite banks land on it -
so it can only be measured, and measuring it once gave about a thousand
halfwords in two clusters. Loading a savestate folds that in and is
worth doing when you have one, but it is a refinement rather than a
permit: the tool says what it does not know instead of refusing to act
on it.
"""
import os

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
)

from functions import (img_writer, psx_vram, state_vram, texture_migrate,
                       vram_map)
from gui.img.img_viewer import chunk_bounds
from gui.vram_viewer import VRAMCanvas, decode_vram_bytes, vram_index_image

PLACED = QColor(255, 60, 60)
PALETTE_MARK = QColor(120, 200, 255)


class MigrateDialog(QDialog):
    """Choose where a model's textures live, and rewrite its UVs to match."""

    def __init__(self, blob, vram, cd_folder, name="model", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Textures - {name}")
        self.resize(1180, 760)
        self.blob = blob
        self.vram = vram                 # what the model was drawn against
        self.cd_folder = cd_folder
        self.name = name
        self.plan = None
        self.new_blob = None
        self.shards = None
        self.to_write = []
        self.reused = []
        self._vram_cache = {}
        self._occupied = None            # from savestates, or None
        # What no chunk declares - the display, and whatever the
        # game uploads itself. Nobody can overwrite that.
        self._runtime = None
        self._states = []

        self.idx = os.path.join(cd_folder, "TOMBA2.IDX")
        self.img = os.path.join(cd_folder, "TOMBA2.IMG")
        self.shard_table = vram_map.chunk_shards(self.idx, self.img)

        # --- the areas this model has to work in ---
        self.areas = QListWidget(self)
        self.areas.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        for area in sorted(self.shard_table):
            item = QListWidgetItem(f"AREA_{area:02X}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, area)
            if area in vram_map.ALWAYS_RESIDENT:
                item.setText(f"AREA_{area:02X}  (always loaded)")
                item.setCheckState(Qt.CheckState.Checked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.areas.addItem(item)
        self.areas.itemChanged.connect(lambda _i: self.replan())
        self.areas.currentItemChanged.connect(lambda *_: self.refresh_preview())

        all_button = QPushButton("Tick every area")
        all_button.clicked.connect(lambda: self._set_all(True))
        none_button = QPushButton("Untick all")
        none_button.clicked.connect(lambda: self._set_all(False))

        self.states_label = QLabel(
            "No savestate loaded - the disc's own claims are all accounted "
            "for; what the game writes at runtime is not.", self)
        self.states_label.setWordWrap(True)
        state_button = QPushButton("Add savestate(s)...")
        state_button.setToolTip(
            "Optional. A savestate is the only thing that shows what the "
            "game writes into VRAM itself while running - nothing on the "
            "disc records it. Add one per area you care about and the "
            "free map accounts for those writes too.")
        state_button.clicked.connect(self._add_states)


        # The escape hatch when nothing fits. Deliberately worded as
        # destroying something, because that is what it does - and see
        # vram_map.owners_of for why it only works when the art being
        # overwritten belongs to the chunk being written into.
        self.overwrite = QCheckBox("Overwrite art that is already there",
                                   self)
        self.overwrite.setToolTip(
            "Place even where the ticked areas already keep artwork, "
            "destroying it.\n\nThis only works when the space belongs to "
            "the SAME chunk you are writing into - your shard is added "
            "last and wins. Space owned by another area's chunk cannot "
            "be taken this way: that chunk loads after the resident one "
            "and would overwrite you instead.")
        self.overwrite.toggled.connect(self.replan)

        # --- where the textures go ---
        self.page_box = QSpinBox(self)
        self.page_box.setRange(0, 31)
        self.x_box = QSpinBox(self)
        self.x_box.setRange(0, 1023)
        self.x_box.setSingleStep(4)
        self.y_box = QSpinBox(self)
        self.y_box.setRange(0, 511)
        for box in (self.page_box, self.x_box, self.y_box):
            box.valueChanged.connect(self._manual_move)
        self.moving = QComboBox(self)
        self.moving.setToolTip(
            "Everything the plan has to place - each patch of texture AND "
            "each palette. Pick one here and the Page/X/Y below move THAT "
            "one; the red and blue boxes in the preview show where each "
            "would land.")
        self.moving.currentIndexChanged.connect(self._show_current_move)

        auto = QPushButton("Auto-place")
        auto.setToolTip("Find the first spot that fits in the free space.")
        auto.clicked.connect(self.replan)

        place_box = QGroupBox("Placement (halfwords)", self)
        place_form = QFormLayout(place_box)
        place_form.addRow("Moving", self.moving)
        place_form.addRow("Page", self.page_box)
        place_form.addRow("X", self.x_box)
        place_form.addRow("Y", self.y_box)
        place_form.addRow("", auto)
        hint = QLabel(
            "Palettes are in the Moving list too - pick one to move it. "
            "X steps by 4 for a texture and by 16 for a palette, which is "
            "the only alignment a CLUT address can express.", self)
        hint.setWordWrap(True)
        place_form.addRow("", hint)

        self.destination = QComboBox(self)
        self.destination.addItem("AREA_01 - loaded in every area", 1)
        for area in sorted(self.shard_table):
            if area != 1:
                self.destination.addItem(f"AREA_{area:02X}", area)
        self.destination.setToolTip(
            "Which chunk the pixels are written into. AREA_01's is loaded "
            "everywhere, so that is where art meant to be seen everywhere "
            "belongs.")

        # --- preview ---
        self.canvas = VRAMCanvas(self)
        self.preview_label = QLabel("Pick an area to preview it.", self)
        self.preview_label.setWordWrap(True)

        self.report = QPlainTextEdit(self)
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(150)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("This model must work in:", self))
        left_layout.addWidget(self.areas, 1)
        row = QHBoxLayout()
        row.addWidget(all_button)
        row.addWidget(none_button)
        left_layout.addLayout(row)
        left_layout.addWidget(state_button)
        left_layout.addWidget(self.states_label)
        left_layout.addWidget(self.overwrite)
        left_layout.addWidget(place_box)
        left_layout.addWidget(QLabel("Write the pixels into:", self))
        left_layout.addWidget(self.destination)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.preview_label)
        right_layout.addWidget(self.canvas, 1)
        right_layout.addWidget(self.report)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 850])

        self.buttons = QDialogButtonBox(self)
        self.apply_button = QPushButton("Apply - copy the pixels, fix the UVs")
        self.apply_button.clicked.connect(self.apply_plan)
        self.buttons.addButton(self.apply_button,
                               QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.buttons)

        self.areas.setCurrentRow(0)
        self.replan()

    # --- areas and VRAM -----------------------------------------------

    def _set_all(self, on):
        self.areas.blockSignals(True)
        for row in range(self.areas.count()):
            item = self.areas.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Checked if on
                                   else Qt.CheckState.Unchecked)
        self.areas.blockSignals(False)
        self.replan()

    def wanted_areas(self):
        out = []
        for row in range(self.areas.count()):
            item = self.areas.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def chunk_vram(self, area):
        if area not in self._vram_cache:
            start, end = chunk_bounds(self.idx, area)
            if end <= start:
                self._vram_cache[area] = None
            else:
                with open(self.img, "rb") as f:
                    f.seek(start)
                    self._vram_cache[area] = decode_vram_bytes(
                        f.read(end - start))
        return self._vram_cache[area]

    def loaded_vram(self, area):
        """What VRAM holds while you stand in `area`."""
        return vram_map.loaded_vram(self.shard_table, self.chunk_vram, area)

    def tick_area(self, area):
        """Tick one area from outside."""
        self.tick_areas([area])

    def tick_areas(self, areas):
        """Tick several at once and replan ONCE.

        Ticking them one at a time replans per tick, and a replan
        decompresses a chunk per area - which for a trail model reached
        from thirty areas is thirty times the work for one answer."""
        wanted = set(areas)
        first = None
        self.areas.blockSignals(True)
        for row in range(self.areas.count()):
            item = self.areas.item(row)
            if item.data(Qt.ItemDataRole.UserRole) in wanted:
                item.setCheckState(Qt.CheckState.Checked)
                if first is None:
                    first = row
        self.areas.blockSignals(False)
        if first is not None:
            self.areas.setCurrentRow(first)
        self.replan()

    def _add_states(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Savestates to read VRAM from")
        if not paths:
            return
        reference = self.chunk_vram(1)
        occupied = self._occupied
        added = 0
        for path in paths:
            try:
                vram = state_vram.read(path, reference)
            except Exception as e:
                QMessageBox.warning(self, "Couldn't read that state",
                                    f"{os.path.basename(path)}: {e}")
                continue
            here = state_vram.occupancy(vram)
            occupied = here if occupied is None else (occupied | here)
            areas = state_vram.resident_areas(vram, self.shard_table,
                                              self.chunk_vram)
            mine = state_vram.runtime_only(vram, self.shard_table, areas)
            self._runtime = mine if self._runtime is None                 else (self._runtime | mine)
            self._states.append((os.path.basename(path), areas))
            added += 1
        if not added:
            return
        self._occupied = occupied
        self.states_label.setText(
            "Savestates: " + "; ".join(
                f"{name} (areas {', '.join(f'{a:02X}' for a in areas)})"
                for name, areas in self._states))
        self.replan()

    # --- planning -----------------------------------------------------

    def free_map(self):
        return vram_map.free_for(self.shard_table, self.wanted_areas(),
                                 occupied=self._occupied)

    def placement_map(self):
        """What auto-place may use. Normally the free map; with
        overwrite on, anywhere but the display and anything a savestate
        shows the game writing every frame - neither of which can be
        taken by anybody."""
        if not self.overwrite.isChecked():
            return self.free_map()
        allowed = vram_map.anywhere_but_display()
        if self._runtime is not None:
            allowed &= ~self._runtime
        return allowed

    def replan(self):
        """Auto-place, then show what it came to."""
        areas = self.wanted_areas()
        try:
            vrams = [self.loaded_vram(a) for a in areas]
            keep_pages, keep_cluts = texture_migrate.needed_for(
                self.blob, self.vram, vrams)
            boxes, cluts = texture_migrate.survey(self.blob)
            self._keep = (keep_pages, keep_cluts)
            if len(keep_pages) == len(boxes) and len(keep_cluts) == len(cluts):
                self.plan = None
                self.report.setPlainText(
                    "Nothing to move: every page and palette this model "
                    "samples is already present, byte for byte, in all "
                    f"{len(areas)} ticked area(s).")
                self.moving.clear()
                self.apply_button.setEnabled(False)
                self.refresh_preview()
                return
            self.plan = texture_migrate.plan(
                self.blob, self.placement_map(), pages=vram_map.USABLE_PAGES,
                keep_pages=keep_pages, keep_cluts=keep_cluts)
        except Exception as e:
            self.plan = None
            self.report.setPlainText(
                f"Cannot place this: {e}\n\nNothing has been changed. Fewer "
                f"ticked areas means more free space; so does putting the "
                f"art somewhere only some areas can see.")
            self.moving.clear()
            self.apply_button.setEnabled(False)
            self.refresh_preview()
            return
        self._fill_moving()
        self._finish_plan()

    def _fill_moving(self):
        self.moving.blockSignals(True)
        self.moving.clear()
        for n, move in enumerate(self.plan.moves):
            w, h = move.src_rect[2], move.src_rect[3]
            self.moving.addItem(
                f"texture  [{n}] page {move.page}, {w}x{h} halfwords",
                ("move", n))
        for old in sorted(self.plan.cluts):
            self.moving.addItem(
                f"palette  0x{old:X}  (16 halfwords, X steps by 16)",
                ("clut", old))
        self.moving.blockSignals(False)
        self._show_current_move()

    def _show_current_move(self):
        data = self.moving.currentData()
        if not data or not self.plan:
            return
        kind, key = data
        for box in (self.page_box, self.x_box, self.y_box):
            box.blockSignals(True)
        if kind == "move":
            move = self.plan.moves[key]
            x, y = move.dest_x, move.dest_y
            # A texture moves in whole halfwords, and 4 texels to each,
            # so stepping by 4 moves it one halfword at a time.
            self.x_box.setSingleStep(4)
        else:
            x, y = psx_vram.clut_address_xy(self.plan.cluts[key])
            # A palette's address packs x/16, so 16 is the only step
            # that lands on an address at all.
            self.x_box.setSingleStep(16)
        self.x_box.setValue(x)
        self.y_box.setValue(y)
        self.page_box.setValue((x // psx_vram.PAGE_HALFWORDS)
                               + (y // psx_vram.PAGE_ROWS)
                               * psx_vram.ATLAS_COLUMNS)
        for box in (self.page_box, self.x_box, self.y_box):
            box.blockSignals(False)

    def _manual_move(self):
        """Rebuild the plan with whatever the boxes now say."""
        data = self.moving.currentData()
        if not data or not self.plan:
            return
        kind, key = data
        x, y = self.x_box.value(), self.y_box.value()
        moves = [(m.page, m.box, m.packets, m.dest_page, m.dest_x, m.dest_y)
                 for m in self.plan.moves]
        clut_dest = dict(self.plan.cluts)
        if kind == "move":
            page = (x // psx_vram.PAGE_HALFWORDS
                    + (y // psx_vram.PAGE_ROWS) * psx_vram.ATLAS_COLUMNS)
            src = moves[key]
            moves[key] = (src[0], src[1], src[2], page, x, y)
        else:
            # Snapped rather than refused: a palette typed one halfword
            # off is a slip, not a decision, and rounding it down is
            # what the address can actually express.
            x -= x % 16
            clut_dest[key] = x * 2 + y * psx_vram.VRAM_STRIDE
        keep_pages, keep_cluts = self._keep
        try:
            self.plan = texture_migrate.place(
                self.blob, moves, clut_dest, keep_cluts)
        except Exception as e:
            self.report.setPlainText(f"That placement will not work: {e}")
            self.apply_button.setEnabled(False)
            return
        self._finish_plan()

    def _finish_plan(self):
        """Work out the consequences of the current plan and show them."""
        try:
            self.new_blob = texture_migrate.retarget(self.blob, self.plan)
            self.shards = texture_migrate.shards_for(self.plan, self.vram)
        except Exception as e:
            self.report.setPlainText(f"Cannot apply this: {e}")
            self.apply_button.setEnabled(False)
            return

        free = self.free_map()
        # Anything already holding exactly these bytes is the same
        # texture, not a collision - see texture_migrate.split_shards.
        dest_vram = self.chunk_vram(self.destination.currentData())
        if dest_vram is None:
            dest_vram = bytearray(psx_vram.VRAM_SIZE)
        self.to_write, self.reused = texture_migrate.split_shards(
            self.shards, dest_vram)
        destination = self.destination.currentData()
        wanted = self.wanted_areas()
        clashes, doomed = [], []
        for x, y, w, h, _pixels in self.to_write:
            if free[y:y + h, x:x + w].all():
                continue
            owners = vram_map.owners_of(self.shard_table, (x, y, w, h),
                                        areas=wanted)
            clashes.append(((x, y, w, h), owners))
            # Somebody else's chunk loading after ours does not lose its
            # art - it takes ours. That is not an overwrite, it is a
            # texture that vanishes the moment you walk in there.
            if any(area != destination for area in owners):
                doomed.append(((x, y, w, h), owners))
            # Only what NO chunk declares is untakeable - see
            # state_vram.runtime_only. A chunk's own art is fair game
            # if you are writing into that chunk.
            if self._runtime is not None                     and self._runtime[y:y + h, x:x + w].any():
                doomed.append(((x, y, w, h), {"runtime": 0}))

        # The proof: sample both models against their own VRAM.
        preview = bytearray(self.loaded_vram(
            self.wanted_areas()[-1] if self.wanted_areas() else 1))
        for x, y, w, h, pixels in self.shards:
            for row in range(h):
                at = (y + row) * psx_vram.VRAM_STRIDE + x * 2
                preview[at:at + w * 2] = pixels[row * w * 2:(row + 1) * w * 2]
        checked, bad = texture_migrate.verify(self.blob, self.new_blob,
                                              self.vram, bytes(preview))

        lines = [self.plan.describe(), ""]
        lines.append(f"{len(self.shards)} shard(s), "
                     f"{sum(w * h * 2 for _x, _y, w, h, _p in self.shards)} "
                     f"bytes of VRAM.")
        if self.reused:
            lines.append(
                f"{len(self.reused)} of them are ALREADY there, byte for "
                f"byte - that art is shared rather than copied again, so "
                f"only {len(self.to_write)} shard(s) get written.")
        for rect, owners in clashes:
            who = ", ".join(f"AREA_{a:02X}" for a in sorted(owners)
                            if isinstance(a, int)) or "something unclaimed"
            lines.append(f"CLASH at ({rect[0]}, {rect[1]}) {rect[2]}x{rect[3]}"
                         f": that space belongs to {who}.")
        if doomed:
            lines.append(
                "This cannot be forced. Space owned by another area's chunk "
                "- or written by the game every frame - is not yours to "
                "take: that chunk loads after the resident one and would "
                "overwrite YOUR texture, not the other way round. Write "
                "into the owning area's chunk instead, or move the "
                "placement.")
        elif clashes and self.overwrite.isChecked():
            lines.append(
                f"Overwriting on purpose: your shard is added last to "
                f"AREA_{destination:02X}'s own chunk, so it wins - and the "
                f"art that was there is gone.")
        elif clashes:
            lines.append("Tick 'Overwrite art that is already there' to "
                         "place anyway and destroy it.")
        if self._occupied is None:
            lines.append(
                "No savestate loaded. The display buffers and the resident "
                "set ARE accounted for - those are known. What is not is "
                "what the game uploads itself while running, which is not "
                "written down anywhere on the disc: measured once it came "
                "to about 1000 halfwords in two clusters. If a colour "
                "comes out wrong in game, that is the thing to check.")
        lines.append(f"checked {checked} texel reads: "
                     + ("every one lands on the same texel it does now."
                        if not bad else f"{len(bad)} would change - not safe."))
        # A savestate is a refinement, not a permit. Everything it adds
        # is said in the report; what stops Apply is a real clash or a
        # verify that would change a texel, both of which are knowable
        # from the disc alone.
        self._clashes, self._doomed = clashes, doomed
        allowed = (not clashes) or (self.overwrite.isChecked() and not doomed)
        self.report.setPlainText("\n".join(lines))
        self.apply_button.setEnabled(bool(allowed) and not bad)
        self.refresh_preview()

    # --- preview ------------------------------------------------------

    def refresh_preview(self):
        item = self.areas.currentItem()
        if item is None:
            return
        area = item.data(Qt.ItemDataRole.UserRole)
        vram = self.loaded_vram(area)
        self.canvas.texels_per_halfword = 4
        self.canvas.set_image(vram_index_image(vram))
        rings = []
        if self.plan:
            span = 4
            for n, move in enumerate(self.plan.moves):
                x, y, w, h = move.dest_rect
                rings.append((QRectF(x * span, y, w * span, h), PLACED,
                              f"[{n}] page {move.page}"))
            for old, new in sorted(self.plan.cluts.items()):
                x, y = psx_vram.clut_address_xy(new)
                rings.append((QRectF(x * span, y, 16 * span, 1),
                              PALETTE_MARK, ""))
        self.canvas.highlights = rings
        self.canvas.update()
        loaded = ", ".join(f"{a:02X}" for a in
                           tuple(vram_map.ALWAYS_RESIDENT) + (area,))
        self.preview_label.setText(
            f"AREA_{area:02X} as the game has it - chunks {loaded} together. "
            f"Red is where the textures would go; blue is a palette. "
            f"Anything under a red box is what you would overwrite.")

    # --- applying -----------------------------------------------------

    def apply_plan(self):
        if not self.plan:
            return
        chunk_index = self.destination.currentData()
        if not self.to_write:
            # Everything it needs is already in VRAM - this is a pure
            # retarget, so the IMG is left alone entirely.
            print(f"migrated {self.name}: nothing to copy, every texture "
                  f"and palette it needs is already there.")
            QMessageBox.information(
                self, "Nothing to copy",
                "Every texture and palette this model needs is already in "
                "VRAM, so TOMBA2.IMG is untouched.\n\nOnly the model's UVs "
                "changed, and they are staged - save to keep them.")
            self.accept()
            return
        answer = QMessageBox.question(
            self, "Rewrite TOMBA2.IMG?",
            f"{len(self.shards)} shard(s) are added to AREA_"
            f"{chunk_index:02X}'s chunk in\n{self.cd_folder}\n\n"
            f"That makes TOMBA2.IMG longer, so every chunk after it moves "
            f"and TOMBA2.IDX is rewritten to match. The model's new UVs "
            f"are staged as an ordinary file edit and are not written "
            f"until you save.\n\nGo ahead?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            start, end = chunk_bounds(self.idx, chunk_index)
            with open(self.img, "rb") as f:
                f.seek(start)
                chunk = f.read(end - start)
            new_chunk = img_writer.add_shards(chunk, self.to_write)
            info = img_writer.rebuild(self.idx, self.img,
                                      {chunk_index: new_chunk},
                                      self.idx, self.img)
        except Exception as e:
            QMessageBox.critical(self, "Couldn't write it",
                                 f"TOMBA2.IMG was not changed:\n\n{e}")
            return
        print(f"migrated {self.name}:\n{self.plan.describe()}")
        print(f"TOMBA2.IMG rewritten: {info}")
        QMessageBox.information(
            self, "Done",
            f"TOMBA2.IMG grew by {info['grew_by']} bytes and TOMBA2.IDX was "
            f"rewritten.\n\nThe model's UVs are staged - save the ISO or the "
            f"files to keep them.")
        self.accept()

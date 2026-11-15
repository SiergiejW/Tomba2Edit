"""Animation picker: browse the animations of an actor, Zippo or Tomba,
each playing on the model, and pick one for an action's animation number.

A list on the left to scroll (arrow keys or the wheel), the highlighted
animation looping in a model view beside it. The frames are the ones the
event editor's own previews use - the game's code run in the emulator for an
NPC or Zippo, the verified table for Tomba - and are made on demand, once
per animation."""
import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QVBoxLayout)


class AnimationPicker(QDialog):
    TICK_MS = 40

    def __init__(self, editor, who, current, title='Pick an animation'):
        super().__init__(editor)
        self.editor, self.who, self.choice = editor, who, None
        self.setWindowTitle(title)
        self.resize(940, 640)
        self.frames, self.tick, self.loop = [], 0, None
        self.mode, self.generation = None, 0
        self._origin = self._yaw = self._angle = self._pivots = None
        self._tomba = {}
        self._jobs = []
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self.heading = QLabel({'tomba': 'Tomba’s animations', 'zippo': 'Zippo’s animations'}.get(who, 'This actor’s animations'))
        left.addWidget(self.heading)
        self.list = QListWidget(self)
        self.list.setMinimumWidth(260)
        left.addWidget(self.list, 1)
        layout.addLayout(left)
        right = QVBoxLayout()
        from formats.models.smst_viewer import SMSTViewer
        self.view = SMSTViewer(self)
        self.view.spread = False
        self.view.spread_action.setVisible(False)
        viewer = editor.panel.viewer
        self.view.set_vram(viewer.vram_raw_bytes, viewer.vram_qimage)
        self.view.setMinimumSize(420, 420)
        right.addWidget(self.view, 1)
        self.note = QLabel('')
        self.note.setWordWrap(True)
        right.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Use this animation')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        layout.addLayout(right, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self._advance)
        self.list.currentRowChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        self._current = current
        self._fill()

    # --- the list ---

    def _fill(self):
        scene = self.editor.panel.scene
        if self.who == 'tomba':
            try:
                from game.event_preview import tomba_catalogue
                self._show_list(tomba_catalogue(scene))
            except Exception as exc:
                self.note.setText(f'Tomba’s animation list is not available: {exc}')
            return
        cache = getattr(self.editor, '_catalogues', None)
        if cache is None:
            cache = self.editor._catalogues = {}
        if self.who in cache:
            self._show_list([(n, ticks, loops, '') for n, ticks, loops in cache[self.who]])
            return
        from gui.level.event_editor import CatalogueDecode
        self.note.setText('Reading the animation table…')
        editor = self.editor
        animations = editor.bindings[editor.active].animations if editor.active is not None else None
        job = CatalogueDecode(scene, editor.instance, animations, self.who, self)
        self._jobs.append(job)
        job.decoded.connect(self._catalogue_ready)
        job.start()

    def _catalogue_ready(self, result):
        who, items, error = result
        self.editor._catalogues[who] = items
        if error and not items:
            self.note.setText(f'No animations here: {error}')
            return
        self._show_list([(n, ticks, loops, '') for n, ticks, loops in items])

    def _show_list(self, items):
        self.list.blockSignals(True)
        self.list.clear()
        row = 0
        for n, ticks, loops, name in items:
            text = f'{n} — {ticks} ticks' + (', loops' if loops else '') + (f' — {name}' if name else '')
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, n)
            self.list.addItem(item)
            if n == self._current:
                row = self.list.count() - 1
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(row)
            self._selected(row)
        else:
            self.note.setText('This model has no animation table.')

    # --- playing the highlighted one ---

    def _selected(self, row):
        item = self.list.item(row)
        if item is None:
            return
        number = item.data(Qt.ItemDataRole.UserRole)
        self.timer.stop()
        self.generation += 1
        generation = self.generation
        self.note.setText('Posing…')
        if self.who == 'tomba':
            if number in self._tomba:
                self._play_tomba(self._tomba[number])
                return
            from gui.level.event_editor import TombaDecode
            job = TombaDecode(self.editor.panel.scene, number, self)
            job.number, job.generation = number, generation
            self._jobs.append(job)
            job.decoded.connect(self._tomba_done)
            job.start()
            return
        self.editor._with_frames(self.who, number, lambda entry, g=generation: self._play_actor(entry, g), viewport=False)
        QTimer.singleShot(5000, lambda g=generation: self._still_posing(g))

    def _still_posing(self, generation):
        if generation == self.generation and self.note.text() == 'Posing…':
            self.note.setText(self.editor.status.text() or 'This animation could not be posed.')

    def _tomba_done(self, result):
        job = self.sender()
        if isinstance(result, str):
            if job.generation == self.generation:
                self.note.setText(result)
            return
        self._tomba[job.number] = result
        if job.generation == self.generation:
            self._play_tomba(result)

    def _play_tomba(self, result):
        view = self.view
        view.model_data = result['model']
        view.hidden_groups = set(result['hidden'])
        view.pose_first_group = 0
        view.pose_spares = dict(result['spares'])
        view.pose = None
        view.prepare_buffers()
        view.set_pose(result['frames'][0], result['pivots'])
        view.frame_model()
        self.note.setText(result['note'])
        self.frames, self.tick, self.loop, self.mode, self._pivots = result['frames'], 0, result['loop'], 'tomba', result['pivots']
        self.timer.start()

    def _play_actor(self, entry, generation):
        if generation != self.generation or not self.isVisible():
            return
        frames, loop, origin, yaw = entry[:4]
        if not frames:
            return
        from game.actor_sim import Posed
        from game.actor_assembly import degrees_to_units
        from gui.level.event_editor import baked_facing
        instance = self.editor.instance
        zippo = self.who == 'zippo'
        self._origin = np.array(origin, dtype=float) if zippo else None
        self._yaw = yaw if zippo else None
        posed = Posed('animation', '', frames[0], [], np.array(instance.game_position, dtype=float) if not zippo else self._origin,
                      degrees_to_units(instance.angle) if not zippo else yaw)
        found = baked_facing(posed)
        self._angle = None if found is None else ((found[0] - found[1]) & 0xfff) * 360 / 4096
        self.frames, self.tick, self.loop, self.mode = frames, 0, loop, 'actor'
        self.note.setText(f'{len(frames)} ticks' + (f', loops from tick {loop}' if loop is not None else ', then restarts'))
        self._draw(first=True)
        self.timer.start()

    def _draw(self, first=False):
        model = self.editor._entity_model(self.frames[self.tick], origin=self._origin, yaw=self._yaw, angle=self._angle)
        if model is None:
            self.timer.stop()
            self.note.setText('This model has no geometry to show.')
            return
        view = self.view
        view.pose = None
        view.pose_spares = {}
        view.model_data = model
        view.hidden_groups = set()
        view.prepare_buffers()
        if first:
            view.frame_model(heading=0.0)
        view.update()

    def _advance(self):
        if not self.frames:
            return
        if self.tick + 1 >= len(self.frames):
            self.tick = self.loop if self.loop is not None else 0
        else:
            self.tick += 1
        if self.mode == 'tomba':
            self.view.set_pose(self.frames[self.tick], self._pivots)
            self.view.update()
        else:
            self._draw()

    # --- the answer ---

    def accept(self):
        item = self.list.currentItem()
        self.choice = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._stop()
        super().accept()

    def reject(self):
        self._stop()
        super().reject()

    def _stop(self):
        self.timer.stop()
        self.generation += 1

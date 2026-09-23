"""A piano roll for one sequence.

Editing music by typing MIDI events is nobody's idea of a good time, so
this draws them: time across, pitch up, one rectangle per note. Drag a
note to move it, drag its right edge to lengthen it, draw on empty space
to add one, right-click or Delete to remove.

Everything that is not a note - program changes, loop markers, tempo,
controllers - is carried through untouched and is not shown. It is not
that they do not matter; it is that they have no position in this view
and editing them by accident is worse than not seeing them.

THE BUDGET IS PART OF THE UI

The ten sequences share one fixed region of TOMBA2.SND and on the US
disc they fill it exactly, so "add a few notes" is a question with a
real answer and the answer is often no. The bar along the bottom shows
what the sequence costs and what is left over the whole set, recomputed
on every edit, so it is obvious before Apply rather than after.
"""
import os

from PyQt6.QtCore import (QBuffer, QByteArray, QRect, QSize, Qt, QThread,
                          pyqtSignal)
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSpinBox, QVBoxLayout, QWidget)

from functions import midi, seq, seq_notes, xa

# One channel, one colour. Sixteen distinguishable hues beats a legend.
CHANNEL_COLOURS = [
    "#4f8dd6", "#d65f4f", "#57b36b", "#c9a227", "#8e6fd0", "#3fb0b0",
    "#d67fb0", "#7f8c8d", "#2f6fb5", "#b5452f", "#3d8f52", "#a8851f",
    "#6f4fb0", "#2f8f8f", "#b55f90", "#5f6f70",
]

BLACK_KEYS = {1, 3, 6, 8, 10}
MIN_ROW = 6
MAX_ROW = 28
EDGE = 5                # pixels at a note's right edge that resize it
RULER = 20              # the strip along the top: bar numbers, and where
                        # the playhead is dragged - dragging it in the
                        # note area would fight drawing notes
MIN_SCALE = 0.01        # pixels per tick
MAX_SCALE = 1.20


class _Render(QThread):
    """Playing the sequence on its own instruments, off the GUI thread.

    A whole sequence takes seconds to render - slot 7 is half a minute
    of audio - and doing that on the GUI thread freezes the editor mid
    edit, which is the one moment it must not."""

    done = pyqtSignal(object, str)

    def __init__(self, blob, snd, bank):
        super().__init__()
        self.args = (blob, snd, bank)

    def run(self):
        blob, snd, bank = self.args
        try:
            stereo, _used = seq.render(blob, 0, snd, bank)
            self.done.emit(
                xa.wav_bytes_raw(seq.pcm(stereo), seq.RATE, 2), "")
        except Exception as exc:
            self.done.emit(None, str(exc))


class Audition:
    """One sequence's sound, rendered and played.

    The audio is built by the game's own renderer from a SEQ, so a note
    played here is the note the game plays: the right instrument off the
    right bank, the right envelope, the right pitch. Building a
    throwaway one-note sequence is a roundabout way to hear one note and
    is worth it - the alternative is a second, parallel synth that would
    drift out of agreement with the real one."""

    def __init__(self, parent):
        self.snd = None
        self.bank = None
        self.resolution = 480
        self.tempo = 500000
        self.player = QMediaPlayer(parent)
        self.output = QAudioOutput(parent)
        self.player.setAudioOutput(self.output)
        self._buffer = None
        # False while a single note is being auditioned.
        self.following = True

    def ready(self):
        return self.snd is not None and self.bank is not None

    def play(self, wav, loop=False, follow=True):
        """`follow` says whether this is the sequence playing.

        A one-note audition goes through the same player, and the
        playhead is driven from that player's position - so without
        this, drawing a note yanked the playhead back to the top and
        set it crawling through the half second the note lasted."""
        self.following = follow
        self.stop()
        self.player.setLoops(QMediaPlayer.Loops.Infinite if loop else 1)
        # Kept on the instance: the player reads from it while it plays,
        # and letting it go collects it mid-note.
        self._buffer = QBuffer()
        self._buffer.setData(QByteArray(wav))
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self._buffer)
        self.player.play()

    def stop(self):
        self.player.stop()

    def note_wav(self, note, program):
        """One note on its own, as WAV bytes - or None if it can't be."""
        if not self.ready():
            return None
        channel = note.channel & 0x0F
        # Long enough to hear the instrument speak, short enough that
        # holding a drag does not queue up seconds of audio.
        length = max(1, min(note.length, self.resolution * 2))
        events = [(0, 0xC0 | channel, program, 0),
                  (0, 0x90 | channel, note.key, note.velocity),
                  (length, 0x90 | channel, note.key, 0)]
        blob = midi.events_to_seq(events, self.resolution, self.tempo)
        try:
            stereo, _used = seq.render(blob, 0, self.snd, self.bank)
        except Exception:
            return None
        return xa.wav_bytes_raw(seq.pcm(stereo), seq.RATE, 2)


class PianoRoll(QWidget):
    """The drawing and the dragging."""

    changed = pyqtSignal()
    # A note was created or moved onto a new pitch and should be heard.
    audition = pyqtSignal(object)
    # The horizontal scale changed, so the dialog's zoom box can follow.
    zoomed = pyqtSignal(float)
    # The playhead was dragged somewhere, in ticks.
    scrubbed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.notes = []
        self.resolution = 480
        self.row = 12                   # pixels per semitone
        self.per_tick = 0.06            # pixels per tick
        self.low, self.high = 48, 72
        self.channel = None             # None = every channel
        self.snap = 0                   # ticks; 0 = off
        self.selected = None
        self._drag = None
        self.playhead = 0
        self._scrubbing = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- geometry ------------------------------------------------------

    def set_notes(self, notes, resolution):
        self.notes = notes
        self.resolution = max(1, resolution)
        self.low, self.high = seq_notes.key_range(notes)
        self.selected = None
        self._resize()

    def _resize(self):
        width = int(max(1, seq_notes.span(self.notes) + self.resolution * 2)
                    * self.per_tick) + 40
        height = (self.high - self.low + 1) * self.row + 1 + RULER
        self.setMinimumSize(QSize(max(400, width), max(120, height)))
        self.updateGeometry()
        self.update()

    def x_of(self, tick):
        return int(tick * self.per_tick)

    def tick_of(self, x):
        return max(0, int(x / self.per_tick))

    def y_of(self, key):
        return int((self.high - key) * self.row) + RULER

    def key_of(self, y):
        return int(self.high - max(0, y - RULER) // self.row)

    def visible(self):
        if self.channel is None:
            return self.notes
        return [n for n in self.notes if n.channel == self.channel]

    def _at(self, position):
        """(note, on its right edge) under the cursor, nearest last."""
        for note in reversed(self.visible()):
            rect = self._rect(note)
            if rect.contains(position):
                return note, position.x() >= rect.right() - EDGE
        return None, False

    def _rect(self, note):
        x = self.x_of(note.tick)
        width = max(3, self.x_of(note.tick + note.length) - x)
        return QRect(x, self.y_of(note.key), width, self.row - 1)

    # -- painting ------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1e1f22"))
        painter.fillRect(0, 0, self.width(), RULER, QColor("#2a2c30"))
        width = self.width()

        # Key lanes: the black notes shaded, so pitch is readable
        # without a keyboard drawn down the side.
        for key in range(self.low, self.high + 1):
            y = self.y_of(key)
            if key % 12 in BLACK_KEYS:
                painter.fillRect(0, y, width, self.row - 1, QColor("#26282c"))
            if key % 12 == 0:
                painter.setPen(QPen(QColor("#3a3d42")))
                painter.drawLine(0, y + self.row - 1, width, y + self.row - 1)

        # Bar lines every four beats, beat lines between, and the bar
        # number in the ruler so a position can be talked about.
        beat = self.resolution
        painter.setFont(QFont("", 7))
        if beat * self.per_tick >= 3:
            tick = 0
            while self.x_of(tick) < width:
                x = self.x_of(tick)
                bar = (tick // beat) % 4 == 0
                painter.setPen(QPen(QColor("#43464c" if bar else "#2c2f33")))
                painter.drawLine(x, RULER, x, self.height())
                if bar:
                    painter.setPen(QPen(QColor("#7e838c")))
                    painter.drawLine(x, RULER - 5, x, RULER)
                    if beat * 4 * self.per_tick >= 26:
                        painter.drawText(x + 3, RULER - 6,
                                         str(tick // (beat * 4) + 1))
                tick += beat

        for note in self.visible():
            rect = self._rect(note)
            colour = QColor(CHANNEL_COLOURS[note.channel % 16])
            if note is self.selected:
                colour = colour.lighter(150)
            painter.fillRect(rect, colour)
            painter.setPen(QPen(colour.darker(160)))
            painter.drawRect(rect)

        # The playhead last, over everything, so it is never hidden
        # behind a note.
        x = self.x_of(self.playhead)
        painter.setPen(QPen(QColor("#e8b84b"), 1))
        painter.drawLine(x, 0, x, self.height())
        painter.fillRect(x - 4, 0, 9, 7, QColor("#e8b84b"))

        if not self.notes:
            painter.setPen(QPen(QColor("#8a8f98")))
            painter.setFont(QFont("", 10))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No notes in this sequence.\n"
                             "Drag on empty space to draw one.")

    # -- editing -------------------------------------------------------

    def _snapped(self, tick):
        if self.snap <= 0:
            return max(0, tick)
        return max(0, int(round(tick / self.snap)) * self.snap)

    def set_playhead(self, tick):
        tick = max(0, int(tick))
        if tick != self.playhead:
            self.playhead = tick
            self.update()

    def mousePressEvent(self, event):
        position = event.position().toPoint()
        # The ruler is the playhead's, not the note grid's: dragging it
        # anywhere else would be indistinguishable from drawing a note.
        if position.y() < RULER and event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing = True
            self.set_playhead(self._snapped(self.tick_of(position.x())))
            self.scrubbed.emit(self.playhead)
            return
        note, on_edge = self._at(position)
        if event.button() == Qt.MouseButton.RightButton:
            if note is not None:
                self.notes.remove(note)
                self.selected = None
                self.changed.emit()
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if note is None:
            # Draw a new one, on the channel being filtered to - or the
            # first channel in use when showing everything, because a
            # note has to go somewhere and channel 0 is usually unused.
            channel = self.channel
            if channel is None:
                used = seq_notes.channels_used(self.notes)
                channel = used[0] if used else 0
            note = seq_notes.Note(
                self._snapped(self.tick_of(position.x())),
                self.snap or self.resolution // 4,
                channel, self.key_of(position.y()), 100)
            self.notes.append(note)
            self.selected = note
            self._drag = ("length", position, note.tick, note.length, note.key)
            self.audition.emit(note)
            self.changed.emit()
            self.update()
            return
        self.selected = note
        self._drag = (("length" if on_edge else "move"), position,
                      note.tick, note.length, note.key)
        self.update()

    def mouseMoveEvent(self, event):
        position = event.position().toPoint()
        if self._scrubbing:
            self.set_playhead(self._snapped(self.tick_of(position.x())))
            self.scrubbed.emit(self.playhead)
            return
        if self._drag is None:
            if position.y() < RULER:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                return
            _note, on_edge = self._at(position)
            self.setCursor(Qt.CursorShape.SizeHorCursor if on_edge
                           else Qt.CursorShape.ArrowCursor)
            return
        kind, origin, tick0, length0, key0 = self._drag
        note = self.selected
        if note is None:
            return
        moved = self.tick_of(position.x()) - self.tick_of(origin.x())
        if kind == "length":
            note.length = max(1, self._snapped(length0 + moved) or 1)
        else:
            note.tick = self._snapped(max(0, tick0 + moved))
            was = note.key
            note.key = max(0, min(127, key0 + (origin.y() - position.y())
                                  // self.row))
            if note.key != was:
                self.audition.emit(note)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, _event):
        if self._scrubbing:
            self._scrubbing = False
            return
        if self._drag is not None:
            self._drag = None
            self._resize()
            self.changed.emit()

    def wheelEvent(self, event):
        """Ctrl and the wheel zooms; Ctrl+Shift stretches it vertically.

        Anchored on whatever is under the pointer, so zooming in on a
        bar keeps that bar under the cursor instead of throwing the view
        somewhere else and making it be found again."""
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.row = max(MIN_ROW, min(MAX_ROW,
                                        self.row + (1 if steps > 0 else -1)))
            self._resize()
            event.accept()
            return
        position = event.position().toPoint()
        anchor = self.tick_of(position.x())
        bar = self._scrollbar()
        # Where the pointer is inside the visible window, as opposed to
        # inside the widget - the widget is wider than the window and
        # scrolled within it.
        seen_at = position.x() - (bar.value() if bar else 0)

        self.per_tick = max(MIN_SCALE, min(
            MAX_SCALE, self.per_tick * (1.25 ** steps)))
        self._resize()
        self.zoomed.emit(self.per_tick)
        if bar is not None:
            bar.setValue(max(0, self.x_of(anchor) - seen_at))
        event.accept()

    def _scrollbar(self):
        """The scroll area's horizontal bar, if this is inside one."""
        holder = self.parentWidget()
        while holder is not None:
            if hasattr(holder, "horizontalScrollBar"):
                return holder.horizontalScrollBar()
            holder = holder.parentWidget()
        return None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.selected in self.notes:
                self.notes.remove(self.selected)
                self.selected = None
                self.changed.emit()
                self.update()
            return
        step = 12 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if self.selected is not None:
            if event.key() == Qt.Key.Key_Up:
                self.selected.key = min(127, self.selected.key + step)
            elif event.key() == Qt.Key.Key_Down:
                self.selected.key = max(0, self.selected.key - step)
            else:
                super().keyPressEvent(event)
                return
            self.audition.emit(self.selected)
            self.changed.emit()
            self.update()
            return
        super().keyPressEvent(event)


class SequenceEditor(QDialog):
    """The piano roll, with the budget under it and Apply at the end."""

    def __init__(self, data, at, slot, budget=None, snd=None, bank=None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit sequence - slot {slot}")
        self.resize(940, 600)
        self._budget = budget or (lambda blob: None)
        self.result_blob = None

        self.resolution, self.tempo = seq.header(data, at)
        events = seq.events(data, at)
        self.notes, self.others = seq_notes.to_notes(events)
        self.original_size = seq.length(data, at)

        # Hearing it. Needs the sound file and which bank this sequence
        # plays on; without them the editor still edits, it just cannot
        # make a sound, and says so rather than showing dead buttons.
        self.sound = Audition(self)
        self.sound.snd = snd
        self.sound.bank = bank
        self.sound.resolution = self.resolution
        self.sound.tempo = self.tempo
        self._render = None
        self._rendered = None        # the WAV for the edit as it stands
        self._dirty = True           # ... or None/True when it is stale

        self.roll = PianoRoll()
        self.roll.set_notes(self.notes, self.resolution)
        self.roll.changed.connect(self._recount)
        self.roll.audition.connect(self._hear_note)
        self.roll.zoomed.connect(self._zoomed)
        self.roll.scrubbed.connect(self._scrubbed)

        # Where the playhead belongs at any moment of the audio. Built
        # from the events rather than from a tempo alone, so a tempo
        # change or a loop does not leave the line behind the sound.
        self._timeline = seq_notes.timeline(events, self.resolution,
                                            self.tempo)
        self.sound.player.positionChanged.connect(self._followed)

        area = QScrollArea()
        area.setWidget(self.roll)
        area.setWidgetResizable(False)

        self.channel_pick = QComboBox()
        self.channel_pick.addItem("All channels", None)
        for channel in seq_notes.channels_used(self.notes):
            self.channel_pick.addItem(f"Channel {channel}", channel)
        self.channel_pick.currentIndexChanged.connect(self._channel)

        self.snap_pick = QComboBox()
        for label, ticks in (("No snap", 0), ("1/16", self.resolution // 4),
                             ("1/8", self.resolution // 2),
                             ("1/4", self.resolution)):
            self.snap_pick.addItem(label, ticks)
        self.snap_pick.setCurrentIndex(1)
        self.snap_pick.currentIndexChanged.connect(self._snap)
        self.roll.snap = self.snap_pick.currentData()

        self.zoom = QSpinBox()
        self.zoom.setRange(2, 60)
        self.zoom.setValue(6)
        self.zoom.setSuffix(" %")
        self.zoom.setToolTip("How wide a beat is drawn")
        self.zoom.valueChanged.connect(self._zoom)

        # Zippo sits with the numbers. The byte budget is the one thing
        # in here that says no, and a bare figure in a corner reads as a
        # telling-off; next to him it reads as him telling you.
        self.mascot = QLabel()
        zippo = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "icons", "tomba", "zippo.png")
        if os.path.isfile(zippo):
            picture = QPixmap(zippo)
            if not picture.isNull():
                self.mascot.setPixmap(picture.scaledToHeight(
                    38, Qt.TransformationMode.SmoothTransformation))
        self.mascot.setAlignment(Qt.AlignmentFlag.AlignBottom
                                 | Qt.AlignmentFlag.AlignLeft)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignVCenter
                                 | Qt.AlignmentFlag.AlignLeft)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(self.mascot, 0)
        footer.addWidget(self.status, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(
            QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)

        self.play_button = QPushButton("Play")
        self.play_button.setToolTip(
            "Play the sequence as it stands now, on its own instruments")
        self.play_button.clicked.connect(self._play)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setEnabled(False)
        self.loop_box = QCheckBox("Loop")
        self.loop_box.setToolTip("Keep repeating until Stop")
        self.hear_notes = QCheckBox("Hear notes")
        self.hear_notes.setChecked(True)
        self.hear_notes.setToolTip(
            "Sound each note as it is drawn or moved to a new pitch")
        if not self.sound.ready():
            for widget in (self.play_button, self.stop_button,
                           self.loop_box, self.hear_notes):
                widget.setEnabled(False)
                widget.setToolTip("The sound bank isn't loaded, so this "
                                  "sequence can't be played here.")

        top = QHBoxLayout()
        top.addWidget(self.play_button)
        top.addWidget(self.stop_button)
        top.addWidget(self.loop_box)
        top.addWidget(self.hear_notes)
        top.addSpacing(12)
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.channel_pick)
        top.addWidget(QLabel("Snap:"))
        top.addWidget(self.snap_pick)
        top.addWidget(QLabel("Zoom:"))
        top.addWidget(self.zoom)
        top.addStretch(1)
        # A one-line instruction here sets the dialog's minimum width,
        # and a helpful sentence is wide enough to force the window
        # open half again as far as it needs to be. It lives on the
        # roll instead, where it is asked for rather than shouted.
        self.roll.setToolTip(
            "Drag a note to move it, its right edge to lengthen it.\n"
            "Drag on empty space to draw one, right-click to delete.\n"
            "Ctrl+wheel zooms, Ctrl+Shift+wheel stretches it vertically.\n"
            "Drag the strip along the top to move the playhead.")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(area, 1)
        layout.addLayout(footer)
        layout.addWidget(buttons)
        self._recount()

    # -- controls ------------------------------------------------------

    def _channel(self):
        self.roll.channel = self.channel_pick.currentData()
        self.roll.update()

    def _snap(self):
        self.roll.snap = self.snap_pick.currentData()

    def _zoom(self, value):
        self.roll.per_tick = value / 100.0
        self.roll._resize()

    def _zoomed(self, per_tick):
        """The roll was zoomed with the wheel; move the box to match."""
        self.zoom.blockSignals(True)
        self.zoom.setValue(max(self.zoom.minimum(),
                               min(self.zoom.maximum(),
                                   int(round(per_tick * 100)))))
        self.zoom.blockSignals(False)

    def _followed(self, milliseconds):
        """Walk the playhead along with the audio."""
        if not self.sound.following:
            return
        self.roll.set_playhead(
            seq_notes.tick_at(self._timeline, milliseconds / 1000.0))

    def _scrubbed(self, tick):
        """Dragging the playhead seeks what is playing."""
        if self.sound.player.duration() > 0:
            self.sound.player.setPosition(
                int(seq_notes.seconds_at(self._timeline, tick) * 1000))

    def encoded(self):
        """The edited sequence as SEQ bytes."""
        events = seq_notes.to_events(self.notes, self.others)
        return midi.events_to_seq(events, self.resolution, self.tempo)

    # -- hearing it ----------------------------------------------------

    def _program_for(self, channel, tick):
        """The instrument that channel is set to by `tick`.

        A sequence changes programs as it goes, so a note drawn late in
        the piece is not necessarily the instrument the piece opens
        with - and playing it as the wrong one is more confusing than
        playing nothing."""
        program = 0
        for when, status, a, _b in self.others:
            if when > tick:
                break
            if status & 0xF0 == 0xC0 and status & 0x0F == channel:
                program = a
        return program

    def _hear_note(self, note):
        if not self.hear_notes.isChecked() or not self.sound.ready():
            return
        wav = self.sound.note_wav(
            note, self._program_for(note.channel, note.tick))
        if wav:
            self.sound.play(wav, follow=False)

    def _play(self):
        if not self.sound.ready():
            return
        if self._rendered is not None and not self._dirty:
            self.sound.play(self._rendered, self.loop_box.isChecked())
            if self.roll.playhead:
                self.sound.player.setPosition(int(seq_notes.seconds_at(
                    self._timeline, self.roll.playhead) * 1000))
            self.stop_button.setEnabled(True)
            return
        try:
            blob = self.encoded()
        except Exception as exc:
            self.status.setText(f"Can't play that: {exc}")
            return
        self.play_button.setEnabled(False)
        self.status.setText("Rendering on the sound bank...")
        self._render = _Render(blob, self.sound.snd, self.sound.bank)
        self._render.done.connect(self._played)
        self._render.start()

    def _played(self, wav, note):
        self.play_button.setEnabled(True)
        if wav is None:
            self.status.setText(f"Could not play that: {note}")
            return
        self._rendered = wav
        self.sound.play(wav, self.loop_box.isChecked())
        if self.roll.playhead:
            self.sound.player.setPosition(int(seq_notes.seconds_at(
                self._timeline, self.roll.playhead) * 1000))
        self.stop_button.setEnabled(True)
        # Puts the byte count back over "Rendering..." - and has to come
        # before the flag is cleared, because counting marks the render
        # stale and here it is exactly as fresh as it gets.
        self._recount()
        self._dirty = False

    def _stop(self):
        self.sound.stop()
        self.stop_button.setEnabled(False)

    def closeEvent(self, event):
        # A render still running holds the sound file; Qt takes the
        # process down noisily if the thread outlives the dialog.
        self.sound.stop()
        if self._render is not None and self._render.isRunning():
            self._render.wait(3000)
        super().closeEvent(event)

    def _recount(self):
        self._dirty = True
        self._timeline = seq_notes.timeline(
            seq_notes.to_events(self.notes, self.others),
            self.resolution, self.tempo)
        try:
            blob = self.encoded()
        except Exception as exc:
            self.status.setText(f"Can't encode that: {exc}")
            self.apply_button.setEnabled(False)
            return
        size = len(blob)
        note = self._budget(blob)
        difference = size - self.original_size
        shape = ("same size as before" if not difference
                 else f"{abs(difference)} "
                      f"{'over' if difference > 0 else 'under'}")
        channels = len(seq_notes.channels_used(self.notes))
        over = note is not None and "too big" in note
        self.status.setText(
            f"<b>{len(self.notes)}</b> notes on <b>{channels}</b> "
            f"channel{'' if channels == 1 else 's'} &nbsp;·&nbsp; "
            f"<b>{size}</b> bytes <span style='color:#8a8f98'>"
            f"({shape})</span>"
            + (f"<br><span style='color:{'#d65f4f' if over else '#8a8f98'}'>"
               f"{note}</span>" if note else ""))
        self.apply_button.setEnabled(not over)

    def _apply(self):
        try:
            self.result_blob = self.encoded()
        except Exception as exc:
            self.status.setText(f"Can't encode that: {exc}")
            return
        self.accept()

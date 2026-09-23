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
from PyQt6.QtGui import (QColor, QFont, QKeySequence, QPainter, QPen,
                         QPixmap, QShortcut)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSpinBox, QVBoxLayout, QWidget)

from formats.audio import midi
from formats.audio import seq
from formats.audio import seq_notes
from formats.audio import xa
from gui import theme
from gui.widgets import mascot
from formats.audio.transport_icons import set_glyph

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
PLAY_HINT = ("Play the sequence as it stands now, on its own "
             "instruments")
PAUSE_HINT = "Pause, and carry on from here"
MIN_SCALE = 0.01        # pixels per tick
MAX_SCALE = 1.20
UNDO_DEPTH = 100        # snapshots kept; a few hundred notes each, so
                        # the whole stack is smaller than one rendered
                        # second of the audio it is editing


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
        self.player.stop()
        # Detach before the old buffer goes. Replacing it while the
        # player still held it was a use-after-free, and clicking notes
        # quickly - which is exactly what auditioning invites - took the
        # whole program down with it.
        self.player.setSourceDevice(None)
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
        self.player.setLoops(QMediaPlayer.Loops.Infinite if loop else 1)
        # Parented to the player so Qt outlives the local either way.
        self._buffer = QBuffer(self.player)
        self._buffer.setData(QByteArray(wav))
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self._buffer)
        self.player.play()

    def stop(self):
        self.player.stop()

    def pause(self):
        self.player.pause()

    def resume(self):
        self.player.play()

    def state(self):
        return self.player.playbackState()

    def paused(self):
        return self.state() == QMediaPlayer.PlaybackState.PausedState

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
    # A note was selected, so the instrument box can follow it.
    picked = pyqtSignal(object)
    # About to change something - the moment to remember for undo. Sent
    # once per gesture rather than once per pixel of a drag, so undoing
    # a drag puts the note back where it started rather than one step
    # along it.
    begin_edit = pyqtSignal()

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
        self._erasing = False
        self._panning = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- geometry ------------------------------------------------------

    def set_notes(self, notes, resolution):
        self.notes = notes
        self.resolution = max(1, resolution)
        self.low, self.high = seq_notes.key_range(notes)
        self.selected = None
        self._resize()

    def refresh_notes(self):
        """The list changed underneath - an undo or a redo put other
        notes in it. The key range only widens: narrowing it would
        scroll the view on undo, and an undo that also moves the page
        is hard to follow."""
        low, high = seq_notes.key_range(self.notes)
        self.low = min(self.low, low)
        self.high = max(self.high, high)
        # The note it pointed at may not be in the list any more.
        self.selected = None
        self._resize()
        self.update()

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
        # Asked for on every repaint rather than cached: the theme can
        # be changed with the editor open, and a roll that stayed dark
        # under a light window would be the bug this fixes.
        paint = theme.roll_colours()
        bright = theme.is_bright()
        painter.fillRect(self.rect(), QColor(paint["ground"]))
        painter.fillRect(0, 0, self.width(), RULER, QColor(paint["ruler"]))
        width = self.width()

        # Key lanes: the black notes shaded, so pitch is readable
        # without a keyboard drawn down the side.
        lane = QColor(paint["lane"])
        octave = QPen(QColor(paint["octave"]))
        for key in range(self.low, self.high + 1):
            y = self.y_of(key)
            if key % 12 in BLACK_KEYS:
                painter.fillRect(0, y, width, self.row - 1, lane)
            if key % 12 == 0:
                painter.setPen(octave)
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
                painter.setPen(QPen(QColor(
                    paint["bar"] if bar else paint["beat"])))
                painter.drawLine(x, RULER, x, self.height())
                if bar:
                    painter.setPen(QPen(QColor(paint["mark"])))
                    painter.drawLine(x, RULER - 5, x, RULER)
                    if beat * 4 * self.per_tick >= 26:
                        painter.drawText(x + 3, RULER - 6,
                                         str(tick // (beat * 4) + 1))
                tick += beat

        for note in self.visible():
            rect = self._rect(note)
            colour = QColor(CHANNEL_COLOURS[note.channel % 16])
            if note is self.selected:
                # Away from the background, not simply lighter: on a
                # light theme a lighter note is a fainter note, which
                # is the wrong way round for the one that is selected.
                colour = colour.darker(135) if bright else colour.lighter(150)
            painter.fillRect(rect, colour)
            painter.setPen(QPen(colour.darker(160),
                                2 if note is self.selected else 1))
            painter.drawRect(rect)

        # The playhead last, over everything, so it is never hidden
        # behind a note.
        x = self.x_of(self.playhead)
        head = QColor(paint["playhead"])
        painter.setPen(QPen(head, 1))
        painter.drawLine(x, 0, x, self.height())
        painter.fillRect(x - 4, 0, 9, 7, head)

        if not self.notes:
            painter.setPen(QPen(QColor(paint["text"])))
            painter.setFont(QFont("", 10))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No notes in this sequence.\n"
                             "Drag on empty space to draw one.")

    # -- editing -------------------------------------------------------

    def _snapped(self, tick):
        if self.snap <= 0:
            return max(0, tick)
        return max(0, int(round(tick / self.snap)) * self.snap)

    def _erase_at(self, position):
        """Delete whatever note is under the cursor, if any."""
        note, _edge = self._at(position)
        if note is None:
            return
        self.notes.remove(note)
        if self.selected is note:
            self.selected = None
        self.changed.emit()
        self.update()

    def _scrollbars(self):
        """(horizontal, vertical) of the scroll area around this."""
        holder = self.parentWidget()
        while holder is not None:
            if hasattr(holder, "horizontalScrollBar"):
                return holder.horizontalScrollBar(), holder.verticalScrollBar()
            holder = holder.parentWidget()
        return None

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
        if event.button() == Qt.MouseButton.MiddleButton:
            # Drag the paper around, the way every other canvas does.
            bars = self._scrollbars()
            if bars:
                self._panning = (event.globalPosition().toPoint(),
                                 bars[0].value(), bars[1].value())
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        note, on_edge = self._at(position)
        if event.button() == Qt.MouseButton.RightButton:
            # Held down it keeps rubbing out whatever it is dragged
            # over, which is what deleting a run of notes wants to be.
            self._erasing = True
            self.begin_edit.emit()
            self._erase_at(position)
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
            self.begin_edit.emit()
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
        self.begin_edit.emit()
        self._drag = (("length" if on_edge else "move"), position,
                      note.tick, note.length, note.key)
        # Picking a note plays it, the way picking one anywhere else
        # does - it is how you find out what you have got hold of.
        self.audition.emit(note)
        self.picked.emit(note)
        self.update()

    def mouseMoveEvent(self, event):
        position = event.position().toPoint()
        if self._panning is not None:
            origin, at_x, at_y = self._panning
            moved = event.globalPosition().toPoint() - origin
            bars = self._scrollbars()
            if bars:
                bars[0].setValue(at_x - moved.x())
                bars[1].setValue(at_y - moved.y())
            return
        if self._erasing:
            self._erase_at(position)
            return
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
        if self._panning is not None:
            self._panning = None
            self.unsetCursor()
            return
        if self._erasing:
            self._erasing = False
            self._resize()
            return
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
        bars = self._scrollbars()
        return bars[0] if bars else None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.selected in self.notes:
                self.begin_edit.emit()
                self.notes.remove(self.selected)
                self.selected = None
                self.changed.emit()
                self.update()
            return
        step = 12 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if self.selected is not None:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.begin_edit.emit()
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
                 instrument_names=None, applyable=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            f"Edit sequence - {slot}" if isinstance(slot, str)
            else f"Edit sequence - slot {slot}")
        self.resize(940, 600)
        self._budget = budget or (lambda blob: None)
        # False for a sequence that can be looked at and played with but
        # not written back - an overlay's, which lives in A0x.BIN.
        self.applyable = applyable
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
        # Undo as whole snapshots of the notes rather than a list of
        # reversible operations: a sequence is a few hundred notes, a
        # snapshot is a few hundred small objects, and the alternative
        # is an inverse for every gesture and a bug in whichever one
        # gets written last.
        self._undo = []
        self._redo = []
        self._render = None
        # Where playback has to be put back to once the audio is
        # running again - see _resume_at.
        self._pending_seek = 0
        self._resume_to = 0
        self._rendered = None        # the WAV for the edit as it stands
        self._rendered_channel = None    # ... and which channel it holds
        self._dirty = True           # ... or None/True when it is stale

        self.roll = PianoRoll()
        self.roll.set_notes(self.notes, self.resolution)
        self.roll.changed.connect(self._recount)
        self.roll.audition.connect(self._hear_note)
        self.roll.zoomed.connect(self._zoomed)
        self.roll.scrubbed.connect(self._scrubbed)
        self.roll.picked.connect(self._picked)
        self.roll.begin_edit.connect(self._push_undo)

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

        # Which instrument a channel plays. A program is an index into
        # the VAB this sequence runs on, so the list is whatever that
        # bank actually holds - not a General MIDI list, which would
        # name instruments this disc does not have.
        self.instrument_names = instrument_names or {}
        self.instrument_pick = QComboBox()
        self.instrument_pick.setMinimumWidth(150)
        self.instrument_pick.setToolTip(
            "The instrument the shown channel plays. Changing it rewrites "
            "that channel's program changes")
        for program in self._bank_programs():
            self.instrument_pick.addItem(self._instrument_label(program),
                                         program)
        self.instrument_pick.currentIndexChanged.connect(self._instrument)
        self.instrument_pick.setEnabled(False)

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
        self.mascot = mascot.label()

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignVCenter
                                 | Qt.AlignmentFlag.AlignLeft)

        # What the mouse and keyboard do, next to him rather than in a
        # tooltip nobody hovers over. It never changes, so it costs one
        # line of layout and saves the guessing.
        self.help = QLabel(
            f"<span style='color:{theme.colours()['dim']}'>"
            "<b>Drag</b> a note to move · its <b>right edge</b> to "
            "lengthen · <b>empty space</b> to draw · <b>right-drag</b> "
            "to rub out<br>"
            "<b>Middle-drag</b> pans · <b>Ctrl+wheel</b> zooms "
            "(<b>+Shift</b> taller) · <b>top strip</b> moves the "
            "playhead · <b>↑↓</b> nudge · <b>Del</b> removes · "
            "<b>Ctrl+Z</b> undoes"
            "</span>")
        self.help.setWordWrap(True)

        beside = QVBoxLayout()
        beside.setContentsMargins(0, 0, 0, 0)
        beside.setSpacing(2)
        beside.addWidget(self.status)
        beside.addWidget(self.help)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(self.mascot, 0)
        footer.addLayout(beside, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(
            QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self._apply)
        if not self.applyable:
            self.apply_button.setEnabled(False)
            self.apply_button.setToolTip(
                "This sequence can't be written back yet - see the note "
                "beside Zippo.")
        buttons.rejected.connect(self.reject)

        # One button for both: it shows what pressing it will do, which
        # two side by side never quite manage - a pause that turns into
        # a play, sitting next to a play, is a riddle.
        self.play_button = QPushButton()
        set_glyph(self.play_button, "play", PLAY_HINT)
        self.play_button.clicked.connect(self._play_pause)
        self.stop_button = QPushButton()
        set_glyph(self.stop_button, "stop")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setEnabled(False)

        self.undo_button = QPushButton()
        set_glyph(self.undo_button, "undo", "Undo  (Ctrl+Z)")
        self.undo_button.clicked.connect(self._undo_once)
        self.undo_button.setEnabled(False)
        self.redo_button = QPushButton()
        set_glyph(self.redo_button, "redo", "Redo  (Ctrl+Shift+Z)")
        self.redo_button.clicked.connect(self._redo_once)
        self.redo_button.setEnabled(False)
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

        # Two rows rather than one: every control added to a single row
        # sets the dialog's minimum width, and by the time the transport
        # and the four pickers were all on it the window could not open
        # narrower than 1292 pixels.
        transport = QHBoxLayout()
        transport.addWidget(self.play_button)
        transport.addWidget(self.stop_button)
        transport.addWidget(self.loop_box)
        transport.addWidget(self.hear_notes)
        transport.addStretch(1)
        transport.addWidget(self.undo_button)
        transport.addWidget(self.redo_button)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.channel_pick)
        top.addWidget(QLabel("Plays:"))
        top.addWidget(self.instrument_pick)
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
        layout.addLayout(transport)
        layout.addLayout(top)
        layout.addWidget(area, 1)
        layout.addLayout(footer)
        layout.addWidget(buttons)
        self._show_instrument()
        self._recount()

        for keys, slot in ((QKeySequence.StandardKey.Undo, self._undo_once),
                           (QKeySequence.StandardKey.Redo, self._redo_once),
                           ("Ctrl+Shift+Z", self._redo_once)):
            # Window-wide: the roll has the focus while editing, but so
            # can a spin box, and Ctrl+Z has to mean the same in both.
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)
        # Qt does not tell the buttons the sound ran out on its own.
        self.sound.player.playbackStateChanged.connect(self._playback_state)
        self.sound.player.mediaStatusChanged.connect(self._media_status)

    # -- controls ------------------------------------------------------

    # -- instruments ---------------------------------------------------

    def _bank_programs(self):
        """Which programs this sequence's sound bank actually has."""
        if self.sound.snd is None or self.sound.bank is None:
            return []
        try:
            return sorted(seq.instruments(self.sound.snd, self.sound.bank))
        except Exception:
            return []

    def _instrument_label(self, program):
        name = self.instrument_names.get(program)
        return f"{program}  {name}" if name else f"Program {program}"

    def _focus_channel(self):
        """The channel the instrument box is talking about.

        Whichever is being shown, or the selected note's when showing
        everything - otherwise "change the instrument" would have to
        mean all sixteen at once, which is never what is wanted."""
        if self.roll.channel is not None:
            return self.roll.channel
        if self.roll.selected is not None:
            return self.roll.selected.channel
        return None

    def _show_instrument(self):
        """Point the box at whatever the focused channel plays now."""
        channel = self._focus_channel()
        enabled = channel is not None and self.instrument_pick.count() > 0
        self.instrument_pick.setEnabled(enabled)
        if not enabled:
            return
        program = self._program_for(channel, 1 << 30)
        index = self.instrument_pick.findData(program)
        self.instrument_pick.blockSignals(True)
        if index >= 0:
            self.instrument_pick.setCurrentIndex(index)
        self.instrument_pick.blockSignals(False)

    def _instrument(self):
        """Give the focused channel a different instrument.

        Every program change that channel has is rewritten, rather than
        only the one before the cursor: a sequence that switches
        instrument part way would otherwise change back a bar later and
        look as though the edit had not taken. A channel with none gets
        one at the top."""
        channel = self._focus_channel()
        program = self.instrument_pick.currentData()
        if channel is None or program is None:
            return
        found = False
        rewritten = []
        for tick, status, a, b in self.others:
            if status & 0xF0 == 0xC0 and status & 0x0F == channel:
                rewritten.append((tick, status, program, b))
                found = True
            else:
                rewritten.append((tick, status, a, b))
        if not found:
            rewritten.append((0, 0xC0 | channel, program, 0))
        self.others = sorted(rewritten, key=lambda item: item[0])
        playing = self._is_playing()
        at = self.roll.playhead
        self._recount()
        if self.roll.selected is not None and self.hear_notes.isChecked():
            self._hear_note(self.roll.selected)
        # Same as switching channel: the point of changing the
        # instrument while it plays is to hear the difference, and the
        # rendered audio is the old instrument until it is built again.
        if playing:
            self._play(resume_at=at)

    def _picked(self, _note):
        self._show_instrument()

    def _is_playing(self):
        """Whether the sequence - not a note - is sounding right now."""
        return (self.sound.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
                and self.sound.following)

    def _channel(self):
        playing = self._is_playing()
        at = self.roll.playhead
        self.roll.channel = self.channel_pick.currentData()
        self.roll.update()
        self._show_instrument()
        # What is cached is the old selection's audio.
        if self._rendered_channel != self.roll.channel:
            self._dirty = True
        self._recount()
        # Switching parts mid-play should switch what is heard, from
        # where it had got to - picking a channel and then having to
        # stop and start again to hear it is the long way round.
        if playing and self._dirty:
            self._play(resume_at=at)

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

    def encoded(self, channel=None):
        """The edited sequence as SEQ bytes.

        `channel` keeps only that channel's notes, for hearing one part
        on its own. The other events stay: a tempo change or a loop is
        what the part is played against, and dropping them would make
        the one channel play at a different speed to the whole."""
        notes = (self.notes if channel is None
                 else [n for n in self.notes if n.channel == channel])
        events = seq_notes.to_events(notes, self.others)
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

    def _play_pause(self):
        """The one transport button: start, hold, or carry on."""
        if not self.sound.ready():
            return
        if self._is_playing():
            self.sound.pause()
            self._playback_state()
            return
        # Carrying on from where it was held - unless it was edited
        # while it sat there, in which case what is paused is no longer
        # the music being looked at and it has to be built again.
        if self.sound.paused() and self.sound.following and not self._dirty:
            self.sound.resume()
            self._playback_state()
            return
        self._play()

    def _play(self, resume_at=None):
        """Start the sequence, from `resume_at` or from the playhead."""
        if not self.sound.ready():
            return
        # Read now, because starting the audio walks the playhead back
        # to the top before anything gets a chance to seek - see
        # _resume_at.
        at = self.roll.playhead if resume_at is None else resume_at
        # Showing one channel means hearing one channel; the filter
        # would be half a filter otherwise.
        channel = self.roll.channel
        if (self._rendered is not None and not self._dirty
                and self._rendered_channel == channel):
            self.sound.play(self._rendered, self.loop_box.isChecked())
            self._resume_at(at)
            self._playback_state()
            return
        try:
            blob = self.encoded(channel)
        except Exception as exc:
            self.status.setText(f"Can't play that: {exc}")
            return
        self._rendered_channel = channel
        self._resume_to = at
        self.play_button.setEnabled(False)
        self.status.setText("Rendering on the sound bank...")
        self._render = _Render(blob, self.sound.snd, self.sound.bank)
        self._render.done.connect(self._played)
        self._render.start()

    def _played(self, wav, note):
        self.play_button.setEnabled(True)
        at, self._resume_to = self._resume_to, 0
        if wav is None:
            self.status.setText(f"Could not play that: {note}")
            return
        self._rendered = wav
        self.sound.play(wav, self.loop_box.isChecked())
        self._resume_at(at)
        self._playback_state()
        # Puts the byte count back over "Rendering..." - and has to come
        # before the flag is cleared, because counting marks the render
        # stale and here it is exactly as fresh as it gets.
        self._recount()
        self._dirty = False

    def _resume_at(self, tick):
        """Put the audio and the playhead back to `tick` after a start.

        Starting a fresh render plays from zero, and the player says so
        loudly enough to drag the playhead up with it - which is why
        switching channel or instrument mid-play threw the cursor back
        to the beginning. The tick is read before the restart and put
        back here.

        Applied again when the media finishes loading, not only now: a
        seek against a source the player has just been handed is
        allowed to do nothing, and doing nothing is the bug."""
        self._pending_seek = max(0, int(tick))
        self._apply_seek()

    def _apply_seek(self):
        if self._pending_seek <= 0:
            return
        self.roll.set_playhead(self._pending_seek)
        self.sound.player.setPosition(int(seq_notes.seconds_at(
            self._timeline, self._pending_seek) * 1000))

    def _media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            self._apply_seek()
            self._pending_seek = 0

    def _stop(self):
        self.sound.stop()
        self._pending_seek = 0
        self.stop_button.setEnabled(False)

    def _playback_state(self, *_state):
        """Put the transport buttons where the player actually is.

        Driven from the player rather than from the clicks, so a
        sequence that simply ends does not leave Stop lit and Pause
        offering to pause silence."""
        if not self.sound.ready():
            return
        playing = self._is_playing()
        paused = self.sound.paused() and self.sound.following
        self.stop_button.setEnabled(playing or paused)
        # The button shows what pressing it will do next, not what the
        # player is doing now.
        set_glyph(self.play_button, "pause" if playing else "play",
                  PAUSE_HINT if playing else PLAY_HINT)

    # -- undo ----------------------------------------------------------
    #
    # Whole snapshots of the notes, taken before a gesture rather than
    # after it: the roll emits begin_edit the moment it is about to
    # change something, which is the only point at which the state
    # being replaced still exists. Drawing, erasing, dragging, resizing
    # and nudging all go through it, so each is one step - a drag is
    # not forty.

    def _snapshot(self):
        return [note.as_tuple() for note in self.notes]

    def _push_undo(self):
        shot = self._snapshot()
        # A gesture that announced itself and then changed nothing - a
        # click that missed, a drag that went nowhere - should not cost
        # a step.
        if self._undo and self._undo[-1] == shot:
            return
        self._undo.append(shot)
        del self._undo[:-UNDO_DEPTH]
        self._redo.clear()
        self._undo_state()

    def _restore(self, shot):
        # In place: the roll holds this same list, and handing it a new
        # one would leave it drawing the old notes.
        self.notes[:] = [seq_notes.Note(*item) for item in shot]
        self.roll.refresh_notes()
        self._show_instrument()
        self._recount()

    def _undo_once(self):
        if not self._undo:
            return
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self._undo_state()

    def _redo_once(self):
        if not self._redo:
            return
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        self._undo_state()

    def _undo_state(self):
        self.undo_button.setEnabled(bool(self._undo))
        self.redo_button.setEnabled(bool(self._redo))

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
        dim = theme.colours()["dim"]
        self.status.setText(
            f"<b>{len(self.notes)}</b> notes on <b>{channels}</b> "
            f"channel{'' if channels == 1 else 's'} &nbsp;·&nbsp; "
            f"<b>{size}</b> bytes <span style='color:{dim}'>"
            f"({shape})</span>"
            + (f"<br><span style='color:{'#d65f4f' if over else dim}'>"
               f"{note}</span>" if note else ""))
        self.apply_button.setEnabled(self.applyable and not over)

    def _apply(self):
        if not self.applyable:
            return
        try:
            self.result_blob = self.encoded()
        except Exception as exc:
            self.status.setText(f"Can't encode that: {exc}")
            return
        self.accept()

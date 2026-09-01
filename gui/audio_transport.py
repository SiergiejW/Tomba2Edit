"""A list of things to play, and the controls to play them.

All three audio surfaces need the same widget - a list, play/pause/stop,
prev/next, a seek bar, a clock, a volume slider, rename and save - so it
lives here once and is fed differently:

    Music       pieces of BGM.XA and DEMO.XA, plus the CD audio track
    Dialogues   the channels of VOICE.XA
    SFX         the waveforms out of the sound banks in TOMBA2.SND

Everything goes through QMediaPlayer rather than playing raw PCM into an
audio sink: given a WAV in a QBuffer it seeks, reports a duration and
tracks position exactly as it does for a file, so one transport serves
all three. Raw PCM would need all of that written again.

Each row carries a key naming what it is - "BGM.XA:1:2", "0:46" - which
is what a name is stored against, so renaming survives the list being
rebuilt or reordered. The owner keeps the names; this only edits them
and says so.

The list itself is a single-column table by default - Music and
Dialogues use it that way, and it looks exactly like a plain list. SFX
asks for extra columns (`columns=`) to show the index, bank and slot,
length and loop flag alongside the name.

Sorting is on, which is the reason `wanted` and `save_requested` carry a
KEY rather than a row number: sorting physically moves rows, and a
decode is asynchronous - the row a click meant when it was made is not
promised to still hold that entry by the time a worker thread calls
back. A key travels with its row wherever it sorts to, so the owner's
callback resolving late is only ever wrong if the owner itself indexes
by position instead of by key too, which is why each panel keeps its
own key -> data mapping rather than a plain list indexed by row.
"""
import os

from PyQt6.QtCore import (QBuffer, QByteArray, Qt, QUrl, pyqtSignal)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog,
                             QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QSlider, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from functions import audio_export

KEY = Qt.ItemDataRole.UserRole
DESCRIPTION = Qt.ItemDataRole.UserRole + 1
LOOPS = Qt.ItemDataRole.UserRole + 2

# A narrower default than Stretch would give it: with extra columns
# competing for room (SFX's Index/Bank/Slot/Length/Loop), the name does
# not need to eat every pixel that isn't currently used, and the user
# can still drag it wider - see the Interactive resize mode below.
NAME_COLUMN_WIDTH = 220


def clock(ms):
    """Milliseconds as m:ss."""
    if not ms or ms < 0:
        ms = 0
    seconds = int(ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class AudioTransport(QWidget):
    """A playlist with transport controls, renaming and saving.

    The owner supplies the audio by connecting `wanted`, which carries
    the KEY of the row wanted; it answers with play_url() or
    play_bytes(). That keeps decoding - which can be slow enough to need
    a thread - out of here. Saving works the same way through
    `save_requested`.

    Save buttons (`save_wav`/`save_mp3`) are built here but are plain
    QPushButtons an owner is expected to reparent into its own toolbar
    rather than leave in this widget's own layout - see any of the three
    panels for the pattern. That is also where a bulk "save all" lives,
    since which "all" means is specific to what the panel is browsing."""

    wanted = pyqtSignal(str)
    renamed = pyqtSignal(str, str)          # key, new name ("" clears it)
    save_requested = pyqtSignal(str, str)   # key, path

    def __init__(self, parent=None, columns=None):
        super().__init__(parent)
        self._scrubbing = False
        self._buffer = None
        self._current = -1
        self._looping = False
        self._extra_columns = list(columns or [])

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0.8)
        self.player.positionChanged.connect(self._moved)
        self.player.durationChanged.connect(self._sized)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._status_changed)

        self.list = QTableWidget(0, 1 + len(self._extra_columns))
        self.list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list.verticalHeader().setVisible(False)
        self.list.setSortingEnabled(True)
        header = self.list.horizontalHeader()
        self.list.setHorizontalHeaderLabels(["Name"] + self._extra_columns)
        # Interactive on every column - a person can drag any of them -
        # but the name starts at a fixed, narrower width rather than
        # Stretch's whole-remaining-space default: with extra columns
        # to share room with, "SFX 214" does not need the width "Fire
        # Pig Robe Model Animation Pointers" would. With none, it is
        # still the only column, so this just becomes its starting size
        # rather than a hard limit - the user can always drag it.
        for i in range(self.list.columnCount()):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        self.list.setColumnWidth(0, NAME_COLUMN_WIDTH)
        self.list.cellDoubleClicked.connect(
            lambda row, _col: self.play_row(row))
        self.list.currentCellChanged.connect(self._maybe_autoplay)
        # F2 renames. Deliberately not SelectedClicked, which would start
        # an edit whenever a chosen row is clicked again.
        self.list.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed)
        self.list.itemChanged.connect(self._item_changed)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._toggle)
        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop)
        previous = QPushButton("Prev")
        previous.clicked.connect(lambda: self.step(-1))
        following = QPushButton("Next")
        following.clicked.connect(lambda: self.step(1))

        rename = QPushButton("Rename")
        rename.setToolTip("Give this entry a name of your own (or press F2). "
                          "Names are saved per disc.")
        rename.clicked.connect(self.rename_current)
        self.autoplay = QCheckBox("Autoplay")
        self.autoplay.setToolTip(
            "Play an entry as soon as it's selected, instead of only on "
            "double-click or Play. Off by default so browsing the list "
            "with the arrow keys doesn't talk over itself.")
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        self.loop.setToolTip(
            "Repeat an entry marked as a loop instead of playing it once. "
            "On by default. Autoplay overrides it while both are checked - "
            "browsing entry to entry would otherwise never move on from "
            "one that loops.")

        self.save_wav = QPushButton("Save selected to WAV...")
        self.save_wav.clicked.connect(lambda: self._save("wav"))
        self.save_mp3 = QPushButton("Save selected to MP3...")
        self.save_mp3.clicked.connect(lambda: self._save("mp3"))
        if not audio_export.have_mp3():
            self.save_mp3.setToolTip(
                "No MP3 encoder found. Install one with "
                "\"pip install lameenc\", or put ffmpeg on PATH.")

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 0)
        self.position.sliderPressed.connect(self._grab)
        self.position.sliderReleased.connect(self._release)
        self.time = QLabel("0:00 / 0:00")

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(120)
        self.volume.valueChanged.connect(
            lambda v: self.output.setVolume(v / 100))

        seek = QHBoxLayout()
        seek.addWidget(self.position, 1)
        seek.addWidget(self.time)
        row = QHBoxLayout()
        for button in (previous, self.play_button, stop, following):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(QLabel("Volume"))
        row.addWidget(self.volume)
        tools = QHBoxLayout()
        tools.addWidget(rename)
        tools.addWidget(self.autoplay)
        tools.addWidget(self.loop)
        tools.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list, 1)
        layout.addLayout(seek)
        layout.addLayout(row)
        layout.addLayout(tools)

    # --- the list -----------------------------------------------------

    def set_entries(self, entries, names=None):
        """Fill the list.

        Each entry is (key, description), (key, description, values), or
        (key, description, values, loops) - `values` has one string per
        extra column, and `loops` (default False) marks a row that
        should repeat rather than play once through, for SFX's benefit.
        The key names the audio and is what a name is stored against;
        the description is what to show when it has no name.

        Sorting is turned off while the table is rebuilt: it applies to
        every insertion otherwise, which is pointless work here and
        fights the row-by-row fill besides."""
        names = names or {}
        self.stop()
        self.list.setSortingEnabled(False)
        self.list.blockSignals(True)
        self.list.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            key, description, *rest = entry
            values = rest[0] if rest else ()
            loops = rest[1] if len(rest) > 1 else False
            item = QTableWidgetItem()
            item.setData(KEY, key)
            item.setData(DESCRIPTION, description)
            item.setData(LOOPS, bool(loops))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._show(item, names.get(key, ""))
            self.list.setItem(row, 0, item)
            for col, value in enumerate(values, start=1):
                cell = QTableWidgetItem()
                cell.setData(Qt.ItemDataRole.DisplayRole, value)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.list.setItem(row, col, cell)
        self.list.blockSignals(False)
        self.list.setSortingEnabled(True)
        if entries:
            self.list.setCurrentCell(0, 0)

    def apply_names(self, names):
        """Redraw every row against a fresh set of names."""
        self.list.blockSignals(True)
        for row in range(self.list.rowCount()):
            item = self.list.item(row, 0)
            self._show(item, names.get(item.data(KEY), ""))
        self.list.blockSignals(False)

    @staticmethod
    def _show(item, name):
        """A named row shows its name; an unnamed one shows what it is."""
        description = item.data(DESCRIPTION) or ""
        item.setText(name or description)
        item.setToolTip(description if name else "")

    def _row_for_key(self, key):
        """The row a key is currently sitting at - not assumed stable,
        since sorting moves rows around underneath it."""
        for row in range(self.list.rowCount()):
            item = self.list.item(row, 0)
            if item is not None and item.data(KEY) == key:
                return row
        return -1

    def key_at(self, row):
        item = self.list.item(row, 0)
        return item.data(KEY) if item is not None else None

    def name_at(self, row):
        """The row's own name, or "" when it is showing its description."""
        item = self.list.item(row, 0)
        if item is None:
            return ""
        text = item.text()
        return "" if text == item.data(DESCRIPTION) else text

    def current_row(self):
        return self.list.currentRow()

    def current_key(self):
        return self.key_at(self.current_row())

    def set_label(self, row, description):
        """Replace a row's description, keeping any name it has."""
        item = self.list.item(row, 0)
        if item is None:
            return
        name = self.name_at(row)
        self.list.blockSignals(True)
        item.setData(DESCRIPTION, description)
        self._show(item, name)
        self.list.blockSignals(False)

    # --- renaming -----------------------------------------------------

    def rename_current(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.editItem(self.list.item(row, 0))

    def _item_changed(self, item):
        """An edit finished: tell the owner, and fall back to the
        description when the name has been cleared.

        Extra columns are flagged non-editable, but guard the column
        anyway - nothing but the name cell should ever reach here."""
        if item.column() != 0:
            return
        key = item.data(KEY)
        name = item.text().strip()
        if name == item.data(DESCRIPTION):
            name = ""
        self.list.blockSignals(True)
        self._show(item, name)
        self.list.blockSignals(False)
        if key:
            self.renamed.emit(key, name)

    # --- saving -------------------------------------------------------

    def _save(self, suffix):
        row = self.list.currentRow()
        if row < 0:
            return
        item = self.list.item(row, 0)
        key = item.data(KEY)
        stem = audio_export.safe_name(
            self.name_at(row) or item.data(DESCRIPTION) or "audio")
        chosen, _ = QFileDialog.getSaveFileName(
            self, f"Save as {suffix.upper()}",
            f"{stem}.{suffix}",
            f"{suffix.upper()} audio (*.{suffix})")
        if not chosen:
            return
        if os.path.splitext(chosen)[1].lower() != f".{suffix}":
            chosen += f".{suffix}"
        self.save_requested.emit(key, chosen)

    # --- playing ------------------------------------------------------

    def play_row(self, row):
        """Ask the owner for row `row`; it calls back with the audio."""
        if 0 <= row < self.list.rowCount():
            key = self.key_at(row)
            self._current = row
            self.list.setCurrentCell(row, 0)
            if key:
                self.wanted.emit(key)

    def play_key(self, key):
        """Select and request a row by key, wherever it currently sits."""
        row = self._row_for_key(key)
        if row >= 0:
            self.play_row(row)

    def play_url(self, url):
        self.stop()
        self._looping = False
        self.player.setSource(QUrl(url) if isinstance(url, str) else url)
        self.player.play()

    def play_bytes(self, data):
        """Play a file held in memory - a WAV built from decoded audio.

        The buffer is kept on the instance: the player reads from it for
        as long as it plays, and letting it go collects it mid-play.

        Loops if the currently selected row was marked as one - a sound
        effect the game holds a button down to sustain rather than one
        that plays once through - and the Loop checkbox agrees, and
        Autoplay isn't on. Autoplay overriding it is deliberate: stepping
        through entries with Autoplay is meant to move on, and a looping
        entry would otherwise just keep answering forever on the row it
        started on. This reads _current rather than taking a "should it
        loop" argument because play_bytes is always answering the most
        recent play_row/play_key, and that row already knows."""
        self.stop()
        current = self.list.item(self._current, 0) if self._current >= 0 else None
        self._looping = (bool(current and current.data(LOOPS))
                         and self.loop.isChecked()
                         and not self.autoplay.isChecked())
        self.player.setLoops(QMediaPlayer.Loops.Infinite if self._looping else 1)
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(data))
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self._buffer)
        self.player.play()

    def stop(self):
        self.player.stop()
        self._looping = False
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None

    def step(self, by):
        row = self.list.currentRow() + by
        if 0 <= row < self.list.rowCount():
            self.play_row(row)

    def _toggle(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        else:
            self.play_row(max(self.list.currentRow(), 0))

    # --- signals ------------------------------------------------------

    def _maybe_autoplay(self, row, _col, previous_row, _previous_col):
        """Selecting a different row plays it, but only with Autoplay
        on - and only for a genuinely new row: play_row() itself moves
        the current cell to where it already is, which would otherwise
        retrigger this and restart the same row it's mid-answering."""
        if row >= 0 and row != previous_row and self.autoplay.isChecked():
            self.play_row(row)

    def _grab(self):
        self._scrubbing = True

    def _release(self):
        self._scrubbing = False
        self.player.setPosition(self.position.value())

    def _moved(self, ms):
        if not self._scrubbing:
            self.position.setValue(ms)
        self.time.setText(f"{clock(ms)} / {clock(self.player.duration())}")

    def _sized(self, ms):
        self.position.setRange(0, ms)
        self.time.setText(f"{clock(self.player.position())} / {clock(ms)}")

    def _state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause" if playing else "Play")

    def _status_changed(self, status):
        # A looping track's own repeats must never be read as it having
        # finished - only step to the next row for one that really has.
        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self._looping:
            self.step(1)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)

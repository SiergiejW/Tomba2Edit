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
"""
import os

from PyQt6.QtCore import (QBuffer, QByteArray, Qt, QUrl, pyqtSignal)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QSlider, QVBoxLayout, QWidget)

from functions import audio_export

KEY = Qt.ItemDataRole.UserRole
DESCRIPTION = Qt.ItemDataRole.UserRole + 1


def clock(ms):
    """Milliseconds as m:ss."""
    if not ms or ms < 0:
        ms = 0
    seconds = int(ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class AudioTransport(QWidget):
    """A playlist with transport controls, renaming and saving.

    The owner supplies the audio by connecting `wanted`, which carries
    the row being played; it answers with play_url() or play_bytes().
    That keeps decoding - which can be slow enough to need a thread -
    out of here. Saving works the same way through `save_requested`."""

    wanted = pyqtSignal(int)
    renamed = pyqtSignal(str, str)          # key, new name ("" clears it)
    save_requested = pyqtSignal(int, str)   # row, path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scrubbing = False
        self._buffer = None
        self._current = -1

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0.8)
        self.player.positionChanged.connect(self._moved)
        self.player.durationChanged.connect(self._sized)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._status_changed)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(
            lambda item: self.play_row(self.list.row(item)))
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
        self.save_wav = QPushButton("Save as WAV...")
        self.save_wav.clicked.connect(lambda: self._save("wav"))
        self.save_mp3 = QPushButton("Save as MP3...")
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
        tools.addStretch(1)
        tools.addWidget(self.save_wav)
        tools.addWidget(self.save_mp3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list, 1)
        layout.addLayout(seek)
        layout.addLayout(row)
        layout.addLayout(tools)

    # --- the list -----------------------------------------------------

    def set_entries(self, entries, names=None):
        """Fill the list.

        `entries` is (key, description) per row - the key names the audio
        and is what a name is stored against; the description is what to
        show when it has no name."""
        names = names or {}
        self.stop()
        self.list.blockSignals(True)
        self.list.clear()
        for key, description in entries:
            item = QListWidgetItem()
            item.setData(KEY, key)
            item.setData(DESCRIPTION, description)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._show(item, names.get(key, ""))
            self.list.addItem(item)
        self.list.blockSignals(False)
        if entries:
            self.list.setCurrentRow(0)

    def apply_names(self, names):
        """Redraw every row against a fresh set of names."""
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            item = self.list.item(row)
            self._show(item, names.get(item.data(KEY), ""))
        self.list.blockSignals(False)

    @staticmethod
    def _show(item, name):
        """A named row shows its name; an unnamed one shows what it is."""
        description = item.data(DESCRIPTION) or ""
        item.setText(name or description)
        item.setToolTip(description if name else "")

    def key_at(self, row):
        item = self.list.item(row)
        return item.data(KEY) if item is not None else None

    def name_at(self, row):
        """The row's own name, or "" when it is showing its description."""
        item = self.list.item(row)
        if item is None:
            return ""
        text = item.text()
        return "" if text == item.data(DESCRIPTION) else text

    def current_row(self):
        return self.list.currentRow()

    def set_label(self, row, description):
        """Replace a row's description, keeping any name it has."""
        item = self.list.item(row)
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
            self.list.editItem(self.list.item(row))

    def _item_changed(self, item):
        """An edit finished: tell the owner, and fall back to the
        description when the name has been cleared."""
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
        item = self.list.item(row)
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
        self.save_requested.emit(row, chosen)

    # --- playing ------------------------------------------------------

    def play_row(self, row):
        """Ask the owner for row `row`; it calls back with the audio."""
        if 0 <= row < self.list.count():
            self._current = row
            self.list.setCurrentRow(row)
            self.wanted.emit(row)

    def play_url(self, url):
        self.stop()
        self.player.setSource(QUrl(url) if isinstance(url, str) else url)
        self.player.play()

    def play_bytes(self, data):
        """Play a file held in memory - a WAV built from decoded audio.

        The buffer is kept on the instance: the player reads from it for
        as long as it plays, and letting it go collects it mid-play."""
        self.stop()
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(data))
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self._buffer)
        self.player.play()

    def stop(self):
        self.player.stop()
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None

    def step(self, by):
        row = self.list.currentRow() + by
        if 0 <= row < self.list.count():
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
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.step(1)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)

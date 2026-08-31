"""A list of things to play, and the controls to play them.

Both audio surfaces need the same widget - a list, play/pause/stop,
prev/next, a seek bar, a clock and a volume slider - so it lives here
once and is fed differently:

    the soundtrack   entries are files, handed over as URLs
    the voice track  entries are channels decoded out of the disc, handed
                     over as WAV bytes built in memory

The second is why everything goes through QMediaPlayer rather than
playing raw PCM into an audio sink: given a WAV in a QBuffer it seeks,
reports a duration and tracks position exactly as it does for a file, so
one transport serves both. Raw PCM would need all of that written again.
"""
from PyQt6.QtCore import QBuffer, QByteArray, Qt, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QSlider,
                             QVBoxLayout, QWidget)


def clock(ms):
    """Milliseconds as m:ss."""
    if not ms or ms < 0:
        ms = 0
    seconds = int(ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class AudioTransport(QWidget):
    """A playlist with transport controls.

    The owner supplies the audio by connecting `wanted`, which carries
    the row being played; it answers with play_url() or play_bytes().
    That keeps decoding - which can be slow enough to need a thread -
    out of here."""

    wanted = pyqtSignal(int)

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

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._toggle)
        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop)
        previous = QPushButton("Prev")
        previous.clicked.connect(lambda: self.step(-1))
        following = QPushButton("Next")
        following.clicked.connect(lambda: self.step(1))

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list, 1)
        layout.addLayout(seek)
        layout.addLayout(row)

    # --- the list -----------------------------------------------------

    def set_entries(self, labels):
        self.stop()
        self.list.clear()
        for label in labels:
            self.list.addItem(QListWidgetItem(label))
        if labels:
            self.list.setCurrentRow(0)

    def current_row(self):
        return self.list.currentRow()

    def set_label(self, row, label):
        item = self.list.item(row)
        if item is not None:
            item.setText(label)

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

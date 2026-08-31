"""A player for the game's soundtrack.

The music is not read out of the disc here - the streamed tracks live in
BGM.XA and the redbook one is a whole second track of the bin/cue - this
plays a folder of already-extracted files, which is what a soundtrack
rip is. Qt decodes FLAC, WAV and MP3 natively, so nothing else is needed.

For the audio that IS on the disc, see the Voice tab, which decodes
VOICE.XA's channels straight out of a raw track.
"""
import os

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QSlider,
                             QVBoxLayout, QWidget)

PLAYABLE = (".flac", ".wav", ".mp3", ".ogg", ".m4a", ".aac", ".wma")

# Where a soundtrack rip tends to sit in this project, so the panel has
# something to show without being pointed at a folder first.
GUESSES = (os.path.join("audio research", "TombaWASoundtrack"),
           "audio research", "soundtrack")


def _clock(ms):
    if ms is None or ms < 0:
        ms = 0
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class MusicPanel(QWidget):
    """Browse a folder of audio files and play them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = None
        self._tracks = []
        self._scrubbing = False

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0.8)
        self.player.positionChanged.connect(self._moved)
        self.player.durationChanged.connect(self._sized)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._status_changed)

        pick = QPushButton("Open folder...")
        pick.clicked.connect(self._browse)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._play_item)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._toggle)
        stop = QPushButton("Stop")
        stop.clicked.connect(self.player.stop)
        previous = QPushButton("Prev")
        previous.clicked.connect(lambda: self._step(-1))
        following = QPushButton("Next")
        following.clicked.connect(lambda: self._step(1))

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

        self.status = QLabel("No folder open.")
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(pick)
        top.addStretch(1)
        top.addWidget(QLabel("Volume"))
        top.addWidget(self.volume)

        seek = QHBoxLayout()
        seek.addWidget(self.position, 1)
        seek.addWidget(self.time)

        row = QHBoxLayout()
        for button in (previous, self.play_button, stop, following):
            row.addWidget(button)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.list, 1)
        layout.addLayout(seek)
        layout.addLayout(row)
        layout.addWidget(self.status)

        for guess in GUESSES:
            if os.path.isdir(guess):
                self.set_folder(guess)
                break

    # --- the list -----------------------------------------------------

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a music folder")
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder):
        """Show every playable file in a folder, in name order."""
        try:
            names = sorted(n for n in os.listdir(folder)
                           if n.lower().endswith(PLAYABLE))
        except OSError as exc:
            self.status.setText(str(exc))
            return
        self.folder = folder
        self._tracks = [os.path.join(folder, n) for n in names]
        self.list.clear()
        for name in names:
            item = QListWidgetItem(os.path.splitext(name)[0])
            self.list.addItem(item)
        self.status.setText(
            f"{os.path.basename(folder)}: {len(names)} track(s)."
            if names else f"{folder} has nothing playable in it.")
        if names:
            self.list.setCurrentRow(0)

    # --- transport ----------------------------------------------------

    def _play_item(self, item):
        self._play(self.list.row(item))

    def _play(self, row):
        if not (0 <= row < len(self._tracks)):
            return
        self.list.setCurrentRow(row)
        self.player.setSource(QUrl.fromLocalFile(self._tracks[row]))
        self.player.play()

    def _toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.player.source().isEmpty():
            self._play(max(self.list.currentRow(), 0))
        else:
            self.player.play()

    def _step(self, by):
        row = self.list.currentRow() + by
        if 0 <= row < len(self._tracks):
            self._play(row)

    # --- signals ------------------------------------------------------

    def _grab(self):
        self._scrubbing = True

    def _release(self):
        self._scrubbing = False
        self.player.setPosition(self.position.value())

    def _moved(self, ms):
        if not self._scrubbing:
            self.position.setValue(ms)
        self.time.setText(f"{_clock(ms)} / {_clock(self.player.duration())}")

    def _sized(self, ms):
        self.position.setRange(0, ms)
        self.time.setText(f"{_clock(self.player.position())} / {_clock(ms)}")

    def _state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause" if playing else "Play")

    def _status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._step(1)                       # roll on to the next track
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.status.setText(
                "Qt could not decode that file. FLAC, WAV and MP3 play; "
                "anything else depends on the codecs this machine has.")

    def closeEvent(self, event):
        self.player.stop()
        super().closeEvent(event)

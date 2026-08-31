"""Listening to the voice track beside the text.

VOICE.XA is 32 interleaved channels of spoken dialogue. Pick a channel,
it is decoded and cut where it falls quiet, and each piece can be played.

The cuts are an approximation - the real boundaries live in a timing
table in the area's overlay that nobody has located yet (see
functions/voice.py), so this is for listening through a channel rather
than for saying which line is which.

The track has to come from a raw BIN, not a CD folder or an ISO: those
have had 276 bytes cut out of every sector of it.
"""
import os

from PyQt6.QtCore import QBuffer, QByteArray, Qt, QThread, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

from functions import voice, xa


class _Decode(QThread):
    """Decoding a channel, off the GUI thread.

    A whole channel is about two seconds of work - short, but long enough
    to freeze the window, and this is where a QThread owned by a widget
    would take the process down with it, so it is left unparented and
    stopped explicitly."""

    done = pyqtSignal(object)

    def __init__(self, image, lba, sectors, channel):
        super().__init__()
        self.args = (image, lba, sectors, channel)

    def run(self):
        try:
            self.done.emit(voice.channel_clips(*self.args))
        except Exception:
            self.done.emit(None)


class VoicePanel(QWidget):
    """Browse and play the dialogue in VOICE.XA."""

    # Emitted when a data track is opened, so anything else that wants
    # the voice track - the TXTD viewer's Play button - can pick it up
    # whichever order the user does things in.
    image_opened = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.lba = self.sectors = 0
        self.samples = []
        self.rate = 37800
        self.spans = []
        self._decode = None
        self._sink = None
        self._buffer = None

        self.pick = QPushButton("Open BIN...")
        self.pick.clicked.connect(self._browse)
        self.channel_box = QComboBox()
        self.channel_box.currentIndexChanged.connect(self._load_channel)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._play())
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._play)
        stop = QPushButton("Stop")
        stop.clicked.connect(self._stop)
        self.status = QLabel("No BIN opened - the voice track needs a raw "
                             "2352-byte track, not a CD folder or ISO.")
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addWidget(QLabel("Channel"))
        top.addWidget(self.channel_box, 1)
        row = QHBoxLayout()
        row.addWidget(self.play_button)
        row.addWidget(stop)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        layout.addWidget(self.status)

    # --- opening ------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the disc's data track", "",
            "Disc track (*.bin *.img);;All files (*)")
        if path:
            self.set_image(path)

    def set_image(self, path):
        """Point the panel at a raw disc track."""
        try:
            self.lba, self.sectors = voice.find_track(path)
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.image = path
        found = voice.channels(path, self.lba, self.sectors)
        self.channel_box.blockSignals(True)
        self.channel_box.clear()
        for channel, count in found:
            self.channel_box.addItem(
                f"channel {channel}  ({count} sectors, "
                f"{count * xa.SAMPLES_PER_SECTOR / 18900:.0f}s)", channel)
        self.channel_box.blockSignals(False)
        self.status.setText(
            f"{os.path.basename(path)}: VOICE.XA at sector {self.lba:,}, "
            f"{self.sectors:,} sectors, {len(found)} channels.")
        self.image_opened.emit(path)
        if found:
            self._load_channel(0)

    # --- decoding -----------------------------------------------------

    def _load_channel(self, _index):
        if not self.image:
            return
        channel = self.channel_box.currentData()
        if channel is None:
            return
        self._stop_decode()
        self.list.clear()
        self.status.setText(f"Decoding channel {channel}...")
        self._decode = _Decode(self.image, self.lba, self.sectors, channel)
        self._decode.done.connect(self._decoded)
        self._decode.start()

    def _decoded(self, result):
        if result is None:
            self.status.setText("Could not decode that channel.")
            return
        self.samples, self.rate, self.spans = result
        self.list.clear()
        for i, (start, end) in enumerate(self.spans):
            seconds = (end - start) / self.rate
            item = QListWidgetItem(
                f"{i + 1:3d}.  {start / self.rate:7.2f}s   {seconds:5.2f}s")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.list.addItem(item)
        self.status.setText(
            f"{len(self.spans)} clips, {len(self.samples) / self.rate:.0f}s "
            f"at {self.rate} Hz. Cut on silence, so the boundaries are "
            "approximate.")
        if self.spans:
            self.list.setCurrentRow(0)

    def _stop_decode(self):
        if self._decode is not None and self._decode.isRunning():
            self._decode.requestInterruption()
            self._decode.wait(3000)
        self._decode = None

    # --- playing ------------------------------------------------------

    def _play(self):
        row = self.list.currentRow()
        if row < 0 or row >= len(self.spans):
            return
        start, end = self.spans[row]
        self.play_samples(self.samples[start:end], self.rate)

    def play_samples(self, samples, rate):
        """Play 16-bit mono PCM straight out of memory.

        The bytes go in with setData: QBuffer(QByteArray(...)) keeps a
        pointer to the array rather than taking ownership of it, so a
        temporary handed to it is freed while the sink is still reading,
        which plays noise and then crashes."""
        self._stop()
        if not samples:
            return
        import array
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._buffer = QBuffer()
        self._buffer.setData(array.array("h", samples).tobytes())
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self._sink = QAudioSink(fmt)
        self._sink.start(self._buffer)

    def _stop(self):
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None

    def closeEvent(self, event):
        self._stop()
        self._stop_decode()
        super().closeEvent(event)

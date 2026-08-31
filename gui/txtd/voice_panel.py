"""Listening to the voice track, a channel at a time.

VOICE.XA interleaves 32 channels of spoken dialogue (see functions/xa).
Each is listed here and played whole, with a seek bar, so a channel can
be scrubbed through to find a line.

Nothing is cut up. Where a clip starts and ends is not something the
audio itself says - it comes from the clip table in the area's overlay,
which the TXTD viewer's Play button uses to speak one chosen line (see
gui/txtd/voice_link.py). Guessing the boundaries from silence, as this
panel used to, put them in roughly the right places and confidently
wrong ones.

Two sources work: a raw disc track, and a VOICE.XA extracted properly -
which "Extract VOICE.XA..." writes, so a CD folder can carry working
audio. A VOICE.XA copied as an ordinary file cannot be used: that takes
2048 bytes of each sector where the format holds 2324, losing 12% of the
audio for good.
"""
import os

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from functions import voice, xa
from gui.audio_transport import AudioTransport, clock


class _Decode(QThread):
    """Decoding a channel, off the GUI thread.

    A whole channel is a couple of seconds of work - short, but long
    enough to freeze the window. Left unparented deliberately: a QThread
    owned by a widget is destroyed with it, and destroying one that is
    still running takes the process down."""

    done = pyqtSignal(int, object, int)

    def __init__(self, image, lba, sectors, channel):
        super().__init__()
        self.args = (image, lba, sectors, channel)
        self.channel = channel

    def run(self):
        image, lba, sectors, channel = self.args
        try:
            frame = xa.framing(image) or xa.RAW
            with open(image, "rb") as f:
                chans = xa.channel_map(f, lba, sectors, frame)
                key = next((k for k in chans if k[1] == channel), None)
                if key is None:
                    self.done.emit(channel, None, 0)
                    return
                samples, rate, speakers = xa.decode_channel(
                    f, lba, chans[key], frame=frame)
            self.done.emit(channel,
                           xa.wav_bytes(samples, rate, speakers), rate)
        except Exception:
            self.done.emit(channel, None, 0)


class VoicePanel(QWidget):
    """Browse and play VOICE.XA's channels."""

    # Emitted when a data track is opened, so anything else that wants
    # the voice track - the TXTD viewer's Play button - can pick it up
    # whichever order the user does things in.
    image_opened = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.lba = self.sectors = 0
        self._channels = []
        self._decode = None
        self._cache = {}

        self.pick = QPushButton("Open BIN...")
        self.pick.setToolTip(
            "Only needed for a disc opened as a folder - opening a BIN "
            "normally sets this up on its own")
        self.pick.clicked.connect(self._browse)
        self.extract = QPushButton("Extract VOICE.XA...")
        self.extract.setToolTip(
            "Write a VOICE.XA that actually works into a CD folder - the "
            "Form 2 payloads, 2324 bytes a sector, which an ordinary file "
            "copy truncates to 2048")
        self.extract.clicked.connect(self._extract)
        self.extract.setEnabled(False)

        self.transport = AudioTransport()
        self.transport.wanted.connect(self._wanted)

        self.status = QLabel("No disc open - the voice track needs a raw "
                             "2352-byte data track, not a CD folder or ISO.")
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addWidget(self.extract)
        top.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.transport, 1)
        layout.addWidget(self.status)

    # --- opening ------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the disc's data track", "",
            "Disc track (*.bin *.img);;Extracted voice (*.XA);;All files (*)")
        if path:
            self.set_image(path)

    def set_image(self, path):
        """Point the panel at a disc track, or a good VOICE.XA."""
        try:
            self.lba, self.sectors = voice.find_track(path)
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.image = path
        self._cache.clear()
        self._channels = voice.channels(path, self.lba, self.sectors)
        per_sector = xa.SAMPLES_PER_SECTOR
        self.transport.set_entries([
            f"Channel {channel:2d}   {count:,} sectors   "
            f"~{clock(count * per_sector * 1000 // 18900)}"
            for channel, count in self._channels])
        self.status.setText(
            f"{os.path.basename(path)}: {len(self._channels)} channels, "
            f"{self.sectors:,} sectors. Pick one to decode and play it "
            "whole - the seek bar scrubs through it.")
        self.extract.setEnabled(True)
        self.image_opened.emit(path)

    def _extract(self):
        """Write a usable VOICE.XA next to a CD folder's other files."""
        if not self.image:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Write VOICE.XA", "VOICE.XA", "XA audio (*.XA)")
        if not path:
            return
        self.status.setText("Extracting...")
        try:
            voice.extract_voice(self.image, path)
        except Exception as exc:
            self.status.setText(f"Could not extract: {exc}")
            return
        self.status.setText(
            f"Wrote {os.path.basename(path)} - {os.path.getsize(path):,} "
            "bytes. That copy opens on its own, so a CD folder can carry "
            "working audio.")

    # --- playing ------------------------------------------------------

    def _wanted(self, row):
        """The transport asked for a row; decode it if it is not cached."""
        if not (0 <= row < len(self._channels)) or not self.image:
            return
        channel = self._channels[row][0]
        cached = self._cache.get(channel)
        if cached is not None:
            self.transport.play_bytes(cached)
            return
        self._stop_decode()
        self.status.setText(f"Decoding channel {channel}...")
        self._decode = _Decode(self.image, self.lba, self.sectors, channel)
        self._decode.done.connect(self._decoded)
        self._decode.start()

    def _decoded(self, channel, wav, rate):
        if wav is None:
            self.status.setText(f"Could not decode channel {channel}.")
            return
        self._cache[channel] = wav
        self.status.setText(
            f"Channel {channel} at {rate} Hz - decoded once and kept, so "
            "coming back to it is instant.")
        if self.transport.current_row() < len(self._channels) and \
                self._channels[self.transport.current_row()][0] == channel:
            self.transport.play_bytes(wav)

    def _stop_decode(self):
        if self._decode is not None and self._decode.isRunning():
            self._decode.requestInterruption()
            self._decode.wait(5000)
        self._decode = None

    def closeEvent(self, event):
        self.transport.stop()
        self._stop_decode()
        super().closeEvent(event)

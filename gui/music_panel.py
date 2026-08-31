"""The disc's music, played off the disc.

Tomba 2 keeps its music in two quite different places, and both are here:

    BGM.XA, DEMO.XA   streamed CD-XA, 8 channels interleaved through each
                      file, stereo ADPCM at 37800 Hz. One channel is one
                      piece of music; the drive plays one and skips the
                      rest (see functions/xa.py).
    Track 2           an ordinary CD audio track - 44.1 kHz stereo PCM
                      with nothing to decode, which is why it is a whole
                      second file in the bin/cue rather than a file on
                      the disc at all.

The list and controls are gui/audio_transport, shared with the Dialogues
tab; all this adds is where the audio comes from. Decoding a channel
takes a few seconds, so it happens on a worker thread and is kept.
"""
import os

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from functions import voice, xa
from gui.audio_transport import AudioTransport, clock

# The streamed music files, in the order they are listed.
STREAMS = ("BGM.XA", "DEMO.XA")

# Redbook audio: 44.1 kHz, 16-bit, stereo, and a 2-second pregap of
# silence at the front that the cue sheet accounts for.
CDDA_RATE = 44100
CDDA_BYTES_PER_SECOND = CDDA_RATE * 2 * 2
CDDA_PREGAP = 150 * 2352


class _Decode(QThread):
    """Decoding one music channel, off the GUI thread."""

    done = pyqtSignal(int, object, str)

    def __init__(self, row, image, lba, sectors, channel):
        super().__init__()
        self.row = row
        self.args = (image, lba, sectors, channel)

    def run(self):
        image, lba, sectors, channel = self.args
        try:
            with open(image, "rb") as f:
                chans = xa.channel_map(f, lba, sectors)
                key = next((k for k in chans if k[1] == channel), None)
                if key is None:
                    self.done.emit(self.row, None, "no such channel")
                    return
                samples, rate, speakers = xa.decode_channel(f, lba, chans[key])
            self.done.emit(self.row, xa.wav_bytes(samples, rate, speakers),
                           f"{rate} Hz "
                           f"{'stereo' if speakers == 2 else 'mono'}")
        except Exception as exc:
            self.done.emit(self.row, None, str(exc))


class MusicPanel(QWidget):
    """Play the music that is on the disc."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self._entries = []          # (kind, payload) per row
        self._cache = {}
        self._decode = None

        self.pick = QPushButton("Open BIN...")
        self.pick.setToolTip(
            "Only needed for a disc opened as a folder - opening a BIN "
            "normally sets this up on its own")
        self.pick.clicked.connect(self._browse)
        self.transport = AudioTransport()
        self.transport.wanted.connect(self._wanted)
        self.status = QLabel(
            "No disc open. The music is streamed CD-XA, which only survives "
            "in a raw data track - not a CD folder or an ISO.")
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.transport, 1)
        layout.addWidget(self.status)

    # --- opening ------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the disc's data track", "",
            "Disc track (*.bin *.img);;All files (*)")
        if path:
            self.set_image(path)

    def set_image(self, path):
        """List every piece of music the disc carries."""
        self.transport.stop()
        self._cache.clear()
        self._entries = []
        labels = []
        try:
            for name in STREAMS:
                where = voice.find_file(path, name)
                if not where:
                    continue
                lba, sectors = where
                with open(path, "rb") as f:
                    chans = xa.channel_map(f, lba, sectors)
                    f.seek(lba * xa.SECTOR)
                    first = f.read(xa.SECTOR)
                speakers, rate, _bits = xa.coding(first[xa.SUBHEADER + 3])
                frames = xa.SAMPLES_PER_SECTOR // max(speakers, 1)
                for _file, channel in sorted(chans):
                    count = len(chans[(_file, channel)])
                    self._entries.append(("xa", (lba, sectors, channel)))
                    labels.append(
                        f"{name}  channel {channel}   "
                        f"~{clock(count * frames * 1000 // rate)}")
        except Exception as exc:
            self.status.setText(f"Could not read the disc: {exc}")
            return
        if not self._entries:
            self.status.setText(
                f"{os.path.basename(path)} has no streamed music in it - "
                "an ISO or a CD folder cannot carry it.")
            self.transport.set_entries([])
            return
        self.image = path
        audio = self._audio_track(path)
        if audio:
            size = os.path.getsize(audio) - CDDA_PREGAP
            self._entries.append(("cdda", audio))
            labels.append(f"Track 2  CD audio   "
                          f"~{clock(size * 1000 // CDDA_BYTES_PER_SECOND)}")
        self.transport.set_entries(labels)
        self.status.setText(
            f"{os.path.basename(path)}: {len(labels)} piece(s) of music. "
            "A streamed channel takes a few seconds to decode the first "
            "time, then it is kept.")

    @staticmethod
    def _audio_track(path):
        """The bin/cue's second track, if it is sitting beside the first."""
        folder, stem = os.path.dirname(path), os.path.basename(path)
        if "Track 1" not in stem:
            return None
        candidate = os.path.join(folder, stem.replace("Track 1", "Track 2"))
        return candidate if os.path.exists(candidate) else None

    # --- playing ------------------------------------------------------

    def _wanted(self, row):
        if not (0 <= row < len(self._entries)):
            return
        cached = self._cache.get(row)
        if cached is not None:
            self.transport.play_bytes(cached)
            return
        kind, payload = self._entries[row]
        if kind == "cdda":
            # Nothing to decode - it is already PCM. The pregap is
            # dropped so it starts on the music rather than 2s of silence.
            with open(payload, "rb") as f:
                f.seek(CDDA_PREGAP)
                wav = xa.wav_bytes_raw(f.read(), CDDA_RATE, 2)
            self._cache[row] = wav
            self.status.setText("Track 2: CD audio, 44100 Hz stereo.")
            self.transport.play_bytes(wav)
            return
        lba, sectors, channel = payload
        self._stop_decode()
        self.status.setText(f"Decoding channel {channel}...")
        self._decode = _Decode(row, self.image, lba, sectors, channel)
        self._decode.done.connect(self._decoded)
        self._decode.start()

    def _decoded(self, row, wav, note):
        if wav is None:
            self.status.setText(f"Could not decode that channel: {note}")
            return
        self._cache[row] = wav
        self.status.setText(f"{note} - decoded once and kept.")
        if self.transport.current_row() == row:
            self.transport.play_bytes(wav)

    def _stop_decode(self):
        if self._decode is not None and self._decode.isRunning():
            self._decode.requestInterruption()
            self._decode.wait(8000)
        self._decode = None

    def closeEvent(self, event):
        self.transport.stop()
        self._stop_decode()
        super().closeEvent(event)

"""The disc's music, played off the disc.

Tomba 2 keeps its music in two quite different places, and both are here:

    BGM.XA, DEMO.XA   streamed CD-XA, 8 channels interleaved through each
                      file, stereo ADPCM at 37800 Hz. The drive plays one
                      channel and skips the rest (see functions/xa.py).
                      A channel holds several pieces of music end to end,
                      cut apart here on the game's own table of track
                      offsets rather than on silence (functions/bgm.py).
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
from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from functions import audio_export, bgm, voice, xa
from gui.audio_transport import AudioTransport, clock
from gui.name_store import NameStore

# The streamed music files, in the order they are listed.
STREAMS = ("BGM.XA", "DEMO.XA")

# Redbook audio: 44.1 kHz, 16-bit, stereo, and a 2-second pregap of
# silence at the front that the cue sheet accounts for.
CDDA_RATE = 44100
CDDA_BYTES_PER_SECOND = CDDA_RATE * 2 * 2
CDDA_PREGAP = 150 * 2352


class _Decode(QThread):
    """Decoding one track, off the GUI thread.

    The sector numbers are worked out when the disc is opened, so all
    this does is decode them - a track starts on a fresh predictor,
    which is what a track boundary means. Carries the track's key
    rather than a row number: sorting the table can move a row while
    this is running, and the key is what still points at the right
    entry when it finishes."""

    done = pyqtSignal(str, object, str)

    def __init__(self, key, image, lba, indices):
        super().__init__()
        self.key = key
        self.args = (image, lba, indices)

    def run(self):
        image, lba, indices = self.args
        try:
            with open(image, "rb") as f:
                samples, rate, speakers = xa.decode_channel(f, lba, indices)
            self.done.emit(self.key, xa.wav_bytes(samples, rate, speakers),
                           f"{rate} Hz "
                           f"{'stereo' if speakers == 2 else 'mono'}")
        except Exception as exc:
            self.done.emit(self.key, None, str(exc))


class MusicPanel(QWidget):
    """Play the music that is on the disc."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self._by_key = {}           # key -> (kind, payload)
        self._cache = {}
        self._decode = None
        self._pending_save = None   # (key, path) waiting on a decode
        self.names = NameStore("music")

        self.pick = QPushButton("Open BIN/IMG...")
        self.pick.setToolTip(
            "Only needed for a disc opened as a folder - opening a BIN "
            "normally sets this up on its own")
        self.pick.clicked.connect(self._browse)
        self.transport = AudioTransport(
            columns=["Index", "Length", "Stream", "Channel", "Track"])
        self.transport.wanted.connect(self._wanted)
        self.transport.renamed.connect(self._renamed)
        self.transport.save_requested.connect(self._save)

        self.export_all = QPushButton("Save all as WAV...")
        self.export_all.setToolTip("Write every piece of music into a "
                                   "folder, using the names given here")
        self.export_all.clicked.connect(self._save_all)
        self.export_all.setEnabled(False)

        self.status = QLabel(
            "No disc open. The music is streamed CD-XA, which only survives "
            "in a raw data track - not a CD folder or an ISO.")
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addStretch(1)
        top.addWidget(self.transport.save_wav)
        top.addWidget(self.transport.save_mp3)
        top.addWidget(self.export_all)
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
        self._by_key = {}
        entries = []
        try:
            exe = voice.extract_file(path, "MAIN.EXE")
            for name in STREAMS:
                where = voice.find_file(path, name)
                if not where:
                    continue
                lba, sectors = where
                with open(path, "rb") as f:
                    pieces = bgm.split(f, lba, sectors, exe)
                    f.seek(lba * xa.SECTOR)
                    first = f.read(xa.SECTOR)
                speakers, rate, _bits = xa.coding(first[xa.SUBHEADER + 3])
                frames = xa.SAMPLES_PER_SECTOR // max(speakers, 1)
                stem = name.split(".")[0]
                per_channel = {}
                for channel, _ordinal, _indices in pieces:
                    per_channel[channel] = per_channel.get(channel, 0) + 1
                for channel, ordinal, indices in pieces:
                    key = f"{name}:{channel}:{ordinal + 1}"
                    self._by_key[key] = ("xa", (lba, indices))
                    length = clock(len(indices) * frames * 1000 // rate)
                    track = (f"{ordinal + 1} of {per_channel[channel]}"
                            if per_channel[channel] > 1 else "")
                    entries.append((
                        key, f"{stem} {len(entries) + 1}",
                        (len(entries) + 1, length, stem, channel, track),
                    ))
        except Exception as exc:
            self.status.setText(f"Could not read the disc: {exc}")
            return
        if not self._by_key:
            self.status.setText(
                f"{os.path.basename(path)} has no streamed music in it - "
                "an ISO or a CD folder cannot carry it.")
            self.transport.set_entries([])
            self.export_all.setEnabled(False)
            return
        self.image = path
        audio = self._audio_track(path)
        if audio:
            size = os.path.getsize(audio) - CDDA_PREGAP
            key = "TRACK2"
            self._by_key[key] = ("cdda", audio)
            entries.append((
                key, "Track 2",
                (len(entries) + 1, clock(size * 1000 // CDDA_BYTES_PER_SECOND),
                 "CD audio", "", ""),
            ))
        disc = self.names.load(path)
        self.transport.set_entries(entries, self.names.names())
        self.export_all.setEnabled(True)
        self.status.setText(
            f"{os.path.basename(path)}: {len(entries)} piece(s) of music"
            + (f", {len(self.names.names())} named ({disc})." if disc else ".")
            + " A track takes a few seconds to decode the first time, then "
            "it is kept. Select one and press F2, or Rename, to name it.")

    @staticmethod
    def _audio_track(path):
        """The bin/cue's second track, if it is sitting beside the first."""
        folder, stem = os.path.dirname(path), os.path.basename(path)
        if "Track 1" not in stem:
            return None
        candidate = os.path.join(folder, stem.replace("Track 1", "Track 2"))
        return candidate if os.path.exists(candidate) else None

    # --- playing ------------------------------------------------------

    def _wanted(self, key):
        self._request(key, play=True)

    def _request(self, key, play):
        """Make sure `key` is decoded; play it or save it when it is.

        Track 2 needs no decoding at all - it is already PCM - so it is
        answered here rather than on the worker."""
        if key not in self._by_key:
            return
        cached = self._cache.get(key)
        if cached is not None:
            self._ready(key, cached, play)
            return
        kind, payload = self._by_key[key]
        if kind == "cdda":
            # The pregap is dropped so it starts on the music rather
            # than on two seconds of silence.
            with open(payload, "rb") as f:
                f.seek(CDDA_PREGAP)
                wav = xa.wav_bytes_raw(f.read(), CDDA_RATE, 2)
            self._cache[key] = wav
            self.status.setText("Track 2: CD audio, 44100 Hz stereo.")
            self._ready(key, wav, play)
            return
        lba, indices = payload
        self._stop_decode()
        self.status.setText("Decoding..." if play else "Decoding to save...")
        self._decode = _Decode(key, self.image, lba, indices)
        self._decode.done.connect(self._decoded)
        self._decode.start()

    def _decoded(self, key, wav, note):
        if wav is None:
            self.status.setText(f"Could not decode that channel: {note}")
            self._pending_save = None
            return
        self._cache[key] = wav
        self.status.setText(f"{note} - decoded once and kept.")
        self._ready(key, wav, play=self.transport.current_key() == key)

    def _ready(self, key, wav, play):
        """A track's audio is in hand: play it, and write it if one was
        asked for while it was still decoding."""
        if self._pending_save and self._pending_save[0] == key:
            _key, path = self._pending_save
            self._pending_save = None
            self._write(path, wav)
        elif play:
            self.transport.play_bytes(wav)

    # --- naming and saving --------------------------------------------

    def _renamed(self, key, name):
        path = self.names.rename(key, name)
        self.status.setText(
            (f"Named {key}." if name else f"Cleared the name for {key}.")
            + (f" Saved to {os.path.basename(path)}." if path else
               " No disc serial found, so the name was not saved."))

    def _save(self, key, path):
        """Save a piece of music. Decoding one takes a few seconds, so
        if it is not in hand yet the write waits on the worker rather
        than freezing the window."""
        cached = self._cache.get(key)
        if cached is not None:
            self._write(path, cached)
            return
        self._pending_save = (key, path)
        self._request(key, play=False)

    def _save_all(self):
        """Write every piece of music into a folder. Decodes each one
        that isn't already cached in turn, which for the streamed
        channels can take a while - the status line tracks progress."""
        folder = QFileDialog.getExistingDirectory(
            self, "Write every piece of music into...")
        if not folder:
            return
        self._stop_decode()
        total = len(self._by_key)
        for row, key in enumerate(self._by_key, 1):
            name = self.names.get(key)
            stem = audio_export.safe_name(f"{row:02d}_{key}"
                                          + (f"_{name}" if name else ""))
            self.status.setText(f"Saving {row}/{total}: {stem}...")
            # This decodes on the GUI thread, one track after another -
            # pump the event loop so the status line above actually
            # updates and the window doesn't read as hung while it does.
            QApplication.processEvents()
            try:
                wav = self._decode_sync(key)
                audio_export.save(os.path.join(folder, f"{stem}.wav"), wav)
            except Exception as exc:
                self.status.setText(f"Stopped at {stem}: {exc}")
                return
        self.status.setText(f"Wrote {total} piece(s) of music into {folder}.")

    def _decode_sync(self, key):
        """The bytes for `key`, decoding and caching them now rather
        than through the worker thread - only used by _save_all(),
        which is already a long blocking operation of its own."""
        if key in self._cache:
            return self._cache[key]
        kind, payload = self._by_key[key]
        if kind == "cdda":
            with open(payload, "rb") as f:
                f.seek(CDDA_PREGAP)
                wav = xa.wav_bytes_raw(f.read(), CDDA_RATE, 2)
        else:
            lba, indices = payload
            with open(self.image, "rb") as f:
                samples, rate, speakers = xa.decode_channel(f, lba, indices)
            wav = xa.wav_bytes(samples, rate, speakers)
        self._cache[key] = wav
        return wav

    def _write(self, path, wav):
        try:
            audio_export.save(path, wav)
        except Exception as exc:
            self.status.setText(f"Could not save: {exc}")
            return
        self.status.setText(f"Wrote {os.path.basename(path)}.")

    def _stop_decode(self):
        if self._decode is not None and self._decode.isRunning():
            self._decode.requestInterruption()
            self._decode.wait(8000)
        self._decode = None

    def closeEvent(self, event):
        self.transport.stop()
        self._stop_decode()
        super().closeEvent(event)

"""The sound effects, out of the disc's sound banks.

TOMBA2.SND holds 24 VABs - PlayStation sound banks - carrying 385
waveforms between them: footsteps and menu blips, Tomba's grunts, and
the instruments the sequenced music is played on. functions/sfx.py takes
them apart; this lists them, plays them and lets them be named.

They are short and decode in milliseconds, so unlike the streamed music
there is no worker thread here - the whole bank is decoded once when the
disc is opened and kept.
"""
import os

from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from functions import audio_export, sfx, voice, xa
from gui.audio_transport import AudioTransport
from gui.name_store import NameStore


def seconds(samples, rate):
    """A sound effect's length. Most are a fraction of a second, so the
    m:ss the other panels use would read 0:00 for nearly all of them."""
    value = samples / rate
    return f"{value:4.1f}s" if value >= 10 else f"{value:4.2f}s"


class SfxPanel(QWidget):
    """Browse, play and name the sound effects."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self._by_key = {}           # "bank:index" -> (offset, length, loops)
        self._snd = None
        self._cache = {}
        self.names = NameStore("sfx")

        self.pick = QPushButton("Open BIN/IMG...")
        self.pick.setToolTip(
            "Only needed for a disc opened as a folder - opening a BIN "
            "normally sets this up on its own")
        self.pick.clicked.connect(self._browse)

        self.transport = AudioTransport(
            columns=["Index", "Bank", "Slot", "Length", "Loop"])
        self.transport.wanted.connect(self._wanted)
        self.transport.renamed.connect(self._renamed)
        self.transport.save_requested.connect(self._save_one)

        self.export_all = QPushButton("Save all as WAV...")
        self.export_all.setToolTip("Write every waveform into a folder, "
                                   "using the names given here")
        self.export_all.clicked.connect(self._save_all)
        self.export_all.setEnabled(False)

        self.status = QLabel("No disc open - the sound effects live in "
                             "TOMBA2.SND on the disc.")
        self.status.setWordWrap(True)

        # Individual saves first, the bulk one last, all in the one row -
        # "Save all" is what the other two do repeated over everything,
        # not a separate action, so it reads as the last step along the
        # same line rather than off on its own above them.
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
            "Disc image (*.bin *.img *.iso);;All files (*)")
        if path:
            self.set_image(path)

    def set_image(self, path):
        """Read the sound banks off the disc and list every waveform."""
        self.transport.stop()
        self._cache.clear()
        try:
            data = voice.extract_file(path, "TOMBA2.SND")
        except Exception as exc:
            self.status.setText(f"Could not read the disc: {exc}")
            return
        if not data:
            self.status.setText(
                f"{os.path.basename(path)} has no TOMBA2.SND in it.")
            self.transport.set_entries([])
            return
        self._snd = data
        slots = sfx.samples(data)
        self.image = path
        disc = self.names.load(path)

        self._by_key = {}
        entries = []
        for number, (bank, index, offset, size) in enumerate(slots, 1):
            held = sfx.loops(data, offset, size)
            key = f"{bank}:{index}"
            self._by_key[key] = (offset, size, held)
            entries.append((
                key, f"SFX {number}",
                (number, bank, index,
                 seconds(sfx.length(size), sfx.RATE), "loop" if held else ""),
                held,
            ))
        self.transport.set_entries(entries, self.names.names())
        self.export_all.setEnabled(True)
        named = len(self.names.names())
        self.status.setText(
            f"{os.path.basename(path)}: {len(slots)} waveforms in "
            f"{1 + max(s[0] for s in slots)} banks"
            + (f", {named} named ({disc})." if disc else ".")
            + " Select one and press F2, or Rename, to name it. A looping "
            "one repeats until you play something else.")

    # --- playing ------------------------------------------------------

    def _wav(self, key):
        """One waveform as WAV bytes, decoded once and kept."""
        if key not in self._cache:
            offset, length, _loops = self._by_key[key]
            samples = sfx.decode(self._snd, offset, length)
            self._cache[key] = xa.wav_bytes(samples, sfx.RATE, 1)
        return self._cache[key]

    def _wanted(self, key):
        if key not in self._by_key or self._snd is None:
            return
        self.transport.play_bytes(self._wav(key))

    # --- naming and saving --------------------------------------------

    def _renamed(self, key, name):
        path = self.names.rename(key, name)
        self.status.setText(
            (f"Named {key}." if name else f"Cleared the name for {key}.")
            + (f" Saved to {os.path.basename(path)}." if path else
               " No disc serial found, so the name was not saved."))

    def _save_one(self, key, path):
        if key not in self._by_key:
            return
        try:
            audio_export.save(path, self._wav(key))
        except Exception as exc:
            self.status.setText(f"Could not save: {exc}")
            return
        self.status.setText(f"Wrote {os.path.basename(path)}.")

    def _save_all(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Write every waveform into...")
        if not folder:
            return
        written = 0
        for row, key in enumerate(self._by_key):
            name = self.names.get(key)
            bank, index = key.split(":")
            stem = audio_export.safe_name(
                f"{row:03d}_{bank}-{index}" + (f"_{name}" if name else ""))
            try:
                audio_export.save(os.path.join(folder, f"{stem}.wav"),
                                  self._wav(key))
                written += 1
            except Exception as exc:
                self.status.setText(f"Stopped at {stem}: {exc}")
                return
        self.status.setText(f"Wrote {written} waveforms into {folder}.")

    def closeEvent(self, event):
        self.transport.stop()
        super().closeEvent(event)

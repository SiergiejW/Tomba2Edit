"""The sound effects, out of the disc's sound banks.

TOMBA2.SND holds 24 VABs - PlayStation sound banks - carrying 385
waveforms between them: footsteps and menu blips, Tomba's grunts, and
the instruments the sequenced music is played on. formats/audio/sfx.py takes
them apart; this lists them, plays them and lets them be named.

They are short and decode in milliseconds, so unlike the streamed music
there is no worker thread here - the whole bank is decoded once when the
disc is opened and kept.
"""
import os

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

from formats.audio import audio_export
from formats.audio import sfx
from formats.audio import voice
from formats.audio import xa
from gui.widgets.waveform import peaks
from gui.widgets import mascot
from formats.audio.audio_transport import AudioTransport
from gui.widgets.name_store import NameStore


def seconds(samples, rate):
    """A sound effect's length. Most are a fraction of a second, so the
    m:ss the other panels use would read 0:00 for nearly all of them."""
    value = samples / rate
    return f"{value:4.1f}s" if value >= 10 else f"{value:4.2f}s"


class SfxPanel(QWidget):
    # Raised when a sound is swapped, so the window can put the
    # pending-edit count and the tab's "*" back in step.
    edits_changed = pyqtSignal()

    """Browse, play and name the sound effects."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self._by_key = {}           # "bank:index" -> (offset, length, loops)
        self._snd = None
        self._rates = {}            # "bank:index" -> Hz, from MAIN.EXE's sound table
        self._cache = {}
        self.names = NameStore("sfx")

        self.pick = QPushButton("Open BIN/IMG...")
        self.pick.setToolTip(
            "Only needed for a disc opened as a folder - opening a BIN "
            "normally sets this up on its own")
        self.pick.clicked.connect(self._browse)

        self.transport = AudioTransport(
            source="SFX", previews=True,
            columns=["Index", "Bank", "Slot", "Length", "Loop", "Rate"], pitch=True)
        # Worth having here and not on Music: an effect's waveform is
        # already in memory once TOMBA2.SND is open, so a thumbnail is
        # a decode of a few kilobytes with no disc read behind it. They
        # fill in one at a time, for the rows on screen only, on a
        # worker thread - scrolling never waits for them.
        self.transport.enable_previews(self._preview)
        self.transport.wanted.connect(self._wanted)
        self.transport.renamed.connect(self._renamed)
        self.transport.save_requested.connect(self._save_one)

        # Swapping a sound. MainWindow points snd_edits at the store
        # that holds TOMBA2.SND, which is what makes a swap survive a
        # project save and reach the disc.
        self.snd_edits = None
        self.replace_btn = QPushButton("Replace with WAV...")
        self.replace_btn.setToolTip(
            "Swap the selected sound for a WAV. It is resampled to the "
            "rate the game plays this one at and encoded to the SPU's "
            "ADPCM; the bank's waveforms share a fixed area, so there is "
            "a byte budget and it is shown after every swap")
        self.replace_btn.clicked.connect(self._replace)
        self.replace_btn.setEnabled(False)

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
        top.addWidget(self.replace_btn)
        top.addWidget(self.transport.save_wav)
        top.addWidget(self.transport.save_mp3)
        top.addWidget(self.export_all)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.transport, 1)
        layout.addWidget(mascot.beside(self.status))

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
        # A project's edited copy wins over the disc's, so a swapped
        # sound is the one that plays and the one that gets listed.
        if self.snd_edits is not None and self.snd_edits.loaded():
            try:
                data = self.snd_edits.rebuild()
            except Exception:
                pass
        elif self.snd_edits is not None:
            self.snd_edits.set_source(data)
        self._snd = data
        try:
            exe = voice.extract_file(path, "MAIN.EXE")
            # The area sounds are defined in each area's overlay.
            overlays = {}
            for area, name in enumerate(sfx.OVERLAYS):
                blob = voice.extract_file(path, name)
                if blob:
                    overlays[area] = blob
            self._rates = sfx.default_rates(exe, data, overlays) if exe else {}
        except Exception:
            self._rates = {}
        slots = sfx.samples(data)
        self.image = path
        disc = self.names.load(path)

        self._by_key = {}
        entries = []
        for number, (bank, index, offset, size) in enumerate(slots, 1):
            held = sfx.loops(data, offset, size)
            key = f"{bank}:{index}"
            self._by_key[key] = (offset, size, held)
            rate = self._rates.get(key)
            entries.append((
                key, f"SFX {number}",
                (number, bank, index,
                 seconds(sfx.length(size), rate or sfx.RATE),
                 "loop" if held else "", f"{rate} Hz" if rate else ""),
                held,
            ))
        self.transport.set_entries(entries, self.names.names())
        self.export_all.setEnabled(True)
        self.replace_btn.setEnabled(self.snd_edits is not None)
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
            # At the rate the game's own sound table plays it, where one
            # names it; the reference rate otherwise.
            self._cache[key] = xa.wav_bytes(samples, self._rates.get(key, sfx.RATE), 1)
        return self._cache[key]

    def _preview(self, key):
        """This waveform's envelope, for the thumbnail column."""
        if key not in self._by_key or self._snd is None:
            return None
        return peaks(self._wav(key))

    def _wanted(self, key):
        if key not in self._by_key or self._snd is None:
            return
        wav = self._wav(key)
        self.transport.show_wave(wav, self._caption(key))
        self.transport.play_bytes(wav)

    def _caption(self, key):
        name = self.names.get(key) if self.names else ""
        return name or f"Sound {key}"

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

    def _replace(self):
        """Swap the selected waveform for a WAV file."""
        from formats.audio import snd_edit
        from formats.audio import vag

        key = self.transport.current_key()
        if key not in self._by_key:
            self.status.setText("Pick a sound in the list first.")
            return
        if self.snd_edits is None or not self.snd_edits.loaded():
            self.status.setText(
                "The sound file isn't loaded, so there is nowhere to put a "
                "replacement. Open the disc or a project first.")
            return
        bank, index = (int(part) for part in key.split(":"))
        offset, size, held = self._by_key[key]

        path, _ = QFileDialog.getOpenFileName(
            self, "Replace this sound with a WAV", "",
            "WAV audio (*.wav);;All files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                samples, rate = vag.from_wav(f.read())
        except (OSError, ValueError) as exc:
            self.status.setText(f"Couldn't use that WAV: {exc}")
            return

        # Resampled to whatever rate the game plays this slot at, so the
        # replacement comes out at the pitch and length the sound was
        # asked for rather than at whatever the file happened to be.
        target = self._rates.get(key) or sfx.RATE
        samples = vag.resample(samples, rate, target)
        blob = vag.encode(samples, loop_start=0 if held else None,
                          repeat=held)

        # Too long for the room the bank has? Offer to cut it rather
        # than just saying no. A sound that is too SHORT needs nothing
        # asked: it already fits, and the bytes left over at the end of
        # the bank are zero-filled by the repack.
        room = self._room_for(bank, index)
        if len(blob) > room:
            kept = (room // sfx.BLOCK) * sfx.BLOCK_SAMPLES
            if kept <= 0:
                self.status.setText(
                    f"There is no room at all for sound {key}: the other "
                    f"waveforms in bank {bank} already fill it.")
                return
            lost = (len(samples) - kept) / float(target)
            ask = QMessageBox(self)
            ask.setIcon(QMessageBox.Icon.Question)
            ask.setWindowTitle("Longer than the slot")
            ask.setText(
                f"That sound is {len(blob) - room:,} byte(s) too big for "
                f"bank {bank}.")
            ask.setInformativeText(
                f"The waveforms in this bank share {room:,} bytes before "
                "the next bank begins, and the banks cannot move.\n\n"
                f"It can be cut to the first {kept / float(target):.2f} "
                f"seconds, losing {lost:.2f} seconds off the end.")
            cut = ask.addButton("Cut it to fit",
                                QMessageBox.ButtonRole.AcceptRole)
            ask.addButton(QMessageBox.StandardButton.Cancel)
            ask.setDefaultButton(cut)
            ask.exec()
            if ask.clickedButton() is not cut:
                self.status.setText("Left alone - the sound was too long.")
                return
            samples = samples[:kept]
            blob = vag.encode(samples, loop_start=0 if held else None,
                              repeat=held)
            trimmed = True
        else:
            trimmed = False

        try:
            state = self.snd_edits.stage_sound(bank, index, blob)
        except snd_edit.SndEditError as exc:
            self.status.setText(str(exc))
            return
        self.edits_changed.emit()

        self._cache.pop(key, None)
        self.set_image(self.image)
        self.status.setText(
            f"Sound {key} replaced from {os.path.basename(path)} - "
            f"{len(samples):,} samples at {target} Hz"
            + (" (cut to fit)" if trimmed else "")
            + (", looping." if held else ".")
            + f" Bank {bank} now uses {state['used']:,} of "
            f"{state['capacity']:,} bytes - {state['free']:,} free. Save the "
            "project to keep it.")

    def _room_for(self, bank, index):
        """How many bytes this one waveform is allowed to take.

        The bank's whole area, less what every OTHER sound in it needs -
        so it counts against the replacements already staged, not
        against what the disc shipped."""
        state = self.snd_edits.compute_sounds(bank)
        held_now = self.snd_edits.sound(bank, index)
        return state["capacity"] - (state["used"] - len(held_now or b""))

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

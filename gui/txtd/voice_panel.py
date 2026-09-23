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
from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog,
                             QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QVBoxLayout, QWidget)

from functions import audio_export, bin_writer, disc_library, voice, xa
from functions.voice_edit import VoiceEditStore
from gui.audio_transport import AudioTransport, clock
from gui.name_store import NameStore
from gui.voice_import import confirm_length


class _Decode(QThread):
    """Decoding a channel, off the GUI thread.

    A whole channel is a couple of seconds of work - short, but long
    enough to freeze the window. Left unparented deliberately: a QThread
    owned by a widget is destroyed with it, and destroying one that is
    still running takes the process down."""

    done = pyqtSignal(int, object, int)

    def __init__(self, image, lba, sectors, channel, overrides=None):
        super().__init__()
        self.args = (image, lba, sectors, channel)
        self.channel = channel
        # A snapshot, not a live reference - see VoicePanel._request's
        # own note on why this has to be taken before the thread starts
        # rather than read from inside run().
        self.overrides = overrides

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
                    f, lba, chans[key], frame=frame, overrides=self.overrides)
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
        self._by_key = {}           # key -> sector count
        self._decode = None
        self._cache = {}            # channel -> wav bytes
        self._pending_save = None   # (key, path) waiting on a decode
        self.names = NameStore("dialogue")
        self._edits = VoiceEditStore()

        self.pick = QPushButton("Open BIN/IMG...")
        self.pick.setToolTip(
            "Only needed for a disc opened as a folder - opening a BIN "
            "normally sets this up on its own")
        self.pick.clicked.connect(self._browse)
        self.known = QComboBox()
        self.known.addItem("Known discs (iso/)...")
        for label, path in disc_library.find_discs():
            self.known.addItem(label, path)
        self.known.setEnabled(self.known.count() > 1)
        self.known.setToolTip(
            "Data tracks already found under the project's iso/ folder")
        self.known.activated.connect(self._open_known)
        self.extract = QPushButton("Extract VOICE.XA...")
        self.extract.setToolTip(
            "Write a VOICE.XA that actually works into a CD folder - the "
            "Form 2 payloads, 2324 bytes a sector, which an ordinary file "
            "copy truncates to 2048")
        self.extract.clicked.connect(self._extract)
        self.extract.setEnabled(False)

        self.transport = AudioTransport(
            source="Dialogues",
            columns=["Index", "Channel", "Sectors", "Length"])
        self.transport.wanted.connect(self._wanted)
        self.transport.renamed.connect(self._renamed)
        self.transport.save_requested.connect(self._save)

        self.export_all = QPushButton("Save all as WAV...")
        self.export_all.setToolTip("Write every channel into a folder, "
                                   "using the names given here")
        self.export_all.clicked.connect(self._save_all)
        self.export_all.setEnabled(False)

        self.import_btn = QPushButton("Import WAV into selected...")
        self.import_btn.setToolTip(
            "Replace the selected channel's own sectors with a WAV - "
            "staged in memory until Export patched BIN writes it out")
        self.import_btn.clicked.connect(self._import_selected)
        self.import_btn.setEnabled(False)

        self.export_patched = QPushButton("Export patched BIN...")
        self.export_patched.setToolTip(
            "Write every staged edit (from here and the TXTD tab) into a "
            "copy of the data track")
        self.export_patched.clicked.connect(self._export_patched)
        self.export_patched.setEnabled(False)

        self.status = QLabel("No disc open - the voice track needs a raw "
                             "2352-byte data track, not a CD folder or ISO.")
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addWidget(self.known)
        top.addWidget(self.extract)
        top.addStretch(1)
        top.addWidget(self.transport.save_wav)
        top.addWidget(self.transport.save_mp3)
        top.addWidget(self.export_all)

        bottom = QHBoxLayout()
        bottom.addWidget(self.import_btn)
        bottom.addWidget(self.export_patched)
        bottom.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.transport, 1)
        layout.addLayout(bottom)
        layout.addWidget(self.status)

    def set_edit_store(self, store):
        """Share one VoiceEditStore with the TXTD tab, so a per-line
        import there and a per-channel one here both end up in the same
        Export patched BIN. Keeps whatever is already staged in it."""
        self._edits = store
        if self.image:
            self._edits.set_image(self.image)
        self.export_patched.setEnabled(self._edits.count() > 0)

    def _open_known(self, index):
        path = self.known.itemData(index)
        if path:
            self.set_image(path)

    # --- opening ------------------------------------------------------

    def _browse(self):
        # A cue sheet is what a rip is actually called, and picking one
        # of the two .bin files beside it is a coin toss - so take the
        # cue and resolve it to the data track ourselves.
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the disc's data track", "",
            "Disc (*.cue *.bin *.img);;Extracted voice (*.XA);;"
            "All files (*)")
        if path:
            self.set_image(path)

    def set_image(self, path):
        """Point the panel at a disc track, or a good VOICE.XA.

        A cue sheet is resolved to the data track it names first, so
        handing this the file a rip is actually called works."""
        from functions import source_disc

        try:
            path = source_disc.data_track(path)
        except source_disc.SourceDiscError as exc:
            self.status.setText(str(exc))
            return
        try:
            self.lba, self.sectors = voice.find_track(path)
        except Exception as exc:
            # The commonest reason by far is a 2048-byte image, where
            # there is no voice to find and never will be - say that
            # instead of repeating a lookup failure the user can't act
            # on. See functions/voice's own note on why.
            if not source_disc.is_raw_track(path):
                self.status.setText(
                    f"{os.path.basename(path)} has 2048-byte sectors, so the "
                    "spoken dialogue isn't in it - extracting a disc that way "
                    "drops 12% of every audio sector. Open the disc's .cue or "
                    "its Track 1 .bin instead.")
                return
            self.status.setText(str(exc))
            return
        self.image = path
        self._cache.clear()
        self._edits.set_image(path)
        self.export_patched.setEnabled(self._edits.count() > 0)
        self.import_btn.setEnabled((xa.framing(path) or xa.RAW) == xa.RAW)
        channels = voice.channels(path, self.lba, self.sectors)
        per_sector = xa.SAMPLES_PER_SECTOR
        disc = self.names.load(path)

        self._by_key = {}
        entries = []
        for number, (channel, count) in enumerate(channels, 1):
            key = f"VOICE.XA:{channel}"
            self._by_key[key] = count
            entries.append((
                key, f"Channel {channel}",
                (number, channel, f"{count:,}",
                 clock(count * per_sector * 1000 // 18900)),
            ))
        self.transport.set_entries(entries, self.names.names())
        self.status.setText(
            f"{os.path.basename(path)}: {len(entries)} channels, "
            f"{self.sectors:,} sectors"
            + (f", {len(self.names.names())} named ({disc})." if disc else ".")
            + " Pick one to decode and play it whole - the seek bar scrubs "
            "through it, and F2 names it.")
        self.extract.setEnabled(True)
        self.export_all.setEnabled(True)
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

    @staticmethod
    def _channel_of(key):
        return int(key.rsplit(":", 1)[1])

    def _wanted(self, key):
        """The transport asked for a key; decode it if it is not cached."""
        self._request(key, play=True)

    def _request(self, key, play):
        if key not in self._by_key or not self.image:
            return
        channel = self._channel_of(key)
        cached = self._cache.get(channel)
        if cached is not None:
            self._ready(key, cached, play)
            return
        self._stop_decode()
        self.status.setText(f"Decoding channel {channel}...")
        # Taken now rather than read inside the thread: self._edits.sectors
        # could gain a new import while this decode is still running, and
        # a dict read concurrently with a write from the GUI thread is
        # exactly the kind of thing to not do across threads. A snapshot
        # is safe either way - it can only ever be a moment stale, and
        # the cache is cleared on import besides (see _import_selected),
        # so the next request starts a fresh decode with a fresh snapshot.
        overrides = (dict(self._edits.sectors)
                    if self._edits.image == self.image else None)
        self._decode = _Decode(self.image, self.lba, self.sectors, channel,
                               overrides=overrides)
        self._decode.done.connect(self._decoded)
        self._decode.start()

    def _decoded(self, channel, wav, rate):
        key = f"VOICE.XA:{channel}"
        if wav is None:
            self.status.setText(f"Could not decode channel {channel}.")
            self._pending_save = None
            return
        self._cache[channel] = wav
        self.status.setText(
            f"Channel {channel} at {rate} Hz - decoded once and kept, so "
            "coming back to it is instant.")
        self._ready(key, wav, play=self.transport.current_key() == key)

    def _ready(self, key, wav, play):
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
        """Write a channel out. A channel takes a few seconds to decode,
        so if it is not in hand the write waits on the worker."""
        if key not in self._by_key:
            return
        cached = self._cache.get(self._channel_of(key))
        if cached is not None:
            self._write(path, cached)
            return
        self._pending_save = (key, path)
        self._request(key, play=False)

    def _save_all(self):
        """Write every channel into a folder, decoding as it goes."""
        folder = QFileDialog.getExistingDirectory(
            self, "Write every channel into...")
        if not folder:
            return
        self._stop_decode()
        total = len(self._by_key)
        for row, key in enumerate(self._by_key, 1):
            channel = self._channel_of(key)
            name = self.names.get(key)
            stem = audio_export.safe_name(
                f"{row:02d}_channel_{channel}" + (f"_{name}" if name else ""))
            self.status.setText(f"Saving {row}/{total}: {stem}...")
            QApplication.processEvents()
            try:
                wav = self._cache.get(channel)
                if wav is None:
                    overrides = (self._edits.sectors
                                if self._edits.image == self.image else None)
                    with open(self.image, "rb") as f:
                        frame = xa.framing(self.image) or xa.RAW
                        chans = xa.channel_map(f, self.lba, self.sectors, frame)
                        found = next((k for k in chans if k[1] == channel), None)
                        samples, rate, speakers = xa.decode_channel(
                            f, self.lba, chans[found], frame=frame,
                            overrides=overrides)
                    wav = xa.wav_bytes(samples, rate, speakers)
                    self._cache[channel] = wav
                audio_export.save(os.path.join(folder, f"{stem}.wav"), wav)
            except Exception as exc:
                self.status.setText(f"Stopped at {stem}: {exc}")
                return
        self.status.setText(f"Wrote {total} channel(s) into {folder}.")

    # --- importing ------------------------------------------------------

    def _import_selected(self):
        key = self.transport.current_key()
        if not key or key not in self._by_key:
            QMessageBox.information(self, "Import",
                                    "Select a channel first.")
            return
        frame = xa.framing(self.image) or xa.RAW
        if frame != xa.RAW:
            QMessageBox.warning(
                self, "Import", "Importing needs a raw disc track "
                "(BIN/IMG) opened, not an extracted VOICE.XA - the "
                "original sectors have to be there to patch.")
            return
        channel = self._channel_of(key)
        path, _ = QFileDialog.getOpenFileName(
            self, f"Replace channel {channel} with...", "",
            "WAV audio (*.wav)")
        if not path:
            return
        try:
            pcm, rate, channels = audio_export.load_wav(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import",
                                 f"Could not read that WAV: {exc}")
            return
        samples = audio_export.resample(
            audio_export.to_mono(pcm, channels), rate, 18900)

        with open(self.image, "rb") as f:
            chans = xa.channel_map(f, self.lba, self.sectors, frame)
        found = next((k for k in chans if k[1] == channel), None)
        if found is None:
            QMessageBox.warning(self, "Import",
                                "That channel isn't in this track anymore.")
            return
        abs_indices = [self.lba + i for i in chans[found]]
        needed = len(abs_indices) * xa.SAMPLES_PER_SECTOR
        if not confirm_length(self, len(samples), needed):
            return
        try:
            count = self._edits.stage_clip(self.image, abs_indices, samples)
        except Exception as exc:
            QMessageBox.critical(self, "Import",
                                 f"Could not stage that: {exc}")
            return
        self._cache.pop(channel, None)
        self.export_patched.setEnabled(True)
        self.status.setText(
            f"Staged channel {channel} ({count} sector(s)) - "
            f"{self._edits.count()} sector(s) staged in all. Export "
            "patched BIN... writes them out.")

    def _export_patched(self):
        if not self._edits.count():
            QMessageBox.information(self, "Export", "No edits staged yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Write the patched data track", "",
            "Disc track (*.bin *.img)")
        if not path:
            return
        self.status.setText("Writing patched track...")
        QApplication.processEvents()
        try:
            n = self._edits.export(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Could not write: {exc}")
            return
        # Without this, the patched Track 1 is orphaned: nothing that
        # reads bin/cue discs (most emulators included) will touch a
        # bare .bin with no .cue naming it, and a bin/cue disc's music
        # lives in a whole separate Track 2 file the copy above never
        # touched - silently dropping it would leave a disc that opens
        # but plays no music at all.
        try:
            note = bin_writer.copy_audio_track(self._edits.image, path)
        except Exception as exc:
            note = f"Wrote the track, but could not write its cue: {exc}"
        self.status.setText(
            f"Wrote {os.path.basename(path)} with {n} patched sector(s). "
            f"{note} Open the .cue in your emulator - not the .bin "
            "directly - in place of the original disc.")

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
            self._decode.wait(5000)
        self._decode = None

    def closeEvent(self, event):
        self.transport.stop()
        self._stop_decode()
        super().closeEvent(event)

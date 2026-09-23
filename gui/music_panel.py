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

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
                             QPushButton, QSplitter, QVBoxLayout, QWidget)

from functions import audio_export, bgm, seq, voice, xa
from gui import mascot
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


class _Render(QThread):
    """One SEQ played on its VAB's instruments, off the GUI thread."""

    done = pyqtSignal(str, object, str)

    def __init__(self, key, data, at, snd, bank):
        super().__init__()
        self.key = key
        self.args = (data, at, snd, bank)

    def run(self):
        try:
            stereo, _used = seq.render(*self.args)
            self.done.emit(self.key,
                           xa.wav_bytes_raw(seq.pcm(stereo), seq.RATE, 2),
                           f"{seq.RATE} Hz stereo, played on its sound bank")
        except Exception as exc:
            self.done.emit(self.key, None, str(exc))


class MusicPanel(QWidget):
    # Raised when a sequence is staged - see SfxPanel.edits_changed.
    edits_changed = pyqtSignal()

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
            source="Music",
            columns=["Index", "Length", "Stream", "Channel", "Track"],
            # Any piece of music can be repeated - there's no "this one
            # loops, that one doesn't" the way an SFX sample has - and
            # once Loop is checked it should keep the current song
            # going rather than letting the advance checkbox hand off
            # to the next one, so both flip from AudioTransport's
            # SFX-shaped defaults.
            #
            # select_plays=False and the "Auto-advance" label matter
            # together: this checkbox is only ever meant to mean "when
            # the song I started finishes, play the next one" - not
            # "selecting a row starts it playing", which is what
            # select_plays governs elsewhere (SFX/Dialogues want that;
            # Music doesn't - opening a disc landing on row 0 of a
            # freshly built list must never start music playing on its
            # own before anyone asked for anything).
            autoplay_default=False, always_loopable=True,
            loop_beats_autoplay=True, select_plays=False,
            autoplay_label="Auto-advance")
        self.transport.wanted.connect(self._wanted)
        self.transport.renamed.connect(self._renamed)
        self.transport.save_requested.connect(self._save)

        # The sequenced music: SEQs played on the sound banks' instruments -
        # event jingles, loading, and what dialogue scripts start
        # (functions/seq.py).
        self._snd = None
        self._seqs = {}             # key -> (data, offset, bank)
        self._seq_cache = {}
        self._render = None
        self.seq_names = NameStore("sequence")
        self.sfx_names = NameStore("sfx")
        # A second list on the same player; keys tell the two apart.
        self.sequence_list = self.transport.add_list(
            columns=["Index", "Length", "Slot", "From", "Use", "Instruments"],
            source="Sequences")

        self.export_all = QPushButton("Save all as WAV...")
        self.export_all.setToolTip("Write every piece of music into a "
                                   "folder, using the names given here")
        self.export_all.clicked.connect(self._save_all)
        self.export_all.setEnabled(False)

        # Editing a sequence happens in a sequencer, not here: a SEQ is
        # near enough a MIDI file that it can go out as one, be worked
        # on in whatever the person already knows, and come back.
        # MainWindow sets snd_edits to the store that holds TOMBA2.SND,
        # which is what makes an imported sequence survive a save.
        self.snd_edits = None
        self.export_midi = QPushButton("Export MIDI...")
        self.export_midi.setToolTip(
            "Write the selected sequence out as a .mid to edit in any "
            "sequencer. Note timing is exact; the instruments are the "
            "game's own sound bank, so it will not sound right elsewhere")
        self.export_midi.clicked.connect(self._export_midi)
        self.export_midi.setEnabled(False)

        self.edit_notes = QPushButton("Edit notes...")
        self.edit_notes.setToolTip(
            "Open the selected sequence in a piano roll - drag notes "
            "about, draw new ones, and see what it costs against the "
            "space the ten sequences share")
        self.edit_notes.clicked.connect(self._edit_notes)
        self.edit_notes.setEnabled(False)

        self.import_midi = QPushButton("Import MIDI...")
        self.import_midi.setToolTip(
            "Replace the selected sequence with an edited .mid. All ten "
            "share one fixed region on the disc, so there is a byte budget "
            "and it is shown after every import")
        self.import_midi.clicked.connect(self._import_midi)
        self.import_midi.setEnabled(False)

        self.status = QLabel(
            "No disc open. The music is streamed CD-XA, which only survives "
            "in a raw data track - not a CD folder or an ISO.")
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addStretch(1)
        top.addWidget(self.edit_notes)
        top.addWidget(self.export_midi)
        top.addWidget(self.import_midi)
        top.addWidget(self.transport.save_wav)
        top.addWidget(self.transport.save_mp3)
        top.addWidget(self.export_all)

        def column(title, table):
            holder = QWidget()
            inner = QVBoxLayout(holder)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.addWidget(QLabel(f"<b>{title}</b>"))
            inner.addWidget(table, 1)
            return holder

        # Two lists, one player: the controls under them play from
        # whichever list was picked from last.
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(column("BGM", self.transport.lists[0]))
        split.addWidget(column("Sequences", self.sequence_list))
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)
        layout.addWidget(self.transport)
        layout.addWidget(mascot.beside(self.status))

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
        self._load_sequences(path)
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
        if key in self._seqs:
            self._seq_wanted(key)
        else:
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
        store = self.seq_names if key in self._seqs else self.names
        path = store.rename(key, name)
        self.status.setText(
            (f"Named {key}." if name else f"Cleared the name for {key}.")
            + (f" Saved to {os.path.basename(path)}." if path else
               " No disc serial found, so the name was not saved."))

    def _save(self, key, path):
        """Save a piece of music. Decoding one takes a few seconds, so
        if it is not in hand yet the write waits on the worker rather
        than freezing the window."""
        if key in self._seqs:
            self._seq_save(key, path)
            return
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

    # --- sequenced music ----------------------------------------------

    def _load_sequences(self, path):
        """List every SEQ: TOMBA2.SND's ten, then the overlays' own."""
        self._seqs, self._seq_cache = {}, {}
        # The edited TOMBA2.SND when there is one, so an imported
        # sequence plays back straight away and the list shows its new
        # length - the disc's own copy is only the starting point.
        snd = None
        if self.snd_edits is not None and self.snd_edits.loaded():
            try:
                snd = self.snd_edits.rebuild()
            except Exception:
                snd = None
        if snd is None:
            try:
                snd = voice.extract_file(path, "TOMBA2.SND")
            except Exception:
                snd = None
            if snd and self.snd_edits is not None:
                self.snd_edits.set_source(snd)
        self._snd = snd
        if not snd:
            self.transport.set_entries([], table=self.sequence_list)
            return
        self.sfx_names.load(path)
        instruments = self.sfx_names.names()
        found = [(slot, "TOMBA2.SND", snd, at, seq.MUSIC_BANK,
                  seq.RESIDENT_USES.get(slot, ""))
                 for slot, at in seq.resident(snd)]
        for name in seq.OVERLAY_SLOTS:
            try:
                data = voice.extract_file(path, f"{name}.BIN")
            except Exception:
                data = None
            for slot, at in seq.overlay(name, data or b""):
                found.append((slot, name, data, at, seq.bank_for(name),
                              seq.AREA_USES.get(slot, "")))
        entries = []
        for number, (slot, origin, data, at, bank, use) in enumerate(found, 1):
            key = f"{origin}:{at:X}"
            # The slot and where it came from ride along now: importing
            # needs to know which of TOMBA2.SND's ten it is replacing,
            # and an overlay's sequence is not one of them.
            self._seqs[key] = (data, at, bank, slot, origin)
            try:
                _notes, length = seq.perform(data, at)
                used = seq.played(data, at, snd, bank)
            except Exception:
                length, used = 0.0, set()
            named = sorted({instruments.get(f"{b}:{v}", "") for b, v in used} - {""})
            entries.append((key, f"SEQ {number}", (
                number, clock(int(length * 1000)), "" if slot is None else slot,
                origin, use, ", ".join(named))))
        self.seq_names.load(path)
        self.transport.set_entries(entries, self.seq_names.names(),
                                   table=self.sequence_list)
        # Enabled once there is anything to act on rather than on the
        # selection: the list has no "something was picked" signal, and
        # a button that says which sequence it wants beats one that is
        # greyed out for a reason nobody can see.
        self.export_midi.setEnabled(bool(self._seqs))
        self.edit_notes.setEnabled(bool(self._seqs)
                                   and self.snd_edits is not None)
        self.import_midi.setEnabled(bool(self._seqs)
                                    and self.snd_edits is not None)

    # --- sequences out as MIDI, and back ------------------------------

    def _selected_sequence(self):
        """(key, data, at, bank, slot, origin) for the picked sequence.

        Asked of the sequence list by name rather than of whichever list
        the transport is pointed at: clicking in the BGM list moves that
        pointer, and these buttons are about the sequences either way."""
        key = self.transport.key_in(self.sequence_list)
        if key in self._seqs:
            return (key,) + self._seqs[key]
        return None

    def _export_midi(self):
        from functions import midi

        chosen = self._selected_sequence()
        if chosen is None:
            self.status.setText("Pick a sequence on the right first.")
            return
        key, data, at, _bank, slot, origin = chosen
        name = self.seq_names.names().get(key) or (
            f"{origin} slot {slot}" if slot is not None else f"{origin} {at:X}")
        suggested = "".join(c for c in name if c.isalnum() or c in " -_") + ".mid"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sequence as MIDI", suggested,
            "MIDI file (*.mid);;All files (*)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(midi.from_seq(data, at))
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not write that: {exc}")
            return
        resolution, _tempo = seq.header(data, at)
        self.status.setText(
            f"Wrote {os.path.basename(path)} - {resolution} ticks per beat. "
            "Keep that division when you save it back, and keep it a single "
            "track: a SEQ has no way to express either being different.")

    def _edit_notes(self):
        """Open the piano roll on the selected sequence."""
        from functions import snd_edit
        from gui.seq_editor import SequenceEditor

        chosen = self._selected_sequence()
        if chosen is None:
            self.status.setText("Pick a sequence on the right first.")
            return
        key, data, at, _bank, slot, origin = chosen
        if self.snd_edits is None or not self.snd_edits.loaded():
            self.status.setText(
                "The music file isn't loaded, so an edit would have nowhere "
                "to go. Open the disc or a project first.")
            return
        # An overlay's sequences open the same way; what they cannot do
        # is be written back, because they live inside A0x.BIN rather
        # than TOMBA2.SND. Refusing to open them at all meant the button
        # appeared to do nothing, which is a worse answer than showing
        # the music and saying where it lives.
        editable = origin == "TOMBA2.SND" and slot is not None
        refusal = ("" if editable else
                   f"This one lives in {origin}, the area's own overlay, "
                   "not in TOMBA2.SND - so it can be played with and "
                   "exported, but not applied back to the disc yet.")

        def budget(blob):
            if not editable:
                return refusal
            state = self.snd_edits.would_fit(slot, blob)
            if state["free"] < 0:
                return (f"{-state['free']} bytes too big for the "
                        f"{state['capacity']} the ten sequences share.")
            return (f"{state['used']} of {state['capacity']} bytes used "
                    f"across all ten - {state['free']} free.")

        editor = SequenceEditor(data, at, slot if slot is not None else origin,
                                budget=budget, snd=self._snd, bank=_bank,
                                instrument_names=self._instrument_names(_bank),
                                applyable=editable, parent=self)
        if not editor.exec() or editor.result_blob is None:
            return
        try:
            state = self.snd_edits.stage_sequence(slot, editor.result_blob)
        except snd_edit.SndEditError as exc:
            self.status.setText(str(exc))
            return
        self.edits_changed.emit()
        self._seq_cache.pop(key, None)
        self._load_sequences(self.image)
        self.status.setText(
            f"Slot {slot} edited - sequences now use {state['used']} of "
            f"{state['capacity']} bytes, {state['free']} free. Play it to "
            "hear it; save the project to keep it.")

    def _instrument_names(self, bank):
        """{program: what its samples are called}, where they are named.

        The SFX tab lets waveforms be named and those names are on the
        disc beside it; a program is a handful of waveforms, so the
        names it reaches are the closest thing to an instrument name
        this game has. Unnamed programs simply show their number."""
        try:
            programs = seq.instruments(self._snd, bank)
        except Exception:
            return {}
        known = self.sfx_names.names()
        out = {}
        for program, (_volume, _pan, tones) in programs.items():
            found = []
            for tone in tones:
                name = known.get(f"{bank}:{tone.vag}")
                if name and name not in found:
                    found.append(name)
            if found:
                out[program] = ", ".join(found[:2])
        return out

    def _import_midi(self):
        from functions import midi, snd_edit

        chosen = self._selected_sequence()
        if chosen is None:
            self.status.setText("Pick a sequence on the right first.")
            return
        key, data, at, _bank, slot, origin = chosen
        if self.snd_edits is None or not self.snd_edits.loaded():
            self.status.setText(
                "The music file isn't loaded, so there is nowhere to put an "
                "imported sequence. Open the disc or a project first.")
            return
        if origin != "TOMBA2.SND" or slot is None:
            # An overlay's sequences sit inside A0x.BIN, which is a
            # different file with a different layout. Saying so beats
            # writing into the wrong one.
            self.status.setText(
                f"{origin}'s sequences are inside the area overlay, not "
                "TOMBA2.SND, and replacing those isn't supported yet. The "
                "ten in TOMBA2.SND can be replaced.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Import a MIDI over this sequence", "",
            "MIDI file (*.mid *.midi);;All files (*)")
        if not path:
            return
        resolution, _tempo = seq.header(data, at)
        try:
            with open(path, "rb") as f:
                blob = midi.to_seq(f.read(), resolution=resolution)
        except (OSError, midi.MidiError, ValueError) as exc:
            self.status.setText(f"Couldn't use that MIDI: {exc}")
            return
        try:
            state = self.snd_edits.stage_sequence(slot, blob)
        except snd_edit.SndEditError as exc:
            self.status.setText(str(exc))
            return
        self.edits_changed.emit()

        self._seq_cache.pop(key, None)
        self._load_sequences(self.image)
        self.status.setText(
            f"Slot {slot} replaced from {os.path.basename(path)}. "
            f"Sequences now use {state['used']} of {state['capacity']} bytes "
            f"- {state['free']} free. Save the project to keep it, or Build "
            "Disc to hear it in the game.")

    def _seq_wanted(self, key):
        if key not in self._seqs or self._snd is None:
            return
        cached = self._seq_cache.get(key)
        if cached is not None:
            self.transport.play_bytes(cached)
            return
        self._stop_render()
        self.status.setText("Playing the sequence on its instruments...")
        data, at, bank, _slot, _origin = self._seqs[key]
        self._render = _Render(key, data, at, self._snd, bank)
        self._render.done.connect(self._seq_rendered)
        self._render.start()

    def _seq_rendered(self, key, wav, note):
        if wav is None:
            self.status.setText(f"Could not render that sequence: {note}")
            return
        self._seq_cache[key] = wav
        self.status.setText(f"{note} - rendered once and kept.")
        if self.transport.current_key() == key:
            self.transport.play_bytes(wav)

    def _seq_save(self, key, path):
        if key not in self._seqs or self._snd is None:
            return
        if key not in self._seq_cache:
            data, at, bank, _slot, _origin = self._seqs[key]
            stereo, _used = seq.render(data, at, self._snd, bank)
            self._seq_cache[key] = xa.wav_bytes_raw(seq.pcm(stereo), seq.RATE, 2)
        self._write(path, self._seq_cache[key])

    def _stop_render(self):
        if self._render is not None and self._render.isRunning():
            self._render.wait(10000)
        self._render = None

    def closeEvent(self, event):
        self.transport.stop()
        self._stop_decode()
        self._stop_render()
        super().closeEvent(event)

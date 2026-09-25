"""Exporting a sequence as MIDI, with a chance to hear it first.

A SEQ's program numbers index the game's own sound bank, so the .mid
that comes out of it plays on whatever those numbers happen to mean
wherever it is opened. That is usually wrong and always a surprise, and
finding out afterwards means going back and forth between this and a
sequencer.

So the export asks first. Pick a SoundFont, press Play, hear what the
file will actually sound like somewhere else, and - if it helps - remap
the programs before saving. The .mid is written either way; the
SoundFont is only ever used to listen, because a MIDI file has nowhere
to carry one.

The renderer is formats.audio.soundfont: a preview synth, honest about
being one. See its docstring for what it does not do.
"""
import os
import struct

from PyQt6.QtCore import (QBuffer, QByteArray, QSettings, Qt, QThread,
                          pyqtSignal)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QHBoxLayout, QLabel, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout,
                             QAbstractItemView, QHeaderView, QCheckBox)

from formats.audio import midi, seq, soundfont
from formats.audio.transport_icons import set_glyph
from gui.widgets import mascot
from gui.widgets.waveform import WaveView, peaks

SETTINGS = ("Tomba2Edit", "Tomba2Edit")
LAST_FONT = "midi/last_soundfont"
RECENT_FONTS = "midi/recent_soundfonts"
MAX_RECENT = 8

# General MIDI, for the remap box. Not because the game's programs mean
# these - they do not - but because a person choosing a replacement is
# choosing from this list in their head anyway.
GM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2",
    "Harpsichord", "Clavi", "Celesta", "Glockenspiel", "Music Box",
    "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar",
    "Guitar Harmonics", "Acoustic Bass", "Electric Bass (finger)",
    "Electric Bass (pick)", "Fretless Bass", "Slap Bass 1", "Slap Bass 2",
    "Synth Bass 1", "Synth Bass 2", "Violin", "Viola", "Cello",
    "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp",
    "Timpani", "String Ensemble 1", "String Ensemble 2", "Synth Strings 1",
    "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice",
    "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe",
    "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder",
    "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass + lead)", "Pad 1 (new age)",
    "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)",
    "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)",
    "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bag pipe", "Fiddle",
    "Shanai", "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]


class _Render(QThread):
    """Rendering the sequence through the font, off the GUI thread.

    A minute of music is a few million samples per voice; on the GUI
    thread it would lock the dialog for as long as it took, which on a
    long piece is long enough to look broken."""

    done = pyqtSignal(object, object, str)

    def __init__(self, events, resolution, tempo, font):
        super().__init__()
        self.args = (events, resolution, tempo, font)

    def run(self):
        events, resolution, tempo, font = self.args
        try:
            left, right = soundfont.render(events, resolution, tempo,
                                           font=font)
            self.done.emit(left, right, "")
        except Exception as exc:
            self.done.emit(None, None, str(exc))


class MidiExportDialog(QDialog):
    """Pick a SoundFont, hear the export, then save it."""

    def __init__(self, data, at, suggested, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export sequence as MIDI")
        self.suggested = suggested
        self.font = None
        self._render = None
        self._closing = False
        self._wav = None
        self.blob = midi.from_seq(data, at)
        self.resolution, self.tempo = seq.header(data, at)
        self.events = seq.events(data, at)
        self.saved_path = None
        self.saved_audio = None

        self.settings = QSettings(*SETTINGS)

        # -- the font -------------------------------------------------
        self.font_pick = QComboBox()
        self.font_pick.setMinimumWidth(280)
        self.font_pick.setToolTip(
            "A sound set to listen through - .sf2 or .dls. Windows' own "
            "gm.dls is picked by default, which is what every other "
            "program on this machine plays a .mid through.\n\nIt is not "
            "saved into the MIDI - a MIDI file has nowhere to put one - "
            "it only decides what you hear here.")
        self.font_pick.activated.connect(self._font_picked)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)

        self.play_button = QPushButton()
        set_glyph(self.play_button, "play", "Hear the export")
        self.play_button.clicked.connect(self._play)
        self.play_button.setEnabled(False)
        self.stop_button = QPushButton()
        set_glyph(self.stop_button, "stop")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setEnabled(False)

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0.8)
        self.player.positionChanged.connect(self._moved)
        self.player.playbackStateChanged.connect(self._state)
        self._buffer = None

        self.wave = WaveView()
        self.wave.scrubbed.connect(self._scrubbed)

        # -- the channels ---------------------------------------------
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Channel", "Notes", "Program in the game", "Send it out as"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        head = self.table.horizontalHeader()
        for i in range(4):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        head.setStretchLastSection(True)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 160)

        self.also_wav = QCheckBox("Also save what I am hearing as a WAV")
        self.also_wav.setToolTip(
            "Write the SoundFont rendering beside the .mid, so the sound "
            "you picked survives the export even though the MIDI itself "
            "cannot carry it.")
        self.also_wav.setEnabled(False)

        self.status = QLabel()
        self.status.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Save MIDI...")

        top = QHBoxLayout()
        top.addWidget(QLabel("SoundFont"))
        top.addWidget(self.font_pick, 1)
        top.addWidget(browse)
        top.addWidget(self.play_button)
        top.addWidget(self.stop_button)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(mascot.label())
        beside = QVBoxLayout()
        beside.setContentsMargins(0, 0, 0, 0)
        beside.addWidget(self.status)
        beside.addWidget(self.also_wav)
        footer.addLayout(beside, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.wave)
        layout.addWidget(QLabel(
            "<b>What each part will play as.</b> The numbers on the left "
            "are the game's; the ones on the right are what gets written "
            "into the MIDI."))
        layout.addWidget(self.table, 1)
        layout.addLayout(footer)
        layout.addWidget(buttons)
        self.resize(760, 560)

        self._fill_channels()
        self._load_recent()
        self._describe()

    # -- the channel table -------------------------------------------

    def _fill_channels(self):
        """One row per channel the sequence actually uses."""
        from formats.audio import seq_notes

        notes, others = seq_notes.to_notes(self.events)
        counts = {}
        for note in notes:
            counts[note.channel] = counts.get(note.channel, 0) + 1
        programs = {}
        for tick, status, a, _b in others:
            if status & 0xF0 == 0xC0:
                programs.setdefault(status & 0x0F, a)

        self.table.setRowCount(len(counts))
        for row, channel in enumerate(sorted(counts)):
            program = programs.get(channel, 0)
            for column, value in ((0, channel), (1, counts[channel])):
                cell = QTableWidgetItem()
                cell.setData(Qt.ItemDataRole.DisplayRole, value)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, column, cell)
            cell = QTableWidgetItem(f"program {program}")
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 2, cell)

            box = QComboBox()
            box.addItem(f"Leave it at {program}", program)
            for number, name in enumerate(GM_NAMES):
                box.addItem(f"{number}  {name}", number)
            box.setCurrentIndex(0)
            box.currentIndexChanged.connect(self._remapped)
            box.setProperty("channel", channel)
            self.table.setCellWidget(row, 3, box)

    def _remap(self):
        """{channel: program} for every row set to something else."""
        out = {}
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 3)
            if box is None or box.currentIndex() == 0:
                continue
            out[box.property("channel")] = box.currentData()
        return out

    def _remapped(self):
        self._wav = None
        self.also_wav.setEnabled(False)
        self._describe()

    def _mapped_events(self):
        """The event stream with the remap applied."""
        remap = self._remap()
        if not remap:
            return self.events
        out = []
        for delta, status, a, b in self.events:
            if status & 0xF0 == 0xC0 and (status & 0x0F) in remap:
                a = remap[status & 0x0F]
            out.append((delta, status, a, b))
        return out

    # -- soundfonts ---------------------------------------------------

    def _load_recent(self):
        """The list: what this machine already has, then recent picks.

        The system sound set goes first and is chosen by default -
        it is the General MIDI voice every other program on the
        machine plays a .mid through, so it is both the most useful
        thing to compare against and the one answer that needs no
        download."""
        system = soundfont.system_fonts()
        known = {path for _label, path in system}
        recent = self.settings.value(RECENT_FONTS) or []
        if isinstance(recent, str):
            recent = [recent]
        recent = [p for p in recent if os.path.isfile(p) and p not in known]

        self.font_pick.clear()
        self.font_pick.addItem("None - export without listening", None)
        for label, path in system:
            self.font_pick.addItem(label, path)
        for path in recent:
            self.font_pick.addItem(os.path.basename(path), path)

        last = self.settings.value(LAST_FONT)
        wanted = last if last and os.path.isfile(last) else (
            system[0][1] if system else None)
        if wanted:
            index = self.font_pick.findData(wanted)
            if index >= 0:
                self.font_pick.setCurrentIndex(index)
                self._open(wanted)

    def _remember(self, path):
        recent = self.settings.value(RECENT_FONTS) or []
        if isinstance(recent, str):
            recent = [recent]
        recent = [p for p in recent if p != path and os.path.isfile(p)]
        recent.insert(0, path)
        self.settings.setValue(RECENT_FONTS, recent[:MAX_RECENT])
        self.settings.setValue(LAST_FONT, path)

    def _browse(self):
        start = self.settings.value(LAST_FONT) or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a sound set", os.path.dirname(start) if start else "",
            "Sound sets (*.sf2 *.dls);;SoundFont (*.sf2);;"
            "Downloadable sounds (*.dls);;All files (*)")
        if not path:
            return
        if self._open(path):
            self._remember(path)
            self._load_recent()
            index = self.font_pick.findData(path)
            if index >= 0:
                self.font_pick.setCurrentIndex(index)

    def _font_picked(self, _index):
        path = self.font_pick.currentData()
        if path is None:
            self.font = None
            self.play_button.setEnabled(False)
            self._describe()
            return
        if self._open(path):
            self._remember(path)

    def _open(self, path):
        if not soundfont.available():
            self.status.setText(
                "Listening needs numpy, which isn't installed - the MIDI "
                "itself still exports.")
            return False
        try:
            self.font = soundfont.load(path)
        except (OSError, soundfont.SoundFontError, ValueError,
                struct.error) as exc:
            self.font = None
            self.status.setText(f"Couldn't read that sound set: {exc}")
            self.play_button.setEnabled(False)
            return False
        self._wav = None
        self.play_button.setEnabled(True)
        self._describe()
        return True

    def _describe(self):
        if self.font is None:
            self.status.setText(
                "The programs in this sequence point at the game's own "
                "sound bank, not at General MIDI - so opened anywhere else "
                "it plays on whatever those numbers mean there. Choose a "
                "SoundFont to hear what that will be.")
            return
        presets = len(self.font.presets)
        remap = self._remap()
        self.status.setText(
            f"<b>{self.font.title}</b> - {presets} preset(s), "
            f"{len([s for s in self.font.samples if s])} sample(s). Press "
            "play to hear the export."
            + (f" {len(remap)} channel(s) remapped." if remap else "")
            + "<br><span style='color:#8a8f98'>A preview: pitch, looping, "
            "envelopes and level, but no filters or effects.</span>")

    # -- listening ----------------------------------------------------

    def _play(self):
        if self.font is None:
            return
        if self._wav is not None:
            self._start(self._wav)
            return
        self.play_button.setEnabled(False)
        self.status.setText("Rendering through the SoundFont...")
        self._render = _Render(self._mapped_events(), self.resolution,
                               self.tempo, self.font)
        self._render.done.connect(self._rendered)
        self._render.finished.connect(self._render_finished)
        self._render.start()

    def _rendered(self, left, right, note):
        if self._closing:
            return
        self.play_button.setEnabled(True)
        if left is None:
            self.status.setText(f"Could not render that: {note}")
            return
        self._wav = soundfont.to_wav_bytes(left, right)
        self.also_wav.setEnabled(True)
        self.wave.set_envelope(peaks(self._wav), self.font.title)
        self._describe()
        self._start(self._wav)

    def _render_finished(self):
        render, self._render = self._render, None
        if render is not None:
            render.deleteLater()
        if self._closing:
            # Do not let Qt destroy a live QThread.  The rendering code
            # has no safe interruption point inside the SoundFont engine,
            # so finish its current pass, then close the dialog.
            self.reject()

    def _start(self, wav):
        self.player.stop()
        self.player.setSourceDevice(None)
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
        self._buffer = QBuffer(self.player)
        self._buffer.setData(QByteArray(wav))
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self._buffer)
        self.player.play()

    def _stop(self):
        self.player.stop()

    def _state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.stop_button.setEnabled(playing)

    def _moved(self, ms):
        length = self.player.duration()
        self.wave.set_position(ms / length if length > 0 else 0.0)

    def _scrubbed(self, fraction):
        length = self.player.duration()
        if length > 0:
            self.player.setPosition(int(fraction * length))

    # -- saving -------------------------------------------------------

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sequence as MIDI", self.suggested,
            "MIDI file (*.mid);;All files (*)")
        if not path:
            return
        remap = self._remap()
        try:
            # With a remap, rebuilt from the changed stream rather than
            # patched in place: a program change can be running-status
            # packed, and rewriting a byte inside that is how a file
            # ends up subtly wrong.
            blob = (_midi_from_events(self._mapped_events(), self.resolution,
                                      self.tempo)
                    if remap else self.blob)
            with open(path, "wb") as f:
                f.write(blob)
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not write that: {exc}")
            return
        self.saved_path = path
        if self.also_wav.isChecked() and self._wav:
            audio = os.path.splitext(path)[0] + ".wav"
            try:
                with open(audio, "wb") as f:
                    f.write(self._wav)
                self.saved_audio = audio
            except OSError as exc:
                self.status.setText(f"The MIDI saved; the WAV did not: {exc}")
                return
        self.accept()

    def closeEvent(self, event):
        self.player.stop()
        if self._render is not None and self._render.isRunning():
            self._closing = True
            self.status.setText("Finishing the active SoundFont render before closing...")
            self.setEnabled(False)
            event.ignore()
            return
        super().closeEvent(event)


def _midi_from_events(events, resolution, tempo):
    """[(delta, status, a, b)] as a format-0 MIDI file."""
    track = bytearray()
    track += midi._vlq(0) + bytes((0xFF, midi.TEMPO_META, 3))
    track += int(tempo).to_bytes(3, "big")
    for delta, status, a, b in events:
        if status == 0xFF:
            track += midi._vlq(delta) + bytes((0xFF, midi.TEMPO_META, 3))
            track += int(a).to_bytes(3, "big")
            continue
        track += midi._vlq(delta) + bytes((status, a))
        if status & 0xF0 not in (0xC0, 0xD0):
            track += bytes((b,))
    track += midi._vlq(0) + bytes((0xFF, midi.END_META, 0))
    head = struct.pack(">HHH", 0, 1, resolution)
    return midi._chunk(midi.MTHD, head) + midi._chunk(midi.MTRK, bytes(track))

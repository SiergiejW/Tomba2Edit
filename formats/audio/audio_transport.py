"""A list of things to play, and the controls to play them.

All three audio surfaces need the same widget - a list, play/pause/stop,
prev/next, a seek bar, a clock, a volume slider, rename and save - so it
lives here once and is fed differently:

    Music       pieces of BGM.XA and DEMO.XA, plus the CD audio track
    Dialogues   the channels of VOICE.XA
    SFX         the waveforms out of the sound banks in TOMBA2.SND

Everything goes through QMediaPlayer rather than playing raw PCM into an
audio sink: given a WAV in a QBuffer it seeks, reports a duration and
tracks position exactly as it does for a file, so one transport serves
all three. Raw PCM would need all of that written again.

Each row carries a key naming what it is - "BGM.XA:1:2", "0:46" - which
is what a name is stored against, so renaming survives the list being
rebuilt or reordered. The owner keeps the names; this only edits them
and says so.

The list itself is a single-column table by default - Music and
Dialogues use it that way, and it looks exactly like a plain list. SFX
asks for extra columns (`columns=`) to show the index, bank and slot,
length and loop flag alongside the name.

`pitch=True` (SFX only) adds a pitch slider. This is not a cosmetic
effect: the SPU plays a waveform at a pitch by resampling it faster or
slower, exactly what changing playback speed does to already-decoded
PCM, so speeding this up or down is the same knob the original hardware
turns for the games that reuse one sample at several pitches. The range
is deliberately modest - enough to hear what a slightly different pitch
would have sounded like, not a toy for turning a footstep into a
chipmunk.

Sorting is on, which is the reason `wanted` and `save_requested` carry a
KEY rather than a row number: sorting physically moves rows, and a
decode is asynchronous - the row a click meant when it was made is not
promised to still hold that entry by the time a worker thread calls
back. A key travels with its row wherever it sorts to, so the owner's
callback resolving late is only ever wrong if the owner itself indexes
by position instead of by key too, which is why each panel keeps its
own key -> data mapping rather than a plain list indexed by row.
"""
import os

from PyQt6.QtCore import (QBuffer, QByteArray, QEvent, QThread, QTimer,
                          Qt, QUrl, pyqtSignal)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog,
                             QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QSlider, QSpinBox, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from formats.audio import audio_export
from formats.audio.transport_icons import set_glyph
from gui.widgets.waveform import WaveDelegate, WaveView, peaks

KEY =Qt.ItemDataRole.UserRole
DESCRIPTION = Qt.ItemDataRole.UserRole + 1
LOOPS = Qt.ItemDataRole.UserRole + 2

# A narrower default than Stretch would give it: with extra columns
# competing for room (SFX's Index/Bank/Slot/Length/Loop), the name does
# not need to eat every pixel that isn't currently used, and the user
# can still drag it wider - see the Interactive resize mode below.
NAME_COLUMN_WIDTH = 220
# The thumbnail column, when a panel asks for one.
WAVE_COLUMN_WIDTH = 110
# What a data column may shrink or grow to when it is sized to its
# contents. The ceiling is the one that matters: Music's "Instruments"
# column lists every instrument a sequence touches, and left to its
# own devices it is wider than the window.
MIN_DATA_COLUMN = 44
MAX_DATA_COLUMN = 200
MIN_NAME_COLUMN = 120


def clock(ms):
    """Milliseconds as m:ss."""
    if not ms or ms < 0:
        ms = 0
    seconds = int(ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def precise_clock(ms):
    """m:ss.xx for short clips where a whole-second clock stays at 0:00."""
    centiseconds = max(0, int(ms or 0)) // 10
    return (f"{centiseconds // 6000}:"
            f"{(centiseconds // 100) % 60:02d}.{centiseconds % 100:02d}")


class _PeakJob(QThread):
    """One row's audio, fetched and reduced to an envelope.

    One at a time, and only for rows on screen: the fetch is a disc
    read and a decode, which is far too expensive to do for a whole
    list up front and completely affordable for the dozen rows a
    person can actually see."""

    done = pyqtSignal(str, object)

    def __init__(self, key, fetch):
        super().__init__()
        self.key, self.fetch = key, fetch

    def run(self):
        try:
            # The panel hands back the finished preview - an envelope
            # or a Notes - because only it knows whether the row is a
            # piece of audio to be decoded or a sequence to be read.
            self.done.emit(self.key, self.fetch(self.key))
        except Exception:
            # A row that will not decode gets no thumbnail; it is a
            # preview, and the panel says what went wrong when the
            # same row is played.
            self.done.emit(self.key, None)


class AudioTransport(QWidget):
    """A playlist with transport controls, renaming and saving.

    The owner supplies the audio by connecting `wanted`, which carries
    the KEY of the row wanted; it answers with play_url() or
    play_bytes(). That keeps decoding - which can be slow enough to need
    a thread - out of here. Saving works the same way through
    `save_requested`.

    Save buttons (`save_wav`/`save_mp3`) are built here but are plain
    QPushButtons an owner is expected to reparent into its own toolbar
    rather than leave in this widget's own layout - see any of the three
    panels for the pattern. That is also where a bulk "save all" lives,
    since which "all" means is specific to what the panel is browsing."""

    wanted = pyqtSignal(str)
    renamed = pyqtSignal(str, str)          # key, new name ("" clears it)
    save_requested = pyqtSignal(str, str)   # key, path

    def __init__(self, parent=None, columns=None, pitch=False,
                 source="Audio", autoplay_default=False,
                 always_loopable=False, loop_beats_autoplay=False,
                 select_plays=True, autoplay_label="Autoplay",
                 previews=False):
        super().__init__(parent)
        # Which list this is - Music, Dialogues or SFX - so a
        # printed selection says where it came from.
        self.source = source
        self._scrubbing = False
        # Where the cursor was put while nothing was playing. Pressing
        # play then starts there rather than at the beginning, which
        # is what dragging a cursor on a stopped sound is asking for.
        self._start_at = 0.0
        # A freshly installed source can reject a seek until it has
        # announced its duration.  Remember that the chosen point still
        # needs applying, rather than relying on one early setPosition().
        self._start_pending = False
        self._source_ready = False
        # True while this is moving the slider itself. The slider's
        # valueChanged cannot tell a drag from a programmatic move, and
        # without this the transport's own updates counted as the
        # person dragging - setRange() on a new track reset the value,
        # which overwrote the position they had just chosen with zero,
        # a line before it was going to be used.
        self._syncing = False
        self._timeline_text_provider = None
        self._buffer = None
        self._current = -1
        self._looping = False
        self._extra_columns = list(columns or [])
        self._has_pitch = pitch
        # SFX marks only specific rows as loop-worthy - a sample the
        # game itself sustains rather than plays once through - so
        # Loop only ever applies to those. Music has no such thing:
        # any track can be repeated, so Music passes this True and
        # every row qualifies regardless of its own LOOPS flag.
        self._always_loopable = always_loopable
        # SFX wants Autoplay to win while both are checked, so
        # stepping through a list of samples with the arrow keys
        # never gets stuck forever on one that loops. Music wants the
        # opposite - once Loop is on, the playing track keeps
        # repeating until Loop is switched off, Autoplay or not.
        self._loop_beats_autoplay = loop_beats_autoplay
        self._autoplay_default = autoplay_default
        # SFX/Dialogues want selecting a row (a click, an arrow key, or
        # even the list simply being (re)filled and landing on its own
        # first row) to start playing it right away, so browsing a long
        # list previews as you go. Music explicitly does not: the
        # checkbox there only means "when the song I started finishes,
        # move on" - selecting a row is never enough on its own, or
        # opening a disc would start music playing on its own the
        # moment the list is built, before anyone asked for anything.
        self._select_plays = select_plays
        self._autoplay_label = autoplay_label
        # A thumbnail column in front of the name, filled in as rows
        # come into view - see enable_previews().
        self._previews = previews
        self._peak_fetch = None
        self._peak_cache = {}
        self._peak_job = None
        # True while this is setting column widths itself, so its own
        # sectionResized does not read as the person dragging one.
        self._sizing = False
        self._peak_timer = QTimer(self)
        self._peak_timer.setSingleShot(True)
        self._peak_timer.setInterval(30)
        self._peak_timer.timeout.connect(self._next_peak)

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0.8)
        self.player.positionChanged.connect(self._moved)
        self.player.durationChanged.connect(self._sized)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._status_changed)

        # Every list this transport plays from, and the one in use - the
        # last one picked from. See add_list().
        self.lists = []
        self.list = self._make_list(self._extra_columns, source)

        self.play_button = QPushButton()
        set_glyph(self.play_button, "play")
        self.play_button.clicked.connect(self._toggle)
        stop = QPushButton()
        set_glyph(stop, "stop")
        stop.clicked.connect(self.stop)
        previous = QPushButton()
        set_glyph(previous, "previous")
        previous.clicked.connect(lambda: self.step(-1))
        following = QPushButton()
        set_glyph(following, "next")
        following.clicked.connect(lambda: self.step(1))

        rename = QPushButton("Rename")
        rename.setToolTip("Give this entry a name of your own (or press F2). "
                          "Names are saved per disc.")
        rename.clicked.connect(self.rename_current)
        self.autoplay = QCheckBox(self._autoplay_label)
        self.autoplay.setChecked(self._autoplay_default)
        if self._select_plays:
            self.autoplay.setToolTip(
                "Play an entry as soon as it's selected, instead of only "
                "on double-click or Play, and move on to the next one "
                "when it ends."
                + (" On by default." if self._autoplay_default else
                  " Off by default so browsing the list with the arrow "
                  "keys doesn't talk over itself."))
        else:
            self.autoplay.setToolTip(
                "When the song you started finishes, move on and play "
                "the next one - selecting a row never starts it playing "
                "on its own; only double-click, Play, or an entry this "
                "already carries you into does that."
                + (" On by default." if self._autoplay_default else
                  " Off by default."))
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(False)
        if self._loop_beats_autoplay:
            self.loop.setToolTip(
                "Repeat whatever is currently playing instead of playing "
                "it once. Off by default. Takes priority over Autoplay "
                "while both are checked - a looping track keeps looping "
                "rather than handing off to the next one. Unchecking it "
                "lets the current pass finish and then, if Autoplay is "
                "on, moves to the next entry; unchecking it doesn't cut "
                "the loop off mid-playback.")
        else:
            self.loop.setToolTip(
                "Repeat an entry marked as a loop instead of playing it "
                "once. Off by default. Autoplay overrides it while both "
                "are checked - browsing entry to entry would otherwise "
                "never move on from one that loops. Unchecking it stops "
                "a loop already playing, rather than waiting for the "
                "next one you pick.")
        self.loop.toggled.connect(self._loop_toggled)

        self.save_wav = QPushButton("Save selected to WAV...")
        self.save_wav.clicked.connect(lambda: self._save("wav"))
        self.save_mp3 = QPushButton("Save selected to MP3...")
        self.save_mp3.clicked.connect(lambda: self._save("mp3"))
        if not audio_export.have_mp3():
            self.save_mp3.setToolTip(
                "No MP3 encoder found. Install one with "
                "\"pip install lameenc\", or put ffmpeg on PATH.")

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 0)
        self.position.sliderPressed.connect(self._grab)
        self.position.sliderReleased.connect(self._release)
        # Dragging the slider moves the wave cursor as it goes. Not
        # through the player: while a drag is in progress the player
        # has not moved yet, so reading its position would leave the
        # wave cursor sitting still until the mouse came up.
        self.position.valueChanged.connect(self._slider_moved)
        self.time = QLabel("0:00.00 / 0:00.00" if source == "SFX"
                           else "0:00 / 0:00")

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(120)
        self.volume.valueChanged.connect(
            lambda v: self.output.setVolume(v / 100))

        if self._has_pitch:
            # +/-50%, i.e. half speed to one and a half times over -
            # enough range to hear a genuinely different pitch, not so
            # much that "a little up or down" turns into a novelty
            # voice. 0 is the sample's own, unmodified pitch.
            pitch_tip = ("Preview this waveform resampled faster or slower "
                        "- the same thing the SPU does to play one sample "
                        "at several pitches. Purely a preview; nothing is "
                        "written back.")
            self.pitch = QSlider(Qt.Orientation.Horizontal)
            self.pitch.setRange(-50, 50)
            self.pitch.setValue(0)
            self.pitch.setMaximumWidth(120)
            self.pitch.setToolTip(pitch_tip)
            # The spin box's up/down arrows (and typing a number, and the
            # keyboard once it has focus) are what "fine tuning" actually
            # needs - a step of 1% at a time, versus a slider a mouse can
            # only ever be so precise dragging. The two stay in sync with
            # each other; only the spin box's end of that also applies
            # the pitch, since by the time it changes the slider already
            # agrees with it either way.
            self.pitch_box = QSpinBox()
            self.pitch_box.setRange(-50, 50)
            self.pitch_box.setSuffix("%")
            self.pitch_box.setToolTip(pitch_tip)
            self.pitch.valueChanged.connect(self.pitch_box.setValue)
            self.pitch_box.valueChanged.connect(self.pitch.setValue)
            self.pitch_box.valueChanged.connect(self._pitch_changed)

        # The big preview sits directly over the seek bar, and the
        # two are one position: dragging either moves the other, so
        # there is never a cursor on screen that disagrees with the
        # sound.
        self.wave = WaveView()
        self.wave.setToolTip(
            "What is playing, drawn end to end. Click or drag to jump "
            "to a moment.")
        self.wave.scrubbed.connect(self._wave_scrubbed)
        self.timeline_caption = QWidget()
        self.timeline_caption.setStyleSheet("background: #000;")
        caption_layout = QVBoxLayout(self.timeline_caption)
        caption_layout.setContentsMargins(10, 4, 10, 5)
        caption_layout.setSpacing(1)
        self.timeline_area = QLabel()
        self.timeline_area.setStyleSheet("color: #6d6d72; font-style: italic;")
        self.timeline_area.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.timeline_area.setTextFormat(Qt.TextFormat.PlainText)
        self.timeline_text = QLabel()
        self.timeline_text.setWordWrap(True)
        self.timeline_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timeline_text.setTextFormat(Qt.TextFormat.RichText)
        self.timeline_text.setStyleSheet(
            "color: white; font-size: 20px; font-weight: 700;")
        caption_font = self.timeline_text.font()
        caption_font.setBold(True)
        self.timeline_text.setFont(caption_font)
        caption_layout.addWidget(self.timeline_area, 0,
                                 Qt.AlignmentFlag.AlignTop)
        caption_layout.addWidget(self.timeline_text, 1)
        self.timeline_caption.hide()

        # Keep the waveform and seek slider the same width. Their
        # cursors represent the same fraction, so neither gets a clock
        # label consuming horizontal space on only one of those rows.
        self.time.setVisible(source in ("Dialogues", "SFX"))
        seek = QHBoxLayout()
        seek.addWidget(self.position, 1)
        row = QHBoxLayout()
        for button in (previous, self.play_button, stop, following):
            row.addWidget(button)
        if source in ("Dialogues", "SFX"):
            self.time.setMinimumWidth(112 if source == "Dialogues" else 126)
            row.addWidget(self.time)
        row.addStretch(1)
        if self._has_pitch:
            row.addWidget(QLabel("Pitch"))
            row.addWidget(self.pitch)
            row.addWidget(self.pitch_box)
        row.addWidget(QLabel("Volume"))
        row.addWidget(self.volume)
        tools = QHBoxLayout()
        tools.addWidget(rename)
        tools.addWidget(self.autoplay)
        tools.addWidget(self.loop)
        tools.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.timeline_caption)
        layout.addWidget(self.wave)
        layout.addLayout(seek)
        layout.addLayout(row)
        layout.addLayout(tools)

    # --- the previews -------------------------------------------------

    def enable_previews(self, fetch):
        """Fill the thumbnail column from `fetch`.

        `fetch(key)` returns what should be drawn: an envelope from
        waveform.peaks(), or a waveform.Notes for a sequence. It is
        called on a worker thread, one key at a time, so it has to be
        safe to call off the GUI thread - opening its own file handle
        rather than sharing one."""
        self._peak_fetch = fetch
        self._want_peaks()

    def _peaks_for(self, key):
        """What the delegate paints, or None while it is being worked
        out. Never computes: a delegate runs inside paint."""
        held = self._peak_cache.get(key)
        return held or None

    def _want_peaks(self, *_args):
        # Asked of the lists, not of the transport: previews are per
        # list, and Music turns them off for its tracks while leaving
        # them on for its sequences.
        if self._peak_fetch is None:
            return
        if any(table.name_col for table in self.lists):
            self._peak_timer.start()

    def _visible_keys(self, table):
        """The keys of the rows on screen, top to bottom."""
        if table.rowCount() == 0:
            return []
        height = table.viewport().height()
        first = max(0, table.rowAt(0))
        last = table.rowAt(max(0, height - 1))
        if last < 0:
            last = table.rowCount() - 1
        out = []
        for row in range(first, min(last + 2, table.rowCount())):
            item = table.item(row, table.name_col)
            if item is not None:
                out.append(item.data(KEY))
        return out

    def _next_peak(self):
        """Start the next thumbnail that a visible row is missing.

        One at a time, and the next one is started from the last one's
        finished() rather than from its result: a QThread destroyed
        between emitting its result and actually returning from run()
        takes the process down with it, and holding the reference
        until Qt says it has finished is what stops that."""
        if self._peak_fetch is None or self._peak_job is not None:
            return
        for table in self.lists:
            if not table.name_col:
                continue
            for key in self._visible_keys(table):
                if key and key not in self._peak_cache:
                    job = _PeakJob(key, self._peak_fetch)
                    job.done.connect(self._got_peak)
                    job.finished.connect(self._peak_finished)
                    self._peak_job = job
                    job.start()
                    return

    def _peak_finished(self):
        job, self._peak_job = self._peak_job, None
        if job is not None:
            job.deleteLater()
        self._peak_timer.start()

    def _got_peak(self, key, preview):
        # False rather than None for "tried and there is nothing", so
        # the row is not asked about again on every scroll.
        self._peak_cache[key] = preview or False
        for table in self.lists:
            if table.name_col:
                table.viewport().update()

    # --- the big preview ----------------------------------------------

    def show_wave(self, wav, caption=""):
        """Draw `wav`'s envelope in the big view."""
        self.wave.set_envelope(peaks(wav) if wav else None, caption)

    def show_sequence(self, notes, span, caption=""):
        """Draw a sequence's notes there instead - see WaveView."""
        self.wave.set_sequence(notes, span, caption)

    def clear_wave(self):
        self.wave.clear()

    def set_timeline_text_provider(self, provider):
        """Show context text for the current point in the waveform.

        Dialogues uses this for its TXTD line; other audio surfaces leave
        it unset and retain the compact waveform-only layout.
        """
        self._timeline_text_provider = provider
        self.timeline_caption.setVisible(provider is not None)
        self._update_timeline_text(self.wave.position)

    def _update_timeline_text(self, fraction):
        if self._timeline_text_provider is None:
            return
        try:
            value = self._timeline_text_provider(fraction)
            area, text = value if isinstance(value, tuple) else ("", value)
            area, text = area or "", text or ""
        except Exception:
            area, text = "", ""
        if area != self.timeline_area.text():
            self.timeline_area.setText(area)
        if text != self.timeline_text.text():
            self.timeline_text.setText(text)

    def stop_previews(self):
        """Let a running thumbnail finish before anything goes away.

        Called when the widget is closed: the worker holds a callable
        belonging to the panel, and the panel must not be torn down
        underneath it."""
        self._peak_timer.stop()
        self._peak_fetch = None
        job, self._peak_job = self._peak_job, None
        if job is not None and job.isRunning():
            job.wait(5000)

    def _wave_scrubbed(self, fraction):
        """The big view was dragged; the sound follows it."""
        self._start_at = fraction
        # QMediaPlayer reports its position asynchronously.  Update the
        # dialogue caption here as well, so it tracks the cursor while the
        # waveform is being dragged rather than a callback later.
        self._update_timeline_text(fraction)
        self._start_pending = self.player.playbackState() == \
            QMediaPlayer.PlaybackState.StoppedState
        length = self.player.duration()
        if length > 0:
            self.player.setPosition(int(fraction * length))

    # --- the list -----------------------------------------------------

    def _make_list(self, columns, source, previews=None):
        # The thumbnail goes in front of the name, so everything that
        # reaches for "the row's item" asks table.name_col rather than
        # assuming zero. Per list rather than per transport: Music has
        # two, and what makes a good thumbnail differs between them.
        lead = 1 if (self._previews if previews is None else previews) else 0
        table = QTableWidget(0, 1 + lead + len(columns))
        table.columns, table.source = list(columns), source
        table.name_col = lead
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)
        table.setHorizontalHeaderLabels(
            (["Wave"] if table.name_col else []) + ["Name"] + table.columns)
        # Interactive on every column - a person can drag any of them -
        # but the name starts at a fixed, narrower width rather than
        # Stretch's whole-remaining-space default.
        header = table.horizontalHeader()
        for i in range(table.columnCount()):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        if table.name_col:
            table.setColumnWidth(0, WAVE_COLUMN_WIDTH)
        table.setColumnWidth(table.name_col, NAME_COLUMN_WIDTH)
        # NOT stretchLastSection: with seven columns that pushed the
        # early ones off the left of the pane while the last one grew
        # to fill whatever was left. The slack goes to Name instead,
        # and the data columns are sized to what is in them - see
        # _fit_columns.
        header.setStretchLastSection(False)
        table.auto_columns = True
        header.sectionResized.connect(
            lambda col, *_a, t=table: self._column_dragged(t, col))
        table.cellClicked.connect(lambda _row, _col, t=table: self._use(t))
        table.cellDoubleClicked.connect(
            lambda row, _col, t=table: self._play_from(t, row))
        table.currentCellChanged.connect(
            lambda row, col, previous, previous_col, t=table:
            self._maybe_autoplay(t, row, col, previous, previous_col))
        # F2 renames. Deliberately not SelectedClicked, which would start
        # an edit whenever a chosen row is clicked again.
        table.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        table.itemChanged.connect(self._item_changed)
        # Return plays the highlighted row. Browsing with the arrow
        # keys and then having to reach for the mouse to hear anything
        # is the long way round, and it is what a list of sounds is
        # expected to do. An event filter rather than a subclass: the
        # same handful of lines would otherwise have to be repeated for
        # every table this transport is given.
        table.installEventFilter(self)
        if table.name_col:
            table.setItemDelegateForColumn(
                0, WaveDelegate(self._peaks_for, KEY, table))
            # Thumbnails are worked out for the rows actually on
            # screen, so scrolling is what asks for more of them.
            table.verticalScrollBar().valueChanged.connect(
                self._want_peaks)
        self.lists.append(table)
        return table

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and watched in self.lists:
            # Column widths are a layout choice, not a response to every
            # window resize.  Fit them once when entries arrive, then leave
            # their positions and widths alone just as if the user had
            # dragged the headers by hand.
            self._want_peaks()
            return False
        if (event.type() == QEvent.Type.KeyPress
                and watched in self.lists
                # Not while a name is being typed in: Return there
                # means "finish the rename", and the editor is a child
                # of the table, so the table never sees that key.
                and watched.state() != QAbstractItemView.State.EditingState
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            row = watched.currentRow()
            if row >= 0:
                self._play_from(watched, row)
                return True
        return super().eventFilter(watched, event)

    def column_tip(self, name, text, table=None):
        """Explain one of the owner's own columns in its header."""
        table = table or self.lists[0]
        if name not in table.columns:
            return
        item = table.horizontalHeaderItem(
            table.name_col + 1 + table.columns.index(name))
        if item is not None:
            item.setToolTip(text)

    def add_list(self, columns=None, source="Audio", previews=None):
        """Another list playing through this same player, for the owner to
        lay out beside the first. The controls serve whichever list was
        picked from last; keys must not repeat across lists."""
        return self._make_list(list(columns or []), source, previews)

    def _column_dragged(self, table, column=None):
        """A column the person set by hand stops being managed.

        Every column stays draggable - the widths here are a starting
        point, not a policy - so the moment one is dragged this stops
        recomputing them and undoing the drag."""
        if (getattr(table, "fill_last_column", False)
                and column == table.columnCount() - 1):
            return
        if not self._sizing:
            table.auto_columns = False

    def _fit_columns(self, table):
        """Size the data columns to their contents, Name takes the rest.

        Called when a list is filled and whenever it is resized, so a
        narrow pane shows narrow columns rather than a scroll bar."""
        # Re-entrancy is not a nicety here: setting a column width can
        # resize the viewport, the viewport's resize comes back through
        # the event filter, and that called this again - straight down
        # into a stack overflow with no Python traceback to show for it.
        if self._sizing:
            return
        if not getattr(table, "auto_columns", True) or table.columnCount() < 2:
            return
        self._sizing = True
        try:
            if getattr(table, "fill_last_column", False):
                # Dialogues keeps its numeric fields narrow and lets
                # "Heard in" use whatever width the window has left.
                # The header stretches that last section on later
                # window resizes, without moving hand-sized columns.
                for col in range(table.name_col + 1,
                                 table.columnCount() - 1):
                    table.resizeColumnToContents(col)
                    table.setColumnWidth(
                        col, max(55, min(table.columnWidth(col) + 8, 150)))
                table.resizeColumnToContents(table.name_col)
                table.setColumnWidth(
                    table.name_col,
                    max(140, min(table.columnWidth(table.name_col) + 12,
                                 260)))
                table.setColumnWidth(table.columnCount() - 1, 220)
                return
            for col in range(table.name_col + 1, table.columnCount()):
                table.resizeColumnToContents(col)
                width = table.columnWidth(col) + 10
                table.setColumnWidth(col, max(MIN_DATA_COLUMN,
                                              min(width, MAX_DATA_COLUMN)))
            if table.name_col:
                table.setColumnWidth(0, WAVE_COLUMN_WIDTH)
            pane = table.viewport().width() - 2
            data = list(range(table.name_col + 1, table.columnCount()))
            used = sum(table.columnWidth(c) for c in range(table.columnCount())
                       if c != table.name_col)
            table.setColumnWidth(table.name_col,
                                 max(MIN_NAME_COLUMN, pane - used))

            # Name has already given up everything it can and the data
            # columns still run off the edge - seven of them do, in a
            # narrow pane. Take the excess off them in proportion,
            # widest first, rather than letting the pane scroll: a
            # column nobody can see is worse than a narrow one.
            over = (used + table.columnWidth(table.name_col)) - pane
            while over > 0 and data:
                room = sum(table.columnWidth(c) - MIN_DATA_COLUMN
                           for c in data)
                if room <= 0:
                    break
                taken = 0
                for c in data:
                    spare = table.columnWidth(c) - MIN_DATA_COLUMN
                    if spare <= 0:
                        continue
                    share = min(spare, max(1, round(over * spare / room)))
                    table.setColumnWidth(c, table.columnWidth(c) - share)
                    taken += share
                if not taken:
                    break
                over -= taken
        finally:
            self._sizing = False

    def _use(self, table):
        if table is self.list:
            return
        self.list = table
        # Two lists, one player. A row left highlighted in the other one
        # reads as a second, half-lit selection - Qt draws an unfocused
        # selection in a paler colour - and worse, it is ambiguous which
        # one the buttons are about. Exactly one row is shown as chosen.
        for other in self.lists:
            if other is not table:
                other.blockSignals(True)
                other.clearSelection()
                other.blockSignals(False)

    def key_in(self, table):
        """The key of `table`'s current row, whichever list is in use.

        current_key() answers for the list the controls are pointed at,
        which is the right question for playing and the wrong one for
        "what is selected over there" - an owner with two lists needs to
        ask about one of them by name."""
        row = table.currentRow()
        item = table.item(row, table.name_col) if row >= 0 else None
        return item.data(KEY) if item is not None else None

    def _play_from(self, table, row):
        self._use(table)
        self.play_row(row)

    def set_entries(self, entries, names=None, table=None):
        """Fill the list.

        Each entry is (key, description), (key, description, values), or
        (key, description, values, loops) - `values` has one string per
        extra column, and `loops` (default False) marks a row that
        should repeat rather than play once through, for SFX's benefit.
        The key names the audio and is what a name is stored against;
        the description is what to show when it has no name.

        Sorting is turned off while the table is rebuilt: it applies to
        every insertion otherwise, which is pointless work here and
        fights the row-by-row fill besides. `table` is a list from
        add_list(); the first list by default."""
        table = table or self.lists[0]
        names = names or {}
        if table is self.list:
            self.stop()
        table.setSortingEnabled(False)
        table.blockSignals(True)
        table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            key, description, *rest = entry
            values = rest[0] if rest else ()
            loops = rest[1] if len(rest) > 1 else False
            item = QTableWidgetItem()
            item.setData(KEY, key)
            item.setData(DESCRIPTION, description)
            item.setData(LOOPS, bool(loops))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._show(item, names.get(key, ""))
            table.setItem(row, table.name_col, item)
            if table.name_col:
                # The thumbnail cell needs an item of its own, carrying
                # the same key: a delegate is handed its own index and
                # has no way to reach along the row to the name.
                thumb = QTableWidgetItem()
                thumb.setData(KEY, key)
                thumb.setFlags(Qt.ItemFlag.ItemIsEnabled)
                table.setItem(row, 0, thumb)
            for col, value in enumerate(values, start=table.name_col + 1):
                cell = QTableWidgetItem()
                cell.setData(Qt.ItemDataRole.DisplayRole, value)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, cell)
        table.blockSignals(False)
        table.setSortingEnabled(True)
        self._peak_cache.clear()
        self._want_peaks()
        self._fit_columns(table)
        if table.columns:
            # The first of the caller's own columns is "Index" - the
            # disc's own order, and the one a freshly opened list
            # should read in regardless of however the table was last
            # left sorted.
            table.sortItems(table.name_col + 1, Qt.SortOrder.AscendingOrder)
        if entries:
            table.setCurrentCell(0, table.name_col)

    def apply_names(self, names, table=None):
        """Redraw every row against a fresh set of names."""
        table = table or self.lists[0]
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, table.name_col)
            self._show(item, names.get(item.data(KEY), ""))
        table.blockSignals(False)

    @staticmethod
    def _show(item, name):
        """A named row shows its name; an unnamed one shows what it is."""
        description = item.data(DESCRIPTION) or ""
        item.setText(name or description)
        item.setToolTip(description if name else "")

    def _row_for_key(self, key):
        """The row a key is currently sitting at - not assumed stable,
        since sorting moves rows around underneath it."""
        for row in range(self.list.rowCount()):
            item = self.list.item(row, self.list.name_col)
            if item is not None and item.data(KEY) == key:
                return row
        return -1

    def key_at(self, row):
        item = self.list.item(row, self.list.name_col)
        return item.data(KEY) if item is not None else None

    def name_at(self, row):
        """The row's own name, or "" when it is showing its description."""
        item = self.list.item(row, self.list.name_col)
        if item is None:
            return ""
        text = item.text()
        return "" if text == item.data(DESCRIPTION) else text

    def current_row(self):
        return self.list.currentRow()

    def current_key(self):
        return self.key_at(self.current_row())

    def set_label(self, row, description):
        """Replace a row's description, keeping any name it has."""
        item = self.list.item(row, self.list.name_col)
        if item is None:
            return
        name = self.name_at(row)
        self.list.blockSignals(True)
        item.setData(DESCRIPTION, description)
        self._show(item, name)
        self.list.blockSignals(False)

    # --- renaming -----------------------------------------------------

    def rename_current(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.editItem(self.list.item(row, self.list.name_col))

    def _item_changed(self, item):
        """An edit finished: tell the owner, and fall back to the
        description when the name has been cleared.

        Extra columns are flagged non-editable, but guard the column
        anyway - nothing but the name cell should ever reach here.

        The name is not always column zero: a list with a thumbnail has
        it at one, and comparing against zero there threw every rename
        away silently - the typed name never reached the store, so it
        was gone again the next time the list was built."""
        table = item.tableWidget()
        if item.column() != getattr(table, "name_col", 0):
            return
        key = item.data(KEY)
        name = item.text().strip()
        if name == item.data(DESCRIPTION):
            name = ""
        table.blockSignals(True)
        self._show(item, name)
        table.blockSignals(False)
        if key:
            self.renamed.emit(key, name)

    # --- saving -------------------------------------------------------

    def _save(self, suffix):
        row = self.list.currentRow()
        if row < 0:
            return
        item = self.list.item(row, self.list.name_col)
        key = item.data(KEY)
        stem = audio_export.safe_name(
            self.name_at(row) or item.data(DESCRIPTION) or "audio")
        chosen, _ = QFileDialog.getSaveFileName(
            self, f"Save as {suffix.upper()}",
            f"{stem}.{suffix}",
            f"{suffix.upper()} audio (*.{suffix})")
        if not chosen:
            return
        if os.path.splitext(chosen)[1].lower() != f".{suffix}":
            chosen += f".{suffix}"
        self.save_requested.emit(key, chosen)

    # --- playing ------------------------------------------------------

    def play_row(self, row):
        """Ask the owner for row `row`; it calls back with the audio."""
        if 0 <= row < self.list.rowCount():
            key = self.key_at(row)
            self._current = row
            self.list.setCurrentCell(row, self.list.name_col)
            if key:
                self.wanted.emit(key)

    def play_key(self, key):
        """Select and request a row by key, whichever list it sits in."""
        for table in self.lists:
            self._use(table)
            row = self._row_for_key(key)
            if row >= 0:
                self.play_row(row)
                return

    def play_url(self, url):
        self.stop()
        self._looping = False
        self.player.setSource(QUrl(url) if isinstance(url, str) else url)
        self.player.play()

    def play_bytes(self, data):
        """Play a file held in memory - a WAV built from decoded audio.

        The buffer is kept on the instance: the player reads from it for
        as long as it plays, and letting it go collects it mid-play.

        Loops if the currently selected row is loop-eligible (always_
        loopable, or marked as one - a sound effect the game holds a
        button down to sustain rather than one that plays once through)
        and the Loop checkbox agrees. Whether Autoplay also being
        checked cancels that depends on loop_beats_autoplay: SFX wants
        Autoplay to win, so stepping through a list of samples with the
        arrow keys never gets stuck forever on one that loops; Music
        wants Loop to win, so a looping track keeps repeating until
        Loop is switched off rather than handing off on its own. This
        reads _current rather than taking a "should it loop" argument
        because play_bytes is always answering the most recent
        play_row/play_key, and that row already knows."""
        self.stop()
        current = self.list.item(self._current, self.list.name_col) if self._current >= 0 else None
        self._looping = self._row_loops(current) and self.loop.isChecked()
        if not self._loop_beats_autoplay:
            self._looping = self._looping and not self.autoplay.isChecked()
        self.player.setLoops(QMediaPlayer.Loops.Infinite if self._looping else 1)
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(data))
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self._source_ready = False
        self.player.setSourceDevice(self._buffer)
        self._start_pending = self._start_at > 0
        self.player.play()
        if self._has_pitch:
            self.player.setPlaybackRate(1 + self.pitch.value() / 100)

    def stop(self):
        self.player.stop()
        self._looping = False
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None

    def step(self, by):
        row = self.list.currentRow() + by
        if 0 <= row < self.list.rowCount():
            self.play_row(row)

    def _toggle(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        else:
            self.play_row(max(self.list.currentRow(), 0))

    # --- signals ------------------------------------------------------

    def _maybe_autoplay(self, table, row, _col, previous_row, _previous_col):
        """Selecting a different row plays it, but only where that's
        what this checkbox is for (select_plays - off for Music, see
        __init__) and only with it checked - and only for a genuinely
        new row: play_row() itself moves the current cell to where it
        already is, which would otherwise retrigger this and restart
        the same row it's mid-answering.

        Without select_plays, this still just prints the selection - a
        row landing current because the list was (re)built, such as
        the moment a disc is opened, must never start audio playing on
        its own, which is exactly what selecting row 0 there would
        otherwise do. A list other than the one in use only takes over
        when the keyboard is in it - refilling it must not."""
        if table is not self.list:
            if not table.hasFocus():
                return
            self._use(table)
        if row >= 0 and row != previous_row:
            # A different entry starts at its own beginning; the
            # cursor position belonged to the one before it.
            self._start_at = 0.0
            self._start_pending = False
            if self.source == "Dialogues":
                # The caption provider reads the selected channel. Keep
                # old channel audio and its waveform from being shown
                # under the new row's TXTD text while changing rows.
                self.stop()
                self.clear_wave()
                self.position.setRange(0, 0)
                total = "0:00"
                if "Length" in self.list.columns:
                    column = (self.list.name_col + 1
                              + self.list.columns.index("Length"))
                    cell = self.list.item(row, column)
                    if cell is not None:
                        total = cell.text()
                self.time.setText(f"0:00 / {total}")
                self._update_timeline_text(0.0)
            self._print_selection(row)
        if (row >= 0 and row != previous_row and self._select_plays
                and self.autoplay.isChecked()):
            self.play_row(row)

    def _print_selection(self, row):
        """Name the picked entry by its key rather than by its label.

        The label is whatever the user renamed it to; the key is what
        the disc calls it, and is what a name is stored against - so it
        is the half worth printing. Music, Dialogues and SFX all come
        through here, so `source` says which."""
        item = self.list.item(row, self.list.name_col)
        if item is None:
            return
        extras = []
        # The caller's own columns start after the name - which is not
        # column 0 when there is a thumbnail in front of it, so the
        # heading for a cell is looked up from the name column rather
        # than from the cell's absolute position.
        first = self.list.name_col + 1
        for column in range(first, self.list.columnCount()):
            cell = self.list.item(row, column)
            if cell is not None and column - first < len(self.list.columns):
                extras.append(f"{self.list.columns[column - first]} "
                              f"{cell.data(Qt.ItemDataRole.DisplayRole)}")
        shown = item.text()
        description = item.data(DESCRIPTION)
        print(f"selected: {self.list.source}  key {item.data(KEY)}  {description}"
              + (f"  named '{shown}'" if shown and shown != description else "")
              + ("  " + "  ".join(extras) if extras else ""))

    def _row_loops(self, item):
        """Whether `item` is loop-eligible at all - every row, for a
        transport built always_loopable (Music), or only rows the
        owner explicitly marked (SFX's own per-row LOOPS flag)."""
        return bool(item and (self._always_loopable or item.data(LOOPS)))

    def _loop_toggled(self, checked):
        """Applies live to whatever is already playing, not just to the
        next thing picked - unchecking Loop mid-loop should stop it
        right there (letting the current pass finish rather than
        cutting it off - see setLoops), not wait for the entry to be
        reselected. Only has anything to do when the current row is
        loop-eligible, and - unless loop_beats_autoplay - only when
        Autoplay isn't also on; otherwise nothing here was looping
        regardless of this checkbox."""
        current = self.list.item(self._current, self.list.name_col) if self._current >= 0 else None
        if not self._row_loops(current):
            return
        if not self._loop_beats_autoplay and self.autoplay.isChecked():
            return
        self._looping = checked
        self.player.setLoops(QMediaPlayer.Loops.Infinite if checked else 1)

    def _pitch_changed(self, value):
        """Live while something is already playing, not just on the next
        play_bytes() - dragging the slider or nudging the spin box
        mid-clip is the whole point of them, rather than a setting that
        only takes effect on the next play."""
        self.player.setPlaybackRate(1 + value / 100)

    def _slider_moved(self, ms):
        length = self.position.maximum()
        fraction = ms / length if length > 0 else 0.0
        self._set_time(ms, length)
        if not self._syncing:
            self._start_at = fraction
            self._start_pending = self.player.playbackState() == \
                QMediaPlayer.PlaybackState.StoppedState
        self.wave.set_position(fraction)
        self._update_timeline_text(fraction)

    def _grab(self):
        self._scrubbing = True

    def _release(self):
        self._scrubbing = False
        self.player.setPosition(self.position.value())

    def _moved(self, ms):
        if not self._scrubbing:
            self._syncing = True
            try:
                self.position.setValue(ms)
            finally:
                self._syncing = False
        length = self.player.duration()
        self._set_time(ms, length)
        # The big view's cursor is the same position as the slider's,
        # so it is driven from the same place rather than from a timer
        # of its own that could drift away from it.
        if not self._scrubbing:
            self.wave.set_position(ms / length if length > 0 else 0.0)
            self._update_timeline_text(ms / length if length > 0 else 0.0)

    def _apply_start(self):
        if (not self._source_ready or not self._start_pending
                or self._start_at <= 0):
            return
        length = self.player.duration()
        if length > 0:
            self.player.setPosition(int(self._start_at * length))
            self._start_pending = False

    def _sized(self, ms):
        self._syncing = True
        try:
            self.position.setRange(0, ms)
        finally:
            self._syncing = False
        self._apply_start()
        self._set_time(self.player.position(), ms)

    def _set_time(self, position, duration):
        fmt = precise_clock if self.source == "SFX" else clock
        shown = f"{fmt(position)} / {fmt(duration)}"
        self.time.setText(shown)
        self.wave.set_clock(shown)

    def _state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        set_glyph(self.play_button, "pause" if playing else "play")

    def _status_changed(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            # setSourceDevice() discards its old source asynchronously.
            # Until this point duration() can still describe the sound
            # just stopped, so a seek here is the first safe one.
            self._source_ready = True
            self._apply_start()
        # Advancing to the next row when one finishes is Autoplay's job
        # too, not just playing a row the moment it's selected - with it
        # off, a track (or a short SFX clip that ends almost right away)
        # is meant to just stop, not silently carry on through the list.
        # A looping track's own repeats must also never be read as it
        # having finished.
        if (status == QMediaPlayer.MediaStatus.EndOfMedia
                and not self._looping and self.autoplay.isChecked()):
            self.step(1)

    def closeEvent(self, event):
        self.stop()
        self.stop_previews()
        super().closeEvent(event)

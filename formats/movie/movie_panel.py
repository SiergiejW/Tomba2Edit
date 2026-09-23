"""The Movies tab: the three STR movies, played and exported.

LOGO.STR, OP.STR and END.STR are the whole of it - the Whoopee Camp
logo, the opening, and the ending. They are picked from the list on the
left and shown on the right, with a timeline underneath.

Two things about STR decide how this is built.

Every frame stands alone. There is no motion compensation in MDEC, so
frame 900 costs exactly one frame's work to decode and dragging the
timeline is as cheap anywhere as it is at the start. That is why the
timeline is a plain frame slider and not a "seek to the nearest
keyframe" affair.

A frame costs about 20ms to decode, which is most of a 30fps frame's
budget, so playback cannot decode on the GUI thread and hope. A worker
thread runs ahead of the playhead filling a cache, and playback shows
whatever has arrived. Where the movie has audio, the soundtrack is what
keeps time - the picture follows the player's own position, so it stays
in step even when a decode falls behind - and where it has none (a
movie read out of an extracted folder), a clock stands in.
"""
import os
from contextlib import contextmanager

import numpy as np
from PyQt6.QtCore import (QBuffer, QByteArray, QElapsedTimer, QMutex,
                          QMutexLocker, QThread, QTimer, QWaitCondition, Qt,
                          pyqtSignal)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                             QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                             QMessageBox, QPushButton, QSlider, QSplitter,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from formats.audio import audio_export
from formats.movie import psxstr
from gui.widgets import mascot
from gui.widgets import panel_title
from formats.movie import movie_export
from formats.audio.transport_icons import set_glyph
from formats.movie.movie_screen import MovieScreen, clock

# How far ahead of the playhead the worker decodes, and how many frames
# are kept at all. 300 frames of 320x240 RGB is about 70 MB, which buys
# the whole of LOGO and a comfortable run of the other two.
LOOK_AHEAD = 90
CACHE_LIMIT = 300


class _Decoder(QThread):
    """Decodes the frames the panel asks for, one at a time.

    Asked for a frame it hands that one back and then carries on with
    the ones after it, which is what fills the cache ahead of playback.
    The panel says what to work on next through want(); the thread
    sleeps when there is nothing.

    Both signals name the movie they decoded, and that is not
    decoration. They are queued, so a frame emitted just before the
    user picked a different movie is delivered after the switch has
    already happened - and without something to check it against, the
    panel files the movie it just left under the new one's frame
    number and shows it."""

    ready = pyqtSignal(object, int, object)
    failed = pyqtSignal(object, int, str)

    def __init__(self, movie, parent=None):
        super().__init__(parent)
        self.movie = movie
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._wanted = None
        self._stopping = False

    def want(self, index):
        """Decode `index` next. None puts the thread back to sleep."""
        with QMutexLocker(self._mutex):
            self._wanted = index
            self._wake.wakeAll()

    def stop(self):
        with QMutexLocker(self._mutex):
            self._stopping = True
            self._wake.wakeAll()
        self.wait(5000)

    def run(self):
        while True:
            with QMutexLocker(self._mutex):
                while self._wanted is None and not self._stopping:
                    self._wake.wait(self._mutex)
                if self._stopping:
                    return
                index = self._wanted
                self._wanted = None
            try:
                self.ready.emit(self.movie, index, self.movie.frame(index))
            except Exception as exc:            # a frame that will not decode
                self.failed.emit(self.movie, index, str(exc))


class MoviePanel(QWidget):
    """Pick a movie, watch it, write it out."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.movies = []
        self.movie = None
        self._decoder = None
        self._cache = {}
        self._current = 0
        self._playing = False
        self._scrubbing = False
        self._wav = None
        self._buffer = None
        self._clock = QElapsedTimer()
        self._clock_frame = 0

        # --- the list of movies -----------------------------------------
        self.list = QTableWidget(0, 5)
        self.list.setHorizontalHeaderLabels(
            ["Movie", "Frames", "Length", "Size", "Sound"])
        self.list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.verticalHeader().setVisible(False)
        for column in range(self.list.columnCount()):
            self.list.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Interactive)
        self.list.setColumnWidth(0, 100)
        self.list.horizontalHeader().setStretchLastSection(True)
        self.list.currentCellChanged.connect(
            lambda row, *_rest: self._chose(row))

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(panel_title.make_panel_title(
            "The movies in MOVIE - double-click to play"))
        left_layout.addWidget(self.list, 1)

        # --- the picture and the timeline -------------------------------
        self.screen = MovieScreen()
        self.screen.double_clicked.connect(self._toggle)
        self.list.cellDoubleClicked.connect(lambda *_a: self.play())

        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.setToolTip(
            "Every frame of an STR stands on its own, so this seeks "
            "anywhere for the cost of one frame")
        self.timeline.sliderPressed.connect(self._grab)
        self.timeline.sliderReleased.connect(self._release)
        self.timeline.valueChanged.connect(self._slid)
        self.position = QLabel("-")
        self.position.setMinimumWidth(190)

        self.play_button = QPushButton()
        set_glyph(self.play_button, "play")
        self.play_button.clicked.connect(self._toggle)
        stop = QPushButton()
        set_glyph(stop, "stop")
        stop.clicked.connect(self.stop)
        first = QPushButton()
        set_glyph(first, "first")
        first.clicked.connect(lambda: self.show_frame(0))
        previous = QPushButton()
        set_glyph(previous, "step_back")
        previous.clicked.connect(lambda: self.step(-1))
        following = QPushButton()
        set_glyph(following, "step_forward")
        following.clicked.connect(lambda: self.step(1))
        last = QPushButton()
        set_glyph(last, "last")
        last.clicked.connect(
            lambda: self.show_frame(len(self.movie.frames) - 1)
            if self.movie else None)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(110)
        self.volume.valueChanged.connect(
            lambda value: self.output.setVolume(value / 100))

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0.8)
        self.player.mediaStatusChanged.connect(self._audio_status)

        # Ticks faster than the movie's own frame rate so a frame is
        # never shown a whole frame late; showing the same one twice
        # costs nothing.
        self._ticker = QTimer(self)
        self._ticker.setInterval(10)
        self._ticker.timeout.connect(self._tick)

        # --- exporting ---------------------------------------------------
        self.save_frame = QPushButton("Save frame...")
        self.save_frame.setToolTip("Write the frame on screen as a PNG")
        self.save_frame.clicked.connect(self._save_frame)
        self.save_video = QPushButton("Save video...")
        self.save_video.setToolTip(
            "Write the whole movie, sound and all, as an MP4, MKV or AVI")
        self.save_video.clicked.connect(self._save_video)
        self.save_frames = QPushButton("Save all frames...")
        self.save_frames.setToolTip(
            "Write every frame into a folder as numbered PNGs")
        self.save_frames.clicked.connect(self._save_frames)
        self.save_audio = QPushButton("Save sound...")
        self.save_audio.setToolTip(
            "Write the movie's soundtrack as a WAV or MP3")
        self.save_audio.clicked.connect(self._save_audio)
        self.save_str = QPushButton("Save .STR...")
        self.save_str.setToolTip(
            "Write the movie's own sectors out unchanged - a 2352-byte "
            "STR when the disc was opened as a BIN, which players and "
            "emulators read directly")
        self.save_str.clicked.connect(self._save_str)

        self.pick = QPushButton("Open BIN or folder...")
        self.pick.setToolTip(
            "Only needed for a disc opened as a folder without a MOVIE "
            "beside it - opening a BIN normally sets this up on its own")
        self.pick.clicked.connect(self._browse)

        self.range_box = QComboBox()
        self.range_box.addItems(["Whole movie", "From here on",
                                 "This frame only"])
        self.range_box.setToolTip(
            "How much of the movie the video and frame exports write")

        self.status = panel_title.make_info_label(
            "No disc open. Open the disc's data track to get the movies "
            "with their sound; an extracted MOVIE folder gives the "
            "picture only.")

        transport = QHBoxLayout()
        for button in (first, previous, self.play_button, stop,
                       following, last):
            transport.addWidget(button)
        transport.addSpacing(12)
        transport.addWidget(self.position)
        transport.addStretch(1)
        transport.addWidget(QLabel("Volume"))
        transport.addWidget(self.volume)

        exports = QHBoxLayout()
        exports.addWidget(QLabel("Export"))
        exports.addWidget(self.range_box)
        for button in (self.save_frame, self.save_frames, self.save_video,
                       self.save_audio, self.save_str):
            exports.addWidget(button)
        exports.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.screen, 1)
        right_layout.addWidget(self.timeline)
        right_layout.addLayout(transport)
        right_layout.addLayout(exports)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([350, 900])

        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        layout.addWidget(mascot.beside(self.status))
        self._enable(False)

    # --- opening -------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the disc's data track", "",
            "Disc track (*.bin *.img);;All files (*)")
        if not path:
            folder = QFileDialog.getExistingDirectory(
                self, "...or the folder the disc was extracted into")
            path = folder or None
        if path:
            self.set_source(path)

    def set_source(self, path):
        """List the movies in a disc track, or in an extracted folder.

        Called when a disc is opened, so it must not throw: a disc with
        no movies on it, or one whose MOVIE folder was not extracted,
        just leaves the tab empty and says why."""
        self.stop()
        self.movie = None
        self._drop_decoder()
        self._cache.clear()
        self.list.setRowCount(0)
        self.screen.show_message("No movie selected")
        self._enable(False)
        self.source = path
        try:
            self.movies = psxstr.find(path) if path else []
        except Exception as exc:
            self.movies = []
            panel_title.set_info(self.status,
                                 f"Could not read the movies: {exc}")
            return
        if not self.movies:
            panel_title.set_info(self.status, (
                f"No STR movies in {os.path.basename(path) or path}. They "
                "live in the disc's MOVIE folder - open the bin/cue data "
                "track, or a folder with MOVIE in it.") if path else
                "No disc open.")
            return

        self.list.setRowCount(len(self.movies))
        for row, movie in enumerate(self.movies):
            sound = (f"{movie.rate} Hz "
                     f"{'stereo' if movie.channels == 2 else 'mono'}"
                     if movie.has_audio else "not in a folder copy")
            values = (movie.name, str(len(movie.frames)),
                      clock(movie.duration),
                      f"{movie.width}x{movie.height}",
                      sound)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column and column != 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                self.list.setItem(row, column, item)
        silent = [m.name for m in self.movies if not m.has_audio]
        panel_title.set_info(self.status, (
            f"{len(self.movies)} movie(s) in "
            f"{os.path.basename(path) or path}."
            + (f" No sound in {', '.join(silent)}: the soundtrack is in "
               "Form 2 sectors, which neither a 2048-byte copy nor a "
               "rebuilt ISO carries - open the bin/cue data track for it."
               if silent else "")))
        self.list.setCurrentCell(0, 0)

    def _chose(self, row):
        """A different movie was picked: show its first frame."""
        if not (0 <= row < len(self.movies)):
            return
        self.stop()
        self._drop_decoder()
        self._cache.clear()
        self.movie = self.movies[row]
        self._current = 0
        self._wav = None
        # Nothing of the movie being left may stay on screen. Holding
        # the last picture while the next one decodes is what keeps
        # scrubbing from flickering, but across a change of movie it
        # means the timeline says LOGO frame 51 over a frame of the
        # opening - which reads as the wrong movie having been decoded.
        self.screen.show_message(f"Decoding {self.movie.name}...")
        self.timeline.blockSignals(True)
        self.timeline.setRange(0, max(len(self.movie.frames) - 1, 0))
        self.timeline.setValue(0)
        self.timeline.blockSignals(False)
        self._decoder = _Decoder(self.movie, self)
        self._decoder.ready.connect(self._decoded)
        self._decoder.failed.connect(self._decode_failed)
        self._decoder.start()
        self._enable(True)
        self.save_audio.setEnabled(self.movie.has_audio)
        self.show_frame(0)
        panel_title.set_info(self.status, (
            f"{self.movie.name}: {len(self.movie.frames)} frames, "
            f"{self.movie.width}x{self.movie.height}, "
            f"{self.movie.fps:.2f} fps, {clock(self.movie.duration)}"
            + (f", {self.movie.rate} Hz "
               f"{'stereo' if self.movie.channels == 2 else 'mono'} sound."
               if self.movie.has_audio else ", no sound in this copy.")))

    def _enable(self, on):
        for widget in (self.timeline, self.play_button, self.save_frame,
                       self.save_frames, self.save_video, self.save_audio,
                       self.save_str, self.range_box):
            widget.setEnabled(on)

    # --- showing frames ------------------------------------------------

    def show_frame(self, index):
        """Put frame `index` on screen, decoding it if it is not in hand."""
        if not self.movie or not self.movie.frames:
            return
        index = max(0, min(index, len(self.movie.frames) - 1))
        self._current = index
        self.timeline.blockSignals(True)
        self.timeline.setValue(index)
        self.timeline.blockSignals(False)
        self._update_position()
        frame = self._cache.get(index)
        if frame is not None:
            self.screen.show_frame(frame)
        self._fill_ahead()

    def step(self, by):
        if self.movie:
            self.stop()
            self.show_frame(self._current + by)

    def _fill_ahead(self):
        """Ask the worker for the next frame worth having: the one on
        screen if it is missing, otherwise the first gap ahead of it."""
        if not self._decoder or not self.movie:
            return
        total = len(self.movie.frames)
        for index in range(self._current,
                           min(self._current + LOOK_AHEAD, total)):
            if index not in self._cache:
                self._decoder.want(index)
                return
        self._decoder.want(None)

    def _decoded(self, movie, index, rgb):
        if movie is not self.movie:
            return                      # decoded for the movie we just left
        self._cache[index] = rgb
        self._trim_cache()
        if index == self._current:
            self.screen.show_frame(rgb)
        self._fill_ahead()

    def _decode_failed(self, movie, index, message):
        if movie is not self.movie:
            return
        if index == self._current:
            self.screen.show_message(f"Frame {index} would not decode:\n"
                                     f"{message}")
        # Marked as done with something blank, so the prefetch does not
        # sit on the same broken frame forever.
        self._cache[index] = np.zeros(
            (self.movie.height, self.movie.width, 3), dtype=np.uint8)
        self._fill_ahead()

    def _trim_cache(self):
        if len(self._cache) <= CACHE_LIMIT:
            return
        for index in sorted(self._cache, key=lambda i: -abs(i - self._current)):
            if len(self._cache) <= CACHE_LIMIT:
                break
            if index != self._current:
                del self._cache[index]

    def _update_position(self):
        if not self.movie:
            self.position.setText("-")
            return
        total = len(self.movie.frames)
        fps = self.movie.fps or 1.0
        self.position.setText(
            f"frame {self._current + 1} / {total}    "
            f"{clock(self._current / fps)} / {clock(self.movie.duration)}")

    # --- playing --------------------------------------------------------

    def _toggle(self):
        if self._playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if not self.movie or not self.movie.frames:
            return
        if self._current >= len(self.movie.frames) - 1:
            self._current = 0
        if self.movie.has_audio:
            if self._wav is None:
                self._wav = self.movie.wav()
            self._start_audio(self._current / (self.movie.fps or 1.0))
        else:
            self._clock_frame = self._current
            self._clock.restart()
        self._playing = True
        set_glyph(self.play_button, "pause")
        self._ticker.start()

    def pause(self):
        self._playing = False
        self._ticker.stop()
        self.player.pause()
        set_glyph(self.play_button, "play")

    def stop(self):
        self._playing = False
        self._ticker.stop()
        self.player.stop()
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None
        set_glyph(self.play_button, "play")

    def _start_audio(self, seconds):
        """Start the soundtrack at `seconds`, which is what the picture
        then follows."""
        self.player.stop()
        if self._buffer is not None:
            self._buffer.close()
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(self._wav))
        self._buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self._buffer)
        self.player.play()
        self.player.setPosition(int(seconds * 1000))

    def _tick(self):
        """Show whichever frame the clock has reached.

        With sound, the clock is the audio player's own position, so a
        slow decode drops frames rather than sliding the picture out of
        step with the voice."""
        if not self._playing or not self.movie or self._scrubbing:
            return
        fps = self.movie.fps or 1.0
        if self.movie.has_audio:
            index = int(self.player.position() / 1000.0 * fps)
        else:
            index = self._clock_frame + int(
                self._clock.elapsed() / 1000.0 * fps)
        if index >= len(self.movie.frames):
            self.stop()
            self.show_frame(len(self.movie.frames) - 1)
            return
        if index != self._current:
            self.show_frame(index)

    def _audio_status(self, status):
        if (status == QMediaPlayer.MediaStatus.EndOfMedia and self._playing
                and self.movie):
            self.stop()
            self.show_frame(len(self.movie.frames) - 1)

    def _grab(self):
        self._scrubbing = True

    def _release(self):
        self._scrubbing = False
        self.show_frame(self.timeline.value())
        if self._playing and self.movie:
            fps = self.movie.fps or 1.0
            if self.movie.has_audio:
                self.player.setPosition(int(self._current / fps * 1000))
            else:
                self._clock_frame = self._current
                self._clock.restart()

    def _slid(self, value):
        """Dragging the timeline shows frames as it goes rather than
        only on release - the point of a timeline on a movie whose
        frames are all independent."""
        if self._scrubbing:
            self._current = value
            self._update_position()
            frame = self._cache.get(value)
            if frame is not None:
                self.screen.show_frame(frame)
            self._fill_ahead()
        elif value != self._current:
            self.show_frame(value)

    # --- exporting ------------------------------------------------------

    def _range(self):
        """(first, last) frame the export buttons should cover."""
        total = len(self.movie.frames)
        choice = self.range_box.currentIndex()
        if choice == 1:
            return self._current, total - 1
        if choice == 2:
            return self._current, self._current
        return 0, total - 1

    def _frames(self, first, last, note):
        """Yield the frames in a range, keeping the window responsive
        and the status line moving. Decoding happens here on the GUI
        thread: an export is a long blocking job either way, and doing
        it on the worker would have it competing with itself.

        Pumping the event loop is what keeps the window from reading as
        hung, and is also why _busy() has to have shut the controls
        first - a movie changed underneath a running export would pull
        the frames out from under it."""
        total = last - first + 1
        movie = self.movie
        for offset, index in enumerate(range(first, last + 1)):
            frame = self._cache.get(index)
            if frame is None:
                frame = movie.frame(index)
            if offset % 8 == 0:
                panel_title.set_info(
                    self.status,
                    f"{note} frame {offset + 1} of {total}...")
                QApplication.processEvents()
            yield frame

    @contextmanager
    def _busy(self):
        """Shut the controls for the length of an export."""
        self.stop()
        self.list.setEnabled(False)
        self._enable(False)
        try:
            yield
        finally:
            self.list.setEnabled(True)
            self._enable(bool(self.movie))
            self.save_audio.setEnabled(bool(self.movie)
                                       and self.movie.has_audio)

    def _stem(self):
        return movie_export.safe_name(
            os.path.splitext(self.movie.name)[0] if self.movie else "movie")

    def _save_frame(self):
        if not self.movie:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save this frame",
            f"{self._stem()}_{self._current:05d}.png", "PNG image (*.png)")
        if not path:
            return
        frame = self._cache.get(self._current)
        if frame is None:
            frame = self.movie.frame(self._current)
        try:
            movie_export.save_png(path, frame)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        panel_title.set_info(self.status,
                             f"Wrote {os.path.basename(path)}.")

    def _save_frames(self):
        if not self.movie:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Write the frames into...")
        if not folder:
            return
        first, last = self._range()
        with self._busy():
            try:
                written = movie_export.write_sequence(
                    folder, self._stem(), self._frames(first, last, "Writing"))
            except Exception as exc:
                panel_title.set_info(self.status, f"Stopped: {exc}")
                QMessageBox.critical(self, "Could not save", str(exc))
                return
        panel_title.set_info(
            self.status, f"Wrote {written} frame(s) into {folder}.")

    def _save_video(self):
        if not self.movie:
            return
        if not movie_export.have_ffmpeg():
            QMessageBox.information(
                self, "No ffmpeg",
                "Saving a video needs ffmpeg on PATH, and there isn't one.\n\n"
                "\"Save all frames\" and \"Save sound\" need nothing and "
                "lose none of the movie - the frames come out as PNGs and "
                "the soundtrack as a WAV.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the movie", f"{self._stem()}.mp4",
            "MP4 video (*.mp4);;Matroska (*.mkv);;AVI, lossless (*.avi)")
        if not path:
            return
        if os.path.splitext(path)[1].lower() not in movie_export.FORMATS:
            path += ".mp4"
        first, last = self._range()
        with self._busy():
            wav = None
            whole = (first, last) == (0, len(self.movie.frames) - 1)
            if self.movie.has_audio and whole:
                panel_title.set_info(self.status, "Decoding the soundtrack...")
                QApplication.processEvents()
                if self._wav is None:
                    self._wav = self.movie.wav()
                wav = self._wav
            try:
                movie_export.write_video(
                    path, self.movie, self._frames(first, last, "Encoding"),
                    wav)
            except Exception as exc:
                panel_title.set_info(self.status, f"Stopped: {exc}")
                QMessageBox.critical(self, "Could not save", str(exc))
                return
        panel_title.set_info(self.status, (
            f"Wrote {os.path.basename(path)} - {last - first + 1} frames at "
            f"{self.movie.fps:.2f} fps"
            + ("" if wav else ", no sound: "
               + ("this copy has none."
                  if not self.movie.has_audio else
                  "the soundtrack only goes with the whole movie."))))

    def _save_audio(self):
        if not self.movie or not self.movie.has_audio:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the soundtrack", f"{self._stem()}.wav",
            "WAV audio (*.wav);;MP3 audio (*.mp3)")
        if not path:
            return
        with self._busy():
            panel_title.set_info(self.status, "Decoding the soundtrack...")
            QApplication.processEvents()
            try:
                if self._wav is None:
                    self._wav = self.movie.wav()
                audio_export.save(path, self._wav)
            except Exception as exc:
                panel_title.set_info(self.status, f"Stopped: {exc}")
                QMessageBox.critical(self, "Could not save", str(exc))
                return
        panel_title.set_info(self.status, f"Wrote {os.path.basename(path)}.")

    def _save_str(self):
        if not self.movie:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the movie's sectors", self.movie.name,
            "STR movie (*.str);;All files (*)")
        if not path:
            return
        try:
            movie_export.write_str(path, self.movie)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        panel_title.set_info(self.status, (
            f"Wrote {os.path.basename(path)} - {self.movie.stride} bytes "
            f"a sector" + ("." if self.movie.raw else ", picture only.")))

    # --- shutting down --------------------------------------------------

    def _drop_decoder(self):
        """Stop the worker and let go of it. deleteLater rather than
        just dropping the reference: it is parented to the panel, so
        every movie picked would otherwise leave a finished QThread
        behind for as long as the tab is open."""
        if self._decoder is not None:
            self._decoder.stop()
            self._decoder.deleteLater()
            self._decoder = None

    def closeEvent(self, event):
        self.stop()
        self._drop_decoder()
        super().closeEvent(event)

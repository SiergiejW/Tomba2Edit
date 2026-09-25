"""Seeing a sound before playing it.

Three views over the same idea, all fed from the same peak envelope:

    peaks()         16-bit PCM down to N buckets of (lowest, highest).
                    The whole point of an envelope rather than the
                    samples is that it is the same size whatever the
                    sound's length, so a two-second effect and a
                    four-minute track draw in the same breath.

    WaveView        the big one above a seek bar: the envelope, the
                    part already played filled in, and a cursor that
                    can be dragged. Dragging emits a fraction, which
                    is what the transport turns into a seek - so the
                    bar and the existing position slider are two
                    handles on one position rather than two positions.

    MiniWave        the thumbnail in a list row, painted by a delegate
                    from the same envelope at a smaller size.

WaveView also draws a SEQUENCE instead of a waveform, because a
sequence has something better to show than its own rendered audio: the
notes. Same widget, same cursor, same signal - see set_sequence.

Peaks are computed off whatever PCM is already to hand and cached by
the caller. Nothing here decodes anything.
"""
import struct

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QStyledItemDelegate, QWidget

from gui import theme

try:
    import numpy as _np
except ImportError:                                  # pragma: no cover
    _np = None

# How many buckets a stored envelope holds. Wider than any thumbnail
# and wider than most big views, so one envelope serves both sizes and
# is averaged down rather than stretched up.
BUCKETS = 1024
MINI_BUCKETS = 96


class Notes:
    """A sequence as a preview: its notes, and where it ends.

    A sequence could be rendered to audio and drawn as a waveform, but
    that takes seconds and throws away the only thing worth seeing at
    this size - which part plays when, and how high. The notes are in
    the file already."""
    __slots__ = ("notes", "span")

    def __init__(self, notes, span):
        # (tick, length, channel, key), in the sequence's own ticks.
        self.notes = list(notes)
        self.span = max(1, int(span or 1))

    def __bool__(self):
        return bool(self.notes)


def _samples(wav):
    """(mono samples, channels) out of a PCM WAV this app wrote.

    Only the shape this app produces is handled - 16-bit PCM, one
    "fmt " and one "data" - because that is the only thing it is ever
    asked about. Anything else answers None rather than guessing."""
    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        return None, 0
    at, channels, bits = 12, 1, 16
    while at + 8 <= len(wav):
        tag = wav[at:at + 4]
        size = struct.unpack_from("<I", wav, at + 4)[0]
        if tag == b"fmt " and size >= 16:
            _fmt, channels, _rate, _br, _al, bits = struct.unpack_from(
                "<HHIIHH", wav, at + 8)
        elif tag == b"data":
            if bits != 16:
                return None, 0
            return wav[at + 8:at + 8 + size], channels
        at += 8 + size + (size & 1)
    return None, 0


def peaks(wav, buckets=BUCKETS):
    """[(low, high)] in -1..1, or None if there is nothing to show.

    Channels are folded together by taking the widest excursion of
    either, not by averaging them: a sound panned hard to one side
    still has to look like a sound."""
    pcm, channels = _samples(wav) if wav else (None, 0)
    if not pcm or channels <= 0:
        return None
    count = len(pcm) // 2
    if count < 2:
        return None
    if _np is not None:
        data = _np.frombuffer(pcm, dtype="<i2", count=count)
        if channels > 1:
            usable = (count // channels) * channels
            data = data[:usable].reshape(-1, channels)
            low = data.min(axis=1)
            high = data.max(axis=1)
        else:
            low = high = data
        frames = len(high)
        buckets = max(1, min(buckets, frames))
        edge = (frames // buckets) * buckets
        if edge < buckets:
            return None
        lo = low[:edge].reshape(buckets, -1).min(axis=1) / 32768.0
        hi = high[:edge].reshape(buckets, -1).max(axis=1) / 32768.0
        return list(zip(lo.tolist(), hi.tolist()))

    frames = count // max(1, channels)
    buckets = max(1, min(buckets, frames))
    step = max(1, frames // buckets)
    out = []
    for b in range(buckets):
        start = b * step
        lo, hi = 0, 0
        for f in range(start, min(start + step, frames)):
            for c in range(channels):
                v = struct.unpack_from("<h", pcm, (f * channels + c) * 2)[0]
                lo = v if v < lo else lo
                hi = v if v > hi else hi
        out.append((lo / 32768.0, hi / 32768.0))
    return out


def _wave_colours():
    """{role: QColor} for a waveform, from whatever theme is on."""
    c = theme.colours()
    bright = theme.is_bright()
    ground = theme.roll_colours()["ground"]
    return {
        "ground": QColor(ground),
        # The part not played yet: present, but clearly behind.
        "idle": QColor(theme._mix(ground, c["text"], 0.34 if bright else 0.30)),
        # The part already played takes the theme's second colour,
        # which on most themes is the accent and on the pink one is
        # the custard - the whole reason accent2 exists.
        "played": QColor(c["accent2"]),
        "cursor": QColor(c["accent"]),
        "line": QColor(theme._mix(ground, c["text"], 0.14)),
        "text": QColor(c["dim"]),
    }


def draw_preview(painter, rect, preview, played, colours, centre_line=True):
    """Whichever kind of preview `preview` is, into `rect`."""
    if isinstance(preview, Notes):
        _draw_notes(painter, rect, preview, played)
    else:
        _draw_wave(painter, rect, preview, played, colours, centre_line)


def _draw_notes(painter, rect, preview, played):
    """Every channel at once, one row of pixels per pitch.

    Not a piano roll - there is no room for one at these heights - but
    the shape of the piece: where each part comes in, where it rests,
    how high it sits."""
    from formats.audio.seq_editor import CHANNEL_COLOURS

    keys = [n[3] for n in preview.notes]
    if not keys:
        return
    low, high = min(keys), max(keys)
    if high - low < 11:
        middle = (low + high) // 2
        low, high = middle - 6, middle + 6
    rows = max(1, high - low + 1)
    row_height = max(1.0, rect.height() / rows)
    edge = rect.left() + played * rect.width()
    for tick, length, channel, key in preview.notes:
        x = rect.left() + tick / preview.span * rect.width()
        width = max(1.0, length / preview.span * rect.width())
        y = rect.bottom() - (key - low + 1) * row_height
        colour = QColor(CHANNEL_COLOURS[channel % 16])
        if played and x + width < edge:
            colour = colour.lighter(115)
        elif played:
            colour.setAlpha(150)
        painter.fillRect(QRectF(x, y, width, max(1.0, row_height - 0.5)),
                         colour)


def _draw_wave(painter, rect, envelope, played, colours, centre_line=True):
    """The envelope into `rect`, `played` (0..1) of it filled in."""
    if not envelope:
        return
    width = max(1, int(rect.width()))
    middle = rect.center().y()
    half = rect.height() / 2.0 - 1
    count = len(envelope)
    edge = rect.left() + played * rect.width()
    idle = QPen(colours["idle"]); idle.setWidth(1)
    done = QPen(colours["played"]); done.setWidth(1)
    if centre_line:
        painter.setPen(QPen(colours["line"]))
        painter.drawLine(int(rect.left()), int(middle),
                         int(rect.right()), int(middle))
    for x in range(width):
        # Every bucket the pixel covers, so narrowing the view drops
        # no peaks - a one-frame transient stays visible at any width.
        first = x * count // width
        last = max(first + 1, (x + 1) * count // width)
        lo = min(v[0] for v in envelope[first:last])
        hi = max(v[1] for v in envelope[first:last])
        at = rect.left() + x
        painter.setPen(done if at < edge else idle)
        top = middle - hi * half
        bottom = middle - lo * half
        if bottom - top < 1:
            top, bottom = middle - 0.5, middle + 0.5
        painter.drawLine(int(at), int(top), int(at), int(bottom))


class WaveView(QWidget):
    """The big preview above a seek bar, with a cursor that drags.

    Shows a waveform, or - when handed a sequence - the sequence's
    notes across all its channels. Either way the cursor means the
    same thing and `scrubbed` carries the same fraction, so the
    transport does not need to know which one is on screen."""

    scrubbed = pyqtSignal(float)        # 0..1, where the cursor was put
    HEIGHT = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.HEIGHT)
        self.setMaximumHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview = None             # an envelope, or a Notes
        self.position = 0.0             # 0..1
        self.caption = ""
        self._dragging = False

    # -- what it is showing --------------------------------------------

    def set_preview(self, preview, caption=""):
        """An envelope from peaks(), or a Notes. Either draws."""
        self.preview, self.caption = preview, caption
        self.update()

    def set_envelope(self, envelope, caption=""):
        self.set_preview(envelope, caption)

    def set_sequence(self, notes, span, caption=""):
        """Draw a sequence rather than a waveform.

        `notes` is [(tick, length, channel, key)] and `span` the tick
        the piece ends on - the roll's own units, so the caller hands
        over what it already has."""
        self.set_preview(Notes(notes, span) if notes else None, caption)

    def clear(self):
        self.preview = None
        self.caption = ""
        self.position = 0.0
        self.update()

    def set_position(self, fraction):
        fraction = 0.0 if fraction is None else max(0.0, min(1.0, fraction))
        # Only repaint when it would actually look different: this is
        # driven from positionChanged, which fires many times a second.
        if abs(fraction - self.position) * max(1, self.width()) >= 1:
            self.position = fraction
            self.update()
        else:
            self.position = fraction

    def has_content(self):
        return bool(self.preview)

    # -- dragging the cursor -------------------------------------------

    def _at(self, x):
        return max(0.0, min(1.0, x / max(1.0, float(self.width()))))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.has_content():
            return
        self._dragging = True
        self.set_position(self._at(event.position().x()))
        self.scrubbed.emit(self.position)

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        self.set_position(self._at(event.position().x()))
        self.scrubbed.emit(self.position)

    def mouseReleaseEvent(self, _event):
        self._dragging = False

    # -- painting ------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        colours = _wave_colours()
        painter.fillRect(self.rect(), colours["ground"])
        area = QRectF(self.rect()).adjusted(0, 2, 0, -2)

        if self.preview:
            draw_preview(painter, area, self.preview, self.position, colours)
        else:
            painter.setPen(QPen(colours["text"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Nothing playing")
            return

        if self.caption:
            painter.setPen(QPen(colours["text"]))
            painter.drawText(self.rect().adjusted(6, 2, -6, -2),
                             Qt.AlignmentFlag.AlignTop
                             | Qt.AlignmentFlag.AlignLeft, self.caption)

        x = int(self.position * self.width())
        painter.setPen(QPen(colours["cursor"], 1))
        painter.drawLine(x, 0, x, self.height())
        painter.fillRect(x - 3, 0, 7, 5, colours["cursor"])

class MiniWave:
    """Thumbnails for list rows, drawn once and kept.

    The pixmap is cached per (key, size, theme) rather than the
    envelope being redrawn every paint: a table repaints its visible
    rows constantly while scrolling, and re-walking a thousand buckets
    per row per frame is exactly the kind of thing that makes a list
    feel heavy."""

    def __init__(self):
        self._cache = {}

    def clear(self):
        self._cache.clear()

    def pixmap(self, key, preview, width, height, ratio=1.0):
        token = (key, int(width), int(height), theme.current_theme(),
                 round(ratio, 2))
        held = self._cache.get(token)
        if held is not None:
            return held
        picture = QPixmap(int(width * ratio), int(height * ratio))
        picture.setDevicePixelRatio(ratio)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        draw_preview(painter, QRectF(0, 0, width, height), preview, 0.0,
                     _wave_colours(), centre_line=False)
        painter.end()
        # Bounded: a long list scrolled end to end would otherwise keep
        # every thumbnail it ever drew.
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[token] = picture
        return picture


class WaveDelegate(QStyledItemDelegate):
    """Paints a row's thumbnail in whichever column it is given.

    `provider(key)` returns that row's preview - an envelope or a
    Notes - or None if it is not worked out yet, in which case the
    cell is left empty and the provider is expected to get around to
    it and ask for a repaint. Nothing is computed here; a delegate
    runs inside paint and is the last place that should be decoding
    audio."""

    def __init__(self, provider, key_role, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.key_role = key_role
        self.mini = MiniWave()

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        key = index.data(self.key_role)
        if not key:
            return
        preview = self.provider(key)
        if not preview:
            return
        rect = option.rect.adjusted(2, 2, -2, -2)
        if rect.width() < 4 or rect.height() < 3:
            return
        ratio = (option.widget.devicePixelRatioF() if option.widget else 1.0)
        painter.drawPixmap(
            rect.topLeft(),
            self.mini.pixmap(key, preview, rect.width(), rect.height(),
                             ratio))

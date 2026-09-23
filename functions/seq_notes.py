"""A sequence as notes, and back again.

seq.events gives a stream of deltas and status bytes, which is what the
player wants and the worst possible thing to draw or drag. seq.perform
gives notes, but in seconds and with the loops already played out, which
is what the renderer wants and is not reversible.

This is the third view, the editable one: notes at absolute ticks with a
length, and every other event kept beside them at the tick it happened.
Editing changes the notes; the rest is carried through untouched, so a
program change, a loop marker or a tempo change still happens where it
did. Converting back sorts everything by tick and works the deltas out
again.

Note-offs are written as note-on with velocity zero, which is what the
disc's own sequences do and what lets running status carry a run of
notes on one channel without repeating the status byte - that
compression is not cosmetic here, because the ten sequences share a
fixed region with nothing to spare.
"""

NOTE_OFF = 0x80
NOTE_ON = 0x90


class Note:
    """One note, in ticks."""
    __slots__ = ("tick", "length", "channel", "key", "velocity")

    def __init__(self, tick, length, channel, key, velocity):
        self.tick = tick
        self.length = max(1, length)
        self.channel = channel
        self.key = key
        self.velocity = velocity

    def __repr__(self):
        return (f"Note(tick={self.tick}, length={self.length}, "
                f"ch={self.channel}, key={self.key}, vel={self.velocity})")

    def copy(self):
        return Note(self.tick, self.length, self.channel, self.key,
                    self.velocity)

    def as_tuple(self):
        return (self.tick, self.length, self.channel, self.key, self.velocity)


def to_notes(events):
    """(notes, others) from seq.events' output.

    `others` is [(tick, status, a, b)] - everything that is not a note,
    at the tick it happens. A note still sounding at the end is closed
    there rather than dropped."""
    notes, others = [], []
    open_notes = {}
    tick = 0
    for delta, status, a, b in events:
        tick += delta
        kind = status & 0xF0
        if status == 0xFF:
            others.append((tick, status, a, b))
            continue
        channel = status & 0x0F
        if kind == NOTE_ON and b:
            # A second note-on for a key already sounding ends the
            # first one, which is what the hardware does - unless it
            # lands on the same tick, which is not a note ending but
            # the same note struck twice in the same instant. Slot 2 on
            # the US disc does exactly that. Treated as one note: it
            # sounds the same, and keeping both means a note of length
            # zero, which cannot be written back out without the ghost
            # swallowing the real note's tail.
            previous = open_notes.get((channel, a))
            if previous is not None and previous.tick == tick:
                previous.velocity = b
                continue
            if previous is not None:
                open_notes.pop((channel, a))
                previous.length = max(1, tick - previous.tick)
            note = Note(tick, 1, channel, a, b)
            notes.append(note)
            open_notes[(channel, a)] = note
        elif kind in (NOTE_OFF, NOTE_ON):
            note = open_notes.pop((channel, a), None)
            if note is not None:
                note.length = max(1, tick - note.tick)
        else:
            others.append((tick, status, a, b))
    for note in open_notes.values():
        note.length = max(1, tick - note.tick)
    notes.sort(key=lambda n: (n.tick, n.channel, n.key))
    return notes, others


def to_events(notes, others):
    """Back to [(delta, status, a, b)], sorted and re-delta'd."""
    timed = []
    for tick, status, a, b in others:
        # Order 1: things that set up a channel - a program change, a
        # volume, a loop marker - belong before the notes at their tick.
        timed.append((tick, 1, status, a, b))
    for note in notes:
        timed.append((note.tick, 2, NOTE_ON | note.channel & 0x0F,
                      note.key, note.velocity))
        # Order 0: a note ending at the same tick another starts must
        # come first, or the new one is cut short by the old one's off.
        timed.append((note.tick + max(1, note.length), 0,
                      NOTE_ON | note.channel & 0x0F, note.key, 0))
    timed.sort(key=lambda item: (item[0], item[1]))

    out, last = [], 0
    for tick, _order, status, a, b in timed:
        out.append((tick - last, status, a, b))
        last = tick
    return out


def span(notes, others=()):
    """The last tick anything happens on."""
    end = 0
    for note in notes:
        end = max(end, note.tick + note.length)
    for tick, *_rest in others:
        end = max(end, tick)
    return end


def timeline(events, resolution, tempo):
    """[(seconds, tick)] as the sequence actually plays.

    Not a straight conversion: the tempo can change part way through,
    and a loop sends the tick backwards while the seconds keep going.
    Both have to be followed or a playhead drawn from this drifts away
    from the sound - which is the one thing a playhead must not do.

    The loop handling mirrors seq.perform's, because that is what the
    renderer does and the audio being followed came out of it."""
    from functions import seq

    per_tick = tempo / 1e6 / max(1, resolution)
    out = [(0.0, 0)]
    time, tick, i = 0.0, 0, 0
    nrpn = None
    loop_at = loop_tick = None
    loop_left, passes = 0, 1
    while i < len(events) and time < seq.MAX_SECONDS:
        delta, status, a, b = events[i]
        i += 1
        time += delta * per_tick
        tick += delta
        if status == 0xFF:
            per_tick = a / 1e6 / max(1, resolution)
        elif status & 0xF0 == 0xB0:
            if a == 99:
                nrpn = b
                if b == seq.LOOP_END and loop_at is not None:
                    jumped = False
                    if loop_left in seq.FOREVER:
                        if passes < seq.LOOP_PASSES:
                            passes += 1
                            jumped = True
                        else:
                            break
                    elif loop_left > 1:
                        loop_left -= 1
                        jumped = True
                    if jumped:
                        out.append((time, tick))
                        i, tick = loop_at, loop_tick
                        out.append((time, tick))
                        continue
            elif a == 6 and nrpn == seq.LOOP_START:
                loop_at, loop_tick, loop_left, nrpn = i, tick, b, None
        out.append((time, tick))
    return out


def tick_at(points, seconds):
    """Where the playhead belongs at `seconds`, from timeline()."""
    if not points:
        return 0
    low, high = 0, len(points) - 1
    if seconds <= points[0][0]:
        return points[0][1]
    if seconds >= points[high][0]:
        return points[high][1]
    while low < high - 1:
        middle = (low + high) // 2
        if points[middle][0] <= seconds:
            low = middle
        else:
            high = middle
    (t0, k0), (t1, k1) = points[low], points[high]
    if t1 <= t0 or k1 < k0:
        return k0
    return int(k0 + (k1 - k0) * (seconds - t0) / (t1 - t0))


def seconds_at(points, tick):
    """The first moment the sequence reaches `tick` - for seeking."""
    for seconds, where in points:
        if where >= tick:
            return seconds
    return points[-1][0] if points else 0.0


def channels_used(notes):
    return sorted({note.channel for note in notes})


def key_range(notes, pad=2):
    """(lowest, highest) key worth showing, with a little air."""
    if not notes:
        return 48, 72
    low = min(note.key for note in notes)
    high = max(note.key for note in notes)
    if high - low < 11:
        middle = (low + high) // 2
        low, high = middle - 6, middle + 6
    return max(0, low - pad), min(127, high + pad)

"""Sony SEQ music out to a Standard MIDI File, and back again.

A SEQ is very nearly an SMF already: strip the "pQES" header off and
what is left is one MIDI track - delta times, running status, the same
status bytes. So this is mostly a matter of moving the tempo and the
resolution from one header shape to the other, which is what makes an
exported .mid something a sequencer opens and edits directly.

Two things do not carry over cleanly, and both are deliberate:

    LOOPS      A SEQ marks a loop with NRPN 20 and 30 - ordinary
               control changes, which survive the trip untouched and
               come back as themselves. A sequencer shows them as CC
               99/98/6 and gives no hint what they are, so an export
               can also drop a marker beside each one. Markers are
               notes to the reader; import ignores them, so adding them
               cannot change the music.

    PROGRAMS   A program number indexes the VAB the SEQ plays on, not
               General MIDI. An exported file therefore sounds wrong in
               anything but this game unless the editor points it at
               the right samples - the notes and timing are exact, the
               instrument names are not ours to give.

Import is the inverse and is strict about one thing: the resolution has
to survive, because every delta in the file is measured in it. A file
saved at a different division is rejected rather than silently played
at the wrong speed.
"""
import struct

from formats.audio import seq

MTHD = b"MThd"
MTRK = b"MTrk"
TEMPO_META = 0x51
MARKER_META = 0x06
END_META = 0x2F

LOOP_MARKERS = {seq.LOOP_START: "loop start", seq.LOOP_END: "loop end"}
# The NRPN a loop marker is written as: CC 99 selects the parameter,
# and it is the number in it that says which of the two this is.
NRPN_MSB = 99


class MidiError(ValueError):
    """Raised when a file isn't a MIDI this can use."""


def _vlq(value):
    """A MIDI variable-length quantity."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | value & 0x7F)
        value >>= 7
    return bytes(out)


def _chunk(tag, payload):
    return tag + struct.pack(">I", len(payload)) + payload


def from_seq(data, at, markers=True):
    """One SEQ at `at` as a format-0 Standard MIDI File.

    Every event is written with an explicit status byte rather than
    running status: it costs a byte each and makes the result something
    any reader handles, which matters more for a file whose whole
    purpose is being opened somewhere else."""
    resolution, microseconds = seq.header(data, at)
    events = seq.events(data, at)

    track = bytearray()
    # The SEQ header's tempo is the starting tempo; a SEQ can change it
    # later with its own FF 51, which comes through as an event below.
    track += _vlq(0) + bytes((0xFF, TEMPO_META, 3))
    track += microseconds.to_bytes(3, "big")

    for delta, status, a, b in events:
        if status == 0xFF:
            track += _vlq(delta) + bytes((0xFF, TEMPO_META, 3))
            track += int(a).to_bytes(3, "big")
            continue
        if markers and status & 0xF0 == 0xB0 and a == NRPN_MSB \
                and b in LOOP_MARKERS:
            # Written at the same delta, so the marker lands exactly on
            # the event it describes and the event itself still gets
            # its full delta from the one before.
            name = LOOP_MARKERS[b].encode("ascii")
            track += _vlq(delta) + bytes((0xFF, MARKER_META, len(name)))
            track += name
            delta = 0
        track += _vlq(delta) + bytes((status, a))
        if status & 0xF0 not in (0xC0, 0xD0):
            track += bytes((b,))

    track += _vlq(0) + bytes((0xFF, END_META, 0))
    head = struct.pack(">HHH", 0, 1, resolution)
    return _chunk(MTHD, head) + _chunk(MTRK, bytes(track))


# ----------------------------------------------------------------------
# Reading one back
# ----------------------------------------------------------------------

def _read_chunks(blob):
    at, out = 0, []
    while at + 8 <= len(blob):
        tag = blob[at:at + 4]
        size = struct.unpack_from(">I", blob, at + 4)[0]
        out.append((tag, blob[at + 8:at + 8 + size]))
        at += 8 + size
    return out


def read(blob):
    """(resolution, [(delta, status, a, b)]) out of a MIDI file.

    Tempo comes back as seq's own 0xFF event so the two event streams
    are the same shape; every other meta event is dropped, markers
    included - they are for whoever opened the file in a sequencer, and
    the game has nowhere to put them."""
    chunks = _read_chunks(blob)
    if not chunks or chunks[0][0] != MTHD or len(chunks[0][1]) < 6:
        raise MidiError("This isn't a MIDI file - no MThd header in it.")
    fmt, tracks, division = struct.unpack_from(">HHH", chunks[0][1], 0)
    if division & 0x8000:
        raise MidiError(
            "This file is timed in SMPTE frames rather than ticks per "
            "beat, which a SEQ has no way to express.")

    merged = []
    for tag, payload in chunks[1:]:
        if tag != MTRK:
            continue
        merged.extend(_track_events(payload))
        if fmt == 0:
            break
    if fmt == 1 and tracks > 1 and len(
            [t for t, _ in chunks[1:] if t == MTRK]) > 1:
        raise MidiError(
            "This is a multi-track MIDI file. A SEQ holds exactly one "
            "track, so save it as format 0 (a single merged track) first.")
    return division, merged


def _track_events(payload):
    # `pending` is time belonging to events that are dropped - markers,
    # track names, anything the game has nowhere to put. The time is
    # still real, so it is handed to the next event that survives
    # rather than thrown away with the event that carried it.
    out, pos, running, pending = [], 0, 0, 0
    end = len(payload)
    while pos < end:
        delta = 0
        while pos < end:
            byte = payload[pos]
            pos += 1
            delta = delta << 7 | byte & 0x7F
            if not byte & 0x80:
                break
        if pos >= end:
            break
        status = payload[pos]
        if status & 0x80:
            pos += 1
            if status < 0xF0:
                running = status
        else:
            status = running
        if status == 0xFF:
            kind = payload[pos]
            pos += 1
            size, shift = 0, 0
            while pos < end:
                byte = payload[pos]
                pos += 1
                size = size << 7 | byte & 0x7F
                shift += 1
                if not byte & 0x80:
                    break
            body = payload[pos:pos + size]
            pos += size
            if kind == TEMPO_META and size == 3:
                out.append((delta + pending, 0xFF,
                            int.from_bytes(body, "big"), 0))
                pending = 0
            elif kind == END_META:
                break
            else:
                pending += delta
            continue
        if status in (0xF0, 0xF7):
            size = 0
            while pos < end:
                byte = payload[pos]
                pos += 1
                size = size << 7 | byte & 0x7F
                if not byte & 0x80:
                    break
            pos += size
            pending += delta
            continue
        if not status:
            break
        if status & 0xF0 in (0xC0, 0xD0):
            out.append((delta + pending, status, payload[pos], 0))
            pos += 1
        else:
            out.append((delta + pending, status, payload[pos],
                        payload[pos + 1] if pos + 1 < end else 0))
            pos += 2
        pending = 0
    return out


def to_seq(blob, resolution=None):
    """A MIDI file as SEQ bytes, ready to go back on the disc.

    `resolution` is what the SEQ being replaced used; the file has to
    match it, because a delta means a different length of time at a
    different division and nothing here can retime the music safely."""
    division, events = read(blob)
    if resolution is not None and division != resolution:
        raise MidiError(
            f"This file is {division} ticks per beat and the music it "
            f"replaces is {resolution}. Set the sequencer's division to "
            f"{resolution} and save it again - retiming it here would "
            "change how the music plays.")

    return events_to_seq(events, division)


def events_to_seq(events, resolution, microseconds=None):
    """[(delta, status, a, b)] as SEQ bytes.

    The event stream shape seq.events produces and seq_notes edits, so
    the piano roll and a MIDI import end up going down the same path -
    including the running-status packing, which is what keeps an edited
    sequence inside its budget."""
    if microseconds is None:
        microseconds = 500000
    body = bytearray()
    # Running status, because the sequences are packed into a fixed
    # region with no room to spare - on the US disc all ten fill it to
    # the byte. Writing an explicit status for every event costs a few
    # hundred bytes on a long piece, which is the difference between a
    # round trip fitting and being refused.
    running = None
    for delta, status, a, b in events:
        if status == 0xFF:
            if not body:
                # A tempo before anything has played is the SEQ
                # header's, which is where it has to go.
                microseconds = int(a)
                continue
            body += _vlq(delta) + bytes((0xFF, TEMPO_META, 3))
            body += int(a).to_bytes(3, "big")
            # A meta event clears it: the reader on the other side is
            # tracking the same thing and stops after one.
            running = None
            continue
        body += _vlq(delta)
        if status != running:
            body += bytes((status,))
            running = status
        body += bytes((a,))
        if status & 0xF0 not in (0xC0, 0xD0):
            body += bytes((b,))

    # End of track. read() consumes the marker without reporting it, so
    # it has to be put back - and it is not optional padding: a SEQ sits
    # directly in front of the next one inside TOMBA2.SND, and without a
    # terminator the player reads straight on into the music after it.
    body += _vlq(0) + bytes((0xFF, END_META, 0))

    out = bytearray(seq.MAGIC)
    out += b"\x00\x00\x00\x01"                      # version
    out += struct.pack(">H", resolution)
    out += int(microseconds).to_bytes(3, "big")
    out += b"\x01\x02"                              # rhythm, as the disc's
    return bytes(out + body)

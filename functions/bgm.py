"""Where one piece of music ends and the next begins.

A channel of BGM.XA is not one song. Channel 1, for instance, is five of
them laid end to end across its 5,902 sectors, and playing the channel
whole runs them together. Nothing in the sectors themselves says where
the joins are: the subheaders carry one file number, one channel, one
coding byte and no flags from the first sector to the last, and the only
EOF bit in the file is on the very last sector. The audio is not marked
either - a join is roughly a sector of near-silence, but "near" is a
threshold, and picking one is guesswork.

The game does not guess, because it has a table. MAIN.EXE holds, for
each channel in turn, a list of (start, length) pairs in sectors as
16-bit words:

    channel 0   (0,1652) (1652,1659) (3311,703) (4014,1614) (5628,265)
    channel 1   (0,1656) (1656,1653) (3309,1589) (4898,730) (5628,274)
    ...

Each list begins at 0, each start is the previous start plus its length,
and the last one ends exactly on the number of audio sectors that
channel actually has - 5893 and 5902 for those two. Thirty-two tracks
across the eight channels, and every total closes.

That last property is what finds the table, rather than an address
hardcoded from one disc: the sector counts are read off the image first,
and the table is wherever in MAIN.EXE a run of pairs happens to
reproduce all eight of them. A wrong offset cannot survive that - it
would have to hit eight consecutive sums by accident.

DEMO.XA has no such table. Its channels hold one clip each and then pad
out with silence to a common length, so they are split on the padding
instead, which is exact: those sectors are all-zero ADPCM, not merely
quiet.
"""
import struct

from functions import xa

PAIR = 4                # (start, length), two 16-bit words
PAD_RUN = 32            # silent sectors that mean padding, not a rest


def parse(exe, offset, counts):
    """Read one (start, length) list per channel, or None.

    `counts` is each channel's audio sector count, in channel order.
    Returns [[(start, length), ...], ...] only if every list starts at 0,
    runs without a gap or an overlap, and closes exactly on its count."""
    out = []
    for total in counts:
        tracks = []
        position = 0
        while position < total:
            if offset + PAIR > len(exe):
                return None
            start, length = struct.unpack_from("<HH", exe, offset)
            if start != position or length == 0:
                return None
            tracks.append((start, length))
            position += length
            offset += PAIR
        if position != total:
            return None
        out.append(tracks)
    return out


def find_table(exe, counts):
    """(offset, tracks) for the first place the whole table parses."""
    for offset in range(0, len(exe) - PAIR, 2):
        tracks = parse(exe, offset, counts)
        if tracks is not None:
            return offset, tracks
    return None


def is_silent(payload):
    """True if every sample nibble in the sector is zero.

    Exact digital silence, which is what a channel pads with once its
    clip has finished - as opposed to quiet music, which this will not
    match. Only the 112 sample bytes of each group are looked at; the
    16-byte headers keep their filter and shift bytes even when there is
    nothing to decode."""
    for group in range(xa.GROUPS):
        base = group * xa.GROUP_LEN
        if any(payload[base + 16:base + xa.GROUP_LEN]):
            return False
    return True


def trim_padding(image, lba, indices):
    """Drop the padding a channel ends with.

    Cutting at the last sector that is not all-zero would stop too early:
    a channel's final sector is its EOF terminator and carries one stray
    sound group, a click rather than audio, and the sector before it
    sometimes carries another. So the cut is made at the start of the
    last long run of silence instead, and whatever follows that run goes
    with it.

    Long is PAD_RUN sectors, about 1.7 seconds. The threshold is on how
    many sectors are silent, not on how quiet they are - each one is
    exactly zero, and music does not contain seconds of digital zero."""
    run = 0
    for position in range(len(indices) - 1, -1, -1):
        image.seek((lba + indices[position]) * xa.SECTOR)
        raw = image.read(xa.SECTOR)
        silent = len(raw) == xa.SECTOR and is_silent(
            raw[xa.PAYLOAD:xa.PAYLOAD + xa.FORM2_LEN])
        if silent:
            run += 1
        elif run >= PAD_RUN:
            return indices[:position + 1]
        else:
            run = 0
    return indices[:0] if run >= PAD_RUN else indices


def split(image, lba, sectors, exe=None):
    """The pieces of music in one XA file.

    Yields (channel, ordinal, indices) with `indices` the sector numbers
    of one track, ready for xa.decode_channel. With a table the channels
    are split into their tracks; without one each channel is a single
    piece with its trailing padding removed."""
    chans = xa.channel_map(image, lba, sectors)
    order = sorted(chans)
    counts = [len(chans[key]) for key in order]

    tracks = None
    if exe:
        found = find_table(exe, counts)
        if found:
            tracks = found[1]

    out = []
    for n, key in enumerate(order):
        indices = chans[key]
        channel = key[1]
        if tracks is None:
            kept = trim_padding(image, lba, indices)
            out.append((channel, 0, kept))
            continue
        for ordinal, (start, length) in enumerate(tracks[n]):
            out.append((channel, ordinal, indices[start:start + length]))
    return out

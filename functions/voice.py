"""Finding and cutting up the voice track.

VOICE.XA holds the spoken dialogue as 32 interleaved channels (see
functions/xa.py). What it does not hold is any marking of where one line
ends and the next begins: the game is told which channel to play, from
which sector, and for how long, so a clip is only a clip because
something outside the file said so.

That leaves two ways to browse it. The right one is the timing table in
the area's overlay, which nobody has located yet. The one here is to
decode a channel and cut it where it falls quiet, which lands close
enough to listen through but should not be mistaken for the real
boundaries - a line with a pause in it splits, and two lines run
together if the gap between them is short.

The file has to be read from a raw 2352-byte track. A CD folder's
VOICE.XA, or one out of a 2048-byte ISO, has had 276 bytes cut out of
every sector and cannot be decoded at all.
"""
import os

from functions import xa
from functions.iso9660 import ISO9660Reader

# What counts as silence when cutting a channel into clips.
QUIET_LEVEL = 200          # amplitude, out of 32767
QUIET_WINDOW = 1024        # samples judged at a time
MIN_GAP = 0.30             # seconds of quiet before it separates two clips
MIN_CLIP = 0.20            # seconds; anything shorter is not a line


class VoiceError(Exception):
    """Raised when a disc image can't give us the voice track."""


def find_track(path):
    """(lba, sectors) of VOICE.XA in a raw disc image.

    Only the data track will do. The other two things that look like they
    ought to work are both refused here rather than left to fail later as
    noise: a bin/cue's second track is CD audio with no filesystem at
    all, and an extracted VOICE.XA - or one out of a 2048-byte ISO - has
    had 276 bytes cut out of every sector and is not decodable."""
    name = os.path.basename(path)
    if name.upper().endswith(".XA"):
        raise VoiceError(
            f"{name} is an extracted copy, and extracting is what breaks it: "
            "the voice track is Mode 2 Form 2, carrying 2324 bytes a sector "
            "where a file copy takes 2048. Open the data track of the "
            "bin/cue instead (Track 1).")
    with open(path, "rb") as f:
        data = f.read()
    try:
        reader = ISO9660Reader(data)
    except Exception:
        raise VoiceError(
            f"{name} has no filesystem in it. A bin/cue's Track 2 is plain "
            "CD audio, not the data track - open Track 1.")
    if reader.sector_size != xa.SECTOR:
        raise VoiceError(
            f"{name} has {reader.sector_size}-byte sectors. The voice track "
            "is Mode 2 Form 2 and only survives in a raw 2352-byte image - "
            "the data track of a bin/cue rip.")

    found = []

    def walk(lba, size, depth=0):
        for entry in reader.list_directory(lba, size):
            name = reader.clean_name(entry.name)
            if not name:
                continue
            if entry.is_dir:
                if depth < 2:
                    walk(entry.lba, entry.size, depth + 1)
            elif name.upper() == "VOICE.XA":
                found.append((entry.lba, entry.size))

    walk(reader.root_lba, reader.root_size)
    if not found:
        raise VoiceError("No VOICE.XA in this image.")
    lba, size = found[0]
    return lba, size // 2048


def clips(samples, rate):
    """Where a decoded channel falls quiet -> [(start, end)] in samples.

    Runs of quiet longer than MIN_GAP separate clips; the quiet itself is
    left out, so a clip starts and ends on sound."""
    if not samples:
        return []
    quiet = []
    for i in range(0, len(samples) - QUIET_WINDOW, QUIET_WINDOW):
        loud = False
        for s in samples[i:i + QUIET_WINDOW]:
            if s > QUIET_LEVEL or s < -QUIET_LEVEL:
                loud = True
                break
        quiet.append(not loud)

    gap = max(1, int(MIN_GAP * rate) // QUIET_WINDOW)
    out = []
    start = None
    run = 0
    for i, is_quiet in enumerate(quiet + [True]):
        if is_quiet:
            run += 1
            continue
        if run >= gap and start is not None:
            end = (i - run) * QUIET_WINDOW
            if (end - start) / rate >= MIN_CLIP:
                out.append((start, end))
            start = None
        if start is None:
            start = i * QUIET_WINDOW
        run = 0
    if start is not None:
        end = len(samples)
        if (end - start) / rate >= MIN_CLIP:
            out.append((start, end))
    return out


def channel_clips(image, lba, sectors, channel, limit=None):
    """Decode one channel and cut it up: (samples, rate, [(start, end)])."""
    with open(image, "rb") as f:
        chans = xa.channel_map(f, lba, sectors)
        key = next((k for k in chans if k[1] == channel), None)
        if key is None:
            return [], 37800, []
        samples, rate = xa.decode_channel(f, lba, chans[key], limit)
    return samples, rate, clips(samples, rate)


# --- linking a line of text to its clip ------------------------------
#
# A TXTD entry carries `extra`; 0xFFFF means the line has no voice, and
# otherwise the low byte indexes a table in the area's overlay:
#
#     (u16 start_block, u16 length_blocks)[]
#
# VOICE.XA interleaves 32 channels, so a "block" is 32 sectors and one
# channel contributes one sector to each. A clip is therefore
#
#     sector = (start_block + b) * 32 + channel,   b = 0 .. length-1
#
# The tables give themselves away by being a chain: each entry starts
# where the previous one ended, so start[n+1] == start[n] + len[n] holds
# from the first entry (which starts at 0) to the last. An overlay holds
# several of them, one per channel it draws voice from.
#
# Verified against four savestates taken on known lines: DuckStation's
# CDROM state had the head at exactly the block each table entry names.

MIN_TABLE = 8              # entries before a chain is believable
BLOCK = 32                 # sectors per interleave block


def find_tables(overlay_path, min_entries=MIN_TABLE):
    """[(offset, [(start, length), ...])] for every clip table found."""
    import struct

    data = open(overlay_path, "rb").read()
    out = []
    i = 0
    while i < len(data) - 8:
        start, length = struct.unpack_from("<HH", data, i)
        if start != 0 or not (0 < length < 400):
            i += 4
            continue
        entries = [(start, length)]
        at = start + length
        j = i + 4
        while j + 4 <= len(data):
            s2, l2 = struct.unpack_from("<HH", data, j)
            if s2 != at or not (0 < l2 < 400):
                break
            entries.append((s2, l2))
            at = s2 + l2
            j += 4
        if len(entries) >= min_entries:
            out.append((i, entries))
            i = j
        else:
            i += 4
    return out


def clip_sectors(entry, channel):
    """The VOICE.XA sector numbers one clip occupies."""
    start, length = entry
    return [(start + b) * BLOCK + channel for b in range(length)]


def best_channel(image, lba, entries, probe_blocks=200):
    """Which channel a table describes, judged by its own boundaries.

    Each entry begins where the one before ended, so on the right channel
    those block boundaries land in the gaps between spoken lines. Scoring
    channels by how often the block before a boundary is quiet picks it
    out; the wrong channel has its own clips, cut at other places."""
    bounds = [s for s, _l in entries[1:] if s < probe_blocks]
    if not bounds:
        return None, 0.0
    best, best_score = None, -1.0
    with open(image, "rb") as f:
        for channel in range(BLOCK):
            samples, _rate = xa.decode_channel(
                f, lba, [b * BLOCK + channel for b in range(probe_blocks)])
            per = xa.SAMPLES_PER_SECTOR
            quiet = 0
            for b in bounds:
                seg = samples[(b - 1) * per:b * per]
                if seg and max(abs(v) for v in seg) < QUIET_LEVEL * 4:
                    quiet += 1
            score = quiet / len(bounds)
            if score > best_score:
                best, best_score = channel, score
    return best, best_score


def channels(image, lba, sectors):
    """Which channels the track carries, and how many sectors each has."""
    with open(image, "rb") as f:
        chans = xa.channel_map(f, lba, sectors)
    return sorted((k[1], len(v)) for k, v in chans.items())

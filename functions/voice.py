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

    Refuses an image that isn't raw, since a 2048-byte one cannot hold
    the audio whatever its directory says."""
    with open(path, "rb") as f:
        data = f.read()
    reader = ISO9660Reader(data)
    if reader.sector_size != xa.SECTOR:
        raise VoiceError(
            f"{os.path.basename(path)} has {reader.sector_size}-byte sectors. "
            "The voice track is Mode 2 Form 2 and only survives in a raw "
            "2352-byte image - a BIN track from a bin/cue rip.")

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


def channels(image, lba, sectors):
    """Which channels the track carries, and how many sectors each has."""
    with open(image, "rb") as f:
        chans = xa.channel_map(f, lba, sectors)
    return sorted((k[1], len(v)) for k, v in chans.items())

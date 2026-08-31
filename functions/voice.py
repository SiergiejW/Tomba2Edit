"""Finding and cutting up the voice track.

VOICE.XA holds the spoken dialogue as 32 interleaved channels (see
functions/xa.py). What it does not hold is any marking of where one line
ends and the next begins: the game is told which channel to play, from
which sector, and for how long, so a clip is only a clip because
something outside the file said so.

There are two ways to cut it up. The exact one is the clip table in the
area's overlay, which says where each clip starts and how long it runs -
see find_tables() below. The other, used for browsing a channel when no
overlay is in hand, is to decode it and cut where it falls quiet; that
lands close but should not be mistaken for the real boundaries, since a
line with a pause in it splits and two lines run together when the gap
between them is short.

Two sources work: a raw 2352-byte disc track, and a VOICE.XA extracted
properly - 2324 bytes a sector, which extract_voice() writes. What does
not work is a VOICE.XA copied as if it were an ordinary file, as a CD
folder or a 2048-byte ISO carries: that discards 276 bytes of every
sector, 12% of the audio, and nothing can put it back.
"""
import mmap
import struct
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
        # A clean Form 2 extraction is usable on its own: 2324 bytes a
        # sector, channels still in their fixed interleave. One that
        # divides by 2048 was copied as an ordinary file, which threw
        # away 276 bytes of every sector - 12% of the audio - and that
        # cannot be put back.
        frame = xa.framing(path)
        size = os.path.getsize(path)
        if frame == xa.FLAT:
            return 0, size // xa.FORM2_LEN
        if frame == xa.RAW:
            return 0, size // xa.SECTOR
        raise VoiceError(
            f"{name} is {size:,} bytes, which divides by 2048 - it was "
            "copied as an ordinary file, and that discards 276 bytes of "
            "every sector (12% of the audio, unrecoverably). Open the "
            "bin/cue data track, or use Voice > Extract VOICE.XA to write "
            "a good copy into the folder.")
    # Mapped rather than read. A data track is hundreds of megabytes and
    # this runs on every disc open; pulling it into memory twice over is
    # enough to take the process down. Reading only the head instead is
    # not an option - the reader checks the image against the size its
    # own directory declares, and a partial read fails that.
    with open(path, "rb") as f:
        try:
            data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            data = f.read()
        try:
            try:
                reader = ISO9660Reader(data)
            except Exception:
                raise VoiceError(
                    f"{name} has no filesystem in it. A bin/cue's Track 2 is "
                    "plain CD audio, not the data track - open Track 1.")
            if reader.sector_size != xa.SECTOR:
                raise VoiceError(
                    f"{name} has {reader.sector_size}-byte sectors. The voice "
                    "track is Mode 2 Form 2 and only survives in a raw "
                    "2352-byte image - the data track of a bin/cue rip.")

            found = []

            def walk(lba, size, depth=0):
                for entry in reader.list_directory(lba, size):
                    entry_name = reader.clean_name(entry.name)
                    if not entry_name:
                        continue
                    if entry.is_dir:
                        if depth < 2:
                            walk(entry.lba, entry.size, depth + 1)
                    elif entry_name.upper() == "VOICE.XA":
                        found.append((entry.lba, entry.size))

            walk(reader.root_lba, reader.root_size)
            if not found:
                raise VoiceError("No VOICE.XA in this image.")
            lba, size = found[0]
            return lba, size // 2048
        finally:
            if isinstance(data, mmap.mmap):
                data.close()


def extract_file(image_path, wanted):
    """One file's bytes out of a disc image, by name, or None.

    The overlays are not unpacked when a disc is opened as an image, so
    the one an area needs is pulled straight out of the image instead of
    making the user find a BIN folder that may not exist."""
    wanted = wanted.upper()
    with open(image_path, "rb") as f:
        try:
            data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            data = f.read()
        try:
            reader = ISO9660Reader(data)
            found = []

            def walk(lba, size, depth=0):
                for entry in reader.list_directory(lba, size):
                    name = reader.clean_name(entry.name)
                    if not name:
                        continue
                    if entry.is_dir:
                        if depth < 2:
                            walk(entry.lba, entry.size, depth + 1)
                    elif name.upper() == wanted:
                        found.append((entry.lba, entry.size))

            walk(reader.root_lba, reader.root_size)
            if not found:
                return None
            lba, size = found[0]
            return reader.read_file(lba, size)
        except Exception:
            return None
        finally:
            if isinstance(data, mmap.mmap):
                data.close()


def extract_voice(image_path, out_path):
    """Write a usable VOICE.XA out of a raw disc track.

    Copies the 2324-byte Form 2 payloads back to back, which is what an
    ordinary file copy gets wrong. The result opens on its own, so a CD
    folder can carry working audio."""
    lba, sectors = find_track(image_path)
    with open(image_path, "rb") as src, open(out_path, "wb") as dst:
        for i in range(sectors):
            src.seek((lba + i) * xa.SECTOR)
            raw = src.read(xa.SECTOR)
            if len(raw) < xa.SECTOR:
                break
            dst.write(raw[xa.PAYLOAD:xa.PAYLOAD + xa.FORM2_LEN])
    return out_path


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
    frame = xa.framing(image) or xa.RAW
    with open(image, "rb") as f:
        chans = xa.channel_map(f, lba, sectors, frame)
        key = next((k for k in chans if k[1] == channel), None)
        if key is None:
            return [], 37800, []
        samples, rate = xa.decode_channel(f, lba, chans[key], limit, frame)
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

# Where an overlay is loaded. Found by locating A00.BIN inside a
# DuckStation savestate's RAM, which matched the file for 284,652 of its
# 285,096 bytes; the overlays share the slot, so one address serves all.
OVERLAY_BASE = 0x80108F9C


def _sx(imm):
    """A 16-bit immediate as MIPS sign-extends it."""
    return imm - 0x10000 if imm > 0x7FFF else imm


def read_dispatch(overlay_path, base=OVERLAY_BASE):
    """{master: (clip table offset, channel, block offset)} from the code.

    The overlay picks a master's voice in a short run of instructions
    that sets three registers and jumps to a common tail:

        lui   v0, hi
        addiu v0, v0, lo        <- the clip table
        addiu a3, zero, n       <- the VOICE.XA channel
        addiu a2, zero, b       <- a block offset added to every start
                                   (a2 is left zero where there is none)

    The big overlays reach these through a jump table, the small ones
    through a chain of compares, so the cases themselves are what gets
    matched here rather than the dispatch above them. They appear in
    master order either way.

    This is the game's own mapping rather than a guess at it, so it
    needs no audio, no probing and no cache."""
    data = open(overlay_path, "rb").read()
    top = len(data) - 4

    def word(at):
        return struct.unpack_from("<I", data, at)[0] if 0 <= at < top else 0

    cases = []
    for at in range(0, top, 4):
        w = word(at)
        # addiu a3, zero, n  - a channel, so n must be a real one
        if w >> 26 != 0x09 or (w >> 16) & 31 != 7 or (w >> 21) & 31 != 0:
            continue
        channel = w & 0xFFFF
        if channel >= BLOCK:
            continue
        # the clip table is built just above; the block offset sits
        # within a couple of instructions either side
        clip_table = offset = None
        for k in range(-4, 4):
            v = word(at + k * 4)
            op, rt, rs, imm = v >> 26, (v >> 16) & 31, (v >> 21) & 31, v & 0xFFFF
            if op == 0x09 and rt == 2 and clip_table is None:
                hi = None
                for j in range(1, 4):
                    u = word(at + (k - j) * 4)
                    if u >> 26 == 0x0F and (u >> 16) & 31 == 2:
                        hi = u & 0xFFFF
                        break
                if hi is not None:
                    clip_table = (hi << 16) + _sx(imm) - base
            elif op == 0x09 and rt == 6 and rs == 0 and offset is None:
                offset = imm
            elif v == 0x00003021 and offset is None:      # addu a2, zero, zero
                offset = 0
        if clip_table is None or not (0 <= clip_table < top):
            continue
        if not read_clip_table(overlay_path, clip_table):
            continue
        cases.append((at, clip_table, channel, offset or 0))

    # One case per master, in the order they appear.
    seen = set()
    out = {}
    for _at, clip_table, channel, offset in cases:
        key = (clip_table, channel, offset)
        if key in seen:
            continue
        seen.add(key)
        out[len(out)] = (clip_table, channel, offset)
    return out


def read_clip_table(overlay_path, offset, limit=400):
    """The (start, length) rows of one clip table, until the chain ends."""
    data = open(overlay_path, "rb").read()
    out = []
    at = offset
    expect = 0
    while at + 4 <= len(data) and len(out) < limit:
        start, length = struct.unpack_from("<HH", data, at)
        if start != expect or not (0 < length < 400):
            break
        out.append((start, length))
        expect = start + length
        at += 4
    return out


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


def clip_sectors(entry, channel, sectors=None):
    """The VOICE.XA sector numbers one clip occupies, never past the
    end of the track."""
    start, length = entry
    out = [(start + b) * BLOCK + channel for b in range(length)]
    if sectors:
        out = [s for s in out if s < sectors]
    return out


BOUNDARY_QUIET = 1500      # amplitude below which a block counts as a gap


def resolve_channels(image, lba, tables, progress=None, sectors=None):
    """Which channel each of an overlay's tables describes.

    Every entry begins where the one before ended, so on the right
    channel those block boundaries fall in the gaps between spoken
    lines; on any other they cut through its own speech. Scoring
    channels by how often the block before a boundary is quiet picks
    each table's out, usually by a wide margin.

    All 32 channels are decoded once, contiguously, and every table is
    scored against that. Contiguously matters: the ADPCM predictor
    carries state from sector to sector, so a block decoded on its own
    starts from silence and its loudness means nothing.

    Returns [channel or None] parallel to `tables`. Costs a decode of
    the whole span - tens of seconds - so callers should cache it and
    keep it off the GUI thread."""
    if not tables:
        return []
    span = max(max(s for s, _l in entries) for _off, entries in tables) + 2
    if sectors:
        # Never probe past the track. A table can reach the very last
        # block, and reading beyond it lands in whatever file follows -
        # not XA audio, and it decodes to nonsense.
        span = min(span, sectors // BLOCK)
    frame = xa.framing(image) or xa.RAW
    peaks = {}
    with open(image, "rb") as f:
        for channel in range(BLOCK):
            if progress:
                progress(channel, BLOCK)
            samples, _rate = xa.decode_channel(
                f, lba, [b * BLOCK + channel for b in range(span)],
                frame=frame)
            per = xa.SAMPLES_PER_SECTOR
            peaks[channel] = [
                max((abs(v) for v in samples[b * per:(b + 1) * per]),
                    default=0)
                for b in range(span)]

    out = []
    for _off, entries in tables:
        bounds = [s for s, _l in entries[1:]]
        if not bounds:
            out.append(None)
            continue
        best, best_score = None, -1.0
        for channel, peak in peaks.items():
            quiet = sum(1 for b in bounds
                        if b < len(peak) and peak[b - 1] < BOUNDARY_QUIET)
            score = quiet / len(bounds)
            if score > best_score:
                best, best_score = channel, score
        out.append(best)
    return out


def channels(image, lba, sectors):
    """Which channels the track carries, and how many sectors each has."""
    frame = xa.framing(image) or xa.RAW
    with open(image, "rb") as f:
        chans = xa.channel_map(f, lba, sectors, frame)
    return sorted((k[1], len(v)) for k, v in chans.items())

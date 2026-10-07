"""Putting a piece of music of your own into one of BGM.XA's slots.

WHAT THE GAME DOES WITH A TRACK

From the decomp (f_StartStreamedAudioTrack, f_RunStreamedAudioPlaybackWorker):
MAIN.EXE has a (start, length) pair for each of the 32 tracks, in
sectors of its channel. To play one the game sets the drive's filter to
the channel, seeks to the track's first sector and reads; every frame it
asks the drive where it is, and when that passes

    start + (length - 2)

it seeks back to the first sector and reads again (background music is
always started looping). There is no loop point inside a track: a loop
is the whole range, start to end, with the drive's seek for a gap. The
original tracks are made for that - each fades to nothing over its last
sectors.

WHAT THAT MEANS FOR A REPLACEMENT

    shorter than the slot   the length in the table has to shrink with
                            it, or the game plays out the silence that
                            is left before it loops. So the table is
                            part of the edit: `length` becomes the
                            sectors of music plus one, and the loop
                            comes at the music's own end
    longer                  there is no more room. The next track starts
                            where this slot ends, and a slot lasts as
                            long as the drive takes to pass it - 88
                            seconds for most - whatever is in it: a
                            cheaper coding (mono, half the rate) leaves
                            sectors unused, it does not buy time. So it
                            is cut to fit, with a fade, and the user is
                            told before it happens

The audio is XA ADPCM, 37800 Hz stereo, 2016 frames a sector
(formats/audio/xa.py). Every sector of the slot is rewritten - the
music, then digital silence to the slot's end - so nothing of the old
piece is left behind it.

WHERE AN EDIT IS KEPT

The sectors go in the same store as re-recorded dialogue
(formats/audio/voice_edit.py): raw sectors by their place on the disc,
written by Build Disc and saved in a project. The new lengths ride in
that store's `meta`, and Build Disc writes them into the MAIN.EXE it
puts on the disc.
"""
import math

from formats.audio import audio_import, bgm, xa

RATE = 37800
FRAMES = xa.SAMPLES_PER_SECTOR // 2         # stereo frames in a sector
LENGTHS = "bgm_lengths"                     # the store's meta key
SILENT = bytes(xa.GROUPS * xa.GROUP_LEN)


def conform(frames, rate):
    """Any audio as the disc wants it: stereo, 37800 Hz, and without the
    silence the file ends on."""
    return audio_import.trim_end(audio_import.resample(audio_import.stereo(frames), rate, RATE))


def seconds(piece):
    """How long a piece the slot takes. The last sector of a range is
    the one the game never reaches, so it is not counted."""
    return max(0, len(piece["room"]) - 1) * FRAMES / RATE


def fit(frames, piece):
    """(frames that fit the slot, whether they had to be cut). A cut
    piece fades over its last second."""
    most = max(0, len(piece["room"]) - 1) * FRAMES
    if len(frames) <= most:
        return frames, False
    return audio_import.fade_out(frames[:most], RATE), True


def stage(store, image, lba, piece, frames, progress=None):
    """Encode `frames` - conformed, and no longer than fit() allows -
    into the piece's slot and stage every sector of it. Returns the
    sectors of music written."""
    slot = piece["room"]
    used = max(1, math.ceil(len(frames) / FRAMES))
    if used > len(slot) - 1:
        raise ValueError("That is more music than the slot holds.")
    bodies = xa.encode_stream(frames, used, progress) + [SILENT] * (len(slot) - used)
    store.set_image(image)
    with open(image, "rb") as f:
        for index, body in zip(slot, bodies):
            f.seek((lba + index) * xa.SECTOR)
            original = f.read(xa.SECTOR)
            if len(original) != xa.SECTOR:
                raise ValueError(f"Sector {lba + index} is past the end of the disc image.")
            store.sectors[lba + index] = xa.with_audio(original, body)
    if piece["entry"] is not None:
        # with the slot's start, so the table can be checked before it is written
        store.meta.setdefault(LENGTHS, {})[str(piece["entry"])] = [piece["start"], used + 1]
    return used


def unstage(store, lba, piece):
    """Take a replacement back out: the disc's own music again."""
    for index in piece["room"]:
        store.sectors.pop(lba + index, None)
    store.meta.get(LENGTHS, {}).pop(str(piece["entry"]), None)


def staged(store, lba, piece):
    """Whether the piece's slot is waiting to be written."""
    return store is not None and bool(piece["room"]) and lba + piece["room"][0] in store.sectors


def staged_length(store, piece):
    """The sectors a replaced piece now plays, or None if it is the disc's own."""
    if store is None:
        return None
    found = store.meta.get(LENGTHS, {}).get(str(piece["entry"]))
    return found[1] if found else None


def count(store):
    """Replaced pieces of music waiting in the store."""
    return len(store.meta.get(LENGTHS, {})) if store is not None else 0


def patch_exe(exe, store):
    """`exe` with the staged pieces' lengths in its track table."""
    lengths = {int(at): tuple(pair) for at, pair in store.meta.get(LENGTHS, {}).items()}
    return bgm.set_lengths(exe, lengths) if lengths else exe

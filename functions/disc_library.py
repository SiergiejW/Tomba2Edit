"""Finding the discs already sitting in the project's own iso/ folder.

Opening the voice track normally means browsing to a bin/cue rip by
hand, every time a disc or a session is opened fresh. Most of the time
one is already on disk somewhere under the project's iso/ - this lists
the data tracks found there, so one can be picked from a menu instead
of a file dialog.

Two shapes show up under iso/, and both are handled:

    a raw dump       a .cue beside one or more .bin/.img files (or a
                      single .img carrying every track back to back, as
                      the two preview builds do) - the data track is
                      whichever TRACK the cue calls MODE2/2352, read out
                      of the cue rather than guessed at, since a
                      multi-disc dump can otherwise pick its CD-audio
                      track by mistake
    a CD-folder rip   BIN/CD/MOVIE subfolders with the files already
                      extracted - which is what the tool's own DAT/IDX
                      reading wants, but carries no raw data track for
                      voice at all (a CD-folder VOICE.XA is always a
                      plain 2048-byte-a-sector copy, unusable - see
                      functions/voice.py). find_discs() leaves a folder
                      like that out rather than pointing at a file that
                      cannot work.
"""
import os
import re

_EXTS = (".bin", ".img")

# A raw PSX data track is hundreds of megabytes; anything smaller under
# one of these names is something else entirely - an extracted CD
# folder's own small .BIN (an overlay, a sound pointer table) rather
# than a disc image, and voice audio does not survive in it anyway.
_MIN_TRACK_SIZE = 50_000_000

# How deep under one top-level folder to look for a raw dump - some of
# the rips this project keeps nest one folder deeper than themselves
# (Fooname/Fooname/Foo (Track 1).bin), so 1 is not enough.
_MAX_DEPTH = 4

# Relative to this file: functions/ -> project root -> iso.
DEFAULT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "iso"))

_CUE_FILE = re.compile(r'FILE\s+"([^"]+)"', re.IGNORECASE)
_CUE_TRACK = re.compile(r'TRACK\s+\d+\s+(\S+)', re.IGNORECASE)


def _data_track_from_cue(cue_path):
    """The data track's own file, out of a .cue - the FILE line most
    recently seen before a MODE2/2352 TRACK line names it, so a
    multi-track dump (game data + CD audio) doesn't hand back the audio
    track by mistake. None if the cue has no such track, or can't be
    read at all."""
    try:
        with open(cue_path, "r", encoding="ascii", errors="ignore") as f:
            text = f.read()
    except OSError:
        return None
    current = None
    for line in text.splitlines():
        m = _CUE_FILE.search(line)
        if m:
            current = m.group(1)
            continue
        m = _CUE_TRACK.search(line)
        if m and current and m.group(1).upper().startswith("MODE2"):
            path = os.path.join(os.path.dirname(cue_path), current)
            return path if os.path.isfile(path) else None
    return None


def _find_in(folder):
    """The best raw-dump data track under `folder`, or None.

    A .cue anywhere in the subtree wins outright, read for its own
    data track. Failing that (a dump with no cue at all), the largest
    big-enough .bin/.img is used, since that is overwhelmingly the
    data track over any CD-audio track beside it."""
    candidates = []
    for base, dirs, files in os.walk(folder):
        depth = base[len(folder):].count(os.sep)
        if depth >= _MAX_DEPTH:
            dirs[:] = []
        for fname in files:
            full = os.path.join(base, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext == ".cue":
                track = _data_track_from_cue(full)
                if track and os.path.getsize(track) >= _MIN_TRACK_SIZE:
                    return track
            elif ext in _EXTS:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size >= _MIN_TRACK_SIZE:
                    candidates.append((size, full))
    if not candidates:
        return None
    track1 = [c for c in candidates
              if "track 1" in os.path.basename(c[1]).lower()
              or "track01" in os.path.basename(c[1]).lower()]
    return max(track1 or candidates)[1]


def find_discs(root=DEFAULT_ROOT):
    """[(label, path)] - one raw data track per top-level folder of
    `root` that has one to offer, labelled by that folder's own name."""
    if not root or not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        found = _find_in(folder)
        if found:
            out.append((name, found))
    return out

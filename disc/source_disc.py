"""Finding the disc a translation project was made from.

A project holds the game's own files, not the 400 MB image they were
pulled out of. Building a playable disc needs that image back, because
the only way to keep the CD audio and the XA music is to patch a copy of
the real track rather than build a fresh ISO around it (see
formats/archive/bin_writer).

Remembering where the image was is not enough. It gets moved, renamed,
or the project is handed to someone whose copy of the disc lives
somewhere else entirely - and the old manifest stored nothing but an
absolute path, so every one of those cases ended as "no track open" with
no way to say which track was wanted. A project therefore remembers what
the disc IS as well as where it was, and a disc offered later is checked
against that before anything is written into it.

The fingerprint is the track's size plus a digest of a few sectors
spread through it. Hashing all 400 MB would also work and takes a couple
of seconds; this takes none, and the sectors it picks - the ISO9660
primary volume descriptor among them - differ between every build and
region, which is the only thing the check has to catch.

A cue sheet is the real handle for a disc, since it is the only file
that names both tracks, so anywhere a track is accepted a .cue is
accepted too and resolved to the data track it lists.
"""
import hashlib
import os
import re
import struct

# The layouts a PS1 disc image comes in: (bytes per physical sector,
# where the 2048 bytes of user data start inside one). Same list
# disc/iso9660.py detects with, kept here so a fingerprint can be
# taken without reading the whole image into memory first.
SECTOR_LAYOUTS = (
    (2048, 0),      # plain .iso
    (2352, 24),     # raw Mode 2 Form 1 - what a PS1 bin/cue data track is
    (2352, 16),     # raw Mode 1
    (2336, 8),
)

LOGICAL_SECTOR = 2048
PVD_LBA = 16                    # where ISO9660 puts the volume descriptor

# Sectors to digest. 16 is the volume descriptor (volume id, sizes,
# dates); the rest are spread through the image so two dumps of
# different games can't collide on the early structure alone.
_PROBE_LBAS = (16, 17, 23, 100, 5000, 40000, 120000)

FILTER = ("Disc image (*.cue *.bin *.img *.iso);;"
          "Cue sheet (*.cue);;All files (*)")

_FILE_RE = re.compile(r'^\s*FILE\s+"([^"]+)"|^\s*FILE\s+(\S+)', re.I)
_TRACK_RE = re.compile(r"^\s*TRACK\s+\d+\s+(\S+)", re.I)


class SourceDiscError(Exception):
    """Raised when an image can't be read or isn't the one wanted."""


# ----------------------------------------------------------------------
# Cue sheets
# ----------------------------------------------------------------------

def cue_tracks(cue_path):
    """[(absolute file path, track type)] for a cue sheet, in order.

    The type is what the TRACK line says - "MODE2/2352", "AUDIO" and so
    on - for the first track each FILE carries, which is all this needs:
    a PS1 rip puts one track in each file."""
    folder = os.path.dirname(os.path.abspath(cue_path))
    out = []
    pending = None
    with open(cue_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            match = _FILE_RE.match(line)
            if match:
                pending = match.group(1) or match.group(2)
                continue
            match = _TRACK_RE.match(line)
            if match and pending is not None:
                out.append((os.path.join(folder, pending), match.group(1).upper()))
                pending = None
    return out


def data_track(path):
    """The data track behind whatever the user picked.

    A cue sheet resolves to the first track that isn't audio; anything
    else is already the track. Raises SourceDiscError if a cue names a
    file that isn't there, which is the usual result of moving one of a
    pair of .bin files without the other."""
    if os.path.splitext(path)[1].lower() != ".cue":
        return path
    tracks = cue_tracks(path)
    if not tracks:
        raise SourceDiscError(
            f"{os.path.basename(path)} lists no tracks - it may be empty or "
            "not a cue sheet at all.")
    for track_path, kind in tracks:
        if kind.startswith("AUDIO"):
            continue
        if not os.path.exists(track_path):
            raise SourceDiscError(
                f"{os.path.basename(path)} points at "
                f"{os.path.basename(track_path)}, which isn't beside it. "
                "Both files have to travel together.")
        return track_path
    raise SourceDiscError(
        f"{os.path.basename(path)} lists only audio tracks - the data track "
        "it should name is missing.")


def cue_beside(track_path):
    """The cue sheet naming this track, if one sits beside it."""
    folder = os.path.dirname(os.path.abspath(track_path))
    if not os.path.isdir(folder):
        return None
    target = os.path.abspath(track_path)
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".cue"):
            continue
        candidate = os.path.join(folder, name)
        try:
            for listed, _kind in cue_tracks(candidate):
                if os.path.abspath(listed) == target:
                    return candidate
        except OSError:
            continue
    return None


# ----------------------------------------------------------------------
# Fingerprints
# ----------------------------------------------------------------------

def _layout(f, size):
    """(sector size, data offset) for an open image, or None."""
    for sector_size, data_offset in SECTOR_LAYOUTS:
        at = PVD_LBA * sector_size + data_offset
        if at + 6 > size:
            continue
        f.seek(at)
        if f.read(6)[1:6] == b"CD001":
            return sector_size, data_offset
    return None


def _volume_id(f, sector_size, data_offset):
    """The disc's own name, for telling the user which one they picked."""
    f.seek(PVD_LBA * sector_size + data_offset + 40)
    return f.read(32).decode("ascii", "replace").strip() or "(unnamed)"


def fingerprint(path):
    """What this track is, as a dict safe to store in a manifest.

    Raises SourceDiscError if the file isn't a disc image at all, so a
    project never records something it won't be able to build from."""
    path = data_track(path)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            layout = _layout(f, size)
            if layout is None:
                raise SourceDiscError(
                    f"{os.path.basename(path)} doesn't look like a disc "
                    "image - no ISO9660 volume descriptor in it.")
            sector_size, data_offset = layout
            digest = hashlib.sha1()
            digest.update(struct.pack("<Q", size))
            for lba in _PROBE_LBAS:
                at = lba * sector_size + data_offset
                if at + LOGICAL_SECTOR > size:
                    continue
                f.seek(at)
                digest.update(f.read(LOGICAL_SECTOR))
            volume = _volume_id(f, sector_size, data_offset)
    except OSError as exc:
        raise SourceDiscError(str(exc)) from exc
    return {
        "name": os.path.basename(path),
        "size": size,
        "sector_size": sector_size,
        "volume": volume,
        "digest": digest.hexdigest(),
    }


def matches(path, stored):
    """Whether the track at `path` is the one `stored` describes.

    A missing or malformed record matches nothing rather than
    everything: a project that never recorded its disc must ask, not
    accept whatever it is handed."""
    if not stored or not stored.get("digest"):
        return False
    try:
        return fingerprint(path)["digest"] == stored["digest"]
    except SourceDiscError:
        return False


def describe(stored):
    """One line naming the disc a project wants, for a dialog to show."""
    if not stored:
        return "the original disc image"
    name = stored.get("name") or "the original disc image"
    size = stored.get("size")
    if size:
        return "%s (%.0f MB)" % (name, size / (1024 * 1024))
    return name


def is_raw_track(stored_or_path):
    """Whether this is a raw 2352-byte track - the only kind that can be
    patched while keeping its CD audio and XA sectors."""
    if isinstance(stored_or_path, dict):
        return stored_or_path.get("sector_size") == 2352
    try:
        return fingerprint(stored_or_path)["sector_size"] == 2352
    except SourceDiscError:
        return False


# ----------------------------------------------------------------------
# Finding it again
# ----------------------------------------------------------------------

def locate(stored, hints=()):
    """Look for the recorded disc without asking anybody.

    `hints` are folders worth trying - where the project sits, where the
    disc was last seen, whatever the settings remember. The recorded
    path is tried first, then the recorded file name inside each hint,
    then any cue sheet or image in a hint whose fingerprint agrees.
    Returns a path, or None to mean the user has to be asked."""
    if not stored:
        return None
    tried = set()
    # A project saved before fingerprints existed knows only a path and
    # a file name. That can't be verified, so the name has to stand in
    # for the check - still better than asking for a disc the user has
    # sitting right beside the project.
    by_name = not stored.get("digest")
    wanted_name = (stored.get("name") or "").lower()

    def check(candidate):
        if not candidate:
            return None
        key = os.path.normcase(os.path.abspath(candidate))
        if key in tried:
            return None
        tried.add(key)
        if not os.path.isfile(candidate):
            return None
        if by_name:
            return (candidate if wanted_name
                    and os.path.basename(candidate).lower() == wanted_name
                    else None)
        return candidate if matches(candidate, stored) else None

    found = check(stored.get("path"))
    if found:
        return found

    name = stored.get("name")
    for hint in hints:
        if not hint or not os.path.isdir(hint):
            continue
        if name:
            found = check(os.path.join(hint, name))
            if found:
                return found
        for entry in sorted(os.listdir(hint)):
            if os.path.splitext(entry)[1].lower() not in (
                    ".cue", ".bin", ".img", ".iso"):
                continue
            candidate = os.path.join(hint, entry)
            try:
                resolved = data_track(candidate)
            except SourceDiscError:
                continue
            found = check(resolved)
            if found:
                return found
    return None


def record(path):
    """The manifest block for a disc, fingerprint plus where it was."""
    track = data_track(path)
    stored = fingerprint(track)
    stored["path"] = os.path.abspath(track)
    cue = cue_beside(track)
    if cue:
        stored["cue"] = os.path.abspath(cue)
    return stored

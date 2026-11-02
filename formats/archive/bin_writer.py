"""Writing edits back into a raw BIN data track.

The disc the game actually plays is a bin/cue: a 2352-byte data track
plus a second track of CD audio. Rebuilding that as an ISO throws the
streamed audio away, because a 2048-byte sector cannot hold the Mode 2
Form 2 sectors the XA music and voice live in. So edits are patched into
a copy of the track instead, and everything not edited stays byte for
byte as it was.

Two ways a file goes back:

    in place    when the new bytes fit the sectors it already occupies,
                which is the common case and touches nothing else
    appended    when it grew - the new copy goes after the end of the
                track and its directory record is repointed at it

Appending is safe here because the track ends inside the region the
filesystem gives to ZZZ.DAT, a padding file this rip does not even
contain: its directory record starts at sector 174,674 while the image
ends at 174,524. Nothing reads it.

Every sector written is put back through functions/cdsector, which
recomputes the EDC and the two parity blocks; without that the track
still mounts but no longer verifies.
"""
import mmap
import os
import shutil
import struct
import re

from disc import cdsector
from disc.iso9660 import ISO9660Reader, LOGICAL_SECTOR_SIZE

SECTOR = cdsector.SECTOR
DATA_AT = cdsector.DATA_AT


class BinWriteError(Exception):
    """Raised when a track can't be written, before anything is changed."""


def _read_sector(f, lba):
    f.seek(lba * SECTOR)
    return bytearray(f.read(SECTOR))


def _write_data(f, lba, payload):
    """Put 2048 bytes of user data into one sector and fix its parity.

    Past the end of the track there is nothing to overwrite, so a sector
    is built instead - which is what lets a grown file be appended."""
    sector = _read_sector(f, lba)
    if len(sector) < SECTOR:
        f.seek(lba * SECTOR)
        f.write(cdsector.make(lba, payload))
        return
    sector[DATA_AT:DATA_AT + LOGICAL_SECTOR_SIZE] = payload.ljust(
        LOGICAL_SECTOR_SIZE, b"\0")
    f.seek(lba * SECTOR)
    f.write(cdsector.rebuild(sector))


def _records(reader, data):
    """Every file's directory record: name -> (lba, size, record offset).

    The offset is where the record sits in the image, so its size and
    extent can be rewritten in place."""
    out = {}

    def walk(lba, size, depth=0):
        count = -(-size // LOGICAL_SECTOR_SIZE)
        at = 0
        blob = reader.read_sectors(lba, count)[:size]
        while at < len(blob):
            length = blob[at]
            if length == 0:
                nxt = ((at // LOGICAL_SECTOR_SIZE) + 1) * LOGICAL_SECTOR_SIZE
                if nxt <= at:
                    break
                at = nxt
                continue
            record = blob[at:at + length]
            if len(record) < 33:
                break
            name_len = record[32]
            raw = record[33:33 + name_len]
            entry_lba = struct.unpack("<I", record[2:6])[0]
            entry_size = struct.unpack("<I", record[10:14])[0]
            is_dir = bool(record[25] & 0x02)
            if not (name_len == 1 and raw in (b"\x00", b"\x01")):
                name = reader.clean_name(
                    raw.decode("ascii", "ignore").strip()).upper()
                if is_dir:
                    if depth < 2:
                        walk(entry_lba, entry_size, depth + 1)
                else:
                    # where this record lives in the image, in bytes
                    sector = lba + at // LOGICAL_SECTOR_SIZE
                    inside = at % LOGICAL_SECTOR_SIZE
                    out[name] = (entry_lba, entry_size, sector, inside)
            at += length

    walk(reader.root_lba, reader.root_size)
    return out


def patch_track(source, destination, replacements, progress=None):
    """Copy a data track and write new file contents into the copy.

    `replacements` is {FILENAME: bytes}. Returns a note per file saying
    whether it went in place or was appended."""
    from disc.source_disc import data_track
    source = data_track(source)
    if (os.path.normcase(os.path.abspath(source)) ==
            os.path.normcase(os.path.abspath(destination))
            or os.path.exists(destination) and os.path.samefile(source, destination)):
        raise BinWriteError("Write the patched track to a new file, not "
                            "over the one being read.")
    with open(source, "rb") as f:
        try:
            whole = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            whole = f.read()
        reader = ISO9660Reader(whole)
        if reader.sector_size == SECTOR and reader.data_offset == DATA_AT:
            records = _records(reader, whole)
            total_sectors = len(whole) // SECTOR
        else:
            records, total_sectors = None, 0
        if isinstance(whole, mmap.mmap):
            whole.close()
    if records is None:
        raise BinWriteError(
            f"{os.path.basename(source)} has {reader.sector_size}-byte "
            "sectors. Only a raw 2352-byte Mode 2 data track can be patched; an "
            "ISO has no room for the audio sectors.")

    missing = [n for n in replacements if n.upper() not in records]
    if missing:
        raise BinWriteError(f"Not on this disc: {', '.join(missing)}")

    # Where a file may grow into: after the last sector of the image.
    append_at = total_sectors
    if progress:
        progress("copying the track", 0, 1)
    shutil.copyfile(source, destination)

    notes = []
    with open(destination, "r+b") as out:
        for name, payload in replacements.items():
            key = name.upper()
            lba, size, rec_sector, rec_at = records[key]
            need = -(-len(payload) // LOGICAL_SECTOR_SIZE)
            have = -(-size // LOGICAL_SECTOR_SIZE)
            if need <= have:
                target = lba
                where = f"in place at sector {lba:,}"
            else:
                target = append_at
                append_at += need
                where = f"appended at sector {target:,}"
            for i in range(need):
                if progress:
                    progress(f"writing {key}", i, need)
                chunk = payload[i * LOGICAL_SECTOR_SIZE:
                                (i + 1) * LOGICAL_SECTOR_SIZE]
                _write_data(out, target + i, chunk)
            # the record's extent and size have to follow the data
            sector = _read_sector(out, rec_sector)
            base = DATA_AT + rec_at
            struct.pack_into("<I", sector, base + 2, target)
            struct.pack_into(">I", sector, base + 6, target)
            struct.pack_into("<I", sector, base + 10, len(payload))
            struct.pack_into(">I", sector, base + 14, len(payload))
            out.seek(rec_sector * SECTOR)
            out.write(cdsector.rebuild(sector))
            notes.append(f"{key}: {len(payload):,} bytes, {where}")
        if append_at > total_sectors:
            # CAR images declare their exact length. A grown overlay must
            # remain inside the ISO volume advertised to the emulator.
            pvd = _read_sector(out, 16)
            volume_sectors = struct.unpack_from("<I", pvd, DATA_AT + 80)[0]
            if append_at > volume_sectors:
                struct.pack_into("<I", pvd, DATA_AT + 80, append_at)
                struct.pack_into(">I", pvd, DATA_AT + 84, append_at)
                out.seek(16 * SECTOR)
                out.write(cdsector.rebuild(pvd))
    return notes


def write_sectors(destination, sectors, progress=None):
    """Drop already-built sectors into a file that already exists, at
    their own absolute positions - no copying, so this can run against
    a track patch_track() (or a caller doing the equivalent by hand)
    already wrote its own file replacements into, letting a text edit
    and a voice edit land in the same output rather than each needing
    its own separate copy of the whole track.

    `sectors` is {absolute lba: raw 2352-byte sector} rather than a
    filename - a VOICE.XA clip has no directory record of its own to
    repoint, only the sector range its table names. Each sector is
    expected to already carry its own correct EDC (see
    functions/xa.encode_full_sector), so this only writes the bytes;
    it does not touch cdsector itself."""
    with open(destination, "r+b") as out:
        for i, (lba, raw) in enumerate(sectors.items()):
            if progress:
                progress("writing voice sectors", i, len(sectors))
            if len(raw) != SECTOR:
                raise BinWriteError(f"Sector {lba} is not {SECTOR} bytes.")
            out.seek(lba * SECTOR)
            out.write(raw)
    return len(sectors)


def patch_sectors(source, destination, sectors, progress=None):
    """Copy a data track and drop already-built sectors into the copy -
    see write_sectors, which does the actual writing once the copy
    exists. The standalone case: nothing else has patched `destination`
    yet, so it starts as a plain copy of `source`."""
    if os.path.abspath(source) == os.path.abspath(destination):
        raise BinWriteError("Write the patched track to a new file, not "
                            "over the one being read.")
    if progress:
        progress("copying the track", 0, 1)
    shutil.copyfile(source, destination)
    return write_sectors(destination, sectors, progress)


def write_cue(cue_path, tracks):
    """A cue sheet for a patched track plus any audio tracks beside it.

    `tracks` is [(filename, "MODE2/2352" or "AUDIO")], in order."""
    lines = []
    for index, (name, kind) in enumerate(tracks, start=1):
        lines.append(f'FILE "{name}" BINARY')
        lines.append(f"  TRACK {index:02d} {kind}")
        if kind == "AUDIO" and index > 1:
            lines.append("    INDEX 00 00:00:00")
            lines.append("    INDEX 01 00:02:00")
        else:
            lines.append("    INDEX 01 00:00:00")
    with open(cue_path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")
    return cue_path


def copy_audio_track(source, target):
    """Bring a bin/cue's second (audio) track along, and write a cue
    sheet over both - the piece missing when only a patched data track
    gets written out, which otherwise leaves an orphan .bin with no cue
    naming it and, if the original disc had one, no music track either.

    `source` is the original data track a patch was made from (used
    only to find its own Track 2, never read otherwise); `target` is
    the patched copy already written. Returns a note about what was
    done, for a status line to show."""
    from disc.source_disc import cue_beside, data_track

    source_cue = (os.fspath(source) if os.path.splitext(source)[1].lower() == ".cue"
                  else cue_beside(source))
    source = data_track(source)
    # CAR is the full raw image consumed by the Special Edition. Its audio
    # sectors are already in the image; an invented CUE is not part of it.
    if os.path.splitext(target)[1].lower() == ".car":
        return "Saved as a Special Edition CAR image."
    if source_cue:
        return _copy_cue_tracks(source_cue, source, target)

    folder = os.path.dirname(source)
    stem = os.path.basename(source)
    audio = None
    if "Track 1" in stem:
        candidate = os.path.join(folder, stem.replace("Track 1", "Track 2"))
        if os.path.exists(candidate):
            audio = candidate
    out_dir = os.path.dirname(target)
    out_stem = os.path.splitext(os.path.basename(target))[0]
    tracks = [(os.path.basename(target), "MODE2/2352")]
    note = ""
    if audio:
        copied = os.path.join(out_dir, out_stem + " (Track 2).bin")
        if os.path.abspath(copied) != os.path.abspath(audio):
            shutil.copyfile(audio, copied)
        tracks.append((os.path.basename(copied), "AUDIO"))
        note = "The audio track was copied beside it."
    cue = write_cue(os.path.join(out_dir, out_stem + ".cue"), tracks)
    return (note + f" Cue sheet: {os.path.basename(cue)}").strip()


def _copy_cue_tracks(source_cue, source, target):
    """Copy every referenced file and retain track indices and pregaps.

    A combined BIN may hold both data and audio tracks; its later TRACK
    lines must keep referencing that same output file. Split dumps may
    call their audio files anything, so the CUE is the source of truth.
    """
    file_line = re.compile(r'^(\s*FILE\s+)(?:"([^"]+)"|(\S+))(.*)$', re.I)
    folder = os.path.dirname(os.path.abspath(source_cue))
    out_dir = os.path.dirname(os.path.abspath(target))
    out_stem = os.path.splitext(os.path.basename(target))[0]
    cue_target = os.path.join(out_dir, out_stem + ".cue")
    if os.path.normcase(os.path.abspath(source_cue)) == os.path.normcase(cue_target):
        raise BinWriteError("The output cue would overwrite the source cue. "
                            "Choose another output name or folder.")
    mapped = {os.path.normcase(os.path.abspath(source)): os.path.basename(target)}
    copies, lines = [], []
    with open(source_cue, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            match = file_line.match(line.rstrip("\r\n"))
            if match:
                listed = os.path.abspath(os.path.join(folder, match.group(2) or match.group(3)))
                key = os.path.normcase(listed)
                if key not in mapped:
                    if not os.path.isfile(listed):
                        raise BinWriteError(f"The cue's audio file is missing: {listed}")
                    suffix = os.path.splitext(listed)[1] or ".bin"
                    name = f"{out_stem} (Track {len(mapped) + 1}){suffix}"
                    copied = os.path.join(out_dir, name)
                    if (os.path.normcase(copied) != key
                            and os.path.exists(copied) and os.path.samefile(copied, listed)):
                        raise BinWriteError("An output audio file aliases its source.")
                    copies.append((listed, copied))
                    mapped[key] = name
                line = f'{match.group(1)}"{mapped[key]}"{match.group(4)}\n'
            lines.append(line)
    for listed, copied in copies:
        if os.path.normcase(listed) != os.path.normcase(copied):
            shutil.copyfile(listed, copied)
    with open(cue_target, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return (f"Copied {len(copies)} audio file(s). "
            f"Cue sheet: {os.path.basename(cue_target)} (original track indices preserved).")

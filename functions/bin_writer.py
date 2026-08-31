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

from functions import cdsector
from functions.iso9660 import ISO9660Reader, LOGICAL_SECTOR_SIZE

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
    if os.path.abspath(source) == os.path.abspath(destination):
        raise BinWriteError("Write the patched track to a new file, not "
                            "over the one being read.")
    with open(source, "rb") as f:
        try:
            whole = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            whole = f.read()
        reader = ISO9660Reader(whole)
        if reader.sector_size == SECTOR:
            records = _records(reader, whole)
            total_sectors = len(whole) // SECTOR
        else:
            records, total_sectors = None, 0
        if isinstance(whole, mmap.mmap):
            whole.close()
    if records is None:
        raise BinWriteError(
            f"{os.path.basename(source)} has {reader.sector_size}-byte "
            "sectors. Only a raw 2352-byte data track can be patched; an "
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
    return notes


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

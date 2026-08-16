"""
ISO Handler module for Tomba2Edit
Extracts TOMBA2.DAT, TOMBA2.IDX and TOMBA2.IMG from a PlayStation disc image
so they can be browsed/edited the same way as files picked from a folder.

Understands both "plain" ISO9660 images (2048 bytes of user data per sector -
the layout used by most .iso/.img rips of a PS1 disc) and raw CD sector dumps
(2352 bytes per physical sector - what you get from a bin/cue rip, or from
some tools that still call the result ".iso"). The physical sector size and
the offset of the 2048-byte user-data payload within each physical sector are
auto-detected from the disc itself, so the caller doesn't need to know the
image's format ahead of time.
"""

import os
import shutil
import struct
import tempfile

LOGICAL_SECTOR_SIZE = 2048  # ISO9660 logical block size - always 2048 bytes,
                             # independent of the physical sector size.
PVD_LBA = 16                 # The Primary Volume Descriptor always sits at
                              # logical sector 16 on an ISO9660 disc.

# (physical_sector_size, user_data_offset_within_sector), tried in order of
# how common each layout is for PS1 images.
_SECTOR_LAYOUTS = [
    (2048, 0),   # Plain ISO9660 image - no raw sector framing.
    (2352, 24),  # Raw CD-XA Mode 2/Form 1 sectors (PS1's usual data-track
                 # format): 12-byte sync + 4-byte header + 8-byte subheader,
                 # then 2048 bytes of user data, then EDC/ECC.
    (2352, 16),  # Raw Mode 1 sectors: 12-byte sync + 4-byte header.
    (2336, 8),   # "Sync-stripped" 2336-byte-sector XA dumps.
]

REQUIRED_FILES = ("TOMBA2.DAT", "TOMBA2.IDX", "TOMBA2.IMG")


class ISOFormatError(Exception):
    """Raised when the selected file doesn't look like a readable ISO9660 image."""


class ISOHandler:
    """Extracts files out of a PlayStation ISO9660 disc image."""

    def __init__(self):
        self.temp_dir = None
        self.extracted_files = {}
        self._raw = None
        self._sector_size = None
        self._data_offset = None

    # ------------------------------------------------------------------
    # Low-level sector access
    # ------------------------------------------------------------------

    def _read_logical_sectors(self, lba, count=1):
        """Return `count` logical sectors (2048 bytes each) of user data
        starting at logical block address `lba`, translated through
        whichever physical sector layout was detected for this image."""
        if count <= 0:
            return b""

        sector_size = self._sector_size
        data_offset = self._data_offset

        # Fast path: a plain ISO9660 image has no raw-sector framing, so its
        # logical sectors are already one contiguous run of bytes.
        if sector_size == LOGICAL_SECTOR_SIZE and data_offset == 0:
            start = lba * LOGICAL_SECTOR_SIZE
            end = start + count * LOGICAL_SECTOR_SIZE
            if end > len(self._raw):
                raise ISOFormatError(
                    "This ISO looks truncated - it's smaller than its own "
                    "directory data says it should be."
                )
            return self._raw[start:end]

        out = bytearray()
        for i in range(count):
            phys_start = (lba + i) * sector_size + data_offset
            phys_end = phys_start + LOGICAL_SECTOR_SIZE
            if phys_end > len(self._raw):
                raise ISOFormatError(
                    "This ISO looks truncated - it's smaller than its own "
                    "directory data says it should be."
                )
            out += self._raw[phys_start:phys_end]
        return bytes(out)

    def _detect_layout(self):
        """Find which (sector_size, data_offset) makes logical sector 16
        contain a valid Primary Volume Descriptor, and remember it."""
        for sector_size, data_offset in _SECTOR_LAYOUTS:
            phys_start = PVD_LBA * sector_size + data_offset
            phys_end = phys_start + LOGICAL_SECTOR_SIZE
            if phys_end > len(self._raw):
                continue
            candidate = self._raw[phys_start:phys_end]
            # Volume Descriptor layout: byte 0 = type (1 = Primary),
            # bytes 1-5 = the "CD001" standard identifier.
            if candidate[0] == 1 and candidate[1:6] == b"CD001":
                self._sector_size = sector_size
                self._data_offset = data_offset
                return
        raise ISOFormatError(
            "Couldn't find a Primary Volume Descriptor (CD001) in this file - "
            "it doesn't look like an ISO9660 disc image."
        )

    # ------------------------------------------------------------------
    # PVD / directory parsing
    # ------------------------------------------------------------------

    def _read_pvd(self):
        pvd = self._read_logical_sectors(PVD_LBA)
        root_record = pvd[156:156 + 34]
        root_lba = struct.unpack("<I", root_record[2:6])[0]
        root_size = struct.unpack("<I", root_record[10:14])[0]
        return root_lba, root_size

    @staticmethod
    def _clean_name(raw_name):
        """ISO9660 file identifiers end in ';<version>' (e.g.
        'TOMBA2.DAT;1'), and directories sometimes carry a trailing '.'
        when they have no extension. Strip both so names compare cleanly."""
        name = raw_name.split(";", 1)[0]
        if name.endswith("."):
            name = name[:-1]
        return name

    def _iter_directory_records(self, lba, size):
        """Yield (name, is_dir, entry_lba, entry_size) for every entry in a
        directory extent.

        Directory records are not allowed to straddle a logical-sector
        boundary, so the remainder of each sector is zero-padded. The
        original extraction code stopped scanning entirely the first time
        it saw that padding (record length 0), which silently drops every
        entry that comes after it in a directory extent spanning more than
        one sector. This instead resumes at the next sector boundary.
        """
        sector_count = -(-size // LOGICAL_SECTOR_SIZE)
        data = self._read_logical_sectors(lba, sector_count)[:size]

        offset = 0
        while offset < len(data):
            record_len = data[offset]
            if record_len == 0:
                next_sector = ((offset // LOGICAL_SECTOR_SIZE) + 1) * LOGICAL_SECTOR_SIZE
                if next_sector <= offset:
                    break
                offset = next_sector
                continue

            record = data[offset:offset + record_len]
            if len(record) < 33:
                break

            flags = record[25]
            is_dir = bool(flags & 0x02)
            name_len = record[32]
            name_bytes = record[33:33 + name_len]

            if name_len == 1 and name_bytes in (b"\x00", b"\x01"):
                name = "." if name_bytes == b"\x00" else ".."
            else:
                name = self._clean_name(name_bytes.decode("ascii", errors="ignore").strip())

            entry_lba = struct.unpack("<I", record[2:6])[0]
            entry_size = struct.unpack("<I", record[10:14])[0]

            yield name, is_dir, entry_lba, entry_size
            offset += record_len

    def _find_files(self, lba, size, wanted, depth=0):
        """Recursively search a directory extent (and its subdirectories, up
        to a sane depth) for the files named in `wanted`.
        Returns {name: (lba, size)} for whatever it found."""
        found = {}
        subdirs = []
        for name, is_dir, entry_lba, entry_size in self._iter_directory_records(lba, size):
            if is_dir:
                if name not in (".", ".."):
                    subdirs.append((entry_lba, entry_size))
                continue
            upper = name.upper()
            if upper in wanted and upper not in found:
                found[upper] = (entry_lba, entry_size)

        if len(found) < len(wanted) and depth < 8:
            for sub_lba, sub_size in subdirs:
                if len(found) == len(wanted):
                    break
                for k, v in self._find_files(sub_lba, sub_size, wanted, depth + 1).items():
                    found.setdefault(k, v)

        return found

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_iso(self, iso_path):
        """Extract TOMBA2.DAT, TOMBA2.IDX and TOMBA2.IMG from the disc image
        at `iso_path` into a fresh temp directory, and return
        {filename: extracted_path}. Raises ISOFormatError / FileNotFoundError
        on failure; the temp directory is cleaned up automatically if it does."""
        self.cleanup()
        self.temp_dir = tempfile.mkdtemp(prefix="tomba2edit_")

        try:
            with open(iso_path, "rb") as f:
                self._raw = f.read()

            self._detect_layout()
            root_lba, root_size = self._read_pvd()

            wanted = set(REQUIRED_FILES)
            locations = self._find_files(root_lba, root_size, wanted)

            missing = [name for name in REQUIRED_FILES if name not in locations]
            if missing:
                raise FileNotFoundError(
                    "Couldn't find {} inside this ISO. Make sure it's an "
                    "unmodified Tomba! 2 disc image.".format(", ".join(missing))
                )

            files_found = {}
            for name in REQUIRED_FILES:
                file_lba, file_size = locations[name]
                sector_count = -(-file_size // LOGICAL_SECTOR_SIZE)
                data = self._read_logical_sectors(file_lba, sector_count)[:file_size]
                dest_path = os.path.join(self.temp_dir, name)
                with open(dest_path, "wb") as out:
                    out.write(data)
                files_found[name] = dest_path

            self.extracted_files = files_found
            return files_found

        except Exception:
            self.cleanup()
            raise
        finally:
            self._raw = None  # don't keep the whole image in memory longer than needed

    def get_temp_dir(self):
        """Get the temporary directory path."""
        return self.temp_dir

    def get_file_path(self, filename):
        """Get the path of an extracted file."""
        return self.extracted_files.get(filename.upper())

    def cleanup(self):
        """Clean up temporary files."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"Warning: Could not clean up temp directory: {e}")
        self.temp_dir = None
        self.extracted_files = {}

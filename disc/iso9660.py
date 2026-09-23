"""
Low-level ISO9660 disc-image reading, shared by iso_handler.py (pulling
TOMBA2.DAT/IDX/IMG out of an opened disc) and iso_builder.py (rebuilding a
disc with those files replaced).

Understands both "plain" ISO9660 images (2048 bytes of user data per sector)
and raw CD sector dumps (2352/2336 bytes per physical sector - Mode 1 or
Mode 2/Form 1, what a BIN/CUE data track looks like). Sector size and the
2048-byte user-data offset within each physical sector are auto-detected
from the disc itself, so callers don't need to know the image's format
ahead of time.
"""

import struct

LOGICAL_SECTOR_SIZE = 2048  # ISO9660 logical block size - always 2048 bytes,
                             # independent of the physical sector size.
PVD_LBA = 16                 # The Primary Volume Descriptor always sits at
                              # logical sector 16 on an ISO9660 disc.

# (physical_sector_size, user_data_offset_within_sector), tried in order of
# how common each layout is for PS1 images.
SECTOR_LAYOUTS = [
    (2048, 0),   # Plain ISO9660 image - no raw sector framing.
    (2352, 24),  # Raw CD-XA Mode 2/Form 1 sectors (PS1's usual data-track
                 # format): 12-byte sync + 4-byte header + 8-byte subheader,
                 # then 2048 bytes of user data, then EDC/ECC.
    (2352, 16),  # Raw Mode 1 sectors: 12-byte sync + 4-byte header.
    (2336, 8),   # "Sync-stripped" 2336-byte-sector XA dumps.
]


class ISOFormatError(Exception):
    """Raised when a file doesn't look like a readable ISO9660 image."""


class DirEntry:
    """One decoded ISO9660 directory record."""
    __slots__ = ("name", "is_dir", "lba", "size", "flags", "date_bytes")

    def __init__(self, name, is_dir, lba, size, flags, date_bytes):
        self.name = name
        self.is_dir = is_dir
        self.lba = lba
        self.size = size
        self.flags = flags
        self.date_bytes = date_bytes

    def __repr__(self):
        return f"DirEntry({self.name!r}, is_dir={self.is_dir}, lba={self.lba}, size={self.size})"


class ISO9660Reader:
    """Read-only access to a disc image's ISO9660 filesystem."""

    def __init__(self, raw_data):
        self._raw = raw_data
        self.sector_size = None
        self.data_offset = None
        self._detect_layout()
        self.root_lba, self.root_size, self.pvd = self._read_pvd()

    # -- sector access ---------------------------------------------------

    def read_sectors(self, lba, count=1):
        """Return `count` logical sectors (2048 bytes each) of user data
        starting at logical block address `lba`."""
        if count <= 0:
            return b""

        sector_size = self.sector_size
        data_offset = self.data_offset

        # Fast path: a plain ISO9660 image has no raw-sector framing, so
        # its logical sectors are already one contiguous run of bytes.
        if sector_size == LOGICAL_SECTOR_SIZE and data_offset == 0:
            start = lba * LOGICAL_SECTOR_SIZE
            end = start + count * LOGICAL_SECTOR_SIZE
            if end > len(self._raw):
                raise ISOFormatError(
                    "This image looks truncated - it's smaller than its own "
                    "directory data says it should be."
                )
            return self._raw[start:end]

        out = bytearray()
        for i in range(count):
            phys_start = (lba + i) * sector_size + data_offset
            phys_end = phys_start + LOGICAL_SECTOR_SIZE
            if phys_end > len(self._raw):
                raise ISOFormatError(
                    "This image looks truncated - it's smaller than its own "
                    "directory data says it should be."
                )
            out += self._raw[phys_start:phys_end]
        return bytes(out)

    def read_file(self, lba, size):
        sector_count = -(-size // LOGICAL_SECTOR_SIZE) if size else 0
        return self.read_sectors(lba, sector_count)[:size]

    def read_file_lenient(self, lba, size):
        """Like read_file, but if the directory's declared extent runs
        past the actual end of the image, returns the bytes that ARE
        present zero-padded to the declared size instead of raising -
        a disc's trailing padding/filler file (e.g. "ZZZ.DAT") can be
        short in an otherwise-intact rip without affecting real game
        data. Returns (data, was_truncated) so callers can surface it."""
        try:
            return self.read_file(lba, size), False
        except ISOFormatError:
            pass

        sector_count = -(-size // LOGICAL_SECTOR_SIZE) if size else 0
        out = bytearray()
        for i in range(sector_count):
            phys_start = (lba + i) * self.sector_size + self.data_offset
            phys_end = phys_start + LOGICAL_SECTOR_SIZE
            if phys_end > len(self._raw):
                break
            out += self._raw[phys_start:phys_end]
        out = out[:size]
        if len(out) < size:
            out += b"\x00" * (size - len(out))
        return bytes(out), True

    def _detect_layout(self):
        for sector_size, data_offset in SECTOR_LAYOUTS:
            phys_start = PVD_LBA * sector_size + data_offset
            phys_end = phys_start + LOGICAL_SECTOR_SIZE
            if phys_end > len(self._raw):
                continue
            candidate = self._raw[phys_start:phys_end]
            # Volume Descriptor layout: byte 0 = type (1 = Primary),
            # bytes 1-5 = the "CD001" standard identifier.
            if candidate[0] == 1 and candidate[1:6] == b"CD001":
                self.sector_size = sector_size
                self.data_offset = data_offset
                return
        raise ISOFormatError(
            "Couldn't find a Primary Volume Descriptor (CD001) in this file - "
            "it doesn't look like an ISO9660 disc image."
        )

    def _read_pvd(self):
        pvd = self.read_sectors(PVD_LBA)
        root_record = pvd[156:156 + 34]
        root_lba = struct.unpack("<I", root_record[2:6])[0]
        root_size = struct.unpack("<I", root_record[10:14])[0]
        return root_lba, root_size, pvd

    # -- directory parsing -------------------------------------------------

    @staticmethod
    def clean_name(raw_name):
        """ISO9660 file identifiers end in ';<version>' (e.g.
        'TOMBA2.DAT;1'), and directories sometimes carry a trailing '.'
        when they have no extension. Strip both so names compare cleanly."""
        name = raw_name.split(";", 1)[0]
        if name.endswith("."):
            name = name[:-1]
        return name

    def iter_directory_records(self, lba, size):
        """Yield a DirEntry for every entry (including '.' and '..') in a
        directory extent.

        Directory records are not allowed to straddle a logical-sector
        boundary, so the remainder of each sector is zero-padded; a record
        length of 0 means "no more records in this sector", not "no more
        records at all" - this resumes at the next sector boundary instead
        of stopping the whole scan, so directories spanning more than one
        sector are read completely.
        """
        sector_count = -(-size // LOGICAL_SECTOR_SIZE)
        data = self.read_sectors(lba, sector_count)[:size]

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

            date_bytes = bytes(record[18:25])
            flags = record[25]
            is_dir = bool(flags & 0x02)
            name_len = record[32]
            name_bytes = record[33:33 + name_len]

            if name_len == 1 and name_bytes in (b"\x00", b"\x01"):
                name = "." if name_bytes == b"\x00" else ".."
            else:
                name = self.clean_name(name_bytes.decode("ascii", errors="ignore").strip())

            entry_lba = struct.unpack("<I", record[2:6])[0]
            entry_size = struct.unpack("<I", record[10:14])[0]

            yield DirEntry(name, is_dir, entry_lba, entry_size, flags, date_bytes)
            offset += record_len

    def list_directory(self, lba, size):
        """Entries in one directory, with '.' and '..' excluded."""
        return [e for e in self.iter_directory_records(lba, size) if e.name not in (".", "..")]

    def find_files(self, lba, size, wanted, depth=0, max_depth=8):
        """Recursively search a directory extent (and its subdirectories, up
        to `max_depth`) for the (uppercased) names in `wanted`.
        Returns {name: (lba, size)} for whatever it found."""
        found = {}
        subdirs = []
        for e in self.iter_directory_records(lba, size):
            if e.is_dir:
                if e.name not in (".", ".."):
                    subdirs.append(e)
                continue
            upper = e.name.upper()
            if upper in wanted and upper not in found:
                found[upper] = (e.lba, e.size)

        if len(found) < len(wanted) and depth < max_depth:
            for e in subdirs:
                if len(found) == len(wanted):
                    break
                for k, v in self.find_files(e.lba, e.size, wanted, depth + 1, max_depth).items():
                    found.setdefault(k, v)

        return found

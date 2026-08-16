"""
Rebuilds a complete, plain ISO9660 disc image (2048 bytes/sector) from an
already-opened Tomba! 2 disc image, with one or more files' contents
replaced (typically the repacked TOMBA2.DAT and TOMBA2.IDX produced after
editing TXTD text).

Every other file and directory on the disc - including nested subfolders
like a MOVIE directory - is carried over unchanged. Files that keep their
original size stay put; anything whose size changes (a repacked TOMBA2.DAT
almost always does) gets a fresh directory record pointing at newly
allocated space, so nothing downstream has to shift around like it would
on a real, contiguous CD-ROM. The PS1 finds files by walking this directory
structure at run time rather than reading hardcoded sector numbers, so
relocating a file this way is safe (this is also exactly what the existing
"copy the modified TOMBA2.DAT/IDX back into the CD folder, then re-master"
workflow relies on).

Caveat: this always produces a single-track, data-only .iso. If the source
was a multi-track BIN/CUE with CD-audio tracks, those tracks are not part
of the ISO9660 filesystem and can't be carried over - the output plays
correctly but without redbook audio. Anyone who needs the music should keep
using their BIN/CUE for playback and only use the rebuilt ISO to check the
TXTD edits landed correctly, or re-apply the same DAT/IDX replacement to
their BIN's data track with other tools if they need audio + edits together.
"""

import struct

from functions.iso9660 import ISO9660Reader, ISOFormatError, LOGICAL_SECTOR_SIZE

SYSTEM_AREA_SECTORS = 16  # LBA 0-15, reserved/unused on ISO9660 discs.
PVD_LBA = 16
TERMINATOR_LBA = 17
PATH_TABLE_START_LBA = 18


class _FileNode:
    def __init__(self, name, orig_lba, orig_size, flags, date_bytes, content=None):
        self.name = name
        self.orig_lba = orig_lba
        self.flags = flags
        self.date_bytes = date_bytes
        self.content = content  # bytes if this file is being replaced, else None
        self.size = len(content) if content is not None else orig_size
        self.orig_size = orig_size
        self.lba = None  # assigned during layout

    @property
    def sectors(self):
        return -(-self.size // LOGICAL_SECTOR_SIZE) if self.size else 0


class _DirNode:
    def __init__(self, name, orig_lba, orig_size, flags, date_bytes, parent=None):
        self.name = name
        self.orig_lba = orig_lba
        self.orig_size = orig_size
        self.flags = flags
        self.date_bytes = date_bytes
        self.parent = parent
        self.children = []  # _DirNode / _FileNode, in original directory order
        self.lba = None
        self.size = None  # assigned once this directory's own bytes are packed
        self.path_index = None  # 1-based index into the path table

    @property
    def sectors(self):
        return -(-self.size // LOGICAL_SECTOR_SIZE) if self.size else 0


def _build_tree(reader, replacements, lba, size, name="", flags=0x02, date_bytes=None, parent=None):
    node = _DirNode(name, lba, size, flags, date_bytes or b"\x00" * 7, parent=parent)
    for e in reader.iter_directory_records(lba, size):
        if e.name in (".", ".."):
            continue
        if e.is_dir:
            child = _build_tree(reader, replacements, e.lba, e.size, e.name, e.flags, e.date_bytes, parent=node)
        else:
            child = _FileNode(e.name, e.lba, e.size, e.flags, e.date_bytes,
                               content=replacements.get(e.name.upper()))
        node.children.append(child)
    return node


def _name_bytes_for_record(name, is_dir):
    if is_dir:
        return name.encode("ascii", errors="ignore")
    # Files always carry an explicit version suffix on-disk; ';1' is what
    # essentially every real PS1 disc (and every mastering tool) uses.
    return f"{name};1".encode("ascii", errors="ignore")


def _record_length(name_bytes):
    length = 33 + len(name_bytes)
    if length % 2 != 0:
        length += 1
    return length


def _pack_record(name_bytes, is_dir, lba, size, flags, date_bytes):
    rec = bytearray()
    rec += b"\x00"  # length placeholder, filled in below
    rec += b"\x00"  # extended attribute record length
    rec += struct.pack("<I", lba) + struct.pack(">I", lba)
    rec += struct.pack("<I", size) + struct.pack(">I", size)
    rec += date_bytes
    rec += bytes([(flags | 0x02) if is_dir else (flags & ~0x02)])
    rec += b"\x00"  # file unit size
    rec += b"\x00"  # interleave gap size
    rec += struct.pack("<H", 1) + struct.pack(">H", 1)  # volume sequence number
    rec += bytes([len(name_bytes)])
    rec += name_bytes
    if len(name_bytes) % 2 == 0:
        rec += b"\x00"  # padding byte to keep the record an even length
    rec[0] = len(rec)
    return bytes(rec)


def _pack_directory(node):
    """Build this directory's own extent bytes: '.', '..', then each child,
    packed sector by sector without letting any record straddle a
    boundary. Requires every child (and this node itself, and its parent)
    to already have an LBA/size assigned - pass lba=0 placeholders for a
    sizing-only pass, since record length never depends on the LBA value."""
    parent = node.parent or node  # root's ".." points back at itself

    entries = [
        (b"\x00", True, node.lba, node.size, node.flags, node.date_bytes),
        (b"\x01", True, parent.lba, parent.size, parent.flags, parent.date_bytes),
    ]
    for child in node.children:
        is_dir = isinstance(child, _DirNode)
        name_bytes = _name_bytes_for_record(child.name, is_dir)
        entries.append((name_bytes, is_dir, child.lba, child.size, child.flags, child.date_bytes))

    sectors = []
    cur = bytearray()
    for name_bytes, is_dir, lba, size, flags, date_bytes in entries:
        rec = _pack_record(name_bytes, is_dir, lba or 0, size or 0, flags, date_bytes)
        if len(cur) + len(rec) > LOGICAL_SECTOR_SIZE:
            cur += b"\x00" * (LOGICAL_SECTOR_SIZE - len(cur))
            sectors.append(bytes(cur))
            cur = bytearray()
        cur += rec
    cur += b"\x00" * (LOGICAL_SECTOR_SIZE - len(cur))
    sectors.append(bytes(cur))
    return b"".join(sectors)


def _pack_path_table(dirs_in_order, big_endian):
    u16 = ">H" if big_endian else "<H"
    u32 = ">I" if big_endian else "<I"
    out = bytearray()
    for d in dirs_in_order:
        name_bytes = b"\x00" if d.parent is None else d.name.encode("ascii", errors="ignore")
        out += bytes([len(name_bytes)])
        out += b"\x00"  # extended attribute record length
        out += struct.pack(u32, d.lba)
        out += struct.pack(u16, d.parent.path_index if d.parent else 1)
        out += name_bytes
        if len(name_bytes) % 2 != 0:
            out += b"\x00"
    return bytes(out)


def build_iso(original_path, replacements, output_path):
    """Rebuild `original_path` as a plain ISO9660 image at `output_path`,
    substituting file contents from `replacements` ({FILENAME: bytes},
    matched case-insensitively, version suffix ignored). Every other file
    and directory is carried over byte-for-byte. Raises ISOFormatError /
    OSError on failure."""
    replacements = {k.upper(): v for k, v in replacements.items()}

    with open(original_path, "rb") as f:
        raw = f.read()
    reader = ISO9660Reader(raw)

    root = _build_tree(reader, replacements, reader.root_lba, reader.root_size)

    # ---- collect every directory (root first, breadth-first) and every
    #      file, in the fixed order we'll both size, lay out, and write. ----
    all_dirs = [root]
    all_files = []
    queue = [root]
    while queue:
        d = queue.pop(0)
        for child in d.children:
            if isinstance(child, _DirNode):
                all_dirs.append(child)
                queue.append(child)
            else:
                all_files.append(child)

    for i, d in enumerate(all_dirs, start=1):
        d.path_index = i

    # ---- each directory's own extent size depends only on its children's
    #      names (not on any LBA value), so this can happen before layout. ----
    for d in all_dirs:
        d.lba, d.size = 0, 0
    for d in all_dirs:
        d.size = len(_pack_directory(d))

    l_path_table = _pack_path_table(all_dirs, big_endian=False)
    path_table_size = len(l_path_table)
    path_table_sectors = -(-path_table_size // LOGICAL_SECTOR_SIZE) if path_table_size else 1

    l_path_lba = PATH_TABLE_START_LBA
    m_path_lba = l_path_lba + path_table_sectors

    # ---- assign real LBAs: all directories first (root first), then all
    #      files, in the same order used above/below for sizing & writing. ----
    cursor = m_path_lba + path_table_sectors
    for d in all_dirs:
        d.lba = cursor
        cursor += d.sectors
    for fnode in all_files:
        fnode.lba = cursor
        cursor += fnode.sectors
    total_sectors = cursor

    # ---- now that every LBA is final, rebuild path tables + directory
    #      bytes for real, and prepare the PVD. ----
    l_path_table = _pack_path_table(all_dirs, big_endian=False)
    m_path_table = _pack_path_table(all_dirs, big_endian=True)

    pvd = bytearray(reader.pvd)
    pvd[80:84] = struct.pack("<I", total_sectors)
    pvd[84:88] = struct.pack(">I", total_sectors)
    pvd[132:136] = struct.pack("<I", path_table_size)
    pvd[136:140] = struct.pack(">I", path_table_size)
    pvd[140:144] = struct.pack("<I", l_path_lba)
    pvd[144:148] = struct.pack("<I", 0)
    pvd[148:152] = struct.pack(">I", m_path_lba)
    pvd[152:156] = struct.pack(">I", 0)
    root_record = _pack_record(b"\x00", True, root.lba, root.size, root.flags, root.date_bytes)
    pvd[156:156 + len(root_record)] = root_record

    terminator = bytearray(LOGICAL_SECTOR_SIZE)
    terminator[0] = 255
    terminator[1:6] = b"CD001"
    terminator[6] = 1

    # ---- write everything out, in the same order LBAs were handed out. ----
    with open(output_path, "wb") as out:
        out.write(b"\x00" * (SYSTEM_AREA_SECTORS * LOGICAL_SECTOR_SIZE))
        out.write(bytes(pvd))
        out.write(bytes(terminator))
        out.write(l_path_table.ljust(path_table_sectors * LOGICAL_SECTOR_SIZE, b"\x00"))
        out.write(m_path_table.ljust(path_table_sectors * LOGICAL_SECTOR_SIZE, b"\x00"))

        for d in all_dirs:
            data = _pack_directory(d)
            out.write(data.ljust(d.sectors * LOGICAL_SECTOR_SIZE, b"\x00"))

        for fnode in all_files:
            if fnode.content is not None:
                data = fnode.content
            else:
                data = reader.read_file(fnode.orig_lba, fnode.orig_size)
            out.write(data.ljust(fnode.sectors * LOGICAL_SECTOR_SIZE, b"\x00"))

    return output_path

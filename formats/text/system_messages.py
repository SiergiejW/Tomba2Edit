"""The resident System message table (System chunk file 3).

f_QueueResourceMessage (script command 40, modes 0/1) shows entry N of it:
g_MessageOffsetTable = file + 16, g_MessageTextBase = that + first u16
halfwords. The second u16 is the entry count. Item messages ("acquired!",
"used!", ...) and stat messages ("Max Vitality increased by 1!") live here.
"""
import struct

RESIDENT_CHUNK = 1
FILE_ID = 3
TABLE = 16


def parse(blob):
    """[text] in entry order, control bytes as {$XX} like the TXTD reader."""
    from formats.text.tombadict import letters
    room, count = struct.unpack_from("<HH", blob, 0)
    base = TABLE + room * 2
    offsets = struct.unpack_from(f"<{count}H", blob, TABLE)
    out = []
    for offset in offsets:
        at, text = base + offset, []
        while at < len(blob) and blob[at] != 0xFF:
            byte = blob[at]
            text.append(letters[byte] if byte in letters else "{$%02X}" % byte)
            at += 1
        out.append("".join(text))
    return out


def load(dat_path, idx_path):
    from gui.level.level_scene import area_files
    start, files = area_files(idx_path, RESIDENT_CHUNK)
    where = {fid: (offset, size) for _, fid, offset, size in files}.get(FILE_ID)
    if where is None:
        raise ValueError("The System chunk has no message table (file 3).")
    offset, size = where
    with open(dat_path, "rb") as source:
        source.seek(start + offset)
        return parse(source.read(size))

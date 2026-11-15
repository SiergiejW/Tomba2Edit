"""Signposts: resident handler 0x80073CD8 objects (kind 15).

Tomba reads one with the action button; the handler spawns a message actor
(0x8007E110) showing entry `table[slot]` of the sign bank - System chunk
file 2, read as the game reads it: a 4-byte table at +16 (offset, aux), text
at 16 + 4 * first u16, entries ended by 0xFF. The id table sits in MAIN.EXE
at 0x800A4CA8, one halfword per slot."""
import struct

HANDLER = 0x80073CD8
ID_TABLE = 0x800A4CA8
SIGN_CHUNK, SIGN_FILE = 1, 2
SLOTS = range(0, 34)


def sign_bank(dat_path, idx_path):
    """[text] for every entry of the sign bank, control bytes as {$XX}."""
    from gui.level.level_scene import area_files
    from formats.text.tombadict import letters
    start, files = area_files(idx_path, SIGN_CHUNK)
    where = {fid: (offset, size) for _, fid, offset, size in files}.get(SIGN_FILE)
    if where is None:
        raise ValueError('The System chunk has no sign bank (file 2).')
    with open(dat_path, 'rb') as source:
        source.seek(start + where[0])
        blob = source.read(where[1])
    count = struct.unpack_from('<H', blob, 0)[0]
    base = 16 + count * 4
    out = []
    for n in range(count):
        at = base + struct.unpack_from('<H', blob, 16 + 4 * n)[0]
        text = []
        while at < len(blob) and blob[at] != 0xFF:
            byte = blob[at]
            text.append(letters[byte] if byte in letters else '{$%02X}' % byte)
            at += 1
        out.append(''.join(text))
    return out


def sign_id(exe, slot):
    """The sign bank entry a signpost of scene-record `slot` shows, or None."""
    if slot not in SLOTS:
        return None
    main = struct.unpack_from('<I', exe, 24)[0]
    at = ID_TABLE - main + 0x800 + 2 * slot
    return struct.unpack_from('<h', exe, at)[0] if at + 2 <= len(exe) else None


def sign_text(exe, bank, slot):
    """(id, text) of the sign for `slot`, or None."""
    number = sign_id(exe, slot)
    if number is None or not 0 <= number < len(bank):
        return None
    return number, bank[number]

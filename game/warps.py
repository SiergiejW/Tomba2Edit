"""Where Tomba arrives: the game's warp-spawn table.

MAIN.EXE keeps, for each of the nine resident areas (0-8), a list of 8-byte
records the area-entry code places Tomba from (f_InitializeTombaAreaEntryState,
f_SnapshotCurrentWarpSpawnForSave): x, height, depth (i16 game units) and a
flags word whose bits 9-10 pick which named section of the area the spawn is
in. The spawn index is byte 1 of src_CurrentArea - a DuckStation state taken
in the pipe area read area 1, spawn 31, and Tomba stood exactly on record 31.

The tables are laid end to end, so a table's length is the distance to the
next one's start. US retail addresses.
"""
import struct

POINTERS = 0x800A54A8            # src_PtrWarpSpawnCoordinates: one pointer per area
SECTION_BASE = 0x800A55B0        # g_AreaSectionBaseIndices: a byte per area
SECTION_NAMES = 0x800A2B84       # src_PtrSectionData: string pointers
AREAS = 9
RECORD = 8


def _at(exe, base, address, size):
    off = address - base + 0x800
    return exe[off:off + size] if 0x800 <= off <= len(exe) - size else None


def _string(exe, base, address):
    off = address - base + 0x800
    if not 0x800 <= off < len(exe): return ''
    end = exe.find(b'\0', off, off + 64)
    return exe[off:end if end > 0 else off + 32].decode('latin-1', 'replace').replace('\n', ' ').strip()


def spawns(exe, area):
    """[(index, x, height, depth, flags, section name)] for resident area `area`
    (0-8); [] for any other, or an exe that is not the verified US layout."""
    if not 0 <= area < AREAS: return []
    base = struct.unpack_from('<I', exe, 24)[0]
    raw = _at(exe, base, POINTERS, 4 * AREAS)
    if raw is None: return []
    starts = struct.unpack('<%dI' % AREAS, raw)
    mine = starts[area]
    if not 0x80000000 <= mine < 0x80200000: return []
    later = sorted({s for s in starts if s > mine})
    count = (later[0] - mine) // RECORD if later else 16
    out = []
    section_base = (_at(exe, base, SECTION_BASE + area, 1) or b'\0')[0]
    for k in range(min(count, 64)):
        rec = _at(exe, base, mine + k * RECORD, RECORD)
        if rec is None: break
        x, height, depth, flags = struct.unpack('<hhhH', rec)
        pointer = struct.unpack('<I', _at(exe, base, SECTION_NAMES + 4 * (section_base + ((flags & 0x600) >> 9)), 4) or bytes(4))[0]
        out.append((k, x, height, depth, flags, _string(exe, base, pointer) if pointer else ''))
    return out

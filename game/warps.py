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


DESTINATION = 0x800BF83A  # u16: high byte area, low byte arrival index


def destinations(exe, overlay, handler, interior=None, area=None):
    """Native destinations on reachable paths; conditions remain alternatives.

    Follow the same bounded control flow as the event inspector. Only literal
    writes to the destination word are accepted, never nearby arrival points.
    """
    from game.event_script import Flow
    class EntranceFlow(Flow):
        def _table(self, image, at, reg):
            targets = super()._table(image, at, reg)
            # A06 dispatches all doors through this handler. Following every
            # selector mixes mine/tower warps with unrelated interior doors.
            if (area == 6 and handler == 0x8011FE54 and interior is not None
                    and at in (0x801200A8, 0x801202A8)):
                return targets[interior:interior + 1] if 0 <= interior < len(targets) else []
            return targets
    flow = EntranceFlow(exe, overlay, handler)
    out = set()
    # Coal Mining Town's door controller uses a door selector, NOT the
    # destination interior. FUN_A02__8011718c initializes interior_only;
    # FUN_A02__80116c14 checks it and writes that room to src_Interior.
    # Selector 13 has a separate hint-only path and is not confirmed here.
    if area == 2 and handler == 0x8011718C and interior is not None:
        rooms = {0: 0, 1: 1, 2: 2, 3: 4, 4: 4, 5: 4, 6: 4,
                 7: 6, 8: 6, 9: 5, 10: 5, 11: 3, 12: 7}
        if interior in rooms:
            out.add(('interior', rooms[interior], 0, ''))
    for at in flow.preds:
        image = flow.image(at)
        if image is None:
            continue
        ins = image.at(at)
        if ins.name == 'sh':
            addresses = flow.before(ins.rs, at, 0, set())
            if not addresses or any(v is None or (v[0] + ins.imm) & 0xffffffff != DESTINATION
                                    for v in addresses):
                continue
            values = flow.before(ins.rt, at, 0, set())
            if not values or any(v is None for v in values):
                continue
            for value in values:
                packed = value[0] & 0xffff
                target_area, arrival = packed >> 8, packed & 255
                from game import labels
                import json
                from pathlib import Path
                names = json.loads((Path(labels.labels_dir()) / 'us-retail.json').read_text(encoding='utf-8'))['bins']
                tag = 'A0' + '0123456789ABCDEFGHIJKL'[target_area] + '.BIN' if target_area < 22 else ''
                if tag not in names:
                    continue
                if target_area < AREAS and arrival >= len(spawns(exe, target_area)):
                    continue
                out.add(('area', target_area, arrival, names[tag]))
        elif ins.name == 'jal' and interior is not None:
            # Verified shared entry helper, US retail.
            if ins.target in (0x800732C0, 0x80073328):
                out.add(('interior', interior, 0, ''))
    return sorted(out)


def destination_label(destination):
    kind, number, arrival, name = destination
    if kind == 'interior':
        return f'warp: room {number}'
    return f'warp: area {number}, {name}'


def destination_chunk(area, source_chunk, available):
    """Preserve the loaded scene's purified version where a counterpart exists."""
    base = area + 4
    purified = base + 22
    if 0x1B <= source_chunk <= 0x22 and 1 <= area <= 8 and purified in available:
        return purified
    return base if base in available else None

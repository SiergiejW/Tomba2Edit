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
    # The last area has no following table to bound it. Its records stay
    # plausible well past 16 (arrival 0x13 is the Water Temple's deep entry).
    count = (later[0] - mine) // RECORD if later else 40
    out = []
    section_base = (_at(exe, base, SECTION_BASE + area, 1) or b'\0')[0]
    for k in range(min(count, 64)):
        rec = _at(exe, base, mine + k * RECORD, RECORD)
        if rec is None: break
        x, height, depth, flags = struct.unpack('<hhhH', rec)
        pointer = struct.unpack('<I', _at(exe, base, SECTION_NAMES + 4 * (section_base + ((flags & 0x600) >> 9)), 4) or bytes(4))[0]
        out.append((k, x, height, depth, flags, _string(exe, base, pointer) if pointer else ''))
    return out


SECTIONS = {}  # (area, arrival) -> the section name of that spawn record, noted as destinations are found
DESTINATION = 0x800BF83A  # u16: high byte area, low byte arrival index


def _field_destinations(flow, image, at, store):
    """The values a destination store can write when it is `field | constant`, with the
    field one the (slot-filtered) flow fills with constants; [] if it is not that."""
    constant = None
    field = None
    for back in range(1, 9):
        before = image.at(at - back * 4) if at - back * 4 in image else None
        if before is None:
            break
        if constant is None and before.name == 'ori' and before.rt == store.rt:
            constant = before.unsigned
            source = before.rs
        elif constant is not None and before.name in ('lhu', 'lh', 'lbu') and before.rt == source:
            field = before.imm
            break
    if constant is None or field is None:
        return []
    found = set()
    for place in flow.seen:
        im = flow.image(place)
        i = im.at(place) if im is not None else None
        if i is None or i.name not in ('sh', 'sb') or i.imm != field:
            continue
        values = flow.before(i.rt, place, 0, set()) if i.rt else [(0, None, None)]
        for v in values or []:
            if v is not None and v[1] is None and v[2] is None:
                found.add((v[0] | constant) & 0xffff)
    return sorted(found)


def destinations(exe, overlay, handler, interior=None, area=None, scene=None, slot=None):
    """Native destinations on reachable paths; conditions remain alternatives.

    Follow the same bounded control flow as the event inspector. Only literal
    writes to the destination word are accepted, never nearby arrival points.
    """
    # Resident pickup/message/render routines are not entrance controllers.
    # In particular the chest handler can reach unrelated area callbacks.
    if handler < 0x80108F9C and handler not in (0x800732C0, 0x80073328):
        return []
    from game.event_script import Flow
    def area_name(number):
        from game import labels
        import json
        from pathlib import Path
        names = json.loads((Path(labels.labels_dir()) / 'us-retail.json').read_text(encoding='utf-8'))['bins']
        tag = 'A0' + '0123456789ABCDEFGHIJKL'[number] + '.BIN' if 0 <= number < 22 else ''
        return names.get(tag, '')
    def room_destination(number):
        return ('interior', number, 0, '') if scene is None else ('area', area, -1, area_name(area))
    # A08 has fourteen selectors in one handler. Its custom state machines
    # use 0..9 as rooms; 10 is the mine, 11 the pig-nose mechanism,
    # 12 Golden Tower, and 13 the raft to Fishermen's Town.
    if area == 8 and handler == 0x8011F16C:
        if interior is None:
            return []
        selector = 2 if interior == 102 else interior
        if 0 <= selector <= 9:
            return [room_destination(selector)]
        packed = {10: (1, 30), 12: (19, 0), 13: (0, 10)}.get(selector)
        return [('area', packed[0], packed[1], area_name(packed[0]))] if packed else []
    # A07's door actors (FUN_A07__8011a398) have no shared-helper call: the
    # state routine at 0x801199C0 stores actor +3 (the slot) into src_Interior
    # (0x800BF817) and sets src_InteriorTransition. Only slot 4 (0x80119DA0)
    # writes an area change; slots 5 and 12 run their own routines.
    if handler == 0x8011A398 and area == 7 and interior is not None:
        if interior in (1, 2, 3, 6, 7, 8, 9, 10, 11):
            return [room_destination(interior)]
    class EntranceFlow(Flow):
        def _table(self, image, at, reg):
            targets = super()._table(image, at, reg)
            # A jump table indexed by the actor's slot (actor +3, perhaps less a
            # constant) is one door's own routine, not every door's.
            if slot is not None and targets:
                lines = [image.at(at - n * 4) for n in range(1, 24) if at - n * 4 in image]
                k = next((n for n, i in enumerate(lines) if i.name == 'lbu' and i.imm == 3), None)
                if k is not None:
                    shift = next((i.imm for i in reversed(lines[:k])
                                  if i.name == 'addiu' and i.rs == lines[k].rt and i.imm < 0), 0)
                    index = slot + shift
                    return targets[index:index + 1] if 0 <= index < len(targets) else []
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
            out.add(room_destination(rooms[interior]))
    enters_room = False
    pending = []
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
                # `lhu rX, F(actor); ori rt, rX, C; sh rt, destination`: the arrival is a
                # field the slot's own init routine stored (constants), or'd with C.
                values = [(v,) for v in _field_destinations(flow, image, at, ins)]
                if not values:
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
                rows = spawns(exe, target_area) if target_area < AREAS else []
                section = rows[arrival][5] if arrival < len(rows) else ''
                if section:
                    SECTIONS[(target_area, arrival)] = section
                pending.append((at, ('area', target_area, arrival, names[tag])))
        elif ins.name == 'sb' and interior is not None:
            # src_Interior (0x800BF817) written: this routine enters a room - the
            # slot's own, as the door controllers store actor +3 there.
            addresses = flow.before(ins.rs, at, 0, set())
            if addresses and all(v is not None and (v[0] + ins.imm) & 0xffffffff == 0x800BF817
                                 for v in addresses):
                enters_room = True
        elif ins.name == 'jal' and interior is not None:
            # Verified shared entry helper, US retail.
            if ins.target in (0x800732C0, 0x80073328):
                out.add(room_destination(interior))
    # A store the very next lines overwrite (a default, then the real value) is not a destination.
    out.update(d for at, d in pending if not any(0 < later - at <= 0x20 for later, _ in pending))
    if enters_room and not (area == 2 and handler == 0x8011718C):
        out.add(room_destination(interior))
        if scene is None:
            # Outside, a same-area arrival is the way back out of that room, not a destination.
            out = {d for d in out if not (d[0] == 'area' and d[1] == area)}
    return sorted(out)


def destination_label(destination):
    kind, number, arrival, name = destination
    if kind == 'interior':
        return f'warp: room {number}'
    section = SECTIONS.get((number, arrival))
    return (f'warp: area {number}, {name}' + (f' - {section}' if section and section not in name else '')
            + (f' (arrival {arrival})' if arrival > 0 and section else '')
            + (' (main area)' if arrival == -1 else ''))


def destination_chunk(area, source_chunk, available):
    """Preserve the loaded scene's purified version where a counterpart exists."""
    base = area + 4
    purified = base + 22
    if 0x1B <= source_chunk <= 0x22 and 1 <= area <= 8 and purified in available:
        return purified
    return base if base in available else None

"""Which NPC and object handlers depend on items.

A static scan of each handler's reachable code (the same Flow the event
inspector uses, callees in the overlay included) for:
  has    - reads of the inventory table (0x800BFAB4 + item id)
  gives  - calls of the add-item routines with a constant item id
  takes  - calls of the remove-item routines with a constant item id
  routine- called overlay routines whose decomp name says they handle an item
  used   - item ids compared with the item Tomba has just used (src_LastUsedItem)
  script - item ids in the pages' own commands (Tomba uses / is given an item,
           item messages)
It reports what the code does, not when: a read can be a test or a display."""
import re
import struct

from game import event_script as script

SPAWN_LINKED = {0x8006FB0C, 0x8006FF10}   # f_SpawnAreaLinkedItemPickupActor / f_SpawnLinkedItemPickupActor (a1 = item id)
GIVES = {0x8004D4C4: 'with message', 0x8004D4F4: 'without message', 0x8004D338: 'quantity'}
TAKES = {0x8004D604: 'with message', 0x8004D650: 'without message', 0x8004D688: 'with alt message'}
ITEM_ROUTINE = re.compile(r'Item|Inventory', re.I)
# A global's high half is loaded just before its read; a base register carried across paths
# (an actor pointer in s0 that one path set to a global) is not a global read.
NEAR = 40                       # the used-item byte sits beside a struct an actor register can be mistaken for
NEAR_INVENTORY = 400            # an inventory address is not mistaken for anything, but its base may be loaded earlier
LAST_USED_ITEM = script.LAST_USED_ITEM   # src_LastUsedItem; script.USING_ITEM is src_UsingItem2 (its state)


def _symbols(tag):
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'decomp' / 'symbols_us.json'
    names = {}
    for name, value in json.loads(path.read_text(encoding='utf-8')).items():
        if isinstance(value, list) and len(value) == 2 and value[0] == tag:
            names[value[1]] = name
    return names


def _compared_with(image, at, reg, window=24):
    """(ids, ranges): the constants register `reg` (just loaded at `at`) is compared
    with while it still holds the value, and the (low, high) bounds of a pair of
    less-than tests (an item id range such as 64..68)."""
    consts, found, lows, shifted, ranges = {}, set(), set(), {}, set()
    for back in range(6, 0, -1):                     # a constant may be set up just before the read
        before = at - 4 * back
        if before in image and image.at(before).name == 'addiu' and image.at(before).rs == 0:
            consts[image.at(before).rt] = image.at(before).imm & 0xff
    for step in range(1, window):
        here = at + 4 * step
        if here not in image:
            break
        i = image.at(here)
        if i.name in ('beq', 'bne'):
            for mine, other in ((i.rs, i.rt), (i.rt, i.rs)):
                if mine == reg and other in consts:
                    found.add(consts[other])
        elif i.name in ('slti', 'sltiu') and i.rs == reg:
            lows.add(i.imm & 0xffff if i.imm < 0 else i.imm)
        elif i.name == 'addiu' and i.rs == reg and i.imm < 0:
            shifted[i.rt] = -i.imm                   # `used - k`, then a bound check: a switch over k..k+n-1
        elif i.name == 'sltiu' and i.rs in shifted and i.imm > 0:
            ranges.add((shifted[i.rs], shifted[i.rs] + i.imm - 1))
        if i.name == 'addiu' and i.rs == 0:
            consts[i.rt] = i.imm & 0xff
        elif script._dest(i) == reg or i.name in ('jal', 'jalr', 'jr'):
            break
    if len(lows) >= 2:
        ranges.add((min(lows), max(lows) - 1))
    return found, ranges


def scan_handler(exe, overlay, handler, symbols=None):
    """{'has': {id}, 'gives': {id}, 'takes': {id}, 'routines': {name}, 'script': {id}} or None."""
    try:
        flow = script.Flow(exe, overlay, handler)
    except (ValueError, IndexError, struct.error):
        return None
    symbols = symbols or {}
    out = dict(has=set(), gives=set(), takes=set(), routines=set(), script=set(), used=set(), used_ranges=set(), reads_used=False, spawns_used=False)
    inventory = range(script.INVENTORY, script.INVENTORY + 0x100)
    for at in sorted(flow.seen):
        image = flow.image(at)
        if image is None:
            continue
        i = image.at(at)
        if i.name in ('lb', 'lbu', 'lh', 'lhu'):
            for v in flow.before(i.rs, at, 0, set()) or []:
                if v and v[1] is not None and 0 < at - v[1] <= NEAR_INVENTORY and ((v[0] + i.imm) & 0xffffffff) in inventory:
                    out['has'].add(((v[0] + i.imm) & 0xffffffff) - script.INVENTORY)
        if i.name in ('lb', 'lbu'):
            for v in flow.before(i.rs, at, 0, set()) or []:
                if v and v[1] is not None and 0 < at - v[1] <= NEAR and ((v[0] + i.imm) & 0xffffffff) == LAST_USED_ITEM:
                    out['reads_used'] = True
                    ids, ranges = _compared_with(image, at, i.rt)
                    out['used'].update(ids)
                    out['used_ranges'].update(ranges)
                    if i.rt == 5 and any(image.at(at + 4 * k).name == 'jal' and image.at(at + 4 * k).target in SPAWN_LINKED
                                         for k in range(1, 4) if at + 4 * k in image):
                        out['spawns_used'] = True      # the used id goes straight to the pickup spawner: any item
        if i.name == 'jal':
            kind = 'gives' if i.target in GIVES else 'takes' if i.target in TAKES else None
            if kind:
                for v in flow.before(4, at, 0, set()) or []:
                    if v and v[1] is None and v[2] is None:
                        out[kind].add(v[0] & 0x3ff)
            name = symbols.get(i.target)
            if name and script.BASE <= i.target < flow.overlay_end and ITEM_ROUTINE.search(name):
                out['routines'].add(name)
    try:
        read = script.reader(exe, overlay)
        for binding in script.discover(exe, overlay, handler):
            for c in script.read_graph(read, binding.script):
                o = c.operands
                if c.opcode in (60, 61) and not o[0] & 0x2000 and not o[0] & 0x1000:
                    out['script'].add(o[0] & 0x3ff)
                elif c.opcode == 40 and o[0] in (2, 3):
                    out['script'].add(o[1] & 0x3ff)
    except (ValueError, IndexError, struct.error):
        pass
    return out


def scene_handlers(overlay):
    """Handler addresses in the overlay's scene tables - runs of 16-byte records (handler in
    the last word) that end at a 0xFF record, which is how the game's scene spawner reads them.
    These are the interior NPCs and room actors the placement tables never list."""
    from game import actor_sim
    found, n = set(), len(overlay)
    for off in range(0, n - 16, 4):
        if overlay[off] == actor_sim.SCENE_END:
            continue
        pos, run, closed = off, [], False
        for _ in range(64):
            if pos + 16 > n:
                break
            if overlay[pos] == actor_sim.SCENE_END:
                closed = bool(run)
                break
            handler = struct.unpack_from('<I', overlay, pos + 12)[0]
            if handler & 3 or not (0x80010000 <= handler < actor_sim.EXE_END
                                   or actor_sim.OVERLAY_BASE <= handler < actor_sim.AREA_BASE):
                break
            run.append(handler)
            pos += 16
        if closed:
            found.update(h for h in run if script.BASE <= h < script.BASE + len(overlay))
    return found


def handlers_of(tag, overlay_path, symbols):
    """Handler addresses worth scanning in one overlay: what it places, plus
    its named update / handle routines."""
    from game import placement
    found = {}
    for record in placement.load_placements(overlay_path):
        if isinstance(record.handler, int) and script.BASE <= record.handler < script.BASE + 0x100000:
            found.setdefault(record.handler, symbols.get(record.handler, ''))
    for address, name in symbols.items():
        if (re.match(r'f_(Update|Handle)', name) or re.search(r'Npc|Actor|Interaction|Conversation|Dialogue|Quest|Reward|Controller', name))                 and script.BASE <= address < script.BASE + 0x100000:
            found.setdefault(address, name)
    for address in scene_handlers(open(overlay_path, 'rb').read()):
        found.setdefault(address, symbols.get(address, ''))
    return found


def scan_all(root):
    """{tag: {handler: (name, result)}} over every area overlay of the game under `root`."""
    from pathlib import Path
    from game import game_build
    root = Path(root)
    exe = (root / 'MAIN.EXE').read_bytes()
    game_build.use(str(root / 'MAIN.EXE'))
    script.load_names(exe, str(root / 'MAIN.EXE'))
    out = {}
    for path in sorted((root / 'BIN').glob('A0*.BIN')):
        tag = path.stem
        symbols = _symbols(tag)
        overlay = path.read_bytes()
        rows = {}
        for handler, name in handlers_of(tag, str(path), symbols).items():
            result = scan_handler(exe, overlay, handler, symbols)
            if result and (any(v for k, v in result.items() if k not in ('reads_used', 'spawns_used')) or result['reads_used']):
                rows[handler] = (name, result)
        out[tag] = rows
    return out


def report(scan):
    """Markdown over `scan_all`'s result, with the game's item names."""
    names = script._names.get('items', {})
    item = lambda n: f'{n} {names.get(n, "?")}'
    lines = ['# NPC and object handlers that touch items', '',
             'Static scan of each handler\'s reachable code (see `game/item_scan.py`). '
             '"checks" is a read of the inventory table: a test or a display.', '']
    for tag, rows in scan.items():
        if not rows:
            continue
        lines += [f'## {tag}', '']
        for handler, (name, r) in sorted(rows.items()):
            bits = []
            for key, label in (('used', 'reacts to Tomba using'), ('has', 'checks'), ('gives', 'gives'), ('takes', 'takes'), ('script', 'page rows use')):
                if r[key]:
                    bits.append(f'{label}: ' + ', '.join(item(n) for n in sorted(r[key])))
            for low, high in sorted(r['used_ranges']):
                span = ', '.join(item(n) for n in range(low, min(high, low + 7) + 1)) + (' …' if high > low + 7 else '')
                bits.append(f'reacts to Tomba using items {low}-{high}: {span}')
            if r['spawns_used']:
                bits.append('spawns whatever item Tomba used as a pickup (any item)')
            if r['reads_used'] and not r['used'] and not r['used_ranges'] and not r['spawns_used']:
                bits.append('reads which item was used (not a fixed id)')
            if r['routines']:
                bits.append('item routines: ' + ', '.join(sorted(r['routines'])))
            lines.append(f'- `0x{handler:08X}` {name or "(unnamed)"} - ' + '; '.join(bits))
        lines.append('')
    return '\n'.join(lines)


ACTOR = 0x801F0000          # a scratch actor record for the probe below
STACK = 0x801FF000


def reaction_routines(flow):
    """Overlay routines of a handler's flow that read the item Tomba is using."""
    roots = sorted(flow.roots)
    found = set()
    for at in sorted(flow.seen):
        image = flow.image(at)
        i = image.at(at) if image is not None else None
        if i is None or i.name not in ('lb', 'lbu'):
            continue
        for v in flow.before(i.rs, at, 0, set()) or []:
            if v and v[1] is not None and 0 < at - v[1] <= NEAR and ((v[0] + i.imm) & 0xffffffff) == LAST_USED_ITEM:
                start = max((r for r in roots if r <= at), default=None)
                if start is not None and start != flow.roots and script.BASE <= start < flow.overlay_end:
                    found.add(start)
    return sorted(found)


def item_reactions(exe, overlay, handler, flow=None, items=range(1, 256)):
    """What the handler's item test does for each item id, by running it: [{'item', 'routine',
    'result' (the routine's return), 'actor' (the 0x100-byte actor record afterwards), 'changed' (offsets)}]
    for the items it accepts (returns non-zero). The routine gets a zeroed actor in a0 and the item in the
    used-item byte; the game's own code does the sorting, nothing is read from tables here."""
    from psx.cpu import CPU, EmuError, Memory
    flow = flow or script.Flow(exe, overlay, handler)
    routines = [r for r in reaction_routines(flow) if r != handler]
    if not routines:
        return []
    memory = Memory()
    memory.load(struct.unpack_from('<I', exe, 24)[0], exe[0x800:])
    memory.load(script.BASE, overlay)
    cpu = CPU(memory)
    out = []
    for routine in routines:
        rows = []
        for item in items:
            memory.load(ACTOR, bytes(0x100))
            memory.write(LAST_USED_ITEM, 1, item)
            try:
                result = cpu.call(routine, (ACTOR,), budget=20000, sp=STACK)
            except (EmuError, struct.error, IndexError):
                rows = []
                break
            actor = memory.bytes(ACTOR, 0x100)
            if result and any(actor):
                rows.append(dict(item=item, routine=routine, result=result, actor=actor,
                                 changed=[k for k, b in enumerate(actor) if b]))
        if len(rows) < len(items):          # one that takes every item is not sorting them
            out.extend(rows)
    return out

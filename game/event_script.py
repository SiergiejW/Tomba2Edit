"""Native US actor scripts: lossless records, control-flow edges and relocation."""
from dataclasses import dataclass, field
import struct
from psx.mips import Image
from game import game_build, pickup_edit

BASE = 0x80108F9C
LOAD_SCRIPT = 0x80040D68
# Native entry points that start a script, and the register holding it:
# f_LoadActorScript (a1), f_InitializeActorScript (a2) and
# f_LoadCurrentScriptActorScript (a0).
LOADERS = {LOAD_SCRIPT: 5, 0x80040CDC: 6, 0x80070888: 4}
CALLBACKS = 0x800A3B78
TICK_HZ = 30
# Edited scripts live at the top of the free-RAM window, clear of the custom
# reward table and grown pickup tables, which fill it from the bottom.
EVENT_START = pickup_edit.EVENT_START
EVENT_END = pickup_edit.WINDOW_END
# Header bits: 0x2000 extended operands, 0x4000 branch pointer, 0x8000 end.
EXTENDED, BRANCH, END = 0x2000, 0x4000, 0x8000
LABELS = {
 0: 'Spawn presentation', 1: 'Animate presentation', 2: 'Skip presentation',
 3: 'End presentation', 4: 'Set progress and wait', 5: 'Wait (timer)',
 6: 'Wait for progress', 7: 'Wait for animation', 8: 'Wait for camera',
 9: 'Wait for conversation progress', 10: 'Wait for player action',
 11: 'Interaction audio', 12: 'Change progress', 13: 'Request conversation',
 14: 'Run conversation', 15: 'Script actor mode', 16: 'Interaction offset',
 17: 'Set position', 18: 'Start event', 19: 'Update event', 20: 'Clear item use',
 21: 'Camera transform', 22: 'Camera focus', 23: 'Camera angles',
 24: 'Focus camera on Tomba', 25: 'Camera control', 26: 'Mirror orientation',
 27: 'Apply orientation', 28: 'Change model part', 29: 'Model resources',
 30: 'Play animation', 31: 'Turn actor', 32: 'Enable tint', 33: 'Disable tint',
 34: 'Change area', 35: 'Move along axis', 36: 'Move to position',
 37: 'Orbit movement', 38: 'Spawn pickup', 39: 'Configure pickup',
 40: 'Show message', 41: 'Spawn item at Tomba', 42: 'Branch on dialogue selector',
 43: 'If area progress', 44: 'If event state', 45: 'Controller vibration',
 46: 'Play sound', 47: 'Play voice', 48: 'Shared control flag',
 49: 'Move Tomba to coordinate', 50: 'Enter weaponless interior',
 51: 'Exit weaponless interior', 52: 'Tomba facing', 53: 'Sync location from Tomba',
 54: 'Set Tomba location', 55: 'Play Tomba animation', 56: 'Turn Tomba toward actor',
 57: 'Actor + Tomba animation', 58: 'Actor faces Tomba', 59: 'Turn actor (sequence)',
 60: 'Item-use sequence', 61: 'Linked pickup sequence', 62: 'Run native code',
}
CONDITION = 'Condition (-1 always, 0 =, 1 <=, 2 >=, 3 any bit)'
FIELDS = {5: ('Ticks (completes after N + 1 ticks)', 'Unused', 'Unused'),
 0: ('Presentation resource / flags', 'Dialogue entry (-1 = runtime)', 'Behavior'),
 1: ('Presentation resource / flags', 'Dialogue entry (-1 = runtime)', 'Actor animation'),
 4: ('Progress byte', 'Value to write', 'Wait until value'),
 6: ('Test (0 =, 1 any bit, 2 bits clear)', 'Progress byte', 'Value / mask'),
 12: ('Mode (0 set, 1 OR, 2 AND)', 'Progress byte', 'Value'),
 17: ('X', 'Height', 'Depth', 'Location byte', 'Unused', 'Unused', 'Unused'),
 22: ('Mode op (0 set, 1 assign, -1 restore)', 'Camera mode', 'Focus (0 point, 1 Tomba)',
      'Focus X', 'Focus height', 'Focus depth', 'Unused'),
 23: ('Camera control 1', 'Camera control 2', 'Camera control 3'),
 30: ('Actor / wait flags', 'Animation ID (-1 = actor default)', 'Model mode'),
 31: ('Target mode (0x8000 = script actor)', 'Max step per tick', 'Angle (4096 = 360°)'),
 35: ('Axis (0 X, 1 height, 2 depth)', 'Terrain probe offset', 'Target coordinate',
      'Speed (1/256 unit per tick)', 'Motion sound', 'Sound frame', 'Unused'),
 36: ('Target X', 'Target height', 'Target depth',
      'Speed (high byte = units per tick)', 'Motion sound', 'Sound frame', 'Unused'),
 40: ('Message mode (odd = wait)', 'Text / item ID', 'Resource selector'),
 42: ('Selector value', 'Unused', 'Unused'),
 43: (CONDITION, 'Progress byte', 'Expected value'),
 44: (CONDITION, 'Event index', 'Expected value'),
 47: ('Playback mode', 'Voice ID', 'Countdown'),
 46: ('Sound ID', 'Pitch offset', 'Volume offset'),
 49: ('Mode (2 = camera-relative)', 'Axis (0 X, else depth)', 'Target coordinate'),
 62: ('Unused', 'Code address low', 'Code address high')}
# Actions offered for insertion, and the defaults a new one starts with.
INSERTABLE = {5: (30, 0, 0), 1: (5, 0, 3), 30: (0, 0, 0), 40: (1, 0, 0),
              46: (0, 0, 0), 47: (0, 0, 0), 17: (0, 0, 0, 0, 0, 0, 0),
              35: (0, 0, 0, 256, 0, 0, 0), 36: (0, 0, 0, 0x0a00, 0, 0, 0),
              31: (0, 256, 0), 23: (0, 0, 0), 24: (0, 0, 0), 22: (-1, 0, 2),
              7: (0, 0, 0), 8: (0, 0, 0), 12: (0, 0, 0)}
CONDITIONS = ('always', '==', '<=', '>=', '& any')

@dataclass
class Command:
    address: int
    raw: bytes
    edges: list = field(default_factory=list)
    dead_tail: bool = False

    @property
    def header(self): return struct.unpack_from('<H', self.raw)[0]
    @property
    def opcode(self): return self.header & 0x7ff
    @property
    def extended(self): return bool(self.header & EXTENDED)
    @property
    def operands(self): return list(struct.unpack_from('<'+'h'* (7 if self.extended else 3), self.raw, 2))
    @property
    def label(self): return LABELS.get(self.opcode, f'Area action 0x{self.opcode:X}' if self.opcode >= 63 else f'Native action 0x{self.opcode:X}')
    @property
    def fallthrough(self): return next((a for a, p in self.edges if p is None), None)
    @property
    def branch(self): return next((a for a, p in self.edges if p is not None), None)
    def set_operands(self, values):
        data = bytearray(self.raw)
        struct.pack_into('<'+'h'*len(self.operands), data, 2, *values)
        self.raw = bytes(data)
    def set_header(self, value):
        data = bytearray(self.raw); struct.pack_into('<H', data, 0, value); self.raw = bytes(data)

def code_pointer(c):
    o = c.operands
    return (o[1] & 0xffff) | (o[2] & 0xffff) << 16

def move_speed(c):
    """Units per tick of an interpolated move: the signed high byte of
    extended operand 4, ten when zero."""
    value = (c.operands[3] >> 8) if c.extended else 0
    value = value - 256 if value > 127 else value
    return value or 10

def summary(c):
    """One readable line per action, RPG-Maker style."""
    o = c.operands; op = c.opcode
    def cond(mode): return CONDITIONS[mode+1] if -1 <= mode <= 3 else f'mode {mode}'
    if op == 5: return f'Wait {o[0]+1} ticks ({(o[0]+1)/TICK_HZ:.2f} s)'
    if op in (0, 1):
        entry = 'runtime-selected entry' if o[1] < 0 else f'entry {o[1]}'
        return f'{c.label}: dialogue {o[0]&0xfff}, {entry}' + (f', actor animation {o[2]}' if op == 1 else '')
    if op == 30:
        who = 'script actor' if o[0] & 1 else 'this actor'
        return f'Play animation {"(default)" if o[1] < 0 else o[1]} on {who}' + (', wait' if o[0] & 2 else '')
    if op == 40:
        kind = {0: 'message', 1: 'message', 2: 'item message', 3: 'item message', 4: 'alt message', 5: 'alt message', 10: 'skip'}.get(o[0], f'mode {o[0]}')
        return f'Show {kind} {o[1]}' + (f' (group {o[2]})' if o[0] in (0, 1) else '') + (', wait' if o[0] & 1 else '')
    if op == 46: return f'Play sound 0x{o[0]&255:02X} (pitch {o[1]:+d}, volume {o[2]:+d})'
    if op == 47: return 'Stop voice' if o[1] < 0 else f'Play voice {o[1]} (mode {o[0]})'
    if op == 17: return f'Set position X {o[0]}, height {o[1]}, depth {o[2]}'
    if op == 35:
        speed = f' at {o[3]/256:.2f}/tick' if c.extended else ''
        return f'Move {("X","height","depth")[o[0]] if 0 <= o[0] <= 2 else "axis "+str(o[0])} to {o[2]}{speed}'
    if op == 36:
        speed = f'{move_speed(c)}/tick' if c.extended else 'carried-over speed'
        return f'Move to X {o[0]}, height {o[1]}, depth {o[2]} at {speed}'
    if op == 31: return f'Turn {"script actor" if o[0] & 0x8000 else "actor"} (mode {o[0]&0x7fff}) to {o[2]*360/4096:.0f}°, step {o[1]}'
    if op == 23: return f'Camera angles {o[0]}, {o[1]}, {o[2]}'
    if op == 22:
        mode = {0: f'set mode {o[1]}', 1: f'assign mode {o[1]}', -1: 'restore mode'}.get(o[0], f'op {o[0]}')
        focus = ' on Tomba' if o[2] == 1 else f' at ({o[3]}, {o[4]}, {o[5]})' if o[2] == 0 and c.extended else ''
        return f'Camera focus: {mode}{focus}'
    if op == 21: return f'Camera transform 0x{code_pointer(c):08X}' + (f' over {o[4]} ticks' if c.extended else '')
    if op in (43, 44):
        what = 'progress' if op == 43 else 'event'
        return f'If {what}[{o[1]}] {cond(o[0])} {o[2]}'
    if op == 6: return f'Wait until progress[{o[1]}] {("==", "& any", "& none")[o[0]] if 0 <= o[0] <= 2 else "?"} {o[2]}'
    if op == 9: return f'Wait until conversation progress == {o[0]}'
    if op == 42: return f'If dialogue selector == {o[0]}'
    if op == 12: return f'progress[{o[1]}] {("=", "|=", "&=")[o[0]] if 0 <= o[0] <= 2 else "?"} {o[2]}'
    if op == 4: return f'progress[{o[0]}] = {o[1]}, wait until {o[2]}'
    if op == 49: return f'Move Tomba along {"X" if o[1] == 0 else "depth"} to {o[2]}'
    if op == 62: return f'Run native code 0x{code_pointer(c):08X}'
    params = ', '.join(str(v) for v in o)
    return f'{c.label} ({params})'

# Callbacks that return the branch results 2/3 and so use both edges;
# the others return 0 (wait) or 1 (done), which takes a branch form's pointer.
CONDITION_OPS = frozenset((42, 43, 44))
# Native-code and area callbacks may return either result.
def two_way(opcode): return opcode in CONDITION_OPS or opcode >= 62

def read_graph(read, entry, limit=256):
    """Read both conditional paths, preserving headers, padding and pointers.

    Jumps from an area script into a resident one stay external pointers.
    A plain action's branch form always takes its pointer, so unreadable
    bytes after it are a dead fall-through, not part of the script."""
    pending, nodes = [(entry, None)], {}
    while pending:
        at, parent = pending.pop(0)
        if at in nodes: continue
        if len(nodes) >= limit: raise ValueError('Script exceeds the bounded command limit.')
        try:
            head = struct.unpack('<H', read(at,2))[0]
            if head & 0x7ff >= 0x52: raise ValueError(f'Unknown script callback at 0x{at:X}.')
            mode = head & 0xe000
            size = 24 if mode == 0x6000 else 16 if mode in (0x2000,0x4000,0xa000,0xe000) else 8
            raw = read(at,size)
        except ValueError:
            if parent is not None and parent.branch is not None and not two_way(parent.opcode):
                parent.edges = [(t, p) for t, p in parent.edges if p is not None]
                parent.dead_tail = True
                continue
            raise
        edges = [] if mode >= 0x8000 else [(at+size, None)]
        if mode in (0x4000,0x6000):
            pos = 20 if mode == 0x6000 else 12
            edges.append((struct.unpack_from('<I',raw,pos)[0],pos))
        node = nodes[at] = Command(at,raw,edges)
        follow = [t for t, p in edges if not (p is not None and entry >= BASE and t < BASE)]
        pending[0:0] = [(t, node) for t in follow]
    ordered=list(nodes.values())
    for node in ordered:
        if any(other.address < node.address < other.address+len(other.raw) for other in ordered):
            raise ValueError('Script edges overlap command operands.')
    return ordered

def compile_graph(commands, base):
    """Place a graph contiguously and relocate both embedded branch pointers."""
    if not commands: raise ValueError('An event must contain at least one action.')
    locations, at = {}, base
    for command in commands:
        if command.address in locations: raise ValueError('Duplicate action identity.')
        locations[command.address] = at; at += len(command.raw)
    out = bytearray()
    for index, command in enumerate(commands):
        raw = bytearray(command.raw)
        for target, pointer in command.edges:
            if target not in locations:
                if pointer is not None and target < BASE: continue  # resident script tail
                raise ValueError('An action references a missing branch target.')
            if pointer is None:
                if index+1 >= len(commands) or commands[index+1].address != target:
                    raise ValueError('Fall-through actions must remain adjacent; reorder their full branch together.')
            else: struct.pack_into('<I',raw,pointer,locations[target])
        out.extend(raw)
    return bytes(out)

def _new_command(commands, opcode, operands):
    if opcode not in LABELS: raise ValueError('This native action is not supported for insertion.')
    operands = list(operands)
    extended = len(operands) > 3
    operands += [0] * ((7 if extended else 3) - len(operands))
    identity = min([0]+[c.address for c in commands])-1
    head = opcode | (EXTENDED if extended else 0)
    return Command(identity, struct.pack('<H'+'h'*len(operands), head, *operands))

def _redirect(commands, old, new, skip=None):
    for c in commands:
        if c is skip: continue
        c.edges = [(new if a == old else a, p) for a, p in c.edges]

def insert_before(commands, selected, opcode, operands=(0,0,0)):
    """Insert a basic native action, redirecting incoming edges to it."""
    target = commands[selected].address
    command = _new_command(commands, opcode, operands)
    _redirect(commands, target, command.address)
    command.edges = [(target, None)]
    commands.insert(selected, command)

def insert_after(commands, selected, opcode, operands=(0,0,0)):
    """Insert after an action; jumps to its successor still land there."""
    before = commands[selected]
    nxt = before.fallthrough
    if nxt is None: raise ValueError('Nothing runs after an end action; insert before it instead.')
    command = _new_command(commands, opcode, operands)
    command.edges = [(nxt, None)]
    before.edges = [(command.address if p is None else a, p) for a, p in before.edges]
    commands.insert(selected+1, command)

def delete(commands, selected):
    gone = commands[selected]
    if gone.branch is not None: raise ValueError('Conditional actions decide where the script goes; they cannot be removed.')
    nxt = gone.fallthrough
    if nxt is None: raise ValueError('End actions finish the script; they cannot be removed.')
    if len(commands) < 2: raise ValueError('An event must contain at least one action.')
    _redirect(commands, gone.address, nxt, skip=gone)
    del commands[selected]

def move_down(commands, selected):
    """Swap an action with the one after it in the same straight run."""
    if not 0 <= selected < len(commands)-1: raise ValueError('Nothing below to swap with.')
    a, b = commands[selected], commands[selected+1]
    if a.fallthrough != b.address or b.fallthrough is None:
        raise ValueError('Only actions in one straight run can be swapped; an end or jump separates these.')
    if any(t == b.address for c in commands if c is not a for t, _ in c.edges):
        raise ValueError('Another action jumps to the lower one; swapping would change what that jump runs.')
    after = b.fallthrough
    _redirect(commands, a.address, b.address, skip=b)
    b.edges = [(a.address if p is None else t, p) for t, p in b.edges]
    a.edges = [(after if p is None else t, p) for t, p in a.edges]
    commands[selected], commands[selected+1] = b, a

def reader(exe, overlay):
    main_base = struct.unpack_from('<I',exe,24)[0]
    def read(at,size):
        data, offset = (overlay, at-BASE) if at >= BASE else (exe,at-main_base+0x800)
        if offset < 0 or offset+size > len(data): raise ValueError(f'Unresolved script address 0x{at:X}.')
        return data[offset:offset+size]
    return read

@dataclass
class Binding:
    script: int
    hi_at: int
    lo_at: int
    hi_word: int
    lo_word: int
    caller: int
    shared_hi: bool = False
    register: int = 5
    # f_InitializeActorScript also attaches the actor's animation table.
    animations: int = None

def _dest(i):
    if i.name in ('lui','addiu','addi','ori','andi','xori','slti','sltiu') or i.name in ('lb','lbu','lh','lhu','lw','lwl','lwr'):
        return i.rt
    if i.name in ('jal','bltzal','bgezal'): return 31
    if i.word >> 26 == 0 and i.name not in ('jr','mult','multu','div','divu','mthi','mtlo','syscall',None): return i.rd
    if i.word >> 26 == 0x12 and (i.rs in (0, 2)): return i.rt  # mfc2/cfc2
    return None

CALLER_SAVED = frozenset(range(1, 16)) | {24, 25, 31}

class Flow:
    """Instruction-level control flow reachable from one native handler.

    Follows both branch paths, jump tables, tail jumps and overlay calls, so
    a call site after an early return is still found. Calls mark their
    return edge so caller-saved registers are not carried across them."""
    def __init__(self, exe, overlay, start, limit=40000):
        main = struct.unpack_from('<I', exe, 24)[0]
        self.images = (Image(overlay, BASE), Image(exe[0x800:], main))
        self.overlay_end = BASE + len(overlay)
        self.preds, self.calls = {}, []
        seen, work, roots = set(), [start], {start}
        while work and len(seen) < limit:
            at = work.pop()
            while at not in seen and len(seen) < limit:
                image = self.image(at)
                if image is None: break
                seen.add(at); i = image.at(at)
                if i.name in ('beq','bne','blez','bgtz','bltz','bgez','j','jal','jr','jalr','bltzal','bgezal'):
                    slot = at + 4
                    self._edge(at, slot); seen.add(slot)
                    if image.at(slot).name in ('j','jal','jr','beq','bne'): break
                    if i.name == 'jal' or i.name == 'jalr':
                        if i.name == 'jal':
                            if i.target in LOADERS: self.calls.append((at, LOADERS[i.target]))
                            elif BASE <= i.target < self.overlay_end and i.target not in roots:
                                roots.add(i.target); work.append(i.target)
                        self._edge(slot, at + 8, call=True); at += 8; continue
                    if i.name == 'j':
                        self._edge(slot, i.target); at = i.target; continue
                    if i.name == 'jr':
                        if i.rs != 31:
                            for target in self._table(image, at, i.rs):
                                self._edge(slot, target); work.append(target)
                        break
                    self._edge(slot, i.target); work.append(i.target)
                    if i.name == 'beq' and i.rs == i.rt: break  # unconditional
                    self._edge(slot, at + 8); at += 8; continue
                self._edge(at, at + 4); at += 4
        self.seen = seen

    def image(self, at):
        return next((im for im in self.images if at in im), None)

    def _edge(self, a, b, call=False):
        self.preds.setdefault(b, []).append((a, call))

    def _table(self, image, at, reg):
        """Targets of `lui; addiu; sll; addu; lw reg, lo(x); jr reg` tables."""
        lines = [image.at(at - n*4) for n in range(1, 16) if at - n*4 in image]
        load = next((n for n, i in enumerate(lines) if i.name == 'lw' and i.rt == reg), None)
        if load is None: return []
        count = next((i.unsigned for i in lines[load:] if i.name == 'sltiu'), 64)
        for n in range(load+1, len(lines)):
            i = lines[n]
            if i.name != 'lui': continue
            low = sum(j.imm for j in lines[load+1:n] if j.name == 'addiu' and j.rt == i.rt and j.rs == i.rt)
            table = (i.unsigned << 16) + low + lines[load].imm
            out = []
            for k in range(min(count, 256)):
                raw = self.read(table + k*4, 4)
                if raw is None or len(raw) < 4: break
                w = struct.unpack('<I', raw)[0]
                if not (abs(w - at) < 0x8000 and w in image): break
                out.append(w)
            if out: return out
        return []

    def read(self, at, size):
        image = self.image(at)
        if image is None: return None
        return image.data[at-image.base:at-image.base+size]

    def after(self, reg, at, depth=0, seen=None):
        """[(value, hi_at, lo_at) | None] for `reg` after `at` executes."""
        seen = set() if seen is None else seen
        if depth > 64: return [None]
        image = self.image(at)
        i = image.at(at)
        if _dest(i) != reg: return self.before(reg, at, depth, seen)
        if i.name == 'lui': return [(i.unsigned << 16, at, None)]
        if i.name in ('addiu', 'ori') and i.rs != 0:
            out = []
            for v in self.before(i.rs, at, depth+1, seen):
                if v is None or v[2] is not None or v[1] is None: out.append(None); continue
                value = (v[0] + i.imm) & 0xffffffff if i.name == 'addiu' else v[0] | i.unsigned
                out.append((value, v[1], at))
            return out
        if i.name in ('addu', 'or') and 0 in (i.rs, i.rt):
            return self.before(i.rt if i.rs == 0 else i.rs, at, depth+1, seen)
        return [None]

    def before(self, reg, at, depth=0, seen=None):
        key = (reg, at)
        if key in seen: return []
        seen.add(key)
        preds = self.preds.get(at)
        if not preds: return [None]
        out = []
        for p, call in preds:
            if call and reg in CALLER_SAVED: out.append(None); continue
            out.extend(self.after(reg, p, depth+1, seen))
        return out

def discover(exe, overlay, handler):
    """Every literal script a handler's reachable code passes to the loader.

    Dynamic script tables are left unresolved; no branch choice is invented.
    A call site whose pointer is built on several paths yields one binding
    per path; a `lui` shared by several of them is flagged.
    """
    flow = Flow(exe, overlay, handler)
    read = reader(exe, overlay)
    found = {}
    for call, register in flow.calls:
        for value in flow.after(register, call + 4):
            if value is None or value[1] is None or value[2] is None: continue
            address, hi, lo = value
            if (hi, lo) in found: continue
            try: read_graph(read, address)
            except ValueError: continue
            tables = {v[0] for v in flow.after(5, call + 4) if v} if register == 6 else set()
            found[(hi, lo)] = Binding(address, hi, lo, struct.unpack('<I', read(hi, 4))[0],
                                      struct.unpack('<I', read(lo, 4))[0], call, register=register,
                                      animations=tables.pop() if len(tables) == 1 else None)
    by_hi = {}
    for b in found.values(): by_hi.setdefault(b.hi_at, []).append(b)
    for group in by_hi.values():
        if len(group) > 1:
            for b in group: b.shared_hi = True
    # Several bindings at one lo (merged paths) cannot be retargeted apart.
    by_lo = {}
    for b in found.values(): by_lo.setdefault(b.lo_at, []).append(b)
    return [b for g in by_lo.values() if len(g) == 1 for b in g]

def handler_for(scene, instance):
    if instance.placement is not None: return instance.placement.handler
    if instance.pickup is not None and instance.pickup.chest:
        from game.actor_sim import CHEST_HANDLER
        return CHEST_HANDLER
    if instance.index in getattr(scene,'event_handlers',{}): return scene.event_handlers[instance.index]
    world = getattr(scene,'world',None)
    actors = getattr(world,'actors',())
    matching = [a.handler for a in actors if scene.handler_name(a.handler) in instance.label]
    return matching[0] if len(set(matching))==1 else None

def _referenced(data, address, own_lo):
    """Whether anything but `own_lo` could still point at `address`.

    Conservative: any low-half immediate or data word matching counts."""
    low = address & 0xffff
    for off in range(0, len(data)-3, 4):
        if BASE+off == own_lo: continue
        w = struct.unpack_from('<I', data, off)[0]
        if w == address: return True
        if w >> 26 in (9, 13) and w & 0xffff == low and EVENT_START-BASE > off: return True
    return False

def _allocate(data, size, current, current_size, own_lo):
    """Where in the event region a script of `size` bytes goes."""
    if (current is not None and EVENT_START <= current < EVENT_END and size <= current_size
            and not _referenced(data, current, own_lo)):
        return current
    region = data[EVENT_START-BASE:EVENT_END-BASE]
    last = max((n for n, b in enumerate(region) if b), default=None)
    # The last record may end in zero operands; leave a full record's gap.
    at = EVENT_START if last is None else (EVENT_START + last + 24 + 7) & ~7
    if at + size > EVENT_END: raise ValueError('The event extension region is full.')
    return at

def _reads(i, reg):
    if i.name in ('lui', 'j', 'jal') or i.name is None: return False
    if i.word >> 26 == 0: return reg in (i.rs, i.rt)
    if i.name in ('beq', 'bne') or i.name in ('sb', 'sh', 'sw', 'swl', 'swr'): return reg in (i.rs, i.rt)
    return i.rs == reg

def _scratch_dies(read, hi_at, lo_at, base, reg):
    """Whether a `lui base` only feeds the `addiu reg, base` at lo_at.

    Straight-line check: between them nothing else reads `base`, and after
    it `base` is rewritten (or clobbered by a call) before any other read
    or branch. With base == reg the pointer register itself is the target."""
    from psx.mips import Instruction
    if base == reg: return True
    at = hi_at + 4
    for _ in range(24):
        i = Instruction(at, struct.unpack('<I', read(at, 4))[0])
        if at != lo_at:
            if _reads(i, base): return False
            if _dest(i) == base: return at > lo_at
            if i.name == 'jal' and at > lo_at and base in CALLER_SAVED: return True
            if i.name in ('j', 'jr', 'jalr', 'beq', 'bne', 'blez', 'bgtz', 'bltz', 'bgez'):
                if at < lo_at: return False
                # Delay slot runs first; then the scratch must not live on.
                slot = Instruction(at+4, struct.unpack('<I', read(at+4, 4))[0])
                if _reads(slot, base): return False
                return _dest(slot) == base or (i.name == 'jal' and base in CALLER_SAVED)
        at += 4
    return False

def stage(exe,overlay,binding,commands):
    """Copy a script into the event region and rebind this call site."""
    if game_build.current().name!='us-retail': raise ValueError('Event writing currently requires US retail.')
    for opcode,target in {5:0x80042090,30:0x8004179c,40:0x80043720,47:0x80043a40}.items():
        if struct.unpack('<I',reader(exe,overlay)(CALLBACKS+opcode*4,4))[0]!=target:
            raise ValueError('The native command table differs from the verified US layout.')
    read=reader(exe,overlay)
    if struct.unpack('<I',read(binding.hi_at,4))[0]!=binding.hi_word or struct.unpack('<I',read(binding.lo_at,4))[0]!=binding.lo_word:
        raise ValueError('The event binding changed; reopen Events before saving.')
    if binding.hi_at<BASE or binding.lo_at<BASE: raise ValueError('Resident shared bindings are read-only.')
    hi,lo=binding.hi_word,binding.lo_word
    if hi>>26!=15 or lo>>26 not in (9,13): raise ValueError('Unverified script pointer instructions.')
    reg=binding.register;base=(hi>>16)&31
    if (lo>>21)&31!=base or (lo>>16)&31!=reg or not _scratch_dies(read,binding.hi_at,binding.lo_at,base,reg):
        raise ValueError('This shared pointer materialization cannot yet be redirected safely.')
    size = sum(len(c.raw) for c in commands)
    try: current_size = sum(len(c.raw) for c in read_graph(read, binding.script))
    except ValueError: current_size = 0
    out = bytearray(overlay)
    if len(out) > EVENT_END-BASE: raise ValueError('The area file extends past the free RAM window.')
    if len(out) < EVENT_START-BASE: out.extend(bytes(EVENT_START-BASE-len(out)))
    address = _allocate(out, size, binding.script, current_size, binding.lo_at)
    payload = compile_graph(commands, address)
    if address == binding.script:
        out[address-BASE:address-BASE+current_size] = bytes(current_size)
    end = address-BASE+len(payload)
    if len(out) < end: out.extend(bytes(end-len(out)))
    out[address-BASE:end] = payload
    high=(address+0x8000)>>16 if lo>>26==9 else address>>16
    if binding.shared_hi and high != hi & 0xffff:
        raise ValueError('This call site shares its high address half with another script, and the new location needs a different one.')
    struct.pack_into('<I',out,binding.hi_at-BASE,(hi&0xffff0000)|(high&0xffff))
    struct.pack_into('<I',out,binding.lo_at-BASE,(lo&0xffff0000)|(address&0xffff))
    return bytes(out)

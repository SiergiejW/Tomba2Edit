"""A native routine as guarded effects, in plain words.

Script command 62 ("Run native code") calls a routine every tick until it
returns non-zero, so most are small state machines: a byte of the actor picks
what this tick does. This walks every path through such a routine with a
symbolic register file (constants, the actor in a0, game globals, fields of
either, a call's result, a comparison) and keeps, per path, the tests that
led to it, what it wrote or called, and what it returned. Nothing is run; a
loop is entered once. A path is dropped when it does nothing and waits.
"""
import re
import struct
import threading

from psx.mips import Image

_mode = threading.local()      # _mode.c: words (default) or C text


def _c():
    return getattr(_mode, 'c', False)

BASE_FIELDS = {0x04: 'state', 0x79: 'sub-step', 0x05: 'phase', 0x06: 'step', 0x2B: 'item contact', 0x2E: 'X', 0x32: 'height', 0x36: 'depth',
               0x40: 'timer', 0x5E: 'behaviour', 0x60: 'item it hands back', 0x78: 'step'}
GLOBAL_WORDS = {0x800BF844: 'the spawned pickup', 0x800BF80A: 'the item-use flag', 0x800BF80B: 'the item Tomba is using',
                0x800BF80E: 'Tomba’s cutscene control'}
MAX_PATHS, MAX_STEPS = 300, 400
MAX_TOTAL = 2500            # paths started in one routine: a routine that forks without end stops here
LOADS = {'lw': 4, 'lh': 2, 'lhu': 2, 'lb': 1, 'lbu': 1}
STORES = {'sw': 4, 'sh': 2, 'sb': 1}
RETURNS = {0: 'keeps waiting (returns 0)', 1: 'finishes (returns 1)', 2: 'falls through (returns 2)', 3: 'jumps (returns 3)'}


def _signed(v):
    return v - 0x100000000 if v is not None and v >= 0x80000000 else v


def text(v, glob):
    """A symbolic value in words (or as a C expression in C mode)."""
    if _c(): return ctext(v, glob)
    k = v[0]
    if k == 'c':
        n = _signed(v[1])
        return str(n) if -0x10000 <= n < 65536 else f'0x{v[1]:X}'
    if k == 'arg': return 'this actor'
    if k == 't': return f'entry {text(v[2], glob)} of the table at 0x{v[1]:08X}'
    if k == 'g': return GLOBAL_WORDS.get(v[1]) or glob(v[1])
    if k == 'f':
        name = BASE_FIELDS.get(v[2])
        return f'{name or "+0x%02X" % v[2]} of {text(v[1], glob)}'
    if k == 'ret':
        return 'the actor it spawned' if 'Spawn' in v[1] or 'Allocate' in v[1] else f'the result of {v[1]}'
    if k == 'cmp':
        if v[1] == '<' and v[3] == ('c', 1):             # x < 1 is x == 0 (unsigned compare of a flag)
            x = v[2]
            if x[0] == 'op' and x[1] == '^' and x[3] == ('c', 1): return f'{text(x[2], glob)} == 1'
            return f'{text(x, glob)} == 0'
        return f'{text(v[2], glob)} {v[1]} {text(v[3], glob)}'
    if k == 'op':
        if v[1] == '+' and v[3][0] == 'c' and _signed(v[3][1]) < 0:
            return f'({text(v[2], glob)} - {-_signed(v[3][1])})'
        return f'({text(v[2], glob)} {v[1]} {text(v[3], glob)})'
    return '?'


C_FIELDS = {0x04: 'lifecycle_state', 0x05: 'pickup_phase', 0x06: 'special_phase', 0x2B: 'field_0x2b', 0x2E: 'x', 0x32: 'y', 0x36: 'z',
            0x40: 'health_timer', 0x5E: 'pickup_behavior', 0x60: 'effect_enabled', 0x78: 'step', 0x79: 'sub_step'}


def _ident(offset):
    return C_FIELDS.get(offset) or f'field_0x{offset:02x}'


def _gname(addr, size=1):
    from game import event_script
    if event_script.EVENT_STATES <= addr < event_script.EVENT_STATES + 0x100: return f'event_state[{addr - event_script.EVENT_STATES}]'
    if event_script.INVENTORY <= addr < event_script.INVENTORY + 0x100: return f'inventory[{addr - event_script.INVENTORY}]'
    if event_script.AREA_PROGRESS <= addr < event_script.AREA_PROGRESS + 0x100: return f'area_progress[{addr - event_script.AREA_PROGRESS}]'
    name = event_script._symbol(addr)
    if not name.startswith('0x'): return name
    return f'*(u{8 * size} *)0x{addr:08X}'


def ctext(v, glob):
    """A symbolic value as a C expression."""
    k = v[0]
    if k == 'c':
        n = _signed(v[1])
        return str(n) if -0x10000 <= n <= 0xFFFF else f'0x{v[1]:X}'
    if k == 'arg': return 'actor'
    if k == 't': return f'((u{8 * v[3]} *)0x{v[1]:08X})[{ctext(v[2], glob)}]'
    if k == 'g': return _gname(v[1], v[2] if len(v) > 2 else 1)
    if k == 'f':
        base = v[1]
        inner = ctext(base, glob)
        if base[0] == 'g': inner = f'((Tomba2PickupObject *){inner})'
        return f'{inner}->{_ident(v[2])}'
    if k == 'ret': return f'r{v[3]}' if len(v) > 3 else f'{v[1]}()'
    if k == 'cmp':
        if v[1] == '<' and v[3] == ('c', 1):
            x = v[2]
            if x[0] == 'op' and x[1] == '^' and x[3] == ('c', 1): return f'{ctext(x[2], glob)} == 1'
            return f'!{ctext(x, glob)}' if x[0] in ('f', 'g', 'ret') else f'({ctext(x, glob)}) == 0'
        return f'{ctext(v[2], glob)} {v[1]} {ctext(v[3], glob)}'
    if k == 'op':
        if v[1] == '+' and v[3][0] == 'c' and _signed(v[3][1]) < 0:
            return f'({ctext(v[2], glob)} - {-_signed(v[3][1])})'
        return f'({ctext(v[2], glob)} {v[1]} {ctext(v[3], glob)})'
    return 'unknown'


def _negate(op):
    return {'==': '!=', '!=': '==', '<': '>=', '>=': '<', '<=': '>', '>': '<='}[op]


class _State:
    __slots__ = ('regs', 'conds', 'effects', 'seen', 'steps', 'bound', 'ctx', 'stack', 'lohi')

    def __init__(self):
        self.regs = {0: ('c', 0), 4: ('arg',), 29: ('sp', 0)}
        self.stack, self.lohi = {}, None
        self.conds, self.effects, self.seen, self.steps, self.bound = [], [], {}, 0, None
        self.ctx = None

    def fork(self):
        s = _State()
        s.regs, s.conds, s.effects = dict(self.regs), list(self.conds), list(self.effects)
        s.seen, s.steps, s.bound, s.ctx = dict(self.seen), self.steps, self.bound, self.ctx
        s.stack, s.lohi = dict(self.stack), self.lohi
        return s


def decode(exe, overlay, start, plain_call, symbol, glob, depth=0, shown=None):
    """[{'conds': [str], 'effects': [str], 'result': str}] for the routine at `start`.
    `plain_call(name, consts)` words a call (or None), `symbol(addr)` names a routine, `glob(addr)` a global."""
    from game import event_script
    base = struct.unpack_from('<I', exe, 24)[0]
    image = next((im for im in (Image(overlay, event_script.BASE), Image(exe[0x800:], base)) if start in im), None)
    if image is None:
        return []
    first = _State()
    first.ctx = (exe, overlay, depth, set() if shown is None else shown)
    paths, work = [], [(start, first)]
    spent = 0
    while work and len(paths) < MAX_PATHS and spent < MAX_TOTAL:
        at, st = work.pop()
        spent += 1
        while at in image and st.steps < MAX_STEPS:
            if st.seen.get(at, 0) >= 2:
                break
            st.seen[at] = st.seen.get(at, 0) + 1
            spent += 0.02
            if spent > MAX_TOTAL: break
            i = image.at(at)
            n = i.name
            if n in ('beq', 'bne', 'blez', 'bgtz', 'bltz', 'bgez'):
                slot = image.at(at + 4) if at + 4 in image else None
                taken_text = _branch(i, st, glob)
                if slot is not None: _step(slot, st, plain_call, symbol, glob)
                st.steps += 2
                if taken_text is None:                     # unconditional (beq r, r)
                    at = i.target; continue
                other = st.fork()
                st.conds.append(taken_text[0]); other.conds.append(taken_text[1])
                work.append((at + 8, other))
                at = i.target
                continue
            if n == 'j':
                if at + 4 in image: _step(image.at(at + 4), st, plain_call, symbol, glob)
                at = i.target; st.steps += 2; continue
            if n == 'jr':
                if at + 4 in image: _step(image.at(at + 4), st, plain_call, symbol, glob)
                if i.rs != 31:
                    index = _table_index(image, at, i.rs, st)
                    for k, target in enumerate(index[1] if index else ()):
                        other = st.fork()
                        other.conds.append((text(index[0], glob), '==', str(k)))
                        work.append((target, other))
                    break
                if i.rs == 31:
                    v0 = st.regs.get(2)
                    if _c(): result = f'return {text(v0, glob)};' if v0 else 'return;'
                    elif v0 and v0[0] == 'c': result = RETURNS.get(v0[1], f'returns {v0[1]}')
                    elif v0 and v0[0] == 'cmp': result = f'finishes once {text(v0, glob)}, waits until then'
                    else: result = f'returns {text(v0, glob)}' if v0 else 'returns'
                    paths.append(dict(conds=_simplify(st.conds), effects=st.effects, result=result))
                break
            if n == 'jal' or n == 'jalr':
                slot = image.at(at + 4) if at + 4 in image else None
                if slot is not None: _step(slot, st, plain_call, symbol, glob)
                _call(i, st, plain_call, symbol, glob)
                st.steps += 2; at += 8; continue
            _step(i, st, plain_call, symbol, glob)
            st.steps += 1; at += 4
    return _tidy(paths)


def _emit(st, text):
    """Record an effect; in C mode with the number of tests passed so far, so it prints where it happened."""
    st.effects.append((len(st.conds), text) if _c() else text)


def _table(a):
    """(base, index) when `a` is a constant table base plus a scaled index, else None."""
    if a[0] != 'op' or a[1] != '+': return None
    for base, idx in ((a[2], a[3]), (a[3], a[2])):
        if base[0] == 'c' and base[1] >= 0x80000000 and idx[0] != 'c':
            if idx[0] == 'op' and idx[1] == '<<' and idx[3][0] == 'c': return base[1], idx[2]
            return base[1], idx
    return None


def _val(st, r):
    return st.regs.get(r, ('unk',))


def _branch(i, st, glob):
    """((left, op, right) if taken, the same if not), or None when it always branches."""
    n = i.name
    a = _unshift(_val(st, i.rs))
    if n in ('beq', 'bne'):
        b = _unshift(_val(st, i.rt))
        if i.rs == i.rt: return None if n == 'beq' else (('never', '', ''), ('always', '', ''))
        eq = n == 'beq'
        for x, y in ((a, b), (b, a)):
            if x[0] == 'cmp' and y == ('c', 0):         # an slt result tested against zero
                held = (text(x[2], glob), x[1], text(x[3], glob))
                gone = (held[0], _negate(x[1]), held[2])
                return (gone, held) if eq else (held, gone)
        t = (text(a, glob), '==', text(b, glob))
        f = (t[0], '!=', t[2])
        return (t, f) if eq else (f, t)
    ops = {'blez': ('<=', '>'), 'bgtz': ('>', '<='), 'bltz': ('<', '>='), 'bgez': ('>=', '<')}[n]
    return ((text(a, glob), ops[0], '0'), (text(a, glob), ops[1], '0'))


def _table_index(image, at, reg, st):
    """(the switch value, [case targets]) of `lui; addiu; sll; addu; lw reg; jr reg`, or None."""
    lines = [image.at(at - n * 4) for n in range(1, 18) if at - n * 4 in image]
    load = next((n for n, i in enumerate(lines) if i.name == 'lw' and i.rt == reg), None)
    if load is None: return None
    bound = next((i for i in lines[load:] if i.name == 'sltiu'), None)
    count = bound.unsigned if bound else 64
    for n in range(load + 1, len(lines)):
        i = lines[n]
        if i.name != 'lui': continue
        low = sum(j.imm for j in lines[load + 1:n] if j.name == 'addiu' and j.rt == i.rt and j.rs == i.rt)
        table = (i.unsigned << 16) + low + lines[load].imm
        out = []
        for k in range(min(count, 64)):
            word = _read_word(image, table + 4 * k)
            if word is None or word not in image or abs(word - at) > 0x8000: break
            out.append(word)
        if out:
            return (st.bound or ('unk',), out)
    return None


def _read_word(image, address):
    if address not in image or address + 4 not in image: return None
    return struct.unpack_from('<I', image.data, address - image.base)[0]


def _unshift(v):
    """A half-word kept in the top of a register (`<< 16`) compares like the half-word."""
    return v[2] if v[0] == 'op' and v[1] == '<<' and v[3] in (('c', 16), ('c', 24)) else v


def _simplify(conds):
    """Tests as words; of several on one value, the equality alone says it all."""
    conds = list(dict.fromkeys(conds))
    exact = {l for l, op, r in conds if op == '=='}
    kept = [c for c in conds if c[1] == '==' or c[0] not in exact]
    return [f'{l} {op} {r}'.strip() for l, op, r in kept]


def _const(v):
    return v[1] if v[0] == 'c' else None


def _step(i, st, plain_call, symbol, glob):
    n, r = i.name, st.regs
    if n == 'lui': r[i.rt] = ('c', (i.unsigned << 16) & 0xffffffff)
    elif n in ('addiu', 'addi'):
        a = _val(st, i.rs)
        if a[0] == 'sp': r[i.rt] = ('sp', a[1] + i.imm)
        else: r[i.rt] = ('c', (a[1] + i.imm) & 0xffffffff) if a[0] == 'c' else (('op', '+', a, ('c', i.imm)) if i.rt != i.rs or True else a)
    elif n == 'ori':
        a = _val(st, i.rs)
        r[i.rt] = ('c', a[1] | i.unsigned) if a[0] == 'c' else ('op', '|', a, ('c', i.unsigned))
    elif n == 'xori':
        a = _val(st, i.rs)
        r[i.rt] = ('c', a[1] ^ i.unsigned) if a[0] == 'c' else ('op', '^', a, ('c', i.unsigned))
    elif n == 'andi':
        a = _val(st, i.rs)
        r[i.rt] = ('c', a[1] & i.unsigned) if a[0] == 'c' else ('op', '&', a, ('c', i.unsigned))
    elif n in ('addu', 'or', 'subu', 'and'):
        a, b = _val(st, i.rs), _val(st, i.rt)
        if n in ('addu', 'or') and (b == ('c', 0) or a == ('c', 0)): r[i.rd] = a if b == ('c', 0) else b
        elif a[0] == 'c' and b[0] == 'c':
            r[i.rd] = ('c', {'addu': a[1] + b[1], 'or': a[1] | b[1], 'subu': a[1] - b[1], 'and': a[1] & b[1]}[n] & 0xffffffff)
        else: r[i.rd] = ('op', {'addu': '+', 'or': '|', 'subu': '-', 'and': '&'}[n], a, b)
    elif n == 'sll':
        a = _val(st, i.rt)
        r[i.rd] = ('c', (a[1] << i.shift) & 0xffffffff) if a[0] == 'c' else ('op', '<<', a, ('c', i.shift))
    elif n in ('srl', 'sra'):
        a = _val(st, i.rt)
        if a[0] == 'op' and a[1] == '<<' and a[3] == ('c', i.shift) and i.shift in (16, 24): r[i.rd] = a[2]
        else: r[i.rd] = ('op', '>>', a, ('c', i.shift)) if a[0] != 'c' else ('c', a[1] >> i.shift)
    elif n in ('mult', 'multu'):
        st.lohi = (('op', '*', _val(st, i.rs), _val(st, i.rt)), ('unk',))
    elif n in ('div', 'divu'):
        a, b = _val(st, i.rs), _val(st, i.rt)
        st.lohi = (('op', '/', a, b), ('op', '%', a, b))
    elif n in ('mflo', 'mfhi'):
        r[i.rd] = (st.lohi[0 if n == 'mflo' else 1] if st.lohi else ('unk',))
    elif n in ('sllv', 'srlv', 'srav'):
        r[i.rd] = ('op', '<<' if n == 'sllv' else '>>', _val(st, i.rt), _val(st, i.rs))
    elif n == 'xor':
        r[i.rd] = ('op', '^', _val(st, i.rs), _val(st, i.rt))
    elif n == 'add':
        a, b = _val(st, i.rs), _val(st, i.rt)
        r[i.rd] = a if b == ('c', 0) else b if a == ('c', 0) else ('op', '+', a, b)
    elif n in ('slti', 'sltiu'):
        if n == 'sltiu': st.bound = _val(st, i.rs)       # the switch value, before it is scaled into a table index
        r[i.rt] = ('cmp', '<', _val(st, i.rs), ('c', i.imm & 0xffffffff if n == 'sltiu' else i.imm))
    elif n in ('slt', 'sltu'):
        r[i.rd] = ('cmp', '<', _val(st, i.rs), _val(st, i.rt))
    elif n in LOADS:
        a = _val(st, i.rs)
        t = _table(a)
        if a[0] == 'sp': r[i.rt] = st.stack.get(a[1] + i.imm, ('unk',))
        elif t: r[i.rt] = ('t', (t[0] + i.imm) & 0xffffffff, t[1], LOADS[n])
        elif a[0] == 'c' and (a[1] + i.imm) >= 0x80000000: r[i.rt] = ('g', (a[1] + i.imm) & 0xffffffff, LOADS[n])
        elif a[0] in ('arg', 'f', 'g', 'ret'): r[i.rt] = ('f', a, i.imm & 0xffff)
        else: r[i.rt] = ('unk',)
    elif n in STORES:
        a, v = _val(st, i.rs), _val(st, i.rt)
        if a[0] == 'sp': st.stack[a[1] + i.imm] = v
        elif a[0] == 'c' and a[1] + i.imm >= 0x80000000:
            where = (a[1] + i.imm) & 0xffffffff
            if _c(): _emit(st, f'{_gname(where, STORES[n])} = {text(v, glob)};')
            else: _emit(st, f'set {GLOBAL_WORDS.get(where) or glob(where)} to {text(v, glob)}')
        elif a[0] in ('arg', 'f', 'g', 'ret') and _c():
            base = text(a, glob)
            if a[0] == 'g': base = f'((Tomba2PickupObject *){base})'
            _emit(st, f'{base}->{_ident(i.imm & 0xffff)} = {text(v, glob)};')
        elif a[0] in ('arg', 'f', 'g', 'ret'):
            _emit(st, f'set the {BASE_FIELDS.get(i.imm & 0xffff, "field +0x%02X" % (i.imm & 0xffff))} of {text(a, glob)} to {text(v, glob)}')
    else:
        from game import event_script
        d = event_script._dest(i)
        if d not in (None, 0): r[d] = ('unk',)
    r[0] = ('c', 0)


def _call(i, st, plain_call, symbol, glob):
    target = i.target if i.name == 'jal' else None
    name = symbol(target) if target is not None else 'a routine in a register'
    args = [_val(st, k) for k in (4, 5, 6, 7)]
    if _c():
        _call_c(i, st, target, name, args, symbol, glob)
        return
    consts = [_const(a) for a in args]
    said = plain_call(name, consts) if target is not None else None
    if said is None and target is not None and args[0] == ('arg',) and st.ctx and st.ctx[2] < 2:
        # An overlay routine run on this actor: say what it does, indented under the call.
        from game import event_script
        exe, overlay, depth, shown = st.ctx
        if target in shown:
            said = f'run {name} (this actor)'          # described where it first appears
        elif event_script.BASE <= target < event_script.BASE + len(overlay):
            shown.add(target)
            sub = decode(exe, overlay, target, plain_call, symbol, glob, depth + 1, shown)
            if sub:
                said = f'run {name} (this actor), which does:' + chr(10) + chr(10).join('        ' + l for l in lines(sub))
    if said is None:
        shown = ', '.join(text(a, glob) for a in args[:3] if a[0] != 'unk')
        said = f'run {name}' + (f' ({shown})' if shown else '')
    if said:
        _emit(st, said)
    for k in list(range(1, 16)) + [24, 25]: st.regs[k] = ('unk',)     # s0-s7 survive a call
    st.regs[2] = ('ret', name, tuple(args))
    st.regs[31] = ('unk',)


def _call_c(i, st, target, name, args, symbol, glob):
    """A call as a C statement; an overlay routine run on this actor is decoded once, into ctx's `shown`."""
    from game import event_script
    known = [k for k, a in enumerate(args) if a[0] != 'unk']
    shown_args = ', '.join(text(a, glob) if a[0] != 'unk' else '0 /* ? */' for a in args[:(known[-1] + 1 if known else 0)])
    call = f'{name}({shown_args})' if target is not None else f'(*fn)({shown_args})'
    keeps = any(w in name for w in ('Spawn', 'Allocate', 'Create'))
    st.regs['rn'] = st.regs.get('rn', 0) + 1
    rn = st.regs['rn']
    _emit(st, f'r{rn} = {call};' if keeps else f'{call};')
    if st.ctx and target is not None and args[0] == ('arg',):
        exe, overlay, depth, shown = st.ctx
        if target not in shown and depth < 2 and event_script.BASE <= target < event_script.BASE + len(overlay):
            shown[target] = None
            shown[target] = decode(exe, overlay, target, None, symbol, glob, depth + 1, shown)
    for k in list(range(1, 16)) + [24, 25]: st.regs[k] = ('unk',)
    st.regs[2] = ('ret', name, tuple(args), rn)
    st.regs[31] = ('unk',)


def _tidy(paths):
    """Drop what does nothing and waits; merge paths with the same effects."""
    out, index = [], {}
    for p in paths:
        if not p['effects'] and p['result'].startswith('keeps waiting'):
            continue
        key = (tuple(p['effects']), p['result'])
        if key in index:
            out[index[key]]['alternatives'].append(p['conds'])
            continue
        index[key] = len(out)
        out.append(dict(conds=p['conds'], effects=p['effects'], result=p['result'], alternatives=[]))
    return out


def lines(paths):
    """The paths as an indented tree: tests shared with the path above are printed once."""
    out, before = [], []
    for p in paths:
        conds = p['conds']
        keep = 0
        while keep < min(len(conds), len(before)) and conds[keep] == before[keep]:
            keep += 1
        for depth in range(keep, len(conds)):
            out.append('    ' * depth + f'When {conds[depth]}:')
        pad = '    ' * len(conds)
        if not conds and not p['effects']:
            out.append('Always:')
        for e in p['effects']:
            first, *rest = e.split(chr(10))
            out.append(pad + first)
            out.extend(pad + l for l in rest)
        out.append(pad + '→ ' + p['result'])
        before = conds
    return out



def c_function(name, paths):
    """One routine as C: tests shared with the path above nest once; each statement prints at the
    depth it happened at (a call before a test it feeds comes before the `if`)."""
    out = [f'int {name}(Tomba2PickupObject *actor)', '{']
    before, emitted = [], set()
    for p in paths:
        conds = p['conds']
        keep = 0
        while keep < min(len(conds), len(before)) and conds[keep] == before[keep]:
            keep += 1
        for depth in range(len(before) - 1, keep - 1, -1):
            out.append('    ' * (depth + 1) + '}')
        by_level = {}
        for level, e in p['effects']:
            by_level.setdefault(min(level, len(conds)), []).append(e)

        def put(depth):
            for n, e in enumerate(by_level.get(depth, [])):
                key = (tuple(conds[:depth]), depth, n, e)
                if key not in emitted:
                    emitted.add(key)
                    out.extend('    ' * (depth + 1) + line for line in e.split(chr(10)))

        put(0) if keep == 0 else None
        for depth in range(1, len(conds) + 1):
            if depth > keep:
                out.append('    ' * depth + f'if ({conds[depth - 1]}) {{')
            put(depth)
        out.append('    ' * (len(conds) + 1) + p['result'])
        before = conds
    for depth in range(len(before) - 1, -1, -1):
        out.append('    ' * (depth + 1) + '}')
    out.append('}')
    return out


def c_text(exe, overlay, start, symbol, glob):
    """The routine and the overlay routines it runs on its actor, as C. Empty when nothing was found."""
    _mode.c = True
    try:
        shown = {}
        main = decode(exe, overlay, start, None, symbol, glob, 0, shown)
        def named(addr):
            n = symbol(addr)
            return f'handler_{addr:08X}' if n.startswith('0x') else n
        blocks = [c_function(named(start), main)] if main else []
        for target, paths in shown.items():
            if paths:
                blocks.append(c_function(named(target), paths))
        text = (chr(10) * 2).join(chr(10).join(b) for b in blocks)
        rows = text.split(chr(10))
        if len(rows) > 600:
            text = chr(10).join(rows[:600] + ['/* ... cut: too many paths to print in full ... */'])
        return text
    finally:
        _mode.c = False


def summary(paths, limit=6):
    """One line for a row: the distinct effects in order."""
    seen = []
    for p in paths:
        for e in p['effects']:
            e = e.split(chr(10))[0].rstrip(':').replace(', which does', '')      # a row is one line
            if e not in seen: seen.append(e)
    if not seen:
        seen = list(dict.fromkeys(p['result'] for p in paths))
    shown = seen[:limit]
    return shown + ([f'… {len(seen) - limit} more'] if len(seen) > limit else [])

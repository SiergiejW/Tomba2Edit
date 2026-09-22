"""A small R3000 + GTE interpreter, for running the game's own actor code.

Not an emulator of the machine: no interrupts, no hardware, no BIOS. It
runs one routine at a time over a RAM image the caller builds out of the
disc - MAIN.EXE, an overlay, an area's files - and stops when the routine
returns to STOP. Routines that would touch what is not modelled (the
render queue, sound, the actor pools) are replaced by Python `hooks`
keyed by entry address; a hook sets v0 and the call returns.

The GTE is here because the transform library is: RotMatrix, MulMatrix0
and ApplyRotMatrix are GTE code, and a part's world matrix comes out of
them. Register layout and command maths follow psx-spx.
"""

import struct

# Loads and stores that land in RAM are read and written straight out of the
# bytearray in run(), which is most of what the interpreter does.
_U32 = struct.Struct("<I")
_U16 = struct.Struct("<H")
_S16 = struct.Struct("<h")

RAM_SIZE = 0x800000                 # 8 MB, so synthetic loads can sit past 2
SCRATCH = 0x1F800000
SCRATCH_SIZE = 0x400
STOP = 0xFFFFFFF0
MASK = 0xFFFFFFFF
GPU_STATUS = 0x1F801814
GPU_READY = 0x1C000000
# GTE.capture: projected vertices are named by screen position, walked in
# a stride coprime to the screen's size so neighbours are far apart.
CAPTURE_WIDTH, CAPTURE_HEIGHT = 320, 240
CAPTURE_STRIDE = 7919
# A hook that returns this only looks: the routine it sits on then runs.
PASS = object()
CAPTURE_DEPTH = 1000
CAPTURE_NCLIP = 0x10000


class EmuError(RuntimeError):
    pass


def s32(v):
    return v - 0x100000000 if v & 0x80000000 else v


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class Memory:
    def __init__(self):
        self.ram = bytearray(RAM_SIZE)
        self.scratch = bytearray(SCRATCH_SIZE)
        self.unmapped = 0

    def _where(self, address):
        a = address & 0x1FFFFFFF
        if a < RAM_SIZE:
            return self.ram, a
        if SCRATCH <= a < SCRATCH + SCRATCH_SIZE:
            return self.scratch, a - SCRATCH
        self.unmapped += 1
        return None, 0

    def load(self, address, data):
        buf, at = self._where(address)
        if buf is None or at + len(data) > len(buf):
            raise EmuError(f"can't load 0x{len(data):X} bytes at 0x{address:08X}")
        buf[at:at + len(data)] = data

    def read(self, address, size):
        buf, at = self._where(address)
        if buf is None or at + size > len(buf):
            # GPU status: always ready, so draw-command waits finish.
            return GPU_READY if address & 0x1FFFFFFF == GPU_STATUS else 0
        return int.from_bytes(buf[at:at + size], "little")

    def write(self, address, size, value):
        buf, at = self._where(address)
        if buf is None or at + size > len(buf):
            return
        buf[at:at + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    def bytes(self, address, size):
        buf, at = self._where(address)
        if buf is None:
            return b"\0" * size
        return bytes(buf[at:at + size])


class GTE:
    def __init__(self):
        self.d = [0] * 32
        self.c = [0] * 32
        # A dict to name vertices: RTPS/RTPT then put out a screen position
        # that is a key here, holding the vertex's camera-space point.
        self.capture = None
        # The names of points an effect's screen-space pieces hang from, and
        # whether the next point named is one (see actor_sim.EFFECT_ANCHORS).
        self.anchors = set()
        self.anchor_next = False

    # --- register access ----------------------------------------------

    def read_data(self, n):
        d = self.d
        if n in (1, 3, 5, 8, 9, 10, 11):
            return s16(d[n]) & MASK
        if n in (7, 16, 17, 18, 19):
            return d[n] & 0xFFFF
        if n == 15:
            return d[14] & MASK
        if n == 28 or n == 29:
            ir = [max(0, min(0x1F, s16(d[9 + k]) >> 7)) for k in range(3)]
            return ir[0] | ir[1] << 5 | ir[2] << 10
        return d[n] & MASK

    def write_data(self, n, v):
        d = self.d
        if n == 15:
            d[12], d[13], d[14] = d[13], d[14], v
        elif n == 30:
            # LZCS, and with it LZCR: how many bits the value leads with -
            # zeroes when it is positive, ones when it is negative, 1 to 32.
            # SquareRoot0 is built on this pair, so without it every
            # distance the game measures comes back saturated.
            d[30] = value = v & MASK
            if value & 0x80000000:
                value = ~value & MASK
            d[31] = 32 - value.bit_length() if value else 32
        elif n == 28:
            d[9] = ((v >> 0) & 0x1F) << 7
            d[10] = ((v >> 5) & 0x1F) << 7
            d[11] = ((v >> 10) & 0x1F) << 7
        elif n in (29, 31):
            pass
        else:
            d[n] = v & MASK

    def read_ctrl(self, n):
        c = self.c
        if n in (4, 12, 20, 26, 27, 29, 30):
            return s16(c[n]) & MASK
        return c[n] & MASK

    def write_ctrl(self, n, v):
        self.c[n] = v & MASK

    # --- helpers --------------------------------------------------------

    def _matrix(self, which):
        c = self.c
        base = (0, 8, 16)[which] if which < 3 else 0
        w = [c[base], c[base + 1], c[base + 2], c[base + 3], c[base + 4]]
        return [[s16(w[0]), s16(w[0] >> 16), s16(w[1])],
                [s16(w[1] >> 16), s16(w[2]), s16(w[2] >> 16)],
                [s16(w[3]), s16(w[3] >> 16), s16(w[4])]]

    def _vector(self, which):
        d = self.d
        if which == 3:
            return [s16(d[9]), s16(d[10]), s16(d[11])]
        xy, z = d[which * 2], d[which * 2 + 1]
        return [s16(xy), s16(xy >> 16), s16(z)]

    def _translation(self, which):
        c = self.c
        base = (5, 13, 21)[which] if which < 3 else None
        if base is None:
            return [0, 0, 0]
        return [s32(c[base]), s32(c[base + 1]), s32(c[base + 2])]

    def _set_mac_ir(self, mac, lm):
        d = self.d
        lo = 0 if lm else -0x8000
        for k in range(3):
            d[25 + k] = mac[k] & MASK
            d[9 + k] = max(lo, min(0x7FFF, mac[k])) & MASK

    def _push_rgb(self):
        d = self.d
        d[20], d[21] = d[21], d[22]
        d[22] = d[6]

    # --- commands -------------------------------------------------------

    def command(self, word):
        cmd = word & 0x3F
        sf = 12 if word & (1 << 19) else 0
        lm = bool(word & (1 << 10))
        d, c = self.d, self.c
        self.c[31] = 0
        if cmd == 0x12:                                   # MVMVA
            mx, v, cv = (word >> 17) & 3, (word >> 15) & 3, (word >> 13) & 3
            m = self._matrix(mx)
            vec = self._vector(v)
            tr = self._translation(cv)
            mac = [(tr[k] * 0x1000 + sum(m[k][j] * vec[j] for j in range(3))) >> sf
                   for k in range(3)]
            self._set_mac_ir(mac, lm)
        elif cmd == 0x3D:                                 # GPF
            ir0 = s16(d[8])
            mac = [(ir0 * s16(d[9 + k])) >> sf for k in range(3)]
            self._set_mac_ir(mac, lm)
            self._push_rgb()
        elif cmd == 0x3E:                                 # GPL
            ir0 = s16(d[8])
            mac = [((s32(d[25 + k]) << sf) + ir0 * s16(d[9 + k])) >> sf
                   for k in range(3)]
            self._set_mac_ir(mac, lm)
            self._push_rgb()
        elif cmd == 0x28:                                 # SQR
            mac = [(s16(d[9 + k]) ** 2) >> sf for k in range(3)]
            self._set_mac_ir(mac, lm)
        elif cmd == 0x0C:                                 # OP
            r = self._matrix(0)
            d1, d2, d3 = r[0][0], r[1][1], r[2][2]
            i1, i2, i3 = s16(d[9]), s16(d[10]), s16(d[11])
            mac = [(d2 * i3 - d3 * i2) >> sf, (d3 * i1 - d1 * i3) >> sf,
                   (d1 * i2 - d2 * i1) >> sf]
            self._set_mac_ir(mac, lm)
        elif cmd in (0x01, 0x30):                         # RTPS / RTPT
            # One matrix and translation for all three vertices.
            m, tr = self._matrix(0), self._translation(0)
            for v in ((0,) if cmd == 0x01 else (0, 1, 2)):
                self._rtp(v, sf, lm, m, tr)
        elif cmd == 0x06 and self.capture is not None:
            # Named screen positions carry no winding: every face faces
            # the camera, and none is too small to draw.
            d[24] = CAPTURE_NCLIP
        elif cmd == 0x06:                                 # NCLIP
            p = [(s16(d[12 + k]), s16(d[12 + k] >> 16)) for k in range(3)]
            d[24] = (p[0][0] * p[1][1] + p[1][0] * p[2][1] + p[2][0] * p[0][1]
                     - p[0][0] * p[2][1] - p[1][0] * p[0][1]
                     - p[2][0] * p[1][1]) & MASK
        elif cmd in (0x2D, 0x2E) and self.capture is not None:
            # Named vertices carry no depth; any ordering-table slot will do.
            d[7] = CAPTURE_DEPTH >> 2
            d[24] = d[7] << 12
        elif cmd == 0x2D:                                 # AVSZ3
            mac0 = s16(c[29]) * ((d[17] & 0xFFFF) + (d[18] & 0xFFFF) + (d[19] & 0xFFFF))
            d[24] = mac0 & MASK
            d[7] = max(0, min(0xFFFF, mac0 >> 12))
        elif cmd == 0x2E:                                 # AVSZ4
            mac0 = s16(c[30]) * sum(d[16 + k] & 0xFFFF for k in range(4))
            d[24] = mac0 & MASK
            d[7] = max(0, min(0xFFFF, mac0 >> 12))
        else:                                             # colour ops
            self._push_rgb()

    def _rtp(self, v, sf, lm, m=None, tr=None):
        d, c = self.d, self.c
        if m is None:
            m, tr = self._matrix(0), self._translation(0)
        vec = self._vector(v)
        x, y, z = vec
        exact = [tr[k] * 0x1000 + m[k][0] * x + m[k][1] * y + m[k][2] * z
                 for k in range(3)]
        mac = [value >> sf for value in exact]
        self._set_mac_ir(mac, lm)
        if self.capture is not None:
            self._name(tuple(value / 4096.0 for value in exact))
            return
        z = max(0, min(0xFFFF, mac[2] >> (12 - sf) if sf == 0 else mac[2]))
        d[16], d[17], d[18] = d[17], d[18], d[19]
        d[19] = z
        h = c[26] & 0xFFFF
        q = min(0x1FFFF, (h * 0x10000 // z) if z > h // 2 else 0x1FFFF)
        sx = max(-0x400, min(0x3FF, (s32(c[24]) + s16(d[9]) * q) >> 16))
        sy = max(-0x400, min(0x3FF, (s32(c[25]) + s16(d[10]) * q) >> 16))
        d[12], d[13] = d[13], d[14]
        d[14] = (sx & 0xFFFF) | (sy & 0xFFFF) << 16
        mac0 = s16(c[27]) * q + s32(c[28])
        d[24] = mac0 & MASK
        d[8] = max(0, min(0x1000, mac0 >> 12))

    def _name(self, point):
        """Project to a screen position that names `point` in capture."""
        d = self.d
        n = (len(self.capture) * CAPTURE_STRIDE + 1) % (CAPTURE_WIDTH * CAPTURE_HEIGHT)
        sx, sy = n % CAPTURE_WIDTH, n // CAPTURE_WIDTH
        self.capture[(sx, sy)] = point
        if self.anchor_next:
            self.anchors.add((sx, sy))
            self.anchor_next = False
        d[16], d[17], d[18] = d[17], d[18], d[19]
        d[19] = CAPTURE_DEPTH
        d[12], d[13] = d[13], d[14]
        d[14] = sx | sy << 16
        d[8] = 0x1000
        # MAC0 as RTPS leaves it at the capture depth: DQA * H/z + DQB, what a
        # screen-space sprite is scaled by (f_DrawProjectedSpriteDefinitionStream).
        c = self.c
        q = min(0x1FFFF, (c[26] & 0xFFFF) * 0x10000 // CAPTURE_DEPTH)
        d[24] = (s16(c[27]) * q + s32(c[28])) & MASK


class CPU:
    def __init__(self, memory=None):
        self.mem = memory or Memory()
        self.gte = GTE()
        self.r = [0] * 32
        self.hi = self.lo = 0
        self.cop0 = [0] * 32
        self.hooks = {}
        self._cache = {}
        self.steps = 0

    def call(self, address, args=(), budget=2_000_000, sp=None):
        """Run the routine at `address` with a0.. = args; returns v0."""
        if not address:
            # A routine this build has none of (functions/game_build.py).
            raise EmuError("no such routine in this build")
        r = self.r
        for n, value in enumerate(args):
            r[4 + n] = value & MASK
        if sp is not None:
            r[29] = sp
        r[31] = STOP
        self.run(address, budget)
        return r[2]

    def run(self, pc, budget):
        r, mem, gte, hooks, cache = self.r, self.mem, self.gte, self.hooks, self._cache
        read, write, ram = mem.read, mem.write, mem.ram
        npc = (pc + 4) & MASK
        steps = 0
        while True:
            if pc == STOP:
                break
            if steps >= budget:
                self.steps += steps
                raise EmuError(f"ran out of budget at 0x{pc:08X}")
            hook = hooks.get(pc)
            if hook is not None:
                value = hook(self)
                if value is not PASS:
                    r[2] = value & MASK
                    pc = r[31]
                    npc = (pc + 4) & MASK
                    steps += 1
                    continue
            ins = cache.get(pc)
            if ins is None:
                if (pc & 0x1FFFFFFF) >= RAM_SIZE or pc & 3:
                    self.steps += steps
                    raise EmuError(f"jumped to 0x{pc:08X}")
                word = _U32.unpack_from(ram, pc & 0x1FFFFFFF)[0]
                ins = (word >> 26, (word >> 21) & 31, (word >> 16) & 31,
                       (word >> 11) & 31, (word >> 6) & 31, word & 63,
                       word & 0xFFFF, s16(word), word & 0x3FFFFFF, word)
                cache[pc] = ins
            op, rs, rt, rd, sh, fn, imm, simm, target, word = ins
            cur = pc
            pc = npc
            npc = (pc + 4) & MASK
            steps += 1

            if op == 0:
                if fn == 33:
                    v = (r[rs] + r[rt]) & MASK
                elif fn == 37:
                    v = r[rs] | r[rt]
                elif fn == 0:
                    v = (r[rt] << sh) & MASK
                elif fn == 8:
                    npc = r[rs]
                    continue
                elif fn == 35:
                    v = (r[rs] - r[rt]) & MASK
                elif fn == 36:
                    v = r[rs] & r[rt]
                elif fn == 3:
                    v = (s32(r[rt]) >> sh) & MASK
                elif fn == 2:
                    v = r[rt] >> sh
                elif fn == 42:
                    v = int(s32(r[rs]) < s32(r[rt]))
                elif fn == 43:
                    v = int(r[rs] < r[rt])
                elif fn == 9:
                    t = r[rs]
                    if rd:
                        r[rd] = (cur + 8) & MASK
                    npc = t
                    continue
                elif fn == 24:
                    p = s32(r[rs]) * s32(r[rt])
                    self.lo, self.hi = p & MASK, (p >> 32) & MASK
                    continue
                elif fn == 25:
                    p = r[rs] * r[rt]
                    self.lo, self.hi = p & MASK, (p >> 32) & MASK
                    continue
                elif fn == 26:
                    a, b = s32(r[rs]), s32(r[rt])
                    if b == 0:
                        self.lo, self.hi = (MASK if a >= 0 else 1), a & MASK
                    else:
                        q = abs(a) // abs(b)
                        if (a < 0) != (b < 0):
                            q = -q
                        self.lo, self.hi = q & MASK, (a - q * b) & MASK
                    continue
                elif fn == 27:
                    a, b = r[rs], r[rt]
                    if b == 0:
                        self.lo, self.hi = MASK, a
                    else:
                        self.lo, self.hi = a // b, a % b
                    continue
                elif fn == 16:
                    v = self.hi
                elif fn == 18:
                    v = self.lo
                elif fn == 17:
                    self.hi = r[rs]
                    continue
                elif fn == 19:
                    self.lo = r[rs]
                    continue
                elif fn == 38:
                    v = r[rs] ^ r[rt]
                elif fn == 39:
                    v = ~(r[rs] | r[rt]) & MASK
                elif fn == 4:
                    v = (r[rt] << (r[rs] & 31)) & MASK
                elif fn == 6:
                    v = r[rt] >> (r[rs] & 31)
                elif fn == 7:
                    v = (s32(r[rt]) >> (r[rs] & 31)) & MASK
                elif fn == 32:
                    v = (r[rs] + r[rt]) & MASK
                elif fn == 34:
                    v = (r[rs] - r[rt]) & MASK
                elif fn == 13:
                    self.steps += steps
                    raise EmuError(f"break at 0x{cur:08X}")
                elif fn == 12:
                    continue
                else:
                    raise EmuError(f"special {fn} at 0x{cur:08X}")
                if rd:
                    r[rd] = v
                continue

            if op == 9:
                if rt:
                    r[rt] = (r[rs] + simm) & MASK
            elif op == 35:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if rt:
                    r[rt] = (_U32.unpack_from(ram, a)[0] if a + 4 <= RAM_SIZE
                             else read((r[rs] + simm) & MASK, 4))
            elif op == 43:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if a + 4 <= RAM_SIZE:
                    _U32.pack_into(ram, a, r[rt])
                else:
                    write((r[rs] + simm) & MASK, 4, r[rt])
            elif op == 15:
                if rt:
                    r[rt] = (imm << 16) & MASK
            elif op == 5:
                if r[rs] != r[rt]:
                    npc = (pc + (simm << 2)) & MASK
            elif op == 4:
                if r[rs] == r[rt]:
                    npc = (pc + (simm << 2)) & MASK
            elif op == 3:
                r[31] = (cur + 8) & MASK
                npc = (pc & 0xF0000000) | (target << 2)
            elif op == 2:
                npc = (pc & 0xF0000000) | (target << 2)
            elif op == 36:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if rt:
                    r[rt] = ram[a] if a < RAM_SIZE else read((r[rs] + simm) & MASK, 1)
            elif op == 33:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if rt:
                    r[rt] = (_S16.unpack_from(ram, a)[0] & MASK if a + 2 <= RAM_SIZE
                             else s16(read((r[rs] + simm) & MASK, 2)) & MASK)
            elif op == 37:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if rt:
                    r[rt] = (_U16.unpack_from(ram, a)[0] if a + 2 <= RAM_SIZE
                             else read((r[rs] + simm) & MASK, 2))
            elif op == 32:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if rt:
                    b = ram[a] if a < RAM_SIZE else read((r[rs] + simm) & MASK, 1)
                    r[rt] = (b - 0x100 if b & 0x80 else b) & MASK
            elif op == 41:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if a + 2 <= RAM_SIZE:
                    _U16.pack_into(ram, a, r[rt] & 0xFFFF)
                else:
                    write((r[rs] + simm) & MASK, 2, r[rt])
            elif op == 40:
                a = (r[rs] + simm) & 0x1FFFFFFF
                if a < RAM_SIZE:
                    ram[a] = r[rt] & 0xFF
                else:
                    write((r[rs] + simm) & MASK, 1, r[rt])
            elif op == 12:
                if rt:
                    r[rt] = r[rs] & imm
            elif op == 13:
                if rt:
                    r[rt] = r[rs] | imm
            elif op == 14:
                if rt:
                    r[rt] = r[rs] ^ imm
            elif op == 10:
                if rt:
                    r[rt] = int(s32(r[rs]) < simm)
            elif op == 11:
                if rt:
                    r[rt] = int(r[rs] < (simm & MASK))
            elif op == 8:
                if rt:
                    r[rt] = (r[rs] + simm) & MASK
            elif op == 6:
                if s32(r[rs]) <= 0:
                    npc = (pc + (simm << 2)) & MASK
            elif op == 7:
                if s32(r[rs]) > 0:
                    npc = (pc + (simm << 2)) & MASK
            elif op == 1:
                value = s32(r[rs])
                taken = value < 0 if rt in (0, 16) else value >= 0
                if rt in (16, 17):
                    r[31] = (cur + 8) & MASK
                if taken:
                    npc = (pc + (simm << 2)) & MASK
            elif op == 18:
                if word & (1 << 25):
                    gte.command(word)
                elif rs == 0:
                    if rt:
                        r[rt] = gte.read_data(rd)
                elif rs == 2:
                    if rt:
                        r[rt] = gte.read_ctrl(rd)
                elif rs == 4:
                    gte.write_data(rd, r[rt])
                elif rs == 6:
                    gte.write_ctrl(rd, r[rt])
            elif op == 50:
                gte.write_data(rt, read((r[rs] + simm) & MASK, 4))
            elif op == 58:
                write((r[rs] + simm) & MASK, 4, gte.read_data(rt))
            elif op == 16:
                if rs == 0:
                    if rt:
                        r[rt] = self.cop0[rd]
                elif rs == 4:
                    self.cop0[rd] = r[rt]
            elif op == 34:                                # lwl
                a = (r[rs] + simm) & MASK
                shift = (3 - (a & 3)) * 8
                w = read(a & ~3, 4)
                r[rt] = ((r[rt] & ((1 << shift) - 1) if shift < 32 else 0)
                         | ((w << shift) & MASK)) if rt else 0
            elif op == 38:                                # lwr
                a = (r[rs] + simm) & MASK
                shift = (a & 3) * 8
                w = read(a & ~3, 4)
                if rt:
                    keep = ~(MASK >> shift) & MASK
                    r[rt] = (r[rt] & keep) | (w >> shift)
            elif op == 42:                                # swl
                a = (r[rs] + simm) & MASK
                shift = (3 - (a & 3)) * 8
                w = read(a & ~3, 4)
                keep = ~(MASK >> shift) & MASK
                write(a & ~3, 4, (w & keep) | (r[rt] >> shift))
            elif op == 46:                                # swr
                a = (r[rs] + simm) & MASK
                shift = (a & 3) * 8
                w = read(a & ~3, 4)
                write(a & ~3, 4, (w & ((1 << shift) - 1)) | ((r[rt] << shift) & MASK))
            else:
                self.steps += steps
                raise EmuError(f"opcode {op} at 0x{cur:08X}")
        self.steps += steps

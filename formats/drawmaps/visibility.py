"""What Town actually draws of its MDAT, the way the game decides it.

Ported from the US decomp (overlay A00, which is src_CurrentArea 0 and
IDX chunk 4): f_CollectVisibleA00GridCellsForCamera,
f_CollectA00GridCellsInsideTriangle and the two environment batches
(named f_DrawTownCreatureGt3Batch / Gt4Batch there). Other areas have
their own copies with their own numbers; this is Town's.

TWO STAGES

    cells       A triangle is laid over the drawmap grid: apex at the
                camera, two far corners `reach` away at the heading
                plus and minus `half_angle`, all of it pushed back by
                the camera's pitch. Every cell it covers that holds
                geometry is listed, up to 254 - if the DRWB lets it:
                drwb[z * 52 + x] & (region << 4), region being the low
                nibble of the cell Tomba stands in.
    polygons    Each listed cell's records go through the GTE. One is
                dropped if any corner is behind the camera or projects
                past +-1024, if it faces away, if no corner has x in
                0..319 or none has y in 0..239, or if it is beyond the
                ordering table. What is left is written to the frame's
                primitive buffer: 40 bytes a triangle, 52 a quad.

Only the second stage costs buffer. The first decides how many records
the CPU has to transform.

CHECKED

Against a PCSX state at Town's start (eye 3516/-1480/2413, yaw -261,
pitch 122, reach 0x3700, half-angle 0x1C7, region 1): the cell list
comes out as the game's own 119, in the same order, and the polygon
stage gives 105 triangles and 109 quads where the buffer in that state
holds 104 and 110.

Game axes, not the viewer's: the game's X is the viewer's Z. A cell is
(x, z) with x counted by the DRWA's first header word.
"""
import math
import struct

import numpy as np

from formats.drawmaps.drwa_parser import parse_drwa

CELL = 0x280
MASK_SIDE = 52
MAX_CELLS = 0xFE
TRI_BYTES, QUAD_BYTES = 40, 52

# Where a record keeps (VX, VY, VZ), in the order the batch loads them.
TRI_CORNERS = ((16, 18, 20), (24, 26, 22), (28, 30, 32))
QUAD_CORNERS = ((20, 22, 24), (28, 30, 26), (32, 34, 36), (40, 42, 38))


def _rsin(angle):
    return int(round(4096 * math.sin((angle & 0xFFF) * math.pi / 2048)))


def _rcos(angle):
    return int(round(4096 * math.cos((angle & 0xFFF) * math.pi / 2048)))


def _div(a, b):
    """C's division: towards zero."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def triangle(eye_x, eye_z, yaw, pitch, reach, half_angle):
    """The view triangle's three corners, in cells."""
    angle = (-half_angle - yaw) & 0xFFF
    left = (eye_x + (_rsin(angle) * reach >> 12), eye_z + (_rcos(angle) * reach >> 12))
    angle = (half_angle - yaw) & 0xFFF
    right = (eye_x + (_rsin(angle) * reach >> 12), eye_z + (_rcos(angle) * reach >> 12))
    tilt = abs(_rsin(pitch))
    back_x = ((_rsin(-yaw) * tilt >> 12) * reach * 5) >> 16
    back_z = ((_rcos(-yaw) * tilt >> 12) * reach * 5) >> 16
    return [(_div(x - back_x, CELL), _div(z - back_z, CELL))
            for x, z in ((eye_x, eye_z), left, right)]


def scan(corners, count_x, count_z):
    """Cells (x, z) the scan converter visits, in its own order."""
    (xa, za), (xb, zb), (xc, zc) = corners
    out = []
    if zb == za and zb == zc:
        return out
    x_mid, z_mid, x_other, z_other = xa, za, xb, zb
    if z_other < za:
        x_mid, z_mid, x_other, z_other = xb, zb, xa, za
    x_top, z_top, x_low, z_low = x_mid, z_mid, xc, zc
    if z_low < z_mid:
        x_top, z_top, x_low, z_low = xc, zc, x_mid, z_mid
    x_mid, z_mid = x_other, z_other
    if z_low < z_other:
        x_mid, x_low, z_mid, z_low = x_low, x_other, z_low, z_other
    upper, whole = z_mid - z_top, z_low - z_top
    if z_mid == z_top:
        right, left, short = max(x_top, x_mid) << 16, min(x_top, x_mid) << 16, 0
    else:
        right = left = x_top << 16
        short = _div((x_mid - x_top) * 0x10000, upper)
    right += 0x10000
    lower = 0 if z_mid == z_low else _div((x_low - x_mid) * 0x10000, z_low - z_mid)
    long_ = _div((x_low - x_top) * 0x10000, whole)

    def row(z, lo, hi):
        if 0 <= z < count_z:
            for x in range(max(lo, 0), min(hi, count_x - 1) + 1):
                out.append((x, z))

    if -whole * (x_mid - x_top) + (x_low - x_top) * upper < 1:
        for z in range(z_top, z_mid):
            row(z, left >> 16, right >> 16)
            right += short
            left += long_
        for z in range(z_mid, z_low + 1):
            row(z, left >> 16, right >> 16)
            left += long_
            right += lower
    else:
        for z in range(z_top, z_mid):
            row(z, left >> 16, right >> 16)
            left += short
            right += long_
        for z in range(z_mid, z_low + 1):
            row(z, left >> 16, right >> 16)
            left += lower
            right += long_
    return out


def region_at(drwb, x, z):
    """The region of the cell a world position is in."""
    return drwb[_div(int(z), CELL) * MASK_SIDE + _div(int(x), CELL)] & 0xF


class Scenery:
    """An MDAT's cells and polygons, read for counting."""

    def __init__(self, blob):
        grid = parse_drwa(blob)
        self.count_x, self.count_z = struct.unpack_from("<HH", blob, 0)
        self.pointers = struct.unpack_from(f"<{self.count_x * self.count_z}H", blob, 4)
        self.cost = {g.pointer: g.tris * TRI_BYTES + g.quads * QUAD_BYTES for g in grid.groups}
        corners, quad, owner, mode = [], [], [], []
        for g in grid.groups:
            at = g.offset + 4
            for count, size, layout in ((g.tris, 36, TRI_CORNERS), (g.quads, 44, QUAD_CORNERS)):
                for _ in range(count):
                    own = [[struct.unpack_from("<h", blob, at + o)[0] for o in corner]
                           for corner in layout]
                    corners.append(own + own[2:] if size == 36 else own)
                    quad.append(size == 44)
                    owner.append(g.pointer)
                    mode.append(blob[at + 7] & 3)
                    at += size
        self._corners = np.array(corners, dtype=np.int64).reshape(-1, 4, 3)
        self._quad = np.array(quad, dtype=bool)
        self._owner = np.array(owner)
        self._mode = np.array(mode)

    def listed(self, corners, drwb=None, region=0):
        """Stage one: ([pointer, ...], packet bytes they hold)."""
        out = []
        for x, z in scan(corners, self.count_x, self.count_z):
            pointer = self.pointers[x * self.count_z + z]
            if pointer == 0xFFFF:
                continue
            if drwb is not None and not drwb[z * MASK_SIDE + x] & (region << 4):
                continue
            if len(out) < MAX_CELLS:
                out.append(pointer)
        return out, sum(self.cost[p] for p in out)

    def emitted(self, pointers, rotation, translation, h=350, offset=(160 << 16, 120 << 16),
                x_scale=(1, 1)):
        """Stage two: (triangles, quads) that reach the primitive buffer.

        `rotation` and `translation` are the camera matrix the game
        keeps at scratchpad 0xF8. `x_scale` is what an emulator's
        widescreen hack multiplies screen X by - (3, 4) for 16:9."""
        _pick, keep, quad = self._kept(pointers, rotation, translation, h, offset, x_scale)
        return int((keep & ~quad).sum()), int((keep & quad).sum())

    def drawn(self, pointers, rotation, translation, h=350, offset=(160 << 16, 120 << 16),
              x_scale=(1, 1)):
        """Stage two, a record at a time: bytes each writes (0, 40 or 52),
        in the order the groups hold them."""
        pick, keep, quad = self._kept(pointers, rotation, translation, h, offset, x_scale)
        out = np.zeros(len(self._owner), dtype=np.int64)
        out[pick] = keep * np.where(quad, QUAD_BYTES, TRI_BYTES)
        return out

    def _kept(self, pointers, rotation, translation, h, offset, x_scale):
        pick = np.isin(self._owner, list(pointers))
        v, quad, mode = self._corners[pick], self._quad[pick], self._mode[pick]
        mac = (np.einsum("ij,nkj->nki", np.asarray(rotation, dtype=np.int64), v)
               + np.asarray(translation, dtype=np.int64) * 4096) >> 12
        bad = (mac[..., 0] > 0x7FFF) | (mac[..., 0] < -0x8000)
        bad |= (mac[..., 1] > 0x7FFF) | (mac[..., 1] < -0x8000)
        sz = mac[..., 2]
        bad |= (sz < 0) | (sz > 0xFFFF)
        depth = np.clip(sz, 1, 0xFFFF)
        bad |= h >= depth * 2
        quotient = np.minimum(0x1FFFF, ((h * 0x20000) // depth + 1) // 2)
        across = quotient * mac[..., 0]
        across = np.sign(across) * (np.abs(across) * x_scale[0] // x_scale[1])
        sx = (across + offset[0]) >> 16
        sy = (quotient * mac[..., 1] + offset[1]) >> 16
        bad |= (sx < -0x400) | (sx > 0x3FF) | (sy < -0x400) | (sy > 0x3FF)
        keep = ~bad[:, :3].any(axis=1) & (~quad | ~bad[:, 3])
        facing = (sx[:, 0] * sy[:, 1] + sx[:, 1] * sy[:, 2] + sx[:, 2] * sy[:, 0]
                  - sx[:, 0] * sy[:, 2] - sx[:, 1] * sy[:, 0] - sx[:, 2] * sy[:, 1])
        keep &= facing > 0
        on_x = (sx >= 0) & (sx < 320)
        on_y = (sy >= 0) & (sy < 240)
        on_x[~quad, 3] = False
        on_y[~quad, 3] = False
        keep &= on_x.any(axis=1) & on_y.any(axis=1)
        # ZSF3 0x155 and ZSF4 0x100: the mean depth over four. Record flags
        # 1 and 2 take the farthest or nearest corner instead.
        mean = np.where(quad, (0x100 * sz.sum(axis=1)) >> 12, (0x155 * sz[:, :3].sum(axis=1)) >> 12)
        otz = np.where(mode == 1, sz.max(axis=1) >> 2, np.where(mode == 2, sz.min(axis=1) >> 2, mean))
        band = otz >> 10
        slot = (otz >> np.minimum(band, 31)) + band * 0x200
        keep &= (slot >= 4) & (slot <= 0x7FF)
        return pick, keep, quad

    def emitted_bytes(self, *args, **kwargs):
        triangles, quads = self.emitted(*args, **kwargs)
        return triangles * TRI_BYTES + quads * QUAD_BYTES

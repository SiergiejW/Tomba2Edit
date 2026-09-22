"""Town collision: the planes Coal Mining Town and Circus Village keep in
their overlays.

FUN_80048d3c gives areas 2, 3, 7, 0x14 and 0x15 no SCLD. A02 and A07 keep
their ground in the overlay instead - one dataset for the streets and one
or two for the rooms - each in three blocks:

    list stream   u16 plane indices, a 0xFFFF after each cell's run
    grid          64 x 64 u16, one per 512-unit cell: where its run starts
                  in the stream, 0xFFFF for an empty cell
    planes        48 bytes each
        +0   u32  the plane's name, a C string in the overlay: kabe (wall),
                  doa (door), yuka/uka (floor), hasigo (ladder), ...
        +4   u32  flags
        +8   i16  normal x, y, z, 4096 to 1
        +14  i16  d, with normal . p + d * 4096 = 0
        +16  i16  four corners (x, z)
        +32  i16  four edge normals (x, z)

Every plane in both towns is level: a wall is a floor's edge whose normal
says which side is solid. A dataset is found by shape - a pointer triple
(stream, grid, planes) with the planes exactly 0x2000 past the grid - and
checked against decomp/town collision (725 planes over 5 datasets).
"""
import struct
from dataclasses import dataclass

from functions import game_build

OVERLAY_BASE = 0x80108F9C
GRID = 64
CELL = 512
GRID_BYTES = GRID * GRID * 2
PLANE_SIZE = 48
EMPTY = 0xFFFF
ONE = 4096
MAX_STREAM = 0x4000
NAME_LIMIT = 24

# US retail's; another build's while it is open (functions/game_build.py).
_BUILD = game_build.Addresses(globals(), main=("OVERLAY_BASE",))

KINDS = (("kabe", "wall"), ("doa", "door"), ("yuka", "floor"),
         ("uka", "floor"), ("hasigo", "ladder"), ("ami", "net"),
         ("camera", "camera"), ("asi", "foothold"), ("ball", "ball"))


@dataclass
class Plane:
    index: int
    address: int
    name: str
    flags: int
    normal: tuple
    d: int
    corners: tuple
    edges: tuple

    def height(self, x, z):
        nx, ny, nz = self.normal
        if not ny:
            return float(-self.d)
        return -(nx * x + nz * z + self.d * ONE) / ny

    def outline(self):
        """The four corners, in the game's axes."""
        return [(x, self.height(x, z), z) for x, z in self.corners]

    @property
    def kind(self):
        name = self.name.lower()
        for prefix, kind in KINDS:
            if name.startswith(prefix):
                return kind
        return "other"


@dataclass
class Dataset:
    stream: int
    grid: int
    at: int
    planes: list


def _string(data, address):
    at = address - OVERLAY_BASE
    if not 0 <= at < len(data):
        return ""
    end = data.find(b"\0", at, at + NAME_LIMIT)
    raw = data[at:end if end >= 0 else at]
    return raw.decode("ascii") if all(32 <= b < 127 for b in raw) else ""


def _plane(data, address, index):
    at = address - OVERLAY_BASE
    name_at, flags, nx, ny, nz, d = struct.unpack_from("<IIhhhh", data, at)
    if not 3900 < (nx * nx + ny * ny + nz * nz) ** 0.5 < 4300:
        return None
    corners = struct.unpack_from("<8h", data, at + 16)
    edges = struct.unpack_from("<8h", data, at + 32)
    return Plane(index, address, _string(data, name_at), flags, (nx, ny, nz),
                 d, tuple(zip(corners[0::2], corners[1::2])),
                 tuple(zip(edges[0::2], edges[1::2])))


def find(data):
    """[Dataset] in an overlay's bytes, in the order the overlay holds them."""
    base, end = OVERLAY_BASE, OVERLAY_BASE + len(data)
    count = len(data) // 4
    words = struct.unpack_from(f"<{count}I", data)
    seen, out = set(), []
    for n in range(count - 2):
        stream, grid, planes = words[n], words[n + 1], words[n + 2]
        if planes - grid != GRID_BYTES or not base <= stream < grid < end:
            continue
        span = grid - stream
        if span & 1 or span > MAX_STREAM or (stream, grid, planes) in seen:
            continue
        used = [i for i in struct.unpack_from(f"<{span // 2}H", data,
                                              stream - base) if i != EMPTY]
        if not used:
            continue
        total = max(used) + 1
        if planes + total * PLANE_SIZE > end:
            continue
        parsed = [_plane(data, planes + k * PLANE_SIZE, k) for k in range(total)]
        if any(p is None for p in parsed):
            continue
        seen.add((stream, grid, planes))
        out.append(Dataset(stream, grid, planes, parsed))
    return out

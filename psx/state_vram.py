"""VRAM out of a savestate - what the game really has loaded.

WHY THIS EXISTS

A chunk's shard table says what the IMG writes into VRAM. It does not
say what the GAME writes into VRAM, and the difference is not small.
Checked against a PCSX state taken in Town of the Fishermen, three
things occupy memory no shard table mentions:

    the display     x halfwords 0-319, every row. Two 320x256 buffers,
                    redrawn every frame.
    runtime CLUTs   palettes uploaded from the DAT rather than the IMG.
                    One sits at VRAM 0x80762 and reads as an obvious
                    colour ramp: ff7b df77 de6f de6b ...
    sprite art      patches in pages 24 and 25 that no chunk declares.

Put a texture in any of those and it is overwritten the moment the game
runs, which is exactly what a migration planned off the shard tables
alone does. So a savestate is treated here as ground truth about
occupancy: whatever has a non-zero halfword in it is spoken for.

Anything this cannot see is still a risk - a state is one moment in one
room - so the honest use is to load several, one per area a model has to
survive in, and take the union.

WHERE VRAM IS IN THE FILE

Not at a fixed offset worth trusting: PCSX-Reloaded writes its register
block before the GPU's, and that block's size is its own business. So
the base is FOUND, by looking for bytes we already know must be there -
the resident chunk's own decompressed pixels - and then checked against
more of them.
"""
import gzip
import struct

import numpy as np

from psx import vram as psx_vram

# How many independent probes have to land before a base is believed.
NEEDED_PROBES = 6

# How much of the file to consider. VRAM cannot start before main RAM
# has been written out.
FIRST_PLAUSIBLE = 0x200000


class StateVRAMError(ValueError):
    """Raised when VRAM can't be found in a state."""


def _payload(path):
    """The state's bytes, unwrapped far enough that VRAM is in there.

    Three shapes, and all three matter because people use all three:
    PCSX-Reloaded writes its sections plainly (sometimes gzipped),
    DuckStation writes a compressed stream of named sections, and a raw
    dump is just itself. The DuckStation case is not optional - its
    payload is Deflate or Zstandard, and searching the file as it sits
    on disc finds nothing at all, which reads as "not from this disc"
    when the truth is "not yet decompressed"."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if data.startswith(b"DUCC"):
        return _duckstation_payload(data)
    return data


def _probes(reference, count=24, size=48):
    """Distinctive byte runs from a VRAM we know is loaded."""
    out = []
    step = max(1, len(reference) // (count * 8))
    for at in range(0, len(reference) - size, step):
        run = bytes(reference[at:at + size])
        if len(set(run)) > 16:
            out.append((at, run))
            if len(out) >= count:
                break
    return out


def find_vram(data, reference):
    """Where VRAM starts in a savestate's bytes.

    `reference` is a VRAM buffer the state must contain - the
    decompressed chunk of a resident area. The first probe that is found
    proposes a base; the rest either confirm it or it is rejected and
    the next candidate tried."""
    probes = _probes(reference)
    if not probes:
        raise StateVRAMError("the reference VRAM has nothing distinctive in it")
    first_at, first_run = probes[0]
    start = 0
    while True:
        found = data.find(first_run, start)
        if found < 0:
            raise StateVRAMError(
                "the state decompressed, but none of AREA_01's pixels are "
                "in it. That usually means it was taken on a different "
                "disc, or in a room whose VRAM has been fully replaced - "
                "try a state taken while standing in a level.")
        base = found - first_at
        start = found + 1
        if base < FIRST_PLAUSIBLE or base + psx_vram.VRAM_SIZE > len(data):
            continue
        agreed = sum(1 for at, run in probes
                     if data[base + at:base + at + len(run)] == run)
        if agreed >= NEEDED_PROBES:
            return base


def read(path, reference):
    """The 1MB of VRAM a savestate holds."""
    data = _payload(path)
    base = find_vram(data, reference)
    return data[base:base + psx_vram.VRAM_SIZE]


def occupancy(vram):
    """Which halfwords hold anything, as a map matching vram_map's."""
    raw = np.frombuffer(bytes(vram[:psx_vram.VRAM_SIZE]), dtype=np.uint8)
    rows = raw.reshape(psx_vram.VRAM_ROWS, psx_vram.VRAM_STRIDE)
    return rows.reshape(psx_vram.VRAM_ROWS, -1, 2).any(axis=2)


def runtime_only(vram, shards, areas):
    """What a state holds that NO loaded chunk put there.

    The distinction matters for overwriting. Space a chunk owns can be
    taken - add your shard to that chunk and it goes down last. Space
    the GAME writes cannot be taken by anyone: the display buffers, the
    palettes it uploads from the DAT, its sprite art. Both look
    identical in a raw occupancy map, and the difference is exactly
    "does any loaded chunk declare it".

    `areas` is which chunks were loaded in this state - normally
    resident_areas()'s answer."""
    from psx import vram_map
    return occupancy(vram) & ~vram_map.claims_of(shards, areas)


def resident_areas(vram, shards, chunk_vram, threshold=0.9):
    """Which chunks this state actually has loaded.

    A chunk counts as loaded when its own declared bytes match the state
    almost exactly. On a real state the answer is stark - the loaded
    ones come out at 100% and everything else around 10% - so the
    threshold is not a delicate number."""
    out = []
    for area, rects in shards.items():
        reference = chunk_vram(area)
        if reference is None:
            continue
        total = same = 0
        for x, y, w, h, _packed in rects:
            for row in range(y, y + h):
                at = row * psx_vram.VRAM_STRIDE + x * 2
                a = bytes(reference[at:at + w * 2])
                b = bytes(vram[at:at + w * 2])
                total += len(a)
                same += sum(1 for i in range(len(a)) if a[i] == b[i])
        if total and same / total >= threshold:
            out.append(area)
    return sorted(out)


# DuckStation savestate: 'DUCC', a version, a title and a serial, then a run
# of u32 fields whose last four say how the payload is compressed, how big
# it is either way, and where it starts.
DUCK_TITLE = 128
DUCK_SERIAL = 32
DUCK_FIELDS = 4 + 4 + DUCK_TITLE + DUCK_SERIAL
DUCK_COMPRESSION = DUCK_FIELDS + 8 * 4
DUCK_NONE, DUCK_DEFLATE, DUCK_ZSTD = 0, 1, 2


def _duckstation_payload(data):
    """A DuckStation state's section stream, decompressed."""
    kind, compressed, plain, at = struct.unpack_from("<4I", data,
                                                     DUCK_COMPRESSION)
    body = data[at:at + compressed]
    if len(body) < compressed:
        raise StateVRAMError("the state is cut short - its payload is missing")
    if kind == DUCK_NONE:
        return body
    if kind == DUCK_DEFLATE:
        import zlib
        return zlib.decompress(body)
    if kind == DUCK_ZSTD:
        try:
            import zstandard
        except ImportError:
            raise StateVRAMError(
                "this state is Zstandard-compressed and the zstandard module "
                "isn't installed. Either `pip install zstandard`, or set "
                "DuckStation's Save State Compression to Deflate or None and "
                "take the state again.")
        return zstandard.ZstdDecompressor().decompress(
            body, max_output_size=max(plain, 1) + 1)
    raise StateVRAMError(f"unknown save state compression {kind}")

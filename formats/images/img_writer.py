"""Adding shards to TOMBA2.IMG, and moving the IDX to match.

A chunk is a count, that many 12-byte shard records padded out to 0x800,
then each shard's compressed bytes back to back (see
formats/images/img_codec.py). Adding one is therefore three edits: a longer
header, the new bytes on the end, and every IDX record from that chunk on
pointing somewhere new.

WHY THE PADDING

Every packed size on the retail disc is a multiple of 0x800 - 0xF000,
0x2800, 0x5000, 0x800 - which is a CD sector, not a coincidence: the
loader reads a shard as whole sectors. Nothing here needs that to be
true, since the decompressor stops at the byte count it is given either
way, but writing shards that are not sector-sized would be the first
thing on the disc that is not, and that is not a difference worth
introducing to save a few hundred bytes. So a new shard is rounded up
and zero-padded like the rest.
"""
import os
import struct

from formats.images import img_codec

IDX_STRIDE = 0x800
HEADER_ROOM = 0x800
RECORD = 12
SECTOR = 0x800

# How many shards a chunk's header has room for.
MAX_SHARDS = (HEADER_ROOM - 4) // RECORD


class IMGWriteError(ValueError):
    """Raised when a chunk or the IDX cannot be rewritten."""


def _round_up(value, to=SECTOR):
    return (value + to - 1) // to * to


def add_shards(chunk, new_shards):
    """`chunk` with `new_shards` appended.

    Each new shard is (x, y, w, h, raw pixels); the pixels are
    compressed here. Existing shards keep their original bytes - they
    are not recompressed, so nothing that already worked can be changed
    by a compressor that packs one byte differently."""
    shards, first = img_codec.read_chunk_header(chunk)
    if len(shards) + len(new_shards) > MAX_SHARDS:
        raise IMGWriteError(
            f"{len(shards)} + {len(new_shards)} shards will not fit in the "
            f"0x800-byte header, which holds {MAX_SHARDS}")

    bodies = []
    at = first
    for _x, _y, _w, _h, packed in shards:
        bodies.append(chunk[at:at + packed])
        at += packed

    records = list(shards)
    for x, y, w, h, pixels in new_shards:
        if len(pixels) != w * h * 2:
            raise IMGWriteError(
                f"a {w}x{h} shard is {w * h * 2} bytes and this one is "
                f"{len(pixels)}")
        packed = img_codec.compress(pixels, w)
        # Belt and braces: a stream that copies from before its own
        # start decodes to whatever VRAM already held, which looks
        # perfect in the tool and wrong in game. compress() will not
        # emit one; this makes sure nothing ever ships if it does.
        if img_codec.reads_before_start(packed, w):
            raise IMGWriteError(
                f"the {w}x{h} shard compressed to a stream that reads "
                f"before its own start - refusing to write it")
        room = _round_up(len(packed))
        bodies.append(packed + bytes(room - len(packed)))
        records.append((x, y, w, h, room))

    out = bytearray(struct.pack("<I", len(records)))
    for x, y, w, h, packed in records:
        out += struct.pack("<HHHHI", x, y, w, h, packed)
    out += bytes(HEADER_ROOM - len(out))
    for body in bodies:
        out += body
    return bytes(out)


def check(idx_bytes, img_bytes):
    """What is wrong with shipping this IDX beside this IMG, if anything.

    Worth doing before an export because the failure is silent and total:
    an IDX whose offsets do not match the IMG next to it points every
    chunk at the wrong bytes, and the whole disc's artwork comes out as
    noise. That is what happens if the IDX is rebuilt with a migration's
    new offsets and the original IMG is shipped alongside - 36 of 38
    chunks unreadable, and nothing says so until the game is run."""
    problems = []
    count = len(idx_bytes) // IDX_STRIDE
    highest = 0
    for chunk in range(count):
        start, end = struct.unpack_from("<2I", idx_bytes, chunk * IDX_STRIDE)
        if end <= start:
            continue
        highest = max(highest, end)
        if end > len(img_bytes):
            problems.append(
                f"AREA_{chunk:02X} points at 0x{start:X}-0x{end:X}, past the "
                f"end of a {len(img_bytes)}-byte TOMBA2.IMG")
    if highest and highest != len(img_bytes):
        problems.append(
            f"the IDX's last chunk ends at {highest} but TOMBA2.IMG is "
            f"{len(img_bytes)} bytes - they are not the same pair of files")
    return problems


def rebuild(idx_path, img_path, replacements, out_idx, out_img):
    """Write a new IMG and IDX with some chunks replaced.

    `replacements` is {chunk index: new chunk bytes}. Chunks stay in
    their existing order and are laid back to back, so a chunk that grew
    pushes every later one along and the IDX is rewritten from the new
    positions rather than patched at the one that moved."""
    count = os.path.getsize(idx_path) // IDX_STRIDE
    with open(idx_path, "rb") as f:
        idx = bytearray(f.read())
    with open(img_path, "rb") as f:
        img = f.read()

    # The chunks in the order they sit in the file, so relaying them
    # cannot reorder anything.
    order = []
    for chunk in range(count):
        start, end = struct.unpack_from("<2I", idx, chunk * IDX_STRIDE)
        if end > start:
            order.append((start, end, chunk))
    order.sort()

    out = bytearray()
    moved = {}
    for start, end, chunk in order:
        data = replacements.get(chunk, img[start:end])
        moved[chunk] = (len(out), len(out) + len(data))
        out += data

    for chunk in range(count):
        at = chunk * IDX_STRIDE
        if chunk in moved:
            struct.pack_into("<2I", idx, at, *moved[chunk])
        else:
            # An area with no chunk keeps whatever it had, which is a
            # start and end that are equal.
            pass

    with open(out_img, "wb") as f:
        f.write(out)
    with open(out_idx, "wb") as f:
        f.write(idx)
    return {"chunks": len(order), "img_bytes": len(out),
            "grew_by": len(out) - len(img)}

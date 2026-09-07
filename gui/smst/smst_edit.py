"""Copying one SMST part over another, bytes and all.

A part is a self-contained run of GPU packets (see smst_parser's header):
its own vertices, its own vertex colours, its own UVs, its own texture
page and palette per face. Nothing outside the group refers into it and
it refers to nothing outside itself, so copying a part IS copying its
bytes - there is no fix-up to do beyond the pointer table that says where
each group starts.

That table is the whole of the work here. It is absolute offsets from the
start of the blob, and the groups sit back to back behind it in order, so
replacing a group with one of a different size moves every group after it
and every pointer from that one on. Rebuilding the table from the group
sizes is both simpler and safer than patching it.

WHAT DOES NOT TRAVEL

The texture pages and palettes the pasted packets name are addresses in
VRAM, not data inside the blob. Paste a part from a model whose art is
in another area's VRAM and it will draw with whatever that area happens
to have at those addresses - the same thing that makes Tuxedo Tomba draw
wrong outside the outro. paste_group() reports the pages and palettes
the incoming part needs so the caller can say so; it cannot fix it.
"""
import struct

from functions.format_detect import FormatError, smst_groups

GROUP_HEADER = 16
TRI_SIZE = 36
QUAD_SIZE = 44

# One copied part, app-wide, so it survives opening a different model -
# which is the only way "copy from this one, paste into that one" can
# work when both share a single viewer.
_CLIPBOARD = None


def clipboard():
    return _CLIPBOARD


def set_clipboard(clip):
    global _CLIPBOARD
    _CLIPBOARD = clip


def _body(data, offset, tris, quads):
    """One group's bytes: its header and its packets."""
    size = GROUP_HEADER + tris * TRI_SIZE + quads * QUAD_SIZE
    return bytes(data[offset:offset + size])


def copy_group(data, index):
    """One part, as something that can be pasted somewhere else.

    Carries the bytes rather than a parsed form: what gets written back
    is exactly what was read, so a round trip through copy and paste
    cannot lose anything the parser does not happen to model."""
    walked = smst_groups(data)
    if not 0 <= index < len(walked):
        raise FormatError(f"there is no group {index} in this SMST")
    _i, offset, tris, quads, size = walked[index]
    return {
        "index": index,
        "tris": tris,
        "quads": quads,
        "size": size,
        "bytes": _body(data, offset, tris, quads),
    }


def group_faces(clip):
    """(page, clut) for every packet in a copied part.

    What the caller needs to warn about: these are VRAM addresses, and
    they mean whatever the destination area's VRAM holds at them."""
    body = clip["bytes"]
    out = []
    at = GROUP_HEADER
    for count, stride in ((clip["tris"], TRI_SIZE),
                          (clip["quads"], QUAD_SIZE)):
        for _ in range(count):
            ind = at + 3
            page = body[ind + 11] & 0x1F
            word = struct.unpack_from("<h", body, ind + 7)[0]
            bits = bin(word & 0xFFFF)[2:].zfill(16)
            clut = (int(bits[10:], 2) << 4) * 2 + int(bits[1:10], 2) * 0x800
            out.append((page, clut))
            at += stride
    return out


def empty_body():
    """A group that draws nothing: the 16-byte header, zero packets.

    The disc has these already - a model with a placeholder part in the
    middle of its list - so an emptied group is a shape the format
    expects rather than one invented here. Keeping the group rather than
    removing it is the point: the parts after it keep their numbers, and
    an animation drives limb n through group n."""
    return bytes(GROUP_HEADER)


def clear_group(data, index):
    """`data` with group `index` emptied. Returns (new blob, note)."""
    bodies = bodies_of(data)
    if not 0 <= index < len(bodies):
        raise FormatError(f"there is no group {index} in this SMST")
    if not any(len(b) > GROUP_HEADER
               for i, b in enumerate(bodies) if i != index):
        raise FormatError(
            "that is the only part with any geometry in it - emptying it "
            "would leave a model nothing can read back as an SMST")
    was = len(bodies[index])
    bodies[index] = empty_body()
    note = (f"part {index} now draws nothing - {was} bytes of packets "
            f"removed. It keeps its place in the list, so the parts after "
            f"it keep their numbers.")
    return rebuild(data, bodies), note


def rebuild(data, bodies):
    """A whole SMST blob from a list of group bodies, in order.

    The table is recomputed rather than patched, so a group that changed
    size takes the ones behind it with it."""
    count = len(bodies)
    table_end = 4 + count * 4
    offsets = []
    at = table_end
    for body in bodies:
        offsets.append(at)
        at += len(body)
    out = bytearray(struct.pack("<HH", 0, count))
    for offset in offsets:
        out += struct.pack("<I", offset)
    for body in bodies:
        out += body
    return bytes(out)


def bodies_of(data):
    """Every group in a blob, as bytes, in order."""
    return [_body(data, offset, tris, quads)
            for _i, offset, tris, quads, _size in smst_groups(data)]


def paste_group(data, index, clip):
    """`data` with group `index` replaced by a copied part.

    Returns (new blob, note). The note names what the incoming part
    samples, since that is the thing a paste cannot carry with it."""
    bodies = bodies_of(data)
    if not 0 <= index < len(bodies):
        raise FormatError(f"there is no group {index} in this SMST")
    was = len(bodies[index])
    bodies[index] = clip["bytes"]
    blob = rebuild(data, bodies)
    faces = group_faces(clip)
    pages = sorted({p for p, _c in faces})
    cluts = sorted({c for _p, c in faces})
    note = (f"{clip['tris']} tris and {clip['quads']} quads, "
            f"{len(clip['bytes'])} bytes where the old part had {was}. "
            f"Draws from page(s) {', '.join(str(p) for p in pages) or '-'} "
            f"and palette(s) "
            f"{', '.join(f'0x{c:X}' for c in cluts) or '-'}, which have to "
            f"be in this area's VRAM for it to look right.")
    return blob, note

"""SMST (model set) parser.

An SMST is what an MDAT is with the drawmap taken off. Where a level
keeps its polygon groups behind a DRWA grid that says which patch of
floor each one covers, an SMST just lists them - and the list is a
model's parts. Tomba's own SMST is 21 of them: two heads (mouth open,
mouth closed), a torso, an upper and lower arm and a hand for each
side, the same again for the legs, and the hair. Everything on the disc
that moves is one of these, which is why there are 316 of them.

Layout of one SMST blob:
    header (4 bytes):
        zero   : u16 - always 0. This is what tells an SMST from an
                       MDAT at a glance: a drawmap opens with its row
                       count, which is never 0.
        count  : u16 - how many groups follow

    pointer table [4 .. 4 + count * 4): u32 per group, bytes from the
        start of the blob. The first is the table's own end, and they
        climb, so the groups sit back to back behind the table in the
        order they are listed.

    each group:
        tris   : u16
        quads  : u16
        twelve bytes, zero on every group on the disc. An MDAT group
        has nothing here and starts its first packet straight after
        the counts; this is the only structural difference between the
        two.
        then tris 36-byte and quads 44-byte packets, triangles first.

    A packet is a PSX GPU primitive as the game hands it to the
    hardware - a tag word, then r, g, b and the draw code, then the
    vertices and UVs. Which is why the field offsets below are the odd
    numbers they are, and why they are identical to formats/geometry/mdat.py's:
    the same packets, reached a different way.

WHERE THE PARTS ARE

Every group is modelled around its own origin - a hand's vertices run
about +/-20 either side of nothing, not out at the end of an arm - so
loading a model and drawing it draws every part on top of every other
one. What puts them where they belong is animation data (the ALFD /
TANP tables), which is a separate file and not decoded. The viewer
offers to spread the parts out instead, which is the honest way to look
at a model whose pose isn't in the file.

WHERE THE TEXTURES ARE

The trail models - Tomba's suits, the townspeople - sample texture
pages that are not in their own area's VRAM at all. They are in
AREA_01's, which is loaded once and stays resident, and which never
overlaps a level's own VRAM by a single byte on the retail disc. So a
trail SMST needs its area's VRAM with AREA_01's merged into it; see
MainWindow._load_area_vram_bytes(merge_common=True).
"""
import struct
from dataclasses import dataclass

from psx import draw_order
from psx import vram as psx_vram
from formats.archive.format_detect import FormatError, smst_groups

TRI_SIZE = 36
QUAD_SIZE = 44
GROUP_HEADER = 16

# Draw codes and what they mean, from the PSX draw-mode manual by way of
# formats/geometry/mdat.py - 1 marks a semi-transparent primitive.
TRIANGLES = {32: 0, 34: 0, 37: 0, 38: 0, 39: 0, 48: 0, 50: 1, 52: 0, 54: 1}
QUADS = {40: 0, 42: 0, 44: 0, 45: 0, 46: 0, 47: 0, 56: 0, 58: 1, 60: 0, 62: 1}

# Byte offsets inside a packet, measured from the draw code (packet + 3),
# exactly as formats/geometry/mdat.py measures them. Each vertex is (x, y, z)
# and each UV (u, v), in the order the renderer wants them.
TRI_VERTS = ((17, 15, 13), (19, 23, 21), (29, 27, 25))
TRI_UVS = ((5, 6), (9, 10), (31, 32))
# Which byte each vertex takes its colour from, and which nibble of it:
# (r, g, b, low_nibble). A negative offset reads the packet's own colour
# word, which sits just before the draw code.
TRI_COLORS = ((-3, -2, -1, 0), (1, 2, 3, 0), (1, 2, 3, 1))

QUAD_VERTS = ((33, 31, 29), (21, 19, 17), (23, 27, 25), (35, 39, 37))
QUAD_UVS = ((13, 14), (5, 6), (9, 10), (15, 16))
QUAD_COLORS = ((1, 2, 3, 0), (-3, -2, -1, 0), (-3, -2, -1, 1), (1, 2, 3, 1))

# UVs are resolved against the VRAM atlas by psx.vram.atlas_uv,
# which both this and formats/geometry/mdat.py call - see its docstring for why
# it aims at the middle of a texel.


@dataclass
class SMSTGroup:
    """One part of the model, and where its faces landed in the shared
    vertex and face arrays."""

    index: int
    offset: int             # bytes from the start of the blob
    tris: int
    quads: int
    size: int
    first_vertex: int = 0
    vertex_count: int = 0
    first_face: int = 0
    face_count: int = 0
    first_polygon: int = 0
    polygon_count: int = 0
    bounds: tuple = ()      # (x0, x1, y0, y1, z0, z1), () when empty

    @property
    def empty(self):
        return not self.face_count

    @property
    def centre(self):
        if not self.bounds:
            return (0.0, 0.0, 0.0)
        x0, x1, y0, y1, z0, z1 = self.bounds
        return ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)

    @property
    def radius(self):
        if not self.bounds:
            return 0.0
        x0, x1, y0, y1, z0, z1 = self.bounds
        return max(x1 - x0, y1 - y0, z1 - z0) / 2


def _clut_address(word):
    """Byte address in VRAM of a packet's palette. The CLUT attribute
    packs x / 16 into the low 6 bits and y into the next 9 - the same
    thing psx/vram.py spells out, kept here in the form
    formats/geometry/mdat.py uses so both read one CLUT the same way."""
    bits = bin(word)[2:].zfill(16)
    x = int(bits[10:], 2) << 4
    y = int(bits[1:10], 2)
    return x * 2 + y * 0x800


def _color(data, ind, r, g, b, low):
    """One vertex's colour, as three floats. The nibbles run 0-15 and
    9 is neutral, which is the scaling formats/geometry/mdat.py settled on."""
    out = []
    for offset in (r, g, b):
        value = data[ind + offset]
        out.append(f"{(value & 0x0F if low else value >> 4) / 9:.6f}")
    return out


def _read_packets(data, at, count, stride, layout, codes, model, group):
    """Decode `count` packets of one kind into `model`, appending to the
    same arrays formats/geometry/mdat.py fills so both feed the same viewer.

    Each packet also gets a record in model['polygons'], shaped exactly
    like formats/geometry/mdat.py's so gui/widgets/polygon_pick.py can pick and describe
    either format without knowing which it has. 'address' is absolute in
    the DAT, which is what makes a picked face addressable in a hex
    editor."""
    from game.texture_window import MODEL, QUAD
    verts, uvs, colors = layout
    kind = "tri" if len(verts) == 3 else "quad"
    for slot in range(count):
        ind = at + 3
        code = data[ind]
        transparent = bool(codes.get(code, 0))
        # The page byte carries the blend mode above the page number.
        # Masking it off entirely - which is what this did - left every
        # semi-transparent surface on the disc drawn additively, when
        # most of them ask for a half-and-half mix.
        blend = (data[ind + 11] >> 5) & 3
        page = data[ind + 11] & 0x1F
        clut = _clut_address(struct.unpack_from("<h", data, ind + 7)[0])

        base = len(model["vertices"])
        packet_uvs = []
        for (ox, oy, oz), (ou, ov), (cr, cg, cb, low) in zip(verts, uvs, colors):
            x = struct.unpack_from("<h", data, ind + ox)[0]
            y = struct.unpack_from("<h", data, ind + oy)[0]
            z = struct.unpack_from("<h", data, ind + oz)[0]
            model["vertices"].append([x, -y, z])          # Y up, as MDAT does
            model["vertex_colors"].append(_color(data, ind, cr, cg, cb, low))
            model["texture_coords"].append(
                psx_vram.atlas_uv(data[ind + ou], data[ind + ov], page))
            packet_uvs.append((data[ind + ou], data[ind + ov]))

        info = (page, clut, transparent, blend)
        # The top byte of the second colour word: flags an area's cell
        # drawer reads, as in formats/geometry/mdat.py - game/texture_window.py.
        flags = data[ind + 4] | MODEL
        first_face = len(model["faces"])
        if len(verts) == 3:
            model["faces"].append([base + 2, base + 1, base])
            model["texture_info"].append(info)
            model.setdefault("face_flags", []).append(flags)
            model["tri_count"] += 1
        else:
            model["faces"].append([base + 2, base + 1, base])
            model["faces"].append([base + 3, base + 2, base])
            model["texture_info"].extend((info, info))
            model.setdefault("face_flags", []).extend([flags | QUAD] * 2)
            model["quad_count"] += 1
        model["polygons"].append({
            "index": len(model["polygons"]),
            "group": group.index,
            "kind": kind,
            "slot": slot,
            # Absolute in the DAT, so it can be typed into a hex editor.
            "address": model["address"] + at,
            "type": code,
            "first_vertex": base,
            "vertex_count": len(verts),
            "first_face": first_face,
            "face_count": len(model["faces"]) - first_face,
            "page": page,
            "clut": clut,
            "transparent": transparent,
            "blend": blend,
            "texels": packet_uvs,
            "flags": flags & 0xFF,
        })
        at += stride


def parse_smst(data, address=0):
    """Every part of one SMST blob, decoded into the same model dict
    formats/geometry/mdat.py returns, with a `groups` list saying which slice of
    it each part owns. Raises FormatError if the blob isn't an SMST."""
    walked = smst_groups(data)

    model = {
        "vertices": [],
        "vertex_colors": [],
        "faces": [],
        "texture_coords": [],
        "texture_info": [],
        "polygons": [],
        "tri_count": 0,
        "quad_count": 0,
        "groups": [],
        "address": address,
        "size": len(data),
    }

    for index, offset, tris, quads, size in walked:
        group = SMSTGroup(index=index, offset=offset, tris=tris, quads=quads,
                          size=size,
                          first_vertex=len(model["vertices"]),
                          first_face=len(model["faces"]),
                          first_polygon=len(model["polygons"]))
        at = offset + GROUP_HEADER
        _read_packets(data, at, tris, TRI_SIZE,
                      (TRI_VERTS, TRI_UVS, TRI_COLORS), TRIANGLES, model, group)
        _read_packets(data, at + tris * TRI_SIZE, quads, QUAD_SIZE,
                      (QUAD_VERTS, QUAD_UVS, QUAD_COLORS), QUADS, model, group)

        group.polygon_count = len(model["polygons"]) - group.first_polygon
        group.vertex_count = len(model["vertices"]) - group.first_vertex
        group.face_count = len(model["faces"]) - group.first_face
        own = model["vertices"][group.first_vertex:]
        if own:
            group.bounds = (min(v[0] for v in own), max(v[0] for v in own),
                            min(v[1] for v in own), max(v[1] for v in own),
                            min(v[2] for v in own), max(v[2] for v in own))
        model["groups"].append(group)

    model["face_levels"] = draw_order.face_levels(model, "group")
    return model


# Where an edit that has not been written to the disc yet can be found.
# MainWindow sets this to look in its own pending_file_edits, so there is
# one source of truth rather than a second copy that can drift: revert
# the edit there and this stops answering for it.
#
# It lives at the bottom of load_smst rather than in any one view because
# every view reaches a model through here - the SMST tab, the ANMP tab's
# embedded viewer, the skeleton search, the export. A part pasted in one
# of them is then the same part in all of them, with nothing to keep in
# step by hand.
_pending_source = None


def set_pending_source(lookup):
    """`lookup(address) -> bytes or None` for unsaved edits."""
    global _pending_source
    _pending_source = lookup


def pending_blob(address):
    return _pending_source(address) if _pending_source else None


def read_smst_bytes(dat_file_path, address, size):
    """The bytes an SMST at `address` would parse from right now - a
    staged edit if there is one, otherwise what the disc holds.

    The one place this is decided, so a viewer's own copy of the blob
    (kept for copy/paste - see formats/models/smst_edit.py) can never drift
    from what load_smst() below builds the model out of. Read straight
    from disk instead of through this and a second paste into the same
    model discards whatever the first one did, because the part being
    pasted onto is the stale, unedited bytes."""
    data = pending_blob(address)
    if data is not None:
        return bytes(data)
    if not size:
        raise FormatError(
            "no size for this entry, so there is no blob to read")
    with open(dat_file_path, "rb") as f:
        f.seek(address)
        return f.read(size)


def load_smst(dat_file_path, address, size):
    """Read and parse the SMST blob at `address` in the DAT.

    An edit staged but not yet saved wins over what the file holds, so
    what is on screen is what would be written."""
    return parse_smst(read_smst_bytes(dat_file_path, address, size),
                      address=address)


def model_bounds(model):
    """(x0, x1, y0, y1, z0, z1) over every part, or None."""
    verts = model.get("vertices") if model else None
    if not verts:
        return None
    return (min(v[0] for v in verts), max(v[0] for v in verts),
            min(v[1] for v in verts), max(v[1] for v in verts),
            min(v[2] for v in verts), max(v[2] for v in verts))

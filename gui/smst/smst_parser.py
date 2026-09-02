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
    numbers they are, and why they are identical to gui/mdat/mdat.py's:
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

from functions import psx_vram
from functions.format_detect import FormatError, smst_groups

TRI_SIZE = 36
QUAD_SIZE = 44
GROUP_HEADER = 16

# Draw codes and what they mean, from the PSX draw-mode manual by way of
# gui/mdat/mdat.py - 1 marks a semi-transparent primitive.
TRIANGLES = {32: 0, 34: 0, 37: 0, 38: 0, 39: 0, 48: 0, 50: 1, 52: 0, 54: 1}
QUADS = {40: 0, 42: 0, 44: 0, 45: 0, 46: 0, 47: 0, 56: 0, 58: 1, 60: 0, 62: 1}

# Byte offsets inside a packet, measured from the draw code (packet + 3),
# exactly as gui/mdat/mdat.py measures them. Each vertex is (x, y, z)
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

# UVs are resolved against the VRAM atlas by functions.psx_vram.atlas_uv,
# which both this and gui/mdat/mdat.py call - see its docstring for why
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
    thing functions/psx_vram.py spells out, kept here in the form
    gui/mdat/mdat.py uses so both read one CLUT the same way."""
    bits = bin(word)[2:].zfill(16)
    x = int(bits[10:], 2) << 4
    y = int(bits[1:10], 2)
    return x * 2 + y * 0x800


def _color(data, ind, r, g, b, low):
    """One vertex's colour, as three floats. The nibbles run 0-15 and
    9 is neutral, which is the scaling gui/mdat/mdat.py settled on."""
    out = []
    for offset in (r, g, b):
        value = data[ind + offset]
        out.append(f"{(value & 0x0F if low else value >> 4) / 9:.6f}")
    return out


def _read_packets(data, at, count, stride, layout, codes, model):
    """Decode `count` packets of one kind into `model`, appending to the
    same arrays gui/mdat/mdat.py fills so both feed the same viewer."""
    verts, uvs, colors = layout
    for _ in range(count):
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
        for (ox, oy, oz), (ou, ov), (cr, cg, cb, low) in zip(verts, uvs, colors):
            x = struct.unpack_from("<h", data, ind + ox)[0]
            y = struct.unpack_from("<h", data, ind + oy)[0]
            z = struct.unpack_from("<h", data, ind + oz)[0]
            model["vertices"].append([x, -y, z])          # Y up, as MDAT does
            model["vertex_colors"].append(_color(data, ind, cr, cg, cb, low))
            model["texture_coords"].append(
                psx_vram.atlas_uv(data[ind + ou], data[ind + ov], page))

        info = (page, clut, transparent, blend)
        if len(verts) == 3:
            model["faces"].append([base + 2, base + 1, base])
            model["texture_info"].append(info)
            model["tri_count"] += 1
        else:
            model["faces"].append([base + 2, base + 1, base])
            model["faces"].append([base + 3, base + 2, base])
            model["texture_info"].extend((info, info))
            model["quad_count"] += 1
        at += stride


def parse_smst(data, address=0):
    """Every part of one SMST blob, decoded into the same model dict
    gui/mdat/mdat.py returns, with a `groups` list saying which slice of
    it each part owns. Raises FormatError if the blob isn't an SMST."""
    walked = smst_groups(data)

    model = {
        "vertices": [],
        "vertex_colors": [],
        "faces": [],
        "texture_coords": [],
        "texture_info": [],
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
                          first_face=len(model["faces"]))
        at = offset + GROUP_HEADER
        _read_packets(data, at, tris, TRI_SIZE,
                      (TRI_VERTS, TRI_UVS, TRI_COLORS), TRIANGLES, model)
        _read_packets(data, at + tris * TRI_SIZE, quads, QUAD_SIZE,
                      (QUAD_VERTS, QUAD_UVS, QUAD_COLORS), QUADS, model)

        group.vertex_count = len(model["vertices"]) - group.first_vertex
        group.face_count = len(model["faces"]) - group.first_face
        own = model["vertices"][group.first_vertex:]
        if own:
            group.bounds = (min(v[0] for v in own), max(v[0] for v in own),
                            min(v[1] for v in own), max(v[1] for v in own),
                            min(v[2] for v in own), max(v[2] for v in own))
        model["groups"].append(group)

    return model


def load_smst(dat_file_path, address, size):
    """Read and parse the SMST blob at `address` in the DAT."""
    if not size:
        raise FormatError("no size for this entry, so there is no blob to read")
    with open(dat_file_path, "rb") as f:
        f.seek(address)
        data = f.read(size)
    return parse_smst(data, address=address)


def model_bounds(model):
    """(x0, x1, y0, y1, z0, z1) over every part, or None."""
    verts = model.get("vertices") if model else None
    if not verts:
        return None
    return (min(v[0] for v in verts), max(v[0] for v in verts),
            min(v[1] for v in verts), max(v[1] for v in verts),
            min(v[2] for v in verts), max(v[2] for v in verts))

"""Surfaces an area's environment drawer builds in code rather than keeping
in its MDAT - so nothing on the disc holds them as geometry.

A01, Large Mine Underground: the lava at the bottom. FUN_A01__801311fc,
called by f_RenderPipeAreaEnvironment only while the mine is cursed (and
src_CurrentArea._1_1_ < 0x0F), keeps 59 vertices - x and z at 0x801388F4,
a phase each at 0x80139014 - and every frame puts each at
x + rcos(phase) >> 4, rcos(phase >> 2) >> 6 - 2000, z + rsin(phase) >> 4
before nudging the phase at random. 44 quads at 0x801389E0, five bytes
each: four vertex indices, then which corners are lit (0x808080, bits
8/4/2/1 for corners 0-3) rather than black. Each is a GT4 through CLUT
0x3073 with uv (u, v) to (u + 63, v + 63), drawn inside the texture window
game/texture_window.py describes. The packet names no texture page;
page 12 is the one the mine's other windowed faces (flag 0x10) sample.
"""
import math
import struct
from dataclasses import dataclass
from types import SimpleNamespace

from game import game_build
from psx import vram as psx_vram
from game import texture_window

ONE = 4096


@dataclass(frozen=True)
class Surface:
    name: str
    drawer: str
    vertices: int           # x, z pairs
    count: int
    phases: int
    quads: int              # four indices and a lit mask each
    quad_count: int
    base_y: int
    clut: int               # as a packet holds it
    page: int


SURFACES = {
    "A01": (Surface("lava", "FUN_A01__801311fc", 0x801388F4, 0x3B, 0x80139014,
                    0x801389E0, 0x2C, -2000, 0x3073, 12),),
}


def rcos(angle):
    return int(round(ONE * math.cos(angle * 2 * math.pi / ONE)))


def rsin(angle):
    return int(round(ONE * math.sin(angle * 2 * math.pi / ONE)))


def clut_address(raw):
    """A packet's CLUT word as the VRAM address the viewers key palettes by."""
    return ((raw >> 6) & 0x1FF) * 0x800 + ((raw & 0x3F) << 4) * 2


def models(overlay_name, data, purified, view_point):
    """[(name, drawer, model dict with one group)] for an overlay's built
    surfaces, as they stand on the first frame, in the viewers' axes."""
    if purified or not data:
        return []
    out = []
    image = overlay_name.upper()[:3]
    for surface in SURFACES.get(image, ()):
        # US retail's addresses, found in the open build's overlay.
        at = {name: game_build.overlay_offset(image, getattr(surface, name))
              for name in ("vertices", "phases", "quads")}
        if None in at.values():
            continue
        pairs = struct.unpack_from(f"<{surface.count * 2}h", data, at["vertices"])
        phases = struct.unpack_from(f"<{surface.count}h", data, at["phases"])
        points = [(pairs[k * 2] + (rcos(phases[k]) >> 4),
                   (rcos(phases[k] >> 2) >> 6) + surface.base_y,
                   pairs[k * 2 + 1] + (rsin(phases[k]) >> 4))
                  for k in range(surface.count)]
        quads = data[at["quads"]:at["quads"] + surface.quad_count * 5]
        model = {"vertices": [], "vertex_colors": [], "texture_coords": [],
                 "faces": [], "texture_info": [], "face_flags": [],
                 "tri_count": 0, "quad_count": surface.quad_count}
        info = (surface.page, clut_address(surface.clut), False, 0)
        corners = ((0, 0), (63, 0), (0, 63), (63, 63))
        for q in range(surface.quad_count):
            *indices, lit = quads[q * 5:q * 5 + 5]
            base = len(model["vertices"])
            for corner, index in enumerate(indices):
                model["vertices"].append(list(view_point(points[index])))
                shade = 1.0 if lit & (8 >> corner) else 0.0
                model["vertex_colors"].append([shade, shade, shade])
                model["texture_coords"].append(psx_vram.atlas_uv(*corners[corner],
                                                                 surface.page))
            # A GT4 is triangles 0-1-2 and 1-3-2; both windings, since the
            # game draws it from above and below alike.
            for triangle in ((0, 1, 2), (1, 3, 2), (2, 1, 0), (2, 3, 1)):
                model["faces"].append([base + t for t in triangle])
                model["texture_info"].append(info)
                model["face_flags"].append(texture_window.GENERATED)
        model["groups"] = [SimpleNamespace(
            index=0, first_vertex=0, vertex_count=len(model["vertices"]),
            first_face=0, face_count=len(model["faces"]), tris=0,
            quads=surface.quad_count, size=0, offset=0, empty=False)]
        out.append((surface.name, surface.drawer, model))
    return out

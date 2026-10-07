"""Blender's ordinary Wavefront OBJ <-> Tomba 2 packed geometry.

The currently selected resource is the authority for opaque packet bytes and
materials. Unchanged faces recover their original records, independent of OBJ
vertex/face ordering. No Blender add-on, private mesh format or sidecar is needed.
Coordinates use the viewers' Y-up space, 100 game units per OBJ unit.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
import math
from pathlib import Path
import re
import struct

from formats.archive.format_detect import smst_groups
from formats.drawmaps.drwa_parser import parse_drwa
from formats.models.smst_edit import bodies_of, rebuild
from psx import gpu_packet as packet

SCALE = 100.0
MAX_FACES = 20000
# US retail alternates two 0x14000-byte primitive buffers at 0x800BFE68.
# GT3/GT4 output packets consume 40/52 bytes before other actors/UI/effects.
FRAME_PACKET_BYTES = 0x14000
SMST_PART_PACKET_BYTES = 0x1000
# A rough measure only. What a frame really costs depends on the camera; see
# formats/drawmaps/visibility.py for the game's own rule.
DENSITY_CELL_SPAN = 9
# Town's batch reads a cell's two counts as bytes. The disc's largest is 88.
CELL_PACKETS = 255
MATERIAL = re.compile(r"^T2_([0-9A-Fa-f]{2})_([0-9A-Fa-f]{2})_([0-9A-Fa-f]{4})_([0-9A-Fa-f]{4})(?:\.\d+)?$")


class ExchangeError(ValueError):
    pass


@dataclass(frozen=True)
class Face:
    vertices: tuple
    uvs: tuple
    colors: tuple
    material: str
    object: str = ""


@dataclass
class Record:
    face: Face
    raw: bytes
    owner: int


@dataclass
class ImportResult:
    data: bytes
    triangles: int
    quads: int
    unchanged: bool
    reused: int
    note: str
    warning: str = ""
    # MDAT cells (col, row) holding no original packet: the level's DRWB
    # knows nothing about them. See drwb_parser.reveal().
    new_cells: tuple = ()
    # game.frame_budget.Forecast, where the level's frames are on record.
    frames: object = None
    # MDAT: {cell: the Blender object of each packet, in the cell's order}.
    objects: dict = None


def _material(raw):
    return f"T2_{raw[3]:02X}_{raw[7]:02X}_{struct.unpack_from('<H', raw, 10)[0]:04X}_{struct.unpack_from('<H', raw, 14)[0]:04X}"


def _material_name(name):
    match = MATERIAL.fullmatch(name)
    return "T2_" + "_".join(s.upper() for s in match.groups()) if match else name


def _face(raw, kind, owner, prefix):
    # OBJ uses a counter-clockwise ring; the game's packet ring is clockwise.
    order = [0, 2, 1] if kind == "tri" else [0, 3, 2, 1]
    vertices = packet.read_vertices(raw, 0, kind)
    uvs = packet.read_uvs(raw, 0, kind)
    colors = packet.read_colors(raw, 0, kind)
    return Face(tuple(vertices[i] for i in order), tuple(uvs[i] for i in order),
                tuple(colors[i] for i in order), _material(raw), f"T2_{prefix}_{owner:04d}")


def records(blob, kind, part=None):
    if kind == "SMST":
        groups = smst_groups(blob)
        if part is None or not 0 <= part < len(groups):
            raise ExchangeError("Select exactly one SMST part to export or replace.")
        i, at, tris, quads, _ = groups[part]
        spans = [(i, at + 16, tris, quads)]
    elif kind == "MDAT":
        grid = parse_drwa(blob)
        if grid.strays or not grid.contiguous:
            raise ExchangeError("This MDAT has an unsupported overlapping or sparse packet layout.")
        spans = [(g.cell, g.offset + 4, g.tris, g.quads) for g in grid.groups]
    else:
        raise ExchangeError("Choose MDAT or SMST.")
    out = []
    for owner, at, tris, quads in spans:
        for n, shape in ((tris, "tri"), (quads, "quad")):
            for _ in range(n):
                raw = bytes(blob[at:at + packet.size(shape)])
                out.append(Record(_face(raw, shape, owner, "part" if kind == "SMST" else "cell"), raw, owner))
                at += packet.size(shape)
    return out


def _finite(values, what):
    if not all(math.isfinite(x) for x in values):
        raise ExchangeError(f"{what} contains a non-finite number.")
    return values


def _srgb(v):
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def _linear(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def shades(own, linear):
    """A file's 0..1 vertex colour -> the game's 0-15, where 9 is unshaded.

    Two scales reach Blender. write_obj() puts n/15 in an OBJ, which
    Blender reads as sRGB. A PS1 tool's GLB has 1.0 for unshaded, which
    glTF calls linear. Blender then writes an OBJ in sRGB and a GLB linear,
    whichever it was given (measured: 0.695 in, 0.851 out in an OBJ), so
    there are four cases. `own`: the model has this program's materials.
    `linear`: the file is a glTF.
    """
    if own:
        scale, curve = 15, (_srgb if linear else float)
    else:
        scale, curve = packet.COLOR_NEUTRAL, (float if linear else _linear)
    return lambda rgb: tuple(max(0, min(15, round(curve(min(1., max(0., v))) * scale))) for v in rgb)


def read_obj(path, scale=SCALE, place=None, *, raw_positions=False, colors=None):
    """Read polygons, per-corner UVs and Blender's optional vertex colours.

    Negative indices are relative to the arrays at that line, as OBJ requires.
    Normals and smoothing groups do not affect PSX geometry. Reject n-gons
    explicitly: silently triangulating them changes the game's packet budget.

    `scale` is game units per OBJ unit. `place` maps a scaled (x, y, z) to
    where it stands in the level - for a model that was not built there.
    `colors` turns a file colour into what a face keeps; see shades().
    """
    colors = colors or shades(True, False)
    vertices, uvs, tints, faces = [], [], [], []
    material, obj = "", ""
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ExchangeError("OBJ exceeds the 64 MiB import limit.")
    def index(value, length):
        n = int(value)
        result = n - 1 if n > 0 else length + n
        if n == 0 or not 0 <= result < length:
            raise ExchangeError("OBJ refers to a vertex or UV outside its array.")
        return result
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        fields = line.partition("#")[0].split()
        if not fields:
            continue
        op, values = fields[0], fields[1:]
        try:
            if op == "v":
                if len(values) not in (3, 4, 6, 7):
                    raise ExchangeError("Expected XYZ, optional W, or XYZ RGB[A].")
                xyz = _finite(tuple(float(x) * scale for x in values[:3]), "Position")
                if place:
                    xyz = _finite(tuple(place(xyz)), "Placed position")
                if not raw_positions:
                    xyz = tuple(round(x) for x in xyz)
                if not raw_positions and not (-32768 <= xyz[0] <= 32767 and -32767 <= xyz[1] <= 32768 and -32768 <= xyz[2] <= 32767):
                    raise ExchangeError(
                        f"Position {values[0]}, {values[1]}, {values[2]} lands at {xyz} in game units, "
                        "outside the signed 16-bit range. One OBJ unit is 100 game units here, so a model "
                        "in another game's units has to be scaled and placed first: in Blender against "
                        "the exported level, or with scripts/import_foreign_level.py, which also "
                        "installs its textures.")
                if len(values) == 4 and float(values[3]) != 1:
                    raise ExchangeError("Homogeneous OBJ positions with W other than 1 are unsupported.")
                vertices.append(xyz)
                rgb = _finite(tuple(float(x) for x in values[3:6]), "Colour") if len(values) >= 6 else None
                if rgb and any(x < -0.00001 or x > 1.00001 for x in rgb):
                    raise ExchangeError("OBJ vertex colours must be in 0..1.")
                tints.append(colors(rgb) if rgb else None)
            elif op == "vt":
                uv = _finite(tuple(float(x) for x in values[:2]), "UV")
                if len(uv) != 2:
                    raise ExchangeError("Expected a two-component texture coordinate.")
                if any(v < -0.000001 or v > 1.000001 for v in uv):
                    raise ExchangeError("UV is outside its 256 x 256 PSX page. Keep UVs at texel centres, inside 0..1.")
                texel = tuple(max(0, min(255, v)) for v in
                              (round(uv[0] * 256 - .5), round((1 - uv[1]) * 256 - .5)))
                uvs.append(texel)
            elif op in ("o", "g"):
                obj = " ".join(values)
            elif op == "usemtl":
                material = _material_name(" ".join(values))
            elif op == "f":
                if len(values) not in (3, 4):
                    raise ExchangeError("Only triangles and quads are supported. Triangulate n-gons in Blender first.")
                points, texels, rgb = [], [], []
                for token in values:
                    corner = token.split("/")
                    vi = index(corner[0], len(vertices))
                    points.append(vertices[vi])
                    rgb.append(tints[vi])
                    texels.append(uvs[index(corner[1], len(uvs))] if len(corner) > 1 and corner[1] else None)
                faces.append(Face(tuple(points), tuple(texels), tuple(rgb), material, obj))
                if len(faces) > MAX_FACES:
                    raise ExchangeError(f"More than {MAX_FACES} polygons; simplify the model first.")
        except (ValueError, IndexError) as exc:
            raise ExchangeError(f'OBJ line {line_no}, Blender object "{obj or "(unnamed)"}": {exc}') from exc
    return faces


def write_obj(path, faces, vram=None):
    """Write native OBJ/MTL, optionally with decoded 256x256 texture pages."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    materials = sorted({f.material for f in faces})
    text = ["# Tomba2Edit: Y up, -Z forward, scale 1; 100 game units per OBJ unit.",
            "# Blender OBJ export: UVs + Materials + Vertex Colors ON; Triangulated Mesh OFF.",
            f"mtllib {path.stem}.mtl"]
    count = 1
    current = None
    for face in faces:
        if face.object != current:
            text.append(f"o {face.object or 'Geometry'}")
            current = face.object
        for xyz, rgb in zip(face.vertices, face.colors):
            values = [v / SCALE for v in xyz]
            if rgb is not None:
                values += [v / 15 for v in rgb]
            text.append("v " + " ".join(f"{v:.9f}" for v in values))
        for uv in face.uvs:
            u, v = uv or (0, 0)
            text.append(f"vt {(u + .5) / 256:.9f} {1 - (v + .5) / 256:.9f}")
        text.append(f"usemtl {face.material}")
        text.append("f " + " ".join(f"{i}/{i}" for i in range(count, count + len(face.vertices))))
        count += len(face.vertices)
    mtl = []
    for name in materials:
        mtl.extend([f"newmtl {name}", "Ka 0 0 0", "Kd 1 1 1", "Ks 0 0 0", "illum 1", "d 1"])
        if vram and MATERIAL.fullmatch(name):
            from PIL import Image
            from formats.models.gltf_export import index_atlas, palette
            match = MATERIAL.fullmatch(name)
            code, _, clut, page = (int(v, 16) for v in match.groups())
            address = ((clut & 63) * 32) + (((clut >> 6) & 511) * 2048)
            p = page & 31
            image = palette(vram, address, bool(code & 2))[index_atlas(vram)[(p // 16) * 256:(p // 16 + 1) * 256, (p % 16) * 256:(p % 16 + 1) * 256]]
            texture = f"{path.stem}_{name}.png"
            Image.fromarray(image).save(path.parent / texture)
            mtl.append(f"map_Kd {texture}")
    path.with_suffix('.mtl').write_text('\n'.join(mtl) + '\n', encoding='utf-8')
    path.write_text('\n'.join(text) + '\n', encoding='utf-8')
    return path


def export_obj(path, blob, kind, part=None, vram=None):
    return write_obj(path, [r.face for r in records(blob, kind, part)], vram)


def _key(face, colors=True):
    corners = tuple((xyz, uv, rgb if colors else None) for xyz, uv, rgb in zip(face.vertices, face.uvs, face.colors))
    # Preserve winding; opposite-facing surfaces are not interchangeable.
    return face.material, min(corners[i:] + corners[:i] for i in range(len(corners)))


def _encode(face, templates):
    kind = 'tri' if len(face.vertices) == 3 else 'quad'
    if any(v is None for v in face.uvs):
        raise ExchangeError("Every new/changed face needs UVs. Unwrap it onto an exported T2 material.")
    if len(set(face.vertices)) < 3:
        raise ExchangeError("A new face collapses to fewer than three positions after rounding to game units.")
    raw = templates.get((face.material, kind))
    if raw is None:
        match = MATERIAL.fullmatch(face.material)
        if not match:
            raise ExchangeError(f"Material {face.material or '(none)'} is not a T2 material. Assign an exported T2 material to new geometry. Importing new texture images is a separate texture operation.")
        code, flags, clut, page = (int(v, 16) for v in match.groups())
        # A material can be used on both triangles and quads. Retain its other
        # command/texture-window/depth-order bits, changing only polygon shape.
        code = (code & ~8) | (8 if kind == 'quad' else 0)
        raw = bytearray(packet.size(kind))
        raw[3], raw[7] = code, flags
        struct.pack_into('<H', raw, 10, clut)
        struct.pack_into('<H', raw, 14, page)
    else:
        raw = bytearray(raw)
    order = [0, 2, 1] if kind == 'tri' else [0, 3, 2, 1]
    packet.write_vertices(raw, 0, kind, [face.vertices[i] for i in order])
    packet.write_uvs(raw, 0, kind, [face.uvs[i] for i in order])
    packet.write_colors(raw, 0, kind, [face.colors[i] or (9, 9, 9) for i in order])
    return bytes(raw)


def _body(packets, header=b'\0' * 4):
    triangles = [r for r in packets if len(r) == 36]
    quads = [r for r in packets if len(r) == 44]
    if max(len(triangles), len(quads)) > 32767:
        raise ExchangeError("Too many packets in a group for the game's signed counts.")
    return struct.pack('<HH', len(triangles), len(quads)) + header[4:] + b''.join(triangles + quads)


def _cell_size(grid):
    fit = grid.cell_size()
    if fit is None or fit[2] < .90:
        raise ExchangeError("Cannot establish this MDAT's spatial grid safely.")
    # Retail uses 640 world units, except the 1024-unit mine grid. Verify the
    # measured slope rather than guessing a value for unknown/custom formats.
    size = min((640, 1024), key=lambda s: abs(fit[0] - s) + abs(fit[1] - s))
    if any(abs(v - size) > size * .15 for v in fit[:2]):
        raise ExchangeError("Unrecognised MDAT cell spacing; keep the original grid.")
    return size


def _trailer(data):
    # Unreferenced zero fill is reusable allocation, not extra geometry. Keep
    # opaque nonzero bytes (and their word alignment) when a slot is resized.
    end = len(data.rstrip(b'\0'))
    return data[:(end + 3) // 4 * 4]


def _packet_pressure(blob, kind, part):
    """Potential output bytes per part or dense 9x9-cell patch.

    This catches concentrated replacement scenes, including the full-resolution
    Village port that froze in widescreen. It is a density screen, not an emulation of every
    overlay's camera culler: runtime testing still matters.
    """
    if kind == 'SMST':
        _, _, t, q, _ = smst_groups(blob)[part]
        return t * 40 + q * 52
    grid = parse_drwa(blob)
    costs = {g.cell: g.tris * 40 + g.quads * 52 for g in grid.groups}
    prefix = [[0] * (grid.width + 1) for _ in range(grid.height + 1)]
    peak = 0
    for y in range(1, grid.height + 1):
        for x in range(1, grid.width + 1):
            prefix[y][x] = (costs.get((y-1) * grid.width + x-1, 0)
                            + prefix[y-1][x] + prefix[y][x-1] - prefix[y-1][x-1])
            top, left = max(0, y-DENSITY_CELL_SPAN), max(0, x-DENSITY_CELL_SPAN)
            peak = max(peak, prefix[y][x] - prefix[top][x] - prefix[y][left] + prefix[top][left])
    return peak


def import_obj(path, blob, kind, part=None, *, max_growth=0, material_library=(), cell_size=None,
               growth_alignment=1):
    return import_faces(read_obj(path), blob, kind, part, max_growth=max_growth,
                        material_library=material_library, cell_size=cell_size,
                        growth_alignment=growth_alignment)


def import_faces(faces, blob, kind, part=None, *, max_growth=0, material_library=(), cell_size=None,
                 growth_alignment=1):
    """Replace all MDAT geometry, or exactly one SMST body.

    Default memory budget is the selected resource's current byte size. An
    explicit larger budget is for callers that have verified the area's RAM
    allocation. Parsing success alone is not evidence that growth is safe.
    """
    originals = records(blob, kind, part)
    if growth_alignment not in (1, 2048):
        raise ExchangeError('Unsupported resource growth alignment.')
    faces = list(faces)
    for face in faces:
        for x, y, z in face.vertices:
            if (not all(math.isfinite(v) and v == round(v) for v in (x, y, z))
                    or not (-32768 <= x <= 32767 and -32767 <= y <= 32768 and -32768 <= z <= 32767)):
                raise ExchangeError(f'Blender object "{face.object or "(unnamed)"}" is outside the game coordinate range. Reduce its scale or move it in the import preview.')
    if kind == 'MDAT' and not faces:
        raise ExchangeError("An MDAT replacement must contain some geometry.")
    exact, without_colors, templates = defaultdict(deque), defaultdict(deque), {}
    for i, record in enumerate(originals):
        exact[_key(record.face)].append(i)
        without_colors[_key(record.face, False)].append(i)
        shape = 'tri' if len(record.raw) == 36 else 'quad'
        templates.setdefault((record.face.material, shape), record.raw)
    # The import dialog and offline converters can supply packets whose
    # texture placement has been prepared for the destination IMG.
    for record in material_library:
        shape = 'tri' if len(record.raw) == 36 else 'quad'
        templates.setdefault((record.face.material, shape), record.raw)
    used, converted = set(), []
    for face in faces:
        if any(uv is None for uv in face.uvs):
            raise ExchangeError('Every face needs UVs. Unwrap it onto a target T2 material before export.')
        if any(c is None for c in face.colors) and not all(c is None for c in face.colors):
            raise ExchangeError('A face mixes vertices with and without colours. Export a complete colour attribute or disable Vertex Colors.')
        no_colors = all(c is None for c in face.colors)
        choices = (without_colors if no_colors else exact).get(_key(face, not no_colors), deque())
        while choices and choices[0] in used:
            choices.popleft()
        if choices:
            n = choices.popleft()
            used.add(n)
            converted.append((originals[n].raw, originals[n].owner, face))
        else:
            # Only existing source materials are allowed: otherwise arbitrary
            # names could silently reference unloaded texture pages or flags.
            if not any(name == face.material for name, _ in templates):
                raise ExchangeError(
                    f'Unknown material {face.material or "(none)"} in Blender object "{face.object or "(unnamed)"}" '
                    'which is not installed in this target. For new artwork, use ordinary Blender '
                    'material names and export their MTL and texture images together.')
            converted.append((_encode(face, templates), None, face))
    tris = sum(len(f.vertices) == 3 for f in faces)
    quads = len(faces) - tris
    if len(used) == len(originals) == len(faces):
        return ImportResult(bytes(blob), tris, quads, True, len(used), 'Byte-identical: every original packet and all padding preserved.')
    objects = None
    if kind == 'SMST':
        bodies = bodies_of(blob)
        old = bodies[part]
        _, _, t, q, extent = smst_groups(blob)[part]
        body = _body([r for r, _, _ in converted], old[:16]) + _trailer(old[extent:])
        # Keep the selected slot's allocated size when the geometry shrinks.
        # The opaque trailer is preserved, followed by zero padding.
        if len(body) < len(old):
            body += bytes(len(old) - len(body))
        elif len(body) > len(old):
            # The SDAT repacker aligns every growth delta to a sector. Put its
            # fill INSIDE the edited part, so the last unedited body's trailer
            # is byte-identical even when read from the final rebuilt disc.
            body += bytes((len(old) - len(body)) % growth_alignment)
        bodies[part] = body
        result = rebuild(blob, bodies)
        smst_groups(result)
        if any(a != b for i, (a, b) in enumerate(zip(bodies_of(blob), bodies_of(result))) if i != part):
            raise ExchangeError('Internal error: a different SMST part changed.')
    else:
        grid = parse_drwa(blob)
        if cell_size is None:
            cell_size = _cell_size(grid)
        elif cell_size not in (640, 1024):
            raise ExchangeError('Unsupported game drawmap cell size.')
        cells, named = defaultdict(list), defaultdict(list)
        kept = set()
        for raw, owner, face in converted:
            if owner is not None:
                kept.add(owner)
            if owner is None:
                x = sum(v[0] for v in face.vertices) / len(face.vertices)
                z = sum(v[2] for v in face.vertices) / len(face.vertices)
                col, row = math.floor(x / cell_size), math.floor(z / cell_size)
                if not 0 <= col < grid.width or not 0 <= row < grid.height:
                    raise ExchangeError(f'Blender object "{face.object or "(unnamed)"}" extends outside the target level at ({x:.0f}, {z:.0f}). Use Fit selected objects to target, or reduce its scale in the preview.')
                # Very large polygons cannot be culled reliably from one cell.
                if any(max(v[a] for v in face.vertices) - min(v[a] for v in face.vertices) > 2 * cell_size for a in (0, 2)):
                    raise ExchangeError('A new face spans more than two drawmap cells. Subdivide it before import.')
                owner = row * grid.width + col
            cells[owner].append(raw)
            named[owner].append(face.object)
        # A group keeps its triangles before its quads (_body).
        objects = {cell: tuple(n for size in (36, 44) for r, n in zip(packets, named[cell]) if len(r) == size)
                   for cell, packets in cells.items()}
        result = bytearray(blob[:grid.data_start])
        struct.pack_into(f'<{grid.cell_count}H', result, 4, *([0xffff] * grid.cell_count))
        for cell, packets in sorted(cells.items()):
            three = sum(len(r) == 36 for r in packets)
            if max(three, len(packets) - three) > CELL_PACKETS:
                raise ExchangeError(
                    f'Drawmap cell ({cell % grid.width}, {cell // grid.width}) holds {three} triangles and '
                    f'{len(packets) - three} quads; the game draws at most {CELL_PACKETS} of each from one cell. '
                    'Thin that spot. The original resource has not been changed.')
            pointer = len(result) // 4
            if pointer >= 0xffff:
                raise ExchangeError('MDAT exceeds its 16-bit drawmap pointer range.')
            struct.pack_into('<H', result, 4 + cell * 2, pointer)
            result += _body(packets)
        result += _trailer(blob[grid.extent:])
        if len(result) < len(blob):
            result += bytes(len(blob) - len(result))
        result = bytes(result)
        parse_drwa(result)
    pressure = _packet_pressure(result, kind, part)
    before = _packet_pressure(blob, kind, part)
    warning, new_cells = '', ()
    if kind == 'SMST':
        limit = max(SMST_PART_PACKET_BYTES, before)
        if pressure > limit:
            raise ExchangeError(
                f'Too much geometry is concentrated in one part: {pressure:,} potential render-packet bytes '
                f'exceeds the {limit:,}-byte density limit. Actors, effects and UI share the drawing buffer; '
                'widescreen can expose more faces. Simplify the mesh in Blender. '
                'The original resource has not been changed.')
    else:
        new_cells = tuple((c % grid.width, c // grid.width) for c in sorted(cells) if c not in kept)
        # Not refused: the mesh is the author's. The 81,920-byte frame buffer
        # is shared, and the stock Town opening already reaches 80,432 in
        # widescreen, so denser scenery needs game/primitive_buffer.py.
        if pressure > before:
            warning = (f'Denser than the original: up to {pressure:,} packet bytes in a 9 x 9 cell patch '
                       f'against {before:,}. The frame buffer is shared with actors and UI and the stock '
                       'game nearly fills it in busy scenes; build with the single-buffer MAIN.EXE patch '
                       '(game/primitive_buffer.py) or check the scene in an emulator.')
    growth = len(result) - len(blob)
    if growth > max_growth:
        raise ExchangeError(f'Replacement needs {growth} extra bytes; the verified budget allows {max_growth}. Simplify the mesh. The original resource has not been changed.')
    note = f'{tris} triangles, {quads} quads; {len(used)} original packets reused; {growth:+d} bytes. Collision unchanged.'
    return ImportResult(result, tris, quads, False, len(used), note + (' ' + warning if warning else ''),
                        warning, new_cells, objects=objects)

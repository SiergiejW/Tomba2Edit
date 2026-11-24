"""Play as another character: put its body on Tomba's costume slots.

How the game builds Tomba (US retail, f_RebuildTombaAppearanceModelParts):
appearance & 0xF picks a pair in chunk 1's IDX trailer - the costume's SMST,
read into one buffer - and a bone table in MAIN.EXE. Part i draws group i;
groups past the bones are spares (open-mouth heads, open hands) that
FUN_80054790 swaps in through the same pointer table. Tomba's TANP only
rotates limbs, so a body whose rig matches his topology and axes plays every
one of his moves once its groups and bone lengths are in his slots.

A swap writes three things, all checked before anything is touched:
  DAT/IDX   the costume's trail file -> a composite SMST
  MAIN.EXE  the costume's bone table -> the new body's lengths
  IMG       the body's art into chunk 1 (resident everywhere)
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory

import numpy as np

from formats.animation import skeleton_tree
from formats.models import smst_edit
from game import texture_migrate
from psx import vram as psx_vram

SYSTEM_CHUNK = 1            # its trailer is the costume table
# File slot 47 is 0x8017C800 in every US state; the next live buffer
# starts at 0x80182000, and reads are whole sectors. Retail max is 0x4FD4.
MAX_MODEL = 0x5800
OVERLAY_BASE = 0x80108F9C

SPARES = {17: 1, 18: 1, 19: 4, 20: 7}
# Wing Suit: parts 17/18 are allocated fresh, and the rebuild jumps to
# group 0x15 for them - the wings are groups 21/22 behind the spares.
WING_GROUPS = {17: 21, 18: 22}

# f_RedrawPants uploads these from MAIN.EXE whenever the pants change, so
# anything put there is overwritten at runtime.
PANTS_CLUTS = tuple(y * psx_vram.VRAM_STRIDE + 496 * 2
                    for y in (0x1E2, 0x1E5, 0x1C9, 0x1D0, 0x1B3))

BONE_NAMES = ("chest", "head",
              "left upper arm", "left forearm", "left hand",
              "right upper arm", "right forearm", "right hand",
              "pelvis",
              "left thigh", "left shin", "left foot",
              "right thigh", "right shin", "right foot",
              "ponytail", "ponytail tip", "left wing", "right wing")


@dataclass(frozen=True)
class Costume:
    slot: int               # appearance & 0xF = chunk-1 trail pair
    name: str
    table: int              # MAIN.EXE bone table
    bones: int

    def group_of(self, bone):
        return WING_GROUPS.get(bone, bone) if self.bones == 19 else bone

    def role(self, group):
        """('bone', b) / ('spare', b standing in for) / None."""
        for bone in range(self.bones):
            if self.group_of(bone) == group:
                return ("bone", bone)
        if group in SPARES:
            return ("spare", SPARES[group])
        return None

    def dropped(self, plan, group):
        """Whether `group`'s art is no longer drawn after `plan`."""
        role = self.role(group)
        return role is not None and plan.choices[role[1]][0] != KEEP


COSTUMES = (
    Costume(0, "Tomba", 0x800A3FA8, 17),
    Costume(1, "Squirrel Suit", 0x800A4030, 17),
    Costume(2, "Wing Suit", 0x800A40C8, 19),
    Costume(3, "Pig Suit", 0x800A4160, 17),
    Costume(4, "Fire Pig Robe", 0x800A41E8, 17),
    Costume(5, "Ice Pig Robe", 0x800A41E8, 17),
    Costume(6, "Ghost Pig Robe", 0x800A41E8, 17),
    Costume(7, "Earth Pig Robe", 0x800A41E8, 17),
    Costume(8, "Water Pig Robe", 0x800A41E8, 17),
    Costume(9, "Invisible Clothes", 0x800A4270, 17),
    Costume(10, "Water Pig Suit", 0x800A4160, 17),
)


def table_groups():
    """[[Costume, ...], ...] - slots that share one bone table and so can
    only change together."""
    out = {}
    for c in COSTUMES:
        out.setdefault(c.table, []).append(c)
    return list(out.values())


class SwapError(ValueError):
    pass


# --- the new body -----------------------------------------------------

@dataclass
class Source:
    name: str
    model: bytes            # SMST, group i = bone i
    bones: list             # [(parent, x, y, z), ...]
    chunk: int              # whose VRAM holds its art (None: `vram`)
    note: str = ""
    vram: bytes = None      # a file body's own staged art
    path: str = None        # the file it came from
    keep: frozenset = frozenset()   # packets already reading resident art


@dataclass
class Skeleton:
    name: str
    bones: list
    note: str = ""
    reference: bytes = None     # a body on it (group i = bone i), for cutting files


# US retail 0x800A3FA8 - kept here because an install overwrites it.
TOMBA_ROWS = [(-1, -10, 23, 0), (0, 5, -51, 0), (0, 2, -41, 37), (2, -2, 0, 41), (3, -2, 0, 41),
              (0, 2, -41, -37), (5, -2, 0, -41), (6, -2, 0, -41), (-1, -10, 23, 0), (8, 0, 13, 19),
              (9, 1, 52, 2), (10, -7, 37, 0), (8, 0, 13, -19), (12, 1, 52, -2), (13, -7, 37, 0),
              (1, -47, -10, 0), (15, -2, 35, 0)]


def tomba_skeleton(exe, dat, idx):
    """Tomba's own rig; his model is its reference only while it is still installed."""
    from formats.archive import repacker
    rows = [tuple(r) for r in skeleton_tree.read_table(exe, skeleton_tree.exe_offset(0x800A3FA8), 17)]
    start, end = repacker.parse_idx_bytes(idx)[SYSTEM_CHUNK]['trail_ranges'][0]
    reference = bytes(dat[start:end]) if rows == TOMBA_ROWS else None
    return Skeleton("Tomba", list(TOMBA_ROWS), "his own bone lengths", reference)


def skeleton_of(source):
    return Skeleton(source.name, list(source.bones), source.note, source.model)


def on_skeleton(body, skeleton):
    """A disc body on another rig: group i still rides bone i."""
    return Source(body.name, body.model, list(skeleton.bones), body.chunk,
                  body.note, body.vram, body.path, body.keep)


def installed(exe, dat, idx, slot=0):
    """A costume as the project has it now - Tomba on a retail disc, the
    swapped body after an install. Its art is resident, so editing it in
    Blender and bringing it back costs only what was repainted or added."""
    from formats.archive import repacker
    c = COSTUMES[slot]
    start, end = repacker.parse_idx_bytes(idx)[SYSTEM_CHUNK]['trail_ranges'][slot]
    rows = skeleton_tree.read_table(exe, skeleton_tree.exe_offset(c.table), 17)
    return Source(f"{c.name} slot as installed", bytes(dat[start:end]), [tuple(r) for r in rows],
                  SYSTEM_CHUNK, f"costume slot {slot} as it is in this project")


def resident(idx_path, img_path):
    """(VRAM, claimed map) of what is loaded in every area: chunks 0-2."""
    from psx import vram_map
    idx, img = Path(idx_path).read_bytes(), Path(img_path).read_bytes()
    shards = vram_map.chunk_shards(str(idx_path), str(img_path))
    vram = vram_map.loaded_vram(shards, _vram_reader(idx, img), SYSTEM_CHUNK)
    return bytes(vram), vram_map.claims_of(shards, vram_map.ALWAYS_RESIDENT)


def _chunk_file(dat, chunks, chunk, file_id):
    c = chunks[chunk]
    pointers = c['sdat_pointers']
    for n, (fid, offset) in enumerate(pointers):
        if fid == file_id:
            stop = pointers[n + 1][1] if n + 1 < len(pointers) else c['dat_end'] - c['dat_start']
            return bytes(dat[c['dat_start'] + offset:c['dat_start'] + stop])
    raise SwapError(f"AREA_{chunk:02X} has no file {file_id}")


def _trim(model):
    """An SMST cut to its last group's end - SDAT entries run to the next
    file, which may be something else entirely."""
    walked = smst_edit.smst_groups(model)
    _i, offset, _t, _q, size = walked[-1]
    return bytes(model[:offset + size])


def tabby(dat, idx, a0f):
    """Tabby as the last pig boss arena loads her: FUN_A0F__80117fa4 builds
    15 parts from file 21 on the table at 0x8011F230; file 20 is her
    unpetrified model, same rig."""
    from formats.archive import repacker
    chunks = repacker.parse_idx_bytes(idx)
    chunk = 0x13            # A0F's chunk
    model = _trim(_chunk_file(dat, chunks, chunk, 20))
    bones = skeleton_tree.read_table(a0f, 0x8011F230 - OVERLAY_BASE, 15)
    if not skeleton_tree.valid_table(bones) or smst_edit.smst_groups(model)[-1][0] != 14:
        raise SwapError("Tabby's model or skeleton is not where the US retail disc has it.")
    return Source("Tabby", model, [tuple(b) for b in bones], chunk,
                  "chunk 0x13 file 20, skeleton A0F 0x8011F230")


def from_recipe(recipe):
    """A body out of the NPC catalogue."""
    n = len(recipe.bones_data) // 8
    bones = [struct.unpack_from("<4h", recipe.bones_data, i * 8) for i in range(n)]
    return Source(recipe.name, bytes(recipe.model_data), bones, recipe.chunk_index,
                  f"AREA_{recipe.chunk_index:02X} handler 0x{recipe.handler:08X}")


# --- bodies to and from Blender ---------------------------------------
#
# A whole body is one file, standing at rest on its skeleton, one object
# per part named part_NN. On the way back a face goes to the bone its
# object names, else to the nearest bone of whichever skeleton is chosen;
# each part is then made relative to its joint again, so the body stands
# exactly as modelled on any rig.

PART_NAME = re.compile(r"part[ _\-]?0*(\d+)", re.I)


def _joints(bones):
    from formats.animation import game_rest
    return game_rest.joints(bones)          # the faces' (viewer) axes


def export_body(path, source, vram):
    """OBJ + MTL + page PNGs of `source` standing on its own bones."""
    from formats.models.obj_exchange import Face, records, write_obj
    from formats.animation import game_rest
    joints = _joints(source.bones)
    names = [n for n, _p in game_rest.hierarchy(source.bones)]
    groups = len(smst_edit.smst_groups(source.model))
    faces = []
    for bone in range(min(groups, len(source.bones))):
        label = f"part_{bone:02d}_{names[bone]}"
        dx, dy, dz = joints[bone]
        for record in records(source.model, "SMST", bone):
            f = record.face
            faces.append(Face(tuple((x + dx, y + dy, z + dz) for x, y, z in f.vertices),
                              f.uvs, f.colors, f.material, label))
    if not faces:
        raise SwapError("This body has no faces to export.")
    return write_obj(path, faces, vram)


def read_body_faces(path):
    """Faces of an OBJ or glTF, in game units (100 per file unit)."""
    from dataclasses import replace
    from formats.models import obj_exchange
    path = Path(path)
    raw = lambda rgb: rgb       # noqa: E731 - shaded per face below
    linear = path.suffix.lower() in (".glb", ".gltf")
    if linear:
        from formats.models.gltf_import import read_gltf
        faces = read_gltf(path, colors=raw)
        faces = [replace(f, vertices=tuple(tuple(v * obj_exchange.SCALE for v in p) for p in f.vertices))
                 for f in faces]
    else:
        faces = obj_exchange.read_obj(path, raw_positions=True, colors=raw)
    # As the geometry import dialog does: this tool's own T2 materials carry
    # n/15 shades, anything else a PS1 tool's "white is unshaded".
    scales = {own: obj_exchange.shades(own, linear) for own in (True, False)}
    return [replace(f, colors=tuple(None if c is None else
                                    scales[bool(obj_exchange.MATERIAL.fullmatch(f.material))](c)
                                    for c in f.colors)) for f in faces]


def _segments(bones):
    """(start, end) per bone: its joint to its furthest child's."""
    joints = _joints(bones)
    kids = _children(bones)
    out = []
    for b in range(len(bones)):
        ends = [joints[k] for k in kids.get(b, ())]
        end = max(ends, key=lambda e: float(np.linalg.norm(e - joints[b]))) if ends else joints[b]
        out.append((joints[b], end))
    return out


def _nearest(points, segments):
    """(bone, distance) of the nearest segment, per point."""
    best = np.full(len(points), np.inf)
    which = np.zeros(len(points), int)
    for b, (a, e) in enumerate(segments):
        d = e - a
        t = np.clip(((points - a) @ d) / max(float(d @ d), 1e-9), 0, 1)
        dist = np.linalg.norm(points - (a + t[:, None] * d), axis=1)
        closer = dist < best
        best[closer], which[closer] = dist[closer], b
    return which, best


def _reference(skeleton):
    """(points, bone per point): the reference body standing at rest."""
    from formats.models.obj_exchange import records
    joints = _joints(skeleton.bones)
    points, owners = [], []
    groups = len(smst_edit.smst_groups(skeleton.reference))
    for bone in range(min(groups, len(skeleton.bones))):
        for record in records(skeleton.reference, "SMST", bone):
            corners = np.array(record.face.vertices, float) + joints[bone]
            for p in list(corners) + [corners.mean(axis=0)]:
                points.append(p)
                owners.append(bone)
    return np.array(points), np.array(owners)


def _closest(centres, skeleton):
    """(bone, distance) per centre: nearest reference surface, else bone."""
    if skeleton.reference is None:
        return _nearest(centres, _segments(skeleton.bones))
    points, owners = _reference(skeleton)
    if not len(points):
        return _nearest(centres, _segments(skeleton.bones))
    which = np.zeros(len(centres), int)
    dist = np.zeros(len(centres))
    for at in range(0, len(centres), 512):
        block = centres[at:at + 512]
        d = np.linalg.norm(block[:, None, :] - points[None, :, :], axis=2)
        near = d.argmin(axis=1)
        which[at:at + 512] = owners[near]
        dist[at:at + 512] = d[np.arange(len(block)), near]
    return which, dist


def assign(faces, skeleton):
    """Bone per face: its object's part_NN, else the bone of the nearest
    surface of the skeleton's own body (or the nearest bone)."""
    centres = np.array([np.mean(f.vertices, axis=0) for f in faces], float)
    which, _d = _closest(centres, skeleton)
    out = []
    for n, f in enumerate(faces):
        m = PART_NAME.search(f.object or "")
        out.append(int(m[1]) if m and int(m[1]) < len(skeleton.bones) else int(which[n]))
    return out


def fit(faces, skeleton):
    """Mean distance from the faces to the skeleton's body - lower is
    better; how a file body finds its rig."""
    centres = np.array([np.mean(f.vertices, axis=0) for f in faces], float)
    _w, dist = _closest(centres, skeleton)
    return float(dist.mean())


def _fold_palettes(blob, vram):
    """Packets reading byte-identical palettes all read the first one."""
    out = bytearray(blob)
    first = {}
    for ind, _uvs in texture_migrate._packets(out):
        word = struct.unpack_from("<H", out, ind + texture_migrate.CLUT_WORD)[0]
        address = texture_migrate._clut_address(word)
        canonical = first.setdefault(bytes(vram[address:address + 32]), address)
        if canonical != address:
            struct.pack_into("<H", out, ind + texture_migrate.CLUT_WORD,
                             texture_migrate._clut_word(canonical, word))
    return bytes(out)


def obj_textures_flat():
    """A texture source that paints plain colours - geometry-only work."""
    from formats.models import obj_textures
    return obj_textures.Source("flat", images={})


def _resident_materials(faces, source, resident_art):
    """Materials whose pictures are, texel for texel over every face's UV
    box, what the resident VRAM already shows at their page and palette -
    art exported from an installed body and not repainted."""
    from collections import defaultdict
    from formats.models import obj_textures
    from formats.models.obj_exchange import MATERIAL
    from formats.models.gltf_export import index_atlas, palette
    if resident_art is None or source.kind != "images":
        return set()
    vram, claimed = resident_art
    atlas = index_atlas(vram)
    index = obj_textures._index(source.images or {})
    by = defaultdict(list)
    for f in faces:
        by[f.material].append(f)
    out = set()
    for name, members in by.items():
        m = MATERIAL.fullmatch(name)
        entry = obj_textures._lookup(index, name)
        if not m or entry is None or any(None in f.uvs for f in members):
            continue
        picture = obj_textures._picture(name, entry)
        if picture is None or picture.shape[:2] != (256, 256):
            continue
        code, _flags, clut, page = (int(v, 16) for v in m.groups())
        page &= 31
        address = (clut & 63) * 32 + ((clut >> 6) & 511) * 2048
        cx, cy = psx_vram.clut_address_xy(address)
        if not claimed[cy, cx:cx + 16].all():
            continue
        top, left = page // 16 * 256, page % 16 * 256
        expected = palette(vram, address, bool(code & 2))[atlas[top:top + 256, left:left + 256]]
        ok = True
        for f in members:
            us, vs = [u for u, _v in f.uvs], [v for _u, v in f.uvs]
            u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
            hx0, hx1 = page % 16 * 64 + u0 // 4, page % 16 * 64 + u1 // 4
            if not claimed[page // 16 * 256 + v0:page // 16 * 256 + v1 + 1, hx0:hx1 + 1].all():
                ok = False
                break
            a = picture[v0:v1 + 1, u0:u1 + 1]
            b = expected[v0:v1 + 1, u0:u1 + 1]
            if not (np.array_equal(a[..., :3], b[..., :3])
                    and np.array_equal(a[..., 3] >= 128, b[..., 3] >= 128)):
                ok = False
                break
        if ok:
            out.add(name)
    return out


def body_from_file(path, skeleton, texture_source=None, faces=None, resident_art=None):
    """A Source cut from an OBJ/GLB onto `skeleton`. Faces whose art is
    already resident and unchanged (`resident_art` = resident()) keep
    reading it; the rest bring their own pictures."""
    from dataclasses import replace
    from formats.models import obj_textures
    from formats.models.obj_exchange import _body, _encode, records
    from psx import gpu_packet as packet
    faces = list(faces) if faces is not None else read_body_faces(path)
    names = {f.material for f in faces}
    source = texture_source or obj_textures.discover(path, names)
    live = _resident_materials(faces, source, resident_art)
    bones = assign(faces, skeleton)
    joints = _joints(skeleton.bones)
    parts = [[] for _ in skeleton.bones]
    keep = set()

    def local(raw, bone):
        kind = "tri" if len(raw) == 36 else "quad"
        jx, jy, jz = joints[bone]
        packet.write_vertices(raw, 0, kind, [(round(x - jx), round(y - jy), round(z - jz))
                                             for x, y, z in packet.read_vertices(raw, 0, kind)])
        return bytes(raw)

    reused = [n for n, f in enumerate(faces) if f.material in live]
    for n in reused:
        raw = local(bytearray(_encode(faces[n], {})), bones[n])
        parts[bones[n]].append(raw)
        keep.add(raw)
    rest = [n for n, f in enumerate(faces) if f.material not in live]
    vram = None
    summary = {"copied": 0, "reduced": 0, "flat": 0}
    if rest:
        own = [faces[n] for n in rest]
        wanted = {f.material for f in own}
        missing = {n for n in wanted if not obj_textures.untextured(n)} - source.textured(wanted)
        if missing and source.kind != "flat":
            raise SwapError(f"No picture for material(s) {', '.join(sorted(missing))}. Export a .glb "
                            f"(it carries its images) or keep the PNGs beside the OBJ.")
        staged, vram, summary, order = obj_textures.build(own, source)
        staged = _fold_palettes(staged, vram)
        for k, record in enumerate(records(staged, "SMST", 0)):
            n = rest[order[k]]
            parts[bones[n]].append(local(bytearray(record.raw), bones[n]))
    model = smst_edit.rebuild(None, [_body(p, bytes(16)) for p in parts])
    note = (f"{Path(path).name}: {len(faces)} faces on {skeleton.name}'s rig; "
            f"{len(reused)} reuse art already loaded, {summary['copied']} material(s) copied, "
            f"{summary['reduced']} reduced to 16 colours, {summary['flat']} plain")
    return Source(Path(path).stem, model, list(skeleton.bones), None, note, vram, str(path),
                  frozenset(keep))


# --- skeletons --------------------------------------------------------

def absolute(bones):
    """Rest position of every bone in the table's own axes."""
    out = []
    for parent, x, y, z in bones:
        base = out[parent] if parent >= 0 else (0, 0, 0)
        out.append((base[0] + x, base[1] + y, base[2] + z))
    return out


def _children(bones):
    kids = {}
    for i, (parent, *_rest) in enumerate(bones):
        kids.setdefault(parent, []).append(i)
    return kids


def _chain(bones, kids, start, length):
    out = [start]
    while len(out) < length:
        nxt = kids.get(out[-1])
        if not nxt:
            break
        # the child that carries the limb on, not a side branch
        out.append(max(nxt, key=lambda k: sum(v * v for v in bones[k][1:])))
    return out


def roles(bones):
    """{tomba bone: this skeleton's bone}, read off its shape.

    Chest: the bone two sideways (+/-z) chains leave. Pelvis: the one two
    downward chains leave. Head: the chest child that goes up."""
    kids = _children(bones)
    at = absolute(bones)

    def side_chains(b, downward):
        out = {}
        for k in kids.get(b, ()):
            _p, x, y, z = bones[k]
            chain = _chain(bones, kids, k, 3)
            tip = at[chain[-1]]
            dy = tip[1] - at[b][1]
            dz = tip[2] - at[b][2]
            if abs(z) < 4 and abs(dz) < 8:
                continue
            if downward != (dy > abs(dz)):
                continue
            side = 1 if dz > 0 or (dz == 0 and z > 0) else -1
            if side not in out or abs(dz) + abs(dy) > out[side][0]:
                out[side] = (abs(dz) + abs(dy), chain)
        return {s: c for s, (_m, c) in out.items()}

    found = {}
    for b in range(len(bones)):
        arms = side_chains(b, False)
        legs = side_chains(b, True)
        if len(arms) == 2 and 0 not in found:
            found[0] = b
            for side, first in ((1, 2), (-1, 5)):
                for n, bone in enumerate(arms[side]):
                    found[first + n] = bone
            up = [k for k in kids.get(b, ()) if k not in arms[1] + arms[-1]
                  and bones[k][2] < 0]
            if up:
                found[1] = min(up, key=lambda k: bones[k][2])
        if len(legs) == 2 and 8 not in found:
            found[8] = b
            for side, first in ((1, 9), (-1, 12)):
                for n, bone in enumerate(legs[side]):
                    found[first + n] = bone
    if 1 in found:
        hair = _chain(bones, kids, kids[found[1]][0], 2) if kids.get(found[1]) else []
        for n, bone in enumerate(hair):
            found[15 + n] = bone
    return found


def auto_map(source_bones, target_bones):
    """[source bone or None] per target bone."""
    n = min(len(source_bones), len(target_bones))
    same = [b[0] for b in source_bones[:n]] == [b[0] for b in target_bones[:n]]
    if same and n >= 15:
        return [i if i < n else None for i in range(len(target_bones))]
    mine = roles(source_bones)
    return [mine.get(i) for i in range(len(target_bones))]


# --- choices ----------------------------------------------------------

SOURCE, KEEP, EMPTY = "source", "keep", "empty"


@dataclass
class Plan:
    """What every bone of a costume becomes."""
    choices: list               # per target bone: (SOURCE, i) / (KEEP,) / (EMPTY,)
    merge_extras: bool = True   # unmapped source bones ride on their parent


def default_plan(source, costume):
    mapping = auto_map(source.bones, [(p, 0, 0, 0) for p in TOMBA_PARENTS[:costume.bones]])
    choices = []
    for bone in range(costume.bones):
        if bone >= 17:
            choices.append((KEEP,))         # wings stay
        elif mapping[bone] is not None and mapping[bone] < len(source.bones):
            choices.append((SOURCE, mapping[bone]))
        else:
            choices.append((EMPTY,))
    return Plan(choices)


TOMBA_PARENTS = (-1, 0, 0, 2, 3, 0, 5, 6, -1, 8, 9, 10, 8, 12, 13, 1, 15, 0, 0)


# --- composing --------------------------------------------------------

TRI_VERTS = ((17, 15, 13), (19, 23, 21), (29, 27, 25))
QUAD_VERTS = ((33, 31, 29), (21, 19, 17), (23, 27, 25), (35, 39, 37))


def _shifted(body, bone_delta):
    """A group body moved by a bone-axes offset. Meshes are a quarter
    turn from bone tables (game_rest.joints): raw x/y/z = bone z/y/x."""
    dz, dy, dx = bone_delta
    out = bytearray(body)
    for kind, at, _size in smst_edit.packet_slots(body):
        ind = at + 3
        for ox, oy, oz in (TRI_VERTS if kind == "tri" else QUAD_VERTS):
            for off, d in ((ox, dx), (oy, dy), (oz, dz)):
                if d:
                    v = struct.unpack_from("<h", out, ind + off)[0] + d
                    if not -0x8000 <= v < 0x8000:
                        raise SwapError("a merged part ends up out of range")
                    struct.pack_into("<h", out, ind + off, v)
    return bytes(out)


def _joined(a, b):
    """Two group bodies as one: triangles, then quads."""
    ta, qa = struct.unpack_from("<HH", a, 0)
    tb, qb = struct.unpack_from("<HH", b, 0)
    h = smst_edit.GROUP_HEADER
    tri_a, quad_a = a[h:h + ta * 36], a[h + ta * 36:h + ta * 36 + qa * 44]
    tri_b, quad_b = b[h:h + tb * 36], b[h + tb * 36:h + tb * 36 + qb * 44]
    head = bytearray(a[:h])
    struct.pack_into("<HH", head, 0, ta + tb, qa + qb)
    return bytes(head) + tri_a + tri_b + quad_a + quad_b


def _split(body, keep):
    """(packets in `keep`, the rest) of a group body, each as a body.
    `keep` None: everything is kept."""
    from formats.models.obj_exchange import _body
    head = body[:smst_edit.GROUP_HEADER]
    packets = [body[at:at + size] for _k, at, size in smst_edit.packet_slots(body)]
    kept = packets if keep is None else [p for p in packets if p in keep]
    rest = [] if keep is None else [p for p in packets if p not in keep]
    return _body(kept, head), _body(rest, head)


def _faces_only(body):
    t, q = struct.unpack_from("<HH", body, 0)
    return body[:smst_edit.GROUP_HEADER + t * 36 + q * 44]


def source_bodies(source, plan):
    """{source bone: body} for every source bone the plan uses, with the
    unused ones merged into their nearest used ancestor."""
    bodies = [_faces_only(b) for b in smst_edit.bodies_of(source.model)]
    used = {c[1] for c in plan.choices if c[0] == SOURCE}
    # a rig with more bones than the body has parts: those bones draw nothing
    out = {b: bodies[b] if b < len(bodies) else smst_edit.empty_body() for b in used}
    merged = []
    if plan.merge_extras:
        at = absolute(source.bones)
        for bone in range(len(source.bones)):
            if bone in used or bone >= len(bodies):
                continue
            up = source.bones[bone][0]
            while up >= 0 and up not in used:
                up = source.bones[up][0]
            if up < 0 or not any(struct.unpack_from("<HH", bodies[bone], 0)):
                continue
            delta = tuple(a - b for a, b in zip(at[bone], at[up]))
            out[up] = _joined(out[up], _shifted(bodies[bone], delta))
            merged.append((bone, up))
    return out, merged


def compose(costume, costume_model, plan, bodies):
    """The costume's new SMST. Spares point at the body of the bone they
    stand in for - the game only ever reads through the pointer table, and
    a spare is drawn instead of its bone, never beside it."""
    old = smst_edit.bodies_of(costume_model)
    groups = len(old)
    if groups < costume.bones:
        raise SwapError(f"{costume.name} has {groups} groups for {costume.bones} bones")
    unique, refs = [], []

    def ref(body):
        for n, b in enumerate(unique):
            if b == body:
                return n
        unique.append(body)
        return len(unique) - 1

    def body_for(bone):
        kind = plan.choices[bone]
        if kind[0] == SOURCE:
            return bodies[kind[1]]
        if kind[0] == KEEP:
            return old[costume.group_of(bone)]
        return smst_edit.empty_body()

    by_bone = {}
    for g in range(groups):
        role = costume.role(g)
        if role is None or plan.choices[role[1]][0] == KEEP:
            refs.append(ref(old[g]))
            continue
        bone = role[1]
        if bone not in by_bone:
            by_bone[bone] = ref(body_for(bone))
        refs.append(by_bone[bone])
    table_end = 4 + groups * 4
    starts, at = [], table_end
    for body in unique:
        starts.append(at)
        at += len(body)
    out = bytearray(struct.pack("<HH", 0, groups))
    for r in refs:
        out += struct.pack("<I", starts[r])
    for body in unique:
        out += body
    return bytes(out)


def bone_table(costume, costume_bones, costume_model, source, plan, bodies):
    """The costume's bone records for the new body, feet where his were."""
    src = absolute(source.bones)
    table = [list(b) for b in costume_bones]
    for bone, choice in enumerate(plan.choices):
        if choice[0] != SOURCE:
            continue
        parent = costume_bones[bone][0]
        here = src[choice[1]]
        if parent >= 0 and plan.choices[parent][0] == SOURCE:
            base = src[plan.choices[parent][1]]
            table[bone][1:] = [h - b for h, b in zip(here, base)]
        elif parent < 0:
            table[bone][1:] = list(here)
    old = smst_edit.bodies_of(costume_model)
    old_feet = _lowest(costume_bones, {b: old[costume.group_of(b)] for b in range(costume.bones)},
                       range(costume.bones))
    new_bodies = {b: (bodies[c[1]] if c[0] == SOURCE else
                      old[costume.group_of(b)] if c[0] == KEEP else None)
                  for b, c in enumerate(plan.choices)}
    new_feet = _lowest(table, new_bodies, range(costume.bones))
    lift = int(round(old_feet - new_feet)) if old_feet is not None and new_feet is not None else 0
    for bone, row in enumerate(table):
        if row[0] < 0 and plan.choices[bone][0] == SOURCE:
            row[2] += lift
    return [tuple(r) for r in table], lift


def _lowest(bones, bodies, which):
    """Largest y (down) any drawn vertex reaches at rest."""
    at = absolute(bones)
    best = None
    for bone in which:
        body = bodies[bone] if isinstance(bodies, dict) else (bodies[bone] if bone < len(bodies) else None)
        if not body:
            continue
        for kind, off, _s in smst_edit.packet_slots(body):
            for _ox, oy, _oz in (TRI_VERTS if kind == "tri" else QUAD_VERTS):
                y = struct.unpack_from("<h", body, off + 3 + oy)[0] + at[bone][1]
                best = y if best is None else max(best, y)
    return best


def pack_table(rows):
    return b"".join(struct.pack("<4h", *r) for r in rows)


# --- textures ---------------------------------------------------------

def sampled(blob, cells=None):
    """Halfword map of every texel and palette `blob` reads."""
    from psx import vram_map
    out = vram_map.empty_map() if cells is None else cells
    for ind, uvs in texture_migrate._packets(blob):
        page = blob[ind + texture_migrate.PAGE_BYTE] & 0x1F
        column = (page % psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_HALFWORDS
        row = (page // psx_vram.ATLAS_COLUMNS) * psx_vram.PAGE_ROWS
        us = [blob[ind + ou] for ou, _ov in uvs]
        vs = [blob[ind + ov] for _ou, ov in uvs]
        out[row + min(vs):row + max(vs) + 1,
            column + min(us) // 4:column + max(us) // 4 + 1] = True
        word = struct.unpack_from("<H", blob, ind + texture_migrate.CLUT_WORD)[0]
        x, y = psx_vram.clut_address_xy(texture_migrate._clut_address(word))
        out[y, x:x + 16] = True
    return out


def _is_smst(data):
    try:
        walked = smst_edit.smst_groups(data)
        return bool(walked) and len(data) >= 4 and data[:2] == b"\0\0"
    except Exception:
        return False


# --- the whole job ----------------------------------------------------

@dataclass
class Prepared:
    files: dict                 # Path -> new bytes
    originals: dict             # Path -> bytes it was prepared against
    report: str
    models: dict = field(default_factory=dict)      # slot -> composite SMST
    tables: dict = field(default_factory=dict)      # table address -> rows


def _vram_reader(idx, img):
    from psx.vram_viewer import decode_vram_bytes
    cache = {}

    def chunk_vram(n):
        if n not in cache:
            start, end = struct.unpack_from("<II", idx, n * 0x800)
            cache[n] = decode_vram_bytes(img[start:end]) if end > start else None
        return cache[n]
    return chunk_vram


def prepare(exe_path, dat_path, idx_path, img_path, source, slots, plans,
            state_paths=(), progress=print):
    """Everything a swap writes, verified, without writing it.

    `source` is one Source for every slot, or {slot: Source} - a body per
    costume. `slots` are costume slot numbers; every slot sharing a bone
    table with one of them must be in it too. `plans` is {slot: Plan}."""
    from formats.archive import repacker
    from formats.images import img_writer
    from psx import state_vram, vram_map

    paths = [Path(p) for p in (exe_path, dat_path, idx_path, img_path)]
    originals = {p: p.read_bytes() for p in paths}
    exe, dat, idx, img = (originals[p] for p in paths)
    chosen = [c for c in COSTUMES if c.slot in set(slots)]
    sources = source if isinstance(source, dict) else {c.slot: source for c in chosen}
    if not chosen:
        raise SwapError("Pick at least one costume.")
    for group in table_groups():
        picked = [c for c in group if c in chosen]
        if picked and len(picked) != len(group):
            raise SwapError(" / ".join(c.name for c in group)
                            + " share one skeleton in MAIN.EXE and can only be swapped together.")
    chunks = repacker.parse_idx_bytes(idx)
    trail = chunks[SYSTEM_CHUNK]['trail_ranges']
    lines = []

    def costume_model(c):
        start, end = trail[c.slot]
        return bytes(dat[start:end])

    def costume_bones(c):
        return [tuple(r) for r in skeleton_tree.read_table(
            exe, skeleton_tree.exe_offset(c.table), c.bones)]

    # 1. bodies: each costume's own source; a body's packets split into the
    # ones already reading resident art (kept) and the ones whose art moves
    progress("Composing the new bodies…")
    per_slot = {}
    groups = {}
    for c in chosen:
        src = sources[c.slot]
        bodies, merged = source_bodies(src, plans[c.slot])
        per_slot[c.slot] = (src, bodies, merged)
        g = groups.setdefault(id(src), dict(source=src, order=[]))
        for body in bodies.values():
            if body not in g["order"]:
                g["order"].append(body)
    if not any(g["order"] for g in groups.values()):
        raise SwapError("Nothing of the new body is used.")

    shards = vram_map.chunk_shards(idx_path, img_path)
    chunk_vram = _vram_reader(idx, img)
    resident_before = vram_map.loaded_vram(shards, chunk_vram, SYSTEM_CHUNK)
    for g in groups.values():
        src = g["source"]
        all_live = src.vram is None and src.chunk in vram_map.ALWAYS_RESIDENT
        g["vram"] = (bytes(src.vram) if src.vram is not None
                     else bytes(vram_map.loaded_vram(shards, chunk_vram, src.chunk)))
        g["split"] = [_split(body, None if all_live else src.keep) for body in g["order"]]
        g["staged"] = smst_edit.rebuild(None, [st for _k, st in g["split"]])
        g["kept"] = smst_edit.rebuild(None, [k for k, _st in g["split"]])
        g["moving"] = any(struct.unpack_from("<HH", st, 0) != (0, 0) for _k, st in g["split"])

    # 2. where its art can go
    progress("Finding room in VRAM…")
    replaced = vram_map.empty_map()
    kept = vram_map.empty_map()
    keepers = []            # (name, blob) that must still draw the same
    for c in COSTUMES:
        if c not in chosen:
            keepers.append((c.name, costume_model(c)))
            continue
        for g, body in enumerate(smst_edit.bodies_of(costume_model(c))):
            if not any(struct.unpack_from("<HH", body, 0)):
                continue
            one = smst_edit.rebuild(None, [_faces_only(body)])
            if c.dropped(plans[c.slot], g):
                sampled(one, replaced)
            else:
                keepers.append((f"{c.name} group {g}", one))
    # Anything on the disc may sample resident art - the outro's Tuxedo
    # Tomba reads Tomba's page 23 - so every other model has to keep drawing.
    costume_files = {trail[c.slot][0] for c in COSTUMES}
    seen_files = set(costume_files)
    for n, c in enumerate(chunks):
        ptrs = c['sdat_pointers']
        spans = [(c['dat_start'] + off,
                  c['dat_start'] + (ptrs[k + 1][1] if k + 1 < len(ptrs) else c['dat_end'] - c['dat_start']),
                  f"AREA_{n:02X} file {fid}") for k, (fid, off) in enumerate(ptrs)]
        spans += [(s, e, f"trail 0x{s:X}") for s, e in c['trail_ranges']]
        for start, end, name in spans:
            if start in seen_files:
                continue
            seen_files.add(start)
            data = bytes(dat[start:end])
            if _is_smst(data):
                try:
                    keepers.append((name, _trim(data)))
                except Exception:
                    pass
    for g in groups.values():
        if any(struct.unpack_from("<HH", k, 0) != (0, 0) for k, _st in g["split"]):
            keepers.append((f"{g['source'].name} (art it reuses)", g["kept"]))
    for _name, blob in keepers:
        try:
            sampled(blob, kept)
        except Exception:
            pass
    reclaim = replaced & ~kept
    reclaim &= ~vram_map.claims_of(shards, [a for a in shards if a not in vram_map.ALWAYS_RESIDENT])
    reclaim &= ~vram_map.display_map()
    for address in PANTS_CLUTS:
        x, y = psx_vram.clut_address_xy(address)
        reclaim[y, x:x + 16] = False
    occupied = None
    for path in state_paths:
        seen = state_vram.read(path, chunk_vram(SYSTEM_CHUNK))
        here = state_vram.runtime_only(seen, shards, state_vram.resident_areas(seen, shards, chunk_vram))
        occupied = here if occupied is None else occupied | here
    if occupied is not None:
        reclaim &= ~occupied
    free = vram_map.free_for(shards, list(shards), occupied)

    def place_all(space):
        space = space.copy()
        out = {}
        for key, g in groups.items():
            if not g["moving"]:
                continue
            found = texture_migrate.plan(g["staged"], space, pages=vram_map.USABLE_PAGES)
            for move in found.moves:
                x, y, w, h = move.dest_rect
                space[y:y + h, x:x + w] = False
            for new in found.cluts.values():
                x, y = psx_vram.clut_address_xy(new)
                space[y, x:x + 16] = False
            out[key] = found
        return out
    try:
        placed = place_all(reclaim)
        where = "into the art of the replaced costumes"
    except texture_migrate.MigrationError:
        try:
            placed = place_all((reclaim | free) & ~kept)
        except texture_migrate.MigrationError as error:
            raise SwapError(f"The new art does not fit in the VRAM every area leaves free: {error}. "
                            f"Reuse more of the existing textures, or paint smaller.") from error
        where = "into the replaced costumes' art and free VRAM"
    additions = []
    for key, g in groups.items():
        found = placed.get(key)
        g["moved"] = texture_migrate.retarget(g["staged"], found) if found else g["staged"]
        if found:
            additions += texture_migrate.shards_for(found, g["vram"])
        reused = sum(len(smst_edit.packet_slots(k)) for k, _st in g["split"])
        lines.append(f"{g['source'].name}: " + (
            f"{len(found.moves)} texture patch(es) and {len(found.cluts)} palette(s) copied {where}"
            if found else "no art to copy") + (f"; {reused} face(s) reuse art already loaded" if reused else "") + ".")
    if additions and not state_paths:
        lines.append("No savestate given: what the game uploads at runtime is "
                     "not accounted for beyond the pants palettes.")

    resident_after = texture_migrate.patch(resident_before, additions)
    for g in groups.values():
        if g["moving"]:
            _n, bad = texture_migrate.verify(g["staged"], g["moved"], g["vram"], bytes(resident_after))
            if bad:
                raise SwapError(f"{len(bad)} texel reads of {g['source'].name} would change - not safe.")
    for name, blob in keepers:
        try:
            _n, bad = texture_migrate.verify(blob, blob, resident_before, bytes(resident_after))
        except Exception:
            continue
        if bad:
            raise SwapError(f"{name} would lose {len(bad)} texels it still draws.")
    renamed = {}
    for g in groups.values():
        moved = smst_edit.bodies_of(g["moved"])
        for n, body in enumerate(g["order"]):
            renamed[(id(g["source"]), body)] = _joined(g["split"][n][0], moved[n])

    # 3. models and skeletons
    progress("Building the costumes…")
    new_dat = bytearray(dat)
    edit_chunks = repacker.parse_idx_bytes(idx)
    models, tables = {}, {}
    for c in sorted(chosen, key=lambda c: -trail[c.slot][0]):
        src, bodies, merged = per_slot[c.slot]
        bodies = {b: renamed[(id(src), body)] for b, body in bodies.items()}
        model = compose(c, costume_model(c), plans[c.slot], bodies)
        if len(model) > MAX_MODEL:
            raise SwapError(f"{c.name}: {len(model)} bytes, over the {MAX_MODEL} the "
                            f"game is known to load.")
        rows, lift = bone_table(c, costume_bones(c), costume_model(c), src,
                                plans[c.slot], bodies)
        if c.table in tables and tables[c.table] != rows:
            raise SwapError(f"{c.name} wants a different skeleton from the costume it shares one with. "
                            f"Use the same bone map for both.")
        tables[c.table] = rows
        models[c.slot] = model
        new_dat = repacker._apply_trail_replacement(new_dat, edit_chunks, trail[c.slot][0], model)
        extra = f", {len(merged)} extra part(s) merged" if merged else ""
        lines.append(f"{c.name} <- {src.name}: {len(model)} bytes, feet lifted {lift:+d}{extra}.")
    new_idx = repacker._idx_bytes_from_chunks(edit_chunks, idx)
    new_exe = bytearray(exe)
    for address, rows in tables.items():
        at = skeleton_tree.exe_offset(address)
        new_exe[at:at + len(rows) * 8] = pack_table(rows)

    # 4. chunk 1's art
    new_img = img
    if additions:
        start, end = struct.unpack_from("<II", new_idx, SYSTEM_CHUNK * 0x800)
        painted = img_writer.paint(img[start:end], img_writer.merged(additions))
        with TemporaryDirectory(prefix="tomba-swap-img-") as folder:
            folder = Path(folder)
            a, b, c, e = (folder / n for n in ("in.IDX", "in.IMG", "out.IDX", "out.IMG"))
            a.write_bytes(new_idx)
            b.write_bytes(img)
            img_writer.rebuild(a, b, {SYSTEM_CHUNK: painted}, c, e)
            new_idx, new_img = c.read_bytes(), e.read_bytes()
        errors = img_writer.check(new_idx, new_img)
        if errors:
            raise SwapError("\n".join(errors))
    files = dict(zip(paths, (bytes(new_exe), bytes(new_dat), new_idx, new_img)))
    return Prepared(files, originals, "\n".join(lines), models, tables)


def commit(prepared):
    """Install a prepared swap, refusing stale files, rolling back on failure."""
    if any(p.read_bytes() != data for p, data in prepared.originals.items()):
        raise SwapError("The project changed while the swap was prepared. Prepare it again.")
    staged, written = [], []
    try:
        for path, data in prepared.files.items():
            if data == prepared.originals[path]:
                continue
            temporary = path.with_name(path.name + ".swap-writing")
            temporary.write_bytes(data)
            staged.append((path, temporary))
        for path, temporary in staged:
            os.replace(temporary, path)
            written.append(path)
    except Exception:
        for path in reversed(written):
            path.write_bytes(prepared.originals[path])
        raise
    finally:
        for _path, temporary in staged:
            if temporary.exists():
                temporary.unlink()


# --- preview ----------------------------------------------------------

def draft(source, costume, costume_model, costume_bones, plan):
    """(SMST, bone rows) as they would be, art not yet moved - for preview."""
    bodies, _merged = source_bodies(source, plan)
    model = compose(costume, costume_model, plan, bodies)
    rows, _lift = bone_table(costume, costume_bones, costume_model, source, plan, bodies)
    return model, rows


def preview_vram(source, resident_vram):
    """What a body that mixes reused and new art draws against: the
    resident VRAM, with the new art's staged cells laid over it."""
    if source.vram is None or not source.keep:
        return source.vram
    staged = smst_edit.rebuild(None, [_split(_faces_only(b), source.keep)[1]
                                      for b in smst_edit.bodies_of(source.model)])
    cells = sampled(staged)
    out = np.frombuffer(bytes(resident_vram), "<u2").reshape(512, 1024).copy()
    new = np.frombuffer(bytes(source.vram), "<u2").reshape(512, 1024)
    out[cells] = new[cells]
    return out.astype("<u2").tobytes()


def tomba_animations(exe, dat, idx):
    """(anmp, bank): Tomba's TANP (chunk 1 file 4) and the verified US clips."""
    from formats.animation import anmp_parser, sequences
    from formats.archive import repacker
    chunks = repacker.parse_idx_bytes(idx)
    blob = _chunk_file(dat, chunks, SYSTEM_CHUNK, 4)
    anmp = anmp_parser.parse_anmp(blob)
    bank = sequences.tomba_bank([("MAIN.EXE", exe)], anmp, 4)
    if bank is None:
        raise SwapError("Tomba's animations need the verified US MAIN.EXE and TANP.")
    return anmp, bank


def posed(model, costume, rows, anmp, clip, max_ticks=240):
    """A clip played on a costume model - the shape event_preview.tomba_frames
    hands the viewer, with groups put in bone order first."""
    from formats.animation import anmp_parser, game_rest, sequences
    from formats.animation.anmp_skeleton import pose_transforms
    from formats.models.smst_parser import parse_smst
    bodies = smst_edit.bodies_of(model)
    order = [costume.group_of(b) for b in range(costume.bones)] + sorted(SPARES)
    index = {g: n for n, g in enumerate(order)}
    view = smst_edit.rebuild(None, [bodies[g] for g in order])
    spares = {index[g]: b for g, b in SPARES.items()}
    parsed = parse_smst(view)
    vertices, pivots = game_rest.rest_pose(parsed, rows, spares)
    parsed = dict(parsed, vertices=vertices.tolist())
    hierarchy = game_rest.hierarchy(rows)
    poses = {f.index: f for f in anmp.frames}
    scales = sequences.scale_states(clip, poses)
    frames = []
    for tick in range(max(1, min(clip.duration, max_ticks))):
        row, nxt, amount = clip.sample(tick)
        rotations, translation, _ = anmp_parser.blend(
            poses[clip.steps[row].pose],
            poses[clip.steps[nxt].pose] if nxt is not None else None, amount)
        frames.append(pose_transforms(rotations, translation, hierarchy, pivots,
                                      scales=scales[row]))
    drawn = {index[clip.parts.get(b, costume.group_of(b))] for b in range(costume.bones)}
    hidden = set(range(len(order))) - drawn
    loop = (clip.starts[clip.loop_start]
            if clip.loop_start is not None and clip.duration <= max_ticks else None)
    return dict(model=parsed, pivots=pivots, frames=frames, hidden=hidden,
                spares={g: b for g, b in spares.items() if g in drawn}, loop=loop)

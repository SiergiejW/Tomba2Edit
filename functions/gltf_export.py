"""glTF 2.0 export for the model formats on the disc.

One writer for all of them, because they all end up as the same thing:
MDAT and SCLD hand back the dict exportMDAT builds, and an SMST is that
dict plus the groups that say which part of it belongs to which bone.
Add a bone table and it exports rigged; add ANMP frames as well and it
exports animated.

WHAT THE PSX DOES THAT GLTF DOES NOT
------------------------------------
The disc stores no finished textures. A face names a 16-colour palette
(a CLUT) sitting somewhere in VRAM, and its texels are 4-bit indices
into that palette - the same bytes read through a different CLUT are a
different picture, which is how one texture page dresses a dozen
characters. The viewer does this the way the hardware does, in a
fragment shader, with the index map and the palette as separate
textures.

Nothing in glTF can express that. Its materials are a fixed set of PBR
inputs with no room for an indirection, so the palette lookup has to
happen here, at export: each distinct CLUT a model uses becomes its own
baked RGBA texture. That is why one model comes out as several
materials - they are not different surfaces, they are the same texture
page read through different palettes.

Baking whole VRAM per palette would be absurd - the index map is
4096x512, and Tomba alone uses eleven CLUTs, which is 92MB of mostly
nothing. Each material is cropped to the region its own faces actually
sample and its UVs rescaled into that crop, so a character exports as a
handful of small textures instead.

LIT, NOT UNLIT
--------------
A PSX model is painted rather than lit - the viewer draws the palette
colour times the vertex colour and nothing else - so KHR_materials_unlit
describes what the game does most exactly. It is deliberately not used.
Unlit materials ignore lamps, and a model that cannot be lit is no use
to anyone building a scene around it. These export as ordinary
metallic-roughness materials, so a lamp works on them straight away;
the vertex colours are still there and still multiply, which means the
game's own baked shading is what a light adds to rather than replaces.

That needs normals, and the disc has none - there was never anything to
store. They are computed from the geometry here, which makes them the
one thing in these files derived rather than read (see _normals).

WHAT IS LOST, HONESTLY
----------------------
Quads. The OBJ path keeps them; glTF has no quad primitive, so
everything is triangulated on the way out.

Vertex colours above 1.0. The PSX treats 128 as neutral and lets a
vertex brighten its texture up to 2x. glTF says COLOR_0 is in [0, 1],
so anything above neutral is clamped - a few blown-out highlights come
out merely white.

RIGGING FALLS OUT FOR FREE
--------------------------
An SMST group's vertices are already relative to its own bone (the game
draws group i with bone i's matrix and never unpacks anything), which is
exactly what a glTF skin wants when the inverse bind matrices are
identity - so they are left undefined, which glTF reads as identity.

Animation is just as direct. skeleton.pose_transforms composes a limb as
`parent_rotation @ local`, and a glTF node hierarchy composes children
against parents the same way, so each bone's own rotation goes straight
onto its node and the hierarchy does the rest.
"""
import base64
import json
import struct

import numpy as np

# The index map every 3D viewer samples: VRAM read as 4bpp, two texels
# per byte. gui/vram_viewer.py builds the same picture for display,
# spread over RGB as index * 17; here the raw 0-15 index is wanted.
ATLAS_WIDTH = 4096
ATLAS_HEIGHT = 512

# glTF enum values, named so the tables below read as something.
FLOAT = 5126
UNSIGNED_INT = 5125
UNSIGNED_SHORT = 5123
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
NEAREST = 9728
CLAMP_TO_EDGE = 33071
TRIANGLES = 4

# A texel of padding around each cropped material, so a renderer that
# filters at the edge has something to filter against instead of
# sampling whatever the neighbouring texture page happens to hold.
PAD = 1

# The game runs at 30fps and the ANMP transport defaults there.
DEFAULT_FPS = 30

# What the viewers divide raw game units by to get something sensibly
# sized on screen (gui/smst/smst_viewer.UNIT_SCALE). Kept as a number
# here rather than imported, because that module is a QOpenGLWidget and
# this one has no business dragging a GL context in to write a file.
UNIT_SCALE = 100.0


def index_atlas(vram_bytes):
    """VRAM as one (512, 4096) array of 4-bit palette indices."""
    want = 1024 * 512 * 2
    raw = np.frombuffer(bytes(vram_bytes[:want]), dtype=np.uint8)
    if raw.size < want:
        raw = np.pad(raw, (0, want - raw.size))
    rows = raw.reshape(ATLAS_HEIGHT, 0x800)
    texels = np.empty((ATLAS_HEIGHT, ATLAS_WIDTH), dtype=np.uint8)
    texels[:, 0::2] = rows & 0x0F
    texels[:, 1::2] = rows >> 4
    return texels


def palette(vram_bytes, address, transparent=False):
    """One CLUT as (16, 4) RGBA, read the way the viewers read it.

    BGR555, 5 bits a channel scaled by 8, and black is the PSX's
    transparent - which is a colour key, not an alpha channel, so it
    comes out as alpha 0 here and the material masks on it."""
    out = np.zeros((16, 4), dtype=np.uint8)
    data = bytes(vram_bytes)
    for i in range(16):
        at = address + i * 2
        word = (data[at] | (data[at + 1] << 8)) if at + 1 < len(data) else 0
        r = (word & 0x1F) * 8
        g = ((word >> 5) & 0x1F) * 8
        b = ((word >> 10) & 0x1F) * 8
        alpha = 0 if not (r or g or b) else (128 if transparent else 255)
        out[i] = (r, g, b, alpha)
    return out


def _png(rgba):
    """A PNG of an (h, w, 4) uint8 array, written here to keep the
    exporter free of an image library it would otherwise only use to
    save a handful of small crops."""
    import zlib

    height, width = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(height))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class _Buffer:
    """The one binary blob, and the accessors that point into it."""

    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def _view(self, payload, target=None):
        while len(self.data) % 4:            # accessors must be aligned
            self.data.append(0)
        offset = len(self.data)
        self.data.extend(payload)
        self.views.append({"buffer": 0, "byteOffset": offset,
                           "byteLength": len(payload),
                           **({"target": target} if target else {})})
        return len(self.views) - 1

    def add(self, array, kind, component, target=None, minmax=False):
        view = self._view(array.tobytes(), target)
        accessor = {"bufferView": view, "componentType": component,
                    "count": len(array), "type": kind}
        if minmax:
            flat = array.reshape(len(array), -1)
            accessor["min"] = flat.min(axis=0).tolist()
            accessor["max"] = flat.max(axis=0).tolist()
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _quaternion(matrix):
    """A 3x3 rotation as glTF's (x, y, z, w)."""
    trace = matrix[0, 0] + matrix[1, 1] + matrix[2, 2]
    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (matrix[2, 1] - matrix[1, 2]) * s
        y = (matrix[0, 2] - matrix[2, 0]) * s
        z = (matrix[1, 0] - matrix[0, 1]) * s
    elif matrix[0, 0] > matrix[1, 1] and matrix[0, 0] > matrix[2, 2]:
        s = 2.0 * np.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2])
        w = (matrix[2, 1] - matrix[1, 2]) / s
        x = 0.25 * s
        y = (matrix[0, 1] + matrix[1, 0]) / s
        z = (matrix[0, 2] + matrix[2, 0]) / s
    elif matrix[1, 1] > matrix[2, 2]:
        s = 2.0 * np.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2])
        w = (matrix[0, 2] - matrix[2, 0]) / s
        x = (matrix[0, 1] + matrix[1, 0]) / s
        y = 0.25 * s
        z = (matrix[1, 2] + matrix[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1])
        w = (matrix[1, 0] - matrix[0, 1]) / s
        x = (matrix[0, 2] + matrix[2, 0]) / s
        y = (matrix[1, 2] + matrix[2, 1]) / s
        z = 0.25 * s
    return [float(x), float(y), float(z), float(w)]


def _normals(points, triangles):
    """Smooth vertex normals, averaged from the faces meeting at each.

    The disc has none - a PSX model is painted, not lit, so there was
    never anything to store - but a glTF material that responds to a
    lamp needs them, and a mesh without them shades flat and facetted.
    So they are worked out from the geometry here. That makes them the
    one thing in the file that is derived rather than read.

    Winding is not consistent across a model that was always drawn
    double-sided, so two faces meeting at a vertex can point opposite
    ways and cancel. Each face's contribution is flipped to agree with
    the first one that reached the vertex, which keeps a seam smooth
    instead of leaving a black band along it."""
    normals = np.zeros(points.shape, dtype=np.float64)
    for tri in triangles:
        a, b, c = points[tri[0]], points[tri[1]], points[tri[2]]
        face = np.cross(b - a, c - a)
        size = np.linalg.norm(face)
        if size < 1e-12:
            continue
        face /= size
        for at in tri:
            if normals[at] @ face < 0 and normals[at].any():
                normals[at] -= face
            else:
                normals[at] += face
    lengths = np.linalg.norm(normals, axis=1)
    empty = lengths < 1e-9
    normals[empty] = (0.0, 0.0, 1.0)
    lengths[empty] = 1.0
    return (normals / lengths[:, None]).astype(np.float32)


def _triangles(face):
    """A face as triangles - glTF has no quad."""
    if len(face) == 3:
        return [tuple(face)]
    if len(face) == 4:
        return [(face[0], face[1], face[2]), (face[0], face[2], face[3])]
    return [(face[0], face[i], face[i + 1]) for i in range(1, len(face) - 1)]


def _bone_of(groups, vertex, joints, spares=None):
    """Which joint a vertex is bound to.

    Group i is bone i, but only as far as the skeleton goes: a model
    carries more groups than the animation moves - Tomba has 21 groups
    against 17 bones - and binding one of those spares to "bone 20" of
    a 17-joint skin is out of range. Blender does not check, it just
    indexes, and comes apart with `index 20 is out of bounds for axis 0
    with size 17`.

    A spare rides the bone belonging to the limb it stands in for when
    the caller knows which that is, and the root otherwise, so it moves
    with the model instead of being pinned at the origin."""
    for group in groups or ():
        if group.first_vertex <= vertex < group.first_vertex + group.vertex_count:
            if group.index < joints:
                return group.index
            stands_in_for = (spares or {}).get(group.index, 0)
            return stands_in_for if stands_in_for < joints else 0
    return 0


def build(model_data, vram_bytes, groups=None, bones=None, frames=None,
          fps=DEFAULT_FPS, name="model", spares=None, skip=None):
    """The glTF document and its binary blob, as (dict, bytearray).

    `spares` maps a group past the end of the skeleton to the limb it
    stands in for, and `skip` names groups to leave out entirely - the
    viewer hides a model's alternates, and an export that matches what
    is on screen beats one that ships two heads inside each other."""
    vertices = np.asarray(model_data.get("vertices") or (), dtype=np.float32)
    if not len(vertices):
        raise ValueError("model has no vertices")
    vertices = vertices / UNIT_SCALE
    colors = np.asarray(model_data.get("vertex_colors") or (), dtype=np.float32)
    uvs = np.asarray(model_data.get("texture_coords") or (), dtype=np.float32)
    faces = model_data.get("faces") or []
    info = model_data.get("texture_info") or []

    if not len(colors):
        colors = np.ones((len(vertices), 3), dtype=np.float32)
    # The PSX lets a vertex brighten as well as darken; glTF does not.
    colors = np.clip(colors, 0.0, 1.0)
    if not len(uvs):
        uvs = np.zeros((len(vertices), 2), dtype=np.float32)

    atlas = index_atlas(vram_bytes)

    # Faces first go to the palette they are read through: one material
    # per CLUT, since that is the only thing a baked texture can be.
    hidden = set(skip or ())
    dropped = set()
    for group in groups or ():
        if group.index in hidden:
            dropped.update(range(group.first_vertex,
                                 group.first_vertex + group.vertex_count))

    by_clut = {}
    for f, face in enumerate(faces):
        if dropped and any(v in dropped for v in face):
            continue
        _page, clut, transparent = (info[f] if f < len(info) else (0, 0, False))
        by_clut.setdefault((clut, bool(transparent)), []).extend(_triangles(face))

    buffer = _Buffer()
    primitives, materials, textures, images = [], [], [], []

    for (clut, transparent), tris in sorted(by_clut.items()):
        used = sorted({i for tri in tris for i in tri})
        if not used:
            continue
        remap = {old: new for new, old in enumerate(used)}
        part_uv = uvs[used]

        # Crop to what this material actually samples. The atlas is
        # 4096x512 and a character uses a corner of it; baking the whole
        # thing once per palette would be most of a hundred megabytes.
        px = np.clip(part_uv[:, 0] * ATLAS_WIDTH, 0, ATLAS_WIDTH)
        py = np.clip(part_uv[:, 1] * ATLAS_HEIGHT, 0, ATLAS_HEIGHT)
        x0 = max(0, int(np.floor(px.min())) - PAD)
        y0 = max(0, int(np.floor(py.min())) - PAD)
        x1 = min(ATLAS_WIDTH, int(np.ceil(px.max())) + PAD)
        y1 = min(ATLAS_HEIGHT, int(np.ceil(py.max())) + PAD)
        x1 = max(x1, x0 + 1)
        y1 = max(y1, y0 + 1)

        baked = palette(vram_bytes, clut, transparent)[atlas[y0:y1, x0:x1]]
        images.append({"mimeType": "image/png", "name": f"clut_{clut:X}",
                       "uri": "data:image/png;base64,"
                              + base64.b64encode(_png(baked)).decode("ascii")})
        textures.append({"sampler": 0, "source": len(images) - 1})

        materials.append({
            "name": f"clut_{clut:X}" + ("_alpha" if transparent else ""),
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": len(textures) - 1},
                "metallicFactor": 0.0, "roughnessFactor": 1.0,
            },
            "doubleSided": True,
            # Black is the transparent colour, not a dark shade, so it
            # is cut out rather than blended.
            "alphaMode": "BLEND" if transparent else "MASK",
            **({} if transparent else {"alphaCutoff": 0.5}),
        })

        local_uv = np.empty_like(part_uv)
        local_uv[:, 0] = (part_uv[:, 0] * ATLAS_WIDTH - x0) / (x1 - x0)
        local_uv[:, 1] = (part_uv[:, 1] * ATLAS_HEIGHT - y0) / (y1 - y0)

        local = np.ascontiguousarray(vertices[used])
        attributes = {
            "POSITION": buffer.add(local, "VEC3", FLOAT,
                                   ARRAY_BUFFER, minmax=True),
            "NORMAL": buffer.add(
                _normals(local, [[remap[i] for i in tri] for tri in tris]),
                "VEC3", FLOAT, ARRAY_BUFFER),
            "TEXCOORD_0": buffer.add(np.ascontiguousarray(local_uv),
                                     "VEC2", FLOAT, ARRAY_BUFFER),
            "COLOR_0": buffer.add(np.ascontiguousarray(colors[used]),
                                  "VEC3", FLOAT, ARRAY_BUFFER),
        }
        if groups:
            joints = np.zeros((len(used), 4), dtype=np.uint16)
            weights = np.zeros((len(used), 4), dtype=np.float32)
            joints[:, 0] = [_bone_of(groups, v, len(bones or ()) or len(groups),
                                     spares) for v in used]
            weights[:, 0] = 1.0
            # Nothing downstream checks this. Blender indexes its joint
            # matrices with whatever is here and comes apart with
            # "index 18 is out of bounds for axis 0 with size 18", a
            # long way from the mistake - so it is checked here, where
            # the mistake would be.
            limit = len(bones or ()) or len(groups)
            if joints.size and int(joints.max()) >= limit:
                raise ValueError(
                    f"joint index {int(joints.max())} in a skin of "
                    f"{limit} - a group past the end of the skeleton "
                    f"was not mapped back onto one")
            attributes["JOINTS_0"] = buffer.add(joints, "VEC4",
                                                UNSIGNED_SHORT, ARRAY_BUFFER)
            attributes["WEIGHTS_0"] = buffer.add(weights, "VEC4",
                                                 FLOAT, ARRAY_BUFFER)

        flat = np.array([remap[i] for tri in tris for i in tri],
                        dtype=np.uint32)
        primitives.append({
            "attributes": attributes,
            "indices": buffer.add(flat, "SCALAR", UNSIGNED_INT,
                                  ELEMENT_ARRAY_BUFFER),
            "material": len(materials) - 1,
            "mode": TRIANGLES,
        })

    gltf = {
        "asset": {"version": "2.0", "generator": "Tomba310"},
        "scene": 0,
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": materials,
        "textures": textures,
        "images": images,
        "samplers": [{"magFilter": NEAREST, "minFilter": NEAREST,
                      "wrapS": CLAMP_TO_EDGE, "wrapT": CLAMP_TO_EDGE}],
    }

    if bones:
        _rig(gltf, buffer, bones, frames, fps, name)
    else:
        gltf["nodes"] = [{"name": name, "mesh": 0}]
        gltf["scenes"] = [{"nodes": [0]}]

    gltf["bufferViews"] = buffer.views
    gltf["accessors"] = buffer.accessors
    gltf["buffers"] = [{"byteLength": len(buffer.data)}]
    return gltf, buffer.data


def _rig(gltf, buffer, bones, frames, fps, name):
    """Add the skeleton, the skin, and the animation if there is one.

    The bones are laid out exactly as game_rest.joints reads them - the
    game's (x, y, z) is the viewer's (z, -y, x) - and each node holds
    only its offset from its parent, which is what the table stores
    anyway."""
    from gui.anmp.skeleton import _euler_matrix

    nodes = [{"name": name, "mesh": 0, "skin": 0}]
    first_bone = len(nodes)
    children = {}
    for i, (parent, *_offset) in enumerate(bones):
        if parent >= 0:
            children.setdefault(parent, []).append(first_bone + i)

    for i, (parent, x, y, z) in enumerate(bones):
        # A root's own offset is not where it goes: skeleton.assemble
        # starts every root at the origin and ignores the three numbers
        # stored with it, so a character with a second root - Tomba's
        # pelvis at bone 8 - has both roots stacked at 0 and the pelvis
        # placed by the animation rather than by the table. Using the
        # stored offset here instead put his lower half 78 units away
        # from where the viewer has it.
        local = (z, -y, x) if parent >= 0 else (0.0, 0.0, 0.0)
        node = {"name": f"bone_{i}",
                "translation": [c / UNIT_SCALE for c in local]}
        if i in children:
            node["children"] = children[i]
        nodes.append(node)

    roots = [first_bone + i for i, b in enumerate(bones) if b[0] < 0]
    gltf["nodes"] = nodes
    # Inverse bind matrices are left undefined, which glTF reads as
    # identity - correct here, because an SMST group's vertices are
    # already in its own bone's space.
    gltf["skins"] = [{"joints": [first_bone + i for i in range(len(bones))],
                      "skeleton": roots[0] if roots else first_bone}]
    gltf["scenes"] = [{"nodes": [0] + roots}]

    if not frames:
        return

    times = np.arange(len(frames), dtype=np.float32) / float(fps or DEFAULT_FPS)
    time_accessor = buffer.add(times, "SCALAR", FLOAT, minmax=True)

    samplers, channels = [], []
    for i in range(len(bones)):
        turns = np.zeros((len(frames), 4), dtype=np.float32)
        for f, frame in enumerate(frames):
            rotations = frame.rotations()
            turns[f] = (_quaternion(_euler_matrix(*rotations[i]))
                        if i < len(rotations) else [0.0, 0.0, 0.0, 1.0])
        samplers.append({"input": time_accessor,
                         "output": buffer.add(turns, "VEC4", FLOAT),
                         "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": first_bone + i, "path": "rotation"}})

    # Where the roots actually go. pose_transforms puts a root at
    # `pivots[root] + translation`, and every root's pivot is the origin
    # (skeleton.assemble starts them all there), so the frame's
    # translation is the whole of it - already in the axes the pivots
    # are in, which is why it needs no swap here.
    #
    # It goes on EVERY root, not just the first. A character with a
    # separate pelvis root - Tomba - otherwise walks off leaving his
    # legs behind, which is a 16-unit split by frame 37.
    if any(getattr(f, "root", False) for f in frames):
        moves = np.zeros((len(frames), 3), dtype=np.float32)
        for f, frame in enumerate(frames):
            moves[f] = np.array(frame.translation(),
                                dtype=np.float32) / UNIT_SCALE
        samplers.append({"input": time_accessor,
                         "output": buffer.add(moves, "VEC3", FLOAT),
                         "interpolation": "LINEAR"})
        move_sampler = len(samplers) - 1
        for node in roots:
            channels.append({"sampler": move_sampler,
                             "target": {"node": node, "path": "translation"}})

    gltf["animations"] = [{"name": "take", "samplers": samplers,
                           "channels": channels}]


def write_glb(path, model_data, vram_bytes, groups=None, bones=None,
              frames=None, fps=DEFAULT_FPS, name="model", spares=None,
              skip=None):
    """Write a single-file .glb - one file with the textures inside it,
    which is what makes this drag-and-droppable into Blender."""
    gltf, blob = build(model_data, vram_bytes, groups, bones, frames, fps,
                       name, spares, skip)
    gltf["buffers"] = [{"byteLength": len(blob)}]

    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    blob = bytes(blob) + b"\x00" * (-len(blob) % 4)

    with open(path, "wb") as out:
        out.write(struct.pack("<III", 0x46546C67, 2,
                              12 + 8 + len(payload) + 8 + len(blob)))
        out.write(struct.pack("<II", len(payload), 0x4E4F534A))
        out.write(payload)
        out.write(struct.pack("<II", len(blob), 0x004E4942))
        out.write(blob)
    return True


def write_gltf(path, model_data, vram_bytes, groups=None, bones=None,
               frames=None, fps=DEFAULT_FPS, name="model", spares=None,
               skip=None):
    """Write a .gltf - JSON with the buffer and textures inlined, for
    when something downstream wants to read it as text."""
    gltf, blob = build(model_data, vram_bytes, groups, bones, frames, fps,
                       name, spares, skip)
    gltf["buffers"] = [{
        "byteLength": len(blob),
        "uri": "data:application/octet-stream;base64,"
               + base64.b64encode(bytes(blob)).decode("ascii"),
    }]
    with open(path, "w", encoding="utf-8") as out:
        json.dump(gltf, out, indent=2)
    return True

"""Reading a .glb / .gltf as the importer's faces.

WHY GLB AS WELL AS OBJ

Checked with Blender 5.2 on a scene imported from a PS1 tool's GLB, where
the meshes carry a colour attribute and the materials carry packed images:

    OBJ export, defaults       no vertex colours, no texture files
    OBJ, Colors ticked         colours, and still no textures: a packed
                               image has no file for the MTL to name
    GLB export, defaults       COLOR_0, every image embedded, object and
                               material names kept

So a model that brings its own art comes whole in a GLB and in pieces, or
not at all, in an OBJ. What a GLB cannot hold is a quad, and here a quad
is 52 bytes of the frame's primitive buffer where two triangles are 80,
so pairs of triangles are joined back up (pair_quads()).

A quad's ring (a, b, c, d) is drawn by the game as the triangles either
side of a-c. Two triangles are only ever joined along the edge they
share, with that edge put at a-c, so the picture is the same before and
after; it is the packet count that changes.

Positions come back as the file has them, Y up, untouched: the import
dialog places and scales. UVs are texels of a 256-wide page.
"""
import base64
import io
import json
import struct
from pathlib import Path

import numpy as np

from .obj_exchange import ExchangeError, Face, MAX_FACES, _material_name, shades

_COMPONENTS = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_JSON, _BIN = 0x4E4F534A, 0x004E4942


def _header(path):
    """A glTF's JSON alone - a GLB's images are not read for it."""
    path = Path(path)
    with path.open("rb") as stream:
        start = stream.read(20)
        if start[:4] != b"glTF":
            try:
                return json.loads(start + stream.read())
            except ValueError as exc:
                raise ExchangeError(f"{path.name} is not a glTF file.") from exc
        size, kind = struct.unpack_from("<II", start, 12)
        if kind != _JSON:
            raise ExchangeError(f"{path.name} is not a readable GLB.")
        return json.loads(stream.read(size))


def material_names(path):
    """{material name: has an image} without loading the file."""
    doc = _header(path)
    return {m.get("name", ""): "baseColorTexture" in m.get("pbrMetallicRoughness", {})
            for m in doc.get("materials", [])}


class Document:
    """A glTF's JSON and buffers, with accessors read as arrays."""

    def __init__(self, path):
        self.path = Path(path)
        data = self.path.read_bytes()
        self.buffers, self.json = [], None
        if data[:4] == b"glTF":
            at = 12
            while at + 8 <= len(data):
                size, kind = struct.unpack_from("<II", data, at)
                body = data[at + 8:at + 8 + size]
                at += 8 + size
                if kind == _JSON:
                    self.json = json.loads(body)
                elif kind == _BIN:
                    self.buffers.append(body)
            if self.json is None:
                raise ExchangeError(f"{self.path.name} is not a readable GLB.")
        else:
            try:
                self.json = json.loads(data)
            except ValueError as exc:
                raise ExchangeError(f"{self.path.name} is not a glTF file.") from exc
        for n, buffer in enumerate(self.json.get("buffers", [])):
            if "uri" in buffer:
                self.buffers.insert(n, self._uri(buffer["uri"]))
        if any("KHR_draco_mesh_compression" in p.get("extensions", {})
               for m in self.json.get("meshes", []) for p in m.get("primitives", [])):
            raise ExchangeError("Draco-compressed glTF is not supported. Export without compression.")

    def _uri(self, uri):
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        from urllib.parse import unquote
        target = self.path.parent / unquote(uri)
        if not target.is_file():
            raise ExchangeError(f"{self.path.name} refers to {uri}, which is not beside it.")
        return target.read_bytes()

    def view(self, index):
        view = self.json["bufferViews"][index]
        start = view.get("byteOffset", 0)
        return self.buffers[view.get("buffer", 0)][start:start + view["byteLength"]], view.get("byteStride")

    def accessor(self, index):
        """An accessor as an array; normalised integers come back 0..1."""
        a = self.json["accessors"][index]
        if "sparse" in a or "bufferView" not in a:
            raise ExchangeError("Sparse glTF accessors are not supported.")
        kind = np.dtype(_COMPONENTS[a["componentType"]])
        width = _WIDTHS[a["type"]]
        raw, stride = self.view(a["bufferView"])
        offset, count = a.get("byteOffset", 0), a["count"]
        if stride and stride != kind.itemsize * width:
            array = np.array([np.frombuffer(raw, kind, width, offset + i * stride) for i in range(count)])
        else:
            array = np.frombuffer(raw, kind, count * width, offset).reshape(count, width)
        if a.get("normalized") and kind.kind in "ui":
            array = array.astype(np.float64) / np.iinfo(kind).max
        return array

    def image(self, index):
        from PIL import Image
        entry = self.json["images"][index]
        if "bufferView" in entry:
            raw = self.view(entry["bufferView"])[0]
        elif "uri" in entry:
            raw = self._uri(entry["uri"])
        else:
            return None
        picture = Image.open(io.BytesIO(raw))
        picture.load()
        return picture


def materials(path):
    """{material name: {"color": rgb, "image": PIL image or None}}."""
    doc = Document(path)
    out, pictures = {}, {}
    for material in doc.json.get("materials", []):
        pbr = material.get("pbrMetallicRoughness", {})
        image = None
        texture = pbr.get("baseColorTexture")
        if texture is not None:
            source = doc.json["textures"][texture["index"]].get("source")
            if source is not None:
                if source not in pictures:
                    pictures[source] = doc.image(source)
                image = pictures[source]
        # "tint": a glTF's factor multiplies its image; an MTL's Kd does not.
        out.setdefault(material.get("name", ""), {
            "color": tuple(float(v) for v in pbr.get("baseColorFactor", (1, 1, 1, 1))[:3]),
            "image": image, "tint": True})
    return out


def _matrix(node):
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T
    m = np.eye(4)
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    rotation = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m[:3, :3] = rotation * np.array(node.get("scale", (1, 1, 1)), float)
    m[:3, 3] = node.get("translation", (0, 0, 0))
    return m


def read_gltf(path, colors=None, pair=True):
    """[Face] with the file's own positions. `colors` turns a COLOR_0
    value into what a face keeps; see obj_exchange.shades()."""
    doc = Document(path)
    colors = colors or shades(False, True)
    # Spelt as Blender's OBJ writer spells them, so one model has one set
    # of names whichever way it was exported.
    names = [_material_name(m.get("name", "").strip().replace(" ", "_"))
             for m in doc.json.get("materials", [])]
    faces = []

    def walk(index, parent):
        node = doc.json["nodes"][index]
        world = parent @ _matrix(node)
        if "mesh" in node:
            mesh = doc.json["meshes"][node["mesh"]]
            label = node.get("name") or mesh.get("name") or f"Object {index}"
            for primitive in mesh.get("primitives", []):
                if primitive.get("mode", 4) != 4:
                    continue
                attributes = primitive["attributes"]
                position = doc.accessor(attributes["POSITION"]).astype(float)
                position = position @ world[:3, :3].T + world[:3, 3]
                uv = doc.accessor(attributes["TEXCOORD_0"]) if "TEXCOORD_0" in attributes else None
                colour = doc.accessor(attributes["COLOR_0"])[:, :3] if "COLOR_0" in attributes else None
                order = (doc.accessor(primitive["indices"]).reshape(-1) if "indices" in primitive
                         else np.arange(len(position)))
                material = names[primitive["material"]] if "material" in primitive else ""
                if uv is not None and ((uv < -1e-6) | (uv > 1 + 1e-6)).any():
                    raise ExchangeError(
                        f'Object "{label}", material "{material}": UVs leave the 0..1 square. '
                        "A PS1 face cannot repeat a texture; keep each face inside the image.")
                points = [tuple(float(v) for v in p) for p in position]
                texels = ([(min(255, max(0, round(float(u) * 256 - .5))),
                            min(255, max(0, round(float(v) * 256 - .5)))) for u, v in uv]
                          if uv is not None else None)
                tints = [colors(tuple(float(v) for v in c)) for c in colour] if colour is not None else None
                flip = np.linalg.det(world[:3, :3]) < 0
                for a, b, c in order[:len(order) // 3 * 3].reshape(-1, 3):
                    ring = (a, c, b) if flip else (a, b, c)
                    faces.append(Face(tuple(points[i] for i in ring),
                                      tuple(texels[i] if texels else None for i in ring),
                                      tuple(tints[i] if tints else None for i in ring),
                                      material, label))
                if len(faces) > 2 * MAX_FACES:
                    raise ExchangeError(f"More than {2 * MAX_FACES} triangles; simplify the model first.")
        for child in node.get("children", []):
            walk(child, world)

    scenes = doc.json.get("scenes") or [{"nodes": list(range(len(doc.json.get("nodes", []))))}]
    for root in scenes[doc.json.get("scene", 0)].get("nodes", []):
        walk(root, np.eye(4))
    if not faces:
        raise ExchangeError(f"{Path(path).name} holds no triangles.")
    if pair:
        faces = pair_quads(faces)
    if len(faces) > MAX_FACES:
        raise ExchangeError(f"More than {MAX_FACES} polygons; simplify the model first.")
    return faces


def _normal(points):
    a, b, c = (np.array(p) for p in points[:3])
    n = np.cross(b - a, c - a)
    length = np.linalg.norm(n)
    return n / length if length else n


def pair_quads(faces, flatness=0.95):
    """Join triangles into quads where two share an edge and agree there.

    They must be of one object and material and carry the same UV and
    colour at both shared corners, so the joined quad draws exactly what
    the two did. Of the pairs that could be made, the flattest whose UVs
    make a parallelogram go first - that is how a quad cut in two looks."""
    edges = {}
    for n, face in enumerate(faces):
        if len(face.vertices) != 3:
            continue
        corners = list(zip(face.vertices, face.uvs, face.colors))
        for i in range(3):
            edges.setdefault((face.object, face.material, corners[i], corners[(i + 1) % 3]), []).append((n, i))
    normals = {}

    def normal(n):
        if n not in normals:
            normals[n] = _normal(faces[n].vertices)
        return normals[n]

    candidates = []
    for (obj, material, a, b), users in edges.items():
        for m, j in edges.get((obj, material, b, a), ()):
            for n, i in users:
                if m <= n:
                    continue
                flat = float(np.dot(normal(n), normal(m)))
                if flat < flatness:
                    continue
                p, q = faces[n].uvs[(i + 2) % 3], faces[m].uvs[(j + 2) % 3]
                square = (None not in (p, q, a[1], b[1])
                          and abs(p[0] + q[0] - a[1][0] - b[1][0]) <= 2
                          and abs(p[1] + q[1] - a[1][1] - b[1][1]) <= 2)
                candidates.append((-(flat + square), n, m, i, j))
    candidates.sort()
    used, joined = set(), {}
    for _score, n, m, i, j in candidates:
        if n in used or m in used:
            continue
        used.update((n, m))
        first, second = faces[n], faces[m]
        # first is (a, b, apex), second (b, a, apex'): the ring a, apex', b,
        # apex has the shared edge where the game cuts a quad.
        ring = [(first, i), (second, (j + 2) % 3), (first, (i + 1) % 3), (first, (i + 2) % 3)]
        joined[n] = Face(tuple(f.vertices[k] for f, k in ring), tuple(f.uvs[k] for f, k in ring),
                         tuple(f.colors[k] for f, k in ring), first.material, first.object)
    return [joined.get(n, face) for n, face in enumerate(faces) if n in joined or n not in used]

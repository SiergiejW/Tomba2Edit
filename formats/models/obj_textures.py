"""A foreign model's materials into source packets and a staging VRAM.

WHERE ITS TEXTURES CAN COME FROM (Source)

    images    a picture a material: the files an OBJ's MTL names, or the
              ones embedded in a .glb
    vram      a raw dump of the VRAM of the game the model came from.
              Materials named Page_XXXX_CLUT_XXXX read their texels and
              palette straight out of it
    flat      nothing: each material is its colour

An OBJ that Blender writes from packed images names none of them (see
gltf_import), and that used to import as a grey model without a word. So
the source is a choice: found where it can be (discover()), picked by
the user where it cannot (open_source()), and never assumed.

THE SAME PAGE BACK FROM ITS PICTURES

A PS1 tool's picture of "page 17 through CLUT 79D2" is 256 x 256 in at
most 16 colours, and several materials are one page through different
CLUTs. Reducing each picture on its own would store that page once a
palette. Instead the pictures of one page are read together: texels that
agree in every picture are one index. For a true 4-bit page that never
needs more than 16, and gives the page as it was - one copy, a palette a
material. A picture that will not go into 16 colours (edited, or 8-bit)
is reduced to them on its own.

This does not install anything. Destination allocation is a separate,
validated step so cancelling an import cannot change the project.
"""
from dataclasses import dataclass, replace
from pathlib import Path
import re
import struct

from .obj_exchange import ExchangeError, _body, _encode, _srgb

PS1_MATERIAL = re.compile(r'Page_(-001|[0-9a-fA-F]{4})_CLUT_([0-9a-fA-F]{4})(?:\.\d+)?$')
VRAM_BYTES = 1048576
# Staging pages: sheets, then one of flat colours, then the palettes.
SHEET_PAGES, FLAT_PAGE, PALETTE_PAGE = 30, 30, 31
WHITE = (1., 1., 1.)


def spelt(name):
    """A material name as Blender's OBJ writer spells it."""
    return (name or '').strip().replace(' ', '_')


def _base(name):
    """...and without the .001 Blender gives a second copy."""
    name = spelt(name)
    return name[:-4] if len(name) > 4 and name[-4] == '.' and name[-3:].isdigit() else name


def _index(info):
    """`info` under every spelling, the exact one winning."""
    out = dict(info)
    for form in (spelt, _base):
        for name, entry in info.items():
            out.setdefault(form(name), entry)
    return out


def _lookup(index, name):
    for form in (name, spelt(name), _base(name)):
        if form in index:
            return index[form]
    return None


def untextured(name):
    """A PS1 tool's name for a face that has no texture."""
    match = PS1_MATERIAL.fullmatch(spelt(name))
    return bool(match) and match[1] == '-001'


def materials(obj_path, missing_ok=False):
    obj_path = Path(obj_path)
    result = {}
    for line in obj_path.read_text(encoding='utf-8-sig').splitlines():
        if not line.startswith('mtllib '):
            continue
        path = obj_path.parent / line[7:].strip().strip('"')
        if not path.is_file():
            if missing_ok:
                continue
            raise ExchangeError(f'Material file is missing: {path.name}. Keep the OBJ and MTL together.')
        result.update(read_mtl(path))
    return result


def read_mtl(path):
    path = Path(path)
    result, current = {}, None
    for row in path.read_text(encoding='utf-8-sig').splitlines():
        fields = row.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        key, value = fields
        if key == 'newmtl':
            current = result.setdefault(value, {'color': WHITE})
        elif current is not None and key == 'Kd':
            try:
                rgb = tuple(float(v) for v in value.split())
            except ValueError as exc:
                raise ExchangeError(f'Invalid diffuse colour in {path.name}.') from exc
            if len(rgb) != 3 or any(not 0 <= v <= 1 for v in rgb):
                raise ExchangeError(f'Diffuse colours in {path.name} must be RGB in 0..1.')
            current['color'] = rgb
        elif current is not None and key == 'map_Kd':
            if value.startswith('-'):
                raise ExchangeError('MTL texture transform options are unsupported. Apply the UV transform in Blender before exporting.')
            current['image'] = path.parent / value.strip('"')
    return result


@dataclass
class Source:
    kind: str               # 'images', 'vram' or 'flat'
    label: str = ''         # the file, for the user
    images: dict = None     # {material: {'color': rgb, 'image': picture or path}}
    vram: bytes = None
    origin: str = ''        # 'model', 'beside' or 'chosen'

    def textured(self, names):
        """Those of `names` this has a texture for."""
        if self.kind == 'vram':
            return {n for n in names if PS1_MATERIAL.fullmatch(spelt(n)) and not untextured(n)}
        if self.kind != 'images':
            return set()
        index = _index(self.images)
        found = set()
        for name in names:
            image = (_lookup(index, name) or {}).get('image')
            if image is not None and (not isinstance(image, Path) or image.is_file()):
                found.add(name)
        return found


def own_images(model_path):
    """What the model's own file says of its materials."""
    path = Path(model_path)
    if path.suffix.lower() in ('.glb', '.gltf'):
        from .gltf_import import materials as embedded
        return embedded(path)
    return materials(path, missing_ok=True)


def discover(model_path, names):
    """The best Source to be had without asking.

    The model's own images first. Where they do not cover it: a .vram of
    the model's name, then whichever .glb beside it has images under the
    most of these material names."""
    from .gltf_import import material_names, materials as embedded
    path = Path(model_path)
    wanted = {n for n in names if not untextured(n)}
    own = Source('images', path.name, images=own_images(path), origin='model')
    have = own.textured(wanted)
    if len(have) == len(wanted):
        return own
    dump = path.with_suffix('.vram')
    if (dump.is_file() and dump.stat().st_size == VRAM_BYTES
            and all(PS1_MATERIAL.fullmatch(spelt(n)) for n in names)):
        return Source('vram', dump.name, vram=dump.read_bytes(), origin='beside')
    best = None
    try:
        beside = [p for p in path.parent.iterdir() if p.suffix.lower() in ('.glb', '.gltf') and p != path]
    except OSError:
        beside = []
    for other in sorted(beside)[:200]:
        try:
            pictured = _index({n: True for n, image in material_names(other).items() if image})
            score = sum(1 for n in wanted - have if _lookup(pictured, n))
            rank = (score, other.stem == path.stem, other.stat().st_mtime)
        except (ValueError, OSError, KeyError, struct.error):
            continue
        if score and (best is None or rank > best[0]):
            best = rank, other
    if best is None:
        return own
    try:
        return merged(own, Source('images', best[1].name, images=embedded(best[1]), origin='beside'))
    except (ValueError, OSError, KeyError, struct.error):
        return own


def merged(own, other):
    """`other`, except where the model's own file already has the picture."""
    if other.kind != 'images' or not own.images:
        return other
    images = dict(other.images)
    index = _index(other.images)
    for name, entry in own.images.items():
        if entry.get('image') is not None or _lookup(index, name) is None:
            images[name] = entry
    return replace(other, images=images)


def open_source(path, kind):
    """A file the user picked, as the `kind` of source they said it is."""
    path = Path(path)
    suffix = path.suffix.lower()
    if kind == 'vram':
        if path.stat().st_size != VRAM_BYTES:
            raise ExchangeError(f'{path.name} is not a raw VRAM dump: that is 1024 × 512 16-bit words, '
                                f'1,048,576 bytes, and this is {path.stat().st_size:,}.')
        return Source('vram', path.name, vram=path.read_bytes(), origin='chosen')
    if suffix in ('.glb', '.gltf'):
        from .gltf_import import materials as embedded
        images = embedded(path)
    elif suffix == '.mtl':
        images = read_mtl(path)
    elif suffix == '.obj':
        images = materials(path)
    else:
        raise ExchangeError('Choose a .glb or .gltf with the images inside it, or an .mtl that names image files.')
    return Source('images', path.name, images=images, origin='chosen')


# --- pictures into 4-bit sheets ----------------------------------------

def _words(rgba):
    """RGBA bytes as PS1 colours. 0 is the transparent one, so black is
    the same black with its top bit set."""
    import numpy as np
    r, g, b = (rgba[..., c].astype(np.uint16) >> 3 for c in range(3))
    words = r | g << 5 | b << 10
    words[words == 0] = 0x8000
    words[rgba[..., 3] < 128] = 0
    return words


def _shared(pictures):
    """(indices, a palette a picture) for pictures of one page, or None
    where they do not agree on 16 indices."""
    import numpy as np
    stack = np.stack([_words(p) for p in pictures], axis=-1).reshape(-1, len(pictures))
    found, inverse = np.unique(stack, axis=0, return_inverse=True)
    if len(found) > 16:
        return None
    palettes = np.zeros((len(pictures), 16), np.uint16)
    palettes[:, :len(found)] = found.T
    return inverse.reshape(pictures[0].shape[:2]).astype(np.uint16), palettes


def _reduced(rgba):
    """(indices, palette, exact) for one picture."""
    import numpy as np
    from PIL import Image
    shared = _shared([rgba])
    if shared:
        return shared[0], shared[1][0], True
    opaque = rgba[..., 3] >= 128
    count = 16 if opaque.all() else 15
    quant = Image.fromarray(rgba[..., :3]).quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    indices = np.array(quant, dtype=np.uint16) + (16 - count)
    indices[~opaque] = 0
    entries = quant.getpalette()[:count * 3]
    colours = np.array(entries + [0] * (count * 3 - len(entries)), dtype=np.uint16).reshape(count, 3) >> 3
    words = colours[:, 0] | colours[:, 1] << 5 | colours[:, 2] << 10
    words[words == 0] = 0x8000
    palette = np.zeros(16, np.uint16)
    palette[16 - count:] = words
    return indices, palette, False


def _picture(name, entry):
    """A material's picture as RGBA bytes, or None."""
    import numpy as np
    from PIL import Image
    image = entry.get('image')
    if image is None:
        return None
    if isinstance(image, Path):
        if not image.is_file():
            raise ExchangeError(f'Texture for "{name}" is missing: {image}. Copy the image with the OBJ/MTL or correct its path.')
        with Image.open(image) as opened:
            return np.array(opened.convert('RGBA'))
    return np.array(image.convert('RGBA'))


def _fitted(rgba, tile):
    """...no wider or taller than `tile`, and whole halfwords across."""
    import numpy as np
    from PIL import Image
    height, width = rgba.shape[:2]
    size = ((min(width, tile) + 3) // 4 * 4, min(height, tile))
    if size == (width, height):
        return rgba, False
    return np.array(Image.fromarray(rgba).resize(size, Image.Resampling.LANCZOS)), True


def _from_images(faces, images):
    import numpy as np
    index = _index(images)
    names = sorted({f.material for f in faces})
    entries = {n: _lookup(index, n) or {'color': WHITE} for n in names}
    pictures = {n: _picture(n, entries[n]) for n in names}
    summary = {'copied': 0, 'reduced': 0, 'flat': 0, 'resized': 0, 'tile': 256}

    def tint(name):
        # An MTL's Kd beside a map_Kd is Blender's unconnected default,
        # not a tint. A glTF's factor is one.
        entry = entries[name]
        return tuple(entry['color']) if entry.get('tint') else WHITE

    vram = np.zeros((512, 1024), np.uint16)
    palettes, placed = [], {}       # placed: name -> (page, palette, u, v, width, height)

    def palette(words):
        n = len(palettes)
        if n >= 1024:
            raise ExchangeError('More than 1,024 palettes: combine materials in Blender first.')
        palettes.append(words)
        x, y = 960 + n % 4 * 16, 256 + n // 4
        vram[y, x:x + 16] = words
        return y * 64 + x // 16

    def sheet(page, u, v, indices):
        height, width = indices.shape
        x, y = page % 16 * 64 + u // 4, page // 16 * 256 + v
        vram[y:y + height, x:x + width // 4] = (indices[:, 0::4] | indices[:, 1::4] << 4
                                               | indices[:, 2::4] << 8 | indices[:, 3::4] << 12)

    # One page through several CLUTs, put back as one page.
    pages, groups = 0, {}
    for name in names:
        match = PS1_MATERIAL.fullmatch(spelt(name))
        picture = pictures[name]
        if (match and match[1] != '-001' and picture is not None
                and picture.shape[:2] == (256, 256) and tint(name) == WHITE):
            page = int(match[1], 16)
            groups.setdefault((page & 31, page >> 7 & 3), []).append(name)
    for members in groups.values():
        shared = _shared([pictures[n] for n in members]) if pages < SHEET_PAGES else None
        if shared is None:
            continue
        sheet(pages, 0, 0, shared[0])
        for name, words in zip(members, shared[1]):
            placed[name] = (pages, palette(words), 0, 0, 256, 256)
        summary['copied'] += len(members)
        pages += 1

    # Every other picture on its own, the same picture once.
    alone = {}
    for name in names:
        if name in placed or pictures[name] is None:
            continue
        alone.setdefault((id(entries[name].get('image')) if not isinstance(entries[name].get('image'), Path)
                          else entries[name]['image'], tint(name)), []).append(name)
    left = SHEET_PAGES - pages
    tile = next((t for t in (256, 128, 64) if len(alone) <= left * (256 // t) ** 2), None)
    if tile is None:
        raise ExchangeError(f'{len(alone)} separate texture images are more than can be staged: '
                            'combine them into atlases in Blender first.')
    summary['tile'] = tile
    across = 256 // tile
    for slot, members in enumerate(alone.values()):
        rgba, resized = _fitted(pictures[members[0]], tile)
        colour = tint(members[0])
        if colour != WHITE:
            rgba = rgba.copy()
            rgba[..., :3] = (rgba[..., :3] * np.array([_srgb(c) for c in colour])).clip(0, 255)
        indices, words, exact = _reduced(rgba)
        page, cell = pages + slot // across ** 2, slot % across ** 2
        u, v = cell % across * tile, cell // across * tile
        sheet(page, u, v, indices)
        clut = palette(words)
        for name in members:
            placed[name] = (page, clut, u, v, rgba.shape[1], rgba.shape[0])
        summary['copied' if exact and not resized else 'reduced'] += len(members)
        summary['resized'] += len(members) * resized

    # Plain colours: a halfword each, fifteen to a palette.
    colours = sorted({tuple(entries[n]['color']) for n in names if n not in placed})
    for start in range(0, len(colours), 15):
        words = np.zeros(16, np.uint16)
        for i, colour in enumerate(colours[start:start + 15], 1):
            r, g, b = (min(31, round(_srgb(c) * 255) >> 3) for c in colour)
            words[i] = (r | g << 5 | b << 10) or 0x8000
        clut = palette(words)
        for i, colour in enumerate(colours[start:start + 15], 1):
            n = start + i - 1
            if n >= 64 * 256:
                raise ExchangeError('Too many plain-colour materials.')
            vram[256 + n // 64, FLAT_PAGE % 16 * 64 + n % 64] = 0x1111 * i
            for name in names:
                if name not in placed and tuple(entries[name]['color']) == colour:
                    placed[name] = (FLAT_PAGE, clut, n % 64 * 4, n // 64, 0, 0)
                    summary['flat'] += 1

    converted = []
    for face in faces:
        page, clut, u, v, width, height = placed[face.material]
        if width and any(uv is None for uv in face.uvs):
            raise ExchangeError(f'Object "{face.object}" needs UVs for material "{face.material}". Unwrap it in Blender.')
        uvs = (tuple((u + int(p[0] * width // 256), v + int(p[1] * height // 256)) for p in face.uvs)
               if width else ((u, v),) * len(face.vertices))
        code = 0x34 if len(face.vertices) == 3 else 0x3c
        converted.append(replace(face, uvs=uvs, material=f'T2_{code:02X}_00_{clut:04X}_{page:04X}'))
    return converted, vram.astype('<u2').tobytes(), summary


def _from_vram(faces, source_vram):
    vram = bytearray(source_vram)
    if len(vram) != VRAM_BYTES:
        raise ExchangeError('Source VRAM must be a raw 1024 × 512 image of 16-bit words (1,048,576 bytes).')
    parsed = {f.material: PS1_MATERIAL.fullmatch(spelt(f.material)) for f in faces}
    bad = next((name for name, match in parsed.items() if match is None), None)
    if bad is not None:
        raise ExchangeError(f'Material "{bad}" does not name a PS1 page/CLUT, so a VRAM dump cannot texture it. '
                            'Use images for this model instead.')
    used = {int(m[1], 16) & 31 for m in parsed.values() if m[1] != '-001'}
    white = next((p for p in range(32) if p not in used), None)
    if white is None and any(m[1] == '-001' for m in parsed.values()):
        raise ExchangeError('No source page remains for untextured faces.')
    if white is not None:
        x, y = white % 16 * 64, white // 16 * 256
        struct.pack_into('<H', vram, y * 2048 + x * 2, 0x1111)
        vram[(y + 255) * 2048 + x * 2:(y + 255) * 2048 + x * 2 + 32] = struct.pack('<16H', 0, 0x7fff, *([0] * 14))
        white_clut = (y + 255) * 64 + x // 16
    converted = []
    for face in faces:
        m = parsed[face.material]
        textured = m[1] != '-001'
        if textured and any(uv is None for uv in face.uvs):
            raise ExchangeError(f'Object "{face.object}" needs UVs for material "{face.material}". Unwrap it in Blender.')
        page, clut = (int(m[1], 16) & 31, int(m[2], 16)) if textured else (white, white_clut)
        code = 0x34 if len(face.vertices) == 3 else 0x3c
        converted.append(replace(face, material=f'T2_{code:02X}_00_{clut:04X}_{page:04X}',
                                 uvs=face.uvs if textured else ((0, 0),) * len(face.vertices)))
    names = set(parsed)
    flat = sum(untextured(n) for n in names)
    return converted, bytes(vram), {'copied': len(names) - flat, 'reduced': 0, 'flat': flat,
                                    'resized': 0, 'tile': 256}


def build(faces, source):
    """(SMST-shaped packets, their 4-bit staging VRAM, summary, order).

    `order[i]` is the face that record i was made from: a group keeps its
    triangles before its quads."""
    faces = list(faces)
    if source.kind == 'vram':
        converted, vram, summary = _from_vram(faces, source.vram)
    else:
        images = source.images or {}
        if source.kind == 'flat':
            images = {name: {'color': entry['color']} for name, entry in images.items()}
        converted, vram, summary = _from_images(faces, images)
    order = sorted(range(len(faces)), key=lambda i: len(faces[i].vertices))
    packets = [_encode(f, {}) for f in converted]
    return struct.pack('<HHI', 0, 1, 8) + _body(packets, b'\0' * 16), vram, summary, order


def packets(faces):
    """Faces that keep the target's materials, as an SMST-shaped run -
    for measuring the texture space they still read. None if none."""
    made = []
    for face in faces:
        try:
            made.append(_encode(face, {}))
        except ExchangeError:
            continue
    return struct.pack('<HHI', 0, 1, 8) + _body(made, b'\0' * 16) if made else None


def source_packets(faces, obj_path, source_vram=None):
    """Return an SMST-shaped source stream and its own 4bpp texture VRAM."""
    source = (Source('vram', vram=source_vram) if source_vram is not None
              else Source('images', images=own_images(obj_path)))
    return build(faces, source)[:2]

"""A sprite, ripped: its frames as the pixels VRAM holds, transparency kept.

Two kinds of thing in a level are sprites rather than geometry:

  CAPTURED SPRITE QUADS  what an effect's draw routine lays out in screen
            space round its projected point (f_DrawProjectedSpriteDefinition
            Stream) - a Seed of Strength, a mine bubble. actor_sim captures
            them as quads facing the camera, a world unit a pixel. Each frame's
            quads are cut out of VRAM at their own UVs, through their own CLUT,
            and put back where they sat, one texel a pixel.

  BILLBOARDS  a pickup's or a sprite object's frames out of a sprite bank,
            already cut into the level viewer's atlas (gui/level/
            pickup_sprites.py) with their origins - the crabs, the crystals.

Either way the result is [(RGBA image, milliseconds)] on one canvas, frames
aligned the way the game aligns them.
"""
import numpy as np
from PIL import Image

from psx import vram as psx_vram

PAGE = 256
# A quad is flat to the camera when its corners' depths agree this closely.
FLAT = 1.0


def _texels(vram, page_word, u0, v0, width, height):
    """Palette indices for a width x height patch of a texture page, 4bpp or
    8bpp by the page word's colour depth bits."""
    depth = (page_word >> 7) & 3
    byte_x, row0 = psx_vram.page_origin(page_word)
    raw = np.frombuffer(bytes(vram), dtype=np.uint8).reshape(
        psx_vram.VRAM_ROWS, psx_vram.VRAM_STRIDE)
    rows = (row0 + (np.arange(v0, v0 + height) % PAGE)) % psx_vram.VRAM_ROWS
    us = np.arange(u0, u0 + width) % PAGE
    if depth == 1:
        columns = (byte_x + us) % psx_vram.VRAM_STRIDE
        return raw[np.ix_(rows, columns)], 256
    columns = (byte_x + us // 2) % psx_vram.VRAM_STRIDE
    packed = raw[np.ix_(rows, columns)]
    return np.where(us % 2 == 0, packed & 0x0F, packed >> 4), 16


def _patch(vram, poly):
    """(RGBA array, x0, y0, x1, y1, units a texel) for one screen-facing quad,
    or None if it is not one."""
    corners, uvs, colours, clut, _blended, page = poly
    if len(corners) != 4:
        return None
    points = np.asarray(corners, dtype=np.float64)
    if np.ptp(points[:, 2]) > FLAT:
        return None
    xs, ys = points[:, 0], points[:, 1]
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    # An axis-aligned rectangle: every corner on its edges.
    if not all((abs(x - x0) < FLAT or abs(x - x1) < FLAT) and
               (abs(y - y0) < FLAT or abs(y - y1) < FLAT) for x, y in zip(xs, ys)):
        return None
    us = [u for u, _v in uvs]
    vs = [v for _u, v in uvs]
    width, height = max(us) - min(us), max(vs) - min(vs)
    if width <= 0 or height <= 0 or x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    indices, count = _texels(vram, page, min(us), min(vs), width, height)
    palette = np.array(psx_vram.read_palette(vram, psx_vram.clut_address(clut), count,
                                             transparent_zero=True, stp=True),
                       dtype=np.float64)
    rgba = palette[indices]
    tint = np.mean(np.asarray(colours, dtype=np.float64), axis=0)
    rgba[..., :3] = np.clip(rgba[..., :3] * tint, 0, 255)
    # Mirrored when the texture runs against the screen.
    left = int(np.argmin(xs))
    right = int(np.argmax(xs))
    top = int(np.argmin(ys))
    bottom = int(np.argmax(ys))
    if uvs[left][0] > uvs[right][0]:
        rgba = rgba[:, ::-1]
    if uvs[top][1] > uvs[bottom][1]:
        rgba = rgba[::-1]
    return rgba.astype(np.uint8), x0, y0, x1, y1, (x1 - x0) / width


def _centre(poly):
    points = np.asarray(poly[0], dtype=np.float64)
    return points.mean(axis=0), float(np.ptp(points[:, 0]) + np.ptp(points[:, 1]))


def _kind(poly):
    """What sprite a quad shows: its palette and the size of its patch."""
    us = [u for u, _v in poly[1]]
    vs = [v for _u, v in poly[1]]
    return poly[3], max(us) - min(us), max(vs) - min(vs)


def _track(frames, start, poly):
    """[[polygon], ...] following `poly` on from frame `start` until it is
    gone - nothing near where it was - or the clip comes round."""
    count = len(frames)
    here, size = _centre(poly)
    out = [[poly]]
    for step in range(1, count):
        polys = frames[(start + step) % count]
        if not polys:
            break
        best = min(polys, key=lambda p: np.linalg.norm(_centre(p)[0] - here))
        where, extent = _centre(best)
        # Further than it could have moved in a frame: another one.
        if np.linalg.norm(where - here) > max(size, extent):
            break
        out.append([best])
        here, size = where, extent
    return out


def follow_one(frames):
    """One sprite out of a clip of several - a single Seed of Strength, one
    mine bubble - followed frame to frame by where it is. Of every start, the
    one seen longest, the commonest kind of sprite breaking a tie (five seeds
    beat the trolley's two sparks). [[polygon], ...] a frame, or None when
    no frame holds more than one sprite."""
    if not frames or max(len(f) for f in frames) < 2:
        return None
    kinds = {}
    for polys in frames:
        for p in polys:
            kinds[_kind(p)] = kinds.get(_kind(p), 0) + 1
    best = None
    for start, polys in enumerate(frames):
        for poly in polys:
            track = _track(frames, start, poly)
            score = (len(track), kinds[_kind(poly)], _centre(poly)[1])
            if best is None or score > best[0]:
                best = (score, track)
    track = best[1]
    return track if len(track) > 1 else None


def rip_cards(frames, vram):
    """Captured projected polygons of any flat shape - A01's steam puff is
    a sheared quad - as [[(RGBA patch, centre, offsets, uvs)], ...] a frame:
    the texels under each polygon's UV box, where it stands (its own centre,
    capture coordinates), its corners about that centre (world units, x
    right, y up) and each corner's place in the patch (0..1). Each card turns
    to the camera about itself: one pivot for a whole clip moved eight
    vents' steam about as the camera went round. None if nothing to cut."""
    if not vram or not frames:
        return None
    if not any(polys for polys in frames):
        return None
    out = []
    for polys in frames:
        # Pieces that share a corner are one drawing - A01's two steam puffs
        # meet on an edge - and turn about one centre; turned apart, a crack
        # opened between them.
        middles = _shared_centres(polys)
        cards = []
        for number, (corners, uvs, colours, clut, _blended, page) in enumerate(polys):
            if len(corners) not in (3, 4):
                return None
            us = [u for u, _v in uvs]
            vs = [v for _u, v in uvs]
            u0, v0 = min(us), min(vs)
            width, height = max(1, max(us) - u0), max(1, max(vs) - v0)
            indices, count = _texels(vram, page, u0, v0, width, height)
            palette = np.array(psx_vram.read_palette(
                vram, psx_vram.clut_address(clut), count, transparent_zero=True, stp=True),
                dtype=np.float64)
            rgba = palette[indices]
            tint = np.mean(np.asarray(colours, dtype=np.float64), axis=0)
            rgba[..., :3] = np.clip(rgba[..., :3] * tint, 0, 255)
            centre = middles[number]
            offsets = tuple((float(x - centre[0]), float(centre[1] - y))
                            for x, y, _z in corners)
            fractions = tuple(((u - u0) / width, (v - v0) / height) for u, v in uvs)
            cards.append((rgba.astype(np.uint8), centre, offsets, fractions))
        out.append(cards)
    return out


def _shared_centres(polys):
    """Per polygon, the centre of the group of polygons it shares a corner
    with, directly or through others."""
    parent = list(range(len(polys)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for number, poly in enumerate(polys):
        for corner in poly[0]:
            key = tuple(int(round(c)) for c in corner)
            if key in owner:
                parent[root(number)] = root(owner[key])
            else:
                owner[key] = number
    groups = {}
    for number, poly in enumerate(polys):
        groups.setdefault(root(number), []).extend(poly[0])
    centres = {r: np.asarray(points, dtype=np.float64).mean(axis=0)
               for r, points in groups.items()}
    return [centres[root(number)] for number in range(len(polys))]


def rip_polygons(frames, vram, ms_per_frame, centred=False,
                 return_scale=False):
    """[(RGBA image, ms)] of captured sprite quads, one list of polygons a
    frame, or None when any of them is not a screen-facing quad (a model
    drawn by code, which only a render shows). `centred` holds each frame's
    sprites on the middle of the canvas - a bubble followed as it rises."""
    if not vram or not frames:
        return None
    cut = []
    for polys in frames:
        patches = [_patch(vram, p) for p in polys]
        if any(p is None for p in patches):
            return None
        cut.append(patches)
    every = [p for patches in cut for p in patches]
    if not every:
        return None
    scale = float(np.median([p[5] for p in every]))
    if centred:
        # Each frame about its own middle, the canvas as big as the biggest.
        shifted = []
        for patches in cut:
            if not patches:
                shifted.append(patches)
                continue
            mx = (min(p[1] for p in patches) + max(p[3] for p in patches)) / 2
            my = (min(p[2] for p in patches) + max(p[4] for p in patches)) / 2
            shifted.append([(r, x0 - mx, y0 - my, x1 - mx, y1 - my, sc)
                            for r, x0, y0, x1, y1, sc in patches])
        cut = shifted
        every = [p for patches in cut for p in patches]
    left = min(p[1] for p in every)
    top = min(p[2] for p in every)
    width = max(1, int(round((max(p[3] for p in every) - left) / scale)))
    height = max(1, int(round((max(p[4] for p in every) - top) / scale)))
    out = []
    for patches in cut:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for rgba, x0, y0, x1, y1, _s in patches:
            w = max(1, int(round((x1 - x0) / scale)))
            h = max(1, int(round((y1 - y0) / scale)))
            piece = Image.fromarray(rgba, "RGBA").resize((w, h), Image.NEAREST)
            canvas.alpha_composite(piece, (int(round((x0 - left) / scale)),
                                           int(round((y0 - top) / scale))))
        out.append((canvas, ms_per_frame))
    held = _held(out)
    return (held, scale) if return_scale else held


def rip_atlas(atlas, steps, ms_per_tick):
    """[(RGBA image, ms)] of a billboard's steps - (Placed, ticks) each, as
    gui/level/pickup_sprites.py lays them in `atlas` - on one canvas, each
    frame hung by its origin."""
    if atlas is None or not steps:
        return None
    height, width = atlas.shape[:2]
    pieces = []
    for placed, ticks in steps:
        x0, y0 = int(round(placed.u0 * width)), int(round(placed.v0 * height))
        w, h = int(round(placed.width)), int(round(placed.height))
        pieces.append((atlas[y0:y0 + h, x0:x0 + w], placed.origin_x, placed.origin_y,
                       max(ticks, 1)))
    left = max(ox for _p, ox, _oy, _t in pieces)
    top = max(oy for _p, _ox, oy, _t in pieces)
    right = max(p.shape[1] - ox for p, ox, _oy, _t in pieces)
    bottom = max(p.shape[0] - oy for p, _ox, oy, _t in pieces)
    size = (max(1, int(round(left + right))), max(1, int(round(top + bottom))))
    out = []
    for pixels, ox, oy, ticks in pieces:
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.alpha_composite(Image.fromarray(np.ascontiguousarray(pixels), "RGBA"),
                               (int(round(left - ox)), int(round(top - oy))))
        out.append((canvas, ticks * ms_per_tick))
    return _held(out)


def _held(frames):
    """Neighbouring frames that are the same picture, held as one."""
    out = []
    for image, ms in frames:
        if out and out[-1][0].tobytes() == image.tobytes():
            out[-1][1] += ms
        else:
            out.append([image, ms])
    return [(image, round(ms)) for image, ms in out]


def save_gif(path, frames, scale=4):
    """Write RGBA frames as a GIF with transparency, blown up `scale` times -
    a sprite is a few dozen pixels."""
    images = []
    for image, _ms in frames:
        big = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
        alpha = big.getchannel("A")
        paletted = big.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        paletted.paste(255, mask=alpha.point(lambda a: 255 if a < 128 else 0))
        paletted.info["transparency"] = 255
        images.append(paletted)
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=[max(20, ms) for _image, ms in frames], loop=0,
                   transparency=255, disposal=2)

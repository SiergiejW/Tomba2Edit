"""Reconstructing what a page of VRAM would actually look like drawn.

VRAM itself carries no palette - the same 256x256 page is read through
however many different CLUTs the disc's own models, sprites and
backgrounds happen to point at it with, which is why the plain view
(gui.vram_viewer.vram_index_image) is flat grey index values rather
than colour. This walks every asset one area's own data can name - its
MDAT room geometry, its SMST models (including trail ones, which is
where most character models actually live), its SPRT banks and its
BGMP backgrounds - and paints each patch of a texture page with the
very palette that asset draws it through, which is the closest a
static VRAM view can get to "how it actually looks" without running
the game.

WHAT THIS CANNOT KNOW

Only what the area's own SDAT and trailer name, and nothing any other
area's chunk contributes on top (see functions/vram_map). A texel two
different assets both sample with two different CLUTs is coloured by
whichever region is discovered last - there is no way to show both in
one static image. And, same as texture migration's own preview, this
knows nothing about what the game uploads into VRAM itself while
running.
"""
import copy
import struct

from functions import format_detect, psx_vram

# The cell every region's colour is spread to - see render().
CELL = 16

# Which formats a plain SDAT scan bothers to survey - MDAT is gathered
# separately, through area_mdat_entries()/exportMDAT() rather than
# format_detect, since an MDAT entry's own drawmap header is what tells
# it apart and format_detect already reads that the same way (see
# functions/format_detect.py's own MDAT detector). The order is what a
# texel a LATER kind also claims wins - sprites and backgrounds last,
# since a recoloured effect (a status flash, a palette-swapped enemy)
# is more often the interesting one to end up seeing.
_KINDS = ("SMST", "BGMP", "SPRT")


class Patch:
    """One rectangle of a texture page, and the CLUT it is read through -
    shaped to match what gui.sprt.sprt_render.VRAMTextures.piece_image
    needs, so that (already correct, already tested) sampler is what
    actually draws every kind of region here, not a second copy of it."""

    __slots__ = ("u0", "v0", "ww", "hh", "hflip", "vflip",
                "page_byte_x", "page_row0", "clut_address", "clut_index",
                "is_8bpp", "__weakref__")

    def __init__(self, page, clut_address, u0, v0, w, h, eight_bit=False):
        self.u0, self.v0 = u0 % psx_vram.UV_WRAP, v0 % psx_vram.UV_WRAP
        self.ww, self.hh = w, h
        self.hflip = self.vflip = False
        self.page_byte_x, self.page_row0 = psx_vram.page_origin(page)
        self.clut_address = clut_address
        # The CLUT word, which VRAMTextures caches palettes by - masking
        # the byte address gave palettes 0x10000 apart one key, and the
        # town's grass came out in a water palette.
        x, y = psx_vram.clut_address_xy(clut_address)
        self.clut_index = (y << 6) | (x >> 4)
        self.is_8bpp = eight_bit

    @property
    def dest(self):
        """(x, y) this patch's own top-left corner lands at in the
        4096x512 texel atlas gui.vram_viewer's images use."""
        # The preview canvas is the 4bpp interpretation of physical VRAM:
        # four pixels per halfword. u0 is relative to the page and was
        # previously omitted, which piled every coloured patch at the left
        # edge of its page. An 8bpp texel occupies two of these display pixels.
        scale = 2 if self.is_8bpp else 1
        return self.page_byte_x * 2 + self.u0 * scale, self.page_row0 + self.v0


def regions_from_polygons(polygons):
    """[Patch] from an SMST or MDAT model's own parsed polygon list -
    one per face, its box the face's own UV corners rather than the
    whole page, so two faces on the same page through different CLUTs
    do not overwrite each other."""
    out = []
    for poly in polygons or ():
        texels = poly.get("texels")
        if not texels:
            continue
        us = [u for u, _v in texels]
        vs = [v for _u, v in texels]
        u0, u1 = min(us), max(us) + 1
        v0, v1 = min(vs), max(vs) + 1
        if u1 <= u0 or v1 <= v0:
            continue
        out.append(Patch(poly["page"], poly["clut"], u0, v0,
                         u1 - u0, v1 - v0))
    return out


def regions_from_sprt(sprt_data):
    """[Patch] from a parsed SPRT bank - one per piece, exactly the box
    and flip state it draws (flips undone by u0/v0 already, per
    SpritePiece's own doc, so a plain unflipped Patch reads the same
    source texels either way)."""
    out = []
    for sprite in sprt_data.sprites:
        for piece in sprite.pieces:
            if piece.ww <= 0 or piece.hh <= 0:
                continue
            out.append(Patch(piece.texpage, piece.clut_address,
                             piece.u0, piece.v0, piece.ww, piece.hh,
                             eight_bit=piece.is_8bpp))
    return out


def regions_from_bgmp(bgmp):
    """[Patch] from a parsed BGMP background - one per tile, through that
    tile's own palette.

    A tile does not have to use the file's own base CLUT: BGMPTile.raw
    carries a palette index of its own into the PALETTE_COUNT palettes
    stacked below it (see gui/bgmp/bgmp_parser.py). One box per palette
    covering all its tiles painted over tiles of other palettes lying
    between them - Ranch Summit's and the Town of Fishermen's skies in
    the wrong colours."""
    from gui.bgmp.bgmp_parser import PALETTE_STRIDE, TILE

    seen, out = set(), []
    for tile in bgmp.tiles:
        key = (tile.page_x, tile.page_y, tile.palette)
        if key in seen:
            continue
        seen.add(key)
        clut = bgmp.clut_address + tile.palette * PALETTE_STRIDE
        out.append(Patch(bgmp.texpage, clut, tile.page_x, tile.page_y, TILE, TILE))
    return out


# --------------------------------------------------------------------
# Finding an area's own assets
# --------------------------------------------------------------------

def _sdat_entries(idx_path, dat_path, chunk_index):
    """[(address, size)] for every non-empty file this area's own SDAT
    table names - not its trailer, which _area_blobs adds separately."""
    with open(idx_path, "rb") as idx:
        idx.seek(chunk_index * 0x800)
        _img_s, _img_e, dat_start, dat_end, count = struct.unpack(
            "<5I", idx.read(20))
        if not count:
            return []
        raw = idx.read(count * 4)
    pointers = struct.unpack(f"<{count}I", raw)
    offsets = [v & 0xFFFFFF for v in pointers]
    out = []
    for i, off in enumerate(offsets):
        nxt = offsets[i + 1] if i + 1 < count else dat_end - dat_start
        size = nxt - off
        if size > 0x10:
            out.append((dat_start + off, size))
    return out


def area_regions(idx_path, dat_path, chunk_index):
    """Every Patch this area's own SDAT and trailer can account for -
    its MDAT room(s), its SMST models (trail ones included, which is
    where almost every character lives), its SPRT banks and its BGMP
    backgrounds.

    Not cheap - it parses every one of those files - so a caller
    showing this more than once should cache the result per area."""
    from gui.level.level_scene import trail_files
    from gui.mdat.mdat import area_mdat_entries, exportMDAT
    from gui.smst.smst_parser import parse_smst
    from gui.sprt.sprt_parser import parse_sprt
    from gui.bgmp.bgmp_parser import parse_bgmp
    from functions.format_detect import FormatError

    regions = []

    # MDAT first, so a room's own floor and walls are what a texture
    # actually placed there overrides, not the other way round.
    try:
        for _i, dat_start, offset, _size in area_mdat_entries(
                idx_path, dat_path, chunk_index):
            try:
                model = exportMDAT(dat_start + offset, dat_path)
                regions += regions_from_polygons(model.get("polygons"))
            except (OSError, ValueError, struct.error, IndexError):
                continue
    except (OSError, struct.error):
        pass

    entries = _sdat_entries(idx_path, dat_path, chunk_index)
    try:
        entries += trail_files(idx_path, chunk_index)
    except (OSError, struct.error):
        pass

    with open(dat_path, "rb") as dat:
        for address, size in entries:
            dat.seek(address)
            blob = dat.read(size)
            match = format_detect.best(blob)
            if not match or match.kind not in _KINDS:
                continue
            try:
                if match.kind == "SMST":
                    model = parse_smst(blob, address=address)
                    regions += regions_from_polygons(model.get("polygons"))
                elif match.kind == "SPRT":
                    regions += regions_from_sprt(parse_sprt(blob))
                elif match.kind == "BGMP":
                    regions += regions_from_bgmp(parse_bgmp(blob))
            except (FormatError, ValueError, struct.error, IndexError):
                continue
    return regions


def regions_from_captured(polys, uv_frames=None):
    """[Patch] from polygons actor_sim captured being drawn - (corners,
    uvs, colours, CLUT word, blended, page word) - each box also at every
    UV step its CLUT's `uv_frames` ({CLUT address: ((du, dv), ...)})
    moves it through, so a stepped stream colours all its cells."""
    out = []
    for _corners, uvs, _colours, clut, _blended, page in polys or ():
        if clut < 0 or not uvs:
            continue
        address = psx_vram.clut_address(clut)
        us = [u for u, _v in uvs]
        vs = [v for _u, v in uvs]
        u0, v0 = min(us), min(vs)
        w, h = max(us) - u0 + 1, max(vs) - v0 + 1
        steps = set((uv_frames or {}).get(address) or ()) | {(0, 0)}
        for du, dv in steps:
            out.append(Patch(page, address, u0 + du, v0 + dv, w, h,
                             eight_bit=(page >> 7) & 3 == 1))
    return out


def scene_regions(scene):
    """[Patch] for what a loaded level editor scene saw drawn: every
    captured polygon (effects, drawn-by-code props, billboards), with
    stepped UVs spread over every cell they step to."""
    out = []
    models = getattr(scene, "models", {}) or {}
    for key, polys in (getattr(scene, "drawn_polys", {}) or {}).items():
        model = models.get(key) or {}
        # drawn_model keyed its steps by CLUT address already.
        out += regions_from_captured(polys, model.get("uv_frames"))
    for frames in (getattr(scene, "captured_billboards", {}) or {}).values():
        for polys in frames:
            out += regions_from_captured(polys)
    return out


# Regions the level editor found, per area chunk, and who to tell when
# they change - the level editor loads progressively.
_level_regions = {}
_listeners = []


def publish_level(chunk_index, regions):
    _level_regions[chunk_index] = regions
    for listener in list(_listeners):
        listener(chunk_index)


def level_regions(chunk_index):
    return _level_regions.get(chunk_index, [])


def on_level_regions(listener):
    _listeners.append(listener)


def clut_spans(regions):
    """{(CLUT address, colour count)} the regions draw through."""
    return {(r.clut_address, 256 if r.is_8bpp else 16) for r in regions}


def paint_cluts(rgba, vram_bytes, spans):
    """Draw each palette row in `spans` in its own colours, straight into
    a (512, 4096, 4) array - a halfword is four pixels wide there."""
    import numpy as np

    raw = bytes(vram_bytes)
    for address, count in spans:
        x, y = psx_vram.clut_address_xy(address)
        if y >= psx_vram.VRAM_ROWS:
            continue
        count = min(count, psx_vram.VRAM_STRIDE // 2 - x)
        words = np.frombuffer(raw, dtype="<u2", count=count,
                              offset=y * psx_vram.VRAM_STRIDE + x * 2)
        colour = np.empty((count, 4), dtype=np.uint8)
        colour[:, 0] = (words & 0x1F) * 255 // 31
        colour[:, 1] = ((words >> 5) & 0x1F) * 255 // 31
        colour[:, 2] = ((words >> 10) & 0x1F) * 255 // 31
        colour[:, 3] = 255
        rgba[y, x * 4:(x + count) * 4] = np.repeat(colour, 4, axis=0)
    return rgba


def render(vram_bytes, regions):
    """The flat grey index view, with every known region painted
    through its own CLUT - the reconstruction this module exists for.

    Returns a PIL Image (RGBA, 4096x512) rather than a QImage, built
    straight from the same numpy array gui.vram_viewer.vram_index_image
    would turn into one, so a caller can composite further before
    handing it to Qt - which gui/vram_viewer.py's own wrapper does."""
    import numpy as np
    from PIL import Image

    from gui.vram_viewer import TEXEL_HEIGHT, TEXEL_WIDTH, vram_texels

    grey = vram_texels(vram_bytes) * 17
    rgba = np.empty((TEXEL_HEIGHT, TEXEL_WIDTH, 4), dtype=np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = grey
    rgba[..., 3] = 255
    base = Image.fromarray(rgba, "RGBA")
    base.alpha_composite(render_layer(vram_bytes, regions))
    return base


def render_layer(vram_bytes, regions, cells=True):
    """Just the painted regions, transparent everywhere else - so a
    caller can put them over any reading of the rest without redoing
    this (a CLUT crosshair steps far faster than this paints)."""
    import numpy as np
    from PIL import Image

    from gui.sprt.sprt_render import VRAMTextures
    from gui.vram_viewer import TEXEL_HEIGHT, TEXEL_WIDTH

    base = Image.new("RGBA", (TEXEL_WIDTH, TEXEL_HEIGHT), (0, 0, 0, 0))
    textures = VRAMTextures(vram_bytes)
    # Every 16x16 cell a region touches, whole, first: a face paints only
    # its own UV box, and the texels between faces stayed grey outlines.
    # The exact boxes go on top after. A cell takes the CLUT covering most
    # of it - the first one to touch it could be a neighbour's, which put
    # the town's grass through a water palette.
    cover = {}                      # cell key -> {CLUT address: (texels, region)}
    for region in regions if cells else ():
        for cu in range(region.u0 // CELL * CELL, region.u0 + region.ww, CELL):
            for cv in range(region.v0 // CELL * CELL, region.v0 + region.hh, CELL):
                if cu >= psx_vram.UV_WRAP or cv >= psx_vram.UV_WRAP:
                    continue
                area = ((min(cu + CELL, region.u0 + region.ww) - max(cu, region.u0))
                        * (min(cv + CELL, region.v0 + region.hh) - max(cv, region.v0)))
                key = (region.page_byte_x, region.page_row0, region.is_8bpp, cu, cv)
                by_clut = cover.setdefault(key, {})
                total, first = by_clut.get(region.clut_address, (0, region))
                by_clut[region.clut_address] = (total + area, first)
    fills = []
    for (_x, _y, _eight, cu, cv), by_clut in cover.items():
        _area, region = max(by_clut.values(), key=lambda found: found[0])
        cell = copy.copy(region)
        cell.u0, cell.v0, cell.ww, cell.hh = cu, cv, CELL, CELL
        fills.append(cell)
    # Where palettes compete for the same texels (one texture drawn as
    # grass, dirt and rock), the one covering most of the page goes last.
    weight = {}
    for region in regions:
        key = (region.page_byte_x, region.page_row0, region.clut_address)
        weight[key] = weight.get(key, 0) + region.ww * region.hh
    exact = sorted(regions, key=lambda r: weight[(r.page_byte_x, r.page_row0,
                                                  r.clut_address)])
    for region in fills + exact:
        try:
            patch = np.array(textures.piece_image(region).convert("RGBA"))
        except (IndexError, ValueError):
            continue
        # Opaque, colour 0 as the black it holds: a transparent texel let
        # the palette painted under it show through as speckles.
        clear = patch[..., 3] == 0
        patch[clear, :3] = 0
        patch[..., 3] = 255
        patch = Image.fromarray(patch, "RGBA")
        if region.is_8bpp:
            patch = patch.resize((patch.width * 2, patch.height), Image.Resampling.NEAREST)
        # paste clips a patch running off the canvas's edge.
        base.paste(patch, region.dest)
    return base

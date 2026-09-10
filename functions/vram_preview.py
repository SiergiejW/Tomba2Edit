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
import struct

from functions import format_detect, psx_vram

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
                "is_8bpp")

    def __init__(self, page, clut_address, u0, v0, w, h, eight_bit=False):
        self.u0, self.v0 = u0 % psx_vram.UV_WRAP, v0 % psx_vram.UV_WRAP
        self.ww, self.hh = w, h
        self.hflip = self.vflip = False
        self.page_byte_x, self.page_row0 = psx_vram.page_origin(page)
        self.clut_address = clut_address
        self.clut_index = psx_vram.clut_index(clut_address)
        self.is_8bpp = eight_bit

    @property
    def dest(self):
        """(x, y) this patch's own top-left corner lands at in the
        4096x512 texel atlas gui.vram_viewer's images use."""
        return self.page_byte_x * 2, self.page_row0 + self.v0


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
    """[Patch] from a parsed BGMP background - one region per distinct
    palette its tiles actually use.

    A tile does not have to use the file's own base CLUT: BGMPTile.raw
    carries a palette index of its own into the PALETTE_COUNT palettes
    stacked below it (see gui/bgmp/bgmp_parser.py). One box per palette,
    the bounding rectangle of whichever cells use it - not necessarily
    the full page."""
    from gui.bgmp.bgmp_parser import PALETTE_STRIDE, TILE

    if not bgmp.tiles:
        return []
    by_palette = {}
    for tile in bgmp.tiles:
        by_palette.setdefault(tile.palette, []).append(tile)
    out = []
    for palette, tiles in by_palette.items():
        xs = [t.page_x for t in tiles]
        ys = [t.page_y for t in tiles]
        u0, u1 = min(xs), max(xs) + TILE
        v0, v1 = min(ys), max(ys) + TILE
        clut = bgmp.clut_address + palette * PALETTE_STRIDE
        out.append(Patch(bgmp.texpage, clut, u0, v0, u1 - u0, v1 - v0))
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


def render(vram_bytes, regions):
    """The flat grey index view, with every known region painted
    through its own CLUT - the reconstruction this module exists for.

    Returns a PIL Image (RGBA, 4096x512) rather than a QImage, built
    straight from the same numpy array gui.vram_viewer.vram_index_image
    would turn into one, so a caller can composite further before
    handing it to Qt - which gui/vram_viewer.py's own wrapper does."""
    import numpy as np
    from PIL import Image

    from gui.sprt.sprt_render import VRAMTextures
    from gui.vram_viewer import TEXEL_HEIGHT, TEXEL_WIDTH, vram_texels

    grey = vram_texels(vram_bytes) * 17
    rgba = np.empty((TEXEL_HEIGHT, TEXEL_WIDTH, 4), dtype=np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = grey
    rgba[..., 3] = 255
    base = Image.fromarray(rgba, "RGBA")

    textures = VRAMTextures(vram_bytes)
    for region in regions:
        try:
            patch = textures.piece_image(region)
            # A region that wraps off the right edge of its own page,
            # or sits at the very edge of VRAM, can land partly outside
            # the 4096x512 canvas - alpha_composite refuses that rather
            # than clipping it, and one odd region should not lose every
            # other one already painted.
            base.alpha_composite(patch, dest=region.dest)
        except (IndexError, ValueError):
            continue
    return base

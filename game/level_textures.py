"""Putting a foreign level's art where the level it replaces had its own.

A level's geometry is replaced whole, so the texture space its MDAT used
is free - but only the part nothing else reads. This finds that part,
packs the new art into it, and hands back the retargeted packets and the
pixels to write.

WHERE ART MAY GO (usage())

    freed     what only the replaced MDAT read: not another MDAT, an
              SMST (trail files included), an SPRT or a BGMP of the
              area, not a palette the overlay animates, not something a
              savestate shows the game uploading itself
    gutters   the slivers between the replaced tiles - unread, and boxed
              in by freed art. Without them the freed space is confetti
    spare     what no loaded chunk writes at all, if a savestate is
              given to say the game leaves it alone

What it must NOT use is the rest of what "nothing reads". In Town that
is 41,552 halfwords, and 5,018 of them were sampled in 576 recorded
frames, by things drawn from code. Unread on the disc is not unused.

HOW IT IS PACKED (install())

Faces are grouped into tiles - faces whose UV boxes overlap share one -
and each tile is moved as a rectangle of whole halfwords, so the texels
a face reads are the ones it read before. The tile with the fewest
places left goes first, into the snuggest of them; a tile that fits
nowhere is split between faces; the whole thing is retried with some
randomness and the best attempt kept. What still has no room is shrunk
by the least that fits, and reported: for the Village into Town that
was 5 rectangles of 61.
"""
import math
import random
import struct

import numpy as np

from game import texture_migrate as migration
from psx import vram as psx_vram
from psx import vram_map

SHAPE = (psx_vram.VRAM_ROWS, psx_vram.VRAM_STRIDE // 2)
PAGE_W, PAGE_H = psx_vram.PAGE_HALFWORDS, psx_vram.PAGE_ROWS
GUTTER_REACH = (1, 4)       # halfwords across, rows down


# --- who reads what ---------------------------------------------------

def cells(patches):
    """(texture, palette) halfword maps for a list of vram_preview.Patch."""
    texture, palette = np.zeros(SHAPE, bool), np.zeros(SHAPE, bool)
    for p in patches:
        per = 2 if p.is_8bpp else 4
        x0 = p.page_byte_x // 2 + p.u0 // per
        x1 = p.page_byte_x // 2 + (p.u0 + p.ww - 1) // per
        texture[p.page_row0 + p.v0:p.page_row0 + p.v0 + p.hh, x0:x1 + 1] = True
        x, y = psx_vram.clut_address_xy(p.clut_address)
        palette[y, x:x + (256 if p.is_8bpp else 16)] = True
    return texture, palette


def readers(idx_path, dat_path, area):
    """{DAT address: (kind, texture map, palette map)} for every file of
    the area, trail included, that samples VRAM."""
    from formats.archive import format_detect
    from formats.archive.repacker import parse_idx
    from formats.background.bgmp_parser import parse_bgmp
    from formats.geometry.mdat import parse_mdat
    from formats.models.smst_parser import parse_smst
    from formats.sprites.sprt_parser import parse_sprt
    from gui.level.level_scene import trail_files
    from psx.vram_preview import (regions_from_bgmp, regions_from_polygons,
                                  regions_from_sprt)

    chunk = parse_idx(idx_path)[area]
    pointers = chunk["sdat_pointers"]
    entries = []
    for slot, (_id, offset) in enumerate(pointers):
        end = (pointers[slot + 1][1] if slot + 1 < len(pointers)
               else chunk["dat_end"] - chunk["dat_start"])
        entries.append((chunk["dat_start"] + offset, end - offset))
    entries += trail_files(idx_path, area)
    out = {}
    with open(dat_path, "rb") as dat:
        for address, size in entries:
            dat.seek(address)
            blob = dat.read(size)
            match = format_detect.best(blob)
            kind = match.kind if match else None
            try:
                if kind == "MDAT":
                    patches = regions_from_polygons(parse_mdat(blob).get("polygons"))
                elif kind == "SMST":
                    patches = regions_from_polygons(
                        parse_smst(blob, address=address).get("polygons"))
                elif kind == "SPRT":
                    patches = regions_from_sprt(parse_sprt(blob))
                elif kind == "BGMP":
                    patches = regions_from_bgmp(parse_bgmp(blob))
                else:
                    continue
            except (ValueError, struct.error, IndexError):
                continue
            out[address] = (kind,) + cells(patches)
    return out


def _close(mask, reach):
    """Morphological closing, a page at a time."""
    rx, ry = reach

    def grow(block):
        out = block.copy()
        for dx in range(-rx, rx + 1):
            for dy in range(-ry, ry + 1):
                if not (dx or dy):
                    continue
                moved = np.zeros_like(block)
                moved[max(dy, 0):block.shape[0] + min(dy, 0),
                      max(dx, 0):block.shape[1] + min(dx, 0)] = \
                    block[max(-dy, 0):block.shape[0] + min(-dy, 0),
                          max(-dx, 0):block.shape[1] + min(-dx, 0)]
                out |= moved
        return out

    out = np.zeros_like(mask)
    for page in range(32):
        px, py = (page % 16) * PAGE_W, (page // 16) * PAGE_H
        block = mask[py:py + PAGE_H, px:px + PAGE_W]
        if block.any():
            out[py:py + PAGE_H, px:px + PAGE_W] = ~grow(~grow(block))
    return out


class Usage:
    """What a replaced file frees in its area's VRAM."""

    def __init__(self, freed, gutters, spare, unread, others):
        self.freed, self.gutters, self.spare = freed, gutters, spare
        self.unread, self.others = unread, others

    @property
    def free(self):
        return (self.freed | self.gutters | self.spare) & ~self.others

    def counts(self):
        return {"freed": int(self.freed.sum()), "gutters": int(self.gutters.sum()),
                "spare": int(self.spare.sum()), "unread, not used": int(self.unread.sum())}


def usage(idx_path, dat_path, img_path, area, replaced, overlay=None, state=None):
    """Usage for replacing the file at DAT address `replaced`.

    `overlay` is the area's Axx.BIN, for its animated palettes. `state`
    is a savestate's VRAM taken in the area (psx.state_vram.read): what
    it holds that no chunk wrote is kept clear, and only with it is any
    spare VRAM offered at all."""
    from formats.animation import clut_anim
    from psx import state_vram

    found = readers(idx_path, dat_path, area)
    if replaced not in found:
        raise ValueError("the replaced file samples no VRAM")
    _kind, texture, palette = found.pop(replaced)
    mine = texture | palette
    others = np.zeros(SHAPE, bool)
    for _kind, texture, palette in found.values():
        others |= texture | palette
    if overlay:
        for animation in clut_anim.load_animations(overlay)[1]:
            others[animation.y, animation.x:animation.x + 16] = True
    shards = vram_map.chunk_shards(idx_path, img_path)
    level = vram_map.claims_of(shards, (area,))
    spare = np.zeros(SHAPE, bool)
    if state is not None:
        loaded = list(vram_map.ALWAYS_RESIDENT) + [area]
        runtime = state_vram.runtime_only(state, shards, loaded)
        runtime[:, :vram_map.DISPLAY_COLUMNS] = False
        others |= runtime
        spare = vram_map.free_for(shards, [area], state_vram.occupancy(state))
    freed = mine & level & ~others
    unread = level & ~mine & ~others
    gutters = _close(freed, GUTTER_REACH) & unread
    return Usage(freed, gutters, spare, unread & ~gutters, others)


# --- packing ----------------------------------------------------------

def tiles(blob, page):
    """[(box, [packets])]: faces whose halfword UV boxes overlap are one
    tile. Finer than texture_migrate.clusters(), which joins tiles that
    merely touch into patches the size of a page."""
    own = migration.packet_boxes(blob, page)
    rects = [[b[0] // 4, b[1], b[2] // 4, b[3]] for b in own.values()]
    groups = [[ind] for ind in own]
    changed = True
    while changed:
        changed = False
        order = sorted((i for i in range(len(rects)) if rects[i]),
                       key=lambda i: rects[i][0])
        for at, a in enumerate(order):
            if rects[a] is None:
                continue
            for b in order[at + 1:]:
                if rects[b] is None:
                    continue
                ra, rb = rects[a], rects[b]
                if rb[0] > ra[2]:
                    break
                if ra[1] <= rb[3] and rb[1] <= ra[3]:
                    rects[a] = [min(ra[0], rb[0]), min(ra[1], rb[1]),
                                max(ra[2], rb[2]), max(ra[3], rb[3])]
                    groups[a] += groups[b]
                    rects[b] = None
                    changed = True
    return [((r[0] * 4, r[1], r[2] * 4 + 3, r[3]), g)
            for r, g in zip(rects, groups) if r]


class Space:
    """Free halfwords, with 'where does w x h fit' by summed-area table."""

    def __init__(self, free):
        self.free = free.copy()
        self.pages = [p for p in range(32) if self._block(p).any()]
        self._tables = {}
        self._counts = {p: {} for p in self.pages}
        self.reserved = {}

    def _block(self, page):
        px, py = (page % 16) * PAGE_W, (page // 16) * PAGE_H
        return self.free[py:py + PAGE_H, px:px + PAGE_W]

    def _fits(self, page, w, h):
        """(where a w x h block fits, free cells in the ring round it)."""
        if w > PAGE_W or h > PAGE_H:
            return None, None
        table = self._tables.get(page)
        if table is None:
            padded = np.zeros((PAGE_H + 3, PAGE_W + 3), np.int32)
            padded[2:PAGE_H + 2, 2:PAGE_W + 2] = self._block(page)
            table = self._tables[page] = padded.cumsum(0).cumsum(1)
        ys, xs = PAGE_H - h + 1, PAGE_W - w + 1

        def box(y0, x0, y1, x1):
            return (table[y1 - 1:y1 - 1 + ys, x1 - 1:x1 - 1 + xs]
                    - table[y0 - 1:y0 - 1 + ys, x1 - 1:x1 - 1 + xs]
                    - table[y1 - 1:y1 - 1 + ys, x0 - 1:x0 - 1 + xs]
                    + table[y0 - 1:y0 - 1 + ys, x0 - 1:x0 - 1 + xs])

        inner = box(2, 2, 2 + h, 2 + w)
        return inner == w * h, box(1, 1, 3 + h, 3 + w) - inner

    def count(self, w, h):
        total = 0
        for page in self.pages:
            cache = self._counts[page]
            key = (w, h)
            if key not in cache:
                ok, _ring = self._fits(page, w, h)
                cache[key] = int(ok.sum()) if ok is not None else 0
            total += cache[key]
        return total

    def place(self, w, h, rng=None, loose=1):
        """(x, y, page) of a snug place, or None. `loose` > 1 picks among
        that many of the snuggest, for a randomised attempt."""
        options = []
        for page in self.pages:
            ok, ring = self._fits(page, w, h)
            if ok is None or not ok.any():
                continue
            score = np.where(ok, ring, 1 << 30)
            flat = score.ravel()
            best = ([int(flat.argmin())] if loose == 1 else
                    np.argpartition(flat, min(loose, flat.size)-1)[:loose])
            for at in best:
                y, x = divmod(int(at), score.shape[1])
                if ok[y, x]:
                    options.append((int(score[y, x]), y, x, page))
        if not options:
            return None
        options.sort()
        _score, y, x, page = options[0] if loose == 1 else rng.choice(options[:loose])
        return (page % 16) * PAGE_W + x, (page // 16) * PAGE_H + y, page

    def take(self, x, y, w, h):
        self.free[y:y + h, x:x + w] = False
        self._tables.pop((y // PAGE_H) * 16 + x // PAGE_W, None)
        self._counts[(y // PAGE_H) * 16 + x // PAGE_W].clear()


def _attempt(blob, items, free, rng, loose, first=(), progress=None, cancelled=None):
    """One packing. `first` are the tiles (numbers into `items`) to place
    before the rest, largest first - the ones an earlier attempt left over."""
    space = Space(free)
    queue, moves, failed = list(enumerate(items)), [], []
    while queue:
        if cancelled and cancelled():
            raise migration.MigrationError('Import cancelled. The project was not changed.')
        if progress and (len(moves)+len(failed)) % 32 == 0:
            progress(len(moves),len(queue)+len(failed))
        scored = []
        for n, (origin, item) in enumerate(queue):
            _x, _y, w, h = migration.source_rect(item[0], item[1])
            if origin in first:
                scored.append((-1, -w * h, n, origin, item, w, h))
                continue
            jitter = rng.randint(-40, 40) if loose > 1 else 0
            scored.append((space.count(w, h), -w * h + jitter, n, origin, item, w, h))
        scored.sort(key=lambda s: s[:3])
        _count, _area, at, origin, item, w, h = scored[0]
        del queue[at]
        page, box, packets, own = item
        spot = space.place(w, h, rng, loose)
        if spot:
            space.take(spot[0], spot[1], w, h)
            moves.append((page, box, packets, spot[2], spot[0], spot[1]))
            continue
        halves = (migration.split_to_fit(blob, page, packets, own,
                                         lambda _w, _h: False, depth=10)
                  if len(packets) > 1 else [])
        if len(halves) > 1:
            queue.extend((origin, (page, b, p, own)) for b, p in halves)
        else:
            failed.append((origin, page, box, packets))
    lost = sum((lambda r: r[2] * r[3])(migration.source_rect(p, b))
               for _origin, p, b, _packets in failed)
    return lost, moves, [f[1:] for f in failed], space, {f[0] for f in failed}


def pack(blob, free, tries=60, seed=1, progress=None, cancelled=None):
    """(moves, palette destinations, tiles left over, Space) - the best of
    `tries` attempts. Raises if even the palettes find no room.

    A tile left over is one that had room until smaller ones took it, so
    the next attempt places it before anything else. Only when that
    turns up no new leftovers does an attempt fall back on chance."""
    _boxes, cluts = migration.survey(blob)
    items = []
    for page in sorted(migration.survey(blob)[0]):
        own = migration.packet_boxes(blob, page)
        items += [(page, box, packets, own) for box, packets in tiles(blob, page)]
    # Palettes go where they always would: once, not once an attempt.
    taken, clut_dest = free.copy(), {}
    for address in cluts:
        found = vram_map.free_rects(taken, 16, 1, limit=1, align=16)
        if not found:
            raise migration.MigrationError(
                "there is not even room for the palettes")
        x, y, _page = found[0]
        taken[y, x:x + 16] = False
        clut_dest[address] = x * 2 + y * psx_vram.VRAM_STRIDE
    rng = random.Random(seed)
    best, cost, first = None, None, frozenset()
    for n in range(tries):
        # In order: by the rule, by the rule with its leftovers first, by chance.
        result = _attempt(blob, items, taken, rng, 1 if n < 2 else 4, first if n == 1 else (),
                          (lambda placed,left: progress(n,placed,left)) if progress else None, cancelled)
        price = _price(result[2], result[3])
        if best is None or price < cost:
            best, cost = result, price
            if progress:
                progress(n, len(result[1]), len(result[2]))
        if cost == (0, 0):
            break
        if n == 0:
            first = frozenset(result[4])
    if cost[0]:
        # Full-size-first packing can consume the space needed by the remaining
        # large tiles. Start over, reserving reduced rectangles largest first,
        # while retaining the same quarter-size quality floor.
        space, failed = Space(taken), []
        queue = sorted(items, key=lambda item: _area(item[:3]))
        while queue:
            if cancelled and cancelled():
                raise migration.MigrationError('Import cancelled. The project was not changed.')
            page, box, packets, own = queue.pop()
            tile = (page,box,packets)
            spot = _fit_smaller(tile,space)
            if spot is None:
                halves = (migration.split_to_fit(blob,page,packets,own,lambda w,h:False,depth=10)
                          if len(packets)>1 else [])
                if len(halves)>1:
                    queue.extend((page,b,p,own) for b,p in halves)
                    queue.sort(key=lambda item:_area(item[:3]))
                    continue
                raise migration.MigrationError(
                    'The model’s textures do not fit the space freed by the target, even at one-quarter resolution. '
                    'Reduce or combine texture images in Blender. The project was not changed.')
            space.reserved[(page,box,tuple(packets))] = spot
            failed.append(tile)
            if progress:
                progress(tries-1,len(failed),len(queue))
        return [], clut_dest, failed, space
    return best[1], clut_dest, best[2], best[3]


def _area(tile):
    box = tile[1]
    return (box[2] - box[0] + 1) * (box[3] - box[1] + 1)


def _fit_smaller(tile, space):
    """Room for a leftover tile at the largest sixteenth of its size that
    has any: (sixteenths, x, y, halfwords, new width, new height). Takes it."""
    reserved = space.reserved.pop((tile[0],tile[1],tuple(tile[2])),None)
    if reserved is not None:
        return reserved
    box = tile[1]
    wide, tall = box[2] - box[0] + 1, box[3] - box[1] + 1
    for num in range(15, 3, -1):
        new_w, new_h = math.ceil(wide * num / 16), math.ceil(tall * num / 16)
        w = max(2, math.ceil(new_w / 8) * 2)
        spot = space.place(w, new_h)
        if spot:
            space.take(spot[0], spot[1], w, new_h)
            return num, spot[0], spot[1], w, new_w, new_h
    return None


def _price(failed, space):
    """Texels a packing loses once its leftovers are shrunk into what
    room it left - what the result looks like, where the area that did
    not fit is only what went wrong."""
    trial, lost, missing = Space(space.free), 0, 0
    for tile in sorted(failed, key=_area, reverse=True):
        found = _fit_smaller(tile, trial)
        if found is None:
            missing += 1
        else:
            lost += _area(tile) - found[4] * found[5]
    # A plan that fits every tile always beats a prettier but impossible one.
    return missing, lost


def _shrink(target, kinds, tile, space, atlas):
    """Place one leftover tile at the largest sixteenth that fits. Edits
    `target`'s packets; returns (shard, sixteenths, sizes)."""
    page, box, packets = tile
    wide, tall = box[2] - box[0] + 1, box[3] - box[1] + 1
    sx, sy = (page % 16) * 256, (page // 16) * 256
    found = _fit_smaller(tile, space)
    if found is None:
        raise migration.MigrationError(
            f"a {wide}x{tall} texture fits nowhere, even at a quarter of its size")
    num, x, y, w, new_w, new_h = found
    dest = (y // PAGE_H) * 16 + x // PAGE_W
    u0, v0 = (x - (dest % 16) * PAGE_W) * 4, y - (dest // 16) * PAGE_H
    cols = np.minimum(wide - 1, (np.arange(w * 4) * wide) // new_w)
    rows = np.minimum(tall - 1, (np.arange(new_h) * tall) // new_h)
    block = atlas[sy + box[1] + rows][:, sx + box[0] + cols].astype(np.uint16)
    words = (block[:, 0::4] | block[:, 1::4] << 4 | block[:, 2::4] << 8
             | block[:, 3::4] << 12).astype("<u2")
    for ind in packets:
        for ou, ov in kinds[ind]:
            target[ind + ou] = u0 + min(new_w - 1, (target[ind + ou] - box[0]) * new_w // wide)
            target[ind + ov] = v0 + min(new_h - 1, (target[ind + ov] - box[1]) * new_h // tall)
        target[ind + migration.PAGE_BYTE] = (target[ind + migration.PAGE_BYTE] & 0xE0) | dest
    return (x, y, w, new_h, words.tobytes()), num, (wide, tall, new_w, new_h)


def install(blob, source_vram, free, dest_vram, tries=60, seed=1, progress=None, cancelled=None):
    """Move every texture and palette `blob` samples into `free`.

    `blob` is an SMST-shaped run of packets pointing into `source_vram`;
    `dest_vram` is what the area has loaded. Returns (retargeted blob,
    shards to write, VRAM as it will be, report). Every face that was not
    shrunk is checked to read the texels it read before."""
    from formats.animation import uv_anim
    from formats.models.gltf_export import index_atlas

    moves, clut_dest, failed, space = pack(blob, free, tries, seed, progress, cancelled)
    plan = migration.place(blob, moves, clut_dest)
    target = bytearray(migration.retarget(blob, plan))
    shards = migration.shards_for(plan, source_vram)
    kinds = dict(migration._packets(blob))
    atlas = index_atlas(source_vram)
    shrunk, scaled = [], set()
    # The largest first: it is the one that needs the largest hole.
    for tile in sorted(failed, key=_area, reverse=True):
        shard, num, sizes = _shrink(target, kinds, tile, space, atlas)
        shards.append(shard)
        scaled |= set(tile[2])
        shrunk.append({"texels": f"{sizes[0]}x{sizes[1]}", "became": f"{sizes[2]}x{sizes[3]}",
                       "scale": f"{num}/16", "faces": len(tile[2])})
    target = bytes(target)
    wrote = np.zeros(SHAPE, bool)
    for x, y, w, h, _pixels in shards:
        if wrote[y:y + h, x:x + w].any():
            raise migration.MigrationError("two textures were given the same place")
        wrote[y:y + h, x:x + w] = True
    if (wrote & ~free).any():
        raise migration.MigrationError("a texture was placed outside the free space")
    vram = migration.patch(dest_vram, shards)
    before, after, checked = {}, {}, 0
    for ind, uvs in migration._packets(blob):
        if ind in scaled:
            continue
        a = blob[ind + migration.PAGE_BYTE] & 31
        b = target[ind + migration.PAGE_BYTE] & 31
        if a not in before:
            before[a] = uv_anim.page_texels(source_vram, a)
        if b not in after:
            after[b] = uv_anim.page_texels(vram, b)
        for ou, ov in uvs:
            checked += 1
            if before[a][blob[ind + ov], blob[ind + ou]] != after[b][target[ind + ov], target[ind + ou]]:
                raise migration.MigrationError("a face would read different texels after the move")
    report = {"rectangles": len(plan.moves) + len(shrunk), "at full size": len(plan.moves),
              "shrunk": shrunk, "palettes": len(plan.cluts),
              "halfwords written": int(wrote.sum()), "corners checked": checked}
    return target, shards, vram, report

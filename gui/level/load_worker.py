"""Progressive scene preparation in a separate, cancellable process.

Only plain arrays and scene snapshots cross the process boundary. Qt/OpenGL
widgets belong exclusively to the application's GUI thread.
"""
import contextlib
import hashlib
import json
import os
import pickle
import sys

import numpy as np


class _StatusTee:
    """stderr for diagnostics; each stage line (LevelScene._log_actors) also
    goes down the protocol as `status <line>` for the viewport."""

    def __init__(self, log, protocol):
        self.log, self.protocol = log, protocol

    def write(self, text):
        self.log.write(text)
        for line in text.splitlines():
            if line.startswith("AREA_"):
                # Drop the class tally: "... - 9 actor(s): 2x f_..., ..."
                head, _sep, _classes = line.partition(" actor(s)")
                status(self.protocol, head + (" actor(s)" if _sep else ""))
        return len(text)

    def flush(self):
        self.log.flush()


def status(protocol, text):
    print("status " + " ".join(text.split()), file=protocol, flush=True)


def load_vram(dat, idx, chunk):
    from gui.img.img_viewer import chunk_bounds
    from gui.vram_viewer import decode_vram_bytes
    def read(area):
        try:
            start, end = chunk_bounds(idx, area)
            if end <= start:
                return None
            with open(os.path.join(os.path.dirname(dat), "TOMBA2.IMG"), "rb") as f:
                f.seek(start)
                return decode_vram_bytes(f.read(end - start))
        except OSError:
            return None
    own, common = read(chunk), read(1)
    if own is None:
        return common
    if common is None:
        return own
    a = np.frombuffer(bytes(own), np.uint8).copy()
    b = np.frombuffer(bytes(common), np.uint8)
    count = min(len(a), len(b))
    a[:count] = np.where(a[:count] == 0, b[:count], a[:count])
    return a.tobytes()


def buffers(scene, vram):
    from functions import texture_window, draw_order
    from gui.smst.smst_viewer import SMSTViewer
    from gui.level.level_viewer import UNIT_SCALE
    model = scene.build()
    indices, ranges, palettes, transparency = [], [], {}, {}
    for group in scene.instances:
        grouped = {}
        for face in range(group.first_face, group.first_face + group.face_count):
            _page, clut, semi, blend = model["texture_info"][face]
            grouped.setdefault((clut, semi, blend), []).extend(model["faces"][face])
        for (clut, semi, blend), faces in grouped.items():
            ranges.append((group.index, clut, len(indices) * 4, len(faces), semi, blend))
            indices.extend(faces)
            if clut not in palettes:
                transparency[clut] = semi
                palettes[clut] = (np.full((16, 4), (255, 255, 255, 128 if semi else 255), np.uint8)
                    if clut < 0 else SMSTViewer._clut_from_bytes(
                        bytes((vram or b"")[clut:clut + 32]), semi))
    arrays = ((scene.positions(model) / UNIT_SCALE).astype(np.float32).flatten(),
              np.asarray(model["vertex_colors"], np.float32).flatten(),
              np.asarray(model["texture_coords"], np.float32).flatten(),
              np.asarray(indices, np.uint32), texture_window.vertex_flags(model),
              draw_order.vertex_levels(model))
    return arrays, ranges, palettes, transparency


def sprites(scene, vram):
    from gui.level import pickup_sprites as p
    from functions import sprite_rip
    from gui.level.level_scene import view_point
    if not vram:
        return None, ()
    sources = {"resident": scene.resident.get(p.RESIDENT_SPRT_ID)}
    own = scene.by_id.get(p.AREA_SPRT_ID)
    if own is not None:
        sources["area"] = (scene.dat_start, own)
    banks = {name: p.SpriteBank(scene.dat_path, where[0], *where[1], vram)
             for name, where in sources.items() if where is not None}
    # Every captured effect as cards: each polygon turned to the camera about
    # its own centre, where it stands. One picture for a whole clip turned
    # every particle - A0K's berries, eight vents' steam - about one pivot.
    extra, cards, pictures = [], {}, {}
    for index, polygons in scene.captured_billboards.items():
        cut = sprite_rip.rip_cards(polygons, vram)
        if not cut:
            continue
        cards[index] = []
        for frame in cut:
            keys = []
            for pixels, centre, offsets, fractions in frame:
                # The same picture - a berry, a puff - packed once.
                digest = (pixels.shape, hashlib.sha1(pixels.tobytes()).digest())
                key = pictures.get(digest)
                if key is None:
                    key = pictures[digest] = ("card", len(pictures))
                    extra.append((key, pixels, 0.0, 0.0))
                keys.append((key, view_point(centre), offsets, fractions))
            cards[index].append(keys)
    atlas, placed = p.build_atlas(banks, p.wanted_frames(scene.instances), extra=extra)
    quads = p.billboards(scene.instances, placed)
    for index, frames in cards.items():
        instance = scene.instances[index]
        # Each card where it stands, as a step from the row, so a dragged
        # row takes its cards with it.
        made = [tuple((placed[key], tuple(np.subtract(at, (instance.x, instance.y, instance.z))),
                       offsets, fractions)
                      for key, at, offsets, fractions in keys if key in placed)
                for keys in frames]
        if not any(made):
            continue
        # A step's own picture is only the box round its cards - what a
        # click is tested against - one world unit a texel.
        every = [(o[0] + shift[0], o[1] + shift[1]) for frame in made
                 for _p, shift, offsets, _f in frame for o in offsets]
        xs, ys = [o[0] for o in every], [o[1] for o in every]
        box = p.Placed(u0=0.0, v0=0.0, u1=0.0, v1=0.0,
                       width=max(xs) - min(xs), height=max(ys) - min(ys),
                       origin_x=-min(xs), origin_y=max(ys))
        quads.append(p.Billboard(index=index, x=instance.x, y=instance.y, z=instance.z,
            steps=tuple((box, 1) for _frame in made), loops=True, units=1.0,
            blend=scene.captured_billboard_blends.get(index), cards=tuple(made)))
    return atlas, quads


def background(scene, vram, overlay):
    from gui.bgmp import bgmp_render
    from gui.bgmp.bgmp_parser import load_bgmp
    from gui.level.level_panel import LevelEditorPanel
    from gui.level.level_scene import BACKGROUND_ID
    from functions import sky_gradient
    sky = sky_gradient.image(overlay) if overlay else None
    entry = scene.by_id.get(BACKGROUND_ID)
    if not entry or not entry[1]:
        return [(sky, 0)] if sky is not None else []
    bg = load_bgmp(scene.dat_path, scene.dat_start, *entry)
    textures = bgmp_render.BackgroundTextures(vram) if vram else None
    offset = bgmp_render.detect_page_y_offset(bg, textures)
    phases = LevelEditorPanel._background_frames(None, bg, vram, offset, overlay)
    if sky is not None:
        phases = [(sky_gradient.under(image, sky), ms) for image, ms in phases]
    return phases


def main(argv):
    from gui.level.level_scene import LevelScene
    manifest, directory = argv
    with open(manifest, encoding="utf-8") as f:
        args = json.load(f)
    vram = load_vram(args[0], args[1], args[2])
    phases = None
    sequence = 0
    protocol = sys.stdout

    def publish(scene, title, complete=False):
        nonlocal sequence, phases
        status(protocol, f"Preparing the view: {title}")
        prepared = buffers(scene, vram)
        from gui.clut_animation import prepare_animation_data
        prepared = (*prepared, prepare_animation_data(vram, scene.build(), args[3]))
        positions = scene.positions(scene.build())
        bounds = {}
        for instance in scene.instances:
            if instance.scene is None:
                continue
            points = (positions[instance.first_vertex:instance.first_vertex + instance.vertex_count]
                      if instance.vertex_count else np.array([[instance.x, instance.y, instance.z]]))
            low, high = points.min(axis=0), points.max(axis=0)
            if instance.scene in bounds:
                low = np.minimum(low, bounds[instance.scene][0])
                high = np.maximum(high, bounds[instance.scene][1])
            bounds[instance.scene] = (low, high)
        scene._prepared_collision = {view: scene.collision(view, bounds)
                                     for view in (None, "all", *bounds)}
        if phases is None:
            phases = background(scene, vram, args[3])
        atlas = sprites(scene, vram)
        state = {k: v for k, v in scene.__dict__.items() if k != "world"}
        path = os.path.join(directory, f"stage-{sequence}.pickle")
        sequence += 1
        with open(path + ".part", "wb") as f:
            pickle.dump((state, vram, phases, atlas, prepared, title, complete), f,
                        protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(path + ".part", path)
        print(os.path.basename(path), file=protocol, flush=True)

    # stdout is the snapshot protocol; normal diagnostics stay on stderr.
    with contextlib.redirect_stdout(_StatusTee(sys.stderr, protocol)):
        scene = LevelScene().load(*args, publish=publish)
        publish(scene, "Ready", complete=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

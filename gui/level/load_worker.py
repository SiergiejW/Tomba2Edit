"""Progressive scene preparation in a separate, cancellable process.

Only plain arrays and scene snapshots cross the process boundary. Qt/OpenGL
widgets belong exclusively to the application's GUI thread.
"""
import contextlib
import json
import os
import pickle
import sys

import numpy as np


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
    from gui.level.level_scene import CLIP_HZ
    if not vram:
        return None, ()
    sources = {"resident": scene.resident.get(p.RESIDENT_SPRT_ID)}
    own = scene.by_id.get(p.AREA_SPRT_ID)
    if own is not None:
        sources["area"] = (scene.dat_start, own)
    banks = {name: p.SpriteBank(scene.dat_path, where[0], *where[1], vram)
             for name, where in sources.items() if where is not None}
    extra, steps, units = [], {}, {}
    for index, polygons in scene.captured_billboards.items():
        ripped = sprite_rip.rip_polygons(polygons, vram, 1000.0 / CLIP_HZ, return_scale=True)
        if not ripped:
            continue
        frames, units[index] = ripped
        steps[index] = []
        for n, (picture, _ms) in enumerate(frames):
            key = ("captured", index, n)
            extra.append((key, np.asarray(picture, np.uint8), picture.width / 2, picture.height / 2))
            steps[index].append(key)
    atlas, placed = p.build_atlas(banks, p.wanted_frames(scene.instances), extra=extra)
    quads = p.billboards(scene.instances, placed)
    for index, keys in steps.items():
        instance = scene.instances[index]
        frames = tuple((placed[k], 1) for k in keys if k in placed)
        if frames:
            quads.append(p.Billboard(index=index, x=instance.x, y=instance.y, z=instance.z,
                steps=frames, loops=True, units=float(units[index]),
                blend=scene.captured_billboard_blends.get(index)))
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
    with contextlib.redirect_stdout(sys.stderr):
        scene = LevelScene().load(*args, publish=publish)
        publish(scene, "Ready", complete=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

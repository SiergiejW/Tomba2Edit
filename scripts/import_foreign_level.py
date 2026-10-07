"""Put another PS1 game's level into Town of the Fishermen (US retail).

    python scripts/import_foreign_level.py edited.obj --vram source.vram --out mods/MyLevel
           [--disc "bincue/... (Track 1).bin"] [--state savestate] [--colours extract.json]
           [--scale 5] [--origin 3721 700 1500] [--game tomba2]

What the OBJ has to be: the model as its own game has it - its units, its
UVs, triangles and quads - with every material named for the texture page
and palette it uses in that game's VRAM:

    Page_0011_CLUT_7A12     page 0x11, CLUT word 0x7A12
    Page_-001_CLUT_0000     no texture

and `--vram` that game's VRAM with the level loaded (1024x512 halfwords,
raw). Nothing is repainted: the texels and palettes are copied as they
are, into the space the replaced Town scenery leaves (game/level_textures).

What it does, in order: places the model (`--scale`, `--origin`), builds
its packets, installs its art, rebuilds the MDAT through the Blender
importer's own cell binning, opens the DRWB for it, and patches MAIN.EXE
(game/primitive_buffer) and A00.BIN (game/view_wedge). Collision, actors,
scripts and camera stay Town's.

`--state` is a savestate taken in Town: with it the VRAM no chunk writes
is used too. `--colours` is the source game's polygon dump, for an OBJ
exported without vertex colours. `--disc` writes a patched copy of that
data track; without it the changed files are written loose.
"""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AREA = 4                      # Town of the Fishermen's chunk
POOL = 0x73000                # the area's RAM, 0x8018A000..0x801FD000
MATERIAL = re.compile(r"Page_(-001|[0-9A-Fa-f]{4})_CLUT_([0-9A-Fa-f]{4})(?:\.\d+)?")
VRAM_BYTES = 1024 * 512 * 2


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("obj", type=Path)
    parser.add_argument("--vram", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--game", type=Path, default=ROOT / "tomba2")
    parser.add_argument("--disc", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--colours", type=Path)
    parser.add_argument("--scale", type=float, default=5.0)
    parser.add_argument("--origin", type=float, nargs=3, default=(3721, 700, 1500))
    parser.add_argument("--tries", type=int, default=60)
    args = parser.parse_args()

    import numpy as np
    from formats.archive import format_detect, repacker
    repacker.debug_print = lambda *a, **k: None
    from formats.drawmaps.drwb_parser import reveal
    from formats.images import img_codec, img_writer
    from formats.models.obj_exchange import (ExchangeError, Face, _body, _encode,
                                             import_obj, read_obj, records, write_obj)
    from game import level_textures, primitive_buffer, view_wedge
    from psx import state_vram, vram_map

    idx_path, dat_path, img_path = (args.game / "CD" / n for n in ("TOMBA2.IDX", "TOMBA2.DAT", "TOMBA2.IMG"))
    overlay_path, exe_path = args.game / "BIN" / "A00.BIN", args.game / "MAIN.EXE"
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    report = {"obj": str(args.obj)}

    # --- the model, placed. Turned a quarter so its length runs along Town's.
    ox, oy, oz = args.origin
    place = lambda p: (ox - p[2], oy + p[1], oz + p[0])
    faces = read_obj(args.obj, scale=args.scale, place=place)
    source_vram = bytearray(args.vram.read_bytes())
    if len(source_vram) != VRAM_BYTES:
        sys.exit(f"{args.vram} is {len(source_vram)} bytes; a raw VRAM is {VRAM_BYTES}")
    unknown = sorted({f.material for f in faces if not MATERIAL.fullmatch(f.material)})
    if unknown:
        sys.exit("materials that do not name a page and palette (Page_XXXX_CLUT_XXXX): " + ", ".join(unknown[:8]))
    used = {int(MATERIAL.fullmatch(f.material).group(1), 16) & 31 for f in faces
            if MATERIAL.fullmatch(f.material).group(1) != "-001"}
    # one white texel and a palette for it, on a page the model does not use
    white = next(p for p in range(32) if p not in used)
    wx, wy = (white % 16) * 64, (white // 16) * 256
    white_clut = (wy + 255) * 64 + wx // 16
    struct.pack_into("<H", source_vram, wy * 2048 + wx * 2, 0x1111)
    source_vram[(wy + 255) * 2048 + wx * 2:(wy + 255) * 2048 + wx * 2 + 32] = \
        struct.pack("<16H", *([0, 0x7FFF] + [0] * 14))

    lookup = {}
    if args.colours:
        for poly in json.loads(args.colours.read_text()):
            for xyz, uv, colour in zip(poly["vertices"], poly["uv"], poly["colors"]):
                at = tuple(round(v) for v in place(tuple(c * args.scale for c in xyz)))
                texel = (round(uv[0] * 256 - .5), round(uv[1] * 256 - .5)) if poly["page"] >= 0 else None
                lookup[(at, texel, poly["page"], poly["clut"])] = tuple(
                    max(0, min(15, round(c * 9))) for c in colour)
    packets, found, missing = [], 0, 0
    for face in faces:
        group = MATERIAL.fullmatch(face.material)
        textured = group.group(1) != "-001"
        page = int(group.group(1), 16) if textured else white
        clut = int(group.group(2), 16) if textured else white_clut
        if textured and any(uv is None for uv in face.uvs):
            sys.exit(f"a face of {face.material} has no UVs")
        uvs = face.uvs if textured else tuple((0, 0) for _ in face.vertices)
        colours = list(face.colors)
        if any(c is None for c in colours):
            for n, (xyz, uv) in enumerate(zip(face.vertices, face.uvs)):
                key = (xyz, uv if textured else None, page if textured else -1, clut if textured else 0)
                colours[n] = lookup.get(key)
                found += colours[n] is not None
                missing += colours[n] is None
        code = 0x34 if len(face.vertices) == 3 else 0x3C
        try:
            packets.append(_encode(Face(face.vertices, uvs, tuple(colours),
                                        f"T2_{code:02X}_00_{clut:04X}_{page:04X}"), {}))
        except ExchangeError as exc:
            sys.exit(f"a face of {face.material} in {face.object or 'the model'}: {exc}")
    blob = struct.pack("<HHI", 0, 1, 8) + _body(packets, b"\0" * 16)
    report["model"] = {"triangles": sum(len(f.vertices) == 3 for f in faces),
                       "quads": sum(len(f.vertices) == 4 for f in faces),
                       "materials": len({f.material for f in faces}),
                       "vertex colours": ("in the OBJ" if not (found or missing) else
                                          f"{found} corners from --colours, {missing} neutral")}

    # --- Town's files
    chunk = repacker.parse_idx(idx_path)[AREA]
    pointers = chunk["sdat_pointers"]
    dat = dat_path.read_bytes()
    slots = {}
    for slot, (_id, offset) in enumerate(pointers):
        end = pointers[slot + 1][1] if slot + 1 < len(pointers) else chunk["dat_end"] - chunk["dat_start"]
        match = format_detect.best(dat[chunk["dat_start"] + offset:chunk["dat_start"] + end])
        if match and match.kind in ("MDAT", "DRWB"):
            slots.setdefault(match.kind, (slot, chunk["dat_start"] + offset, end - offset))
    mdat_slot, mdat_at, mdat_size = slots["MDAT"]
    drwb_slot, drwb_at, drwb_size = slots["DRWB"]

    idx, img = idx_path.read_bytes(), img_path.read_bytes()
    shards = vram_map.chunk_shards(idx_path, img_path)

    def chunk_vram(area):
        s, e = struct.unpack_from("<II", idx, area * 2048)
        data = bytearray(VRAM_BYTES)
        for (x, y, w, h, _packed), pixels in img_codec.decompress_chunk(img[s:e]):
            for row in range(h):
                data[(y + row) * 2048 + x * 2:(y + row) * 2048 + (x + w) * 2] = pixels[row * w * 2:(row + 1) * w * 2]
        return data

    state = state_vram.read(args.state, chunk_vram(1)) if args.state else None
    usage = level_textures.usage(idx_path, dat_path, img_path, AREA, mdat_at,
                                 overlay=str(overlay_path), state=state)
    report["space"] = usage.counts()
    loaded = vram_map.loaded_vram(shards, chunk_vram, AREA)
    say = lambda n, placed, left: print(f"   packing, try {n}: {placed} placed, {left} without room", flush=True)
    target, new_shards, vram, textures = level_textures.install(
        blob, bytes(source_vram), usage.free, loaded, tries=args.tries, progress=say)
    report["textures"] = textures

    # --- art, into the uploads the chunk already has
    s, e = struct.unpack_from("<II", idx, AREA * 2048)
    updated = img_writer.paint(img[s:e], img_writer.merged(new_shards))
    built = np.zeros((512, 1024), np.uint16)
    for (x, y, w, h, _packed), pixels in img_codec.decompress_chunk(updated):
        built[y:y + h, x:x + w] = np.frombuffer(pixels[:w * h * 2], "<u2").reshape(h, w)
    now = vram_map.claims_of({AREA: img_codec.read_chunk_header(updated)[0]}, (AREA,))
    if not (built[now] == np.frombuffer(bytes(vram), "<u2").reshape(512, 1024)[now]).all():
        sys.exit("the rewritten texture chunk does not decode to the planned VRAM")
    img_writer.rebuild(idx_path, img_path, {AREA: updated}, out / "textures.IDX", out / "TOMBA2.IMG")
    report["texture chunk"] = {"bytes": len(updated), "was": e - s, "limit": img_writer.MAX_CHUNK}

    # --- geometry, through the importer's own cell binning
    library = records(target, "SMST", 0)
    # Also what to open in Blender to keep editing: placed, with the level's
    # own T2 materials, and importable into the built disc with the button.
    staged = out / "Blender" / "level.obj"
    write_obj(staged, [Face(r.face.vertices, r.face.uvs, r.face.colors, r.face.material, "Level")
                       for r in library], vram)
    room = (POOL - (chunk["dat_end"] - chunk["dat_start"])) // 0x800 * 0x800
    try:
        result = import_obj(staged, dat[mdat_at:mdat_at + mdat_size], "MDAT", max_growth=room,
                            material_library=library, growth_alignment=2048)
    except ExchangeError as exc:
        sys.exit(f"geometry: {exc}")
    mask = reveal(dat[drwb_at:drwb_at + drwb_size], result.new_cells)
    report["geometry"] = {"triangles": result.triangles, "quads": result.quads, "bytes": len(result.data),
                          "was": mdat_size, "drawmap cells opened": len(result.new_cells),
                          "warning": result.warning}
    edits = [{"area": AREA, "file_idx": mdat_slot, "data": result.data},
             {"area": AREA, "file_idx": drwb_slot, "data": mask}]
    repacker.repack_files(dat_path, out / "textures.IDX", edits, out / "TOMBA2.DAT", out / "TOMBA2.IDX")
    (out / "textures.IDX").unlink()
    after = repacker.parse_idx(out / "TOMBA2.IDX")[AREA]
    if after["dat_end"] - after["dat_start"] > POOL:
        sys.exit("the area no longer fits its RAM")

    # --- code
    (out / "MAIN.EXE").write_bytes(primitive_buffer.apply(exe_path.read_bytes()))
    (out / "A00.BIN").write_bytes(view_wedge.narrow(overlay_path.read_bytes()))
    report["patches"] = ["MAIN.EXE: primitive buffer", f"A00.BIN: view half-angle {view_wedge.NARROW * 360 / 4096:.0f} degrees"]

    if args.disc:
        from formats.archive.bin_writer import patch_track, write_cue
        target_bin = out / (out.name + ".bin")
        if target_bin.exists():
            target_bin.unlink()
        names = ("MAIN.EXE", "A00.BIN", "TOMBA2.DAT", "TOMBA2.IDX", "TOMBA2.IMG")
        notes = patch_track(str(args.disc), str(target_bin), {n: (out / n).read_bytes() for n in names})
        # The audio track goes beside it, so the folder plays wherever it is moved.
        audio = Path(str(args.disc).replace("(Track 1)", "(Track 2)"))
        tracks = [(target_bin.name, "MODE2/2352")]
        if audio != args.disc and audio.exists():
            import shutil
            copy = out / (out.name + "-Audio.bin")
            if not copy.exists():
                shutil.copyfile(audio, copy)
            tracks.append((copy.name, "AUDIO"))
        write_cue(str(out / (out.name + ".cue")), tracks)
        report["disc"] = {"file": target_bin.name, "notes": notes,
                          "sha256": hashlib.sha256(target_bin.read_bytes()).hexdigest()}
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

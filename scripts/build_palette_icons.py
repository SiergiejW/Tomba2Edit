"""Render the Level Editor palette's preset icons from US retail game data.

    python scripts/build_palette_icons.py [tomba2 folder]

Every icon is the game's own art, not a drawing:

    chest    red chest model (resident file 1, groups 3+4, lid at its
             native offset), rendered isometrically on the CPU
    crystal  100 AP crystal - resident sprite 6 recoloured with reward 4's
             CLUT, exactly what the game draws
    apple    red apple - resident sprite 0 with reward 0's CLUT
    item     Red Key - its ground sprite, Fishermen's own sprite file frame 9
    weapon   wooden Boomerang - the 3D model the game spawns for reward 18:
             a model pickup reads the reward's palette field as the model
             file (1, resident) and its sequence as the group (16)

Textures come from Fishermen's VRAM with AREA_01's (the resident chunk)
filled in underneath, the same merge the Level Editor draws with.
Output: icons/palette/<kind>.png, 176x144 (the palette shows them at half).
"""
import os
import struct
import sys
from dataclasses import replace

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from formats.images.img_viewer import chunk_bounds          # noqa: E402
from formats.models.smst_parser import parse_smst            # noqa: E402
from formats.sprites import pickup_art, sprt_render          # noqa: E402
from formats.sprites.sprt_parser import load_sprt            # noqa: E402
from gui.level.level_scene import view_offset                # noqa: E402
from psx.vram_viewer import decode_vram_bytes                # noqa: E402
import retail_icon_render                                    # noqa: E402

SIZE = (176, 144)
RESIDENT, FISHERMEN = 1, 4
SPRITES = {"crystal": (6, 4), "apple": (0, 0), "item": (9, None)}
# The Red Key's ground sprite lives only in Fishermen's own sprite file.
AREA_SPRITES = {"item": FISHERMEN}
WEAPON_REWARD = 18


def _files(idx, chunk):
    _, _, start, end, count = struct.unpack_from("<5I", idx, chunk * 0x800)
    entries = [(v >> 24, v & 0xFFFFFF) for v in
               struct.unpack_from(f"<{count}I", idx, chunk * 0x800 + 20)]
    out = {}
    for i, (fid, off) in enumerate(entries):
        nxt = entries[i + 1][1] if i + 1 < len(entries) else end - start
        out[fid] = (start + off, nxt - off)
    return out


def _vram(cd):
    def chunk(area):
        lo, hi = chunk_bounds(os.path.join(cd, "TOMBA2.IDX"), area)
        with open(os.path.join(cd, "TOMBA2.IMG"), "rb") as f:
            f.seek(lo)
            return np.frombuffer(bytes(decode_vram_bytes(f.read(hi - lo))), np.uint8)
    own, common = chunk(FISHERMEN).copy(), chunk(RESIDENT)
    own[own == 0] = common[:own.size][own == 0]
    return bytearray(own.tobytes())


def _fit(image):
    """Integer-scale pixel art to fill the icon, centred, kept crisp."""
    box = image.getbbox()
    if box:
        image = image.crop(box)
    scale = max(1, min((SIZE[0] - 16) // image.width, (SIZE[1] - 16) // image.height))
    image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    canvas.alpha_composite(image, ((SIZE[0] - image.width) // 2, (SIZE[1] - image.height) // 2))
    return canvas


def build(retail, out_dir):
    cd = os.path.join(retail, "CD")
    exe = os.path.join(retail, "MAIN.EXE")
    idx = open(os.path.join(cd, "TOMBA2.IDX"), "rb").read()
    dat_path = os.path.join(cd, "TOMBA2.DAT")
    vram = _vram(cd)
    files = _files(idx, RESIDENT)
    os.makedirs(out_dir, exist_ok=True)
    made = {}

    # The chest: its real model, body and lid where the game puts them. The
    # keyhole face is view -X; back faces culled as the GPU does, or the
    # inner walls cover the outer ones.
    at, size = files[pickup_art.CHEST_FILE]
    with open(dat_path, "rb") as f:
        f.seek(at)
        model = parse_smst(f.read(size), at)
    body, lid = pickup_art.chest_models(exe)[0]
    offsets = [view_offset(o) for o in pickup_art.chest_offsets(exe)]
    poses = {body[1]: retail_icon_render.pose(offset=offsets[0]),
             lid[1]: retail_icon_render.pose(offset=offsets[1])}
    made["chest"] = retail_icon_render.render_model(
        model, vram, groups=[body[1], lid[1]], poses=poses,
        size=SIZE, yaw=-125.0, margin=10, cull=1)

    # The weapon: the model its reward names, out of the same resident file.
    # It's a flat upright card, so it's turned in its own plane (90° clockwise).
    art = pickup_art.reward_art(exe)[WEAPON_REWARD]
    assert art.clut & 0x7FFF == pickup_art.CHEST_FILE, "weapon model is not in resident file 1"
    made["weapon"] = retail_icon_render.render_model(
        model, vram, groups=[art.sequence], size=SIZE[::-1], yaw=90.0, elevation=60.0,
        margin=14).rotate(-90, expand=True)

    # Sprites, through the same renderer and CLUT override the editor uses.
    textures = sprt_render.VRAMTextures(vram, stp=True)
    rewards = pickup_art.reward_art(exe)
    banks = {}
    for kind, (frame, reward) in SPRITES.items():
        chunk = AREA_SPRITES.get(kind, RESIDENT)
        if chunk not in banks:
            at, size = _files(idx, chunk)[0 if chunk == RESIDENT else pickup_art.AREA_BANK_FILE]
            banks[chunk] = load_sprt(dat_path, at, 0, size)
        sprite = banks[chunk].sprites[frame]
        if reward is not None and rewards[reward].recolored:
            clut = rewards[reward].clut
            sprite = replace(sprite, pieces=[replace(p, clut=clut) for p in sprite.pieces])
        image, _x, _y = sprt_render.render_sprite(sprite, textures)
        made[kind] = _fit(image)

    for kind, image in made.items():
        path = os.path.join(out_dir, f"{kind}.png")
        image.save(path)
        print(f"wrote {path}")
    return made


if __name__ == "__main__":
    retail = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tomba2")
    build(retail, os.path.join(ROOT, "icons", "palette"))

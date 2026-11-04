"""Generate palette art from the local US retail game files."""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game import game_build
from formats.models.smst_parser import load_smst
from formats.sprites import pickup_art
from gui.level.level_scene import area_files
from gui.level.load_worker import load_vram
from gui.level.pickup_sprites import SpriteBank
from scripts.retail_icon_render import render_parts, render_model


def main():
    source = ROOT / 'tomba2'
    exe, dat, idx = source/'MAIN.EXE', source/'CD/TOMBA2.DAT', source/'CD/TOMBA2.IDX'
    if game_build.use(str(exe)).name != 'us-retail':
        raise ValueError('US retail files are required')
    vram = load_vram(str(dat), str(idx), 4)
    start, own = area_files(str(idx), 4)
    common, resident = area_files(str(idx), 1)
    own = {f:(o,s) for _,f,o,s in own}
    resident = {f:(o,s) for _,f,o,s in resident}
    model = load_smst(str(dat), common+resident[1][0], resident[1][1])
    output = ROOT/'icons/placements/us-retail'
    output.mkdir(parents=True, exist_ok=True)
    parts = []
    for group, offset in zip(pickup_art.chest_models(str(exe))[0], pickup_art.chest_offsets(str(exe))):
        matrix = np.eye(4)
        matrix[:3,3] = (offset[2], -offset[1], offset[0])
        parts.append((model, group[1], matrix))
    render_parts(parts, vram, size=128).save(output/'chest.png')
    render_model(model, vram, groups=[16], size=128).save(output/'weapon.png')
    banks = {'resident':SpriteBank(str(dat), common, *resident[0], vram),
             'area':SpriteBank(str(dat), start, *own[10], vram)}
    arts = pickup_art.reward_art(str(exe), overlay=(source/'BIN/A00.BIN').read_bytes(), area=0)
    for kind, reward in [('apple',0),('crystal',4)]:
        art = arts[reward]
        image = banks[art.bank].image(art.frames[0].frame, art.clut if art.recolored else None)[0]
        fit(image).save(output/f'{kind}.png')
    fit(banks['area'].image(9)[0]).save(output/'item.png')
    metadata = {'source':'US retail', 'main_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
        'chest':{'resident_file':1,'groups':[g[1] for g in pickup_art.chest_models(str(exe))[0]],'projection':'isometric'},
        'weapon':{'resident_file':1,'group':16,'projection':'isometric'},
        'item':{'chunk':4,'file':10,'frame':9,'kind':'native sprite'},
        'crystal':{'reward':4,'kind':'native sprite'},'apple':{'reward':0,'kind':'native sprite'}}
    (output/'sources.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    sheet = Image.new('RGBA',(640,128),(30,30,30,255))
    for i,kind in enumerate(('chest','crystal','item','apple','weapon')):
        sheet.alpha_composite(Image.open(output/f'{kind}.png'),(i*128,0))
    sheet.save(ROOT/'work/retail_palette_preview.png')


def fit(image):
    image = image.convert('RGBA')
    bounds = image.getbbox()
    if bounds is None:
        raise ValueError('Retail sprite is empty')
    image = image.crop(bounds)
    scale = min(108/image.width,108/image.height)
    image = image.resize((max(1,round(image.width*scale)),max(1,round(image.height*scale))),Image.Resampling.NEAREST)
    canvas = Image.new('RGBA',(128,128))
    canvas.alpha_composite(image,((128-image.width)//2,(128-image.height)//2))
    return canvas


if __name__ == '__main__':
    main()

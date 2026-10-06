"""Check the requested US-retail MDAT and SMST through Blender's native OBJ.

python scripts/blender_geometry_roundtrip.py --dat tomba2/CD/TOMBA2.DAT
Uses local files only; no ROM or Blender add-on is included.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def native_pass(folder):
    import bpy
    for name in ('MDAT', 'SMST'):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.wm.obj_import(filepath=str(folder / f'{name}.obj'),
                              forward_axis='NEGATIVE_Z', up_axis='Y')
        bpy.ops.wm.save_as_mainfile(filepath=str(folder / f'{name}.blend'))
        bpy.ops.wm.obj_export(filepath=str(folder / f'{name}_blender.obj'),
                              forward_axis='NEGATIVE_Z', up_axis='Y', export_colors=True,
                              export_uv=True, export_materials=True, export_triangulated_mesh=False)


def main():
    if '--native' in sys.argv:
        native_pass(Path(sys.argv[sys.argv.index('--native') + 1]).resolve())
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dat', type=Path, default=ROOT / 'tomba2/CD/TOMBA2.DAT')
    parser.add_argument('--blender', type=Path, default=Path(r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'))
    parser.add_argument('--out', type=Path, default=ROOT / 'work/blender_geometry')
    parser.add_argument('--vram', type=Path, help='Optional 1024x512x16-bit loaded VRAM for texture previews')
    args = parser.parse_args()
    from formats.models.obj_exchange import export_obj, import_obj, records
    data = args.dat.read_bytes()
    vram = args.vram.read_bytes() if args.vram else None
    out = args.out.resolve()
    targets = [('MDAT', 0x53724, 0x228b8, None, 3378), ('SMST', 0x90418, 0x92c8, 61, 9)]
    for kind, at, size, part, count in targets:
        blob = data[at:at + size]
        if len(blob) != size or len(records(blob, kind, part)) != count:
            raise ValueError('The input does not contain the expected original US-retail targets.')
        export_obj(out / f'{kind}.obj', blob, kind, part, vram)
    subprocess.run([str(args.blender), '--background', '--factory-startup', '-t', '2',
                    '--python-exit-code', '1', '--python', str(Path(__file__).resolve()),
                    '--', '--native', str(out)], check=True)
    report = {}
    for kind, at, size, part, _ in targets:
        original = data[at:at + size]
        result = import_obj(out / f'{kind}_blender.obj', original, kind, part)
        if result.data != original:
            raise AssertionError(f'{kind} round trip changed bytes')
        report[kind] = {'bytes': len(original), 'part': part, 'byte_identical': True,
                        'sha256': hashlib.sha256(result.data).hexdigest()}
    (out / 'roundtrip.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

# Editing MDAT and SMST in Blender 5.2

Tomba2Edit uses Blender's built-in **Wavefront OBJ** importer and exporter.
No add-on, custom Blender format or metadata sidecar is required. Use the OBJ
buttons for geometry that will go back into the game; glTF remains available
for rendering and animation exports.

## Workflow

1. Open your disc in Tomba2Edit. Open an MDAT resource, or open an SMST and
   select **one part** in its parts list.
2. Click **Export Blender OBJ**. Keep the OBJ, MTL and PNG files together.
3. In Blender 5.2, use **File → Import → Wavefront (.obj)** with scale **1**,
   Forward **-Z**, Up **Y**. One Blender unit represents 100 game units.
4. Edit or completely replace the mesh. New topology, vertices, faces, UVs and
   vertex colours are supported. Assign the exported `T2_...` materials to new
   faces and keep their names: they identify the game's texture pages, palettes
   and draw settings. Use triangles and quads; triangulate n-gons yourself.
5. Use **File → Export → Wavefront (.obj)** with scale **1**, Forward **-Z**,
   Up **Y**, **UV Coordinates**, **Materials** and **Vertex Colors** enabled.
   Leave **Triangulated Mesh** disabled to retain quads. Export only the meshes
   being replaced. Blender exports evaluated object transforms.
6. In the same Tomba2Edit resource/part, click **Import Blender OBJ**. Inspect
   the staged result, then use **Export Project** or **Build Disc** to save it.

An MDAT import replaces its entire geometry and rebuilds its cell pointers.
An SMST import replaces only the selected part. All other part bodies stay
byte-identical, including their opaque headers and trailers. Later part offsets
change when the selected body grows. An unchanged OBJ restores the complete
original resource byte-for-byte, including unknown fields and padding.
Sector padding for SMST growth stays inside the selected part, so the other
part bodies remain identical when read back from the exported disc too.

**Collision stays original.** Visual replacements retain the original level's
walkable paths, invisible barriers, objects, scripts and camera. Tomba can walk
through or behind new scenery when it does not match those paths.

## Limits and validation

* Positions round to integer game units and must fit signed 16-bit storage.
  UVs address the material's 256×256 PSX page and must stay inside 0..1.
* Vertex colours use the game's 0–15 range, represented as 0–1 in OBJ; neutral
  is 9/15. Missing colours preserve matching original packets; new faces
  without colours receive neutral shading. Enable Vertex Colors on export.
* Matching tolerates reordered faces/vertices and a different first corner of
  a face. Winding is significant. Changing it changes which side is visible.
* New MDAT faces must fit the original grid. Faces spanning more than two cells
  must be subdivided. New faces go into the cell containing their horizontal
  centre. US retail spacing comes from the game (640 units, or 1024 in AREA_07),
  so replacing a level with a nearly flat mesh does not lose its grid spacing.
* Unknown materials, missing UVs, invalid indices, non-finite/out-of-range
  values and excessive data are rejected before staging.
* Growth is disabled for unknown allocations. US retail Town of Fishermen uses
  the known `0x8018A000..0x801FD000` area allocation. The budget includes all
  staged edits and the repacker's sector padding, including reusable slack.
* A density check rejects a new SMST part or 7×7-cell MDAT patch whose potential
  GPU packet size exceeds the 81,920-byte frame buffer or the original's higher
  density. This catches severely concentrated scenes. It is **not** a complete
  simulation of the camera culler, actor instances, effects or UI. Test changed
  scenes in-game; passing an import does not guarantee every possible edit is
  crash-free.

The workflow uses textures already installed in the target game. Changing an
exported PNG or assigning an arbitrary Blender material does not install new
texture data. External model ports need their textures placed into destination
VRAM separately. That step is already done for the example below.

## Requested Tomba 1 replacements

The portable edited ROM is kept in **`mods/Tomba1-Village-Part61`**. Open its
`Tomba2-Blender-Demo.cue` in an emulator, or its `.bin` in Tomba2Edit. Both tracks
are included, so the folder can be moved or backed up together. Editable Blender
projects, OBJ files and textures are in its `Blender` subfolder.

Working files and detailed diagnostics remain in `work/blender_geometry`:

| File | Contents |
| --- | --- |
| `Village_of_All_Beginnings_for_Tomba2.blend` | All 574 triangles and 3,147 quads of the Tomba 1 level, scaled and rotated along the Town grid's long axis |
| `WMD_52FF_Model001_for_Tomba2.blend` | The requested 11-quad model from A006.GAM +0x160C0, WMD 0x52FF, Model 001 |
| `MDAT.blend`, `SMST.blend` | Original Town MDAT and original SMST part 61 for the identity smoke test |
| `Tomba2-Blender-Demo.cue` and `.bin` | Rebuilt disc containing both replacements |
| `TOMBA2.DAT`, `TOMBA2.IDX`, `TOMBA2.IMG` | Repacked archives |
| `validation.json`, `roundtrip.json`, `runtime_validation.json` | Byte comparisons and test results |

The SMST destination is **part 61**, originally `0x96B90` (`0x90418 + 0x6778`),
including the selected quad #598 / slot 3 at `0x96C48`. **Part 62 remains
original.** The bank now has 11 quads in part 61; its other 86 parts are intact.

Open the rebuilt BIN in Tomba2Edit to continue editing the installed replacements.
Use the corresponding Blender file, export an OBJ, and import it into that
rebuilt disc's MDAT or SMST part 61. The new materials are already installed
there. They will be rejected if imported directly into an untouched original
disc that does not contain their textures. Repacking relocates resource
addresses, so use the part number and resource type on the rebuilt disc.

Both models passed through Blender **5.2.1 LTS** using native OBJ. All source
polygons were retained. Textures were reduced to one quarter of their width
and height to fit unused VRAM, retaining their original palettes. Existing
texture uploads remain unchanged. The CUE references the original audio track
under `../../bincue`; keep that relative path intact.

## Verification performed

* Original MDAT: **141,496 bytes identical** after native Blender import/export.
* Original complete SMST bank: **37,576 bytes identical** after exporting and
  reimporting part 61 through Blender.
* Modified disc: all **86 other SMST parts**, collision bytes and **568 other
  SDAT entries** retain their original contents.
* **15 regression tests passed**, including identity, changed topology,
  protected parts, shrink/regrow, invalid input, area budgets, dense geometry
  and texture upload alignment. Real Qt panels passed export, identity import,
  changed import, selection and staging checks. PyInstaller rebuilt the EXE.
* The final part-61 disc cold-booted in the Beetle PSX software core for **18,000
  frames**, then ran **8,100 more frames** with movement, jumps and menu input.
  Both replacement blobs matched the live game RAM. Sampled frame-packet usage
  peaked at **71,768 / 81,920 bytes**; player movement and rendering continued.
  This validates that Town smoke route, not a complete playthrough or hardware
  compatibility guarantee.

Runtime testing found and fixed an odd-halfword texture-upload allocation that
caused a PS1 alignment exception. A smaller, densely packed village layout also
overran the primitive buffer; the delivered layout spreads the full model over
more cells and passes the density check and gameplay test.

## Rechecking locally

```powershell
python scripts/blender_geometry_roundtrip.py --dat tomba2/CD/TOMBA2.DAT
python -m unittest discover -s tests -p test_obj_exchange.py -v
python -m PyInstaller main.spec --noconfirm
```

The round-trip script accepts `--blender`, `--out` and an optional `--vram` dump
for texture previews. The local tests and example preparation/rebuild scripts
remain in this checkout; source extraction, native Blender passes and the
emulator harness are under `work/blender_geometry`. No ROM, BIOS or emulator
binary is included in source control.

Blender's settings are described in the
[official OBJ manual](https://docs.blender.org/manual/en/latest/files/import_export/obj.html).

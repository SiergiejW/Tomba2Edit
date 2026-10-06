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
* A drawmap cell holds at most 255 triangles and 255 quads: Town's draw
  routine reads each count as one byte. The disc's largest cell has 88.
* An SMST part is limited to 4,096 potential GPU-packet bytes, or its original
  higher cost, because the game may draw multiple instances.
* An MDAT denser than the original is **imported, with a warning**. The mesh
  is not simplified for you. See the next section for what the limit really is.

## The frame buffer, the drawmap and the DRWB

Every packet of a frame (actors, Tomba, scenery, effects, backdrop, UI) goes
into one 81,920-byte buffer. Nothing checks it, and Tomba's own state sits
right after it. The MDAT is one tenant among several: at Town's start the
stock game uses 29,912 bytes, of which 9,880 are MDAT scenery.

The stock game runs close to the limit. In DuckStation with the widescreen
hack, the untouched disc peaks at **80,432 bytes** in the Town opening. That,
not the mesh, is why the first full-detail Village port froze, and why the
shipped one was decimated to fit under Town's own load.

How Town decides what to draw (`formats/drawmaps/visibility.py`, ported from
the decomp and checked against a savestate):

1. A view triangle from the camera, 80° wide and 14,080 units deep at Town's
   start, is laid over the drawmap. Cells inside it are listed if the **DRWB**
   allows it: the DRWB's low nibble is the region of the cell Tomba stands in,
   its high nibble the regions a cell is drawn from.
2. Each listed polygon is transformed and dropped if it faces away or is off
   screen. Only what survives uses buffer.

So the drawmap and DRWB cannot shrink what is genuinely on screen. What they
decide is how many polygons the CPU transforms, and that is what makes frames
late. Importing an MDAT into US Town also stages the DRWB, opened for every
cell that holds no original packet, so new geometry does not vanish when
Tomba walks into another region.

Two patches (US retail) go with a level denser than Town:

* `game/primitive_buffer.py` (MAIN.EXE). A frame that follows one over 65,536
  bytes waits for the GPU and may then use all 163,840 bytes; lighter frames
  are untouched. Needed even in 4:3: the Village's opening reaches 95,700.
* `game/view_wedge.py` (A00.BIN). The view triangle is 40° either side of the
  heading, the screen shows 24.6° (4:3) or 31.4° (16:9 hack). Along the
  opening route Town transforms 860 to 1,170 polygons a frame, the Village
  1,400 to 2,000. At 32° the Village is 760 to 1,480 and no cell with
  something on screen is dropped in either aspect ratio.

```powershell
python -m game.primitive_buffer MAIN.EXE MAIN.patched.EXE
python -m game.view_wedge A00.BIN A00.narrow.BIN
```

Frames on time along the walking route (x 3200..7926, 2,400 game frames,
DuckStation 0.1-12074, same input):

| | 4:3 | 16:9 hack |
| --- | --- | --- |
| Stock disc | 100% | 100% |
| Full-detail Village, buffer patch only | 70% | 65% |
| Full-detail Village, both patches (the built disc) | 91% | 72% |

What is still late are the frames over 65,536 bytes, which wait for the GPU:
160 of them in 4:3, 639 in 16:9, between x 5200 and 7600 where Town's own
actors already use most of the buffer. Frames under that run as stock does.
A frame that jumps from under 65,536 to over 81,920 in one step is not
covered. None did in any run.

## Textures for a replaced level

`mods/Tomba1-Village-FullDetail` carries the Village's art at full size.
How it got there, and what the tool should do by itself:

* **Where art may go.** What only the replaced MDAT read, plus spare VRAM,
  plus the gutters between the replaced tiles. For Town that is 50,900
  halfwords freed, 10,623 spare, 1,134 of gutters. Readers are every MDAT,
  SMST (trail files too), SPRT and BGMP of the area; the overlay's animated
  palettes and what a savestate shows the game uploading are kept clear.
  576 recorded frames of the stock game sampled none of the freed cells from
  anything but the MDAT, and 576 of the new build sampled none of the changed
  ones from anything but the Village.
* **Where it may not.** 41,552 more halfwords of the level's chunk are read by
  nothing the disc names, but 5,018 of those were sampled in those frames, by
  things drawn from code. That space is unverified, not free.
* **A chunk may not pass 0x53000 bytes** (`img_writer.MAX_CHUNK`). The game
  reads no further; a chunk grown to 0x5F800 lost its last 24 uploads in
  game, palettes among them, while the tool's own view looked right.
  `img_writer.paint()` writes into the uploads a chunk already has instead of
  adding new ones, which is what keeps a level's worth of art under it.
* **Result.** 56 of 61 texture rectangles at full size and all 72 palettes;
  five rectangles (11 faces) found no room and were shrunk to between 8/16
  and 12/16. Every other face reads exactly the texels it read in Tomba 1.

The scripts that did this are in `work/blender_geometry/claude_session`; it
is not yet part of the importer.

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
| `mods/Tomba1-Village-Part61/Blender/Village_of_All_Beginnings_for_Tomba2.blend` | Playable Village replacement, simplified in Blender to 2,228 triangles and fitted along the Town grid's long axis |
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

Both models passed through Blender **5.2.1 LTS** using native OBJ. The Village
uses a simplified mesh to fit the game's drawing budget; the small SMST model
retains all 11 original quads. Textures were reduced to one quarter of their
width and height to fit unused VRAM, retaining their original palettes.
Existing texture uploads remain unchanged. The portable CUE references both
tracks within its own folder; keep those three files together.

The portable Blender files are exported from the final installed packets, have
packed textures, and reimport unchanged byte-for-byte into the corrected ROM.
Use these files for further editing. Earlier full-resolution working files
are diagnostic/source material and can exceed the drawing budget.

## Verification performed

* Original MDAT: **141,496 bytes identical** after native Blender import/export.
* Original complete SMST bank: **37,576 bytes identical** after exporting and
  reimporting part 61 through Blender.
* Modified disc: all **86 other SMST parts**, collision bytes and **568 other
  SDAT entries** retain their original contents.
* **18 regression tests passed**, including identity, changed topology,
  protected parts, shrink/regrow, invalid input, area budgets, dense geometry
  texture upload alignment, wider camera coverage, actor/UI headroom and SMST
  instance budgets. Real Qt panels passed export, identity import,
  changed import, selection and staging checks. PyInstaller rebuilt the EXE.
* The corrected disc cold-booted in Beetle PSX with **16:9 widescreen enabled**,
  completed the opening dialogue and ran **10,200 gameplay frames** with
  movement, jumps and menu input. Per-frame checks peaked at **67,500 / 81,920
  bytes** on that gameplay route. A further 4:3 route also passed.
* **DuckStation 0.1-12074**, using the user's widescreen and 9× resolution
  overrides, passed **1,800 completed game frames** of walking and jumping.
  A debugger breakpoint after drawing checked the arena on every game frame:
  peak **68,048 / 81,920 bytes**. Controller input was supplied through a
  temporary emulated button-reader hook; the hook was restored after testing
  and is not included in the disc. Both model blobs and the original collision
  matched live RAM in DuckStation and Beetle.
* This validates the opening Town routes tested, not a complete playthrough or
  hardware compatibility guarantee. See the portable `runtime_validation.json`
  for counts, bounds, hashes and the exact scope.

Runtime testing first fixed an odd-halfword texture-upload allocation that
caused a PS1 alignment exception. The subsequent full-resolution Village port
passed an insufficient 4:3 smoke test but froze after walking in widescreen.
The wider view overflowed the native primitive arena and overwrote Tomba's
state; this was reproduced in both emulators. That revision is superseded.
The decimated mesh in `mods/Tomba1-Village-Part61` reduces potential scenery
packet output from 186,604 to 89,120 bytes before culling. That was the wrong
fix: the buffer was already 98% full without the Village (see "The frame
buffer, the drawmap and the DRWB"). The importer no longer rejects the
full-detail OBJ; it warns, and the full-detail build uses the MAIN.EXE patch.
The decimated build leaves collision, the other 86 SMST parts and the game's
executable intact.
Start the corrected CUE from a fresh boot: an old save state contains the old
geometry and can restore the crash even when the disc has been replaced.

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

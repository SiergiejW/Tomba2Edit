# Editing MDAT and SMST in Blender 5.2

Tomba2Edit uses Blender's built-in **Wavefront OBJ** importer and exporter.
No add-on, custom Blender format or metadata sidecar is required. Geometry
goes out as OBJ and comes back as OBJ or as **glTF (.glb)**; a model from
somewhere else is best brought in as a .glb, which carries its textures and
vertex colours inside it (see below).

## Workflow

1. Open your disc in Tomba2Edit. Open an MDAT resource, or open an SMST and
   select **one part** in its parts list.
2. Click **Export Blender OBJ**. Keep the OBJ, MTL and PNG files together.
3. In Blender 5.2, use **File â†’ Import â†’ Wavefront (.obj)** with scale **1**,
   Forward **-Z**, Up **Y**. One Blender unit represents 100 game units.
4. Edit or completely replace the mesh. New topology, vertices, faces, UVs and
   vertex colours are supported. Assign the exported `T2_...` materials to new
   faces and keep their names: they identify the game's texture pages, palettes
   and draw settings. Use triangles and quads; triangulate n-gons yourself.
5. Use **File â†’ Export â†’ Wavefront (.obj)** with scale **1**, Forward **-Z**,
   Up **Y**, **UV Coordinates**, **Materials** and **Vertex Colors** enabled.
   Leave **Triangulated Mesh** disabled to retain quads. Export only the meshes
   being replaced. Blender exports evaluated object transforms.
6. In the same Tomba2Edit resource/part, click **Import from Blender**. The import
   window lists the names from Blender's Outliner. Click a name to highlight
   its wireframe in orange; tick the objects to import. Gray shows the original
   target and cyan shows the replacement. Unchanged exports keep their original
   placement and T2 materials. Click **Import selected objects**, inspect the
   staged result, then use **Export Project** or **Build Disc** to save it.

## Importing your own model through the interface

The same **Import from Blender** button accepts foreign meshes. No command-line
conversion is required. **Keep exported placement is the default** for every
model: 100 game units per exported unit, with no added rotation or translation.
Adjust **Scale**, **Turn around vertical axis**, and **Move X/Y/Z** only when wanted.
Use the Top and Front views to check placement. **Fit selected objects to target**
fits the checked objects together. Drawmap rebuilding is automatic. Oversized
new or changed MDAT faces are split automatically, interpolating UVs and vertex
colours. This does not reduce polygon density or change collision.

A Tomba2Edit export keeps the target's installed materials and its
byte-identical round-trip path; faces on `T2_...` materials keep them even in
a file that also holds foreign ones. For everything else the window has three
choices under **Textures and vertex colours**, and says under each what it
found.

### Which file format

What Blender 5.2 writes for a scene that came from another PS1 tool's GLB
(packed images, a colour attribute), measured:

| Export | Vertex colours | Textures |
| --- | --- | --- |
| OBJ, defaults | none | none: a packed image has no file for the MTL to name |
| OBJ, **Geometry: Colors** ticked | yes | none |
| glTF 2.0 (.glb), defaults | yes | every image, embedded |

So export a foreign model as **.glb**. It holds only triangles; the importer
joins them back into quads where two share an edge and agree on UV and colour
there, along the diagonal the game draws a quad with, so the picture is the
same and the frame costs 52 bytes instead of 80. The Village came back as 758
triangles and 3,055 quads from 6,868 triangles.

### Textures

* **Automatic (recommended).** The model's own images first: embedded in a
  .glb, or the files an MTL names. Where they do not cover it, a `.vram` with
  the model's name, then a `.glb` in the same folder that has images under the
  same material names (so an OBJ exported beside the GLB it was made from just
  works). The window names the file it took and how many materials it covers.
* **Images from a file I choose.** A .glb/.gltf or an .mtl.
* **Exact copy from the source game's VRAM dump.** A raw 1,048,576-byte dump;
  materials named `Page_XXXX_CLUT_XXXX` read their texels and palette from it.
* **No textures: plain material colours.**

Nothing is imported untextured without asking. Pictures of one PS1 page seen
through several CLUTs (`Page_0011_CLUT_79D2`, `Page_0011_CLUT_7A12`...) are
put back as that one 4-bit page with a palette each, so they cost what they
cost in the source game and match it texel for texel: 0 of 1,985,412 texels
differ from the VRAM dump for the Village. Any other picture is reduced to
16 colours, at up to 256 texels a side; the result line says which happened.

**`mods/Blender-Import-Example`** is the OBJ route with real image files:
`edited.obj`, `edited.mtl` and 63 PNGs. Keep the folder together and select
its `edited.obj`.

### Texture room

New textures go where the replaced model's were. **Also VRAM a savestate
shows the game leaves empty** adds what no file of the level loads into and a
savestate taken there shows unused (10,623 halfwords in Town). If something
still has no room at full size, the window says how many regions and how
small, and asks before anything is shrunk or written. The full Village: 33
of 94 regions shrunk without a savestate, 4 of 60 with one.

### Vertex colours

Read automatically. This program's OBJ writes 9/15 for unshaded; a PS1
tool's GLB writes 1.0, and Blender re-encodes on the way out of an OBJ
(0.695 becomes 0.851) but not a GLB, so the scale is picked from the
materials and the file type. A file without colours imports evenly lit, and
the window says so and why.

Texture preparation includes other staged edits. It only reuses texture space
freed by the replaced resource and verified gaps between those tiles; other
resources and animated palettes are protected. For SMST, the other parts'
texture references are protected too. Some texture regions may be reduced to
fit, and the result reports how many. If there is insufficient room, importing
stops before changing the project. New textures for shared TRAIL models are not
supported by this window; editing a Tomba2Edit export with its existing
materials remains supported for those models.

Cancelling the window changes nothing. Successful texture import updates the
working project's IDX/IMG pair and stages geometry for normal project/disc
export; it does not overwrite your original ROM. Save the result with
**Build Disc**. A successful import is not a full gameplay stability test:
the rendering limits below still apply, especially to dense replacement levels.

Validated on 2026-10-07: 27 automated interchange/placement/transaction tests;
real Qt button tests for original MDAT and SMST byte identity; imports of the
supplied colour-only OBJ and the PNG-backed example; colour and image imports
into SMST part 61 with all other 86 part bodies unchanged. The rebuilt IMG is
decoded and compared with the planned pixels before it can be saved. The new
example has not had a gameplay stability run; earlier runtime results below
refer to the named, separately built ROMs.

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
  UVs address the material's 256Ă—256 PSX page and must stay inside 0..1.
* Vertex colours use the game's 0â€“15 range, represented as 0â€“1 in OBJ; neutral
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

### Knowing before the game freezes

A frozen game after an import is this buffer. A real case, from the resume
state DuckStation saved on exit: draw packets across Tomba's struct at
0x800E7E68. The level had *fewer* faces than Town (2,621 against 3,378), and
the area's RAM was untouched. What counts is how much one camera catches, on
top of everything else in the frame: in the frame that overflowed, the stock
game writes 73,692 bytes, and the import's scenery cost 8,596 more than
Town's does from there - 82,288, which is 368 too many.

`game/frame_budget.py` forecasts it. Collision, actors, scripts and camera
stay Town's when the MDAT is replaced, so every frame's camera is the stock
game's. `game/town_views.json` holds 8,799 of them, recorded from the US disc
through the opening and a walk each way along the street, with what each
frame wrote and how much of that was scenery. A new MDAT's frame is the
rest, as recorded, plus its own scenery under that camera by the game's own
rule. Against the disc above, frame for frame over 827 frames: never more
than 104 bytes out.

* The import window has **Check this selection against the game's frame
  buffer**. It takes a few seconds, runs again before an import, and names
  the objects that cost most in the heaviest frame. For the case above:
  unticking the two it named first was enough for 4:3 (peak 77,988), four
  for an emulator's 16:9 hack as well (76,932) - no patch, no slowdown.
* **Build Disc** asks when Town does not fit, and can add
  `game/primitive_buffer.py`'s patch to MAIN.EXE. That removes the freeze;
  frames over 65,536 bytes then wait for the GPU, which is slower there.
* The widescreen hack puts more on screen. Its forecast covers the scenery
  only, so it is a floor: 79,752 for the stock opening, 80,432 measured.
* It is blind to what was not recorded: other parts of the level, other
  events. US retail Town only.

    python -m game.frame_budget MDAT.bin DRWB.bin

How Town decides what to draw (`formats/drawmaps/visibility.py`, ported from
the decomp and checked against a savestate):

1. A view triangle from the camera, 80Â° wide and 14,080 units deep at Town's
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
* `game/view_wedge.py` (A00.BIN). The view triangle is 40Â° either side of the
  heading, the screen shows 24.6Â° (4:3) or 31.4Â° (16:9 hack). Along the
  opening route Town transforms 860 to 1,170 polygons a frame, the Village
  1,400 to 2,000. At 32Â° the Village is 760 to 1,480 and no cell with
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

The scripts for that earlier build are in `work/blender_geometry/claude_session`.
The normal import window now exposes texture installation as described above.
It conservatively avoids spare VRAM which would require a runtime savestate to
validate, so its packing results can differ from those earlier offline builds.
Exports retaining native `T2_...` material names preserve installed textures.
To import new artwork, give the Blender materials ordinary names and export
their image references in the MTL.

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
* **DuckStation 0.1-12074**, using the user's widescreen and 9Ă— resolution
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


## Packing and quad checks (2026-10-07)

Texture packing reports its current attempt and stage and can be cancelled
before any project files change. Unchanged free-space searches are cached.
If filling the full-size tiles first leaves an impossible layout, the allocator
starts over and reserves room for all texture regions together. Texture
reductions retain the existing one-quarter-resolution minimum and are counted
in the import result; geometry is not simplified by texture packing.

The supplied `swap.glb` contains 2,699 triangles. The importer reconstructs
1,118 quads and retains 463 triangles. A regression compares every resulting
triangle's positions, UVs, colours and material against the source GLB. GLB
cannot encode the original quad boundaries, so reconstructed pairings are not
proof of the original Blender topology. OBJ retains authored quads. Automatic
splitting of oversized quads now makes smaller quads rather than triangles.

The reported 179x114 texture failure is covered by the new reservation pass.
The supplied GLB completes preparation against the original Town resources in
about 20 seconds in the local test. Most reduced regions retain 15/16 of each
dimension; the smallest retains 8/16. Exported placement remains unchanged.

Music import preserves the source amplitude. The supplied Tomba 1 WAV changed
by less than 0.001 dB RMS in a ten-second resample/XA encode/decode test. The
US game's own mixer applies CD music attenuation (0x47FF/0x8000), plus its
Music setting (default 7 out of 9) and other runtime mixing. The editor preview
is the encoded recording, not an emulation of that live game mix. Raise the
in-game Music setting before changing the recording's gain: this WAV already
peaks at -0.18 dBFS, so a plain boost can clip its peaks.

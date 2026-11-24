# Play as another character

**Mods → Play as Another Character…** puts another body on Tomba's costume slots. He keeps every animation, ability and physics rule; only the body, its bone lengths and its art change. Code: `game/character_swap.py`, dialog `gui/widgets/character_swap_dialog.py`.

## How the game builds Tomba (US retail)

- `appearance & 0xF` (player +0x17E, set from the equipped clothes) picks a start/end pair in chunk 1's IDX trailer. 0 Tomba, 1 Squirrel Suit, 2 Wing Suit, 3 Pig Suit, 4–8 the five Pig Robes, 9 Invisible Clothes, 10 Water Pig Suit.
- That SMST is read into file slot 47 (`0x8017C800`). The next live buffer starts at `0x80182000`, so a costume can be at most `0x5800` bytes. The largest on the retail disc is `0x4FD4`.
- `f_RebuildTombaAppearanceModelParts` attaches group *i* to part *i*. The bone tables are 8-byte records in MAIN.EXE: `0x800A3FA8` (0), `0x800A4030` (1), `0x800A40C8` (2, 19 bones), `0x800A4160` (3 and 10), `0x800A41E8` (4–8), `0x800A4270` (9). Slots that share a table can only change together.
- Groups 17/18 are spare heads (they stand in for bone 1), 19 and 20 are open hands (bones 4 and 7). The Wing Suit's wing bones 17/18 draw groups 21/22.
- Tomba's TANP only rotates limbs, so a body whose rig has his topology and axes plays every clip once its bone lengths are in the table.

## What an install writes

| File | Change |
|---|---|
| TOMBA2.DAT / IDX | each chosen costume's trail file becomes a composite SMST. Spares point at the bone's own body (the game reads only through the pointer table). |
| MAIN.EXE | the costume's bone records, with the roots lifted so the feet stand where Tomba's did |
| TOMBA2.IMG | the body's art, painted into chunk 1 over texels only the replaced costumes read, then free VRAM. It never goes on the pants palettes `f_RedrawPants` uploads. |

Every other SMST on the disc is checked to still read the same texels; one that wouldn't stops the install. Tomba shares 2,688 of his 3,208 texture cells with the suits, so a body's art usually fits only when all the costumes are swapped together.

## Bodies and skeletons

- **Body**: Tabby (chunk 0x13 file 20), any character an area builds (**Find characters**), or a file from Blender.
- **Skeleton**: the body's own, Tomba's, Tabby's, or any character found in an area. Any body can use any skeleton.
- **Export for Blender** writes the body standing at rest on its skeleton, one object per part (`part_NN_name`), with its texture pages as PNGs.
- **Import from Blender** takes an OBJ or GLB of a whole character at rest:
  - Faces in an object named `part_NN` go to bone NN. Anything else goes to the bone of the nearest surface of the chosen skeleton's own body, so a joined, renamed mesh still splits correctly.
  - Each part is made relative to its joint again, so the body stands exactly as modelled on whichever rig is chosen.
  - With "Its own", a file uses the best-fitting skeleton. The dialog shows each rig's fit score.
  - Textures come from the file's own images (`obj_textures`), are packed to 4-bit, and identical palettes are folded together.
  - Vertex colours on this tool's `T2_` materials read back as exported. Other materials follow the PS1-tool rule, where white means unshaded.
- A body on a rig whose joints don't sit where its limbs bend shows gaps when animated. Pick the closest rig.

## A body per costume, and editing what is installed

- Each costume row has its own **Body**: "Leave as is", or any body loaded in the dialog. Every body remembers its own skeleton and bone map. **Give every costume this body** fills all rows.
- **Tomba slot as installed** is the costume as the project has it now: Tomba on a retail disc, or the swapped body after an install. To make a costume variant, export it, edit it in Blender, import it and give it to one row.
- On import, a face keeps reading art that is already loaded when two things hold:
  - its `T2_` material's picture matches the resident VRAM at that page and palette, texel for texel over the face's UV box;
  - the page and palette are claimed by chunks 0–2.

  Everything else (repainted pictures, new materials) is copied. The install moves only those packets, places each body's art in turn from one shared pool, and treats reused packets as readers that must not change.
- **Tested:** a pig snout with its own 32×32 picture was added in Blender to the installed Tabby and given to the Pig Suits only. 326 of 374 faces reused loaded art, and only 272 halfwords of VRAM were used (2,428 → 2,156 free in every area). In the emulator, all 11 costumes passed, and the Pig Suits draw the snout.

## Verified in an emulator (2026-11-24)

A disc with Tabby on all 11 costumes was booted from scratch in the headless Beetle core (`work/blender_geometry/emulator/run_core.py`). It played 9,600 frames: the opening, walking around Town and the scripted dialogues, with no crash. Read back from the emulator's memory:

- The costume buffer at `0x8017C800` equals the installed model.
- The bone table at `0x800A3FA8` is Tabby's.
- All 17 parts point into her groups.
- All 2,886 halfwords she samples in live VRAM match the install.
- All 1,281 texel reads and 10 palettes are identical to Tabby in her own area.

**Costume changes.** All 11 costumes were also changed in the emulator through the game's own action 0x3A, the way A06's Invisible Clothes event triggers it: Tomba's state word is saved to `0x1F80023C`, the clothes byte is set at `0x800E7EEF`, and the state word is set to action 0x3A, phase 0. The test patches main RAM and scratchpad inside a serialized state. It also has to run under lifecycle 1 (the action dispatcher), because 5 and 6 are the OPN.BIN opening. For every costume:

- The buffer holds that costume's model.
- That costume's bone table is in place.
- Each part draws its intended group, including the Wing Suit's 19 parts.
- Invisible Clothes hides parts 0, 2, 3, 5, 6, 10 and 13.
- Live VRAM has 0 differences.

**Blender body.** A Tabby edited in Blender (joined into one mesh, renamed, 5% bigger, exported as GLB) passed the same checks on its own disc.

Runtime VRAM uploads other than the pants palettes are only known from savestates you add. Areas other than Town of the Fishermen were not booted.

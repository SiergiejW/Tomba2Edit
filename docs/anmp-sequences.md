# ANMP animations and poses

Open Tomba's animation under **AREA_01** (the second IDX area), resource
ID 4. On the supported US retail build the ANMP viewer automatically
offers **Tomba (US) — 239 animations**. Choose an animation ID in the
dropdown. Every entry ends with its distinct pose count. The rows show
the actual pose order, tick duration,
tween/hold flag and loop target. Selecting a row shows that step's pose;
Play uses the recorded timing at the game's 30 Hz NTSC / 25 Hz PAL update
rate (the scheduler waits two vblanks per update). Completed clips stop on their last pose,
while looping clips repeat their loop without replaying the intro.

**All poses (raw ANMP)** is the initial view and restores the original full pose list and its
manual FPS/blend controls. Reset pose still shows the skeleton's rest
pose. When a clip is selected, Export explicitly asks whether to write that
game animation or every raw pose. The selected clip is sampled at the game's
tick rate. It exports one traversal; glTF does
not encode the game's intro/loop target or actor events.

For NPCs, compatible sequence tables are found automatically in the current
area's overlay and MAIN.EXE. The table covering the largest part of the open
ANMP is selected first; Ghost Guard's A06 table, for example, resolves to 15
clips covering all 124 poses. Other compatible tables stay in the dropdown.
Structural compatibility does not by itself prove which character owns a
shared table, so inferred tables retain their candidate warning and RAM
address. Unknown builds and banks remain usable in raw mode. Four-pose robe
banks are allowed to contain a single animation; larger archives require a
run of at least three sequence pointers to reject coincidental data.

Autoplay is enabled by default and starts after opening an ANMP or selecting
another clip. The first transport row contains Play, Autoplay, Reset pose and
the timeline; interpolation/rate controls and status are on the second row.

Model and skeleton selection is narrowed using the game's own actor
initialization calls. Those calls carry the area file ID, bone count and
skeleton-table pointer together, so an unrelated skeleton is no longer
ranked merely because it has the same number of bones. If one model archive
is genuinely initialized with several compatible tables, only those exact
alternatives remain in the skeleton dropdown. Labels and packing adjacency
remain as fallbacks for trail assets and builds whose code cannot be decoded.
The model and animation selectors are ordinary dropdowns, not editable search
fields.
When a model comes from another area, the viewer switches to that model's
area VRAM (plus the common resident pages), so cross-area candidates no
longer borrow the currently open ANMP area's textures.
The same code-derived association is used when a standalone SMST is exported
with a rig; geometric matching is used only when no code binding was found.

## Evidence and limits

The US retail Tomba ANMP has 1,152 poses: 1,147 with 17 limbs and five
with 19 limbs. It is resource 4 at original DAT offset `0x91CC` in
AREA_01. The offset is not used to identify the resource at runtime.

`f_SetTombaAnimation` and `f_SetTombaAnimationIfChanged` reference the
239-pointer bank at RAM `0x80017FE8` (original MAIN.EXE offset `0x87E8`).
The bank ends at its first sequence, `0x800183A4`. Every pointer decodes
against the Tomba pose bank, including 136 loops and the static terminal
pose in animation `0xE4`. Build-header and pose-layout checks prevent
applying this address to unrelated builds or resources. Edited US text
pools do not invalidate the header check.

The local US decompilation identifies the record consumers:

- `f_StartActorSkeletalAnimation`, RAM `0x80077C40`, picks a sequence.
- `f_AdvanceActorSkeletalAnimation`, RAM `0x80076D68`, handles timing,
  next entries, indirect links and completion/loop markers.
- `FUN_80076904` takes the entry's first halfword as an ANMP pose index.

Each eight-byte entry contains four halfwords: pose index, two actor
payloads, and duration/flags. The low 12 bits are ticks; `0x2000` enables
tweening. The top two bits select inline advance (`0`), indirect link
(`0x4000`), terminal hold (`0x8000`), or indirect link plus completion
notification (`0xC000`). The link pointer follows the entry. Revisited
addresses identify the precise loop start, even when clips share poses.

The viewer does not run actor payloads, action-state changes, or external
animation overrides, and interpolation is floating point rather than
the console's rounded fixed-point math. Most clips retain numeric IDs;
the few descriptive names are usage contexts supported by the decomp.

Scale is persistent actor state: ordinary frames retain it, and a tween reads
the target scale immediately instead of interpolating it. Generic actors use
`f_UpdateActorScaledPartTransforms`. Sea Anemones use A04's special two-pass
routine: parent scale positions the segment chain, while each mesh receives
only its own scale. This prevents the anemone head width from multiplying
through all four ancestors. Sea Anemone glTF export bakes those world-space
joint transforms onto flat animated joints, since an ordinary glTF hierarchy
would necessarily inherit the parent width and recreate the same error.

Parser regression checks (no ROM needed):

```text
python -m unittest discover -s tests -p test_anmp_sequences.py -v
```

# Warp destination labels

US retail scenes display small red departure labels with the marker overlay:
`warp: room N` or `warp: area N, area label`. Hidden entities hide their
labels. Arrival markers are no longer added to the scene. Their native tables remain available for destination resolution and navigation.

`game/warps.py` follows bounded native control flow and accepts constant halfword
writes to 0x800BF83A. The high byte is the destination area and the low byte its
arrival index. Area labels come from the existing retail labels file; arrival
indices appear in entity notes. Multiple reachable destinations are alternatives;
they are not a claim that every quest condition is currently satisfied.

The shared interior entry helper at 0x800732C0 uses the matched actor selector.
Coal Mining Town's custom door selectors are mapped to their actual interiors
(e.g. door selector 11 enters interior 3). Donglin's shared entrance handler is
restricted to the selected door's dispatch-table branches so unrelated doors do
not contribute destinations. The door near AREA_0A arrival #14 (7162, -2700,
6720) uses selector 12 and leads to area 1 arrival #28 when the mine is purified;
it does not enter interior 11. Other custom interior handlers remain incomplete.

Coal Mining Town has a separate walking boundary in FUN_A02__801122a4:
x > 10000 and depth < 2800, destination 0x0102. Its marker is a point within
that region near arrival #0, not the extent of the trigger. The minecart handler
FUN_A02__8011aef0 writes 0x0400 and is labelled on its native controller.
Both were checked against the local retail files and an assembled AREA_1C scene.

Remaining work: trace other area-level walking boundary predicates into spatial
markers, custom interior state machines, runtime-computed destinations, and
interaction/quest conditions. Elevator coverage has not been gameplay-validated.
Named nonresident destinations are supported, including area 18, Tower of
Wisdom. Donglin selector 11 resolves to that tower. The Forest Entrance has a
room-side marker for room 9 from entry #6's native flags; its exact departure
trigger bounds are still unresolved. It is a room location anchor, not a traced
walk-in trigger.

Select a warp and use Go to warp destination in the Selected panel or the
viewport context menu. Rooms switch the Rooms view. Areas load their destination
chunk and resident destinations frame the arrival position without adding arrival
markers. Multiple destinations get a chooser. Navigation preserves the loaded
scene's cursed/purified version where the target has a counterpart; it does not
reconstruct the complete purification flags of a gameplay save. Quest gates are
not enforced by editor navigation.

Warp overlays now include a world-sized open-door symbol from the shared
`icons/palette/warp.svg` asset. The symbol and text use solid #ff4545 with a
transparent background and no shadow. The bottom centre is anchored to the
entity's projected origin. Click the symbol or text to select the entity;
right-click either to open its destination action. Hidden warps and the Markers
toggle hide both parts of the overlay.

The door billboards scale with perspective (420 game units tall). Text appears
within 5000 game units of the camera. The depth buffer clips each door pixel
against geometry, and hidden pixels cannot be selected. Text is suppressed when
the door centre is occluded.

Water Temple's shared fourteen-selector entrance controller is resolved per
selector: room entries, the mine door, Golden Tower and the raft each have their
own destination. Resident chest/message routines do not generate warp labels.
Room-side return markers use the native entrance controller's return path;
known room-side doorway offsets are applied in Water Temple. This is still
partial coverage of custom room/area transition handlers across the game.

Water Temple has separate outdoor Part 1/2/3 views. Main mesh ownership follows
the three main TRAIL meshes; the side wing is included with Part 1. Outdoor
entities use the nearest main-mesh vertices for section assignment; boundary
ownership is approximate. Area and every room keeps the complete assembly.

Edit mode respects Fresh/Events done/Both instead of forcing Fresh. Both runs
two bounded initializations without recording actor loops. This includes the
100-Year-Old Man's room model in the Events done variant. Native palette, UV and
background texture animation run in Edit mode. The former Preview mode is
labelled Simulation mode.

## Overlay update

Direction arrows now follow the resident controller's two-part definition at
MAIN 0x800A4BA8 (parts 11/12, Y offsets +25/-25). The controller at
0x8006F2D0 follows Tomba; instructions 0x8006F52C..0x8006F538 place its origin
300 units above Tomba. The editor anchors that lift to the transition's standing
surface. Adjacent and reciprocal collision cells are grouped within 110 game
units to produce one readable hint location, rather than drawing pairs for every
cell and both inputs. The Collision menu's Direction hints: Down input switch
chooses the previewed input. This grouping and static source-plane choice are
editor adaptations; they are not a running player simulation.

Doors are 240 game units tall, capped at 96 screen pixels, with text within
8000 game units. Obstructed parts use 50% opacity near the camera, fading by
`1 / (1 + (distance / 12000)^2)`. Text follows the door's obstruction and fades
with it. Collision and ordinary marker hidden passes use the same distance curve
(with camera depth in place of Euclidean distance for the shared GL shader).

Depth readback is downsampled on the GPU to at most 320 pixels wide before CPU
transfer, and stationary-camera depth refresh is limited to 10 Hz. Door masks are
built with vectorized arrays and Qt bitmap conversion rather than Python row
loops. A local 300-mask comparison measured 554 ms before and 25 ms after.
The downsampling and shader link were checked in an offscreen native OpenGL
context on the RTX 4080 SUPER. These measurements are not full-scene FPS.

The latest door sizing is 140 game units with a 32-pixel screen cap. Within
2500 game units, doors and their labels ignore geometry obstruction and stay
solid. Between 2500 and 4000 units they ease into the distant obstruction fade.
The symbol is now classified as a whole using nine depth samples; per-pixel
silhouette masks were removed to avoid torn/cut-out artifacts at obstacles.

Collision hint arrows now share the same height, 300 units above their standing
surface. The editor no longer applies the native part definition's separate
vertical offsets to the two arrows.

Proximity un-obstruction now blends continuously from full opacity within 750
game units to the regular obstruction fade at 6000 units, using smoothstep.
There is no separate 2500-unit obstruction bypass. Door and text share the same
8-bit opacity, replacing the previous coarse 32-step alpha.

Collision arrow fallback now loads `icons/palette/arrows.smst`, bundled outside
the ROM. It contains only original SMST parts 11 and 12, with their complete
part payloads preserved byte-for-byte and a two-part pointer table. It uses the
regular SMST parser and retains the original vertex colour values without
clamping or recolouring. Collision generation no longer reads the arrow model
or its material from the opened ROM. The shared asset is bundled via icons.py;
there are no per-build copies.

## Solid arrow rendering correction

The external SMST was intact, but the collision overlay disabled depth writes,
so back faces overwrote front faces. Native arrow models now render into their
own colour/depth buffers first, with normal depth writes. Their nearest surfaces
are then composited against the unchanged scene depth; obstruction opacity is
applied only after the model has resolved its own front/back surfaces.

`icons/palette/arrows.png` bundles the original constant texture patch alongside
the SMST. Both assets load without the ROM. Float colour is retained until this
texture's colour is applied, so original shading is preserved. GPU comparisons
against the original textured SMST at three camera angles differ by at most one
8-bit channel value. Full/partial obstruction, main-model selection masks and
scene depth preservation are also checked by `work/verify_collision_arrows.py`.

The arrow pair now has a minimal two-game-unit vertical separation (+1/-1
around the shared hint origin) to prevent coincident faces from z-fighting.
The bundled original models are unchanged.

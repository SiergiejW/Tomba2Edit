# Level item placement

The Level Editor palette adds persistent chests, AP crystals, inventory
items, red/blue apples, and weapons. Defaults are a red chest containing
100 AP, a 100 AP crystal, Red Key, a red apple, and Boomerang. Changing an
item also displays the category read from the game's inventory definition:
equipment, usable/contextual, or story/automatic.

Outdoor placements use the actual SCLD collision lane. A chest's heading
comes from that lane; its native record has no independent rotation field.
Town chests use native behavior 4, whose plane byte supplies their yaw.
Interiors currently support chests. Native loose pickups do not receive an
interior ID from the placement-table spawner, so adding them indoors is
disabled.

The editor stages the full native pickup record, including contents,
color, lane, position, and behavior. A new record joins one of the area's
own tables holding the same kind of record: chests go to a chest table
(walked by `f_SpawnPersistentPickupPlacementTable`), everything else to a
loose-pickup table (walked by the 32-record cluster manager). Retail never
mixes the two. Each table holds at most 32 records. An area without a
native table of the right kind needs its spawn code implemented first.

## Where added data lives in RAM

The game uses every byte of its 2 MB. Measured over 70 retail savestates
in every area, the only range nothing ever writes is **0x8014E944
(the end of A00, the largest overlay) .. 0x80157002**:

| RAM | contents |
| --- | --- |
| 0x80108F9C .. overlay end | the area overlay's code and data |
| overlay end .. 0x8014E944 | that overlay's heap (starts right at its end) |
| **0x8014E960 .. 0x80157000** | **free: editor extension window** |
| 0x80157000 .. 0x80158000 | live 4 KB block |
| 0x80158000 .. 0x8017C800 | resident System chunk: shared pickup sprites, chest models |
| 0x8017C800 .. 0x8018A000 | live |
| 0x8018A000 .. | the area's own data chunk |

A grown table moves into its own slot of that window, carried by its
overlay file (padded out to it), and MAIN.EXE's pointer is updated.
Original overlay code and data keep their addresses; MAIN.EXE keeps its
retail size. Slots are globally distinct, so one overlay's padding never
reads as another's table.

The first version of this feature put its data at overlay `+0x70000`,
which is inside the resident System chunk. Loading any padded overlay
zeroed the shared sprite bank; the sprite part loop
(`f_DrawActorSpriteParts`) then read a part count of 0, never ended, and
its GPU packets overran BSS into the BIOS interrupt chain at 0x80102440.
Every level crashed on load. Projects saved that way are repaired
automatically when opened (`pickup_edit.repair_legacy_layout`).

New records receive unique persistence bits from the upper half of the
game's 128-byte collection banks. All project area overlays are checked;
shipped/scripted placements keep the lower half. Existing saves preserve
their collected items. Apples and other pickups use separate banks.

## Inventory items beyond the native reward list

Inventory item IDs and ground reward selectors are different. The US
retail game has 51 native ground rewards; Red Key has an inventory
definition but no ground reward. The editor can prepare up to 77 additional
reward definitions on the verified US executable layout. All 22 area
overlays receive the same extended table at the head of the free window
(0x8014E960). Five audited MAIN.EXE instruction pairs then select that
table. Every reader acts on a live pickup actor, which only exists while
an area overlay is loaded, so no copy is kept in MAIN.EXE.

Rewards drawn from an area's own sprite bank (Boomerang, Hammer, keys,
pig nose panels...) are numbered in that area's own sprite file and
sequence table. They are offered only in areas whose shipped records show
them; elsewhere the index names a different sprite or runs off the end
of the table. The inventory item is still available as a shared-icon
custom reward.

Additional items use the resident Lunch Box pickup presentation. The UI
labels these with **shared pickup icon**. Their inventory item ID and
acquired-item message are the chosen item's, including Red Key; a key is
not silently replaced by another reward. Native item-grant behavior still
applies, including equipment and quest effects. Harp/pearl components whose
native grant deliberately requires a quest-specific actor are disabled.
Other builds retain their native supported reward choices until their
custom-reward patch has been verified.

Enabling this extension stages MAIN.EXE and all 22 overlays as one project
transaction. Keep those files together when exporting; the Level Editor
does this automatically. The extension refuses unknown instructions,
inconsistent overlay tables, occupied reserved addresses, and unsupported
build layouts.

## Verification

`python -m unittest tests.test_pickup_edit` checks table growth, repeat
saves, collision-lane/configuration persistence, save-bit isolation,
capacity limits, independent relocation across areas, custom reward
transactions, and executable guards. When local US retail files are
present, it also executes the edited MIPS initializer and item-grant code:
custom Red Key resolves to inventory ID 29, increments its actual inventory
quantity, and queues acquired-item message `(29, 2)` without unmapped memory
accesses. Message display, sound, and effects are stubbed in that check.

The native-code check and editor round trips do not replace an emulator
playthrough covering collection, chest opening, save/load, and area changes.

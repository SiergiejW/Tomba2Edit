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
color, lane, position, and behavior. A grown table is appended to its
overlay and MAIN.EXE's table pointer is updated. Original overlay code and
data keep their addresses. Relocated tables receive globally distinct
pointer ranges, including when several overlays have identical lengths.
Each manager supports at most 32 records because the game uses a 32-bit
per-record mask. An area without an existing native table needs its spawn
code implemented before it can receive palette pickups.

New records receive unique persistence bits from the upper half of the
game's 128-byte collection banks. All project area overlays are checked;
shipped/scripted placements keep the lower half. Existing saves preserve
their collected items. Apples and other pickups use separate banks.

## Inventory items beyond the native reward list

Inventory item IDs and ground reward selectors are different. The US
retail game has 51 native ground rewards; Red Key has an inventory
definition but no ground reward. The editor can prepare up to 77 additional
reward definitions on the verified US executable layout. All 22 area
overlays receive the same extended table at overlay offset `0x70000`,
below the area resource region. Five audited MAIN.EXE instruction pairs
then select that table. MAIN.EXE also contains an initial copy so scripted
rewards can use it before the first area overlay loads. Its PS-X EXE load
size is updated and padded to a complete sector.

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

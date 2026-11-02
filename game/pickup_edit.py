"""Edit the game's persistent pickup tables without moving overlay code.

The tables are reached through MAIN.EXE, not through the MDAT archive.
Growing a table therefore requires staging both its overlay and MAIN.EXE.
This module produces bytes only; the editor owns the project transaction.
"""
from dataclasses import dataclass, replace
from pathlib import Path
import struct

from formats.sprites import pickup_art
from game import game_build, placement

# The native placement manager keeps one 32-bit visibility/allocated mask.
MAX_TABLE_RECORDS = 32
TABLE_CAPACITY = (MAX_TABLE_RECORDS + 1) * placement.PICKUP_SIZE
# Both native collection banks are 128 bytes. Reserve the lower half for
# shipped/scripted placements, including ones outside the persistent tables.
NEW_BIT_START = 512
BANK_BITS = 1024
NATIVE_REWARDS = 51
ITEM_TYPES = {0: "Equipment", 1: "One-time / contextual", 2: "Story / automatic"}

# The only RAM no overlay ever uses (US retail). Measured over 70 savestates
# in every area: nothing writes 0x8014E944 (the end of A00, the largest
# overlay) .. 0x80157002. Below is each overlay's code and heap - a small
# overlay's heap starts at its own file end - and above is a live 4 KB block
# and then the resident System chunk at 0x80158000 (pickup sprites, chest
# models). Extension data rides in the area overlays, padded out to here.
WINDOW_START = 0x8014E960
WINDOW_END = 0x80157000
CUSTOM_OFFSET = WINDOW_START - game_build.US_OVERLAY_BASE
CUSTOM_COUNT = 128
CUSTOM_TAG = b"T2EDIT_REWARDS_V2"
CUSTOM_TAG_AT = CUSTOM_OFFSET + CUSTOM_COUNT * pickup_art.REWARD.size
CUSTOM_END = CUSTOM_TAG_AT + len(CUSTOM_TAG)
TABLES_OFFSET = (CUSTOM_END + 0xF) & ~0xF
WINDOW_LIMIT = WINDOW_END - game_build.US_OVERLAY_BASE

# The first layout put the reward table at overlay +0x70000 and a copy in
# MAIN.EXE. That is inside the resident System chunk: loading any padded
# overlay zeroed the shared sprite bank, the sprite part loop then never
# ended and its packets overran BSS into the BIOS interrupt chain.
LEGACY_OFFSET = 0x70000
LEGACY_TAG = b"T2EDIT_REWARDS_V1"
LEGACY_TAG_AT = LEGACY_OFFSET + CUSTOM_COUNT * pickup_art.REWARD.size
US_EXE_SIZE = 716800
US_OVERLAY_SIZES = {
    "A00": 285096, "A01": 202460, "A02": 201004, "A03": 78444, "A04": 250788,
    "A05": 234164, "A06": 284348, "A07": 229720, "A08": 266124, "A09": 22125,
    "A0A": 126520, "A0B": 115560, "A0C": 123328, "A0D": 119592, "A0E": 124656,
    "A0F": 135500, "A0G": 17980, "A0H": 14016, "A0I": 15152, "A0J": 19068,
    "A0K": 93380, "A0L": 74832}
# These quest components deliberately skip the generic inventory-add path
# in f_GrantNonGemPickupReward and need their own area's scripted actor.
SCRIPTED_ITEM_GRANTS = frozenset((0xA0, 0xA1, 0xA2, 0xA3))
# Audited US retail materializations of g_GroundPickupRewardDefinitions.
# Each pair is checked byte-for-byte before changing either instruction.
REWARD_REFS = (
    (0x800221D8, 0x3C02800A, 0x800221DC, 0x245E29CC),
    (0x8002237C, 0x3C02800A, 0x80022384, 0x244229CC),
    (0x8004A8A4, 0x3C03800A, 0x8004A8A8, 0x246329CC),
    (0x8004A9B0, 0x3C02800A, 0x8004A9C0, 0x244229CC),
    (0x8006FCD8, 0x3C02800A, 0x8006FCDC, 0x244229CC),
)


@dataclass(frozen=True)
class CustomRewards:
    files: dict
    item_rewards: dict


def _word_at(exe, address):
    base = struct.unpack_from("<I", exe, placement.EXE_BASE_AT)[0]
    at = address - base + placement.EXE_TEXT
    if at < placement.EXE_TEXT or at + 4 > len(exe):
        raise placement.PlacementError("The executable does not contain a verified reward reference.")
    return at


def _ref_words(hi, lo, target):
    return ((hi & 0xFFFF0000) | ((target + 0x8000) >> 16 & 0xFFFF),
            (lo & 0xFFFF0000) | (target & 0xFFFF))


def _custom_ref_words(hi, lo):
    return _ref_words(hi, lo, WINDOW_START)


def _legacy_ref_words(hi, lo):
    return _ref_words(hi, lo, game_build.US_OVERLAY_BASE + LEGACY_OFFSET)


def window_supported():
    """Whether the free-RAM window was measured for the open build."""
    return game_build.current().name == "us-retail"


def custom_rewards_supported(exe):
    """Whether all audited references are pristine US or our exact patch."""
    if (game_build.current().name != "us-retail" or len(exe) < placement.EXE_TEXT
            or exe[:8] != placement.EXE_MAGIC):
        return False
    try:
        pairs = []
        for hi_at, hi, lo_at, lo in REWARD_REFS:
            actual = (struct.unpack_from("<I", exe, _word_at(exe, hi_at))[0],
                      struct.unpack_from("<I", exe, _word_at(exe, lo_at))[0])
            pairs.append(actual in ((hi, lo), _custom_ref_words(hi, lo)))
        return all(pairs)
    except (placement.PlacementError, struct.error):
        return False


def custom_reward_records(exe, overlay):
    """Verified custom definitions for pickup_art, or an empty mapping."""
    if not overlay or not custom_rewards_supported(exe):
        return {}
    if overlay[CUSTOM_TAG_AT:CUSTOM_TAG_AT + len(CUSTOM_TAG)] != CUSTOM_TAG:
        return {}
    for hi_at, hi, lo_at, lo in REWARD_REFS:
        if (struct.unpack_from("<I", exe, _word_at(exe, hi_at))[0],
                struct.unpack_from("<I", exe, _word_at(exe, lo_at))[0]) != _custom_ref_words(hi, lo):
            return {}
    out = {}
    for reward in range(NATIVE_REWARDS, CUSTOM_COUNT):
        record = pickup_art.REWARD.unpack_from(overlay, CUSTOM_OFFSET + reward * pickup_art.REWARD.size)
        if record[-1] >= 0:
            out[reward] = record
    return out


def refresh_custom_presentation(exe_path, overlay_paths):
    """{path: bytes} re-drawing every custom reward with each area's own
    art (AREA_ITEM_ART), or {} when nothing changes."""
    exe = Path(exe_path).read_bytes()
    paths = {Path(p).stem.upper(): str(p) for p in overlay_paths if p}
    if not custom_rewards_supported(exe) or not set(US_OVERLAY_SIZES) <= set(paths):
        return {}
    datas = {n: Path(paths[n]).read_bytes() for n in US_OVERLAY_SIZES}
    if not all(d[CUSTOM_TAG_AT:CUSTOM_END] == CUSTOM_TAG for d in datas.values()):
        return {}
    items = [_custom_items(d[CUSTOM_OFFSET:CUSTOM_TAG_AT]) for d in datas.values()]
    if len(set(items)) != 1:
        return {}
    size = pickup_art.REWARD.size
    at = pickup_art.REWARD_TABLE - struct.unpack_from("<I", exe, 0x18)[0] + placement.EXE_TEXT
    native = exe[at:at + NATIVE_REWARDS * size]
    template = pickup_art.REWARD.unpack_from(native, 13 * size)
    mapping = {item: r for r, item in enumerate(items[0]) if r >= NATIVE_REWARDS and item >= 0}
    out = {}
    for name, data in datas.items():
        table = custom_table(native, mapping, name, template)
        if data[CUSTOM_OFFSET:CUSTOM_TAG_AT] != table:
            new = bytearray(data)
            new[CUSTOM_OFFSET:CUSTOM_TAG_AT] = table
            out[paths[name]] = bytes(new)
    return out


def ensure_custom_rewards(exe_path, overlay_paths, *, item_ids=(29,)):
    """Prepare a transaction adding selected inventory IDs as native rewards.

    Every area overlay receives the same table in the free-RAM window
    (WINDOW_START). Five audited MAIN references then use it; every reader
    acts on a live pickup, which only exists while an area overlay is
    loaded. All native definitions remain byte-identical and in native
    order. Custom entries use the area's own ground sprite where
    AREA_ITEM_ART names one, else the resident Lunch Box presentation;
    their inventory grant and message use the selected item definition.
    The caller must commit ALL returned files together.
    """
    exe_path = str(exe_path)
    exe = Path(exe_path).read_bytes()
    if not custom_rewards_supported(exe) or not window_supported():
        raise placement.PlacementError(
            "Custom item rewards require the verified US retail executable layout.")
    paths = {Path(p).stem.upper(): str(p) for p in overlay_paths if p}
    required = set(US_OVERLAY_SIZES)
    if not required.issubset(paths):
        raise placement.PlacementError("All 22 area overlays are required to enable custom item rewards.")
    sources = {name: Path(paths[name]).read_bytes() for name in sorted(required)}
    end = CUSTOM_END
    at = pickup_art.REWARD_TABLE - struct.unpack_from("<I", exe, 0x18)[0] + placement.EXE_TEXT
    native = exe[at:at + NATIVE_REWARDS * pickup_art.REWARD.size]
    if len(native) != NATIVE_REWARDS * pickup_art.REWARD.size:
        raise placement.PlacementError("The native reward definitions are truncated.")
    tagged = [data[CUSTOM_TAG_AT:CUSTOM_TAG_AT + len(CUSTOM_TAG)] == CUSTOM_TAG
              for data in sources.values()]
    if any(tagged) and not all(tagged):
        raise placement.PlacementError("Custom reward tables are inconsistent across the project overlays.")
    if all(tagged):
        # Presentation may differ per area; which item each selector grants
        # may not, or a save made in one area would mean another elsewhere.
        columns = {_custom_items(data[CUSTOM_OFFSET:CUSTOM_TAG_AT]) for data in sources.values()}
        if len(columns) != 1:
            raise placement.PlacementError("The custom item reward definitions differ between overlays.")
        if any(data[CUSTOM_OFFSET:CUSTOM_OFFSET + len(native)] != native for data in sources.values()):
            raise placement.PlacementError("The custom table no longer matches the native reward definitions.")
        mapping = {item: reward for reward, item in enumerate(next(iter(columns)))
                   if reward >= NATIVE_REWARDS and item >= 0}
    elif any(any(data[CUSTOM_OFFSET:end]) for data in sources.values()):
        raise placement.PlacementError("An overlay already occupies the reserved custom reward address.")
    else:
        mapping = {}
    if any(US_OVERLAY_SIZES[name] > CUSTOM_OFFSET for name in sources):
        raise placement.PlacementError("An overlay's own code reaches the custom reward window.")
    free = [r for r in range(NATIVE_REWARDS, CUSTOM_COUNT) if r not in mapping.values()]
    # Native selector 13 is a stationary, universally resident sprite.
    template = pickup_art.REWARD.unpack_from(native, 13 * pickup_art.REWARD.size)
    if template[1] & 0x7FFF == pickup_art.AREA_BANK or template[-1] != 92:
        raise placement.PlacementError("The verified resident presentation for custom rewards differs.")
    for item in dict.fromkeys(int(v) for v in item_ids):
        if not 0 <= item < pickup_art.ITEM_COUNT:
            raise placement.PlacementError("The inventory item ID is outside the game's item definitions.")
        if item in SCRIPTED_ITEM_GRANTS:
            raise placement.PlacementError("This quest component requires its native scripted actor to grant it.")
        if item in mapping:
            continue
        if not free:
            raise placement.PlacementError("All 77 custom ground-reward slots are in use.")
        mapping[item] = free.pop(0)
    # MAIN.EXE keeps its retail size: no resident copy. Anything past
    # 0x800BE800 that MAIN loads lands in live memory (BSS, the overlay
    # heap, the resident chunk) and the readers only run inside areas.
    patched_exe = bytearray(exe)
    for hi_at, hi, lo_at, lo in REWARD_REFS:
        new_hi, new_lo = _custom_ref_words(hi, lo)
        struct.pack_into("<I", patched_exe, _word_at(exe, hi_at), new_hi)
        struct.pack_into("<I", patched_exe, _word_at(exe, lo_at), new_lo)
    files = {exe_path: bytes(patched_exe)}
    for name, original in sources.items():
        data = bytearray(original)
        if len(data) < end:
            data.extend(bytes(end - len(data)))
        data[CUSTOM_OFFSET:CUSTOM_TAG_AT] = custom_table(native, mapping, name, template)
        data[CUSTOM_TAG_AT:end] = CUSTOM_TAG
        files[paths[name]] = bytes(data)
    return CustomRewards(files, mapping)


# Where an item's real ground sprite lives: {item: {overlay: sequence in
# that overlay's own sequence table}}. A custom reward there draws it
# through the game's area-sprite path (palette 1); elsewhere it uses the
# shared resident icon. Red Key: Fishermen sprite 9 = sequence 2.
AREA_ITEM_ART = {29: {"A00": 2}}


def _custom_items(table):
    """The item column of a custom table, selector by selector."""
    return tuple(pickup_art.REWARD.unpack_from(table, r * pickup_art.REWARD.size)[-1]
                 for r in range(CUSTOM_COUNT))


def custom_table(native, mapping, overlay_name, template):
    """One overlay's custom reward table: native rows untouched, then each
    mapped item drawn with this area's own art if it has any."""
    rows = bytearray(native + b"".join(pickup_art.REWARD.pack(0, 0, 0, 0, -1)
                                        for _ in range(CUSTOM_COUNT - NATIVE_REWARDS)))
    for item, reward in mapping.items():
        sequence = AREA_ITEM_ART.get(item, {}).get(overlay_name)
        if sequence is not None:
            row = (sequence, pickup_art.AREA_BANK, template[2], template[3], item)
        else:
            row = (*template[:-1], item)
        pickup_art.REWARD.pack_into(rows, reward * pickup_art.REWARD.size, *row)
    return bytes(rows)


@dataclass(frozen=True)
class RewardChoice:
    reward: int
    label: str
    category: str
    item_type: str = ""
    available: bool = True
    reason: str = ""
    item: int = -1


def item_types(exe_path):
    """Inventory categories from the actual build's item-definition table."""
    data, base = pickup_art._image(exe_path)
    result = {}
    for item in range(pickup_art.ITEM_COUNT):
        try:
            at = pickup_art._at(data, base,
                                pickup_art.ITEM_TABLE + item * pickup_art.ITEM.size,
                                pickup_art.ITEM.size)
        except pickup_art.PickupArtError:
            break
        category, slot, *_ = pickup_art.ITEM.unpack_from(data, at)
        result[item] = (ITEM_TYPES.get(category, "Unknown"), category == 0 and slot == 0)
    return result


def shipped_rewards(overlay_path, exe_path):
    """Rewards this area's own shipped records show, loose or in a chest.

    Editor-added records take bits from NEW_BIT_START up, so they never
    vouch for art the area doesn't really have."""
    if not overlay_path or not exe_path:
        return set()
    out = set()
    for record in placement.load_pickups(overlay_path, exe_path):
        if record.bit >= NEW_BIT_START:
            continue
        out.add(record.contents if record.chest else record.art_reward)
    return out


def table_kind(records):
    """'chest' or 'loose' for a homogeneous table, else None. Retail never
    mixes the two in one table, so the editor doesn't either."""
    chests = {r.chest for r in records}
    if chests == {True}:
        return "chest"
    if chests == {False}:
        return "loose"
    return None


def table_for(category, pickups, preferred=None):
    """The table a new pickup of `category` joins: one of this area's own
    tables holding the same kind of record, with room left."""
    want = "chest" if category == "chest" else "loose"
    by = {}
    for record in pickups:
        by.setdefault(record.table, []).append(record)
    fits = [t for t, rows in by.items()
            if table_kind(rows) == want and len(rows) < MAX_TABLE_RECORDS]
    if preferred in fits:
        return preferred
    if fits:
        return min(fits, key=lambda t: len(by[t]))
    if any(table_kind(rows) == want for rows in by.values()):
        raise placement.PlacementError(
            f"Every native {want} table in this area is full ({MAX_TABLE_RECORDS} records).")
    raise placement.PlacementError(
        f"This area has no native {'chest' if want == 'chest' else 'pickup'} table "
        "to extend. Its spawn code would have to be added first.")


def reward_catalog(exe_path, overlay_path=None, area=None):
    """Native reward choices, with unsafe/missing local artwork disabled.

    Reward selectors and inventory IDs have different numbering. In
    particular, retail has a Red Key inventory item but no Red Key ground
    reward definition; it must never be mislabeled as a different reward.
    """
    overlay = Path(overlay_path).read_bytes() if overlay_path else None
    arts = pickup_art.reward_art(exe_path, count=NATIVE_REWARDS,
                                 overlay=overlay, area=area)
    types = item_types(exe_path)
    shipped = shipped_rewards(overlay_path, exe_path)
    result = []
    for reward, art in arts.items():
        if reward in (0, 1):
            category = "apple"
            label = "Red apple" if reward == 0 else "Blue apple"
        elif 4 <= reward <= 11:
            category, label = "crystal", art.grants + " crystal"
        else:
            category = "weapon" if types.get(art.item, ("", False))[1] else "item"
            label = art.grants
        area_art = art.clut & 0x7FFF == pickup_art.AREA_BANK
        if reward >= NATIVE_REWARDS and not area_art:
            label += " (shared pickup icon)"
        available = bool(art.frames)
        reason = "" if available else "This area's native sprite sequence is unavailable."
        # An area-bank sprite is numbered in THIS area's own sprite file and
        # sequence table, which hold only what the area itself shows. Any
        # other index reads another sprite or runs off the end of the table.
        # Custom rows only point there when AREA_ITEM_ART vouches for it.
        if available and area_art and reward < NATIVE_REWARDS and reward not in shipped:
            available = False
            reason = "This area has no art of its own for this pickup."
        if art.item in SCRIPTED_ITEM_GRANTS:
            available = False
            reason = "This quest component requires its native scripted actor to grant it."
        result.append(RewardChoice(reward, label, category,
                                    types.get(art.item, ("", False))[0],
                                    available, reason, art.item))
    # A virtual selector identifies an inventory choice until the caller
    # stages ensure_custom_rewards and resolves item_rewards[item].
    data = Path(exe_path).read_bytes()
    supported = custom_rewards_supported(data)
    represented = {c.item for c in result if c.available}
    for item, name in pickup_art.item_names(exe_path).items():
        if item in represented or name.strip() == "(empty)":
            continue
        item_type, weapon = types.get(item, ("Unknown", False))
        available = supported and item not in SCRIPTED_ITEM_GRANTS
        reason = ("This quest component requires its native scripted actor to grant it."
                  if item in SCRIPTED_ITEM_GRANTS else
                  "" if supported else "Custom rewards need the verified US executable layout.")
        result.append(RewardChoice(256 + item, name + " (shared pickup icon)",
                                    "weapon" if weapon else "item", item_type,
                                    available, reason,
                                    item))
    return result


def default_reward(category, catalog):
    wanted = {"chest": 4, "crystal": 4, "apple": 0, "weapon": 18}
    if category == "item":
        key = next((c for c in catalog if c.item == 29 and c.available), None)
        if key is None:
            raise placement.PlacementError(
                "Red Key has no native ground-pickup reward in this build. "
                "Choose an available native item instead.")
        return key.reward
    reward = wanted.get(category)
    if reward is None:
        raise placement.PlacementError(f"Unknown pickup category: {category}")
    choice = next((c for c in catalog if c.reward == reward), None)
    if category == "weapon" and (choice is None or not choice.available):
        choice = next((c for c in catalog if c.item == 3 and c.available), choice)
        reward = choice.reward if choice is not None else reward
    if category != "chest" and (choice is None or not choice.available):
        raise placement.PlacementError(
            f"The default {category} cannot be drawn by this area's native assets.")
    return reward


def _all_records(pickups, exe_path, overlay_paths):
    records = list(pickups)
    for path in set(overlay_paths or ()):
        if path and Path(path).is_file():
            records.extend(placement.load_pickups(path, exe_path))
    return records


def new_pickup(category, pickups, exe_path, overlay_paths=None, *, table=None,
               position=(1, 0, 1), plane=0, interior=None, reward=None,
               overlay_path=None, area=None):
    """Create an unsaved native record (offset -1) with a unique save bit.

    Callers must provide all project overlays when adding records so save
    bits remain unique across areas. The upper half of each native bank is
    kept separate from shipped/scripted records in the lower half.
    """
    if game_build.current().demo:
        raise placement.PlacementError("Adding pickups is not supported in demo builds.")
    if not placement._pointer_array(placement.pickup_addresses(exe_path)):
        raise placement.PlacementError("This build has no verified persistent-pickup pointer array.")
    if not window_supported():
        raise placement.PlacementError(
            "Adding pickups needs free RAM measured for this build; only US retail has been.")
    table = table_for(category, pickups, preferred=table)
    if interior is not None and category != "chest":
        raise placement.PlacementError("Native interior placement currently supports chests only.")
    if reward is None:
        reward = default_reward(category, reward_catalog(exe_path, overlay_path, area))
    if reward >= 256:
        raise placement.PlacementError(
            "This inventory item must be prepared as a custom reward before placement.")
    apple = category == "apple"
    used = [r.bit for r in _all_records(pickups, exe_path, overlay_paths) if r.apple == apple]
    bit = max([NEW_BIT_START - 1] + used) + 1
    if bit >= BANK_BITS:
        raise placement.PlacementError("The native persistent pickup save bank is full.")
    x, y, z = (int(round(v)) for v in position)
    chest = category == "chest"
    persist = placement.APPLE if apple else (int(interior) if interior is not None else 0)
    record = placement.Pickup(
        index=sum(r.table == table for r in pickups), table=table, offset=-1,
        x=x, y=y, z=z, type=(2 if chest else 0) | (0x80 if interior is not None else 0),
        alloc=2 if chest else 5, persist=persist, reward=0 if chest else reward,
        bit=bit, plane=int(plane), behaviour=0,
        config=(1 << placement.CONTENTS_BITS) | reward if chest else 0)
    pack_pickup(record)
    return record


def pack_pickup(record):
    """Validate and serialize every native field, including reward and lane."""
    if record.type & placement.TYPE_MASK > placement.PICKUP_MAX_TYPE:
        raise placement.PlacementError("Invalid native pickup actor type.")
    if record.alloc not in placement.PICKUP_ALLOC:
        raise placement.PlacementError("Invalid native pickup allocation list.")
    if not 0 <= record.bit < BANK_BITS:
        raise placement.PlacementError("Pickup persistence bit is outside its native save bank.")
    if record.chest:
        if record.reward & placement.PICKUP_REWARD_MASK not in placement.CHEST_KINDS:
            raise placement.PlacementError("The native chest presentation must be red, green, blue or white.")
        if record.contents >= CUSTOM_COUNT:
            raise placement.PlacementError("The chest's reward is outside the native selector range.")
    elif record.art_reward >= CUSTOM_COUNT:
        raise placement.PlacementError("The pickup reward is outside the native selector range.")
    try:
        return placement.PICKUP.pack(record.type, record.alloc, record.persist, record.reward,
                                     int(record.x), int(record.y), int(record.z), record.bit,
                                     record.plane, record.behaviour, record.config)
    except (struct.error, OverflowError) as exc:
        raise placement.PlacementError("Pickup fields are outside their native numeric ranges.") from exc


def _exe_table(exe):
    if len(exe) < placement.EXE_TEXT or exe[:8] != placement.EXE_MAGIC:
        raise placement.PlacementError("A valid MAIN.EXE is required to save new pickups.")
    base = struct.unpack_from("<I", exe, placement.EXE_BASE_AT)[0]
    at = placement.PICKUP_TABLES - base + placement.EXE_TEXT
    if at < placement.EXE_TEXT or at + placement.PICKUP_TABLE_COUNT * 4 > len(exe):
        raise placement.PlacementError("The pickup pointer array lies outside MAIN.EXE.")
    addresses = struct.unpack_from(f"<{placement.PICKUP_TABLE_COUNT}I", exe, at)
    if not placement._pointer_array(addresses):
        raise placement.PlacementError("The native pickup pointer array could not be verified.")
    return at, addresses


def _table_end(data, offset):
    """End offset (just past the 0xFF) of a valid pickup table, or None."""
    at, rows = offset, []
    while (record := placement._pickup(data, at)) is not None:
        rows.append(record)
        at += placement.PICKUP_SIZE
    if not rows or at >= len(data) or data[at] != placement.END:
        return None
    if not all(placement._ordered(r.bit for r in rows if r.apple is which)
               for which in (True, False)):
        return None
    return at + 1


def repair_legacy_layout(exe_path, overlay_paths):
    """{path: bytes} moving a first-layout project into the free window,
    or {} when there is nothing to repair.

    The first layout padded overlays to +0x70000 (inside the resident
    System chunk), grew MAIN.EXE to carry a copy, and appended grown tables
    right past an overlay's end (inside its heap). Everything it added is
    recognised exactly - anything else past a retail length is refused,
    not discarded."""
    if not window_supported() or not exe_path:
        return {}
    exe = bytearray(Path(exe_path).read_bytes())
    paths = {Path(p).stem.upper(): str(p) for p in overlay_paths or () if p}
    datas = {n: Path(p).read_bytes() for n, p in paths.items()
             if n in US_OVERLAY_SIZES and Path(p).is_file()}
    pointer_at, addresses = _exe_table(exe)
    t_addr = struct.unpack_from("<I", exe, placement.EXE_BASE_AT)[0]
    base = game_build.US_OVERLAY_BASE

    # Tables the first layout moved, and who owns each.
    moved = {}
    for number, address in enumerate(addresses):
        off = address - base if address else -1
        if off < 0 or TABLES_OFFSET <= off < WINDOW_LIMIT:
            continue
        owners = [n for n, d in datas.items()
                  if off >= US_OVERLAY_SIZES[n] and _table_end(d, off)]
        if len(owners) > 1:
            raise placement.PlacementError(
                f"Pickup table {number} reads as valid in several overlays; refusing to guess.")
        if owners:
            name = owners[0]
            moved[number] = (name, address, datas[name][off:_table_end(datas[name], off)])

    tagged = [n for n, d in datas.items()
              if d[LEGACY_TAG_AT:LEGACY_TAG_AT + len(LEGACY_TAG)] == LEGACY_TAG]
    custom = datas[tagged[0]][LEGACY_OFFSET:LEGACY_TAG_AT] if tagged else None
    if tagged and any(datas[n][LEGACY_OFFSET:LEGACY_TAG_AT] != custom for n in tagged):
        raise placement.PlacementError("The old custom reward tables differ between overlays.")
    refs = [(struct.unpack_from("<I", exe, _word_at(exe, h))[0],
             struct.unpack_from("<I", exe, _word_at(exe, l))[0]) for h, _, l, _ in REWARD_REFS]
    legacy_refs = any(pair == _legacy_ref_words(hi, lo)
                      for pair, (_, hi, _, lo) in zip(refs, REWARD_REFS))
    if not (moved or tagged or legacy_refs or len(exe) > US_EXE_SIZE):
        return {}

    # Everything past a retail length must be ours, or nothing is touched.
    for name, data in datas.items():
        retail = US_OVERLAY_SIZES[name]
        known = bytearray(len(data))
        if name in tagged:
            known[LEGACY_OFFSET:LEGACY_TAG_AT + len(LEGACY_TAG)] = b"\x01" * (
                LEGACY_TAG_AT + len(LEGACY_TAG) - LEGACY_OFFSET)
        for owner, address, raw in moved.values():
            if owner == name:
                off = address - base
                known[off:off + len(raw)] = b"\x01" * len(raw)
        foreign = [i for i in range(retail, len(data)) if data[i] and not known[i]]
        if foreign:
            raise placement.PlacementError(
                f"{name}.BIN holds unrecognised data past its retail length "
                f"(at +0x{foreign[0]:X}); it was left untouched.")
    if len(exe) > US_EXE_SIZE:
        start = base + LEGACY_OFFSET - t_addr + placement.EXE_TEXT
        end = start + LEGACY_TAG_AT - LEGACY_OFFSET + len(LEGACY_TAG)
        if any(exe[US_EXE_SIZE:start]) or any(exe[end:]):
            raise placement.PlacementError(
                "MAIN.EXE holds unrecognised data past its retail length; it was left untouched.")
        del exe[US_EXE_SIZE:]
        struct.pack_into("<I", exe, 0x1C, US_EXE_SIZE - placement.EXE_TEXT)

    out = {name: bytearray(data[:US_OVERLAY_SIZES[name]])
           for name, data in datas.items() if len(data) > US_OVERLAY_SIZES[name]}
    for (h_at, hi, l_at, lo) in REWARD_REFS:
        words = _custom_ref_words(hi, lo) if custom is not None else (hi, lo)
        struct.pack_into("<I", exe, _word_at(exe, h_at), words[0])
        struct.pack_into("<I", exe, _word_at(exe, l_at), words[1])
    if custom is not None:
        size = pickup_art.REWARD.size
        at = pickup_art.REWARD_TABLE - t_addr + placement.EXE_TEXT
        native = bytes(exe[at:at + NATIVE_REWARDS * size])
        template = pickup_art.REWARD.unpack_from(native, 13 * size)
        mapping = {item: reward for reward, item in enumerate(_custom_items(custom))
                   if reward >= NATIVE_REWARDS and item >= 0}
        for name in US_OVERLAY_SIZES:
            if name not in datas:
                raise placement.PlacementError("All 22 area overlays are needed to move the custom rewards.")
            data = out.setdefault(name, bytearray(datas[name]))
            if len(data) < CUSTOM_END:
                data.extend(bytes(CUSTOM_END - len(data)))
            data[CUSTOM_OFFSET:CUSTOM_TAG_AT] = custom_table(native, mapping, name, template)
            data[CUSTOM_TAG_AT:CUSTOM_END] = CUSTOM_TAG
    target = TABLES_OFFSET
    for number in sorted(moved):
        name, address, raw = moved[number]
        if target + TABLE_CAPACITY > WINDOW_LIMIT:
            raise placement.PlacementError("The free RAM window has no room for every moved table.")
        data = out.setdefault(name, bytearray(datas[name]))
        if len(data) < target + TABLE_CAPACITY:
            data.extend(bytes(target + TABLE_CAPACITY - len(data)))
        data[target:target + TABLE_CAPACITY] = raw + bytes(TABLE_CAPACITY - len(raw))
        for alias, original in enumerate(addresses):
            if original == address:
                struct.pack_into("<I", exe, pointer_at + alias * 4, base + target)
        target += TABLE_CAPACITY
    files = {str(exe_path): bytes(exe)}
    files.update({paths[name]: bytes(data) for name, data in out.items()})
    return files


def stage_pickups(overlay_bytes, exe_bytes, pickups):
    """Return (overlay, MAIN.EXE, records) ready to stage together.

    Existing-size tables are patched in place. A grown table moves into
    the free-RAM window (WINDOW_START..WINDOW_END), never just past the
    overlay's file end: that is where the overlay's own heap starts. Every
    original instruction/data address stays put and all matching MAIN
    pointer aliases are updated. No input records mutate.
    """
    overlay, exe = bytearray(overlay_bytes), bytearray(exe_bytes)
    pointer_at, addresses = _exe_table(exe)
    # MAIN's array is shared by every overlay, so every area's relocated
    # table gets its own slot of the window: one overlay's padding then
    # reads as nothing at another's address. The custom reward table
    # keeps the window's head.
    reserved = [(address - placement.OVERLAY_BASE,
                 address - placement.OVERLAY_BASE + TABLE_CAPACITY)
                for address in addresses if address]
    reserved.append((CUSTOM_OFFSET, TABLES_OFFSET))
    groups = {}
    for record in pickups:
        if not 0 <= record.table < placement.PICKUP_TABLE_COUNT:
            raise placement.PlacementError("Pickup table index is outside MAIN.EXE's array.")
        groups.setdefault(record.table, []).append(record)
    updated = []
    for table, records in groups.items():
        address = addresses[table]
        old_at = address - placement.OVERLAY_BASE
        old, at = [], old_at
        if at < 0:
            raise placement.PlacementError("Pickup pointer does not belong to this overlay.")
        while (record := placement._pickup(overlay_bytes, at)) is not None:
            old.append(record)
            at += placement.PICKUP_SIZE
        if not old or at >= len(overlay_bytes) or overlay_bytes[at] != placement.END:
            raise placement.PlacementError("The original native pickup table could not be verified.")
        if len(records) < len(old):
            raise placement.PlacementError("Removing native pickup records is not supported.")
        growing = len(records) > len(old)
        if growing and len(records) > MAX_TABLE_RECORDS:
            raise placement.PlacementError("A native pickup manager supports at most 32 records.")
        # Preserve native order: managers identify records by their ordinal.
        records = sorted(records, key=lambda r: r.index)
        if [r.index for r in records] != list(range(len(records))):
            raise placement.PlacementError("Pickup record indices are not contiguous.")
        for apple in (False, True):
            bits = [r.bit for r in records if r.apple == apple]
            if len(set(bits)) != len(bits):
                raise placement.PlacementError("Two pickups share one collected-item save bit.")
        payload = b"".join(pack_pickup(r) for r in records)
        if growing and TABLES_OFFSET <= old_at < WINDOW_LIMIT:
            # Already in its own window slot, sized for a full table.
            target = old_at
            overlay[target:target + len(payload) + 1] = payload + bytes([placement.END])
        elif growing:
            if not window_supported():
                raise placement.PlacementError(
                    "Adding pickups needs free RAM measured for this build; only US retail has been.")
            target = TABLES_OFFSET
            while True:
                overlaps = [hi for lo, hi in reserved
                            if target < hi and target + TABLE_CAPACITY > lo]
                if not overlaps:
                    break
                target = (max(overlaps) + 0xF) & ~0xF
            if target + TABLE_CAPACITY > WINDOW_LIMIT:
                raise placement.PlacementError("The free RAM window has no room for another pickup table.")
            if len(overlay) < target + TABLE_CAPACITY:
                overlay.extend(bytes(target + TABLE_CAPACITY - len(overlay)))
            slot = payload + bytes([placement.END])
            overlay[target:target + TABLE_CAPACITY] = slot + bytes(TABLE_CAPACITY - len(slot))
            reserved.append((target, target + TABLE_CAPACITY))
            for alias, original in enumerate(addresses):
                if original == address:
                    struct.pack_into("<I", exe, pointer_at + alias * 4,
                                     placement.OVERLAY_BASE + target)
        else:
            target = old_at
            overlay[target:target + len(payload)] = payload
        updated.extend(replace(r, offset=target + i * placement.PICKUP_SIZE)
                       for i, r in enumerate(records))
    return bytes(overlay), bytes(exe), updated

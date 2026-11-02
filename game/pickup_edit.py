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
# Both native collection banks are 128 bytes. Reserve the lower half for
# shipped/scripted placements, including ones outside the persistent tables.
NEW_BIT_START = 512
BANK_BITS = 1024
NATIVE_REWARDS = 51
ITEM_TYPES = {0: "Equipment", 1: "One-time / contextual", 2: "Story / automatic"}
CUSTOM_OFFSET = 0x70000
CUSTOM_COUNT = 128
CUSTOM_TAG = b"T2EDIT_REWARDS_V1"
CUSTOM_TAG_AT = CUSTOM_OFFSET + CUSTOM_COUNT * pickup_art.REWARD.size
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


def _custom_ref_words(hi, lo):
    target = placement.OVERLAY_BASE + CUSTOM_OFFSET
    return ((hi & 0xFFFF0000) | ((target + 0x8000) >> 16 & 0xFFFF),
            (lo & 0xFFFF0000) | (target & 0xFFFF))


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


def ensure_custom_rewards(exe_path, overlay_paths, *, item_ids=(29,)):
    """Prepare a transaction adding selected inventory IDs as native rewards.

    Every area overlay receives the same table at a fixed unused address
    below the area's resource region. Five audited MAIN references then use
    it. All native definitions remain byte-identical and in native order.
    Custom entries deliberately use the resident Lunch Box presentation;
    their inventory grant and message use the selected item definition.
    The caller must commit ALL returned files together before exporting.
    """
    exe_path = str(exe_path)
    exe = Path(exe_path).read_bytes()
    if not custom_rewards_supported(exe):
        raise placement.PlacementError(
            "Custom item rewards require the verified US retail executable layout.")
    paths = {Path(p).stem.upper(): str(p) for p in overlay_paths if p}
    required = {f"A0{c}" for c in "0123456789ABCDEFGHIJKL"}
    if not required.issubset(paths):
        raise placement.PlacementError("All 22 area overlays are required to enable custom item rewards.")
    sources = {name: Path(paths[name]).read_bytes() for name in sorted(required)}
    end = CUSTOM_TAG_AT + len(CUSTOM_TAG)
    if placement.OVERLAY_BASE + end > game_build.current().area_base:
        raise placement.PlacementError("The custom reward table would overlap area resources.")
    at = pickup_art.REWARD_TABLE - struct.unpack_from("<I", exe, 0x18)[0] + placement.EXE_TEXT
    native = exe[at:at + NATIVE_REWARDS * pickup_art.REWARD.size]
    if len(native) != NATIVE_REWARDS * pickup_art.REWARD.size:
        raise placement.PlacementError("The native reward definitions are truncated.")
    tagged = [data[CUSTOM_TAG_AT:CUSTOM_TAG_AT + len(CUSTOM_TAG)] == CUSTOM_TAG
              for data in sources.values()]
    if any(tagged) and not all(tagged):
        raise placement.PlacementError("Custom reward tables are inconsistent across the project overlays.")
    table = (next(iter(sources.values()))[CUSTOM_OFFSET:CUSTOM_TAG_AT] if all(tagged)
             else native + b"".join(pickup_art.REWARD.pack(0, 0, 0, 0, -1)
                                   for _ in range(CUSTOM_COUNT - NATIVE_REWARDS)))
    if all(tagged):
        if any(data[CUSTOM_OFFSET:CUSTOM_TAG_AT] != table for data in sources.values()):
            raise placement.PlacementError("The custom item reward definitions differ between overlays.")
        if table[:len(native)] != native:
            raise placement.PlacementError("The custom table no longer matches the native reward definitions.")
    elif any(len(data) > CUSTOM_OFFSET for data in sources.values()):
        raise placement.PlacementError("An overlay already occupies the reserved custom reward address.")
    table = bytearray(table)
    mapping = {}
    free = []
    for reward in range(NATIVE_REWARDS, CUSTOM_COUNT):
        item = pickup_art.REWARD.unpack_from(table, reward * pickup_art.REWARD.size)[-1]
        if item < 0:
            free.append(reward)
        else:
            mapping[item] = reward
    # Native selector 13 is a stationary, universally resident sprite.
    template = list(pickup_art.REWARD.unpack_from(native, 13 * pickup_art.REWARD.size))
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
        reward = free.pop(0)
        template[-1] = item
        table[reward * pickup_art.REWARD.size:(reward + 1) * pickup_art.REWARD.size] = pickup_art.REWARD.pack(*template)
        mapping[item] = reward
    patched_exe = bytearray(exe)
    for hi_at, hi, lo_at, lo in REWARD_REFS:
        new_hi, new_lo = _custom_ref_words(hi, lo)
        struct.pack_into("<I", patched_exe, _word_at(exe, hi_at), new_hi)
        struct.pack_into("<I", patched_exe, _word_at(exe, lo_at), new_lo)
    # Make the table resident from boot as well. Scripted linked rewards
    # can run before the first area overlay; loading the same bytes with
    # MAIN guarantees those consumers see valid native definitions too.
    # The startup's BSS clear ends at 0x80106228, below the overlay base.
    base = struct.unpack_from("<I", exe, 0x18)[0]
    resident_at = placement.OVERLAY_BASE + CUSTOM_OFFSET - base + placement.EXE_TEXT
    resident_end = resident_at + len(table) + len(CUSTOM_TAG)
    resident_tag = exe[resident_at + len(table):resident_end]
    if (len(exe) > resident_at and resident_tag != CUSTOM_TAG
            and any(exe[resident_at:resident_end])):
        raise placement.PlacementError("MAIN.EXE already uses the reserved custom reward address.")
    padded_end = (resident_end + 0x7FF) & ~0x7FF
    if len(patched_exe) < padded_end:
        patched_exe.extend(bytes(padded_end - len(patched_exe)))
    patched_exe[resident_at:resident_at + len(table)] = table
    patched_exe[resident_at + len(table):resident_end] = CUSTOM_TAG
    struct.pack_into("<I", patched_exe, 0x1C, len(patched_exe) - placement.EXE_TEXT)
    files = {exe_path: bytes(patched_exe)}
    for name, original in sources.items():
        data = bytearray(original)
        if len(data) < end:
            data.extend(bytes(end - len(data)))
        data[CUSTOM_OFFSET:CUSTOM_TAG_AT] = table
        data[CUSTOM_TAG_AT:end] = CUSTOM_TAG
        files[paths[name]] = bytes(data)
    return CustomRewards(files, mapping)


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
        if reward >= NATIVE_REWARDS:
            label += " (shared pickup icon)"
        available = bool(art.frames)
        reason = "" if available else "This area's native sprite sequence is unavailable."
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
    tables = sorted({r.table for r in pickups})
    if table is None:
        table = next((t for t in tables
                      if sum(r.table == t for r in pickups) < MAX_TABLE_RECORDS),
                     tables[0] if tables else None)
    if table not in tables:
        raise placement.PlacementError(
            "This area has no native pickup table to extend. Its spawn code must be added first.")
    if sum(r.table == table for r in pickups) >= MAX_TABLE_RECORDS:
        raise placement.PlacementError("This native pickup table is full (32 records).")
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


def stage_pickups(overlay_bytes, exe_bytes, pickups):
    """Return (overlay, MAIN.EXE, records) ready to stage together.

    Existing-size tables are patched in place. Grown tables are appended,
    leaving every original instruction/data address untouched, and all
    matching MAIN pointer aliases are updated. No input records mutate.
    """
    overlay, exe = bytearray(overlay_bytes), bytearray(exe_bytes)
    pointer_at, addresses = _exe_table(exe)
    # MAIN's array is shared by every overlay. Equal overlay lengths must
    # not produce equal new pointer values: a later area would otherwise
    # look like an alias of the earlier area's relocated table.
    reserved = [(address - placement.OVERLAY_BASE,
                 address - placement.OVERLAY_BASE + (MAX_TABLE_RECORDS + 1) * placement.PICKUP_SIZE)
                for address in addresses if address]
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
        if growing:
            target = (len(overlay) + 3) & ~3
            capacity = (MAX_TABLE_RECORDS + 1) * placement.PICKUP_SIZE
            while True:
                overlaps = [hi for lo, hi in reserved
                            if target < hi and target + capacity > lo]
                if not overlaps:
                    break
                target = (max(overlaps) + 3) & ~3
            end = target + len(payload) + placement.PICKUP_SIZE
            limit = min(game_build.current().area_base - placement.OVERLAY_BASE,
                        placement.OVERLAY_REACH)
            if end > limit:
                raise placement.PlacementError("The overlay has no safe RAM space for more pickups.")
            overlay.extend(bytes(target - len(overlay)))
            overlay.extend(payload + bytes([placement.END]) + bytes(placement.PICKUP_SIZE - 1))
            reserved.append((target, target + capacity))
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

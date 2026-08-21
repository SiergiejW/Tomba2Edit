"""
Fixed-budget editor for usMAIN.EXE's string pool (see mainbin_parser.py
for the pool scanner/encoder this builds on).

Four pointer table regions, found by cross-referencing every known
string's RAM address against the whole exe:

  SYSTEM_MENU  0x93054-0x931c8   pure char* array (94 slots)
  AREA_NAMES   0x93364-0x933e4   pure char* array
  ITEM_DB      0x933e8-0x93bc4   struct { u32 id; char* name; char* desc; }
  QUEST_LOG    0x93bd8-0x942fc   struct { u32 id; char* short_; char* long_; }
"""

import hashlib
import struct

from functions.mainbin_parser import scan_entries, encode_bytes, MainBinParseError

# Gate against a different build (German/Spanish are 718848 bytes, laid
# out differently). Hashes only the header prefix, not the whole file,
# masking out t_size, so the tool doesn't lock itself out of its own
# saved output.
EXPECTED_FILE_SIZE = 716800
KNOWN_GOOD_PREFIX_SHA256 = "0bcbb30fa93e299480a874c831681da88a8ab1fc9e7ba076e2240a0205b05571"

EXE_HEADER_SIZE = 0x800
RAM_BASE = 0x80010000
T_SIZE_FIELD_OFFSET = 0x1C
SECTOR_SIZE = 2048  # ISO9660 allocates whole sectors regardless of a file's exact byte size

# main.bin's extent within the exe (usMAIN.EXE[0x800:0x800+18980]).
# Past this point is code, not pool text.
_MAINBIN_SIZE = 18980
POOL_REGION_END = EXE_HEADER_SIZE + _MAINBIN_SIZE

TABLES = [
    (0x93054, 0x931c8),  # system/menu messages - pure char* array
    (0x93364, 0x933e4),  # area/location names - pure char* array
    (0x933e8, 0x93bc4),  # item database - {u32 id; char* name; char* desc}
    (0x93bd8, 0x942fc),  # quest/event log - {u32 id; char* short_; char* long_}
]

# 6 of 746 entries (5 at the pool's start, 1 at its end) have no known
# table reference - tiny fragments, likely alignment artifacts, left
# untouched and excluded from the budget. Everything between them is
# "flowable": safe to tightly repack with zero gaps on every save,
# reclaiming ~1098 bytes of the original layout's own alignment padding.
FLOW_REGION_START = 0xE92  # after the pinned entries at the pool's start
FLOW_REGION_END = 0x521C   # before the pinned '!' entry at the pool's end
FLOW_CAPACITY = FLOW_REGION_END - FLOW_REGION_START


class MainBinEditError(Exception):
    pass


class UnsupportedExeError(MainBinEditError):
    pass


def verify_supported(exe_path):
    """Raises UnsupportedExeError unless exe_path is the exact build
    TABLES/POOL_REGION_END were mapped against. Call before trusting any
    offset in this module."""
    with open(exe_path, "rb") as f:
        data = bytearray(f.read())
    if len(data) < EXPECTED_FILE_SIZE:
        raise UnsupportedExeError(
            f"This MAIN.EXE is {len(data)} bytes; the build the pointer "
            f"tables in this module were mapped against is at least "
            f"{EXPECTED_FILE_SIZE} bytes (different region/revision?) - "
            f"editing it here would risk patching the wrong bytes, so "
            f"it's refused rather than guessed at."
        )
    prefix = bytearray(data[:EXE_HEADER_SIZE])
    prefix[T_SIZE_FIELD_OFFSET:T_SIZE_FIELD_OFFSET + 4] = b"\x00\x00\x00\x00"
    prefix_digest = hashlib.sha256(bytes(prefix)).hexdigest()
    if prefix_digest != KNOWN_GOOD_PREFIX_SHA256:
        raise UnsupportedExeError(
            "This MAIN.EXE's code doesn't match the build the pointer "
            "tables in this module were mapped against (same size, "
            "different revision?) - editing it here would risk patching "
            "the wrong bytes, so it's refused rather than guessed at."
        )


def _mainbin_entries(exe_path):
    """scan_entries() against the exe's own file offsets (main.bin
    shifted by EXE_HEADER_SIZE). Doesn't call verify_supported() -
    callers that trust TABLES (build_reference_index, repack_pool) gate
    on it themselves."""
    return scan_entries(
        exe_path,
        region_start=0x680 + EXE_HEADER_SIZE,
        region_end=POOL_REGION_END,
    )


def build_reference_index(exe_path, entries):
    """entry_offset (exe-relative) -> list of exe file offsets holding
    a 4-byte pointer to that entry's RAM address, across every known
    table. Empty list means no known table references it."""
    verify_supported(exe_path)

    with open(exe_path, "rb") as f:
        exe = f.read()

    entry_by_ram = {RAM_BASE + e["offset"] - EXE_HEADER_SIZE: e for e in entries}
    refs = {e["offset"]: [] for e in entries}

    for start, end in TABLES:
        for off in range(start, end + 4, 4):
            v = struct.unpack_from("<I", exe, off)[0]
            e = entry_by_ram.get(v)
            if e is not None:
                refs[e["offset"]].append(off)

    return refs


def _is_flowable(offset):
    return FLOW_REGION_START <= offset < FLOW_REGION_END


def compute_pool_state(entries, edits=None):
    """Live budget check for the flowable pool - what a tight repack
    would need given `edits` ({offset: new_text}) layered on entries'
    current text. Doesn't touch disk; safe to call on every keystroke.

    Returns {"used", "capacity", "free", "errors": {offset: message}}.
    `free` goes negative on overflow rather than raising. `errors`
    covers text that can't be encoded at all - excluded from `used`."""
    edits = edits or {}
    used = 0
    errors = {}
    for e in entries:
        if not _is_flowable(e["offset"]):
            continue
        text = edits.get(e["offset"], e["text"])
        try:
            used += len(encode_bytes(text)) + 1
        except MainBinParseError as ex:
            errors[e["offset"]] = str(ex)
    return {"used": used, "capacity": FLOW_CAPACITY, "free": FLOW_CAPACITY - used, "errors": errors}


def repack_pool(exe_path, entries, edits, output_path):
    """Rebuild the flowable pool from scratch: every flowable entry's
    current text, packed back-to-back with zero gaps, all table
    references repatched to match. Pinned entries are never touched;
    editing one raises. Raises on a bad build, invalid text, or
    over-budget total (check compute_pool_state first for live
    feedback).

    Returns {"used", "capacity", "free", "entries": [{"offset",
    "old_text", "new_text", "old_pool_offset", "new_pool_offset"}]}."""
    verify_supported(exe_path)

    with open(exe_path, "rb") as f:
        exe = bytearray(f.read())

    by_offset = {e["offset"]: e for e in entries}
    unknown = set(edits) - set(by_offset)
    if unknown:
        raise MainBinEditError(f"Unknown entry offset(s): {sorted(hex(o) for o in unknown)}")

    pinned_edits = {o for o in edits if not _is_flowable(o)}
    if pinned_edits:
        raise MainBinEditError(
            f"Entry offset(s) {sorted(hex(o) for o in pinned_edits)} have no known "
            f"table reference and can never be edited - their text/position is "
            f"fixed, not part of the flowable pool."
        )

    refs = build_reference_index(exe_path, entries)
    flowable = [e for e in entries if _is_flowable(e["offset"])]

    new_pool = bytearray()
    new_offset_of = {}
    for e in flowable:
        text = edits.get(e["offset"], e["text"])
        try:
            encoded = encode_bytes(text)
        except MainBinParseError as ex:
            raise MainBinEditError(f"Entry at offset {e['offset']:#06x}: {ex}") from ex
        new_offset_of[e["offset"]] = FLOW_REGION_START + len(new_pool)
        new_pool += encoded + b"\x00"

    used = len(new_pool)
    if used > FLOW_CAPACITY:
        raise MainBinEditError(
            f"Text pool overflow: {used} byte(s) needed, only {FLOW_CAPACITY} "
            f"available ({used - FLOW_CAPACITY} byte(s) over budget). Shorten "
            f"some entries before saving."
        )
    new_pool += b"\x00" * (FLOW_CAPACITY - used)
    exe[FLOW_REGION_START:FLOW_REGION_END] = new_pool

    report = {"used": used, "capacity": FLOW_CAPACITY, "free": FLOW_CAPACITY - used, "entries": []}
    for e in flowable:
        old_offset = e["offset"]
        new_offset = new_offset_of[old_offset]
        new_text = edits.get(old_offset, e["text"])
        new_ram = RAM_BASE + (new_offset - EXE_HEADER_SIZE)
        packed_ptr = struct.pack("<I", new_ram)
        for slot in refs.get(old_offset, []):
            exe[slot:slot + 4] = packed_ptr
        report["entries"].append({
            "offset": old_offset, "old_text": e["text"], "new_text": new_text,
            "old_pool_offset": old_offset, "new_pool_offset": new_offset,
        })

    with open(output_path, "wb") as f:
        f.write(exe)

    return report

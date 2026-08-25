"""
Fixed-budget editor for MAIN.EXE's string pool (see mainbin_parser.py
for the pool scanner/encoder this builds on).

Each build (English/Spanish/German) relinks the executable at a
different absolute offset, but the pool layout itself is identical:
the same leading pinned fragments at the same file offset, the same
pointer-table structure (just at different addresses), and the same
single pinned '!' entry closing the pool. BUILDS below records each
build's own table/pool offsets, found by the same technique the
English ones were: scan the pool for text, then search the whole exe
for 4-byte pointers to each entry's RAM address - the hits cluster
into the table ranges below.
"""

import hashlib
import struct

from gui.mainbin.mainbin_parser import scan_entries, encode_bytes, MainBinParseError

EXE_HEADER_SIZE = 0x800
RAM_BASE = 0x80010000
T_SIZE_FIELD_OFFSET = 0x1C
SECTOR_SIZE = 2048  # ISO9660 allocates whole sectors regardless of a file's exact byte size

TEXT_REGION_START = 0x680 + EXE_HEADER_SIZE  # 0xE80 - identical across every known build

BUILDS = {
    "en": {
        "label": "English",
        "file_size": 716800,
        "prefix_sha256": "0bcbb30fa93e299480a874c831681da88a8ab1fc9e7ba076e2240a0205b05571",
        "tables": [
            {"range": (0x93054, 0x931c8), "label": "System / Menu"},
            {"range": (0x93364, 0x933e4), "label": "Area Names"},
            {"range": (0x933e8, 0x93bc4), "label": "Item Database"},
            {"range": (0x93bd8, 0x942fc), "label": "Quest / Event Log"},
        ],
        "flow_region_start": 0xE92,
        "flow_region_end": 0x521C,
        "scan_end": 0x5224,
    },
    "es": {
        "label": "Spanish",
        "file_size": 718848,
        "prefix_sha256": "d7c44106b2be320600977a65c983ea54e15eaa6fbefbfb0e9e30bf6ed4e4dd9a",
        "tables": [
            {"range": (0x93b60, 0x93ce0), "label": "System / Menu"},
            {"range": (0x93e7c, 0x946dc), "label": "Area Names / Item Database"},
            {"range": (0x946f0, 0x94e14), "label": "Quest / Event Log"},
        ],
        "flow_region_start": 0xE92,
        "flow_region_end": 0x56D4,
        "scan_end": 0x56DC,
    },
    "de": {
        "label": "German",
        "file_size": 718848,
        "prefix_sha256": "63dcbd62e2bf281c225fac1a5ae97ed1f4f6a511aa9f2718d8ac0e35374d1440",
        "tables": [
            {"range": (0x93930, 0x93ab0), "label": "System / Menu"},
            {"range": (0x93c4c, 0x944ac), "label": "Area Names / Item Database"},
            {"range": (0x944c0, 0x94be8), "label": "Quest / Event Log"},
        ],
        "flow_region_start": 0xE92,
        "flow_region_end": 0x53EC,
        "scan_end": 0x53F4,
    },
}


class MainBinEditError(Exception):
    pass


class UnsupportedExeError(MainBinEditError):
    pass


def _prefix_digest(data):
    prefix = bytearray(data[:EXE_HEADER_SIZE])
    prefix[T_SIZE_FIELD_OFFSET:T_SIZE_FIELD_OFFSET + 4] = b"\x00\x00\x00\x00"
    return hashlib.sha256(bytes(prefix)).hexdigest()


def detect_build(exe_path):
    """Returns the matching entry from BUILDS for exe_path, or raises
    UnsupportedExeError. Call before trusting any offset in this module."""
    with open(exe_path, "rb") as f:
        data = f.read()
    digest = _prefix_digest(data)
    for build in BUILDS.values():
        if len(data) == build["file_size"] and digest == build["prefix_sha256"]:
            return build
    known = ", ".join(b["label"] for b in BUILDS.values())
    raise UnsupportedExeError(
        f"This MAIN.EXE ({len(data)} bytes) doesn't match any build this tool "
        f"knows the pointer tables for ({known}) - editing it here would risk "
        f"patching the wrong bytes, so it's refused rather than guessed at."
    )


def verify_supported(exe_path):
    """Raises UnsupportedExeError unless exe_path is a known build."""
    detect_build(exe_path)


def _heuristic_scan_end(exe_path, window=12, noise_threshold=0.5, probe_len=60000):
    """For a build with no known table mapping: estimate where real pool
    text stops and code begins, by scanning forward and stopping at the
    FIRST sustained run of mostly non-printable/escaped-byte entries
    (code misread as text) - not the last such run found, since code
    often contains scattered legitimate debug strings further out that
    would otherwise pull the boundary too far past the real pool. Good
    enough for read-only viewing - NOT precise enough to trust for
    editing, since there's no known pointer table to repatch."""
    entries = scan_entries(exe_path, region_start=TEXT_REGION_START, region_end=TEXT_REGION_START + probe_len)
    if not entries:
        return TEXT_REGION_START
    boundary_idx = len(entries)
    for i in range(len(entries) - window):
        seg = entries[i:i + window]
        total = sum(len(e["text"]) for e in seg) or 1
        escaped = sum(e["text"].count("{$") * 6 for e in seg)
        if escaped / total > noise_threshold:
            boundary_idx = max(i, 1)
            break
    last = entries[boundary_idx - 1]
    return last["offset"] + last["length"] + 1


def _mainbin_entries(exe_path):
    """scan_entries() against the exe's own file offsets, bounded by the
    detected build's own pool extent - or, for an unmapped build, a
    heuristic estimate (see _heuristic_scan_end) good enough to browse
    but not to trust for editing."""
    try:
        region_end = detect_build(exe_path)["scan_end"]
    except UnsupportedExeError:
        region_end = _heuristic_scan_end(exe_path)
    return scan_entries(exe_path, region_start=TEXT_REGION_START, region_end=region_end)


def build_reference_index(exe_path, entries):
    """entry_offset (exe-relative) -> list of exe file offsets holding
    a 4-byte pointer to that entry's RAM address, across every known
    table. Empty list means no known table references it."""
    build = detect_build(exe_path)

    with open(exe_path, "rb") as f:
        exe = f.read()

    entry_by_ram = {RAM_BASE + e["offset"] - EXE_HEADER_SIZE: e for e in entries}
    refs = {e["offset"]: [] for e in entries}

    for table in build["tables"]:
        start, end = table["range"]
        for off in range(start, end + 4, 4):
            v = struct.unpack_from("<I", exe, off)[0]
            e = entry_by_ram.get(v)
            if e is not None:
                refs[e["offset"]].append(off)

    return refs


PINNED_CATEGORY = "Pinned"


def categorize_entries(exe_path, entries):
    """{entry_offset: table_label} for every entry - which of BUILDS'
    named tables (see module docstring) references it, or
    PINNED_CATEGORY for the 6 entries with no known reference at all.
    Purely a GUI grouping aid - has no bearing on what's editable
    (that's still _is_flowable/FLOW_REGION_START/END)."""
    build = detect_build(exe_path)

    with open(exe_path, "rb") as f:
        exe = f.read()

    entry_by_ram = {RAM_BASE + e["offset"] - EXE_HEADER_SIZE: e for e in entries}
    categories = {e["offset"]: PINNED_CATEGORY for e in entries}

    for table in build["tables"]:
        start, end = table["range"]
        for off in range(start, end + 4, 4):
            v = struct.unpack_from("<I", exe, off)[0]
            e = entry_by_ram.get(v)
            if e is not None:
                categories[e["offset"]] = table["label"]

    return categories


def _is_flowable(offset, build):
    if build is None:
        return False
    return build["flow_region_start"] <= offset < build["flow_region_end"]


def compute_pool_state(entries, build, edits=None):
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
        if not _is_flowable(e["offset"], build):
            continue
        text = edits.get(e["offset"], e["text"])
        try:
            used += len(encode_bytes(text)) + 1
        except MainBinParseError as ex:
            errors[e["offset"]] = str(ex)
    capacity = build["flow_region_end"] - build["flow_region_start"]
    return {"used": used, "capacity": capacity, "free": capacity - used, "errors": errors}


def repack_pool(exe_path, entries, edits, output_path):
    """Rebuild the flowable pool from scratch: every flowable entry's
    current text, packed back-to-back with zero gaps, all table
    references repatched to match. Pinned entries are never touched;
    editing one raises. Raises on a bad build, invalid text, or
    over-budget total (check compute_pool_state first for live
    feedback).

    Returns {"used", "capacity", "free", "entries": [{"offset",
    "old_text", "new_text", "old_pool_offset", "new_pool_offset"}]}."""
    build = detect_build(exe_path)
    flow_start, flow_end = build["flow_region_start"], build["flow_region_end"]
    capacity = flow_end - flow_start

    with open(exe_path, "rb") as f:
        exe = bytearray(f.read())

    by_offset = {e["offset"]: e for e in entries}
    unknown = set(edits) - set(by_offset)
    if unknown:
        raise MainBinEditError(f"Unknown entry offset(s): {sorted(hex(o) for o in unknown)}")

    pinned_edits = {o for o in edits if not _is_flowable(o, build)}
    if pinned_edits:
        raise MainBinEditError(
            f"Entry offset(s) {sorted(hex(o) for o in pinned_edits)} have no known "
            f"table reference and can never be edited - their text/position is "
            f"fixed, not part of the flowable pool."
        )

    refs = build_reference_index(exe_path, entries)
    flowable = [e for e in entries if _is_flowable(e["offset"], build)]

    new_pool = bytearray()
    new_offset_of = {}
    for e in flowable:
        text = edits.get(e["offset"], e["text"])
        try:
            encoded = encode_bytes(text)
        except MainBinParseError as ex:
            raise MainBinEditError(f"Entry at offset {e['offset']:#06x}: {ex}") from ex
        new_offset_of[e["offset"]] = flow_start + len(new_pool)
        new_pool += encoded + b"\x00"

    used = len(new_pool)
    if used > capacity:
        raise MainBinEditError(
            f"Text pool overflow: {used} byte(s) needed, only {capacity} "
            f"available ({used - capacity} byte(s) over budget). Shorten "
            f"some entries before saving."
        )
    new_pool += b"\x00" * (capacity - used)
    exe[flow_start:flow_end] = new_pool

    report = {"used": used, "capacity": capacity, "free": capacity - used, "entries": []}
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

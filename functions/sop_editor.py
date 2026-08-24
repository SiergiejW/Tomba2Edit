"""
Fixed-budget editor for BIN/SOP.BIN's intro story-crawl text.

SOP.BIN is a raw overlay (no PS-EXE header) found alongside MAIN.EXE at
BIN/SOP.BIN on the disc. Its first 0x58 bytes are a 20-entry table
whose values are always base+constant offsets - cross-checked
identical (same offset, every entry) across English/German/Spanish/
Japanese builds - so those aren't text pointers.

The 12 story lines ARE individually addressed, though: a separate
table further into the file (REF_TABLE below) holds each line's own
RAM address, used by the scroll-animation code to know which line is
current at a given frame - some slots repeat a line's address across
several consecutive "hold" frames, and some point at blank padding
between lines, but every one of the 12 lines appears there exactly
once. This was missed in an earlier version of this module (which
assumed no such table existed and tightly repacked the lines assuming
they were read start-to-finish) - confirmed wrong by a real in-game
test, and the real table found afterward by brute-force address
search, cross-validated across all three known builds the same way
mainbin_editor.py's TABLES were.

With that table known, this now works exactly like mainbin_editor.py's
pool: a fixed-capacity region, tightly repacked on save, every line's
reference re-patched to match its new position. The pinned trailing
filler byte (alignment padding, not a displayed line - see
FLOW_REGION_END) has no reference anywhere and is never touched.
"""

import hashlib
import struct

from functions.mainbin_parser import scan_entries, encode_bytes, MainBinParseError

TEXT_REGION_START = 0x58  # fixed header size, identical across every known build

BUILDS = {
    "en": {
        "label": "English",
        "file_size": 17660,
        "prefix_sha256": "937a5f1eaebd6d9026206e3d2136e358843b176d037c7de4de05a19ebd103869",
        "ram_base": 0x80108F9C,
        "flow_region_end": 0x1C8,
        "ref_table": (0x4384, 0x43EC),
    },
    "de": {
        "label": "German",
        "file_size": 17696,
        "prefix_sha256": "e3e51320baafff8c79993f4d8a166fd5ae5ac7afb07574de572ccf0ef04193b1",
        "ram_base": 0x80109F24,
        "flow_region_end": 0x1E0,
        "ref_table": (0x43A8, 0x4410),
    },
    "es": {
        "label": "Spanish",
        "file_size": 17708,
        "prefix_sha256": "37af70a0028b2a93a5401d7d99f0333cfd486bdea4a26cf1e8b2d87b3a0eeeba",
        "ram_base": 0x8010A154,
        "flow_region_end": 0x1EC,
        "ref_table": (0x43B4, 0x441C),
    },
}


class SopEditError(Exception):
    pass


class UnsupportedSopError(SopEditError):
    pass


def detect_build(sop_path):
    """Returns the matching entry from BUILDS for sop_path, or raises
    UnsupportedSopError."""
    with open(sop_path, "rb") as f:
        data = f.read()
    digest = hashlib.sha256(data[:TEXT_REGION_START]).hexdigest()
    for build in BUILDS.values():
        if len(data) == build["file_size"] and digest == build["prefix_sha256"]:
            return build
    known = ", ".join(b["label"] for b in BUILDS.values())
    raise UnsupportedSopError(
        f"This SOP.BIN ({len(data)} bytes) doesn't match any build this tool "
        f"knows the text layout for ({known}) - editing it here would risk "
        f"corrupting the file, so it's refused rather than guessed at."
    )


def verify_supported(sop_path):
    """Raises UnsupportedSopError unless sop_path is a known build."""
    detect_build(sop_path)


def _heuristic_scan_end(sop_path, window=12, noise_threshold=0.5, probe_len=4000):
    """For a build with no known text-layout mapping (e.g. Japanese, or
    any other unrecognized SOP.BIN): estimate where the story text ends
    and code begins, the same way mainbin_editor's fallback does. Good
    enough for read-only viewing only."""
    entries = scan_entries(sop_path, region_start=TEXT_REGION_START, region_end=TEXT_REGION_START + probe_len)
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


def sop_entries(sop_path):
    """The story's lines, in file order, as [{"offset", "length", "text"}] -
    or, for an unmapped build, a heuristic estimate (see
    _heuristic_scan_end) good enough to browse but not to trust for
    editing."""
    try:
        region_end = detect_build(sop_path)["flow_region_end"]
    except UnsupportedSopError:
        region_end = _heuristic_scan_end(sop_path)
    return scan_entries(sop_path, region_start=TEXT_REGION_START, region_end=region_end)


def build_reference_index(sop_path, entries):
    """entry_offset -> list of file offsets holding a 4-byte pointer to
    that entry's RAM address, anywhere in the scroll-animation's
    reference table. Empty list means no reference (the pinned
    trailing filler)."""
    build = detect_build(sop_path)
    with open(sop_path, "rb") as f:
        data = f.read()

    entry_by_ram = {build["ram_base"] + e["offset"]: e for e in entries}
    refs = {e["offset"]: [] for e in entries}

    start, end = build["ref_table"]
    for off in range(start, end + 4, 4):
        v = struct.unpack_from("<I", data, off)[0]
        e = entry_by_ram.get(v)
        if e is not None:
            refs[e["offset"]].append(off)

    return refs


def _is_flowable(offset, entries):
    """Every line except the trailing filler fragment (see module
    docstring) - matches whichever entry build_reference_index() found
    no reference for, by construction the last one in file order."""
    return bool(entries) and offset != entries[-1]["offset"]


def compute_pool_state(entries, build, edits=None):
    """Live budget check - what a tight repack would need right now,
    given `edits` ({offset: new_text}) layered on entries' current
    text. Doesn't touch disk; safe to call on every keystroke.

    Returns {"used", "capacity", "free", "errors": {offset: message}}.
    `free` goes negative on overflow rather than raising."""
    edits = edits or {}
    used = 0
    errors = {}
    for e in entries:
        if not _is_flowable(e["offset"], entries):
            continue
        text = edits.get(e["offset"], e["text"])
        try:
            used += len(encode_bytes(text)) + 1
        except MainBinParseError as ex:
            errors[e["offset"]] = str(ex)
    capacity = (entries[-1]["offset"] if entries else build["flow_region_end"]) - TEXT_REGION_START
    return {"used": used, "capacity": capacity, "free": capacity - used, "errors": errors}


def repack_pool(sop_path, entries, edits, output_path):
    """Rebuild the story text from scratch: every flowable line's
    current text (edited or original) packed back-to-back with zero
    gaps, every reference to it re-patched to its new address (see
    build_reference_index). The pinned trailing filler is never moved.

    Raises SopEditError on a bad build, an edit targeting the pinned
    filler, invalid text, or an over-budget total (check
    compute_pool_state first for live feedback).

    Returns {"used", "capacity", "free", "entries": [{"offset",
    "old_text", "new_text", "old_pool_offset", "new_pool_offset"}]}."""
    build = detect_build(sop_path)
    flow_end = build["flow_region_end"]

    with open(sop_path, "rb") as f:
        data = bytearray(f.read())

    by_offset = {e["offset"]: e for e in entries}
    unknown = set(edits) - set(by_offset)
    if unknown:
        raise SopEditError(f"Unknown entry offset(s): {sorted(hex(o) for o in unknown)}")

    pinned_edits = {o for o in edits if not _is_flowable(o, entries)}
    if pinned_edits:
        raise SopEditError(
            f"Entry offset(s) {sorted(hex(o) for o in pinned_edits)} have no known "
            f"reference and can never be edited - alignment padding, not a "
            f"displayed line."
        )

    refs = build_reference_index(sop_path, entries)
    flowable = [e for e in entries if _is_flowable(e["offset"], entries)]
    # the pinned filler's own scanned offset is the true end of usable
    # space - not derived from flow_region_end, since the filler's span
    # doesn't always land exactly on that boundary (off by a byte or two
    # depending on build - confirmed by direct check, not assumed).
    flow_end_usable = entries[-1]["offset"] if len(entries) > len(flowable) else flow_end

    new_pool = bytearray()
    new_offset_of = {}
    for e in flowable:
        text = edits.get(e["offset"], e["text"])
        try:
            encoded = encode_bytes(text)
        except MainBinParseError as ex:
            raise SopEditError(f"Line at offset {e['offset']:#06x}: {ex}") from ex
        new_offset_of[e["offset"]] = TEXT_REGION_START + len(new_pool)
        new_pool += encoded + b"\x00"

    used = len(new_pool)
    capacity = flow_end_usable - TEXT_REGION_START
    if used > capacity:
        raise SopEditError(
            f"Text pool overflow: {used} byte(s) needed, only {capacity} "
            f"available ({used - capacity} byte(s) over budget). Shorten "
            f"some lines before saving."
        )
    new_pool += b"\x00" * (capacity - used)
    data[TEXT_REGION_START:flow_end_usable] = new_pool
    # pinned entries keep their original bytes untouched - nothing to write

    report = {"used": used, "capacity": capacity, "free": capacity - used, "entries": []}
    for e in flowable:
        old_offset = e["offset"]
        new_offset = new_offset_of[old_offset]
        new_text = edits.get(old_offset, e["text"])
        new_ram = build["ram_base"] + new_offset
        packed_ptr = struct.pack("<I", new_ram)
        for slot in refs.get(old_offset, []):
            data[slot:slot + 4] = packed_ptr
        report["entries"].append({
            "offset": old_offset, "old_text": e["text"], "new_text": new_text,
            "old_pool_offset": old_offset, "new_pool_offset": new_offset,
        })

    with open(output_path, "wb") as f:
        f.write(data)

    return report

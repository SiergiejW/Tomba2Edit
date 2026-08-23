"""
Fixed-position editor for BIN/SOP.BIN's intro story-crawl text.

SOP.BIN is a raw overlay (no PS-EXE header) found alongside MAIN.EXE at
BIN/SOP.BIN on the disc. Its first 0x58 bytes are a fixed 20-entry
table whose values are always base+constant offsets - cross-checked
identical (same offset, every entry) across English/German/Spanish/
Japanese builds, so none of them are text pointers or reference the
story lines at all.

Each story line is read from its OWN fixed file offset, independently
of the others - confirmed the hard way, by a real in-game test: an
earlier version of this module tightly repacked the lines (reclaiming
inter-line padding, like mainbin_editor.py's pool), and every line
after the first one whose length changed displayed as a garbled
mid-string fragment. Since there's no scannable pointer table for
these lines (unlike MAIN.EXE's pool), each is almost certainly
addressed by a hardcoded immediate load baked directly into code - so
a line can never move or grow past its own original span. Editing here
is therefore same-position, same-or-shorter-length only, exactly like
the very first (pre-flowable-pool) MAIN.EXE editor.
"""

import hashlib

from functions.mainbin_parser import scan_entries, encode_bytes, MainBinParseError

TEXT_REGION_START = 0x58  # fixed header size, identical across every known build

BUILDS = {
    "en": {
        "label": "English",
        "file_size": 17660,
        "prefix_sha256": "937a5f1eaebd6d9026206e3d2136e358843b176d037c7de4de05a19ebd103869",
        "flow_region_end": 0x1C8,
    },
    "de": {
        "label": "German",
        "file_size": 17696,
        "prefix_sha256": "e3e51320baafff8c79993f4d8a166fd5ae5ac7afb07574de572ccf0ef04193b1",
        "flow_region_end": 0x1E0,
    },
    "es": {
        "label": "Spanish",
        "file_size": 17708,
        "prefix_sha256": "37af70a0028b2a93a5401d7d99f0333cfd486bdea4a26cf1e8b2d87b3a0eeeba",
        "flow_region_end": 0x1EC,
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


def line_state(entry, edits=None):
    """Live per-line budget check: how many bytes `entry`'s current text
    (edited or original) encodes to, against its fixed original span.
    Doesn't touch disk; safe to call on every keystroke.

    Returns {"used", "capacity", "free", "error": message-or-None}.
    `free` goes negative once `used` exceeds the line's own original
    length - that's the overflow amount, not an exception; only
    repack_pool() itself raises, and only when actually asked to save
    a line that's still over its own budget."""
    edits = edits or {}
    text = (edits or {}).get(entry["offset"], entry["text"])
    capacity = entry["length"]
    try:
        used = len(encode_bytes(text))
        error = None
    except MainBinParseError as ex:
        used = 0
        error = str(ex)
    return {"used": used, "capacity": capacity, "free": capacity - used, "error": error}


def repack_pool(sop_path, entries, edits, output_path):
    """Write each line back into EXACTLY its own original file span -
    never shifts, never borrows space from a neighboring line (see
    module docstring for why). A shortened line is padded with extra
    0x00 bytes up to its original span; scan_entries() already skips
    runs of 0x00 one byte at a time, so the padding is harmless on a
    future re-scan.

    Raises SopEditError on a bad build, invalid text, or a line whose
    encoded length exceeds its original (check line_state() first for
    live per-line feedback).

    Returns {"entries": [{"offset", "old_text", "new_text"}]}."""
    detect_build(sop_path)

    with open(sop_path, "rb") as f:
        data = bytearray(f.read())

    by_offset = {e["offset"]: e for e in entries}
    unknown = set(edits) - set(by_offset)
    if unknown:
        raise SopEditError(f"Unknown entry offset(s): {sorted(hex(o) for o in unknown)}")

    report_entries = []
    for e in entries:
        text = edits.get(e["offset"], e["text"])
        try:
            encoded = encode_bytes(text)
        except MainBinParseError as ex:
            raise SopEditError(f"Line at offset {e['offset']:#06x}: {ex}") from ex
        if len(encoded) > e["length"]:
            raise SopEditError(
                f"Line at offset {e['offset']:#06x} encodes to {len(encoded)} byte(s), "
                f"longer than its original {e['length']} - this line is read from a "
                f"fixed address hardcoded in the game's own code, not a relocatable "
                f"table, so it can't grow. Shorten it to fit, or pad with spaces."
            )
        span = e["length"] + 1  # + terminator
        data[e["offset"]:e["offset"] + span] = encoded + b"\x00" * (span - len(encoded))
        report_entries.append({"offset": e["offset"], "old_text": e["text"], "new_text": text})

    with open(output_path, "wb") as f:
        f.write(data)

    return {"entries": report_entries}

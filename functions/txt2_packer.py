"""
Reverse of gui/txtd/txt2.py's preview(). Rebuilds a TXT2 chunk from the
entries list preview() produces.

entries must stay in physical file order (gap and table entries
interleaved as preview() found them) - bucketing gaps separately from
table entries corrupts files that have gaps inside the table region,
not just before entry_root.

Doesn't preserve tail-sharing, so re-saved files run a bit larger than
the original even unedited.
"""

import struct

from functions.txtd_packer import encode_text, TxtdPackError, _align_up, MHSIZE, ALIGN

# Marker text.preview() uses for a table entry whose pointer resolves
# outside this file's own bytes. Not a real tombadict token.
OOB_MARKER = "{$OOB}"


class Txt2PackError(TxtdPackError):
    pass


def pack_txt2(txt2_data):
    """
    txt2_data: {"entries": [{"adr", "extra", "text", "is_gap"}, ...]},
    in physical file order (see module docstring).

    adr==extra==0xFFFF is the END sentinel, preserved as-is. Text still
    exactly "{$OOB}" keeps its original adr/extra instead of being
    encoded.
    """
    entries = txt2_data.get("entries", [])

    # leading gap entries are the only text that can sit before entry_root
    split = 0
    while split < len(entries) and entries[split].get("is_gap"):
        split += 1
    leading_gaps = entries[:split]
    body = entries[split:]

    table_entries = [e for e in body if not e.get("is_gap")]
    entry_amount = len(table_entries)

    entry_table_raw_end = MHSIZE + entry_amount * 4
    table_region_end = _align_up(entry_table_raw_end, ALIGN)
    entry_table_pad = table_region_end - entry_table_raw_end

    leading_gap_blob = bytearray()
    for gap_entry in leading_gaps:
        try:
            leading_gap_blob += encode_text(gap_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e

    # first table entry may have a "lead_in_len" prefix that belongs
    # before entry_root (see txt2.py) - split it back out
    first_table_entry = table_entries[0] if table_entries else None
    first_entry_encoded = None
    first_entry_split = 0
    lead_in_bytes = b""
    if first_table_entry is not None and first_table_entry.get("lead_in_len", 0) > 0:
        try:
            first_entry_encoded = encode_text(first_table_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e
        first_entry_split = min(first_table_entry["lead_in_len"], len(first_entry_encoded))
        lead_in_bytes = first_entry_encoded[:first_entry_split]

    # padding goes before lead_in_bytes so it stays flush against entry_root
    entry_root_rel = _align_up(table_region_end + len(leading_gap_blob) + len(lead_in_bytes), ALIGN)
    leading_gap_pad = entry_root_rel - len(lead_in_bytes) - (table_region_end + len(leading_gap_blob))
    entry_root_raw = (entry_root_rel - MHSIZE) // 4

    if entry_root_raw > 0xFFFF or entry_amount > 0xFFFF:
        raise Txt2PackError(
            "TXT2 block too large to encode (entry_root_raw={}, "
            "entry_amount={}) - both must fit in 16 bits."
            .format(entry_root_raw, entry_amount)
        )

    entry_table = bytearray()
    pool_blob = bytearray()
    for entry in body:
        if entry.get("is_gap"):
            # unaddressed - just occupies pool space
            try:
                pool_blob += encode_text(entry["text"])
            except TxtdPackError as e:
                raise Txt2PackError(str(e)) from e
            continue

        is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)
        if is_sentinel:
            entry_table += struct.pack("<HH", 0xFFFF, 0xFFFF)
            continue

        if entry.get("text") == OOB_MARKER:
            # dead slot - force an address that stays out of bounds
            # regardless of pool size (reusing the original adr can land
            # back in-bounds once the pool is rebuilt)
            extra = entry.get("extra", 0) & 0xFFFF
            dead_adr = 0xFFFF if extra != 0xFFFF else 0xFFFE
            entry_table += struct.pack("<HH", dead_adr, extra)
            continue

        if entry is first_table_entry and first_entry_encoded is not None:
            encoded = first_entry_encoded[first_entry_split:]
        else:
            try:
                encoded = encode_text(entry["text"])
            except TxtdPackError as e:
                raise Txt2PackError(str(e)) from e

            lead_in_len = entry.get("lead_in_len", 0)
            if lead_in_len > 0:
                # mid-pool merge - lead-in half goes unaddressed, like a gap entry
                split = min(lead_in_len, len(encoded))
                pool_blob += encoded[:split]
                encoded = encoded[split:]

        adr = len(pool_blob)
        if adr > 0xFFFF:
            raise Txt2PackError(
                "Text overflowed 64KB in this TXT2 block's text pool - "
                "shorten the text."
            )
        extra = entry.get("extra", 0)
        entry_table += struct.pack("<HH", adr, extra)
        pool_blob += encoded

    header = struct.pack("<HH12x", entry_root_raw, entry_amount)

    out = bytearray()
    out += header
    out += entry_table
    out += b"\xFF" * entry_table_pad
    out += leading_gap_blob
    out += b"\xFF" * leading_gap_pad
    out += lead_in_bytes
    out += pool_blob

    pad = (-len(out)) % 4
    if pad:
        out += b"\xFF" * pad

    return bytes(out)

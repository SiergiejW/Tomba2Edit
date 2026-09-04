"""
Reverse of the TXT2 reader logic. Rebuilds a TXT2 chunk from the
entries list produced during preview/parsing.

Entries must stay in physical file order (gap and table entries
interleaved as discovered) to preserve correct data structures.
"""

import struct

from gui.txtd.txtd_packer import (
    encode_text, pointer_shift, TxtdPackError, _align_up, MHSIZE, ALIGN,
    _REVERSE_LETTERS)

# Marker used for a table entry whose pointer resolves outside
# this file's own bytes.
OOB_MARKER = "{$OOB}"

# Minimum own remainder bytes required for split text entries
MIN_OWN_REMAINDER = 1


class Txt2PackError(TxtdPackError):
    pass


def pack_txt2(txt2_data):
    """
    Packs standard dual-pointer TXT2 blocks (Model ID = 2).
    """
    entries = txt2_data.get("entries", [])

    # Separate leading gap entries that sit before entry_root
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

    # Handle lead-in splits for the first table entry safely
    first_table_entry = table_entries[0] if table_entries else None
    first_entry_encoded = None
    first_entry_split = 0
    lead_in_bytes = b""

    if first_table_entry is not None and first_table_entry.get("lead_in_len", 0) > 0:
        try:
            first_entry_encoded = encode_text(first_table_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e

        if len(first_entry_encoded) > first_table_entry["lead_in_len"]:
            first_entry_split = first_table_entry["lead_in_len"]
            lead_in_bytes = first_entry_encoded[:first_entry_split]

    entry_root_rel = _align_up(table_region_end + len(leading_gap_blob) + len(lead_in_bytes), ALIGN)
    leading_gap_pad = entry_root_rel - len(lead_in_bytes) - (table_region_end + len(leading_gap_blob))
    entry_root_raw = (entry_root_rel - MHSIZE) // 4

    if entry_root_raw > 0xFFFF or entry_amount > 0xFFFF:
        raise Txt2PackError(
            f"TXT2 block too large to encode (entry_root_raw={entry_root_raw}, "
            f"entry_amount={entry_amount}) - both must fit in 16 bits."
        )

    shift = pointer_shift()
    entry_table = bytearray()
    pool_blob = bytearray()

    for entry in body:
        if entry.get("is_gap"):
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
            if lead_in_len > 0 and len(encoded) > lead_in_len:
                split_idx = lead_in_len
                pool_blob += encoded[:split_idx]
                encoded = encoded[split_idx:]

        adr = len(pool_blob) >> shift
        if adr > 0xFFFF:
            raise Txt2PackError("Text overflowed 64KB in this TXT2 block's text pool.")

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


def pack_txt2_flat(txt2_data):
    """
    Packs flat-pointer TXT2 blocks (Model ID = 3).
    Ensures safe offset bounds and suffix reuse without generating
    out-of-bounds mapping crashes.

    Nothing calls this - id 3 is packed by pack_txt2_simple - so its
    pointer arithmetic has never been put in front of the Japanese
    disc's unit-counting pointers. It refuses that disc rather than
    write a file whose every pointer is twice where it should be.
    """
    if pointer_shift():
        raise Txt2PackError(
            "pack_txt2_flat has no Japanese path - id 3 is packed by "
            "pack_txt2_simple, which does.")

    entries = txt2_data.get("entries", [])

    split = 0
    while split < len(entries) and entries[split].get("is_gap"):
        split += 1
    leading_gaps = entries[:split]
    body = entries[split:]

    table_entries = [e for e in body if not e.get("is_gap")]
    pointer_count = len(table_entries)
    raw_count = pointer_count + 1  # +1 for the trailing 0xFFFF sentinel

    entry_table_raw_end = MHSIZE + pointer_count * 2 + 2
    table_region_end = _align_up(entry_table_raw_end, ALIGN)
    entry_table_pad = table_region_end - entry_table_raw_end

    leading_gap_blob = bytearray()
    gap_offsets = []
    for gap_entry in leading_gaps:
        gap_offsets.append(len(leading_gap_blob))
        try:
            leading_gap_blob += encode_text(gap_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e

    # How many leading gaps are ALSO dual-purpose verb-suffix table
    # slots - NOT the same as len(leading_gaps) (some leading gaps, e.g.
    # "Magic Gauge grew!", are just unrelated unaddressed filler that
    # happens to also sit before entry_root). Computed once, in the
    # reader, against the file's own original data - see preview()'s own
    # comment on this for why it can't be safely re-derived here.
    verb_suffix_count = min(txt2_data.get("verb_suffix_count", 0), len(leading_gaps))

    first_table_entry = table_entries[0] if table_entries else None
    first_entry_encoded = None
    first_entry_split = 0
    lead_in_bytes = b""

    if first_table_entry is not None and first_table_entry.get("lead_in_len", 0) > 0:
        try:
            first_entry_encoded = encode_text(first_table_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e

        if len(first_entry_encoded) - first_table_entry["lead_in_len"] >= MIN_OWN_REMAINDER:
            first_entry_split = first_table_entry["lead_in_len"]
            lead_in_bytes = first_entry_encoded[:first_entry_split]

    entry_root_rel = _align_up(table_region_end + len(leading_gap_blob) + len(lead_in_bytes), ALIGN)
    leading_gap_pad = entry_root_rel - len(lead_in_bytes) - (table_region_end + len(leading_gap_blob))
    # id 3's entry_root_raw is a halfword (2-byte) count, not a word count -
    # see txt2.preview()'s own comment on this, confirmed directly against
    # the pristine retail file.
    entry_root_raw = (entry_root_rel - MHSIZE) // 2

    if entry_root_raw > 0xFFFF or raw_count > 0xFFFF:
        raise Txt2PackError(
            f"TXT2 block too large to encode (entry_root_raw={entry_root_raw}, "
            f"raw_count={raw_count}) - both must fit in 16 bits."
        )

    entry_table = bytearray()
    pool_blob = bytearray()
    oob_patches = []
    verb_slot_patches = []
    verb_slots_padded = False
    table_entry_index = 0

    for entry in body:
        if entry.get("is_gap"):
            try:
                pool_blob += encode_text(entry["text"])
            except TxtdPackError as e:
                raise Txt2PackError(str(e)) from e
            continue

        if table_entry_index == verb_suffix_count and not verb_slots_padded:
            # Guarantee the pool reaches the last verb slot's offset even
            # if entry 0 got shortened a lot - no-op on unedited files.
            verb_slots_padded = True
            if verb_suffix_count and len(pool_blob) <= gap_offsets[verb_suffix_count - 1]:
                pool_blob += b"\xFF" * (gap_offsets[verb_suffix_count - 1] + 1 - len(pool_blob))

        # Slots 1..N-1: only the forced pointer (patched in below) is
        # ever read by the game, so their own remainder is skipped
        # entirely rather than packed-then-overridden - packing it first
        # and overriding the pointer after can orphan that content when
        # gap_offsets shifts (e.g. editing a leading gap's own text),
        # which confuses this reader's gap-scan on the next read.
        is_locked_secondary = 0 < table_entry_index < verb_suffix_count
        if is_locked_secondary:
            verb_slot_patches.append((len(entry_table), gap_offsets[table_entry_index]))
        table_entry_index += 1

        if is_locked_secondary:
            # Still write the lead-in bytes (if any) - some other, real
            # entry may tail-share them.
            lead_in_len = entry.get("lead_in_len", 0)
            if lead_in_len > 0:
                try:
                    full_encoded = encode_text(entry["text"])
                except TxtdPackError as e:
                    raise Txt2PackError(str(e)) from e
                if len(full_encoded) > lead_in_len:
                    pool_blob += full_encoded[:lead_in_len]
            entry_table += struct.pack("<H", 0)  # placeholder, patched below
            continue

        if entry.get("text") == OOB_MARKER:
            oob_patches.append((len(entry_table), entry.get("adr")))
            entry_table += struct.pack("<H", 0xFFFF)
            continue

        just_split = False
        if entry is first_table_entry and first_entry_encoded is not None:
            encoded = first_entry_encoded[first_entry_split:]
            just_split = first_entry_split > 0
        else:
            try:
                encoded = encode_text(entry["text"])
            except TxtdPackError as e:
                raise Txt2PackError(str(e)) from e

            lead_in_len = entry.get("lead_in_len", 0)
            if lead_in_len > 0 and len(encoded) - lead_in_len >= MIN_OWN_REMAINDER:
                split_idx = lead_in_len
                pool_blob += encoded[:split_idx]
                encoded = encoded[split_idx:]
                just_split = True

        if not just_split and encoded and pool_blob.endswith(bytes(encoded)):
            ptr = len(pool_blob) - len(encoded)
        else:
            ptr = len(pool_blob)
            if ptr > 0xFFFF:
                raise Txt2PackError("Text overflowed 64KB in this TXT2 block's text pool.")
            pool_blob += encoded

        entry_table += struct.pack("<H", ptr)

    for offset, required_value in verb_slot_patches:
        entry_table[offset:offset + 2] = struct.pack("<H", required_value)

    entry_table += struct.pack("<H", 0xFFFF)
    header = struct.pack("<HH12x", entry_root_raw, raw_count)

    out = bytearray()
    out += header
    out += entry_table
    out += b"\xFF" * entry_table_pad
    out += leading_gap_blob
    out += b"\xFF" * leading_gap_pad
    out += lead_in_bytes
    out += pool_blob

    # Enforce 8-byte alignment matching actual file structures
    pad = (-len(out)) % 8
    if pad:
        out += b"\xFF" * pad

    entry_table_offset_in_out = len(header)
    for table_offset, original_adr in oob_patches:
        if isinstance(original_adr, int) and 0 <= original_adr <= 0xFFFF:
            if entry_root_rel + original_adr >= len(out):
                pos = entry_table_offset_in_out + table_offset
                out[pos:pos + 2] = struct.pack("<H", original_adr)

    return bytes(out)


def encode_text_dedok(text):
    """
    Direct port of Dedok179's C# GetByteString(). Same algorithm as our
    own encode_text(), different control flow: scan char by char, and
    on an opening "{$" grab up to 32 chars up to the closing '}' as one
    token. "{$END}" is special-cased - its real dictionary entry is
    "{$END}\n\n" (the two newlines are display-only, consumed here
    without producing their own bytes). A token not found in the
    dictionary is parsed as a raw "{$XX}" hex byte escape.
    """
    data = bytearray()
    n = len(text)
    i = 0
    while i < n:
        symbol = text[i]
        if symbol == "{" and i + 1 < n and text[i + 1] == "$":
            key = ""
            for j in range(32):
                if i + j >= n:
                    break
                symbol = text[i + j]
                key += symbol
                if symbol == "}":
                    i += j
                    break

            if key == "{$END}":
                key = "{$END}\n\n"
                i += 2

            if key in _REVERSE_LETTERS:
                data.append(_REVERSE_LETTERS[key])
            else:
                try:
                    hex_part = key.split("$", 1)[1].split("}", 1)[0]
                    data.append(int(hex_part, 16))
                except (IndexError, ValueError) as e:
                    raise Txt2PackError(
                        f"Can't encode token {key!r}: not in the dictionary "
                        f"and not a valid {{$XX}} hex escape."
                    ) from e
        else:
            if symbol not in _REVERSE_LETTERS:
                raise Txt2PackError(f"Can't encode character {symbol!r}: no entry in tombadict.py's letters table.")
            data.append(_REVERSE_LETTERS[symbol])
        i += 1

    return bytes(data)


def pack_txt2_simple(txt2_data):
    """
    Direct Python port of an externally-provided, working C#
    MakeTextData3() implementation for TXT2 (id=3). Every entry (gap or
    not - this model doesn't distinguish) gets its own independent copy
    of its text, one after another, no tail-sharing, no lead-in merging.
    The last entry's own table offset is overwritten to 0xFFFF, acting
    as the table's end marker instead of a separate terminator slot.
    """
    entries = txt2_data.get("entries", [])
    text_list = entries
    shift = pointer_shift()

    text_head_sz = len(text_list) * 2 + 0x10
    while text_head_sz % 0x10 != 0:
        text_head_sz += 2

    offset_list = []
    text_block = bytearray()
    text_offset = 0

    for i, entry in enumerate(text_list):
        offset_list.append(text_offset >> shift)
        try:
            # TXT2 is the page's own 8x8 alphabet rather than Shift-JIS,
            # on the disc where those are two different things.
            encoded = encode_text(entry["text"], cells=True)
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e
        if i == len(text_list) - 1:
            offset_list[i] = 0xFFFF
        text_offset += len(encoded)
        text_block += encoded

    entry_root_raw = (text_head_sz - 0x10) // 2
    raw_count = len(text_list)

    text_header = bytearray()
    text_header += struct.pack("<H", entry_root_raw)
    text_header += struct.pack("<H", raw_count)
    text_header += b"\x00" * 0xC

    for off in offset_list:
        text_header += struct.pack("<H", off & 0xFFFF)

    while len(text_header) % 0x10 != 0:
        text_header += b"\xFF\xFF"

    # The last message's terminator is dropped and made up for by the
    # padding below - one byte, or one whole unit on the disc where a
    # byte is half of one.
    if text_block:
        del text_block[-(1 << shift):]

    while len(text_block) % 8 != 0:
        text_block += b"\xFF"

    return bytes(text_header) + bytes(text_block)
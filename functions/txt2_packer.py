"""
Reverse of gui/txtd/txt2.py's preview(). Rebuilds a TXT2 chunk from the
entries list preview() produces.

entries must stay in physical file order (gap and table entries
interleaved as preview() found them) - bucketing gaps separately from
table entries corrupts files that have gaps inside the table region,
not just before entry_root.

pack_txt2() (paired id=2 model) doesn't preserve tail-sharing, so
re-saved files run a bit larger than the original even unedited.
pack_txt2_flat() (id=3) does, via greedy suffix reuse - see its own
docstring.
"""

import struct

from functions.txtd_packer import encode_text, TxtdPackError, _align_up, MHSIZE, ALIGN

# Marker txt2.preview() uses for a table entry whose pointer resolves
# outside this file's own bytes. Not a real tombadict token.
OOB_MARKER = "{$OOB}"

# Reverted: a floor here (requiring >=2 bytes, i.e. real content plus
# terminator) broke zero-edit fidelity - the pristine retail file itself
# has a lead-in-split entry (index 108 in the reference 0x81D4 file)
# whose own address is a bare terminator with zero real content, and
# that file ships and presumably works, so "empty remainder" isn't
# provably unsafe. Root cause of the "e!" display bug is still open.
MIN_OWN_REMAINDER = 1


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
        # Only split if the edited text still has something left over for
        # the entry's own address afterward - if it was shortened at or
        # below the original lead_in_len, splitting would swallow the
        # WHOLE edited text as unaddressed lead-in and leave this entry
        # pointing at nothing (confirmed directly: shortening "Mermaid's
        # Scale" below its own lead_in_len produced a corrupt extra
        # entry on reread). Falling back to no split at all just makes
        # this entry own its full (shorter) text - always safe.
        if len(first_entry_encoded) > first_table_entry["lead_in_len"]:
            first_entry_split = first_table_entry["lead_in_len"]
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
            if lead_in_len > 0 and len(encoded) > lead_in_len:
                # mid-pool merge - lead-in half goes unaddressed, like a
                # gap entry. Only if there's still something left over
                # for the entry's own address afterward - see the
                # first-entry case above for why a too-short edit must
                # fall back to no split instead of swallowing everything.
                split = lead_in_len
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


def pack_txt2_flat(txt2_data):
    """
    Flat-pointer TXT2 packer (id=3 only - see txt2.preview()'s own
    docstring for why). Every table entry is one independent 2-byte
    pointer, freshly recomputed - no (adr,extra) pairing, no END
    sentinel, just a single trailing 0xFFFF terminator after the real
    pointers. Same leading-gap/lead-in-merge/OOB handling as pack_txt2().
    """
    entries = txt2_data.get("entries", [])

    split = 0
    while split < len(entries) and entries[split].get("is_gap"):
        split += 1
    leading_gaps = entries[:split]
    body = entries[split:]

    table_entries = [e for e in body if not e.get("is_gap")]
    pointer_count = len(table_entries)
    raw_count = pointer_count + 1  # +1 for the trailing 0xFFFF terminator

    entry_table_raw_end = MHSIZE + pointer_count * 2 + 2
    table_region_end = _align_up(entry_table_raw_end, ALIGN)
    entry_table_pad = table_region_end - entry_table_raw_end

    leading_gap_blob = bytearray()
    gap_offsets = []  # gap_offsets[k] = k-th leading gap's own offset within leading_gap_blob
    for gap_entry in leading_gaps:
        gap_offsets.append(len(leading_gap_blob))
        try:
            leading_gap_blob += encode_text(gap_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e
    leading_gap_count = len(leading_gaps)

    first_table_entry = table_entries[0] if table_entries else None
    first_entry_encoded = None
    first_entry_split = 0
    lead_in_bytes = b""
    if first_table_entry is not None and first_table_entry.get("lead_in_len", 0) > 0:
        try:
            first_entry_encoded = encode_text(first_table_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e
        # See pack_txt2()'s identical check for why this must be a
        # strict excess, not just min(). MIN_OWN_REMAINDER also guards
        # against a real corruption found by hand: even a generous
        # excess can leave an all-terminator (zero real character)
        # remainder for this entry's own address, which decodes as
        # empty text - confirmed against the pristine retail file that
        # the game's own minimum for this kind of split is small but
        # never zero real bytes, so don't go below that either.
        if len(first_entry_encoded) - first_table_entry["lead_in_len"] >= MIN_OWN_REMAINDER:
            first_entry_split = first_table_entry["lead_in_len"]
            lead_in_bytes = first_entry_encoded[:first_entry_split]

    entry_root_rel = _align_up(table_region_end + len(leading_gap_blob) + len(lead_in_bytes), ALIGN)
    leading_gap_pad = entry_root_rel - len(lead_in_bytes) - (table_region_end + len(leading_gap_blob))
    entry_root_raw = (entry_root_rel - MHSIZE) // 4

    if entry_root_raw > 0xFFFF or raw_count > 0xFFFF:
        raise Txt2PackError(
            "TXT2 block too large to encode (entry_root_raw={}, "
            "raw_count={}) - both must fit in 16 bits."
            .format(entry_root_raw, raw_count)
        )

    entry_table = bytearray()
    pool_blob = bytearray()
    oob_patches = []  # (table byte offset, original adr) - resolved once pool_blob's final size is known
    verb_slot_patches = []  # (table byte offset, required gap_offset) - see loop body below
    verb_slots_padded = False
    table_entry_index = 0
    for entry in body:
        if entry.get("is_gap"):
            try:
                pool_blob += encode_text(entry["text"])
            except TxtdPackError as e:
                raise Txt2PackError(str(e)) from e
            continue

        # Table slots 0..leading_gap_count-1 are dual-purpose. Confirmed
        # by tracing real PS1 memory: the live game reads these same
        # slots a SECOND way, via table_region_end instead of entry_root,
        # to resolve the generic "used!/acquired!/equipped!/..." suffix
        # shared by every item pickup/use/equip in this area - a
        # mechanism this tool never modeled before. Their raw pointer
        # value must exactly equal that leading gap's own offset within
        # leading_gap_blob (verified byte-for-byte against 3 unedited
        # retail files, 39/39 slots matching). Entry 0 always lands on
        # its own gap offset naturally (ptr 0), so only 1..N-1 need
        # tracking here; they still pack normally below (preserving
        # zero-edit byte-identical output) and get force-patched only if
        # an edit elsewhere made the natural placement drift - see the
        # patch loop after this one.
        if table_entry_index == leading_gap_count and not verb_slots_padded:
            # Every table_entries[0..N-1] entry has now finished its own
            # normal packing (including appending its own content). If
            # that combined natural content didn't reach as far as the
            # last verb slot's offset - e.g. entry 0 got shortened a lot
            # - guarantee it does anyway, with safe terminated filler, so
            # every forced pointer below lands on SOMETHING rather than
            # spilling into a later, unrelated entry's own text. No-op
            # for any unedited file - that's exactly why the retail file
            # already has this property naturally.
            verb_slots_padded = True
            if gap_offsets and len(pool_blob) <= gap_offsets[-1]:
                pool_blob += b"\xFF" * (gap_offsets[-1] + 1 - len(pool_blob))

        if 0 < table_entry_index < leading_gap_count:
            verb_slot_patches.append((len(entry_table), gap_offsets[table_entry_index]))
        table_entry_index += 1

        if entry.get("text") == OOB_MARKER:
            # dead slot - default to 0xFFFF (far beyond any realistic
            # pool size) so it stays out of bounds regardless of how the
            # pool shifts. If this entry's own original address is still
            # >= the final pool size, restore it instead - harmless (it
            # was already unreachable) and keeps an unedited round-trip
            # byte-identical to the source file instead of needlessly
            # rewriting untouched dead slots.
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
                split = lead_in_len
                pool_blob += encoded[:split]
                encoded = encoded[split:]
                just_split = True

        # Tail-share reuse: if these bytes already sit at the end of the
        # pool built so far, point there instead of duplicating them.
        # This is exactly the compression the retail ROM relies on (one
        # entry's text is a literal suffix of another's) - replaying it
        # greedily, in the same order entries were originally found,
        # naturally rediscovers the original sharing, so an unedited
        # round-trip comes back byte-identical instead of ballooning.
        # Skipped right after a lead-in split: this entry's own address
        # MUST stay immediately after the lead-in bytes just written, or
        # the gap-scanner won't rediscover the merge on the next read -
        # a short, generic remainder (e.g. just "!{$FF}") can otherwise
        # false-positive match some unrelated entry's tail instead.
        if not just_split and encoded and pool_blob.endswith(bytes(encoded)):
            ptr = len(pool_blob) - len(encoded)
        else:
            ptr = len(pool_blob)
            if ptr > 0xFFFF:
                raise Txt2PackError(
                    "Text overflowed 64KB in this TXT2 block's text pool - "
                    "shorten the text."
                )
            pool_blob += encoded
        entry_table += struct.pack("<H", ptr)

    # Force the dual-purpose verb-suffix slots (see comment above) back
    # to their required value if editing/dedup drifted them - for an
    # unedited file this is a no-op, since the natural placement already
    # produces the same value (that's WHY the retail file has this
    # property in the first place).
    for offset, required_value in verb_slot_patches:
        entry_table[offset:offset + 2] = struct.pack("<H", required_value)

    entry_table += struct.pack("<H", 0xFFFF)  # terminator

    header = struct.pack("<HH12x", entry_root_raw, raw_count)

    out = bytearray()
    out += header
    out += entry_table
    out += b"\xFF" * entry_table_pad
    out += leading_gap_blob
    out += b"\xFF" * leading_gap_pad
    out += lead_in_bytes
    out += pool_blob

    # Real TXT2 chunks are 8-byte aligned overall (confirmed against
    # every sample file on hand - all exact multiples of 8), not just 4.
    pad = (-len(out)) % 8
    if pad:
        out += b"\xFF" * pad

    # Now that the final file length (including trailing alignment
    # padding) is known, patch dead slots back to their original address
    # wherever that's still safely past the end of the whole file - not
    # just past the pool. An address landing inside the alignment pad is
    # still "in bounds" by the reader's own size check and would decode
    # as a bogus {$FF}-only entry instead of staying invisible.
    entry_table_offset_in_out = len(header)
    for table_offset, original_adr in oob_patches:
        if isinstance(original_adr, int) and 0 <= original_adr <= 0xFFFF and entry_root_rel + original_adr >= len(out):
            pos = entry_table_offset_in_out + table_offset
            out[pos:pos + 2] = struct.pack("<H", original_adr)

    return bytes(out)

"""
Reverse of gui/txtd/txt2.py's preview() function.

Rebuilds a self-consistent TXT2 binary blob from the structure produced
by txt2.preview() (optionally edited in the GUI).

TXT2 is structurally identical to a single TXTD "master" block (see
txtd_packer.py's own docstring for the full alignment rationale/
justification - it's not repeated here) - just without the outer
master-table layer. This packer mirrors pack_txtd()'s single-master-
block logic, with one addition: txt2.py can also surface "gap" entries
(see its own docstring) - real, readable text sitting between the
table and entry_root that isn't addressed by any (adr,extra) pair.
Layout:

    [0x00]      TXT2 header (16 bytes): entry_root, entry_amount
    [0x10]      entry table (entry_amount * 4 bytes)
                + 0xFF padding up to the next 16-byte boundary
                  (relative to 0x00, this block's own start)
    [table end] every gap entry's text, concatenated in order
                + 0xFF padding up to the next 16-byte boundary
                  (matches the original file's own convention -
                  its 240-byte gap is itself a clean multiple of 16)
    [entry_root] every table entry's text, concatenated in entry
                order (skipping END! sentinels)

...then the whole blob is padded to a 4-byte boundary, matching the
per-master end-padding pack_txtd() applies (word alignment for
whatever follows at the repacker/sector level).

Every pointer written out (entry_root_raw, each entry's adr) is
RECOMPUTED from this layout, not copied from any original file - same
reasoning as pack_txtd(): editing one entry's OR one gap fragment's
text shifts every subsequent offset (entry_root included, since gap
text sits before it), so there's no safe way to patch bytes in place.

Entries are matched to txt2_data["entries"] purely by LIST POSITION,
not by any stale "adr"/"extra" value - gap entries (tagged "is_gap":
True by txt2.py) are identified the same way, by that tag, not by
position, so editing text or reordering the list some other way still
routes each entry to the right region. An entry with adr == extra ==
0xFFFF is treated as the "END!" sentinel (no real text) and is
preserved as such, not re-encoded. A gap entry has no adr/extra of its
own to preserve - it's written back purely by its text and position
among the other gap entries.
"""

import struct

from functions.txtd_packer import encode_text, TxtdPackError, _align_up, MHSIZE, ALIGN


class Txt2PackError(TxtdPackError):
    """Raised when TXT2 text can't be encoded, or the structure is
    invalid. Subclasses TxtdPackError since it's the same family of
    problem (bad/unencodable text, corrupted structure) - callers that
    already catch TxtdPackError around TXTD exports will also catch
    this for free."""
    pass


def pack_txt2(txt2_data):
    """
    txt2_data: the structure produced by txt2.preview(), optionally
    edited in the GUI:
        {
          "entries": [{"adr": ..., "extra": ..., "text": ..., "is_gap": ...}, ...]
        }

    entries[i] is matched to its slot purely by LIST POSITION within
    its own kind (table vs gap, split by "is_gap") - "adr" is
    meaningless input for table entries, it gets fully recomputed; gap
    entries have no adr/extra at all.

    Returns the raw TXT2 chunk bytes, ready to replace the original
    chunk's bytes in the DAT file.
    """
    entries = txt2_data.get("entries", [])
    table_entries = [e for e in entries if not e.get("is_gap")]
    gap_entries = [e for e in entries if e.get("is_gap")]

    entry_amount = len(table_entries)

    # The real table must land on a 16-byte boundary relative to the
    # very start of this TXT2 block (offset 0) - same convention as one
    # TXTD master's own sub-header/entry-table region.
    entry_table_raw_end = MHSIZE + entry_amount * 4
    table_region_end = _align_up(entry_table_raw_end, ALIGN)
    entry_table_pad = table_region_end - entry_table_raw_end

    # Gap text (if any) follows immediately, then gets padded up to the
    # next 16-byte boundary too - matching the original file's own gap,
    # which was itself a clean multiple of 16 bytes.
    gap_blob = bytearray()
    for gap_entry in gap_entries:
        try:
            gap_blob += encode_text(gap_entry["text"])
        except TxtdPackError as e:
            raise Txt2PackError(str(e)) from e

    entry_root_rel = _align_up(table_region_end + len(gap_blob), ALIGN)
    gap_pad = entry_root_rel - (table_region_end + len(gap_blob))
    entry_root_raw = (entry_root_rel - MHSIZE) // 4

    if entry_root_raw > 0xFFFF or entry_amount > 0xFFFF:
        raise Txt2PackError(
            "TXT2 block too large to encode (entry_root_raw={}, "
            "entry_amount={}) - both must fit in 16 bits."
            .format(entry_root_raw, entry_amount)
        )

    entry_table = bytearray()
    text_blob = bytearray()
    for entry in table_entries:
        is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)
        if is_sentinel:
            entry_table += struct.pack("<HH", 0xFFFF, 0xFFFF)
            continue

        adr = len(text_blob)
        if adr > 0xFFFF:
            raise Txt2PackError(
                "Text overflowed 64KB in this TXT2 block's text pool - "
                "shorten the text."
            )
        extra = entry.get("extra", 0)
        try:
            encoded = encode_text(entry["text"])
        except TxtdPackError as e:
            # Re-raise under the TXT2-specific error class for a clearer
            # error source, while keeping the original message intact.
            raise Txt2PackError(str(e)) from e

        entry_table += struct.pack("<HH", adr, extra)
        text_blob += encoded

    header = struct.pack("<HH12x", entry_root_raw, entry_amount)

    out = bytearray()
    out += header
    out += entry_table
    out += b"\xFF" * entry_table_pad
    out += gap_blob
    out += b"\xFF" * gap_pad
    out += text_blob

    # Pad the whole blob to a 4-byte boundary, matching pack_txtd()'s
    # per-master end padding.
    pad = (-len(out)) % 4
    if pad:
        out += b"\xFF" * pad

    return bytes(out)
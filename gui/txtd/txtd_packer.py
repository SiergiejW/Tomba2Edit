"""
Reverse of gui/txtd/txtd.py's preview() function.

Rebuilds a self-consistent TXTD binary blob from the structure produced
by txtd.preview() (optionally edited in the GUI).

16-BYTE ALIGNMENT: every table region (master table, each master's own
entry table) is followed by zero padding so the next region starts on
a 16-byte boundary relative to that table's own start - true across
every retail TXTD file, and required since MIPS traps on misaligned
word/halfword loads.

Since every pointer (master_root, each entry_root, every master/entry
address) is stored explicitly and shortening/lengthening one text
string shifts everything after it, in-place patching isn't viable -
this packer instead rebuilds the whole blob with ONE fixed,
deterministic, 16-byte-aligned layout:

    [0x00]                    TXTD header (16 bytes)
    [0x10]                    master table (master_amount * 4 bytes)
                               + zero padding up to the next 16-byte
                                 boundary (relative to 0x00)
    [tables region]           for each master, in order:
                                   sub-header (16 bytes)
                                   entry table (entry_amount * 4 bytes)
                                   + zero padding up to the next
                                     16-byte boundary (relative to
                                     this master's own start)
                                   this master's entries' text,
                                   concatenated in entry order
                                   (then padded to a 4-byte boundary
                                   so the next master's block - which
                                   only needs word alignment, not
                                   16-byte alignment - starts clean)

Every pointer written out (master_root_raw, each master_adr,
each entry_root_raw, each entry adr) is RECOMPUTED from this layout,
not copied from any original file. Since txtd.py's preview() makes no
assumption about layout either (every table position is derived from
the pointers it reads), the result is guaranteed internally consistent
regardless of what the original ROM's layout looked like - and now
also respects the real alignment convention.
"""

import struct

from gui.txtd.tombadict import letters as LETTERS

MHSIZE = 0x10
ALIGN = 0x10  # 16-byte alignment for table-region boundaries


class TxtdPackError(Exception):
    """Raised when text can't be encoded, or the structure is invalid.
    Deliberately loud - a silently wrong export would be much worse
    than a rejected one."""
    pass


# --- Build the reverse lookup (decoded string/token -> raw byte) once ---
# Some bytes decode to the same string (e.g. both 0xFB and 0xC2 decode
# to a plain space). We keep the FIRST one encountered in tombadict's
# definition order as the canonical byte to re-encode to. 0xFE decodes
# to an empty string and can't be represented in decoded text at all,
# so it's excluded (it will simply never be re-emitted by this packer).
_REVERSE_LETTERS = {}
for _byte, _s in LETTERS.items():
    if _s == "":
        continue
    if _s not in _REVERSE_LETTERS:
        _REVERSE_LETTERS[_s] = _byte

# Longest tokens first, so the tokenizer below is a correct greedy/
# maximal-munch matcher (e.g. "{$END}\n\n" must win over a bare "\n").
_TOKENS = sorted(_REVERSE_LETTERS.keys(), key=len, reverse=True)


def _align_up(n, align=ALIGN):
    return (n + align - 1) // align * align


def encode_text(text):
    """
    Turn a decoded text string (as shown/edited in the TXTD viewer)
    back into raw TXTD bytes, INCLUDING the terminating 0xFF byte.
    Raises TxtdPackError instead of guessing if something can't be
    encoded, so a silent, subtly-corrupt export can't happen.
    """
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        matched = False

        # 1) Explicit raw-byte escape "{$XX}" (produced by the reader
        #    itself for any byte with no defined character).
        if text[i:i + 2] == "{$" and i + 5 <= n and text[i + 4] == "}":
            hex_part = text[i + 2:i + 4]
            if all(c in "0123456789ABCDEFabcdef" for c in hex_part):
                out.append(int(hex_part, 16))
                i += 5
                matched = True

        # 2) Any other known token (named escapes, or a single mapped
        #    character), longest match first.
        if not matched:
            for tok in _TOKENS:
                if text.startswith(tok, i):
                    out.append(_REVERSE_LETTERS[tok])
                    i += len(tok)
                    matched = True
                    break

        if not matched:
            raise TxtdPackError(
                "Can't encode character {!r} at position {} of text {!r}. "
                "It has no entry in tombadict.py's letters table, and isn't "
                "a valid {{$XX}} byte escape.".format(text[i], i, text)
            )

    if not out or out[-1] != 0xFF:
        out.append(0xFF)
    return bytes(out)


def pack_txtd(txtd_data):
    """
    txtd_data: the structure produced by txtd.preview(), optionally
    edited in the GUI:
        {
          "master_headers": [{"adr": ..., "extra": ...}, ...],
          "entries": [
              {"master_adr": ..., "entry_amount": ..., "entries": [
                  {"adr": ..., "extra": ..., "text": ...}, ...
              ]},
              ...
          ]
        }

    master_headers[i] and entries[i] are matched up by LIST POSITION
    (the same order preview() produced them / the GUI displays them),
    NOT by "adr" - that field is meaningless input here, it gets fully
    recomputed.

    An entry with adr == extra == 0xFFFF is treated as the "END!"
    sentinel (no real text) and is preserved as such, not re-encoded.

    Returns the raw TXTD chunk bytes, ready to replace the original
    chunk's bytes in the DAT file.
    """
    masters = txtd_data.get("master_headers", [])
    groups = txtd_data.get("entries", [])
    if len(masters) != len(groups):
        raise TxtdPackError(
            "master_headers ({}) and entries ({}) count mismatch - "
            "this TXTD structure looks corrupted.".format(len(masters), len(groups))
        )

    master_amount = len(masters)

    # Top-level header: master_root must land on a 16-byte boundary
    # relative to the very start of the blob (offset 0).
    master_table_raw_end = 16 + master_amount * 4
    master_root_rel = _align_up(master_table_raw_end)
    master_root_raw = (master_root_rel - 16) // 4
    master_table_pad = master_root_rel - master_table_raw_end

    after_master_table = bytearray()   # tables region: sub-headers + entry tables + text, per master
    master_table_entries = []          # (master_adr, master_extra) in group order

    for master_meta, group in zip(masters, groups):
        entries = group.get("entries", [])
        entry_amount = len(entries)

        assert len(after_master_table) % 4 == 0, "internal alignment invariant broken"
        master_adr = len(after_master_table) // 4

        # This master's own entry_root must land on a 16-byte boundary
        # relative to THIS master's own sub-header start (not the file
        # start) - matches the real format exactly (verified against
        # multiple original TXTD files).
        entry_table_raw_end = 16 + entry_amount * 4
        entry_root_rel = _align_up(entry_table_raw_end)
        entry_root_raw = (entry_root_rel - 16) // 4
        entry_table_pad = entry_root_rel - entry_table_raw_end

        entry_table = bytearray()
        text_blob = bytearray()
        for entry in entries:
            is_sentinel = (entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF)
            if is_sentinel:
                entry_table += struct.pack("<HH", 0xFFFF, 0xFFFF)
                continue

            adr = len(text_blob)
            if adr > 0xFFFF:
                raise TxtdPackError(
                    "Text for master_adr={} overflowed 64KB in a single "
                    "text pool - split this TXTD block or shorten the text."
                    .format(master_adr)
                )
            extra = entry.get("extra", 0)
            entry_table += struct.pack("<HH", adr, extra)
            text_blob += encode_text(entry["text"])

        after_master_table += struct.pack("<HH12x", entry_root_raw, entry_amount)
        after_master_table += entry_table
        after_master_table += b"\xFF" * entry_table_pad
        after_master_table += text_blob

        # Pad to a 4-byte boundary so the NEXT master's block (which
        # only needs word alignment for its master_adr, not a fresh
        # 16-byte alignment) starts clean.
        pad = (-len(after_master_table)) % 4
        if pad:
            after_master_table += b"\xFF" * pad

        master_table_entries.append((master_adr, master_meta.get("extra", 0)))

    header = struct.pack("<HH12x", master_root_raw, master_amount)
    master_table = bytearray()
    for master_adr, master_extra in master_table_entries:
        master_table += struct.pack("<HH", master_adr, master_extra)
    master_table += b"\xFF" * master_table_pad

    return bytes(header) + bytes(master_table) + bytes(after_master_table)

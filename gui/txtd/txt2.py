import re
import struct

from gui.txtd.tombadict import letters as l

MHSIZE = 0x10

# How much of the file to hand the Japanese codec for one message; it
# stops at the terminator, so this is only an upper bound.
JP_WINDOW = 0x2000

_HEX_ESCAPE_RE = re.compile(r"\{\$[0-9A-Fa-f]{2}\}")


def is_probably_text(text):
    if not text or text == "END!":
        return True
    if "{$EOF}" in text or "{$OOB}" in text:
        return False
    stripped = text.replace("{$FF}", "")
    if not stripped:
        return True
    escaped_chars = sum(len(m.group(0)) for m in _HEX_ESCAPE_RE.finditer(stripped))
    return escaped_chars < 0.35 * len(stripped)


def _align_up16(n):
    return (n + 15) // 16 * 16


def preview(DAT, datstart, size=None, id_val=None):
    """
    id_val selects the table format: 3 (TXT2) reads the table as a FLAT
    list of independent 2-byte pointers - every value is its own
    independently-addressed message, with no (adr,extra) pairing and a
    single trailing 0xFFFF terminator (not a (0xFFFF,0xFFFF) sentinel).
    Anything else (2/TXT1, or unspecified) uses the paired-table
    reading.

    Confirmed against a real English TXT2 sample and an independent fan
    translation's source (github.com/jywjyw/tomba2-hack), which rebuilds
    this table the same flat way.

    The Japanese disc holds both of these as 16-bit units rather than
    bytes, and holds them in two different alphabets: TXT1 is Shift-JIS
    and TXT2 is cell numbers into the page's own 8x8 kana font, which is
    the same split the Latin builds have between the 8x16 font and the
    8x8 one. Its pointers count units. See gui/txtd/jptext.
    """
    from gui.txtd import dicts

    japanese = dicts.japanese_disc()
    cells = japanese and id_val == 3
    ptr_shift = 1 if japanese else 0
    if japanese:
        from gui.txtd import jptext
        jp_decode = jptext.decode_cells if cells else jptext.decode

    def getB(number=1):
        return int.from_bytes(rom.read(number), byteorder='little')

    def prepareText(ptr, who, real):
        if ptr == 0xFFFF and who == 0xFFFF:
            return "END!"
        else:
            print("\t{:04X}/{:04X}, (at {:04X})".format(ptr, who, real))
            return getText(real)

    def getText(real):
        if file_end is not None and real >= file_end:
            rom.seek(real)
            return "{$OOB}"
        if japanese:
            rom.seek(real)
            room = JP_WINDOW if file_end is None else min(JP_WINDOW,
                                                          file_end - real)
            window = rom.read(max(room, 0))
            text, end = jp_decode(window, 0)
            # Leave the handle where the byte path would leave it - the
            # caller takes rom.tell() as this message's end.
            rom.seek(real + end)
            if window[end - 2:end] != b"\xFF\xFF":
                text += "{$OOB}" if file_end is not None else "{$EOF}"
            return text
        textout = ""
        rom.seek(real)
        n = -1
        while n != 0xFF:
            if file_end is not None and rom.tell() >= file_end:
                textout += "{$OOB}"
                break
            chunk = rom.read(1)
            if not chunk:
                textout += "{$EOF}"
                break
            n = chunk[0]
            if n in l:
                textout += l[n]
            else:
                surrogate = "{:02X}".format(n)
                textout += "{$" + surrogate + "}"
        return textout

    output = {"entries": []}
    file_end = None if size is None else datstart + size

    try:
        print(f"Opening DAT file: {DAT}")
        with open(DAT, "rb") as rom:
            print(f"DAT: {DAT}")
            print(f"Seeking to datstart: {datstart}")
            rom.seek(datstart)

            def decode_bytes(raw):
                if japanese:
                    return jp_decode(raw, 0)[0]
                return "".join(l.get(byte, "{{${:02X}}}".format(byte)) for byte in raw)

            def scan_fragments(start, end):
                # A fragment runs to its own terminator - one 0xFF byte,
                # or one 0xFFFF unit on the Japanese disc, where reading
                # a unit at a time is also what keeps the scan aligned.
                step = 2 if japanese else 1
                terminator = b"\xFF\xFF" if japanese else b"\xFF"
                scan_end = end if file_end is None else min(end, file_end)
                rom.seek(start)
                pos = start
                out = []
                while pos < scan_end:
                    frag_start = pos
                    frag = bytearray()
                    while True:
                        if file_end is not None and pos >= file_end:
                            break  # this file's own bytes stop here - same hard stop as true physical EOF
                        chunk = rom.read(step)
                        if len(chunk) < step:
                            break  # true physical EOF - the only other hard stop
                        frag += chunk
                        pos += step
                        if chunk == terminator:
                            break
                    if not frag:
                        break  # nothing left to read - stop the scan
                    if frag == terminator:
                        continue
                    overflow_end = pos if pos > end else None
                    out.append((frag_start, bytes(frag), overflow_end))
                return out

            def add_gap_fragments(entries, start, end):
                for frag_start, raw, overflow_end in scan_fragments(start, end):
                    if overflow_end is None:
                        entries.append({
                            "adr": None,
                            "extra": None,
                            "text": decode_bytes(raw),
                            "is_gap": True,
                            "_sort_key": frag_start,
                        })
                        print(f"Gap fragment: {decode_bytes(raw)}")
                        continue
                    lead_raw = raw[: end - frag_start]
                    lead_text = decode_bytes(lead_raw)
                    target = next(
                        (e for e in entries
                         if not e["is_gap"] and e.get("_real_start") == end),
                        None,
                    )
                    if target is not None:
                        print(f"Gap fragment runs into entry at 0x{end:X} - "
                              f"merging lead-in text onto it: {lead_text!r} "
                              f"+ {target['text']!r}")
                        target["text"] = lead_text + target["text"]
                        target["lead_in_len"] = len(lead_raw)
                    else:
                        full_text = decode_bytes(raw)
                        print(f"Gap fragment (no merge target found): {full_text}")
                        entries.append({
                            "adr": None,
                            "extra": None,
                            "text": full_text,
                            "is_gap": True,
                            "_sort_key": frag_start,
                        })

            print("Reading TXT2 root and entry amount...")
            entry_root_raw, raw_count = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
            # entry_root_raw counts this table's OWN native slot width, not
            # bytes - confirmed directly against the pristine retail file:
            # id 3's table is 2 bytes/slot, and decoding with *2 turns 4
            # entries that looked permanently out-of-bounds under *4 into
            # perfectly valid, complete English text (e.g. "{$PINK}The Last
            # Pig Bag{$WHITE} acquired!..."), while *4 pushes all 4 of them
            # past this file's own end. All other id 3 entries decode
            # cleanly either way, so those 4 were the only tell. id 2's
            # table is 4 bytes/slot (adr+extra), and *4 still decodes every
            # entry there cleanly, so it's left as-is.
            multiplier = 2 if id_val == 3 else 4
            entry_root = (entry_root_raw * multiplier) + MHSIZE + datstart
            print(f"Entry root: {entry_root:08X}, raw_count: {raw_count}")

            entries = []

            if id_val == 3:
                # Flat pointer table (see this function's own docstring).
                # Some writers (pack_txt2_flat) always follow raw_count-1
                # real pointers with one dedicated 0xFFFF terminator slot;
                # others (pack_txt2_simple) give the table exactly
                # raw_count slots and let the LAST real entry's own value
                # be 0xFFFF instead. Reading up to raw_count slots and
                # stopping at the first 0xFFFF handles both: real pointer
                # values are always tiny offsets into this file's own text
                # pool, never anywhere near 0xFFFF.
                pointer_count = raw_count
                if pointer_count > 2000:
                    print(f"Warning: pointer_count seems unusually high ({pointer_count}). Limiting to 2000.")
                    pointer_count = 2000

                pointers = []
                hit_terminator = False
                for i in range(pointer_count):
                    val = getB(2)
                    pointers.append(val)
                    if val == 0xFFFF:
                        hit_terminator = True
                        break
                if not hit_terminator:
                    print(f"Warning: no 0xFFFF terminator found within {pointer_count} pointers - table may be misread.")

                for i, ptr in enumerate(pointers):
                    is_sentinel = (ptr == 0xFFFF)
                    if is_sentinel:
                        print(f"\t{i}: ptr=0xFFFF - END marker")
                        entries.append({
                            "adr": 0xFFFF,
                            "extra": 0xFFFF,
                            "text": "END!",
                            "is_gap": False,
                            "_sort_key": float("inf"),
                            "_real_start": None,
                            "_real_end": None,
                        })
                        continue

                    real = entry_root + (ptr << ptr_shift)
                    print(f"\t{i}: ptr=0x{ptr:04X} (at 0x{real:X})")
                    text_content = getText(real)
                    real_end = rom.tell()
                    print(f"text:{text_content}")
                    entries.append({
                        "adr": ptr,
                        "extra": None,
                        "text": text_content,
                        "is_gap": False,
                        "_sort_key": real,
                        "_real_start": real,
                        "_real_end": real_end,
                    })

                # Align relative to this chunk's own start, then add
                # datstart back - NOT the other way around. datstart
                # (this chunk's absolute DAT position) isn't itself
                # always a multiple of 16, so aligning the absolute
                # position gives a different (wrong) answer than
                # aligning the chunk-relative size and adding datstart
                # after - confirmed directly: reading this same file at
                # its real DAT offset (0x81D4, not 16-aligned) computed
                # a table_region_end 4 bytes early compared to reading
                # it as a standalone extracted file (datstart=0, where
                # the bug can't show since 0 is already 16-aligned).
                # len(pointers) already includes the terminator slot
                # (dedicated or self-terminating), so no "+2" needed here.
                table_region_end = datstart + _align_up16(MHSIZE + len(pointers) * 2)

            else:
                # Paired (adr,extra) table - TXT1 (id 2) and anything unspecified.
                entry_amount = raw_count
                if entry_amount > 1000:
                    print(f"Warning: Entry amount seems unusually high ({entry_amount}). Limiting to 1000 entries.")
                    entry_amount = 1000

                print("Reading entry table...")
                entry_headers = {}
                for b in range(entry_amount):
                    entry_adr = getB(2)
                    entry_extra = getB(2)
                    entry_headers[b] = {"adr": entry_adr, "extra": entry_extra}

                for b in range(entry_amount):
                    print(f"Processing entry {b + 1}/{entry_amount}...")
                    ptr = entry_headers[b]["adr"]
                    who = entry_headers[b]["extra"]
                    is_sentinel = (ptr == 0xFFFF and who == 0xFFFF)
                    real = entry_root + (ptr << ptr_shift)
                    text_content = prepareText(ptr, who, real)

                    real_end = rom.tell()
                    print(f"ptr:0x{ptr:X}, who:0x{who:X}, real:0x{real:X}")
                    print(f"text:{text_content}")
                    entries.append({
                        "adr": ptr,
                        "extra": who,
                        "text": text_content,
                        "is_gap": False,
                        "_sort_key": float('inf') if is_sentinel else real,
                        "_real_start": None if is_sentinel else real,
                        "_real_end": None if is_sentinel else real_end,
                    })
                    if is_sentinel:
                        if b + 1 < entry_amount:
                            print(f"Hit first END sentinel at entry {b} - "
                                  f"ignoring the remaining {entry_amount - b - 1} "
                                  f"reserved-but-unused table slot(s).")
                        break

                physical_table_slots = len(entries)
                if entries and entries[-1]["is_gap"] is False and \
                   entries[-1]["adr"] == 0xFFFF and entries[-1]["extra"] == 0xFFFF:
                    while physical_table_slots < entry_amount:
                        hdr = entry_headers[physical_table_slots]
                        if hdr["adr"] != 0xFFFF or hdr["extra"] != 0xFFFF:
                            break
                        physical_table_slots += 1

                table_region_end = datstart + MHSIZE + physical_table_slots * 4

            covered = []
            if entry_root > table_region_end:
                gap_size = entry_root - table_region_end
                print(f"Gap between table end (0x{table_region_end:X}) and "
                      f"entry_root (0x{entry_root:X}): {gap_size} bytes - "
                      f"scanning for extra un-addressed {{$FF}}-delimited text.")
                add_gap_fragments(entries, table_region_end, entry_root)
                # The whole [table_region_end, entry_root) span was just
                # read start to finish above (scan_fragments walks every
                # byte in the range, padding included) - mark all of it
                # covered so gap scan #2 doesn't re-read any of it.
                covered.append((table_region_end, entry_root))
            else:
                # No declared slack between the table and entry_root - can
                # still happen if a file's own accounting doesn't leave
                # room for a forward gap. The entry starting exactly at
                # entry_root (adr=0) can still be a lead-in overflow, same
                # idea as the forward case: scan backward from entry_root
                # for the previous message's own terminator, bounded so it
                # can never walk back past the table's own start.
                zero_adr_entry = next(
                    (e for e in entries if not e["is_gap"] and e.get("adr") == 0),
                    None,
                )
                lower_bound = datstart + MHSIZE
                if zero_adr_entry is not None and entry_root > lower_bound:
                    step = 2 if japanese else 1
                    terminator = b"\xFF\xFF" if japanese else b"\xFF"
                    rom.seek(entry_root - step)
                    prev_byte = rom.read(step)
                    if len(prev_byte) == step and prev_byte != terminator:
                        pos = entry_root - step
                        while pos > lower_bound:
                            rom.seek(pos - step)
                            b = rom.read(step)
                            if len(b) < step or b == terminator:
                                break
                            pos -= step
                        if pos < entry_root:
                            rom.seek(pos)
                            lead_raw = rom.read(entry_root - pos)
                            lead_text = decode_bytes(lead_raw)
                            print(f"Entry 0 has no clean start at 0x{entry_root:X} - "
                                  f"recovered lead-in from 0x{pos:X}: {lead_text!r}")
                            zero_adr_entry["text"] = lead_text + zero_adr_entry["text"]
                            zero_adr_entry["lead_in_len"] = len(lead_raw)

            for e in entries:
                if not e["is_gap"] and e["_real_start"] is not None:
                    covered.append((e["_real_start"], e["_real_end"]))
            covered.sort(key=lambda r: r[0])
            if covered:
                current_end = covered[0][1]
                for start, end in covered[1:]:
                    if start > current_end:
                        print(f"Gap between two entries (0x{current_end:X} - "
                              f"0x{start:X}): {start - current_end} bytes - "
                              f"scanning for extra un-addressed {{$FF}}-delimited text.")
                        add_gap_fragments(entries, current_end, start)
                    current_end = max(current_end, end)

            entries.sort(key=lambda e: e["_sort_key"])

            # Confirmed by tracing real PS1 memory: the live game reads
            # the first 11 table slots a SECOND way, via
            # table_region_end + table[k] instead of the usual
            # entry_root + table[k], to find the generic "used!/
            # acquired!/equipped!/removed!/given!/sent to nest!/fed!/
            # entered hotspring!/set!/burned up!/chanted!" suffix shared
            # by every item pickup/use/equip. This 11 is a fixed,
            # engine-level constant, not something to re-derive per file
            # - a leading gap's own offset can coincidentally equal an
            # unrelated table slot's own pointer value (confirmed
            # directly: slot 11 "matches" here purely by chance, since
            # "Magic Gauge grew!" isn't part of this at all), so matching
            # numbers alone isn't a safe way to detect this past what's
            # actually been proven. Still verify the match holds even
            # within that cap, in case a file has fewer than 11.
            VERB_SUFFIX_COUNT = 11
            verb_suffix_count = 0
            if id_val == 3:
                leading_gap_entries = [e for e in entries if e["is_gap"] and e["_sort_key"] < entry_root]
                real_table_entries = [e for e in entries if not e["is_gap"] and e.get("adr") is not None]
                n = min(VERB_SUFFIX_COUNT, len(leading_gap_entries), len(real_table_entries))
                for k in range(n):
                    gap_offset = ((leading_gap_entries[k]["_sort_key"]
                                   - table_region_end) >> ptr_shift)
                    if real_table_entries[k]["adr"] == gap_offset:
                        verb_suffix_count = k + 1
                    else:
                        break

            for e in entries:
                del e["_sort_key"]
                e.pop("_real_start", None)
                e.pop("_real_end", None)

            output["entries"] = entries
            output["verb_suffix_count"] = verb_suffix_count

            print("Finished processing TXT2 data.")
            return output

    except Exception as e:
        print(f"Error in preview function: {e}")
        raise e

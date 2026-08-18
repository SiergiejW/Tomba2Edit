import re
import struct

from gui.txtd.tombadict import letters as l

MHSIZE = 0x10

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


def preview(DAT, datstart, size=None):
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
                return "".join(l.get(byte, "{{${:02X}}}".format(byte)) for byte in raw)

            def scan_fragments(start, end):
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
                        chunk = rom.read(1)
                        if not chunk:
                            break  # true physical EOF - the only other hard stop
                        frag.append(chunk[0])
                        pos += 1
                        if chunk[0] == 0xFF:
                            break
                    if not frag:
                        break  # nothing left to read - stop the scan
                    if frag == b"\xFF":
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
            entry_root_raw, entry_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
            entry_root = (entry_root_raw << 2) + MHSIZE + datstart
            print(f"Entry root: {entry_root:08X}, Entry amount: {entry_amount}")

            if entry_amount > 1000:
                print(f"Warning: Entry amount seems unusually high ({entry_amount}). Limiting to 1000 entries.")
                entry_amount = 1000

            print("Reading entry table...")
            entry_headers = {}
            for b in range(entry_amount):
                entry_adr = getB(2)
                entry_extra = getB(2)
                entry_headers[b] = {"adr": entry_adr, "extra": entry_extra}

            entries = []
            for b in range(entry_amount):
                print(f"Processing entry {b + 1}/{entry_amount}...")
                ptr = entry_headers[b]["adr"]
                who = entry_headers[b]["extra"]
                is_sentinel = (ptr == 0xFFFF and who == 0xFFFF)
                real = entry_root + ptr
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
            for e in entries:
                del e["_sort_key"]
                e.pop("_real_start", None)
                e.pop("_real_end", None)

            output["entries"] = entries

            print("Finished processing TXT2 data.")
            return output

    except Exception as e:
        print(f"Error in preview function: {e}")
        raise e
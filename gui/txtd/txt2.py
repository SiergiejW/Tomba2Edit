"""
Reader for TOMBA2's "TXT2" chunks (SDAT id 2 or 3 - see
MainWindow.id_convert).

TXT2 turns out to be structurally IDENTICAL to a single TXTD "master"
block (see txtd.py) - the same 16-byte header (root, amount, padding),
the same (adr, extra) x u16 entry table, the same per-entry text decode
via tombadict, and the same 0xFFFF/0xFFFF "END!" sentinel convention for
an unused slot - just WITHOUT the outer master-table layer that groups
several of those blocks together under one TXTD chunk. In other words:
TXT2 is what you'd get by taking one TXTD master's entries and using
them as the whole chunk, with no master_headers wrapper at all.

This was verified against two real extracted TXT2 chunks (id=2 and
id=3) by decoding them with the actual tombadict table and confirming
the output is well-formed, grammatical English game text (tutorial
hints for id=2, item/spell pickup messages for id=3) that always ends
in a clean {$FF} terminator - not by guessing at the byte layout blind.
The two chunks looked structurally different at first glance (id=2's
"extra" field happens to be the same constant value - 0x02FF - on every
entry, while id=3's varies per entry), but that's just a data
difference, not a format difference: both decode correctly with this
exact same logic.

Entries with adr == extra == 0xFFFF are unused/blank slots (decoded as
"END!", matching txtd.py's own convention). The FIRST such slot marks
the true end of this file's real entry list - reading stops there and
never processes entry_amount's remaining declared slots. This matters
because entry_amount can reserve MORE slots than a given instance of
this chunk actually fills in (id=2's sample never needs this - its one
sentinel sits exactly at the last of its 141 slots - but a second,
fuller id=3 sample makes it unambiguous: entry_amount there is 117, yet
only the first 58 slots are real ascending (adr,extra) pairs, slot 58
is already an END sentinel, and every raw byte from slot 60 onward -
still nominally "inside the table" per entry_amount - decodes not as
more (adr,extra) pairs but as one continuous, perfectly grammatical
stretch of English ("equipped!", "used!", "acquired!", "removed!",
"given!", "sent to nest!", ... flowing seamlessly into the real
entry_root text pool with no gap at all). That's stale leftover text
sitting in reserved-but-unused table space, not real entries - the
game almost certainly stops scanning at the first blank slot too,
since there'd be no other way for it to know where the real list ends
short of entry_amount. Trying to decode past the first sentinel is
what previously showed a wall of garbled/{$EOF} "entries" after a
normal-looking start.

Stopping at the first sentinel isn't a complete fix, though - blank/
unused slots don't ALWAYS get the clean 0xFFFF/0xFFFF marker. In that
same fuller id=3 sample, entries 56 and 57 (both BEFORE the first
sentinel at 58, so still read as "real") decode not to dialogue but to
one long, perfectly regular 4-byte-per-record structure: a constant
marker byte (0x91), an ascending u16 (LE, stepping by exactly 81 every
record, ~75 records straight), and a constant 0x00 - reconstructed and
confirmed numerically, not just eyeballed. That's unmistakably some
OTHER binary structure entirely unrelated to text, whose stale pointer
values just happen to still sit in these two message-table slots
without ever having been marked 0xFFFF. See is_probably_text() below,
which flags entries like this (heavy on raw "{$XX}" byte escapes) so
the viewer can visually separate them from real, editable dialogue
without silently dropping or refusing to show their bytes.

ONE MORE WRINKLE: entry_root doesn't always sit immediately after the
real table. In that same id=3 sample, the real table (58 entries + the
END sentinel that stops it, 59 slots, ending at file offset 256) is
followed by entry_root at offset 496 - a 240-byte GAP in between. That
gap is NOT padding and NOT more garbage: byte-for-byte it's a clean run
of {$FF}-terminated strings ("equipped!", "used!", "acquired!",
"removed!", "given!", "sent to nest!", "fed!", "entered hotspring!",
"set!", "burned up!", "chanted!", "Magic Gauge grew!", "Pot of life
acquired\nMax Vitality increased by 1!", ...) - genuine, readable,
well-formed text, confirmed by reconstructing and decoding the exact
bytes. None of it is addressed by any of this file's own (adr,extra)
table entries though (verified: no table entry's computed address
lands anywhere in this range) - it's read purely sequentially, one
message after another, with no explicit pointer of its own. Whatever
mechanism actually references these strings in-game lives outside this
one file's table (a fixed/hardcoded offset, or a pointer from some
other structure elsewhere in the DAT) - this parser doesn't need to
know what that mechanism is to expose the text faithfully, though.

preview() now scans that gap (whenever entry_root is further out than
the real table needs) and returns each {$FF}-delimited fragment as its
own extra entry, tagged "is_gap": True with "adr"/"extra" left as None
(they have no table slot of their own to report). The very last gap
fragment can run past entry_root itself (this sample's does, by design
- it turned out entry_root(496) actually points into the MIDDLE of
that fragment, which is exactly how entry 0's "Vitality increased by
1!" text is a tail-shared substring of the gap's own last message,
"...Max Vitality increased by 1!") - so the scan always keeps whatever
fragment it's mid-way through at the point it reaches entry_root,
rather than truncating it.

The returned entry list is sorted into PHYSICAL FILE ORDER (by where
each entry's own text bytes actually start), not "table entries first,
then gap fragments." The (adr,extra) PAIRS live early in the file
(right after the header), but the TEXT those pairs point to lives out
at entry_root+adr - which, in this id=3 sample, is AFTER the gap. So
reading the file start to end, the gap's "equipped!"/"used!"/
"acquired!"/... messages genuinely come first, and only then entry 0's
"...Vitality increased by 1!" - a "table entries first" ordering had
this backwards and made a normal, correctly-ordered file look scrambled.

The tail-sharing seen between the gap and entry 0 isn't a one-off,
either - it's this format's normal, load-bearing space-saving trick,
used constantly WITHIN the real table too. Plenty of "real" entries
decode to obviously mid-word fragments ("ased\nMax Vitality increased
from 8 to 16!", " by 1!", "cked!", "erialized!") because their adr
points partway into another string's bytes and reuses its tail instead
of storing a duplicate copy. This was verified byte-for-byte, not just
eyeballed: entry 16's raw bytes (" by 1!") are an EXACT suffix of entry
15's raw bytes ("Vitality increased by 1!"), starting at the exact
byte where " by 1!" begins - same relationship confirmed for several
other pairs (entry 3 is a suffix of entry 2, entry 11/13 of entry 10,
entry 17 of entry 6, entry 32 of entry 31, entry 37 of entry 35, and
more). Some fragments' "parent" string isn't any of this file's own 58
visible entries at all - same as the gap/entry-0 case, they overlap
some other string that only a different table (or a fixed/hardcoded
offset) actually points at directly. Either way, every one of these
reads are still fully within entry_root+adr...+0xFF for THIS file - the
fragment looking "chopped" is the format reusing bytes, not a
miscalculated or out-of-range read.

GAPS AREN'T JUST BEFORE entry_root, EITHER. The [table_region_end,
entry_root) gap above is one specific case of a general pattern: ANY
two entries whose own [real_start, real_end) ranges don't touch or
overlap leaves genuine unaddressed bytes between them, and that can
happen anywhere in the text pool, not only right before entry_root. A
real full DAT for this same id=3 chunk has exactly this: a ~54-byte
readable message ("Took out the {$PINK}Mermaid's Scale{$WHITE}!") sits
between entry 70's " Nose." and the next table entry's own text -
neither entry's (adr,extra) pointer covers those bytes, so the single
before-entry_root gap scan never found it and it was silently missing
from the output entirely (not garbled, not flagged - just never read).

preview() now does a second pass for this: every non-sentinel table
entry's own [real_start, real_end) (real_end being wherever getText()
actually stopped - its terminator, or physical EOF) is treated as a
"covered" range, alongside the [table_region_end, entry_root) range
already handled above. Sorting all of these by start and merging
overlaps (tail-sharing means entries constantly start inside another
already-covered range, which is normal and NOT a gap) leaves only the
TRUE holes - each one strictly between two addresses this file's own
table already vouches for on both ends, so this can't drift outside
bytes the file already proved were in-range. Every hole found is
handed through the same 0xFF-delimited fragment scan as the original
gap, and merged into the same physically-ordered output list.

One consequence: some of these fragments hit the START of the next
covered range before ever finding their OWN 0xFF terminator - i.e. they
flow directly into the next entry's bytes with no separating
terminator at all (the mirror image of tail-sharing: instead of ending
where a LATER string's tail already ends, they end where the NEXT
string's front already begins). Those are still real, fully in-range,
readable text - just without an independent terminator of their own in
the original file. The packer (see functions/txt2_packer.py) gives
every entry a fresh terminator on save regardless, so re-packing this
kind of fragment adds one 0xFF that wasn't there before; the visible
text is identical either way, this just cleanly separates what used to
be two messages sharing one terminator into two independently
terminated ones going forward.
"""

import re
import struct

from gui.txtd.tombadict import letters as l

MHSIZE = 0x10

_HEX_ESCAPE_RE = re.compile(r"\{\$[0-9A-Fa-f]{2}\}")


def is_probably_text(text):
    """Heuristic: does this decoded entry look like real dialogue, or
    like non-text binary data that the (adr,extra) table sweep happened
    to catch (see module docstring)? Not a hard rule - just flags
    entries where a large share of the decoded output is raw "{$XX}"
    escapes for bytes with no defined tombadict character, which
    hand-written game dialogue essentially never has much of, but
    non-text data decoded byte-by-byte through the same table usually
    does. The universal {$FF} terminator every entry ends with is
    excluded from the count first - otherwise very short real entries
    like "-{$FF}" would get flagged just for being short.

    "{$EOF}" is a special case, not a tombadict escape at all: getText()
    appends it and bails out when a read runs past the end of the DAT/
    buffer it was given mid-decode. That only ever means one thing - the
    entry's true 0xFF terminator lives outside the bytes we were handed
    (a truncated extract, a mis-sized read, etc.) - so any entry
    containing it is *never* real, in-range text, regardless of what the
    hex-escape ratio below would say. Checked before the ratio heuristic
    since "EOF" isn't 2 hex digits and would otherwise slip past
    _HEX_ESCAPE_RE entirely.
    """
    if not text or text == "END!":
        return True
    if "{$EOF}" in text:
        return False
    stripped = text.replace("{$FF}", "")
    if not stripped:
        return True
    escaped_chars = sum(len(m.group(0)) for m in _HEX_ESCAPE_RE.finditer(stripped))
    return escaped_chars < 0.35 * len(stripped)


def preview(DAT, datstart):
    def getB(number=1):
        return int.from_bytes(rom.read(number), byteorder='little')

    def prepareText(ptr, who, real):
        if ptr == 0xFFFF and who == 0xFFFF:
            return "END!"
        else:
            print("\t{:04X}/{:04X}, (at {:04X})".format(ptr, who, real))
            return getText(real)

    def getText(real):
        # NOTE: txtd.py's own getText() has no EOF guard at all - reading
        # past the end of a real, correctly-sized DAT just keeps hitting
        # other unrelated file data byte-by-byte until it eventually finds
        # some 0xFF somewhere, so it "works" there in practice. But when
        # testing this against a standalone, hand-extracted TXT2 sample
        # (rather than the live DAT), a truncated tail with no 0xFF before
        # EOF turned an unguarded version of this same loop into an
        # infinite one (rom.read(1) at EOF returns b"", which decodes as
        # byte 0, which isn't 0xFF, forever). Since this file is read
        # every time a TXT2 entry is opened in the GUI, a single bad/
        # corrupt chunk hanging the whole app is worse than a wrong-but-
        # bounded read, so this version stops cleanly at EOF instead.
        textout = ""
        rom.seek(real)
        n = -1
        while n != 0xFF:
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

    try:
        print(f"Opening DAT file: {DAT}")
        with open(DAT, "rb") as rom:
            print(f"DAT: {DAT}")
            print(f"Seeking to datstart: {datstart}")
            rom.seek(datstart)

            def decode_bytes(raw):
                return "".join(l.get(byte, "{{${:02X}}}".format(byte)) for byte in raw)

            def scan_fragments(start, end):
                # Shared by every gap scan below (the one before
                # entry_root, and every gap found BETWEEN two entries'
                # own text - see the big comment further down). Reads
                # [start, end) as a run of 0xFF-delimited fragments,
                # exactly like getText() reads one entry.
                #
                # IMPORTANT: `end` is only ever "where the next already-
                # known entry happens to start" - it is NOT a real
                # boundary that exists in the file itself. An earlier
                # version of this function hard-stopped every fragment's
                # read right at `end`, which chopped the LAST fragment in
                # a scan off mid-word any time its real 0xFF terminator
                # actually lived a few bytes past `end` (found directly:
                # "Pot of life 1/2 acquired\nMax " with no terminator,
                # and separately "You've become {$ORANGE}Invisi" /
                # "ble{$WHITE}!" as two broken halves of one message -
                # both are the SAME bug, just showing up in the two
                # different gap regions this function serves). So only
                # the START of each fragment is bounded by `end` - once a
                # fragment has begun, it reads to its own real 0xFF (or
                # true physical EOF) same as getText() does for a normal
                # entry, never an earlier soft cutoff.
                #
                # If that last fragment's terminator turns out to sit AT
                # OR PAST `end`, its tail necessarily overlaps whatever
                # entry actually starts at `end` (that's the only way to
                # reach a byte past `end` without first hitting a
                # terminator of its own). Rather than returning that as
                # its own confusing, duplicate-looking fragment, this
                # returns it tagged "overflow" with `end` as a split
                # point - the caller merges the part BEFORE `end` onto
                # the front of whatever entry starts there, instead of
                # showing it as a second, disconnected copy of the same
                # text (see the caller for exactly how).
                #
                # Returns (frag_start, frag_text, overflow_end) tuples,
                # skipping any fragment that's a lone 0xFF (that's
                # alignment padding, not an empty message - see the
                # original table/entry_root gap's own comment history).
                # overflow_end is None for a normal, fully self-contained
                # fragment, or the position right after its real
                # terminator when it ran past `end`.
                rom.seek(start)
                pos = start
                out = []
                while pos < end:
                    frag_start = pos
                    frag = bytearray()
                    while True:
                        chunk = rom.read(1)
                        if not chunk:
                            break  # true physical EOF - the only hard stop
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
                # Runs scan_fragments(start, end) and appends real,
                # standalone gap entries to `entries` - except for a
                # final fragment tagged with an overflow_end (see
                # scan_fragments' own docstring): that one isn't a
                # separate message at all, it's unaddressed lead-in text
                # for whatever entry starts exactly at `end`. Splitting
                # its raw bytes at `end` and decoding only the part
                # BEFORE it (the part after is already decoded, correctly,
                # as that entry's own text) and prepending that onto the
                # target entry's text is what turns two broken-looking
                # halves - e.g. "You've become {$ORANGE}Invisi" shown
                # separately from "ble{$WHITE}!" - back into the one
                # coherent message they always were: "You've become
                # {$ORANGE}Invisible{$WHITE}!". Slicing the RAW BYTES
                # (not the already-decoded text) is required here since
                # "{$XX}" escapes are variable-length - byte count and
                # character count aren't the same thing.
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
                    else:
                        # No entry starts exactly at `end` (shouldn't
                        # normally happen - `end` always comes from an
                        # actual entry's own real_start, or entry_root
                        # itself) - fall back to showing it as its own
                        # fragment rather than silently losing it.
                        full_text = decode_bytes(raw)
                        print(f"Gap fragment (no merge target found): {full_text}")
                        entries.append({
                            "adr": None,
                            "extra": None,
                            "text": full_text,
                            "is_gap": True,
                            "_sort_key": frag_start,
                        })

            # Reading entry root and entry amount - same 16-byte header
            # shape as one TXTD master's own sub-header.
            print("Reading TXT2 root and entry amount...")
            entry_root_raw, entry_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
            entry_root = (entry_root_raw << 2) + MHSIZE + datstart
            print(f"Entry root: {entry_root:08X}, Entry amount: {entry_amount}")

            # Same defensive clamp as txtd.py, for the same reason: a
            # corrupt/misidentified chunk shouldn't be able to make the
            # GUI try to read thousands of bogus entries.
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
                # getText() reads straight through with no extra seeks,
                # so the file cursor is now sitting exactly one byte past
                # whatever ended this entry (its 0xFF terminator, or
                # physical EOF) - capture that as this entry's own real
                # end position. Needed below to find gaps BETWEEN two
                # entries' text, not just the one gap before entry_root.
                real_end = rom.tell()
                print(f"ptr:0x{ptr:X}, who:0x{who:X}, real:0x{real:X}")
                print(f"text:{text_content}")
                entries.append({
                    "adr": ptr,
                    "extra": who,
                    "text": text_content,
                    "is_gap": False,
                    # Where this entry's actual TEXT bytes live in the
                    # file - used only to put the final list back into
                    # physical file order below (see "_sort_key" note
                    # further down). END! has no real text of its own,
                    # so it's pushed past everything with float('inf')
                    # rather than trusting entry_root+0xFFFF, which
                    # would just coincidentally also be huge.
                    "_sort_key": float('inf') if is_sentinel else real,
                    "_real_start": None if is_sentinel else real,
                    "_real_end": None if is_sentinel else real_end,
                })
                if is_sentinel:
                    # Stop DISPLAYING at the first blank slot - see module
                    # docstring. Whatever entry_amount reserves past this
                    # point isn't shown as its own entry.
                    if b + 1 < entry_amount:
                        print(f"Hit first END sentinel at entry {b} - "
                              f"ignoring the remaining {entry_amount - b - 1} "
                              f"reserved-but-unused table slot(s).")
                    break

            # The table's TRUE physical extent can still be a few slots
            # longer than what got displayed above, though - this id=3
            # sample has the same (0xFFFF,0xFFFF) sentinel twice in a row
            # (slots 58 AND 59) before slot 60 stops being a valid pair at
            # all (it's the start of the gap text - see below). Only the
            # first sentinel is shown as an entry, but BOTH still occupy
            # real table bytes, so keep consuming immediately-following
            # sentinel slots (without displaying them) to find where the
            # table's raw bytes actually end before starting the gap scan.
            physical_table_slots = len(entries)
            if entries and entries[-1]["is_gap"] is False and \
               entries[-1]["adr"] == 0xFFFF and entries[-1]["extra"] == 0xFFFF:
                while physical_table_slots < entry_amount:
                    hdr = entry_headers[physical_table_slots]
                    if hdr["adr"] != 0xFFFF or hdr["extra"] != 0xFFFF:
                        break
                    physical_table_slots += 1

            # Gap scan #1 - see module docstring. table_region_end is
            # where the real table's own raw bytes stop (16-byte header +
            # however many (adr,extra) slots physically belong to it,
            # including any extra trailing sentinel slots just skipped
            # above); if entry_root sits further out than that,
            # everything in between is extra {$FF}-delimited text with no
            # table pointer of its own, not padding.
            table_region_end = datstart + MHSIZE + physical_table_slots * 4
            covered = []  # (start, end) ranges already accounted for -
                           # fed into gap scan #2 below.
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

            # Gap scan #2 - gaps BETWEEN two entries' own text, anywhere
            # in the rest of the text pool, not just the one region
            # before entry_root. Found the hard way: a real id=3 DAT had
            # a ~54-byte stretch of genuine, readable text (an item-
            # pickup message) sitting between entry 70's " Nose." and
            # entry 56's text - neither entry's own (adr,extra) pointer
            # covers those bytes, so gap scan #1 alone (which only ever
            # looks at the ONE region before entry_root) never touched
            # them and they were silently missing from the output. Since
            # tail-sharing (see module docstring) means entries routinely
            # START inside another entry's already-decoded bytes, a
            # "next entry's real address is past this one's end" check
            # alone isn't enough - this does a proper interval merge
            # instead: every non-sentinel entry's own [real_start,
            # real_end) is a "covered" range (real_end is exactly where
            # getText() actually stopped, terminator included); sorting
            # all covered ranges - including the [table_region_end,
            # entry_root) range from gap scan #1 above - by start and
            # merging overlaps leaves only the TRUE uncovered gaps, each
            # one strictly BETWEEN two already-verified in-file
            # addresses, so nothing here can read outside bytes this
            # file's own table already vouches for on both ends.
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

            # Put the final list back into PHYSICAL FILE ORDER before
            # returning it, rather than "table slots first, then
            # whatever's in the gap" (their construction order above,
            # which is really just "the order we happened to go looking
            # for each kind"). The table's (adr,extra) PAIRS live early
            # in the file (right after the header), but the TEXT each
            # pair points to lives out at entry_root+adr - which, in the
            # id=3 sample that motivated this, is AFTER the gap text
            # (entry_root itself sits past the gap). So byte-for-byte,
            # reading this file start to end, a person hits the gap's
            # "equipped!", "used!", "acquired!", ... messages well
            # before they'd ever reach entry 0's "...Vitality increased
            # by 1!" - the previous table-then-gap ordering had that
            # backwards. Sorting every entry (table or gap) by where its
            # own text actually starts fixes this in one place instead
            # of special-casing gap-vs-table order: whichever region's
            # bytes come first in the file is shown first. This is a
            # stable sort, so within the table entries (already read in
            # ascending adr order, which - for real, non-edited game
            # data - already climbs steadily through the text pool) and
            # within the gap fragments (already appended in the order
            # they were read off disk) nothing gets reshuffled relative
            # to itself; only the two groups swap places relative to
            # each other where needed.
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
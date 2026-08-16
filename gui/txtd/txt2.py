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
"END!", matching txtd.py's own convention) and can appear anywhere in
the table, not just at the end - the game apparently reserves more
slots than any one instance of this file necessarily fills in.
"""

from gui.txtd.tombadict import letters as l
import struct

MHSIZE = 0x10


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
                real = entry_root + ptr
                text_content = prepareText(ptr, who, real)
                print(f"ptr:0x{ptr:X}, who:0x{who:X}, real:0x{real:X}")
                print(f"text:{text_content}")
                entries.append({
                    "adr": ptr,
                    "extra": who,
                    "text": text_content,
                })

            output["entries"] = entries

            print("Finished processing TXT2 data.")
            return output

    except Exception as e:
        print(f"Error in preview function: {e}")
        raise e
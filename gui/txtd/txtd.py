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
        textout = ""
        rom.seek(real)
        n = -1
        while n != 0xFF:
            n = getB()
            if n in l:
                textout += l[n]
            else:
                surrogate = "{:02X}".format(n)
                textout += "{$" + surrogate + "}"
        return textout

    output = {"master_headers": [], "entries": []}

    try:
        print(f"Opening DAT file: {DAT}")
        with open(DAT, "rb") as rom:
            print(f"DAT: {DAT}")
            print(f"Seeking to datstart: {datstart}")
            rom.seek(datstart)

            # Reading master root and master amount
            print("Reading master root and master amount...")
            master_root, master_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
            master_root = (master_root << 2) + MHSIZE
            print(f"Master root: {master_root:08X}, Master amount: {master_amount}")

            master_headers = {}

            # Processing each master header
            for a in range(0, master_amount):
                print(f"Processing master header {a + 1}/{master_amount}...")
                master_adr = getB(2)
                master_extra = getB(2)
                master_headers[a] = {"adr": master_adr, "extra": master_extra}
                output["master_headers"].append({"adr": master_adr})  # Add to output

            # Processing each entry in master headers
            for entry in master_headers:
                destination = master_headers[entry]["adr"]
                start = datstart + (destination << 2) + master_root
                print(f"\nMaster pointer {destination:04X} (at {start:08X})")
                rom.seek(start)

                # Reading entry root and entry amount
                print("Reading entry root and entry amount...")
                entry_root, entry_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
                entry_root = (entry_root << 2) + MHSIZE + start
                print(f"Entry root: {entry_root:08X}, Entry amount: {entry_amount}")

                # Bound the number of entries read
                if entry_amount > 1000:
                    print(f"Warning: Entry amount seems unusually high ({entry_amount}). Limiting to 1000 entries.")
                    entry_amount = 1000

                entry_headers = {}
                for b in range(0, entry_amount):
                    print(f"Processing entry {b + 1}/{entry_amount}...")
                    entry_adr = getB(2)
                    entry_extra = getB(2)
                    entry_headers[b] = {"adr": entry_adr, "extra": entry_extra}

                entries = []
                # Processing each entry text
                for text in entry_headers:
                    real = entry_root + entry_headers[text]["adr"]
                    ptr = entry_headers[text]["adr"]
                    who = entry_headers[text]["extra"]
                    text_content = prepareText(ptr, who, real)
                    print(f"ptr:0x{ptr:X}, who:0x{who:X}, real:0x{real:X}")
                    print(f"text:{text_content}")
                    entries.append({
                        "adr": entry_headers[text]["adr"],  # Store relative address
                        "extra": entry_headers[text]["extra"],
                        "text": text_content
                    })

                output["entries"].append({
                    "master_adr": master_headers[entry]["adr"],  # Store relative address
                    "entry_amount": entry_amount,
                    "entries": entries
                })

            print("Finished processing TXTD data.")
            return output

    except Exception as e:
        print(f"Error in preview function: {e}")
        raise e


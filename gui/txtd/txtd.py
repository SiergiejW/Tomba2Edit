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
        with open(DAT, "rb") as rom:
            rom.seek(datstart)

            # Reading master root and master amount
            master_root, master_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
            master_root = (master_root << 2) + MHSIZE

            # Process master headers
            master_headers = []
            for _ in range(master_amount):
                master_adr = getB(2)
                master_extra = getB(2)
                master_headers.append({"adr": master_adr, "extra": master_extra})
                output["master_headers"].append({"adr": master_adr})

            # Process each master header
            for master in master_headers:
                destination = master["adr"]
                start = datstart + (destination << 2) + master_root
                rom.seek(start)

                # Reading entry root and entry amount
                entry_root, entry_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
                entry_root = (entry_root << 2) + MHSIZE + start

                # Process entry headers
                entries = []
                for _ in range(entry_amount):
                    entry_adr = getB(2)
                    entry_extra = getB(2)
                    real = entry_root + entry_adr
                    text_content = prepareText(entry_adr, entry_extra, real)
                    entries.append({
                        "adr": entry_adr,
                        "extra": entry_extra,
                        "text": text_content
                    })

                output["entries"].append({
                    "master_adr": master["adr"],
                    "entry_amount": entry_amount,
                    "entries": entries
                })

            return output

    except Exception as e:
        print(f"Error in preview function: {e}")
        raise e
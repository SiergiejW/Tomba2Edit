from tombadict import letters as l
import os
import sys
import struct

MHSIZE = 0x10




def preview(DAT, datstart):
    MHSIZE = 0x10

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

    with open(DAT, "rb") as rom:
        rom.seek(datstart)
        master_root, master_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
        master_root = (master_root << 2) + MHSIZE
        master_headers = {}
        for a in range(0, master_amount):
            master_headers[a] = {"adr": getB(2), "extra": getB(2)}

        for entry in master_headers:
            destination = master_headers[entry]["adr"]
            start = (destination << 2) + master_root
            print("\nMaster pointer {:04X} (at {:04X})".format(destination, datstart+start))
            rom.seek(dat_start+start)
            entry_root, entry_amount = struct.unpack("<HHxxxxxxxxxxxx", rom.read(MHSIZE))
            entry_root = (entry_root << 2) + MHSIZE + datstart+start
            entry_headers = {}
            for b in range(0, entry_amount):
                entry_headers[b] = {"adr": getB(2), "extra": getB(2)}


            for text in entry_headers:
                real, ptr, who = entry_root + entry_headers[text]["adr"], entry_headers[text]["adr"], entry_headers[text]["extra"]
                print(prepareText(ptr, who, real))



# Call the preview function and print the resulting dictionary.
dat_file = "C:/Users/Patryk/PycharmProjects/Tomba/iso/retail-us/CD/TOMBA2.DAT"
dat_start = 0x78A08
result = preview(dat_file, dat_start)


# Function to print the output recursively
def print_dict_recursive(d, indent=0):
    """Recursively prints a nested dictionary with indentation."""
    for key, value in d.items():
        print("  " * indent + str(key) + ":", end=" ")
        if isinstance(value, dict):
            print()  # New line before printing nested dictionary
            print_dict_recursive(value, indent + 1)
        else:
            print(value)  # Print the actual value


# Print the result
#print_dict_recursive(result)



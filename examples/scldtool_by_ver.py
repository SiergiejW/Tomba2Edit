import struct

def getB(number=1):
    return int.from_bytes(rom.read(number), byteorder='little')

#scld = "/Users/vervalkon/Downloads/20-07-"
scld = "/Users/vervalkon/Downloads/Tomba2ex_v02/Extracted/retail-us/outputfolder/chunk_08/08_sdats/08-07-_.____"
with open(scld, "rb") as rom:
    amount = getB(2)
    ptrs = []
    for p in range(0, amount):
        ptrs.append((getB(2)) << 1)
    
    for i in range(0, amount):
        rom.seek(ptrs[i])
        coords = struct.unpack("<hhhh",rom.read(8))
        print("<line class='st0' x1='{:d}' x2='{:d}' y1='{:d}' y2='{:d}'/>".format(coords[0],coords[1],coords[2],coords[3]))

#with open(scld, "rb") as rom:
#    rom.seek(0x9DC)
#    sigma = 0
#    while True
#        val = struct.unpack("<hBBBBBB",rom.read(8))[0]
#        print(val)



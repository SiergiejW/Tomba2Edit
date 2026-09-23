#Thanks to vervalkon from Tomba Club
import struct
from PIL import Image

def exportBGMP(show_msgs,name,vrampath,bgmappath,id):
    BS = 16
    PALSZ = 32
    CSKIP = 2048 - PALSZ
    PALAMOUNT = 16
    TPROW = 128
    TSKIP = 2048 - TPROW
    VRAMSIZE = 0x100000
    # wtf...
    yoff = 0

    def getClut(value):
        x = (value & 0x3F) * 0x20
        y = (value >> 6) * 0x800
        return x + y

    def unClut(value):
        a = value // 0x800
        y = a << 6
        x = (value - (a << 11)) >> 5
        return x | y

    def getB(number=1):
        return int.from_bytes(rom.read(number), byteorder='little')

    oname = "test2.png"
    vrampath = "Extracted/retail-us/AREA_10/10_vrams/10.vram"
    bgmappath = "Extracted/retail-us/AREA_10/10_sdats/10-11-_.BGMP"

    vram = open(vrampath, "rb")
    with open(bgmappath, "rb") as rom:
        tp, clut, uk1, uk2, uk3, uk4, widthW, heightR, size, uk5, uk6 = struct.unpack("<HHHHHHBBHHH", rom.read(0x14))

        # MAKE TEXPAGE PIXEL INDEX LIST
        page = []
        rowpush = 0
        if tp > 0xF:
            tp = tp & 0xF
            rowpush = 0x80000
        vram.seek(TPROW * tp + rowpush)
        for pixR in range(0, 256):
            for pixC in range(0, TPROW):
                p = ord(vram.read(1))
                page.append(p & 0x0F)
                page.append(p >> 4)
            vram.seek(TSKIP, 1)

        # MAKE A PALETTE ARRAY
        clutRoot = getClut(clut)
        vram.seek(clutRoot)
        palettes = []
        print(vrampath)
        for i in range(0, PALAMOUNT):
            pal = []
            idxes = struct.unpack("<16H", vram.read(PALSZ))
            print(i)
            for j in range(0, 16):
                _R = ((idxes[j] & 0x1F)) * 8
                _G = ((idxes[j] & 0x3E0) >> 5) * 8
                _B = ((idxes[j] & 0x7C00) >> 10) * 8
                if (idxes[j] == 0):
                    # _A = 0
                    _A = 0xFF
                else:
                    _A = 0xFF
                pal.append((_R, _G, _B, _A))
            palettes.append(pal)

            if vram.tell() + CSKIP >= VRAMSIZE:
                break
            vram.seek(CSKIP, 1)

        # MAKE A LIST OF TEXPAGES FROM ALL CLUTS
        fullpagelist = []
        for i in range(0, len(palettes)):
            pageimg = Image.new("RGBA", (256, 256))
            colorlist = []
            for j in range(0, len(page)):
                colorlist.append(palettes[i][page[j]])
            pageimg.putdata(colorlist)
            fullpagelist.append(pageimg)

        CS = (widthW << 4, heightR << 4)
        canvas = Image.new("RGBA", CS)

        for rows in range(0, heightR):
            for cols in range(0, widthW):
                blot = getB(2)
                y = (blot & 0x00F0) + yoff
                x = (blot & 0x000F) << 4
                c = blot >> 8

                usepage = fullpagelist[c]
                piece = usepage.crop((x, y, x + BS, y + BS))
                canvas.paste(piece, (cols << 4, rows << 4), piece)

        canvas.save(oname)







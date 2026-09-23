#join the Tomba club boi
import os, sys, struct
from PIL import Image

print("■ MDAT2obj.py module is active")


def exportMDAT (show_msgs,drwa_addr,path,name,id,datpath,vrampath):
    lista = []
    triangles = {32: 0, 34: 0, 37: 0, 38: 0, 39: 0, 48: 0, 50: 1, 52: 0, 54: 1}
    quads = {40: 0, 42: 0, 44: 0, 45: 0, 46: 0, 47: 0, 56: 0, 58: 1, 60: 0, 62: 1}
    transparent = False
    mtl_list = []
    if show_msgs==True:print("Now exporting MDAT from address %s in path %s"%(drwa_addr,path))
    if not os.path.exists(path):
        os.makedirs(path)

    def short(ind, off):
        rom.seek(ind + off)
        return struct.unpack("<h", rom.read(2))[0]

    def char(ind, off):
        rom.seek(ind + off)
        return struct.unpack("B", rom.read(1))[0]

    def vtx_col(ind, num, byte):
        rom.seek(ind + num)
        foo = (bytearray(rom.read(1)).hex())
        if byte == 0:
            foo = int(foo[:1], 16) / 9
        else:
            foo = int(foo[1:], 16) / 9
        return '%1.6f' % foo

    def getClutCoords(num):
        return (int((bin(num)[2:].zfill(16))[10:], 2) << 4, int((bin(num)[2:].zfill(16))[1:10], 2))

    def clutCoords2Address(intuple):
        return (intuple[0] * 2 + intuple[1] * 0x800)

    def vtx(r, g, b, b1, b2, b3):
        return str(vtx_col(ind, r, b1)) + " " + str(vtx_col(ind, g, b2)) + " " + str(vtx_col(ind, b, b3)) + "\n"

    def xyz(x, y, z):
        return str("v ") + str(short(ind, x)) + " " + str(short(ind, y)) + " " + str(short(ind, z))

    def tex(u, v):
        return "vt " + str(char(ind, u) / 256) + " -" + str(char(ind, v) / 256) + "\n"

    def printTriangle():
        xyz1 = xyz(17, 15, 13) + " " + vtx(-3, -2, -1, 0, 0, 0)
        xyz2 = xyz(19, 23, 21) + " " + vtx(1, 2, 3, 0, 0, 0)
        xyz3 = xyz(29, 27, 25) + " " + vtx(1, 2, 3, 1, 1, 1)

        tex1 = tex(5, 6)

        if tex(9, 10) == tex(5, 6):
            tex2 = "vt " + str((char(ind, 9) + 0.00001) / 256) + " -" + str((char(ind, 10) + 0.00001) / 255) + "\n"
        else:
            tex2 = tex(9, 10)

        if tex(31, 32) == tex(5, 6) or tex(31, 32) == tex(9, 10):
            tex3 = "vt " + str((char(ind, 31) + 0.00001) / 256) + " -" + str((char(ind, 32) + 0.00001) / 255) + "\n"
        else:
            tex3 = tex(31, 32)

        texmtl = "usemtl %d-%s" % (char(ind, 11) & 0x1F, str(hex(clutCoords2Address(getClutCoords(short(ind, 7)))))[2:].upper())
        faces = "f %d/%d %d/%d %d/%d" % (face, face, face + 1, face + 1, face + 2, face + 2)
        if tex1 == tex2 == tex3:
            write = (xyz1, xyz2, xyz3, "vt " + str(char(ind, off + 5) / 255) + " -" + str(char(ind, off + 6) / 255) + "\n", tex2, tex3, texmtl, "\n", faces, "\n\n")
        else:
            write = (xyz1, xyz2, xyz3, tex1, tex2, tex3, texmtl, "\n", faces, "\n\n")
        return write

    def printQuad():
        xyz1 = xyz(33, 31, 29) + " " + vtx(1, 2, 3, 0, 0, 0)
        xyz2 = xyz(21, 19, 17) + " " + vtx(-3, -2, -1, 0, 0, 0)
        xyz3 = xyz(23, 27, 25) + " " + vtx(-3, -2, -1, 1, 1, 1)
        xyz4 = xyz(35, 39, 37) + " " + vtx(1, 2, 3, 1, 1, 1)

        tex1 = tex(13, 14)

        if tex(5, 6) == tex(13, 14):
            tex2 = "vt " + str(char(ind, 5) / 256) + " -" + str((char(ind, 6) + 0.00001) / 255) + "\n"
        else:
            tex2 = tex(5, 6)

        if tex(9, 10) == tex(13, 14) or tex(9, 10) == tex(5, 6):
            tex3 = "vt " + str(char(ind, 9) / 256) + " -" + str((char(ind, 10) + 0.00001) / 255) + "\n"
        else:
            tex3 = tex(9, 10)

        if tex(15, 16) == tex(13, 14) or tex(15, 16) == tex(9, 10) or tex(15, 16) == tex(9, 10):
            tex4 = "vt " + str(char(ind, 15) / 256) + " -" + str((char(ind, 16) + 0.00001) / 255) + "\n"
        else:
            tex4 = tex(15, 16)

        texmtl = "usemtl %d-%s" % (char(ind, 11) & 0x1F, str(hex(clutCoords2Address(getClutCoords(short(ind, 7)))))[2:].upper())
        faces = "f %d/%d %d/%d %d/%d %d/%d" % (face, face, face + 1, face + 1, face + 2, face + 2, face + 3, face + 3)
        if tex1 == tex2 == tex3 == tex4 or tex1 == tex2 and tex3 == tex4 or tex1 == tex3 and tex2 == tex4 or tex1 == tex4 and tex2 == tex3:
            write = (xyz1, xyz2, xyz3, xyz4, "vt " + str(char(ind, off + 13) / 255.9) + " -" + str(char(ind, off + 14) / 255.9) + "\n", tex2, tex3, tex4, texmtl, "\n", faces, "\n\n")
        else:
            write = (xyz1, xyz2, xyz3, xyz4, tex1, tex2, tex3, tex4, texmtl, "\n", faces, "\n\n")
        return write

    def makePalette(CLUTADDRESS):
        palette = []
        num_offset = 0
        with open(vrampath+"/{:02X}.vram".format(id), "rb") as vramimg:
            for i in range(16):
                vramimg.seek(CLUTADDRESS + num_offset)
                word = int.from_bytes(vramimg.read(2), byteorder=sys.byteorder)
                R = ((word & 0x1F)) * 8
                G = ((word & 0x3E0) >> 5) * 8
                B = ((word & 0x7C00) >> 10) * 8
                if transparent == True:
                    alpha = 128
                else:
                    alpha = 255
                if R == 0 and G == 0 and B == 0:
                    palette.append((R, G, B, 0))
                else:
                    palette.append((R, G, B, alpha))
                num_offset += 2
        return palette

    def getTex(page, CLUTADDRESS):
        imagelist = makePalette(CLUTADDRESS)
        if page > 16:
            num = 512 * 1020
        else:
            num = 0
        newimglist = []
        if show_msgs == True:print("      %s/%d-%s.png" % (path,page, str(hex(CLUTADDRESS))[2:].upper()))
        with open(vrampath+"/{:02X}.vram".format(id), "rb") as vramimg:
            for n in range(256):
                for i in range(128):
                    vramimg.seek(num + i + 128 * page)
                    foo = vramimg.read(1)
                    byte = (bytearray(foo).hex())
                    pix1 = int(byte[1:], 16)
                    pix2 = int(byte[:1], 16)
                    newimglist.append(imagelist[pix1])
                    newimglist.append(imagelist[pix2])
                num += 128 * 16
            canvas2 = Image.new('RGBA', (256, 256))
            canvas2.putdata(newimglist)
            if not os.path.exists(path+"/tex/%s" % name):
                os.makedirs(path+"/tex/%s" % name)
            canvas2.save(path+"/tex/%s/%d-%s.png" % (name, page, str(hex(CLUTADDRESS))[2:].upper()))

    def createTex(page, clutaddress):
        if "%d-%s" % (page & 0x1F, str(hex(clutaddress))[2:].upper()) not in mtl_list:
            getTex(page & 0x1F, clutaddress)
            mtl_list.append("%d-%s" % (page & 0x1F, str(hex(clutaddress))[2:].upper()))
    with open(datpath, "rb") as rom:
        with open("DRWA", "wb") as out:
            rom.seek(drwa_addr)
            amount = struct.unpack("<hh", rom.read(4))
            out.write(rom.read(amount[0] * amount[1] * 2))
            drwa_size = amount[0] * amount[1] * 2
        with open("DRWA", "rb") as DRWA:
            with open(path +"/"+ name + ".obj", "w") as out:

                eye = 0
                face = 1
                for i in range(int(drwa_size / 2)):
                    DRWA.seek(eye)
                    foo = DRWA.read(2)
                    foo2 = int("%s%0s" % (bytearray(foo).hex()[2:], bytearray(foo).hex()[:2]), 16)
                    ind = drwa_addr + (foo2 * 4)
                    if 0 < foo2 < 65535:
                        foo2 = abs(foo2)
                        rom.seek(ind)
                        amount = struct.unpack("<hh", rom.read(4))
                        if show_msgs == True:print("%s: %d tris, %d quads" % (hex(ind).upper(), amount[0], amount[1]))
                        for n in range(amount[0]):  # triangles
                            ind += 7
                            if triangles[char(ind, 0)] == 1:
                                if show_msgs == True:print("   %d transp triangle (%d)" % (n + 1, char(ind, 0)))
                                transparent = True
                            else:
                                if show_msgs == True:print("   %d triangle (%d)" % (n + 1, char(ind, 0)))
                                transparent = False
                            write = printTriangle()
                            for n in write:
                                out.write(n)
                            face += 3
                            createTex(char(ind, 11), clutCoords2Address(getClutCoords(short(ind, 7))))

                            ind += 36 - 7
                            lista.append(hex(ind + 3).upper())

                        for n in range(amount[1]):  # quads
                            ind += 7
                            if quads[char(ind, 0)] == 1:
                                if show_msgs == True:print("   %d transp quad (%d)" % (n + 1, char(ind, 0)))
                                transparent = True
                            else:
                                if show_msgs == True:print("   %d quad (%d)" % (n + 1, char(ind, 0)))
                                transparent = False
                            write = printQuad()
                            for n in write:
                                out.write(n)
                            face += 4
                            createTex(char(ind, 11), clutCoords2Address(getClutCoords(short(ind, 7))))

                            ind += 44 - 7
                            lista.append(hex(ind + 3).upper())

                        with open(path +"/" + name + ".mtl", "w") as mtl:
                            for n in mtl_list:
                                write = ("newmtl %s" % (n), "\nKa 1 1 1\nKd 1 1 1\n", "map_Kd tex/%s/%s.png" % (name, n),
                                         "\nKs 1 1 1\nNs 50\nillum 7""\n\n")
                                for n in write:
                                    mtl.write(n)
                    eye += 2
                print("Exported \""+path +"/" + name + ".obj\"")









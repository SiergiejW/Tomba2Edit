baccuscluthack = 0x7FC8
CW = 64
CH = 64

sprpath = "/Users/vervalkon/Documents/LDAR-tool/JP Retail/jLDAR10_out/entry_07/D705.GAM-6.35FF.SPR"
vrampath = "/Users/vervalkon/Documents/LDAR-tool/JP Retail/jLDAR10_out/entry_07/jLDAR10.VRAM"

vram = open(vrampath, "rb")

import os
import struct
from PIL import Image, ImageDraw

def getB(number=1):
    return int.from_bytes(rom.read(number), byteorder='little')

def getSprPtrDict():
    amos = []
    offs = []
    total = 4
    while True:
        amos.append(getB(2))
        offs.append(getB(2))
        if total == min(offs):
            break
        total += 4
    
    return list(zip(amos, offs))

def getClut(value):
    if value >= 0x8000:
        print("CLUT BIT 15 SET WARNING!")
        value = value & 0x7FFF
    #value = value^0b0000010000000010 #HauntedMansionHack
    x = (value & 0x3F) * 0x20
    y = (value >> 6) * 0x800
    return x+y

def unClut(value):
    a = value//0x800
    y = a << 6
    x = (value - (a << 11)) >> 5
    return x|y


def getSprite(sprptrdictentry):
    amo, adr = tuple(sprptrdictentry)
    rom.seek(adr)
    out = []
    for i in range(0, amo):
        data = {}
        data["tlX"], data["tlY"], data["clut"], data["trX"], data["trY"], data["pg"], data["blX"], data["blY"], data["ww"], data["hh"], data["brX"], data["brY"], data["pX"], data["pY"] = struct.unpack("<BBHBBHBBBBBBbb", rom.read(0x10))
        if data["clut"] == 0x7830:
            print("Baccus hack...")
            data["clut"] = 0x7FC8
        data["getClut"] = getClut(data["clut"])
        data["8bpp"] = bool(data["pg"]&0x80==0x80) #8bpp check
        out.append(data)
    return out


def populatePool(src):
    clutPool = {}
    for x in src:
        for attrs in src[x]:
            #print("{:04X}-{:04X} ({:04X})".format(attrs["pg"], getClut(attrs["clut"]), attrs["clut"]))
            uniqlut = getClut(attrs["clut"])
            if uniqlut not in clutPool:
                ebpp = attrs["8bpp"]
                clutPool[attrs["clut"]] = getPal(uniqlut, ebpp)
    return clutPool

def getPal(adr, ebpp):
    rta = vram.tell()
    vram.seek(adr)  #HACK for 8bpp uncompliance
    palette = []
    #984576
    if ebpp:
        palstr = struct.unpack("256H", vram.read(0x200))
    else:
        palstr = struct.unpack("16H", vram.read(0x20))
    for num in palstr:
        _R = ((num & 0x1F)) * 8
        _G = ((num & 0x3E0) >> 5) * 8
        _B = ((num & 0x7C00) >> 10) * 8
        if (num == 0):
            _A = 0
        else:
            _A = 0xFF
        palette.append((_R,_G,_B,_A))
    vram.seek(rta)
    print("Length of palette:",len(palette))
    return palette

def getvramidxlist():
    bindexes = []
    for r in range(0,512):
        row = struct.unpack("2048B",vram.read(2048))
        for x in row:
            bindexes.append(x&0xF)
            bindexes.append(x>>4)
    return bindexes

def loop(number):
    if number > 0x1F:
        return number - 32
    else:
        return number

def doSprite(spritelist):
    strip = Image.new("RGBA", (CW, CH*32))
    for MAGIC in range(0,31):
        canvas = Image.new("RGBA", (CW,CH))
        pque = []
        for s in spritelist: #s on dicti joka sisältää kaikki ominaisuudet
            normalised_texpage = loop((s["pg"] & 0x1F) + MAGIC)#TÄÄ ON SE JUTTU
            txpgstr = "-{:02d}".format(normalised_texpage)
            if s["8bpp"]:
                bppstr = "E"
            else:
                bppstr = "F"
            #usepage = pagedict[s["clut"]][normalised_texpage]    <noncache
            usepage = Image.open(gfxFolder+"{:s}{:04X}".format(bppstr, s["clut"])+txpgstr+".png")
            
            
            
            #SHIT implementation does not consider rotation, only flips. also no stretch.
            hflip = False
            vflip = False
            
            if (s["tlX"] > s["trX"] or s["blX"] > s["brX"]):
                hflip = True
            
            if (s["tlY"] > s["blY"] or s["trY"] > s["brY"]):
                vflip = True
            
            oX = min([s["tlX"], s["trX"], s["blX"], s["brX"]])
            oY = min([s["tlY"], s["trY"], s["blY"], s["brY"]])
            oW = oX + s["ww"]
            oH = oY + s["hh"]
            
            piece = usepage.crop((oX,oY,oW,oH))
            
            if (hflip):
                piece = piece.transpose(Image.FLIP_LEFT_RIGHT)
            if (vflip):
                piece = piece.transpose(Image.FLIP_TOP_BOTTOM)
                
            pque.insert(0,[piece, (CW>>1)+s["pX"], (CH>>1)+s["pY"]])#palasen tiedonjyvät jotka menee jonoon
        
        #lätkäise ne kiinni canvaan
        for p in range(0,len(pque)):
            x = pque[p]
            canvas.paste(x[0],(x[1],x[2]),x[0])
        
        strip.paste(canvas, (0, CH*MAGIC))
    #canvas.show()
    return strip

def mdir(directory):
  if not os.path.exists(directory):
    os.makedirs(directory)

def bppmerge(inlist):
    outlist = []
    for i in range(0,len(inlist),2):
        outlist.append(int("0x{:X}{:X}".format(inlist[i+1] , inlist[i]), 16))
    return outlist

outFolder = "/Users/vervalkon/Documents/Ultimate SPRTOOL/out/"
logfile = open(outFolder+"log.js", "w")

with open(sprpath, "rb") as rom:
    sprptrdict = getSprPtrDict()
    sprites = {}
    for entry in range(0,len(sprptrdict)):
        sprites[entry] = getSprite(sprptrdict[entry])
        logfile.write("{:d}: ".format(entry)+str(sprites[entry])+"\n")
    
    pool = populatePool(sprites)
    vramlist = getvramidxlist()
    full_vramlist = bppmerge(vramlist)
    pagedict = {}
    
    gfxFolder = outFolder+"GFX/"
    
    if not os.path.exists(gfxFolder):
        mdir(gfxFolder)
        
        for rgbalist in pool:
            if len(pool[rgbalist]) == 16:
                print("yes, it's 16:",pool[rgbalist])
                framebuffer = Image.new("RGBA", (4096, 512))
                
                inter_framebuffer = []
                for px in vramlist:
                    inter_framebuffer.append(pool[rgbalist][px])
                framebuffer.putdata(inter_framebuffer)
                
                framebuffer.save(gfxFolder+"F{:04X}.png".format(rgbalist))
                
                texpages = []
                for fr in range(0,2):
                    t = fr << 8
                    b = t + 256
                    for fc in range(0,16):
                        l = fc << 8
                        r = l + 256
                        texpage = framebuffer.crop((l,t,r,b))
                        texpage.save(gfxFolder+"F{:04X}-{:02d}.png".format(rgbalist, (fr<<4)+fc))
                        texpages.append(texpage)
                
                pagedict[rgbalist] = texpages
            else:
                print("Longer than 16, it's an 8bpp thingie")
                framebuffer = Image.new("RGBA", (2048, 512))
                
                inter_framebuffer = []
                for px in full_vramlist:
                    inter_framebuffer.append(pool[rgbalist][px])
                framebuffer.putdata(inter_framebuffer)
                
                framebuffer.save(gfxFolder+"E{:04X}.png".format(rgbalist))
                
                texpages = []
                for fr in range(0,2):
                    t = fr << 8
                    b = t + 256
                    for fc in range(0,16):
                        l = fc << 7
                        r = l + 128
                        texpage = framebuffer.crop((l,t,r,b))
                        texpage.save(gfxFolder+"E{:04X}-{:02d}.png".format(rgbalist, (fr<<4)+fc))
                        texpages.append(texpage)
                
                pagedict[rgbalist] = texpages
    
    
    #print(pagedict)
    
    megashit = Image.new("RGBA", (len(sprites)*CW , CH*32))
    for spriteout in range(0,len(sprites)):
        #doSprite(sprites[spriteout]).save(outFolder+"{:02d}.png".format(spriteout)) < tallenna individuaalit
        megashit.paste(doSprite(sprites[spriteout]) , (CW*spriteout, 0))
    
    megashit.save(outFolder+"OUT.png")
        





#    [{'sX': 0, 'sY': 127, 'clut': 31760, 'u0': 31, 'u1': 127, 'pg': 96, 'xx': 0, 'yy': 104, 'ww': 32, 'hh': 24, 'u2': 31, 'u3': 104, 'pX': -16, 'pY': 4},
#    {'sX': 168, 'sY': 48, 'clut': 31824, 'u0': 191, 'u1': 48, 'pg': 96, 'xx': 168, 'yy': 71, 'ww': 24, 'hh': 24, 'u2': 191, 'u3': 71, 'pX': -28, 'pY': 1}, {'sX': 168, 'sY': 48, 'clut': 31824, 'u0': 191, 'u1': 48, 'pg': 96, 'xx': 168, 'yy': 71, 'ww': 24, 'hh': 24, 'u2': 191, 'u3': 71, 'pX': -31, 'pY': -13}, {'sX': 168, 'sY': 48, 'clut': 31824, 'u0': 191, 'u1': 48, 'pg': 96, 'xx': 168, 'yy': 71, 'ww': 24, 'hh': 24, 'u2': 191, 'u3': 71, 'pX': -22, 'pY': -24}, {'sX': 168, 'sY': 48, 'clut': 31824, 'u0': 191, 'u1': 48, 'pg': 96, 'xx': 168, 'yy': 71, 'ww': 24, 'hh': 24, 'u2': 191, 'u3': 71, 'pX': -3, 'pY': -23}, {'sX': 168, 'sY': 48, 'clut': 31824, 'u0': 191, 'u1': 48, 'pg': 96, 'xx': 168, 'yy': 71, 'ww': 24, 'hh': 24, 'u2': 191, 'u3': 71, 'pX': 5, 'pY': -13}]


#one piece's spec is:
#BBHBBHBBBBBBbb
#where the args are
#sX, sY, clut, u0, u1, pg, xx, yy, ww, hh, u2, u3, pX, pY
#B = unsigned byte
#H = unsigned word (16 bits, LE)
#b = signed byte

#caterpiggar: F8A00 ja F8200 eli 1018368 ja 1016320
#sX sY colut u0 u1 paggg xx yy ww hh u2 u3 pX pY
#00 00 50 7C 17 00 60 00 00 17 18 18 17 17 F4 F2
#18 00 50 7C 2F 00 60 00 18 17 18 18 2F 17 F4 F2
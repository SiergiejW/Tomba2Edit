# join the tomba club, boi
import os, sys, struct, MDAT2obj, SMST2obj, BGMP2png
from PIL import Image


def mdir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def tuplify(item):
    dat_id = item >> 24
    dat_ptr = item & 0x00FFFFFF
    return (dat_id, dat_ptr)

lista = {}
comparedlist = {}
newlista = []
outlist = {}
unknown_count = 0
unmapped_list = []
newMAP = {}

#################################################################
###EDIT THESE TWO LINES TO SPECIFY CD FOLDER AND OUTPUT FOLDER###
#################################################################
ver = "retail"                                                     #choose "demo" or "retail" version of the game
#CDpath = "iso/" + ver + "-us/CD/"
CDpath = "../iso/retail-us/CD"                              #where the game's DAT files reside?
#outfolder = "Extracted/" + ver + "-us"
outfolder = "Extracted/retail-us/"                          #where do you want the extracted files to go to?

if ver == "demo":
    tombamap = "TOMBAMAPdemo.txt"                                #txt demo file which contains MAPped addresses
elif ver == "retail":
    tombamap = "TOMBAMAP.txt"                                    #txt retail file which contains MAPped addresses
else:   #proto
    tombamap = "TOMBAMAP.txt"
#################################################################
##Don't forget to omit the forward-slash in the end of the name##
#################################################################
def save_vram_as_png(vram_path, png_path):
    # Read the VRAM file
    with open(vram_path, 'rb') as f:
        vram_data = f.read()

    # VRAM is typically 1024x512 pixels with 16-bit color
    width = 1024
    height = 512

    # Create a new image with RGB mode
    img = Image.new('RGB', (width, height))
    pixels = img.load()

    # Convert 16-bit color to 24-bit RGB
    for y in range(height):
        for x in range(width):
            # Get the 16-bit color value (2 bytes per pixel)
            pos = y * width * 2 + x * 2
            if pos + 1 >= len(vram_data):
                break
            color16 = (vram_data[pos + 1] << 8) | vram_data[pos]

            # Convert 16-bit (5-5-5-1) to 24-bit RGB
            r = ((color16 >> 10) & 0x1F) << 3
            g = ((color16 >> 5) & 0x1F) << 3
            b = (color16 & 0x1F) << 3

            pixels[x, y] = (r, g, b)

    # Save as PNG
    img.save(png_path)

def id_convert(id):
    if ver=="demo":     #DEMO IDs
        if id==0: #sprites general
            return "SPRT"
        elif id==2 or id==3 or id==11: #text
            return "TXTD"
        elif id==4: #tomba animation
            return "TANP"
        elif id==5: #collision
            return "SCLD"
        elif id==6: #level data
            OUTDAT.seek(int(pointer_start, 16)+4)
            if struct.unpack("<h",OUTDAT.read(2))[0] == -1:
                return "MDAT"
        elif id==7: #level drawmap
            return "DRWB"
        elif id==8: #level 2D sprites
            return "SPRT"
        elif id==9: #background graphics
            return "BGMP"
        elif id==10 or id==13 or id==1: #10=asset pack, 1=weapon pack or 13=zippo model
            return "SMST"
        elif id ==12: #other type of animations
            return "BETP"
        elif id==14: #zippo animation ALFD
            return "ALFD"
        elif id>=15: #smst, mdat or alfd
            OUTDAT.seek(int(pointer_start, 16)+4) #check if mdat
            if struct.unpack("<h",OUTDAT.read(2))[0] == -1:
                return "MDAT"
            OUTDAT.seek(int(pointer_start, 16))
            if struct.unpack("<h",OUTDAT.read(2))[0]: #check if alfd
                return "ALFD"
            else: #must be SMST
                return "SMST"
        else:
            return "NULL"

    elif ver=="retail":               #RETAIL IDs
        if id==0: #sprites general
            return "SPRT"
        elif id==2 or id==3 or id==13: #text
            return "TXTD"
        elif id==4: #tomba animation
            return "TANP"
        elif id==6: #baron animation
            return "TANP"
        elif id==7: #collision
            return "SCLD"
        elif id==8: #level data
            OUTDAT.seek(int(pointer_start, 16)+4)
            if struct.unpack("<h",OUTDAT.read(2))[0] == -1:
                return "MDAT"
        elif id==9: #level drawmap
            return "DRWB"
        elif id==10: #level 2D sprites
            return "SPRT"
        elif id==11: #background graphics
            return "BGMP"
        elif id==12 or id==16 or id==1 or id==5: #12=asset pack 1=weapon pack 5=baron model 16=zippo model
            return "SMST"
        elif id ==14: #other type of animations
            return "BETP"
        elif id==17: #zippo animation ALFD
            return "ALFD"
        elif id>=18: #smst, mdat or alfd
            OUTDAT.seek(int(pointer_start, 16)+4)
            if struct.unpack("<h",OUTDAT.read(2))[0] == -1: #check if MDAT
                return "MDAT"
            OUTDAT.seek(int(pointer_start, 16))
            if struct.unpack("<h",OUTDAT.read(2))[0]: #check if ALFD
                return "ALFD"
            else: #must be SMST
                return "SMST"
        else:
            return "NULL"
    elif ver=="proto":               #proto IDs
        if id==0: #sprites general
            return "SPRT"
        elif id==2 or id==3 or id==13: #text
            return "TXTD"
        elif id==4: #tomba animation
            return "TANP"
        elif id==6: #baron animation
            return "TANP"
        elif id==7: #collision
            return "SCLD"
        elif id==8: #level data
            OUTDAT.seek(int(pointer_start, 16)+4)
            if struct.unpack("<h",OUTDAT.read(2))[0] == -1:
                return "MDAT"
        elif id==9: #level drawmap
            return "DRWB"
        elif id==10: #level 2D sprites
            return "SPRT"
        elif id==11: #background graphics
            return "BGMP"
        elif id==12 or id==16 or id==1 or id==5: #12=asset pack 1=weapon pack 5=baron model 16=zippo model
            return "SMST"
        elif id ==14: #other type of animations
            return "BETP"
        elif id==17: #zippo animation ALFD
            return "ALFD"
        elif id>=18: #smst, mdat or alfd
            OUTDAT.seek(int(pointer_start, 16)+4)
            if struct.unpack("<h",OUTDAT.read(2))[0] == -1: #check if MDAT
                return "MDAT"
            OUTDAT.seek(int(pointer_start, 16))
            if struct.unpack("<h",OUTDAT.read(2))[0]: #check if ALFD
                return "ALFD"
            else: #must be SMST
                return "SMST"
        else:
            return "NULL"

######## ACTUAL CODE STARTS HERE ########
mdir(outfolder)

idxpath = CDpath + "/TOMBA2.IDX"
datpath = CDpath + "/TOMBA2.DAT"
imgpath = CDpath + "/TOMBA2.IMG"

IDX = open(idxpath, "rb")
DAT = open(datpath, "rb")
IMG = open(imgpath, "rb")
OUTDAT = open(datpath, "rb")

chunk_size = 0x800
trailer = 0x700

for chunk_index in range(int(os.path.getsize(idxpath) / chunk_size)):
    # START PART
    print("Reading Chunk index {:02X}...".format(int(str(chunk_index),16)))

    IDX.seek(chunk_index * chunk_size)
    img_start, img_end, dat_start, dat_end, pointer_amount = struct.unpack("<5I", IDX.read(20))
    if not any([img_start, img_end, dat_start, dat_end, pointer_amount]):
        print("Chunk index {:02X} is completely empty.".format(int(str(chunk_index),16)))
        dest = outfolder + "/AREA_{:02X}".format(int(str(chunk_index), 16))
        mdir(dest)
        continue

    IMG.seek(img_start)
    imgdata = IMG.read(img_end - img_start)

    DAT.seek(dat_start)
    datdata = DAT.read(dat_end - dat_start)

    sdat_pointers = [tuplify(item) for item in struct.unpack("<{:d}I".format(pointer_amount), IDX.read(pointer_amount * 4))]

    # TRAILER PART
    IDX.seek(chunk_index * chunk_size + (chunk_size - trailer))
    traildata = struct.unpack("<{:d}I".format(trailer >> 2), IDX.read(trailer))
    trail_list = []
    for t in range(0, len(traildata), 2):
        dat_trail_start, dat_trail_end = traildata[t], traildata[t + 1]
        dat_trail_size = dat_trail_end - dat_trail_start
        if dat_trail_size != 0:
            trail_list.append((dat_trail_start, dat_trail_end, dat_trail_size))

    # WRAPPING IT ALL UP
    dest = outfolder + "/AREA_{:02X}".format(int(str(chunk_index),16))
    tbadest = dest + "/{:02X}_trail".format(int(str(chunk_index),16))

    mdir(dest)
    mdir(tbadest)



    if imgdata:
        imgdest = dest + "/{:02X}_vrams".format(int(str(chunk_index),16))
        mdir(imgdest)

        out_cvram = open(imgdest + "/{:04X}-{:04X}.cvram".format(img_start, img_end), "wb+")
        out_cvram.write(imgdata)
        out_cvram.seek(0)

        ####### vvv LEGACY UNIMG CODE, MAY BE CONFUSING BUT WORKS vvv ######
        sharddest = imgdest + "/{:02X}_shards".format(int(str(chunk_index),16))
        mdir(sharddest)

        c_header_amount = struct.unpack("<I", out_cvram.read(4))[0]
        c_header_size = c_header_amount * 0xC + 4
        skip = 0x800 - c_header_size
        c_header_list = []

        for h in range(c_header_amount):
            c_header = struct.unpack("<HHHHI", out_cvram.read(12))
            c_header_list.append(c_header)
        out_cvram.read(skip)
        vram_build_entry = []

        for i in range(c_header_amount):
            print("sharding round {:d}, at {:02X}".format(i, out_cvram.tell()))
            shardfilename = sharddest + "/{:02X}-{:d}.shard".format(int(str(chunk_index),16), i)
            with open(shardfilename, "wb+") as shard:
                # init
                x = c_header_list[i][0]
                y = c_header_list[i][1]
                w = c_header_list[i][2]
                h = c_header_list[i][3]
                s = c_header_list[i][4]
                vram_build_entry.append([shardfilename, x, y, w, h])
                lz = w * 2
                shardsize = w * 2 * h
                extras = [0, -1, -lz, -lz - 1, -lz - 2, -lz - 3, -lz + 1, -lz + 2]
                scompare = 0
                while True:
                    base = ord(out_cvram.read(1))
                    scompare += 1
                    if (scompare >= s):
                        break

                    amount = base >> 3
                    extra = base & 7
                    if (extra == 0):
                        shard.write(out_cvram.read(amount))
                        scompare += amount
                        if (scompare >= s):
                            break
                    if (extra != 0):
                        for i in range(amount):
                            shard.seek(extras[extra], 2)
                            b = shard.read(1)
                            shard.seek(0, 2)
                            shard.write(b)

        print("\n\tAll Shards done. Now making the real VRAM page.\n")
        with open(imgdest + "/{:02X}.vram".format(int(str(chunk_index),16)), "w+b") as vram:
            vram.seek(0x100000 - 1)
            vram.write(b"\0")
            for j in range(len(vram_build_entry)):
                entry = vram_build_entry[j]
                with open(entry[0], "rb") as inshard:
                    x = entry[1]
                    y = entry[2]
                    w = entry[3]
                    h = entry[4]
                    vram.seek((x * 2) + (y * 0x800))
                    for k in range(h):
                        vram.write(inshard.read(w * 2))
                        vram.read(0x800 - w * 2)
        ####### ^^^ LEGACY UNIMG CODE, MAY BE CONFUSING BUT WORKS ^^^ ######
        vram_path = imgdest + "/{:02X}.vram".format(int(str(chunk_index), 16))
        png_path = imgdest + "/{:02X}.png".format(int(str(chunk_index), 16))
        save_vram_as_png(vram_path, png_path)

        out_cvram.close()


    else:
        print("Chunk index {:02X} missing IMG pointers.".format(int(str(chunk_index),16)))

    if datdata:
        datdest = dest + "/{:02X}_sdats".format(int(str(chunk_index),16))
        mdir(datdest)


        out_sdat = open(datdest + "/{:04X}-{:04X}.sdat".format(dat_start, dat_end), "wb")
        out_sdat.write(datdata)
        out_sdat.close()
        out_sdat_info = open(datdest + "/{:02X}_pointers.txt".format(int(str(chunk_index),16)), "w")
        out_sdat_info.write("**** Indexes and Pointers for chunk_index {:02X}'s SDAT ****\n".format(int(str(chunk_index),16)))

        for i in range(0, len(sdat_pointers)):
            map_matched = False
            pointer_start = hex(dat_start + sdat_pointers[i][1])[2:].zfill(6).upper()

            if i < len(sdat_pointers) - 1 and len(sdat_pointers) > 1:
                pointer_end = hex(dat_start + sdat_pointers[i + 1][1] - 1)[2:].zfill(6).upper()
            else:
                pointer_end = hex(dat_end - 1)[2:].zfill(6).upper()
            with open(tombamap, "r") as rommap:
                romlines = [line.rstrip('\n') for line in rommap]
                romlines[0] = romlines[0][3:]
                for n in romlines:
                    file_adr = n[:6]
                    file_type = id_convert(sdat_pointers[i][0])
                    file_name = n[23:]
                    if file_adr == pointer_start:
                        map_matched = True
                        print("\tID: {:02X} | %s-%s :  %s : %s".format(int(str(sdat_pointers[i][0]),16)) % (pointer_start, pointer_end, file_type, file_name))
                        newMAP.update({pointer_start: "%s-%s : %s : %s" % (pointer_start, pointer_end, file_type, file_name)})

                        OUTDAT.seek(int(pointer_start,16))
                        outdata = OUTDAT.read(int(pointer_end, 16) - int(pointer_start, 16) + 1)
                        outfile = open(datdest + "/{:02X}-{:02X}-%s.%s".format(int(str(chunk_index),16), int(str(sdat_pointers[i][0]),16))%(file_name,file_type), "wb")
                        outfile.write(outdata)
                        outfile.close()
                        out_sdat_info.write("ID: {:02X} | Pointer: {:06X} | %s-%s : %s : %s\n".format(int(str(sdat_pointers[i][0]),16), int(str(sdat_pointers[i][1]),16)) % (pointer_start, pointer_end, file_type,file_name))

                        if file_type == "BGMP":
                            print("     ■ BGMP2png: %s.png..." % (file_name))
                            #BGMP2png.exportBGMP(False, dat_start + sdat_pointers[i][1], datdest + "/SMST", file_name, int(str(chunk_index), 16), datpath, dest)

                        elif file_type == "MDAT":
                            print ("     ■ MDAT2obj: %s.obj..."%(file_name))
                            MDAT2obj.exportMDAT(False,dat_start + sdat_pointers[i][1], datdest + "/MDAT", file_name, int(str(chunk_index),16),datpath,imgdest)
                        elif file_type == "SMST":
                            print("     ■ SMST2obj: %s.obj..." % (file_name))
                            SMST2obj.exportSMST(False, dat_start + sdat_pointers[i][1], datdest + "/SMST", file_name, int(str(chunk_index), 16), datpath, dest)
                        break

                if not map_matched:
                    file_type = id_convert(sdat_pointers[i][0])
                    print("\tID: {:02X} | %s-%s : %s : unknown, UNMAPPED".format(sdat_pointers[i][0]) % (
                    pointer_start, pointer_end,file_type))
                    newMAP.update({pointer_start: "%s-%s : ____ : unknown" % (pointer_start, pointer_end)})
                    unmapped_list.append("%s-%s : unknown" % (pointer_start, pointer_end))
                    unknown_count += 1

                    OUTDAT.seek(int(pointer_start, 16))
                    outdata = OUTDAT.read(int(pointer_end, 16) - int(pointer_start, 16) + 1)
                    outfile = open(datdest + "/{:02X}-{:02X}-unknown.%s".format(int(str(chunk_index), 16), int(str(sdat_pointers[i][0]), 16))%(file_type), "wb")
                    outfile.write(outdata)
                    outfile.close()

                    out_sdat_info.write("ID: {:02X} | Pointer: {:06X} | %s-%s : ____ : unknown\n".format(sdat_pointers[i][0], sdat_pointers[i][1]) % (pointer_start, pointer_end))
                    if file_type == "MDAT":
                        print("     ■ MDAT2obj: %s.obj..." % (file_name))
                        MDAT2obj.exportMDAT(False, dat_start + sdat_pointers[i][1], datdest + "/MDAT", pointer_start+file_name, int(str(chunk_index), 16), datpath, imgdest)
                    elif file_type == "SMST":
                        print("     ■ SMST2obj: %s.obj..." % (file_name))
                        SMST2obj.exportSMST(False, dat_start + sdat_pointers[i][1], datdest + "/SMST", pointer_start+file_name, int(str(chunk_index), 16), datpath, dest)
                    elif file_type == "BGMP":
                        print("     ■ BGMP2png: %s.png..." % (file_name))
                        #BGMP2png.exportBGMP(False, dat_start + sdat_pointers[i][1], datdest + "/BGMP", pointer_start+file_name, int(str(chunk_index), 16), datpath, dest)
            lista.update({pointer_start: pointer_end})

        out_sdat_info.close()




    else:
        print("Chunk index {:02X} missing DAT pointers.".format(int(str(chunk_index),16)))

    if traildata:
        dattraildest = dest + "/{:02X}_trail".format(int(str(chunk_index), 16))
        mdir(dattraildest)
        out_trail_info = open(tbadest + "/{:02X}_trail_pointers.txt".format(int(str(chunk_index),16)), "w")
        out_trail_info.write("**** Indexes and Pointers for chunk_index {:02X}'s trail data ****\n".format(chunk_index))
        for i in range(0, len(trail_list)):
            map_matched = False
            adr, end, sz = trail_list[i][0], trail_list[i][1], trail_list[i][2]
            DAT.seek(adr)
            lista.update({adr: end})
            out_trail_info.write("{:04X}-{:04X} \n".format(adr, end))
            OUTDAT.seek(adr)
            if struct.unpack("<h", OUTDAT.read(2))[0] != 0:
                # print("MDAT trail export here")
                file_type = "MDAT"
            else:
                # print("SMST trail export here")
                file_type = "SMST"
            with open(tombamap, "r") as rommap:
                romlines = [line.rstrip('\n') for line in rommap]
                romlines[0] = romlines[0][3:]
                adrhex = hex(adr)[2:].zfill(6).upper()
                endhex = hex(end)[2:].zfill(6).upper()
                for n in romlines:
                    file_adr = n[:6]
                    file_name = n[23:].replace(" ","_")
                    if file_adr == adrhex:
                        map_matched = True
                        print("%s-%s : %s : %s" % (adr, end, file_type, file_name))
                        newMAP.update({adrhex: "%s-%s : %s : %s" % (adrhex, endhex, file_type, file_name)})
                        out_tba = open(tbadest + "/{:06X}-%s.%s".format(adr)%(file_name,file_type), "wb")
                        out_tba.write(DAT.read(sz))
                        out_tba.close()
                        if file_type == "MDAT":
                            print("     ■ MDAT2obj: %s.obj..." % (file_name))
                            MDAT2obj.exportMDAT(False, int(adrhex, 16), dattraildest + "/MDAT", file_name, int(str(chunk_index), 16), datpath, imgdest)
                        elif file_type == "SMST":
                            print("     ■ SMST2obj: %s.obj..." % (file_name))
                            SMST2obj.exportSMST(False, int(adrhex,16), dattraildest + "/SMST", file_name, int(str(chunk_index), 16), datpath, dest)
                        break
                if not map_matched:
                    print("%s-%s : ____ : unknown trail data, UNMAPPED" % (adrhex, endhex))
                    newMAP.update({adrhex: "%s-%s : ____ : unknown trail" % (adrhex, endhex)})
                    if "%s-%s : ____ : unknown trail data" % (adrhex, endhex) not in unmapped_list:
                        unmapped_list.append("%s-%s : ____ : unknown trail data" % (adrhex, endhex))
                        unknown_count += 1
                    out_tba = open(tbadest + "/{:06X}-unknown.%s".format(adr)%(file_type), "wb")
                    out_tba.write(DAT.read(sz))
                    out_tba.close()
                    file_name = file_name+str(adr)
                    if file_type == "MDAT":
                        print("     ■ MDAT2obj: %s.obj..." % (file_name))
                        MDAT2obj.exportMDAT(False, int(adrhex, 16), dattraildest + "/MDAT", file_name, int(str(chunk_index), 16), datpath, imgdest)
                    elif file_type == "SMST":
                        print("     ■ SMST2obj: %s.obj..." % (file_name))
                        SMST2obj.exportSMST(False, int(adrhex, 16), dattraildest + "/SMST", file_name, int(str(chunk_index), 16), datpath, dest)


        out_trail_info.close()


    else:
        print("Chunk index {:02X} missing trail pointers.".format(int(str(chunk_index),16)))
OUTDAT.close()

newMAPlist = []
for n in newMAP:
    newMAPlist.append(newMAP[n])
newMAPlist.sort()
with open("new" + tombamap, "w") as newtombamap:
    for n in newMAPlist:
        newtombamap.write(n + "\n")

if len(unmapped_list) != 0:
    for n in unmapped_list:
        print(n)
    print("\n\"%s\" has been updated with %d missing addresses:" % ("new" + tombamap, unknown_count))
else:
    print("\n\tEvery exported entry has been mapped.")








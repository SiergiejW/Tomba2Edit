import struct



with open("C:/Users/Patryk/PycharmProjects/Tomba310/repacker/out_new.txt", "w") as out:
    out.write("img_strt img_end  dat_strt dat_end     SDAMOUNT     ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS  ID ADRESS    TRAIL DATA IS HERE\n")
    with open("C:/Users/Patryk/PycharmProjects/Tomba310/repacker/NEW_TOMBA2.IDX", "rb") as rom:
        rom.seek(0,2)
        chunk_amount = int(rom.tell()/0x800)
        rom.seek(0)
        for i in range(0, chunk_amount):
            a = struct.unpack("<5I",rom.read(0x14))
            b = struct.unpack("<59I",rom.read(0xEC))
            c = struct.unpack("<448I", rom.read(0x700))
            out.write("{:08X} {:08X} {:08X} {:08X}    {:08d}  =  ".format(a[0],a[1],a[2],a[3],a[4]))
            for j in range(0,len(b)):
                out.write("{:02d} {:06X}  ".format(b[j] >> 24, b[j] & 0x00FFFFFF))
            out.write("  ")
            for j in range(0,len(c)):
                out.write("{:08X} ".format(c[j]))
            out.write("\n")



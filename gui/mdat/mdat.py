import struct
import io

def exportMDAT(drwa_addr, datpath):
    base_idx = 0
    model_data = {
        'vertices': [],
        'vertex_colors': [],
        'faces': []
    }

    triangles = {32: 0, 34: 0, 37: 0, 38: 0, 39: 0, 48: 0, 50: 1, 52: 0, 54: 1}
    quads = {40: 0, 42: 0, 44: 0, 45: 0, 46: 0, 47: 0, 56: 0, 58: 1, 60: 0, 62: 1}
    transparent = False

    print(f"Now exporting MDAT from address 0x{drwa_addr:X}")

    def short(rom, ind, off):
        rom.seek(ind + off)
        return struct.unpack("<h", rom.read(2))[0]

    def char(rom, ind, off):
        rom.seek(ind + off)
        return struct.unpack("B", rom.read(1))[0]

    def vtx_col(rom, ind, num, byte):
        rom.seek(ind + num)
        val = rom.read(1)[0]
        return f"{(val & 0x0F if byte else val >> 4) / 9:.6f}"

    def vtx(rom, ind, r, g, b, b1, b2, b3):
        return [vtx_col(rom, ind, r, b1), vtx_col(rom, ind, g, b2), vtx_col(rom, ind, b, b3)]

    def xyz(rom, ind, x, y, z):
        return [short(rom, ind, x), -short(rom, ind, y), short(rom, ind, z)]  # Flip Y

    with open(datpath, "rb") as rom:
        rom.seek(drwa_addr)
        amount_x, amount_y = struct.unpack("<hh", rom.read(4))
        drwa_size = amount_x * amount_y * 2
        drwa_data = rom.read(drwa_size)
        drwa = io.BytesIO(drwa_data)

        eye = 0
        face = 1
        while eye < drwa_size:
            drwa.seek(eye)
            raw = drwa.read(2)
            val = int(f"{raw[1]:02X}{raw[0]:02X}", 16)
            if 0 < val < 0xFFFF:
                ind = drwa_addr + (val * 4)
                rom.seek(ind)
                num_tris, num_quads = struct.unpack("<hh", rom.read(4))
                print(f"0x{ind:X}: {num_tris} tris, {num_quads} quads")

                # Triangles
                for _ in range(num_tris):
                    ind += 7
                    ttype = char(rom, ind, 0)
                    transparent = bool(triangles.get(ttype, 0))
                    label = "transp triangle" if transparent else "triangle"
                    print(f"   {face} {label} ({ttype})")

                    v1 = xyz(rom, ind, 17, 15, 13)
                    c1 = vtx(rom, ind, -3, -2, -1, 0, 0, 0)

                    v2 = xyz(rom, ind, 19, 23, 21)
                    c2 = vtx(rom, ind, 1, 2, 3, 0, 0, 0)

                    v3 = xyz(rom, ind, 29, 27, 25)
                    c3 = vtx(rom, ind, 1, 2, 3, 1, 1, 1)

                    base_idx = len(model_data['vertices'])
                    model_data['vertices'].extend([v1, v2, v3])
                    model_data['vertex_colors'].extend([c1, c2, c3])
                    model_data['faces'].append([base_idx + 2, base_idx + 1, base_idx])

                    face += 3
                    ind += (36 - 7)

                # Quads
                for _ in range(num_quads):
                    ind += 7
                    qtype = char(rom, ind, 0)
                    transparent = bool(quads.get(qtype, 0))
                    label = "transp quad" if transparent else "quad"
                    print(f"   {face} {label} ({qtype})")

                    v1 = xyz(rom, ind, 33, 31, 29)
                    c1 = vtx(rom, ind, 1, 2, 3, 0, 0, 0)

                    v2 = xyz(rom, ind, 21, 19, 17)
                    c2 = vtx(rom, ind, -3, -2, -1, 0, 0, 0)

                    v3 = xyz(rom, ind, 23, 27, 25)
                    c3 = vtx(rom, ind, -3, -2, -1, 1, 1, 1)

                    v4 = xyz(rom, ind, 35, 39, 37)
                    c4 = vtx(rom, ind, 1, 2, 3, 1, 1, 1)

                    base_idx = len(model_data['vertices'])
                    model_data['vertices'].extend([v1, v2, v3, v4])
                    model_data['vertex_colors'].extend([c1, c2, c3, c4])
                    model_data['faces'].append([base_idx + 2, base_idx + 1, base_idx])
                    model_data['faces'].append([base_idx + 3, base_idx + 2, base_idx])

                    face += 4
                    ind += (44 - 7)
            eye += 2

        print(f"Exported from 0x{drwa_addr:X}: {face} faces, {base_idx} base index")
        return model_data

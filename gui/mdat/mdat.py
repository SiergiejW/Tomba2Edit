import struct
import io

from functions import psx_vram


def find_area_mdat_location(idx_path, chunk_index):
    """Scan one AREA's SDAT pointer table in TOMBA2.IDX for its MDAT
    (id 8) entry - same chunk layout idx_parser.parse_idx_file() reads,
    and the same scan gui.scld.scld_parser.find_area_scld_location does
    for id 7. Returns (dat_start, offset), or None if this area has no
    MDAT."""
    chunk_size = 0x800
    with open(idx_path, "rb") as idx:
        idx.seek(chunk_index * chunk_size)
        _, _, dat_start, dat_end, pointer_amount = struct.unpack("<5I", idx.read(20))
        raw = idx.read(pointer_amount * 4)
    pointers = struct.unpack(f"<{pointer_amount}I", raw)
    entries = [(v >> 24, v & 0xFFFFFF) for v in pointers]
    for id_, offset in entries:
        if id_ == 8:
            return dat_start, offset
    return None


def area_mdat_entries(idx_path, dat_path, chunk_index):
    """Every MDAT in one AREA, as [(file_index, dat_start, offset,
    size), ...].

    find_area_mdat_location() above stops at the first id 8, which is
    all a level room needs. This one finds them all, and uses the same
    test functions.format_detect uses - 0xFFFF at +4, which is the
    first word of the entry's drawmap - so
    an area's second MDAT is found too (AREA_1B keeps one at id 0x20,
    and it is the one that area's DRWB belongs to)."""
    chunk_size = 0x800
    with open(idx_path, "rb") as idx:
        idx.seek(chunk_index * chunk_size)
        _, _, dat_start, dat_end, pointer_amount = struct.unpack("<5I", idx.read(20))
        raw = idx.read(pointer_amount * 4)
    pointers = struct.unpack(f"<{pointer_amount}I", raw)
    entries = []
    with open(dat_path, "rb") as dat:
        for i, value in enumerate(pointers):
            id_, offset = value >> 24, value & 0xFFFFFF
            if id_ != 8 and id_ < 18:
                continue
            next_offset = (pointers[i + 1] & 0xFFFFFF if i + 1 < len(pointers)
                           else dat_end - dat_start)
            dat.seek(dat_start + offset + 4)
            head = dat.read(2)
            if len(head) < 2 or struct.unpack("<h", head)[0] != -1:
                continue
            entries.append((i, dat_start, offset, next_offset - offset))
    return entries


def _polygon(entry, kind, first_vertex, first_face, address, type_byte,
             tex_info, uvs, index, slot):
    """One record, as the viewer needs it to outline and describe it.

    `first_vertex` and `first_face` index the arrays being built, so a
    selection can be drawn without re-reading anything. The vertices are
    a ring - a quad's two triangles are (0,1,2) and (0,2,3) - which is
    what an outline is drawn along."""
    page, clut, transparent, blend = tex_info
    return {
        'index': index,
        'entry': entry['index'],
        'kind': kind,
        'slot': slot,           # this polygon's place among its entry's tris/quads
        'address': address,
        'type': type_byte,
        'first_vertex': first_vertex,
        'vertex_count': len(uvs),
        'first_face': first_face,
        'face_count': 1 if kind == 'tri' else 2,
        'page': page,
        'clut': clut,
        'transparent': transparent,
        'blend': blend,
        'texels': [(round(u * psx_vram.ATLAS_WIDTH - 0.5) % psx_vram.UV_WRAP,
                    round(v * psx_vram.ATLAS_HEIGHT - 0.5) % psx_vram.UV_WRAP)
                   for u, v in uvs],
    }


def exportMDAT(drwa_addr, datpath):
    base_idx = 0
    model_data = {
        'vertices': [],
        'vertex_colors': [],
        'faces': [],
        'texture_coords': [],
        'texture_info': [],  # (page, clut_address, is_transparent, blend_mode)
        'tri_count': 0,
        'quad_count': 0,
        # What the drawmap says, kept so a viewer can show the geometry
        # the way the file has it - one entry per cell that points
        # somewhere, one polygon per record inside it. See
        # gui/drwa/drwa_parser.py for the layout these come from.
        'drawmap': (0, 0),   # (rows, columns)
        'entries': [],
        'polygons': [],
    }

    triangles = {32: 0, 34: 0, 37: 0, 38: 0, 39: 0, 48: 0, 50: 1, 52: 0, 54: 1} #this is from PSX draw modes manual
    quads = {40: 0, 42: 0, 44: 0, 45: 0, 46: 0, 47: 0, 56: 0, 58: 1, 60: 0, 62: 1} #this is from PSX draw modes manual
    transparent = False

    #print(f"Now exporting MDAT from address 0x{drwa_addr:X}")

    def getClutCoords(num):
        return (int((bin(num)[2:].zfill(16))[10:], 2) << 4,
                int((bin(num)[2:].zfill(16))[1:10], 2))

    def clutCoords2Address(intuple):
        return (intuple[0] * 2 + intuple[1] * 0x800)

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

    def adjust_uv(raw_u, raw_v, page):
        # Where in the 4096x512 VRAM atlas this texel is - see
        # functions.psx_vram.atlas_uv, which aims at the middle of the
        # texel so a face whose vertices all share one UV keeps the one
        # colour it is meant to be instead of flickering between that
        # texel and its neighbour as the camera moves.
        return psx_vram.atlas_uv(raw_u, raw_v, page)


    with open(datpath, "rb") as rom:
        rom.seek(drwa_addr)
        amount_x, amount_y = struct.unpack("<hh", rom.read(4))
        drwa_size = amount_x * amount_y * 2
        drwa_data = rom.read(drwa_size)
        drwa = io.BytesIO(drwa_data)

        # The first word is the row count, so the grid's stride is the
        # second - see gui/drwa/drwa_parser.py.
        model_data['drawmap'] = (amount_x, amount_y)
        stride = amount_y or 1

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
                #print(f"0x{ind:X}: {num_tris} tris, {num_quads} quads")

                cell = eye // 2
                entry = {
                    'index': len(model_data['entries']),
                    'cell': cell, 'col': cell % stride, 'row': cell // stride,
                    'pointer': val, 'address': ind, 'offset': val * 4,
                    'tris': num_tris, 'quads': num_quads,
                    'size': 4 + num_tris * 36 + num_quads * 44,
                    'first_polygon': len(model_data['polygons']),
                    'polygon_count': 0,
                }
                model_data['entries'].append(entry)

                # Triangles
                for slot in range(num_tris):
                    ind += 7
                    ttype = char(rom, ind, 0)
                    transparent = bool(triangles.get(ttype, 0))
                    label = "transp triangle" if transparent else "triangle"
                    #print(f"   {face} {label} ({ttype})")

                    v1 = xyz(rom, ind, 17, 15, 13)
                    c1 = vtx(rom, ind, -3, -2, -1, 0, 0, 0)

                    v2 = xyz(rom, ind, 19, 23, 21)
                    c2 = vtx(rom, ind, 1, 2, 3, 0, 0, 0)

                    v3 = xyz(rom, ind, 29, 27, 25)
                    c3 = vtx(rom, ind, 1, 2, 3, 1, 1, 1)

                    # Masked to the five bits that are the page, as the
                    # quad path below already did. The rest of a PSX
                    # texpage attribute is the semi-transparency mode and
                    # the colour depth, and 273 triangles on the disc set
                    # them. This is tidying rather than a fix: an
                    # unmasked page lands off the bottom of the atlas and
                    # the texture wrap brings it back to the same texel,
                    # so the 3D view drew these correctly either way. It
                    # matters to anything that gets the UVs without the
                    # wrap - the GLTF export, for one.
                    texture_page = char(rom, ind, 11) & 0x1F
                    # Bits 5-6 of the same byte are the blend mode - see
                    # gui/smst/smst_parser.py on why they matter.
                    blend_mode = (char(rom, ind, 11) >> 5) & 3
                    clut_coords = getClutCoords(short(rom, ind, 7))
                    clut_address = clutCoords2Address(clut_coords)

                    # Store texture info
                    tex_info = (texture_page, clut_address, transparent, blend_mode)

                    # Get UV coordinates
                    uv1 = adjust_uv(char(rom, ind, 5), char(rom, ind, 6), texture_page)
                    uv2 = adjust_uv(char(rom, ind, 9), char(rom, ind, 10), texture_page)
                    uv3 = adjust_uv(char(rom, ind, 31), char(rom, ind, 32), texture_page)
                    #print(f"[DEBUG] Page {texture_page}: UV1 = {uv1} UV2 = {uv2} UV3 = {uv3}")

                    base_idx = len(model_data['vertices'])
                    model_data['polygons'].append(_polygon(
                        entry, 'tri', base_idx, len(model_data['faces']),
                        ind - 3, ttype, tex_info, [uv1, uv2, uv3],
                        len(model_data['polygons']), slot))
                    model_data['vertices'].extend([v1, v2, v3])
                    model_data['vertex_colors'].extend([c1, c2, c3])
                    model_data['faces'].append([base_idx + 2, base_idx + 1, base_idx])
                    model_data['texture_coords'].extend([uv1, uv2, uv3])
                    model_data['texture_info'].append(tex_info)
                    model_data['tri_count'] += 1

                    face += 3
                    ind += (36 - 7)

                # Quads
                for slot in range(num_quads):
                    ind += 7
                    qtype = char(rom, ind, 0)
                    transparent = bool(quads.get(qtype, 0))
                    label = "transp quad" if transparent else "quad"
                    #print(f"   {face} {label} ({qtype})")

                    v1 = xyz(rom, ind, 33, 31, 29)
                    c1 = vtx(rom, ind, 1, 2, 3, 0, 0, 0)

                    v2 = xyz(rom, ind, 21, 19, 17)
                    c2 = vtx(rom, ind, -3, -2, -1, 0, 0, 0)

                    v3 = xyz(rom, ind, 23, 27, 25)
                    c3 = vtx(rom, ind, -3, -2, -1, 1, 1, 1)

                    v4 = xyz(rom, ind, 35, 39, 37)
                    c4 = vtx(rom, ind, 1, 2, 3, 1, 1, 1)

                    # Get texture info
                    texture_page = char(rom, ind, 11) & 0x1F
                    # Bits 5-6 of the same byte are the blend mode - see
                    # gui/smst/smst_parser.py on why they matter.
                    blend_mode = (char(rom, ind, 11) >> 5) & 3
                    clut_coords = getClutCoords(short(rom, ind, 7))
                    clut_address = clutCoords2Address(clut_coords)

                    # Store texture info
                    tex_info = (texture_page, clut_address, transparent, blend_mode)

                    # Get UV coordinates
                    uv1 = adjust_uv(char(rom, ind, 13), char(rom, ind, 14), texture_page)
                    uv2 = adjust_uv(char(rom, ind, 5), char(rom, ind, 6), texture_page)
                    uv3 = adjust_uv(char(rom, ind, 9), char(rom, ind, 10), texture_page)
                    uv4 = adjust_uv(char(rom, ind, 15), char(rom, ind, 16), texture_page)
                    #print(f"[DEBUG] Page {texture_page}: UV1 = {uv1} UV2 = {uv2} UV3 = {uv3} UV4 = {uv4}")

                    base_idx = len(model_data['vertices'])
                    model_data['polygons'].append(_polygon(
                        entry, 'quad', base_idx, len(model_data['faces']),
                        ind - 3, qtype, tex_info, [uv1, uv2, uv3, uv4],
                        len(model_data['polygons']), slot))
                    model_data['vertices'].extend([v1, v2, v3, v4])
                    model_data['vertex_colors'].extend([c1, c2, c3, c4])
                    model_data['faces'].append([base_idx + 2, base_idx + 1, base_idx])
                    model_data['faces'].append([base_idx + 3, base_idx + 2, base_idx])
                    model_data['texture_coords'].extend([uv1, uv2, uv3, uv4])
                    model_data['texture_info'].append(tex_info)
                    model_data['texture_info'].append(tex_info)
                    model_data['quad_count'] += 1

                    face += 4
                    ind += (44 - 7)

                entry['polygon_count'] = (len(model_data['polygons'])
                                          - entry['first_polygon'])
            eye += 2

        #print(f"Exported from 0x{drwa_addr:X}: {face} faces, {base_idx} base index")
        return model_data

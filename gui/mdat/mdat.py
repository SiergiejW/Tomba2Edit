import struct


def exportMDAT(drwa_addr, datpath):
    """Extracts 3D model data from MDAT format without file operations"""

    # Data structures to hold the extracted model information
    model_data = {
        'vertices': [],
        'vertex_colors': [],
        'tex_coords': [],
        'faces': [],
        'materials': []
    }

    # Primitive type definitions
    triangles = {32: 0, 34: 0, 37: 0, 38: 0, 39: 0, 48: 0, 50: 1, 52: 0, 54: 1}
    quads = {40: 0, 42: 0, 44: 0, 45: 0, 46: 0, 47: 0, 56: 0, 58: 1, 60: 0, 62: 1}

    def read_short(ind, offset):
        rom.seek(ind + offset)
        return struct.unpack("<h", rom.read(2))[0]

    def read_byte(ind, offset):
        rom.seek(ind + offset)
        return struct.unpack("B", rom.read(1))[0]

    def read_vertex_color(ind, offset, nibble):
        rom.seek(ind + offset)
        byte_value = rom.read(1)[0]
        color_value = (byte_value >> (4 * (1 - nibble))) & 0xF  # Extract correct nibble
        return float(f"{color_value / 9:.6f}")

    def get_clut_address(clut_value):
        """Convert CLUT value to VRAM address"""
        x = (clut_value >> 6) & 0x3F  # Upper 6 bits become X (shifted left by 4 later)
        y = (clut_value >> 0) & 0x1FF  # Lower 9 bits become Y
        return (x << 4) + (y * 0x800)  # X*2 + Y*2048

    def process_triangle(ind, face_idx):
        # Vertex coordinates (x, y, z)
        v1 = (read_short(ind, 17), read_short(ind, 15), read_short(ind, 13))
        v2 = (read_short(ind, 19), read_short(ind, 23), read_short(ind, 21))
        v3 = (read_short(ind, 29), read_short(ind, 27), read_short(ind, 25))

        # Vertex colors (r, g, b)
        c1 = (
            read_vertex_color(ind, -3, 0),
            read_vertex_color(ind, -2, 0),
            read_vertex_color(ind, -1, 0)
        )
        c2 = (
            read_vertex_color(ind, 1, 0),
            read_vertex_color(ind, 2, 0),
            read_vertex_color(ind, 3, 0)
        )
        c3 = (
            read_vertex_color(ind, 1, 1),
            read_vertex_color(ind, 2, 1),
            read_vertex_color(ind, 3, 1)
        )

        # Texture coordinates (u, v)
        uv1 = (read_byte(ind, 5) / 256, read_byte(ind, 6) / 256)
        uv2 = (read_byte(ind, 9) / 256, read_byte(ind, 10) / 256)
        uv3 = (read_byte(ind, 31) / 256, read_byte(ind, 32) / 256)

        # Material info
        material_id = read_byte(ind, 11) & 0x1F
        clut_value = read_short(ind, 7)
        clut_address = get_clut_address(clut_value)

        # Add to model data
        base_idx = len(model_data['vertices'])
        model_data['vertices'].extend([v1, v2, v3])
        model_data['vertex_colors'].extend([c1, c2, c3])
        model_data['tex_coords'].extend([uv1, uv2, uv3])
        model_data['faces'].append((base_idx, base_idx + 1, base_idx + 2))
        model_data['materials'].append((material_id, clut_address))

        return 3  # Vertices added

    def process_quad(ind, face_idx):
        # Vertex coordinates (x, y, z)
        v1 = (read_short(ind, 33), read_short(ind, 31), read_short(ind, 29))
        v2 = (read_short(ind, 21), read_short(ind, 19), read_short(ind, 17))
        v3 = (read_short(ind, 23), read_short(ind, 27), read_short(ind, 25))
        v4 = (read_short(ind, 35), read_short(ind, 39), read_short(ind, 37))

        # Vertex colors (r, g, b)
        c1 = (
            read_vertex_color(ind, 1, 0),
            read_vertex_color(ind, 2, 0),
            read_vertex_color(ind, 3, 0)
        )
        c2 = (
            read_vertex_color(ind, -3, 0),
            read_vertex_color(ind, -2, 0),
            read_vertex_color(ind, -1, 0)
        )
        c3 = (
            read_vertex_color(ind, -3, 1),
            read_vertex_color(ind, -2, 1),
            read_vertex_color(ind, -1, 1)
        )
        c4 = (
            read_vertex_color(ind, 1, 1),
            read_vertex_color(ind, 2, 1),
            read_vertex_color(ind, 3, 1)
        )

        # Texture coordinates (u, v)
        uv1 = (read_byte(ind, 13) / 256, read_byte(ind, 14) / 256)
        uv2 = (read_byte(ind, 5) / 256, read_byte(ind, 6) / 256)
        uv3 = (read_byte(ind, 9) / 256, read_byte(ind, 10) / 256)
        uv4 = (read_byte(ind, 15) / 256, read_byte(ind, 16) / 256)

        # Material info
        material_id = read_byte(ind, 11) & 0x1F
        clut_value = read_short(ind, 7)
        clut_address = get_clut_address(clut_value)

        # Add to model data
        base_idx = len(model_data['vertices'])
        model_data['vertices'].extend([v1, v2, v3, v4])
        model_data['vertex_colors'].extend([c1, c2, c3, c4])
        model_data['tex_coords'].extend([uv1, uv2, uv3, uv4])
        model_data['faces'].append((base_idx, base_idx + 1, base_idx + 2, base_idx + 3))
        model_data['materials'].append((material_id, clut_address))

        return 4  # Vertices added

    with open(datpath, "rb") as rom:
        # Read DRWA section header
        rom.seek(drwa_addr)
        tri_count, quad_count = struct.unpack("<hh", rom.read(4))
        drwa_size = (tri_count + quad_count) * 2
        drwa_data = rom.read(drwa_size)

        face_idx = 0
        for i in range(0, len(drwa_data), 2):
            # Read and convert 16-bit pointer (little-endian)
            pointer_bytes = drwa_data[i:i + 2]
            pointer_value = int.from_bytes(pointer_bytes, byteorder='little')

            if 0 < pointer_value < 65535:
                # Calculate absolute address of primitive block
                primitive_block_addr = drwa_addr + 4 + (pointer_value * 4)

                rom.seek(primitive_block_addr)
                tri_count, quad_count = struct.unpack("<hh", rom.read(4))

                # Process triangles
                for _ in range(tri_count):
                    primitive_block_addr += 7  # Skip header
                    face_idx += process_triangle(primitive_block_addr, face_idx)
                    primitive_block_addr += 36 - 7  # Skip to next primitive

                # Process quads
                for _ in range(quad_count):
                    primitive_block_addr += 7  # Skip header
                    face_idx += process_quad(primitive_block_addr, face_idx)
                    primitive_block_addr += 44 - 7  # Skip to next primitive
    print(model_data)
    return model_data
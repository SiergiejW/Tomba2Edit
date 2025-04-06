import struct
import numpy as np


def exportMDAT(drwa_addr, datpath):
    """
    Extract 3D model data from MDAT file
    Returns dictionary with:
    - vertices: list of (x,y,z) coordinates
    - vertex_colors: list of (r,g,b) colors
    - faces: list of vertex indices forming faces
    """
    print("■ MDAT.py module is active")
    print(f"Extracting model data from address 0x{drwa_addr:X} in file {datpath}")

    model_data = {
        'vertices': [],
        'vertex_colors': [],
        'faces': []
    }

    with open(datpath, "rb") as rom:
        # Read the DRWA header
        rom.seek(drwa_addr)
        amount = struct.unpack("<hh", rom.read(4))
        drwa_size = amount[0] * amount[1] * 2
        print(f"DRWA section size: {drwa_size} bytes ({amount[0]}x{amount[1]} entries)")

        # Process each DRWA entry
        for i in range(int(drwa_size / 2)):
            rom.seek(drwa_addr + i * 2)
            foo = rom.read(2)
            foo2 = int("%s%0s" % (bytearray(foo).hex()[2:], bytearray(foo).hex()[:2]), 16)
            ind = drwa_addr + (foo2 * 4)

            if 0 < foo2 < 65535:
                print(f"\nProcessing entry {i + 1}/{int(drwa_size / 2)} at offset 0x{ind:X}")
                rom.seek(ind)
                amount = struct.unpack("<hh", rom.read(4))
                triangle_count = amount[0]
                quad_count = amount[1]
                print(f"Found {triangle_count} triangles and {quad_count} quads")

                # Process triangles
                for n in range(triangle_count):
                    ind += 7  # skip header
                    print(f"  Processing triangle {n + 1}/{triangle_count} at offset 0x{ind:X}")

                    # Get vertex positions
                    v1 = (
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0]
                    )
                    v2 = (
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0]
                    )
                    v3 = (
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0]
                    )

                    # Get vertex colors
                    color_data = rom.read(6)
                    c1 = (
                        color_data[0] / 255.0,
                        color_data[1] / 255.0,
                        color_data[2] / 255.0
                    )
                    c2 = (
                        color_data[3] / 255.0,
                        color_data[4] / 255.0,
                        color_data[5] / 255.0
                    )
                    c3 = (1.0, 1.0, 1.0)  # Default white for third vertex

                    # Add to model data
                    base_idx = len(model_data['vertices'])
                    model_data['vertices'].extend([v1, v2, v3])
                    model_data['vertex_colors'].extend([c1, c2, c3])
                    model_data['faces'].append([base_idx, base_idx + 1, base_idx + 2])

                    ind += 36 - 7  # skip remaining data

                # Process quads (convert to two triangles)
                for n in range(quad_count):
                    ind += 7  # skip header
                    print(f"  Processing quad {n + 1}/{quad_count} at offset 0x{ind:X}")

                    # Get vertex positions
                    v1 = (
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0]
                    )
                    v2 = (
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0]
                    )
                    v3 = (
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0]
                    )
                    v4 = (
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0],
                        struct.unpack("<h", rom.read(2))[0]
                    )

                    # Get vertex colors
                    color_data = rom.read(8)
                    c1 = (
                        color_data[0] / 255.0,
                        color_data[1] / 255.0,
                        color_data[2] / 255.0
                    )
                    c2 = (
                        color_data[3] / 255.0,
                        color_data[4] / 255.0,
                        color_data[5] / 255.0
                    )
                    c3 = (1.0, 1.0, 1.0)  # Default white for third vertex
                    c4 = (1.0, 1.0, 1.0)  # Default white for fourth vertex

                    # Add to model data (split quad into two triangles)
                    base_idx = len(model_data['vertices'])
                    model_data['vertices'].extend([v1, v2, v3, v4])
                    model_data['vertex_colors'].extend([c1, c2, c3, c4])
                    model_data['faces'].append([base_idx, base_idx + 1, base_idx + 2])  # First triangle
                    model_data['faces'].append([base_idx, base_idx + 2, base_idx + 3])  # Second triangle

                    ind += 44 - 7  # skip remaining data

    print("\nExtraction complete!")
    print(f"  Total vertices: {len(model_data['vertices'])}")
    print(f"  Total faces: {len(model_data['faces'])}")
    return model_data
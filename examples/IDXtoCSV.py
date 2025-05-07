import struct
import csv

CHUNK_SIZE = 0x800
HEADER_SIZE = 0x14  # 20 bytes
POINTERS_SIZE = 0xEC  # 236 bytes, 59 * 4
TRAILER_SIZE = 0x700  # 1792 bytes, 448 * 4

# Hardcoded paths
IDX_PATH = "TOMBA2.IDX"
CSV_PATH = "out.csv"


def parse_idx_to_csv(idx_path, csv_path):
    with open(idx_path, 'rb') as idx_file, open(csv_path, 'w', newline='') as csv_file:
        writer = csv.writer(csv_file)

        # Prepare header
        header = [
            'img_start', 'img_end', 'dat_start', 'dat_end', 'pointer_amount'
        ]
        # Add 59 pointer columns
        header += [f'pointer_{i}_id' for i in range(59)]
        header += [f'pointer_{i}_offset' for i in range(59)]
        # Add 448 trailer columns
        header += [f'trailer_{i}' for i in range(448)]

        writer.writerow(header)

        idx_file.seek(0, 2)
        total_size = idx_file.tell()
        chunk_count = total_size // CHUNK_SIZE
        idx_file.seek(0)

        for _ in range(chunk_count):
            # Read header
            img_start, img_end, dat_start, dat_end, pointer_amount = struct.unpack('<5I', idx_file.read(HEADER_SIZE))

            # Read pointers (max 59 entries)
            pointers_raw = struct.unpack('<59I', idx_file.read(POINTERS_SIZE))
            pointers = [(p >> 24, p & 0x00FFFFFF) for p in pointers_raw]

            # Read trailer
            trailer_data = struct.unpack('<448I', idx_file.read(TRAILER_SIZE))

            # Build row
            row = [
                f"{img_start:08X}", f"{img_end:08X}", f"{dat_start:08X}", f"{dat_end:08X}", pointer_amount
            ]

            # Add pointer ID and offset separately
            for id_, offset in pointers:
                row.append(id_)
            for id_, offset in pointers:
                row.append(f"{offset:06X}")

            # Add trailer
            for t in trailer_data:
                row.append(f"{t:08X}")

            writer.writerow(row)


if __name__ == '__main__':
    parse_idx_to_csv(IDX_PATH, CSV_PATH)
    print(f"✅ Successfully parsed {IDX_PATH} to {CSV_PATH}")
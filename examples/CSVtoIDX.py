import struct
import csv
import os

CHUNK_SIZE = 0x800
HEADER_SIZE = 0x14
POINTERS_SIZE = 0xEC
TRAILER_SIZE = 0x700

CSV_PATH = "out.csv"
NEW_IDX_PATH = "../TOMBA2.IDX"
ORIGINAL_IDX_PATH = "TOMBA2.IDX"  # for size comparison

def rebuild_idx_from_csv(csv_path, new_idx_path, original_idx_path):
    with open(csv_path, 'r', newline='') as csv_file, open(new_idx_path, 'wb') as idx_file:
        reader = csv.reader(csv_file)
        headers = next(reader)  # Skip header row

        for row in reader:
            img_start = int(row[0], 16)
            img_end = int(row[1], 16)
            dat_start = int(row[2], 16)
            dat_end = int(row[3], 16)
            pointer_amount = int(row[4])

            pointers = []
            for i in range(59):
                id_ = int(row[5 + i])
                offset_hex = row[5 + 59 + i]
                offset = int(offset_hex, 16)
                combined = (id_ << 24) | offset
                pointers.append(combined)

            trailer = []
            for i in range(448):
                trailer_hex = row[5 + 118 + i]
                trailer.append(int(trailer_hex, 16))

            # Write header
            idx_file.write(struct.pack('<5I', img_start, img_end, dat_start, dat_end, pointer_amount))

            # Write pointers
            idx_file.write(struct.pack('<59I', *pointers))

            # Write trailer
            idx_file.write(struct.pack('<448I', *trailer))

    # Compare sizes
    original_size = os.path.getsize(original_idx_path)
    new_size = os.path.getsize(new_idx_path)

    print(f"✅ Rebuilt IDX written to {new_idx_path}")
    print(f"Original IDX size: {original_size} bytes")
    print(f"New IDX size: {new_size} bytes")

    if original_size != new_size:
        print("⚠️ Warning: Size mismatch between original and rebuilt IDX!")
    else:
        print("🎯 Sizes match exactly!")

if __name__ == '__main__':
    rebuild_idx_from_csv(CSV_PATH, NEW_IDX_PATH, ORIGINAL_IDX_PATH)

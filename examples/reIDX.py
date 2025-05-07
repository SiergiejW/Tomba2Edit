import struct
import re
import os

def parse_out_txt(path):
    with open(path, 'r') as f:
        lines = f.readlines()

    chunks = []
    for line in lines:
        if line.startswith('img_strt') or line.strip() == '' or line.startswith('='):
            continue  # Skip header

        header_match = re.match(r"([0-9A-F]+) ([0-9A-F]+) ([0-9A-F]+) ([0-9A-F]+)\s+([0-9A-F]+)\s+=\s+(.*)", line)
        if not header_match:
            continue

        img_start = int(header_match.group(1), 16)
        img_end = int(header_match.group(2), 16)
        dat_start = int(header_match.group(3), 16)
        dat_end = int(header_match.group(4), 16)
        pointer_amount = int(header_match.group(5), 10)
        rest = header_match.group(6)

        pointer_trail_split = rest.split('  ', 1)
        pointers_str = pointer_trail_split[0].strip()
        trail_str = pointer_trail_split[1].strip() if len(pointer_trail_split) > 1 else ''

        pointer_entries = []
        if pointers_str:
            pointer_tokens = pointers_str.split()
            for i in range(0, len(pointer_tokens), 2):
                id_hex = int(pointer_tokens[i], 10)
                addr_hex = int(pointer_tokens[i+1], 16)
                pointer_entries.append((id_hex, addr_hex))

        trail_entries = []
        if trail_str:
            trail_tokens = trail_str.split()
            for t in trail_tokens:
                trail_entries.append(int(t, 16))

        chunks.append({
            'img_start': img_start,
            'img_end': img_end,
            'dat_start': dat_start,
            'dat_end': dat_end,
            'pointer_amount': pointer_amount,
            'pointers': pointer_entries[:pointer_amount],  # Only use first pointer_amount entries!
            'trail': trail_entries,
        })

    return chunks

def build_idx_file(chunks, output_path):
    CHUNK_SIZE = 0x800
    TRAILER_SIZE = 0x700
    HEADER_SIZE = CHUNK_SIZE - TRAILER_SIZE

    with open(output_path, 'wb') as f:
        for chunk in chunks:
            # Enforce maximum of 59 pointers to prevent header overflow
            max_pointers = 59
            pointer_amount = min(chunk['pointer_amount'], max_pointers)
            pointers = chunk['pointers'][:max_pointers]

            f.write(struct.pack('<5I', chunk['img_start'], chunk['img_end'], chunk['dat_start'], chunk['dat_end'], pointer_amount))
            for (id_val, addr_val) in pointers:
                combined = (id_val << 24) | addr_val
                f.write(struct.pack('<I', combined))

            header_written = 20 + (len(pointers) * 4)
            f.write(b'\x00' * (HEADER_SIZE - header_written))

            trailer_to_write = chunk['trail']
            if len(trailer_to_write) < (TRAILER_SIZE // 4):
                trailer_to_write += [0] * ((TRAILER_SIZE // 4) - len(trailer_to_write))
            elif len(trailer_to_write) > (TRAILER_SIZE // 4):
                trailer_to_write = trailer_to_write[:(TRAILER_SIZE // 4)]  # Truncate if necessary
            f.write(struct.pack(f'<{len(trailer_to_write)}I', *trailer_to_write))

def compare_idx_files(file1, file2):
    with open(file1, 'rb') as f1, open(file2, 'rb') as f2:
        data1 = f1.read()
        data2 = f2.read()

    if data1 == data2:
        print("✅ Rebuilt IDX matches the original!")
    else:
        print("❌ Rebuilt IDX does NOT match the original!")
        min_len = min(len(data1), len(data2))
        for i in range(min_len):
            if data1[i] != data2[i]:
                print(f"First difference at offset {i:08X}: {data1[i]:02X} != {data2[i]:02X}")
                break
        print(f"Original size: {len(data1)}, Rebuilt size: {len(data2)}")

if __name__ == "__main__":
    out_txt_path = "out.txt"
    output_idx_path = "TOMBA2_rebuilt.IDX"
    original_idx_path = "TOMBA2.IDX"

    chunks = parse_out_txt(out_txt_path)
    build_idx_file(chunks, output_idx_path)

    print("✅ TOMBA2.IDX rebuilt successfully!")

    if os.path.exists(original_idx_path):
        compare_idx_files(original_idx_path, output_idx_path)
    else:
        print("⚠️ Original IDX not found for comparison.")
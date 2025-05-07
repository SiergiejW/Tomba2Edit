import os
import struct
import re

CHUNK_SIZE = 0x800
TRAILER_SIZE = 0x700
HEADER_SIZE = CHUNK_SIZE - TRAILER_SIZE


def debug_print(stage, message):
    print(f"[DEBUG] {stage}: {message}")


def parse_idx(idx_path):
    chunks = []
    debug_print("parse_idx", f"Parsing IDX file: {idx_path}")
    with open(idx_path, 'rb') as f:
        idx_size = os.path.getsize(idx_path)
        chunk_count = idx_size // CHUNK_SIZE
        debug_print("parse_idx", f"Found {chunk_count} chunks in IDX file")
        for chunk_idx in range(chunk_count):
            f.seek(chunk_idx * CHUNK_SIZE)
            img_start, img_end, dat_start, dat_end, pointer_amount = struct.unpack('<5I', f.read(20))
            pointers = list(struct.unpack(f'<{pointer_amount}I', f.read(pointer_amount * 4)))
            f.seek(chunk_idx * CHUNK_SIZE + HEADER_SIZE)
            trail_data = list(struct.unpack(f'<{TRAILER_SIZE // 4}I', f.read(TRAILER_SIZE)))

            sdat_pointers = [(p >> 24, p & 0x00FFFFFF) for p in pointers]
            trail_ranges = [(trail_data[i], trail_data[i + 1]) for i in range(0, len(trail_data), 2) if trail_data[i] != 0]

            chunks.append({
                'img_start': img_start,
                'img_end': img_end,
                'dat_start': dat_start,
                'dat_end': dat_end,
                'sdat_pointers': sdat_pointers,
                'trail_ranges': trail_ranges,
            })
    return chunks


def repack_single_file(original_dat, modified_file_path, chunks, output_dat_path):
    debug_print("repack_single_file", f"Starting repack of {modified_file_path} into {output_dat_path}")

    filename = os.path.basename(modified_file_path)
    match_file = re.match(r'AREA_(\w+)_FILE_(\w+)_ID_(\w+)_OFFSET_(\w+)\.bin', filename)

    if not match_file:
        raise ValueError("Filename format not recognized for replacement.")

    area_hex, file_idx_hex, id_hex, offset_hex = match_file.groups()
    area = int(area_hex, 16)
    file_idx = int(file_idx_hex, 16)
    offset = int(offset_hex, 16)

    with open(original_dat, 'rb') as f_in:
        original_data = f_in.read()

    with open(modified_file_path, 'rb') as f_mod:
        new_data = f_mod.read()

    original_dat_size = len(original_data)
    new_file_size = len(new_data)

    debug_print("sizes", f"Original DAT size: {original_dat_size}, New file size: {new_file_size}")

    chunk = chunks[area]
    id_val, old_relative_offset = chunk['sdat_pointers'][file_idx]

    next_relative_offset = chunk['dat_end'] - chunk['dat_start']
    if file_idx + 1 < len(chunk['sdat_pointers']):
        next_relative_offset = chunk['sdat_pointers'][file_idx + 1][1]

    old_file_absolute_start = chunk['dat_start'] + old_relative_offset
    old_file_absolute_end = chunk['dat_start'] + next_relative_offset

    debug_print("file_offsets", f"Replacing bytes from {old_file_absolute_start:08X} to {old_file_absolute_end:08X}")

    # Build new DAT content
    new_dat = bytearray()
    new_dat += original_data[:old_file_absolute_start]
    new_dat += new_data
    new_dat += original_data[old_file_absolute_end:]

    # Update pointers if needed
    size_diff = new_file_size - (old_file_absolute_end - old_file_absolute_start)

    if size_diff != 0:
        debug_print("adjust_pointers", f"Adjusting pointers by {size_diff} bytes")
        for c in chunks:
            if c['dat_start'] > old_file_absolute_end:
                c['dat_start'] += size_diff
                c['dat_end'] += size_diff
            elif c['dat_start'] <= old_file_absolute_end and c['dat_end'] > old_file_absolute_end:
                c['dat_end'] += size_diff

        # Update SDAT pointers inside the same chunk if after modified one
        for i in range(file_idx + 1, len(chunk['sdat_pointers'])):
            id_temp, ptr = chunk['sdat_pointers'][i]
            chunk['sdat_pointers'][i] = (id_temp, ptr + size_diff)

        # Update trail data globally
        for c in chunks:
            new_trails = []
            for start, end in c['trail_ranges']:
                if start >= old_file_absolute_end:
                    start += size_diff
                if end >= old_file_absolute_end:
                    end += size_diff
                new_trails.append((start, end))
            c['trail_ranges'] = new_trails

    with open(output_dat_path, 'wb') as f_out:
        f_out.write(new_dat)

    # Final size check
    new_dat_size = os.path.getsize(output_dat_path)
    debug_print("final_sizes", f"New DAT size: {new_dat_size}")

    if new_file_size == (old_file_absolute_end - old_file_absolute_start) and new_dat_size != original_dat_size:
        print("[WARNING] File sizes match but DAT sizes differ! Possible repacking bug.")

    debug_print("done", "New DAT repacked successfully!")


def write_new_idx(chunks, output_idx_path):
    debug_print("write_new_idx", f"Writing new IDX file to {output_idx_path}")
    with open(output_idx_path, 'wb') as f:
        for chunk in chunks:
            header = struct.pack('<5I', chunk['img_start'], chunk['img_end'], chunk['dat_start'], chunk['dat_end'], len(chunk['sdat_pointers']))
            f.write(header)

            for id, ptr in chunk['sdat_pointers']:
                f.write(struct.pack('<I', (id << 24) | ptr))

            f.write(b'\x00' * (HEADER_SIZE - len(chunk['sdat_pointers']) * 4 - 20))

            trail_flat = [val for rng in chunk['trail_ranges'] for val in rng]
            trail_flat += [0] * ((TRAILER_SIZE // 4) - len(trail_flat))
            f.write(struct.pack(f'<{len(trail_flat)}I', *trail_flat))




if __name__ == "__main__":
    original_idx = "C:/Users/Patryk/PycharmProjects/Tomba/iso/retail-us/CD/TOMBA2.IDX"
    original_dat = "C:/Users/Patryk/PycharmProjects/Tomba/iso/retail-us/CD/TOMBA2.DAT"
    modified_file = "C:/Users/Patryk/PycharmProjects/Tomba310/repacker/AREA_04_FILE_08_ID_D_OFFSET_00078A08.bin"

    output_idx = "C:/Users/Patryk/PycharmProjects/Tomba310/repacker/insert/TOMBA2.IDX"
    output_dat = "C:/Users/Patryk/PycharmProjects/Tomba310/repacker/insert/TOMBA2.DAT"

    chunks = parse_idx(original_idx)
    repack_single_file(original_dat, modified_file, chunks, output_dat)
    write_new_idx(chunks, output_idx)

    print("✅ Single file repacking completed!")
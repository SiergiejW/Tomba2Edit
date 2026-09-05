import os
import struct
import re

CHUNK_SIZE = 0x800
TRAILER_SIZE = 0x700
HEADER_SIZE = CHUNK_SIZE - TRAILER_SIZE
CD_SECTOR_SIZE = 0x800  # PSX CD-ROM sector size - DAT is read in whole
                        # sectors, so its size (and every chunk boundary
                        # within it) must stay a multiple of this.


def pad_to_sector(data):
    """Zero-pad `data` (bytes-like) up to the next whole CD_SECTOR_SIZE
    boundary. A no-op if already aligned."""
    pad = (-len(data)) % CD_SECTOR_SIZE
    if pad:
        data = bytes(data) + b'\x00' * pad
    return data


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


def _apply_single_replacement(dat_bytes, chunks, area, file_idx, new_data):
    """
    Replace one SDAT file's bytes within `dat_bytes` (bytes-like) and
    update `chunks` (as produced by parse_idx(), MUTATED IN PLACE) so
    every dat_start/dat_end/relative-pointer/trail-range stays
    internally consistent - including when the replaced region exactly
    touches a neighboring chunk's boundary.

    Any address at or after the end of the replaced region shifts by
    the size difference; DAT areas are packed back-to-back, so it's
    common for a neighboring chunk's dat_start to land exactly on that
    boundary.

    Every chunk's dat_start must stay a multiple of CD_SECTOR_SIZE. The
    edited chunk's own dat_end is padded up to the next sector boundary
    (later files within the same chunk only shift by the raw size
    difference - only whole-AREA boundaries need sector alignment), and
    that combined shift cascades to every later chunk's
    dat_start/dat_end and trail range, keeping them aligned by
    induction.

    Returns the new DAT bytes (bytearray).
    """
    chunk = chunks[area]
    id_val, old_relative_offset = chunk['sdat_pointers'][file_idx]

    next_relative_offset = chunk['dat_end'] - chunk['dat_start']
    if file_idx + 1 < len(chunk['sdat_pointers']):
        next_relative_offset = chunk['sdat_pointers'][file_idx + 1][1]

    old_file_absolute_start = chunk['dat_start'] + old_relative_offset
    old_file_absolute_end = chunk['dat_start'] + next_relative_offset

    old_size = old_file_absolute_end - old_file_absolute_start
    size_diff = len(new_data) - old_size

    debug_print(
        "replace",
        "AREA {:02X} FILE {:02X}: {:08X}-{:08X} ({} bytes) -> {} bytes (diff {:+d})".format(
            area, file_idx, old_file_absolute_start, old_file_absolute_end, old_size, len(new_data), size_diff
        )
    )

    new_dat = bytearray()
    new_dat += dat_bytes[:old_file_absolute_start]
    new_dat += new_data
    new_dat += dat_bytes[old_file_absolute_end:]

    # Later pointers within THIS chunk are relative to its own dat_start
    # and always come after the resized file, so they shift by the raw
    # content size difference only - files within a chunk don't need
    # sector alignment between each other, only the chunk's own overall
    # boundary does.
    if size_diff != 0:
        for i in range(file_idx + 1, len(chunk['sdat_pointers'])):
            id_temp, ptr = chunk['sdat_pointers'][i]
            chunk['sdat_pointers'][i] = (id_temp, ptr + size_diff)

    # Pad this chunk's own end up to the next whole CD sector, and fold
    # that padding into the shift applied to everything after it.
    new_chunk_end_unpadded = chunk['dat_end'] + size_diff
    extra_pad = (-new_chunk_end_unpadded) % CD_SECTOR_SIZE
    total_shift = size_diff + extra_pad

    if extra_pad:
        insert_pos = new_chunk_end_unpadded
        new_dat = new_dat[:insert_pos] + b'\x00' * extra_pad + new_dat[insert_pos:]
        debug_print(
            "align",
            "AREA {:02X}: padded chunk end by {} byte(s) to stay sector-aligned "
            "(new chunk end {:#x})".format(area, extra_pad, new_chunk_end_unpadded + extra_pad)
        )

    if total_shift != 0:
        def remap(x):
            return x + total_shift if x >= old_file_absolute_end else x

        for c in chunks:
            c['dat_start'] = remap(c['dat_start'])
            c['dat_end'] = remap(c['dat_end'])

        for c in chunks:
            c['trail_ranges'] = [(remap(s), remap(e)) for s, e in c['trail_ranges']]

    return new_dat


def _apply_trail_replacement(dat_bytes, chunks, address, new_data):
    """Replace one TRAIL file's bytes and keep every trailer consistent.

    The trail is the part of the DAT past the last AREA chunk - on the
    retail disc, 0x82F000 to the end - and its files are addressed
    absolutely rather than by a slot in a chunk's pointer table, which
    is why they need their own path through here. Two things follow
    from that and make this the simpler of the two:

    Nothing in front moves. Every chunk sits below the trail, so no
    dat_start, dat_end or SDAT pointer changes; only the trail ranges
    at or after the replaced file shift.

    A trail file is SHARED. Thirteen of the retail disc's 53 are listed
    by more than one area - that is what the trail is for - so the same
    range turns up in several chunks' trailers and every copy of it has
    to move together. Replacing one changes it for every area that uses
    it, which is the point rather than a hazard, but it is worth
    knowing before doing it.

    Trail files start on a sector boundary, so the replacement is padded
    up to one and the shift applied to everything after it is the padded
    difference.

    Returns the new DAT bytes (bytearray).
    """
    starts = sorted({start for chunk in chunks
                     for start, _stop in chunk['trail_ranges']})
    if address not in starts:
        raise ValueError(f"no trail file starts at {address:#x}")
    end = None
    for chunk in chunks:
        for start, stop in chunk['trail_ranges']:
            if start == address:
                end = stop if end is None else max(end, stop)

    # A trail file's SLOT runs to wherever the next one begins, which is
    # further than the file itself: every trail file starts on a sector
    # boundary and none of them fills its last sector, so there is slack
    # behind each one. Replacing the file alone and padding that to a
    # sector would eat the slack and leave the next file unaligned, so
    # the whole slot is what gets replaced.
    later = [s for s in starts if s > address]
    slot_end = later[0] if later else len(dat_bytes)

    padded = pad_to_sector(new_data)
    size_diff = len(padded) - (slot_end - address)

    debug_print(
        "trail",
        "{:08X}-{:08X} in a slot to {:08X} -> {} bytes padded to {} "
        "(diff {:+d})".format(address, end, slot_end, len(new_data),
                              len(padded), size_diff))

    new_dat = bytearray(dat_bytes[:address]) + padded + dat_bytes[slot_end:]

    def remap(x):
        return x + size_diff if x >= slot_end else x

    for chunk in chunks:
        chunk['trail_ranges'] = [
            # The replaced file's own end is where its new bytes finish,
            # inside the padding; everything past the slot shifts.
            (remap(start),
             address + len(new_data) if start == address else remap(stop))
            for start, stop in chunk['trail_ranges']]
    return new_dat


def repack_files(original_dat_path, original_idx_path, edits, output_dat_path, output_idx_path):
    """
    Apply a batch of edits in one pass and write the resulting DAT + IDX.

    edits: list of {"area": int, "file_idx": int, "data": bytes} for a
    file in an AREA's chunk, or {"trail": int, "data": bytes} naming a
    TRAIL file by the DAT address it starts at.

    Safe to call with edits spanning multiple AREAs, or several edits
    within the same AREA - each edit's pointer/size math is computed
    against the cumulative state left by the edits applied before it.

    ORDER MATTERS, and is settled here rather than by the caller. A
    trail edit names its file by a DAT address, so anything that has
    already moved the trail would leave that address pointing at the
    wrong file; the chunk edits do move it, so the trail edits go first.
    Among themselves they run from the back of the DAT forwards, since
    replacing a trail file only shifts what is after it - so every
    address still to be used is still where the caller found it. The
    chunk edits then shift the whole trail as they always have (see
    _apply_single_replacement's remap).
    """
    chunks = parse_idx(original_idx_path)

    with open(original_dat_path, 'rb') as f:
        dat_bytes = bytearray(f.read())

    ordered = (sorted((e for e in edits if 'trail' in e),
                      key=lambda e: e['trail'], reverse=True)
               + [e for e in edits if 'trail' not in e])
    for edit in ordered:
        if 'trail' in edit:
            dat_bytes = _apply_trail_replacement(
                dat_bytes, chunks, edit['trail'], edit['data'])
        else:
            dat_bytes = _apply_single_replacement(
                dat_bytes, chunks, edit['area'], edit['file_idx'], edit['data']
            )

    with open(output_dat_path, 'wb') as f:
        f.write(pad_to_sector(dat_bytes))

    write_new_idx(chunks, output_idx_path)

    debug_print("repack_files", f"Wrote {output_dat_path} and {output_idx_path}")


def repack_single_file(original_dat, modified_file_path, chunks, output_dat_path):
    """
    Kept for backwards compatibility with the original filename-driven,
    one-file-at-a-time CLI flow (AREA_xx_FILE_xx_ID_x_OFFSET_xxxxxxxx.bin).
    New code (including the GUI's Export Files) should prefer
    repack_files(), which supports batching several edits into one
    consistent pass instead of re-reading/re-writing the whole DAT
    once per file.
    """
    filename = os.path.basename(modified_file_path)
    match_file = re.match(r'AREA_(\w+)_FILE_(\w+)_ID_(\w+)_OFFSET_(\w+)\.bin', filename)

    if not match_file:
        raise ValueError("Filename format not recognized for replacement.")

    area_hex, file_idx_hex, id_hex, offset_hex = match_file.groups()
    area = int(area_hex, 16)
    file_idx = int(file_idx_hex, 16)

    with open(original_dat, 'rb') as f_in:
        dat_bytes = bytearray(f_in.read())
    with open(modified_file_path, 'rb') as f_mod:
        new_data = f_mod.read()

    original_dat_size = len(dat_bytes)

    new_dat = _apply_single_replacement(dat_bytes, chunks, area, file_idx, new_data)

    with open(output_dat_path, 'wb') as f_out:
        f_out.write(pad_to_sector(new_dat))

    new_dat_size = os.path.getsize(output_dat_path)
    debug_print("final_sizes", f"Original DAT size: {original_dat_size}, New DAT size: {new_dat_size}")
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

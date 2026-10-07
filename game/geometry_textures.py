"""Prepare foreign geometry textures without changing any project files."""
from pathlib import Path
from tempfile import TemporaryDirectory
import struct

from formats.models.obj_exchange import ExchangeError, records


def prepare(cd, entry, edits, source, source_vram, *, part=None, overlay=None, kept=None, state=None,
            progress=None, cancelled=None):
    """`kept` is an SMST-shaped run of the faces that stay on the target's
    own materials: what they read is not free, replaced file or not.
    `state` is a savestate taken in the area; with it, VRAM that no chunk
    writes and the state shows empty is offered too."""
    from formats.archive import repacker
    from formats.images import img_writer, img_codec
    from formats.models.smst_parser import parse_smst
    from psx import vram_map
    from psx.vram_preview import regions_from_polygons
    from psx.vram_viewer import decode_vram_bytes
    from game import level_textures

    if entry['kind'] != 'sdat':
        raise ExchangeError('New textures for shared TRAIL models are not supported here. Use existing target materials; their textures must work in every area sharing the model.')
    cd = Path(cd)
    idx, dat, img = (cd / ('TOMBA2.' + ext) for ext in ('IDX', 'DAT', 'IMG'))
    area = entry['area']
    def status(message):
        if cancelled and cancelled():
            raise ExchangeError('Import cancelled. The project was not changed.')
        if progress:
            progress(message)
    status('Checking texture space and existing model references…')
    with TemporaryDirectory(prefix='tomba-model-') as folder:
        folder = Path(folder)
        survey_idx, survey_dat = idx, dat
        if edits:
            survey_idx, survey_dat = folder / 'survey.IDX', folder / 'survey.DAT'
            repacker.repack_files(dat, idx, edits, survey_dat, survey_idx)
        chunk = repacker.parse_idx(survey_idx)[area]
        at = chunk['dat_start'] + chunk['sdat_pointers'][entry['file_idx']][1]
        original_idx, original_img = idx.read_bytes(), img.read_bytes()
        cache = {}
        def chunk_vram(number):
            if number not in cache:
                start, end = struct.unpack_from('<II', original_idx, number*2048)
                cache[number] = decode_vram_bytes(original_img[start:end]) if end > start else None
            return cache[number]
        seen = None
        if state:
            from psx import state_vram
            # Found in the file by the pixels of chunk 1, which is always loaded.
            seen = state_vram.read(state, chunk_vram(1))
        usage = level_textures.usage(survey_idx, survey_dat, img, area, at, overlay=overlay, state=seen)
        if part is not None:
            pointers = chunk['sdat_pointers']; slot = entry['file_idx']
            end = chunk['dat_start'] + pointers[slot+1][1] if slot+1 < len(pointers) else chunk['dat_end']
            with survey_dat.open('rb') as stream:
                stream.seek(at); bank = stream.read(end-at)
            polygons = [p for p in parse_smst(bank)['polygons'] if p['group'] != part]
            texture, palette = level_textures.cells(regions_from_polygons(polygons))
            usage.others |= texture | palette
        if kept:
            texture, palette = level_textures.cells(regions_from_polygons(parse_smst(kept)['polygons']))
            usage.others |= texture | palette
        loaded = vram_map.loaded_vram(vram_map.chunk_shards(idx, img), chunk_vram, area)
        target, shards, vram, report = level_textures.install(
            source, source_vram, usage.free, loaded, tries=12, cancelled=cancelled,
            progress=lambda attempt,placed,left: status(
                f'Packing textures: attempt {attempt+1}/12 — {placed} regions placed, {left} unresolved'))
        status('Compressing the new texture uploads…')
        start, end = struct.unpack_from('<II', original_idx, area*2048)
        updated = img_writer.paint(original_img[start:end], img_writer.merged(shards))
        status('Verifying the texture pixels and rebuilding the image archive…')
        import numpy as np
        expected = np.frombuffer(bytes(vram), '<u2').reshape(512,1024)
        decoded = np.frombuffer(decode_vram_bytes(updated), '<u2').reshape(512,1024)
        uploads = vram_map.claims_of({area: img_codec.read_chunk_header(updated)[0]}, (area,))
        if not np.array_equal(decoded[uploads], expected[uploads]):
            raise ExchangeError('Texture verification failed: the rebuilt IMG does not reproduce the prepared pixels. The project was not changed.')
        new_idx, new_img = folder / 'new.IDX', folder / 'new.IMG'
        img_writer.rebuild(idx, img, {area: updated}, new_idx, new_img)
        idx_bytes, img_bytes = new_idx.read_bytes(), new_img.read_bytes()
        problems = img_writer.check(idx_bytes, img_bytes)
        if problems:
            raise ExchangeError('\n'.join(problems))
        status('Texture verification complete.')
        return dict(library=records(target, 'SMST', 0), vram=bytes(vram),
                    idx=idx_bytes, img=img_bytes, original_idx=original_idx,
                    original_img=original_img, cd=cd, report=report)


def commit(prepared):
    """Reject stale preparation and restore the pair if a write fails."""
    idx, img = (prepared['cd'] / ('TOMBA2.' + ext) for ext in ('IDX', 'IMG'))
    if idx.read_bytes() != prepared['original_idx'] or img.read_bytes() != prepared['original_img']:
        raise ExchangeError('Project textures changed while importing. Reopen the import window and try again.')
    try:
        img.write_bytes(prepared['img'])
        idx.write_bytes(prepared['idx'])
    except OSError:
        img.write_bytes(prepared['original_img'])
        idx.write_bytes(prepared['original_idx'])
        raise

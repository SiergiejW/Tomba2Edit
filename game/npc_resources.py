"""Area-local NPC registrations and complete character resource imports.

The IDX entries carry model/pose bytes. Skeletons and native sequence graphs
live in the overlay extension, so their addresses survive later DAT repacks.
Preparation is read-only; commit installs the verified files as one bundle.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

from game import game_build, new_npc, pickup_edit
from psx import mips

TAG = b'T2NPC_MODEL_V1\0\0'
HEADER = struct.Struct('<16s4I')
BASE = game_build.US_OVERLAY_BASE
# Leave the game's stack and high runtime workspace above the area buffer.
AREA_LIMIT = 0x73000


class NpcResourceError(ValueError):
    pass


class DestinationStateRequired(NpcResourceError):
    pass


def _records(overlay):
    start = pickup_edit.EVENT_START - BASE
    end = min(len(overlay), pickup_edit.WINDOW_END - BASE)
    at = start
    while at < end:
        at = overlay.find(TAG, at, end)
        if at < 0:
            break
        if at + HEADER.size > end:
            raise NpcResourceError('Truncated NPC registration header.')
        _tag, size, json_size, bones_at, sequences_at = HEADER.unpack_from(overlay, at)
        if (size < HEADER.size + json_size + 1 or at + size > end
                or not HEADER.size + json_size <= bones_at <= sequences_at < size
                or overlay[at + size - 1] != 0xA5):
            raise NpcResourceError('Invalid NPC registration boundaries.')
        try:
            record = json.loads(overlay[at + HEADER.size:at + HEADER.size + json_size])
            if record.get('version') != 1:
                raise ValueError('Unknown registration version.')
            if (not isinstance(record.get('name'), str)
                    or not 1 <= record['parts'] <= 32
                    or not 0 <= record['file_id'] < 64
                    or not 0 <= record['anim_file'] < 64
                    or sequences_at - bones_at < record['parts'] * 8
                    or len(record['scale']) != 3
                    or any(not isinstance(v, int) or not 0 < v <= 0x7FFF for v in record['scale'])
                    or len(record['box']) != 4
                    or record['transform'] not in (0x80051844, 0x800518FC, 0x800517F8)):
                raise ValueError('Unsupported NPC model parameters.')
            record.update(address=BASE + at, size=size,
                          layout=BASE + at + bones_at,
                          anim_table=BASE + at + sequences_at)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise NpcResourceError('Invalid NPC registration metadata.') from exc
        yield record
        at += size


def registered_models(overlay):
    """The NPCs explicitly registered for this area's picker."""
    out = []
    for r in _records(overlay):
        try:
            out.append(new_npc.ModelChoice(
                r['name'], 0, r['file_id'], r['parts'], r['layout'],
                r['anim_file'], r['anim_table'], tuple(r['box']),
                scale=tuple(r['scale']), transform=r['transform']))
        except (KeyError, TypeError) as exc:
            raise NpcResourceError('Incomplete NPC registration.') from exc
    return out


def _fingerprint(recipe):
    h = hashlib.sha256()
    for data in (recipe.model_data, recipe.animation_data, recipe.bones_data):
        h.update(struct.pack('<I', len(data)))
        h.update(data)
    # Sequence pointers are relocated to the same synthetic address for identity.
    sequences = recipe.sequences.relocate(0x80155000)
    if isinstance(sequences, tuple):
        sequences = sequences[0]
    h.update(sequences)
    h.update(repr((tuple(recipe.scale), recipe.transform, tuple(recipe.box))).encode())
    return h.hexdigest()


def register_recipe(overlay, recipe, file_id, anim_file, name=None, resource_hashes=None):
    """Copy bones/sequence graph and persist one selectable area-local model."""
    if game_build.current().name != 'us-retail':
        raise NpcResourceError('NPC resource registration requires the US retail layout.')
    if not 1 <= recipe.parts <= 32 or len(recipe.bones_data) != recipe.parts * 8:
        raise NpcResourceError('A custom NPC needs a verified skeleton of 1–32 parts.')
    if (len(recipe.scale) != 3 or any(not 0 < v <= 0x7FFF for v in recipe.scale)
            or recipe.transform not in (0x80051844, 0x800518FC, 0x800517F8)
            or not 0 <= file_id < 64 or not 0 <= anim_file < 64):
        raise NpcResourceError('The NPC has unsupported scale, transform or resource parameters.')
    fingerprint = _fingerprint(recipe)
    for r in _records(overlay):
        if (r['fingerprint'], r['file_id'], r['anim_file']) == (fingerprint, file_id, anim_file):
            return bytes(overlay), next(m for m in registered_models(overlay)
                                        if m.layout == r['layout'])
    box = tuple(recipe.box)
    if len(box) != 4 or box[0] <= 0 or box[1] <= 0 or box[3] <= 0:
        box = new_npc.DEFAULT_BOX
    metadata = dict(version=1, name=name or recipe.name, fingerprint=fingerprint,
                    source_chunk=recipe.chunk, source_handler=recipe.handler,
                    file_id=file_id, anim_file=anim_file, parts=recipe.parts,
                    scale=list(recipe.scale), transform=recipe.transform,
                    box=list(box))
    if resource_hashes:
        metadata.update(resource_hashes)
    encoded = json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode('utf8')
    bones_at = (HEADER.size + len(encoded) + 3) & ~3
    sequences_at = (bones_at + len(recipe.bones_data) + 3) & ~3
    prototype = recipe.sequences.relocate(0x80155000)
    if isinstance(prototype, tuple):
        prototype = prototype[0]
    size = (sequences_at + len(prototype) + 1 + 7) & ~7
    from game import event_script
    out = bytearray(overlay)
    if len(out) > pickup_edit.WINDOW_END - BASE:
        raise NpcResourceError('The overlay extends past the verified extension window.')
    address = event_script._allocate(out, size, None, 0, None)
    sequences = recipe.sequences.relocate(address + sequences_at)
    if isinstance(sequences, tuple):
        sequences, table = sequences
        if table != address + sequences_at:
            raise NpcResourceError('The relocated sequence table must lead the sequence graph.')
    blob = bytearray(size)
    HEADER.pack_into(blob, 0, TAG, size, len(encoded), bones_at, sequences_at)
    blob[HEADER.size:HEADER.size + len(encoded)] = encoded
    blob[bones_at:bones_at + len(recipe.bones_data)] = recipe.bones_data
    blob[sequences_at:sequences_at + len(sequences)] = sequences
    blob[-1] = 0xA5  # Keeps later event allocations beyond zero-ended native data.
    at = address - BASE
    if len(out) < at + size:
        out.extend(bytes(at + size - len(out)))
    out[at:at + size] = blob
    choice = registered_models(out)[-1]
    return bytes(out), choice


def _referenced_slots(exe, overlay):
    """Conservatively retain literal file-table accesses in native code."""
    used = set(range(32)) | set(range(47, 64))
    for data, base in ((exe[0x800:], struct.unpack_from('<I', exe, 24)[0]),
                       (overlay, BASE)):
        regs = {}
        for at in range(0, len(data) - 3, 4):
            ins = mips.Instruction(base + at, struct.unpack_from('<I', data, at)[0])
            if ins.name in mips.LOADS or ins.name in mips.STORES:
                address = regs.get(ins.rs)
                if address is not None:
                    address = (address + ins.imm) & 0xFFFFFFFF
                    if new_npc.FILE_TABLE <= address < new_npc.FILE_TABLE + 64 * 4:
                        used.add((address - new_npc.FILE_TABLE) // 4)
            if ins.name == 'lui':
                regs[ins.rt] = ins.unsigned << 16
            elif ins.name in ('addiu', 'ori'):
                v = 0 if ins.rs == 0 else regs.get(ins.rs)
                if v is not None:
                    regs[ins.rt] = ((v + ins.imm) if ins.name == 'addiu'
                                    else v | ins.unsigned) & 0xFFFFFFFF
                else:
                    regs.pop(ins.rt, None)
            elif ins.name == 'addu':
                a, b = (0 if n == 0 else regs.get(n) for n in (ins.rs, ins.rt))
                if a is not None and b is not None:
                    regs[ins.rd] = (a + b) & 0xFFFFFFFF
                else:
                    regs.pop(ins.rd, None)
            elif ins.name in mips.LOADS:
                regs.pop(ins.rt, None)
            elif ins.name == 'jr':
                regs.clear()
    return used


def _entry_bytes(dat, chunk):
    entries = []
    pointers = chunk['sdat_pointers']
    for n, (file_id, offset) in enumerate(pointers):
        stop = pointers[n + 1][1] if n + 1 < len(pointers) else chunk['dat_end'] - chunk['dat_start']
        entries.append((file_id, dat[chunk['dat_start'] + offset:chunk['dat_start'] + stop]))
    return entries


def _copy_textures(model, source_chunk, destination, idx_path, img_path, state_path):
    from formats.images import img_writer
    from game import texture_migrate
    from psx import vram_map, state_vram
    from psx.vram_viewer import decode_vram_bytes
    idx, img = Path(idx_path).read_bytes(), Path(img_path).read_bytes()
    shards = vram_map.chunk_shards(idx_path, img_path)
    cache = {}

    def chunk_vram(number):
        if number not in cache:
            start, end = struct.unpack_from('<II', idx, number * 2048)
            cache[number] = decode_vram_bytes(img[start:end]) if end > start else None
        return cache[number]

    source = vram_map.loaded_vram(shards, chunk_vram, source_chunk)
    target = vram_map.loaded_vram(shards, chunk_vram, destination)
    keep_pages, keep_cluts = texture_migrate.already_there(model, source, target)
    pages, cluts = texture_migrate.survey(model)
    if set(pages) == keep_pages and set(cluts) == keep_cluts:
        return model, None, 'Textures and palettes already loaded in the destination.'
    if not state_path:
        raise DestinationStateRequired(
            'This character needs additional textures. Select a savestate from the destination area '
            'so the importer can reserve VRAM without overwriting runtime graphics.')
    seen = state_vram.read(state_path, chunk_vram(1))
    free = vram_map.free_for(shards, (destination,), state_vram.occupancy(seen))
    plan = texture_migrate.plan(model, free, keep_pages=keep_pages, keep_cluts=keep_cluts)
    moved = texture_migrate.retarget(model, plan)
    additions = texture_migrate.shards_for(plan, source)
    combined = texture_migrate.patch(target, additions)
    _checked, problems = texture_migrate.verify(model, moved, source, combined)
    if problems:
        raise NpcResourceError('The imported texture coordinates did not reproduce the source pixels.')
    start, end = struct.unpack_from('<II', idx, destination * 2048)
    updated = img_writer.paint(img[start:end], img_writer.merged(additions))
    return moved, updated, f'Copied {len(plan.moves)} texture regions and {len(plan.cluts)} palettes.'


@dataclass
class PreparedImport:
    files: dict
    originals: dict
    model: object
    report: str


def prepare_import(exe_path, overlay_path, dat_path, idx_path, img_path,
                   destination_chunk, recipe, state_path=None, name=None):
    from formats.archive import repacker
    from formats.images import img_writer
    game_build.use(str(exe_path))
    if game_build.current().name != 'us-retail' or not 4 <= destination_chunk < 26:
        raise NpcResourceError('NPC imports currently require a US retail gameplay area.')
    paths = [Path(p) for p in (dat_path, idx_path, img_path, overlay_path)]
    originals = {p: p.read_bytes() for p in paths}
    dat, idx, img, overlay = (originals[p] for p in paths)
    exe = Path(exe_path).read_bytes()
    if state_path:
        from psx.state_vram import _payload
        payload = _payload(state_path)
        base = struct.unpack_from('<I', exe, 24)[0]
        address = 0x8007A904
        probe = exe[0x800 + address - base:0x800 + address - base + 48]
        found = payload.find(probe)
        ram_at = found - (address - 0x80000000)
        if found < 0 or ram_at < 0 or payload[ram_at + 0xBF870] != destination_chunk - 4:
            raise NpcResourceError('Choose a US savestate taken in the destination area.')
    chunks = repacker.parse_idx_bytes(idx)
    entries = _entry_bytes(dat, chunks[destination_chunk])
    registrations = list(_records(overlay))
    fingerprint = _fingerprint(recipe)
    for registration in registrations:
        if registration['fingerprint'] != fingerprint:
            continue
        actual = dict(entries)
        hashes = (('model', registration['file_id']),
                  ('animation', registration['anim_file']))
        if all(key + '_hash' in registration and key + '_size' in registration
               and slot in actual and len(actual[slot]) >= registration[key + '_size']
               and hashlib.sha256(actual[slot][:registration[key + '_size']]).hexdigest()
               == registration[key + '_hash'] for key, slot in hashes):
            model = next(m for m in registered_models(overlay)
                         if m.layout == registration['layout'])
            return PreparedImport(dict(originals), originals, model,
                                  f'{model.name} is already registered in this area.')
    model_data, new_image_chunk, texture_report = _copy_textures(
        recipe.model_data, recipe.chunk, destination_chunk, idx_path, img_path, state_path)
    used = _referenced_slots(exe, overlay)
    for chunk in (0, 1, 2, destination_chunk):
        used.update(i for i, _offset in chunks[chunk]['sdat_pointers'])
    free_ids = [i for i in range(32, 47) if i not in used]
    added = []
    registered_ids = {r[key] for r in registrations for key in ('file_id', 'anim_file')}

    def resource(data, source_id, trail_index):
        for file_id, existing in entries + added:
            stable = (file_id in registered_ids or any(file_id == i for i, _d in added)
                      or (destination_chunk == recipe.chunk and trail_index is None
                          and file_id == source_id))
            if (stable and len(existing) >= len(data) and existing[:len(data)] == data
                    and not any(existing[len(data):])):
                return file_id
        if not free_ids:
            raise NpcResourceError('The area has no unused stable NPC resource slots (32–46).')
        file_id = free_ids.pop(0)
        added.append((file_id, data))
        return file_id

    model_id = resource(model_data, recipe.file_id, recipe.trail_index)
    anim_id = resource(recipe.animation_data, recipe.anim_file, recipe.animation_trail_index)
    new_dat, new_idx = repacker.append_sdat_files(dat, idx, destination_chunk, added)
    grown = repacker.parse_idx_bytes(new_idx)[destination_chunk]
    if grown['dat_end'] - grown['dat_start'] > AREA_LIMIT:
        raise NpcResourceError('The imported resources would exceed the 0x73000-byte area RAM buffer.')
    hashes = dict(model_hash=hashlib.sha256(model_data).hexdigest(), model_size=len(model_data),
                  animation_hash=hashlib.sha256(recipe.animation_data).hexdigest(),
                  animation_size=len(recipe.animation_data))
    new_overlay, model = register_recipe(overlay, recipe, model_id, anim_id, name, hashes)
    new_img = img
    if new_image_chunk is not None:
        with TemporaryDirectory(prefix='tomba-npc-img-') as folder:
            folder = Path(folder)
            a, b, c, e = (folder / n for n in ('in.IDX', 'in.IMG', 'out.IDX', 'out.IMG'))
            a.write_bytes(new_idx)
            b.write_bytes(img)
            img_writer.rebuild(a, b, {destination_chunk: new_image_chunk}, c, e)
            new_idx, new_img = c.read_bytes(), e.read_bytes()
        errors = img_writer.check(new_idx, new_img)
        if errors:
            raise NpcResourceError('\n'.join(errors))
    files = dict(zip(paths, (new_dat, new_idx, new_img, new_overlay)))
    return PreparedImport(files, originals, model,
                          f'{model.name}: model slot {model_id}, animation slot {anim_id}; '
                          f'{len(added)} new IDX entries. {texture_report}')


def commit(prepared):
    """Install a prepared bundle, refusing stale files and rolling back failures."""
    if any(p.read_bytes() != data for p, data in prepared.originals.items()):
        raise NpcResourceError('The project changed during NPC import. Prepare the import again.')
    staged, written = [], []
    try:
        for path, data in prepared.files.items():
            temporary = path.with_name(path.name + '.npc-writing')
            if temporary.exists():
                raise NpcResourceError(f'A previous NPC import is still staged beside {path.name}.')
            temporary.write_bytes(data)
            staged.append((path, temporary))
        for path, temporary in staged:
            os.replace(temporary, path)
            written.append(path)
    except Exception:
        for path in reversed(written):
            path.write_bytes(prepared.originals[path])
        raise
    finally:
        for _path, temporary in staged:
            if temporary.exists():
                temporary.unlink()

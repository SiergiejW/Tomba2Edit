"""Complete NPC resources captured from the game's own initializers.

The area's SDAT list is not a list of characters. Some characters load a
TRAIL model into a temporary slot, and their skeleton and animation
sequences live in MAIN.EXE or the area overlay. A recipe carries all four
pieces, without retaining the temporary slot's placeholder as its model.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

from formats.animation.anmp_parser import ANMPError, parse_anmp
from formats.animation.sequences import SequenceError, read_clip
from formats.models.smst_parser import smst_groups
from game import actor_models, actor_sim, game_build
from game.labels import content_key, load as load_labels
from psx.cpu import PASS
from psx.mips import Image


class RecipeError(ValueError):
    """The native actor did not expose a complete, portable character."""


@dataclass(frozen=True)
class SequenceRecord:
    address: int
    data: bytes


@dataclass(frozen=True)
class SequenceGraph:
    """Native 8/12-byte sequence records and every table entry using them."""
    pointers: tuple
    records: tuple
    durations: tuple = ()

    @property
    def clip_count(self):
        return len(self.pointers)

    @property
    def size(self):
        return len(self.pointers) * 4 + sum(len(r.data) for r in self.records)

    def relocate(self, address):
        """Bytes beginning with the pointer table at aligned RAM `address`.

        Record payloads and control flags are preserved. Only pointer-table
        words and the destinations of native link records are rewritten.
        """
        if address & 3 or not 0 <= address <= 0xFFFFFFFF - self.size:
            raise RecipeError("Animation sequence destination is invalid.")
        mapping, cursor = {}, address + len(self.pointers) * 4
        records = sorted(self.records, key=lambda r: r.address)
        previous_end = None
        for record in records:
            if record.address in mapping or len(record.data) not in (8, 12):
                raise RecipeError("Animation sequence graph has invalid records.")
            if previous_end is not None and record.address < previous_end:
                raise RecipeError("Animation sequence records overlap.")
            mapping[record.address] = cursor
            cursor += len(record.data)
            previous_end = record.address + len(record.data)
        try:
            out = bytearray(struct.pack(f"<{len(self.pointers)}I",
                                        *(mapping[p] for p in self.pointers)))
            for record in records:
                raw = bytearray(record.data)
                control = struct.unpack_from("<H", raw, 6)[0] & 0xC000
                if control in (0x4000, 0xC000):
                    if len(raw) != 12:
                        raise RecipeError("Animation link record is truncated.")
                    target = struct.unpack_from("<I", raw, 8)[0]
                    struct.pack_into("<I", raw, 8, mapping[target])
                elif len(raw) != 8:
                    raise RecipeError("Ordinary animation record has trailing bytes.")
                if control == 0:
                    target = record.address + 8
                    if mapping.get(target) != mapping[record.address] + 8:
                        raise RecipeError("Animation continuation is missing.")
                out.extend(raw)
        except KeyError as exc:
            raise RecipeError("Animation sequence points outside its graph.") from exc
        return bytes(out)


def sequence_graph(memory, table, poses, max_clips=256):
    """Capture a native table's consecutive valid clips against real poses.

    `memory` has the actor simulator's RAM interface, and `poses` is the
    actual set of pose indices in that actor's ANMP. Invalid entry zero is
    an error; an invalid later entry ends the contiguous native table.
    """
    if not actor_sim._in_ram(table) or table & 3:
        raise RecipeError("The actor has no aligned native animation table.")
    if not 1 <= max_clips <= 256:
        raise RecipeError("Animation table clip limit must be between 1 and 256.")
    raw_ram = bytes(memory.ram)
    pointers, durations, records = [], [], {}
    for clip_id in range(max_clips):
        pointer = memory.read(table + clip_id * 4, 4)
        try:
            if not actor_sim._in_ram(pointer):
                raise SequenceError("Sequence pointer is not in RAM")
            clip = read_clip(raw_ram, actor_sim.RAM_BASE, pointer, poses,
                             clip_id=clip_id)
        except (SequenceError, struct.error) as exc:
            if not pointers:
                raise RecipeError("The actor's idle animation cannot be decoded.") from exc
            break
        pointers.append(pointer)
        durations.append(clip.duration)
        for step in clip.steps:
            control = step.flags & 0xC000
            size = 12 if control in (0x4000, 0xC000) else 8
            raw = memory.bytes(step.address, size)
            if len(raw) != size:
                raise RecipeError("Animation sequence record is truncated.")
            if step.address in records and records[step.address] != raw:
                raise RecipeError("Animation sequence records disagree.")
            records[step.address] = raw
    graph = SequenceGraph(tuple(pointers), tuple(
        SequenceRecord(address, raw) for address, raw in sorted(records.items())),
        tuple(durations))
    # The same validation used when importing also checks native graphs.
    graph.relocate(0)
    return graph


@dataclass(frozen=True)
class NpcRecipe:
    name: str
    chunk_index: int
    area_index: int
    scene: int
    variant: int
    handler: int
    file_id: int
    parts: int
    layout: int
    anim_file: int
    anim_table: int
    box: tuple
    model_data: bytes
    animation_data: bytes
    bones_data: bytes
    sequences: SequenceGraph
    scale: tuple
    transform: int
    trail_index: object = None
    animation_trail_index: object = None
    model_groups: tuple = ()
    source_model_data: bytes = b""

    @property
    def source_chunk(self):
        return self.chunk_index

    @property
    def chunk(self):
        return self.chunk_index

    @property
    def source_area(self):
        return self.area_index

    @property
    def area(self):
        return self.area_index

    @property
    def model_source(self):
        return (actor_sim.TRAIL_ID + self.trail_index
                if self.trail_index is not None else self.file_id)

    @property
    def key(self):
        """Address-independent identity for repeated copies of a character."""
        digest = hashlib.sha256()
        for data in (self.model_data, self.animation_data, self.bones_data,
                     self.sequences.relocate(0)):
            digest.update(struct.pack("<I", len(data)))
            digest.update(data)
        digest.update(struct.pack("<I3h", self.transform, *self.scale))
        return digest.hexdigest()[:24]

    def describe(self):
        source = (f"TRAIL {self.trail_index}" if self.trail_index is not None
                  else f"file {self.file_id}")
        return (f"{self.name} ({self.parts} parts, {self.sequences.clip_count} animations; "
                f"AREA_{self.chunk_index:02X}, scene {self.scene}, variant {self.variant}; {source})")


@dataclass(frozen=True)
class CapturedBuild:
    file_id: int
    parts: int
    layout: int
    bones_data: bytes
    model_data: bytes
    trail_index: object = None


def _resource(world, pointer):
    for file_id, (address, size) in world.files.items():
        if address == pointer and size > 0:
            return file_id, bytes(world.mem.bytes(address, size)), world.trail_slots.get(file_id)
    raise RecipeError("The actor resource is not a complete loaded IDX entry.")


def normalize_model_groups(model_data, groups):
    """Put a captured actor's body groups first without changing their packets.

    Native handlers can choose alternate hands or heads after the multipart
    initializer. The generated standalone NPC needs that same initial body in
    group order 0..parts-1. Unused source groups remain available after it.
    Later body switches performed by the original native behavior are outside
    this standalone model recipe.
    """
    try:
        source = list(smst_groups(model_data))
    except (ValueError, struct.error) as exc:
        raise RecipeError("The character's model cannot be decoded.") from exc
    groups = tuple(groups)
    if not groups or any(not isinstance(i, int) or not 0 <= i < len(source) for i in groups):
        raise RecipeError("The character's model group mapping is invalid.")
    if groups == tuple(range(len(groups))):
        return bytes(model_data)
    order = groups + tuple(i for i in range(len(source)) if i not in groups)
    payloads = [bytes(model_data[source[i][1]:source[i][1] + source[i][4]]) for i in order]
    at, offsets = 4 + len(order) * 4, []
    for payload in payloads:
        offsets.append(at)
        at += len(payload)
    return (struct.pack('<HH', 0, len(order))
            + struct.pack(f'<{len(offsets)}I', *offsets) + b''.join(payloads))


def capture_builds(world):
    """Install a pass-through observer; return captures keyed by actor address."""
    captures = {}
    previous = world.cpu.hooks.get(actor_models.MULTI_PART)

    def capture(cpu):
        actor, parts, pointer, layout = cpu.r[4:8]
        if 1 <= parts <= actor_models.MAX_PARTS and actor_sim._in_ram(layout):
            try:
                file_id, model_data, trail = _resource(world, pointer)
                captures[actor] = CapturedBuild(
                    file_id, parts, layout, bytes(world.mem.bytes(layout, parts * 8)),
                    model_data, trail)
            except RecipeError:
                pass
        return previous(cpu) if previous is not None else PASS

    world.cpu.hooks[actor_models.MULTI_PART] = capture
    transforms = world.__dict__.setdefault('_npc_catalogue_transforms', {})
    for target in actor_sim.TRANSFORMS:
        previous_transform = world.cpu.hooks.get(target)

        def capture_transform(cpu, target=target, previous=previous_transform):
            transforms[cpu.r[4]] = target
            return previous(cpu) if previous is not None else PASS

        world.cpu.hooks[target] = capture_transform
    return captures


def _display_name(model_data, handler, labels=None, names=None):
    if names is not None:
        name = names(handler) if callable(names) else names.get(handler)
        if name:
            return str(name)
    label = labels.get(content_key(model_data)) if labels else None
    if label and label.name:
        name = label.name
        return name[:-6] if name.endswith(" Model") else name
    if handler == 0x80071A3C:
        return "Zippo"
    return f"NPC 0x{handler:08X}"


def recipe_from_actor(world, actor, build, *, chunk_index, area_index,
                      scene=0, labels=None, names=None):
    """Extract one fully initialized actor before restoring/changing its scene."""
    read, address = world.mem.read, actor.address
    if actor.dead or actor.error or read(address + actor_sim.PART_COUNT, 1) != build.parts:
        raise RecipeError("The actor did not finish initializing its model.")
    if len(build.bones_data) != build.parts * 8:
        raise RecipeError("The character skeleton is truncated.")
    for index, (parent, _x, _y, _z) in enumerate(struct.iter_unpack("<4h", build.bones_data)):
        if not -1 <= parent < index:
            raise RecipeError("The character skeleton is not parent-first.")
    try:
        groups = list(smst_groups(build.model_data))
    except (ValueError, struct.error) as exc:
        raise RecipeError("The character's model cannot be decoded.") from exc
    if len(groups) < build.parts:
        raise RecipeError("The model has fewer groups than its skeleton.")
    group_offsets = [g[1] for g in groups]
    model_address = None
    for file_id, (pointer, _size) in world.files.items():
        if file_id == build.file_id:
            model_address = pointer
            break
    if model_address is None:
        raise RecipeError("The model's IDX source is no longer loaded.")
    if world.mem.bytes(model_address, len(build.model_data)) != build.model_data:
        raise RecipeError("The character's temporary model slot was overwritten.")
    model_groups = []
    for index in range(build.parts):
        part = read(address + actor_sim.PARTS + index * 4, 4)
        pointer = read(part + 0x40, 4) if part else 0
        try:
            model_groups.append(group_offsets.index(pointer - model_address))
        except ValueError as exc:
            raise RecipeError("The actor assembles its body from more than one model entry.") from exc
    portable_model = normalize_model_groups(build.model_data, model_groups)
    anim_file, animation_data, anim_trail = _resource(world, read(address + 0x3C, 4))
    try:
        animation = parse_anmp(animation_data)
    except (ANMPError, struct.error) as exc:
        raise RecipeError("The actor's animation entry is not a complete ANMP.") from exc
    # Native pose application bounds its loop by both the frame's limb count
    # and the actor's allocated part count. Shared town ANMPs legitimately
    # alternate 15- and 16-limb frames on 15-part bodies such as Charles.
    poses = {frame.index for frame in animation.frames}
    table = read(address + 0x7C, 4)
    if actor_sim._in_ram(table):
        graph = sequence_graph(world.mem, table, poses)
    else:
        # Idle talkers such as Ark and the Fisherman attach their animation
        # table only when a conversation starts. Resolve literal tables from
        # their native handler, then validate against this actor's real ANMP.
        # More than one distinct valid table remains an ambiguity, not a guess.
        from game import new_npc
        images = (Image(bytes(world.mem.ram[0x10000:0xF0000]), 0x80010000),
                  Image(bytes(world.mem.ram[actor_sim.OVERLAY_BASE - 0x80000000:
                                            actor_sim.AREA_BASE - 0x80000000]),
                        actor_sim.OVERLAY_BASE))
        found = {}
        new_npc._scan(images, actor.handler, actor_models.DEPTH, set(), found)
        candidates = {}
        for candidate in dict.fromkeys(found.get("tables", ())):
            try:
                possible = sequence_graph(world.mem, candidate, poses)
                candidates.setdefault(possible.relocate(0), (candidate, possible))
            except RecipeError:
                pass
        if len(candidates) != 1:
            raise RecipeError("The actor's native animation table is unresolved or ambiguous.")
        table, graph = next(iter(candidates.values()))
    transform = getattr(world, '_npc_catalogue_transforms', {}).get(address)
    if transform is None:
        transform = world._transform_of(actor.handler)
    if transform not in actor_sim.TRANSFORMS:
        raise RecipeError("The actor uses an unresolved part-transform routine.")
    scale = tuple(struct.unpack("<3h", world.mem.bytes(address + 0xB8, 6)))
    if actor.handler == 0x80071A3C:
        # Zippo's initializer keeps him hidden at 0x200; FUN_8007091C
        # grows him to 0xC00 for his ordinary visible conversation pose.
        scale = (0xC00, 0xC00, 0xC00)
    elif transform == 0x800517F8:
        scale = (0x1000, 0x1000, 0x1000)
    elif not all(0 < v <= 0x7FFF for v in scale):
        raise RecipeError("The actor's root scale is unresolved.")
    box = tuple(struct.unpack("<4h", world.mem.bytes(address + 0x80, 8)))
    return NpcRecipe(
        _display_name(build.model_data, actor.handler, labels, names),
        int(chunk_index), int(area_index), int(scene), read(address + actor_sim.SLOT, 1),
        actor.handler, build.file_id, build.parts, build.layout, anim_file, table, box,
        portable_model, bytes(animation_data), bytes(build.bones_data), graph,
        scale, transform, build.trail_index, anim_trail, tuple(model_groups),
        bytes(build.model_data))


def catalogue(exe_path, overlay_path, dat_path, idx_path, chunk_index,
              area_index=None, names=None, labels=None, rejected=None):
    """Complete, distinct characters initialized in every scene of one area.

    `rejected`, when supplied, receives `(scene, handler, reason)` tuples.
    Incomplete or bespoke native bodies are never presented as working NPCs.
    """
    if area_index is None:
        area_index = int(chunk_index) - 4
        if area_index >= 22:
            area_index -= 22
    world = actor_sim.World(str(exe_path), str(overlay_path), str(dat_path), str(idx_path),
                            int(chunk_index), int(area_index))
    if game_build.current().name != "us-retail":
        raise RecipeError("The NPC catalogue currently requires the US retail layout.")
    if labels is None:
        path = Path(__file__).resolve().parents[1] / "labels" / "us-retail.json"
        try:
            labels = load_labels(str(path))
        except (OSError, ValueError):
            labels = None
    world.static_edit = True
    captures = capture_builds(world)
    world.start_workers()
    spawner = actor_sim.find_scene_spawner(Path(overlay_path).read_bytes())
    if not spawner:
        raise RecipeError("The area's native scene spawner was not found.")
    base = world.snapshot()
    found = {}
    for scene in range(actor_sim.MAX_SCENES):
        world.restore(base)
        captures.clear()
        world._npc_catalogue_transforms.clear()
        if not world._scene_listed(spawner, scene):
            continue
        world.pool_used.clear()
        if world.enter_scene(spawner, int(area_index), scene) is None:
            continue
        world.run(12, workers=False)
        for actor in world.actors:
            build = captures.get(actor.address)
            if build is None:
                continue
            try:
                recipe = recipe_from_actor(
                    world, actor, build, chunk_index=chunk_index, area_index=area_index,
                    scene=scene, labels=labels, names=names)
            except RecipeError as exc:
                if rejected is not None:
                    rejected.append((scene, actor.handler, str(exc)))
                continue
            found.setdefault(recipe.key, recipe)
    return sorted(found.values(), key=lambda r: (r.name.casefold(), r.file_id, r.handler))

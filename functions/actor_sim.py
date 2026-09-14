"""What an area's actors build, found by running their own code.

functions/actor_assembly.py reads one prop's init by hand. This does
what the game does instead, for every placed actor at once: load MAIN.EXE,
the overlay and the area's files where the game loads them, make an actor
per placement record the way FUN_80072a78 does, and call each actor's
lifecycle routine for a few frames in functions/psx_cpu.py. Whatever the
code attaches - parts it posed, children it spawned, sprites it started -
is then read back out of the actor structs, the same fields a savestate
walk reads (see project notes on Tomba2PickupObject).

Only a few MAIN.EXE routines are replaced, each because it reaches past
what is modelled; their addresses were recovered with
functions/decomp_symbols.py from the US decomp:

    f_AllocateActorRecordByType   the pools are not set up - hand out
                                  zeroed records and remember who asked
    f_AllocateActorModelPart      the same for parts
    f_TestActorVisibilityAndQueueForRender
                                  there is no camera - everything is on
                                  screen, which is what makes the update
                                  routines pose their parts
    f_LoadResourceChunkIntoGroup  copy an IDX trail resource into a file
                                  slot, as the area's scene code asks
    f_PlaySoundEffect, f_YieldCurrentCooperativeWorker
                                  nothing to play, nobody to yield to

The state is a fresh game: every progress flag is zero.
"""
import struct
from dataclasses import dataclass, field

import numpy as np

from functions import psx_cpu
from functions.psx_cpu import CPU, EmuError, s16, s32

EXE_HEADER = 0x800
OVERLAY_BASE = 0x80108F9C
AREA_BASE = 0x8018A000
FILE_TABLE = 0x800ECF58
FILE_SLOTS = 64
PART_BUDGET = 0x800ED098
PART_BUDGET_HELD = 0x4000
AREA_NUMBER = 0x800BF870
HEAP = 0x80300000
HEAP_END = 0x80780000
RESIDENT_BASE = 0x80200000
STACK = 0x801FFF00

ALLOCATE_RECORD = 0x8007A980
ALLOCATE_PART = 0x8007AAE8
VISIBILITY = 0x8007712C
LOAD_GROUP = 0x80045258
PLAY_SOUND = 0x80074590
YIELD = 0x80051F80
# The two routines that attach a model to a part. Done here rather than
# run, only so each actor keeps a note of what its code loaded - the
# decomp bodies are two and fifteen lines.
SET_PART_MODEL = 0x80051B04         # f_SetActorPartModel
INIT_SINGLE_PART = 0x80051B70       # f_InitializeSinglePartActorModel

# The area's scene workers: FUN_800263e8 fills eight 0x4C-byte slots at
# 0x80100400 from the id list MAIN.EXE keeps per area, and FUN_80026368
# runs each through the table at 0x8009D314 every frame. They are what
# spawns the scene's own actors - NPCs, enemies, birds - out of tables
# the placement records never mention.
START_WORKERS = 0x800263E8
# FUN_80048d3c: points the scratchpad's plane table at file slot 7, the
# area's SCLD, which anything that stands on the ground projects onto.
START_PLANES = 0x80048D3C
RUN_WORKERS = 0x80026368
WORKER_SLOTS = 0x80100400
WORKER_SIZE = 0x4C
WORKER_COUNT = 8
WORKER_TABLE = 0x8009D314
# Ids every area runs: Tomba, the persistent pickups - drawn elsewhere.
SHARED_WORKERS = frozenset((0, 1))

# Interiors. The interior frame loop never draws the area's MDAT: a room
# is only actors, spawned by the area's scene spawner with src_Interior + 1
# (f_UpdateInteriorSceneHandoff, f_UpdateCircusInteriorSceneTransition).
INSIDE = 0x800BF816                 # src_InsideWeaponlessInterior
INTERIOR = 0x800BF817               # src_Interior
TRANSITION = 0x800BF818             # src_InteriorTransition
ENTER_INTERIOR = 0x800A4AF8         # per area: moves Tomba and the camera in
MAX_SCENES = 24
PROLOGUE = 0x27BD
EXE_END = 0x800F0000

ACTOR_SIZE = 0xC0
MAX_PARTS = 64
PART_SIZE = 0x44
ACTIVE, KIND, SLOT, LIFECYCLE = 0x01, 0x02, 0x03, 0x04
PART_FRAME, PART_COUNT = 0x08, 0x09
LINKED, CALLBACK, FLAGS = 0x10, 0x1C, 0x28
POSITION, SEQUENCE, BANK, TURN = 0x2C, 0x38, 0x3C, 0x54
PARTS = 0xC0
DESTROY_STATE = 3

FRAMES = 4
BUDGET = 400_000

# pickup_art's sequence step encoding.
TICKS = 0x3FFF
OPCODE = 0xC000
STOP, GO_ON, JUMP, JUMP_RELOAD = 0x0000, 0x4000, 0x8000, 0xC000
MAX_STEPS = 64

TRAILER_BYTES = 0x700


@dataclass
class Part:
    source: tuple                   # (file id, group)
    matrix: np.ndarray              # game axes, includes scale
    position: np.ndarray            # world, game axes
    flags: int = 0
    posed: bool = True              # False: stood in rest pose by us


@dataclass
class Actor:
    address: int
    handler: int
    record: object = None           # the Placement, for a placed actor
    spawner: int = None             # address of the actor that made it
    worker: int = None              # id of the scene worker that made it
    parts: list = field(default_factory=list)
    bank: int = None                # file id of its sprite bank
    frames: tuple = ()              # ((frame, ticks), ...)
    loops: bool = False
    reward: int = 0
    position: np.ndarray = None
    error: str = ""
    dead: bool = False
    revived: int = 0                # how often its code tried to destroy it
    scene: int = None               # the room's scene index, None outside
    # Parts it allocated and then left with no model - an invisible
    # trigger, like A07's interior entrances, which load group 0 and clear
    # the pointer straight after.
    blank: int = 0
    # Every (file, group) its code attached to a part, whatever became of it.
    loaded: frozenset = frozenset()


class World:
    """RAM laid out the way an area is running, and the actors in it."""

    def __init__(self, exe_path, overlay_path, dat_path, idx_path, chunk,
                 area_number, resident_chunks=(0, 1, 2)):
        from gui.level.level_scene import area_files
        self.cpu = cpu = CPU()
        self.mem = mem = cpu.mem
        with open(exe_path, "rb") as f:
            exe = f.read()
        mem.load(struct.unpack_from("<I", exe, 0x18)[0], exe[EXE_HEADER:])
        with open(overlay_path, "rb") as f:
            mem.load(OVERLAY_BASE, f.read())
        self.files = {}                         # id -> (address, size)
        with open(dat_path, "rb") as dat:
            at = RESIDENT_BASE
            for number in resident_chunks:
                start, files = area_files(idx_path, number)
                if not files:
                    continue
                length = max(o + s for _i, _f, o, s in files)
                dat.seek(start)
                mem.load(at, dat.read(length))
                for _i, file_id, offset, size in files:
                    self._slot(file_id, at + offset, size)
                at = (at + length + 0xFFF) & ~0xFFF
            start, files = area_files(idx_path, chunk)
            length = max((o + s for _i, _f, o, s in files), default=0)
            dat.seek(start)
            mem.load(AREA_BASE, dat.read(length))
            for _i, file_id, offset, size in files:
                self._slot(file_id, AREA_BASE + offset, size)
            self.trail = self._trail(idx_path, chunk)
            self.dat = dat_path
        mem.write(PART_BUDGET, 4, PART_BUDGET_HELD)
        mem.write(AREA_NUMBER, 1, area_number)
        self.heap = HEAP
        self.actors = []
        self.by_address = {}
        self.running = None
        self.running_worker = None
        self.running_scene = None
        self.worker_errors = []
        self.rooms = {}                         # scene -> [Actor]
        self.part_owner = {}                    # part address -> actor
        cpu.hooks.update({
            ALLOCATE_RECORD: self._allocate_record,
            ALLOCATE_PART: self._allocate_part,
            VISIBILITY: self._visible,
            LOAD_GROUP: self._load_group,
            SET_PART_MODEL: self._set_part_model,
            INIT_SINGLE_PART: self._init_single_part,
            PLAY_SOUND: lambda c: 0,
            YIELD: lambda c: 0,
        })

    # --- setup ----------------------------------------------------------

    def _slot(self, file_id, address, size):
        if 0 <= file_id < FILE_SLOTS:
            self.mem.write(FILE_TABLE + file_id * 4, 4, address)
            self.files[file_id] = (address, size)

    @staticmethod
    def _trail(idx_path, chunk):
        with open(idx_path, "rb") as idx:
            idx.seek(chunk * 0x800 + (0x800 - TRAILER_BYTES))
            raw = idx.read(TRAILER_BYTES)
        return struct.unpack(f"<{len(raw) // 4}I", raw)

    def _alloc(self, size):
        at = self.heap
        self.heap = (self.heap + size + 3) & ~3
        if self.heap > HEAP_END:
            raise EmuError("simulation heap exhausted")
        self.mem.load(at, bytes(size))
        return at

    # --- hooks ----------------------------------------------------------

    def _allocate_record(self, cpu):
        address = self._alloc(ACTOR_SIZE + MAX_PARTS * 4)
        self.mem.write(address + 0x0A, 1, cpu.r[6])
        self.mem.write(address + 0x0C, 1, cpu.r[5])
        parent = self.by_address.get(self.running)
        actor = Actor(address, 0, spawner=self.running,
                      worker=None if self.running else self.running_worker,
                      scene=parent.scene if parent is not None
                      else self.running_scene)
        self.actors.append(actor)
        self.by_address[address] = actor
        return address

    def _allocate_part(self, cpu):
        address = self._alloc(PART_SIZE)
        self.part_owner[address] = self.running
        return address

    def _model_pointer(self, file_id, group):
        table = self.mem.read(FILE_TABLE + file_id * 4, 4)
        return table + self.mem.read(table + group * 4 + 4, 4) if table else 0

    def _note_loaded(self, address, file_id, group):
        actor = self.by_address.get(address)
        if actor is not None:
            actor.loaded = actor.loaded | {(file_id, group)}

    def _set_part_model(self, cpu):
        part, file_id, group = cpu.r[4], cpu.r[5], cpu.r[6]
        self.mem.write(part + 0x40, 4, self._model_pointer(file_id, group))
        self._note_loaded(self.part_owner.get(part) or self.running,
                          file_id, group)
        return 0

    def _init_single_part(self, cpu):
        actor, file_id, group = cpu.r[4], cpu.r[5], cpu.r[6]
        write = self.mem.write
        write(actor + PART_FRAME, 1, 1)
        write(actor + PART_COUNT, 1, 1)
        write(actor + 0x0D, 1, 0)
        for at in (0xB8, 0xBA, 0xBC):
            write(actor + at, 2, 0x1000)
        part = self._alloc(PART_SIZE)
        self.part_owner[part] = actor
        write(actor + PARTS, 4, part)
        write(part + 6, 2, 0xFFFF)
        for at in (0x38, 0x3A, 0x3C):
            write(part + at, 2, 0x1000)
        write(part + 0x40, 4, self._model_pointer(file_id, group))
        self._note_loaded(actor, file_id, group)
        return 0

    def _visible(self, cpu):
        self.mem.write(cpu.r[4] + ACTIVE, 1, 1)
        return 1

    def _load_group(self, cpu):
        index, group = cpu.r[4], cpu.r[5]
        if not (0 <= group < FILE_SLOTS and index + 1 < len(self.trail)):
            return 0
        start, end = self.trail[index], self.trail[index + 1]
        target = self.mem.read(FILE_TABLE + group * 4, 4)
        if end <= start or not target:
            return 0
        with open(self.dat, "rb") as dat:
            dat.seek(start)
            data = dat.read(end - start)
        try:
            self.mem.load(target, data)
            self.files[group] = (target, len(data))
            self._map_cache = None
        except EmuError:
            pass
        return 0

    # --- running --------------------------------------------------------

    def place(self, record, units):
        """An actor for one placement record - FUN_80072a78's writes."""
        cpu = self.cpu
        address = self._allocate_record(cpu)
        actor = self.by_address[address]
        actor.record = record
        actor.spawner = None
        w = self.mem.write
        w(address + FLAGS, 1, record.object_flags)
        w(address + CALLBACK, 4, record.handler)
        w(address + KIND, 1, record.kind)
        w(address + SLOT, 1, record.slot)
        w(address + POSITION + 2, 2, record.x)
        w(address + POSITION + 6, 2, record.y)
        w(address + POSITION + 10, 2, record.z)
        w(address + TURN + 2, 2, units(record.angle))
        w(address + TURN + 4, 2, units(record.angle2))
        return actor

    def start_workers(self, budget=BUDGET):
        for routine in (START_PLANES, START_WORKERS):
            try:
                self.cpu.call(routine, (), budget=budget, sp=STACK)
            except EmuError as e:
                self.worker_errors.append(f"start 0x{routine:08X}: {e}")

    def run_workers(self, budget=BUDGET):
        """FUN_80026368, one worker at a time so a spawn knows its maker."""
        read = self.mem.read
        for slot, worker in self.workers():
            routine = read(WORKER_TABLE + worker * 4, 4)
            if not routine:
                continue
            self.running, self.running_worker = None, worker
            self.mem.write(PART_BUDGET, 2, PART_BUDGET_HELD)
            try:
                self.cpu.call(routine, (slot,), budget=budget, sp=STACK)
            except EmuError as e:
                self.worker_errors.append(f"worker {worker}: {e}")
            finally:
                self.running_worker = None

    def workers(self):
        """[(slot address, id)] for the live scene workers."""
        read = self.mem.read
        return [(WORKER_SLOTS + n * WORKER_SIZE, read(WORKER_SLOTS + n * WORKER_SIZE + 2, 1))
                for n in range(WORKER_COUNT)
                if read(WORKER_SLOTS + n * WORKER_SIZE, 1)]

    def run(self, frames=FRAMES, budget=BUDGET, only=None, workers=True):
        """Every actor, every frame. Nothing is let go: an actor whose code
        asks to be destroyed is put back in the state it was in, and one
        whose code faults keeps what it had built - the editor shows what
        stands in a level, not what a fresh game happens to keep."""
        cpu, mem = self.cpu, self.mem
        read, write = mem.read, mem.write
        for _frame in range(frames):
            if workers:
                self.run_workers(budget)
            for actor in list(self.actors):
                if actor.dead or (only is not None and not only(actor)):
                    continue
                handler = read(actor.address + CALLBACK, 4)
                if not handler:
                    continue
                actor.handler = handler
                # As FUN_8007a904 does: off screen until the call says
                # otherwise. And the part budget held where no init can
                # refuse - a 16-bit count that releases push past 0x7FFF
                # goes negative and every actor after that kills itself.
                write(actor.address + ACTIVE, 1, 0)
                write(PART_BUDGET, 2, PART_BUDGET_HELD)
                before = mem.bytes(actor.address, ACTOR_SIZE + MAX_PARTS * 4)
                self.running = actor.address
                try:
                    cpu.call(handler, (actor.address, 0, 0), budget=budget,
                             sp=STACK)
                except EmuError as e:
                    actor.error = str(e)
                    actor.dead = True
                finally:
                    self.running = None
                if read(actor.address + LIFECYCLE, 1) == DESTROY_STATE:
                    actor.revived += 1
                    state = before[LIFECYCLE]
                    if state == DESTROY_STATE or not before[PART_COUNT]:
                        # Killed before anything was built: keep what it
                        # has now, and stop asking.
                        write(actor.address + CALLBACK, 4, handler)
                        if not read(actor.address + PART_COUNT, 1):
                            mem.load(actor.address, before)
                        actor.dead = True
                    else:
                        mem.load(actor.address, before)
            self._snapshot()

    def _snapshot(self):
        """Keep each actor's first complete reading - the pose it takes as
        the area opens, before anything has swung or wandered off."""
        models, banks = self._maps()
        for actor in self.actors:
            parts = self._parts(actor, models)
            score = (sum(p.posed for p in parts), len(parts))
            best = (sum(p.posed for p in actor.parts), len(actor.parts))
            if score > best or actor.position is None:
                actor.parts = parts
                self._sprite(actor, banks)
                self._place(actor)

    # --- rooms ----------------------------------------------------------

    def snapshot(self):
        # Shallow: an actor's record must stay the very Placement the scene
        # holds - the scene finds its actor by that identity. What a run
        # changes on an actor is reassigned, never mutated in place.
        import copy
        return (bytes(self.mem.ram), bytes(self.mem.scratch),
                [copy.copy(a) for a in self.actors], self.heap, dict(self.files))

    def restore(self, saved):
        import copy
        ram, scratch, actors, heap, files = saved
        self.mem.ram[:] = ram
        self.mem.scratch[:] = scratch
        self.actors = [copy.copy(a) for a in actors]
        self.by_address = {a.address: a for a in self.actors}
        self.heap, self.files = heap, dict(files)
        self._map_cache = None

    def _code(self, address):
        """Whether an address starts a routine that opens a stack frame."""
        if address & 3 or not (0x80010000 <= address < EXE_END
                               or OVERLAY_BASE <= address < AREA_BASE):
            return False
        return self.mem.read(address, 4) >> 16 == PROLOGUE

    def enter_scene(self, spawner, area_number, scene, budget=BUDGET):
        """Stand in the room whose scene index is `scene` the way the game
        arrives there, and spawn what its table holds. The actors made, or
        None if there is no such table."""
        write, read = self.mem.write, self.mem.read
        write(INTERIOR, 1, scene - 1)
        write(INSIDE, 1, 1)
        write(TRANSITION, 1, 2)
        enter = read(ENTER_INTERIOR + area_number * 4, 4)
        if self._code(enter):
            try:
                self.cpu.call(enter, (), budget=budget, sp=STACK)
            except EmuError:
                pass
        controller = self._alloc(ACTOR_SIZE + MAX_PARTS * 4)
        first = len(self.actors)
        self.running, self.running_scene = None, scene
        try:
            self.cpu.call(spawner, (controller, scene), budget=budget, sp=STACK)
        except EmuError:
            return None
        finally:
            self.running_scene = None
        made = self.actors[first:]
        if not made or not all(self._code(read(a.address + CALLBACK, 4))
                               for a in made):
            return None
        return made

    def run_rooms(self, spawner, area_number, frames=FRAMES):
        """Every room the area's scene spawner has a table for, each run
        from the state the area opened in, into `rooms`."""
        base = self.snapshot()
        rooms = {}
        for scene in range(1, MAX_SCENES):
            self.restore(base)
            if self.enter_scene(spawner, area_number, scene) is None:
                continue
            self.run(frames, only=lambda a, s=scene: a.scene == s,
                     workers=False)
            self.harvest()
            rooms[scene] = [a for a in self.actors if a.scene == scene]
        self.restore(base)
        self.rooms = rooms

    # --- reading back ---------------------------------------------------

    def models(self):
        """{model pointer: (file id, group)} for every loaded SMST."""
        out = {}
        read = self.mem.read
        for file_id, (address, size) in self.files.items():
            # The pointer table ends where the first group starts.
            first = read(address + 4, 4)
            if first < 8 or first & 3 or first > size:
                continue
            count = first // 4 - 1
            for group in range(count):
                offset = read(address + 4 + group * 4, 4)
                if 0 < offset < size:
                    out.setdefault(address + offset, (file_id, group))
        return out

    def banks(self):
        """{address: file id} for the loaded sprite banks - +0x3C holds an
        ANMP on a character, so only a SPRT counts."""
        from functions import format_detect
        out = {}
        for file_id, (address, size) in self.files.items():
            found = format_detect.best(self.mem.bytes(address, size))
            if found is not None and found.kind == "SPRT":
                out[address] = file_id
        return out

    def _maps(self):
        if getattr(self, "_map_cache", None) is None:
            self._map_cache = (self.models(), self.banks())
        return self._map_cache

    def _place(self, actor):
        read = self.mem.read
        actor.position = np.array([s32(read(actor.address + POSITION + k * 4, 4))
                                   / 65536.0 for k in range(3)])
        actor.reward = read(actor.address + SLOT, 1)

    def _parts(self, actor, models):
        """Every part with a model. One its code never got round to
        posing - an actor stopped before its first update - is stood in
        its rest pose off its parent chain rather than left out."""
        from functions.actor_assembly import rot_xyz
        read, mem, a = self.mem.read, self.mem, actor.address
        count = read(a + PART_COUNT, 1)
        drawn = read(a + PART_FRAME, 1)
        if drawn and count:
            count = min(count, drawn)
        turn = struct.unpack("<3h", mem.bytes(a + TURN, 6))
        root_m = rot_xyz(turn)
        root_t = np.array([s32(read(a + POSITION + k * 4, 4)) / 65536.0
                           for k in range(3)])
        out, poses = [], []
        blank = 0
        for n in range(min(count, MAX_PARTS)):
            part = read(a + PARTS + n * 4, 4)
            if not part:
                poses.append((root_m, root_t))
                continue
            blank += not read(part + 0x40, 4)
            raw = struct.unpack("<9h", mem.bytes(part + 0x18, 18))
            if any(raw):
                matrix = np.array(raw, dtype=np.float64).reshape(3, 3) / 4096.0
                position = np.array(struct.unpack("<3i", mem.bytes(part + 0x2C, 12)),
                                    dtype=np.float64)
            else:
                parent = s16(read(part + 6, 2))
                pm, pt = poses[parent] if 0 <= parent < len(poses) else (root_m, root_t)
                offset = np.array(struct.unpack("<3h", mem.bytes(part, 6)), dtype=np.float64)
                matrix = pm @ rot_xyz(struct.unpack("<3h", mem.bytes(part + 8, 6)))
                position = pm @ offset + pt
            poses.append((matrix, position))
            source = models.get(read(part + 0x40, 4))
            if source is not None:
                out.append(Part(source, matrix, position, read(part + 0x3E, 2),
                            posed=any(raw)))
        actor.blank = max(actor.blank, blank)
        return out

    def _sprite(self, actor, banks):
        read = self.mem.read
        bank = banks.get(read(actor.address + BANK, 4))
        step = read(actor.address + SEQUENCE, 4)
        if bank is not None and step:
            actor.bank = bank
            actor.frames, actor.loops = self._sequence(step)

    def harvest(self):
        """One last reading, for actors that never ran a frame."""
        self._map_cache = None
        self._snapshot()

    def _sequence(self, address):
        read, frames, seen = self.mem.read, [], set()
        while len(frames) < MAX_STEPS and address:
            if address in seen:
                return tuple(frames), True
            seen.add(address)
            frame, control = read(address, 2), read(address + 2, 2)
            frames.append((frame, control & TICKS))
            opcode = control & OPCODE
            if opcode == STOP:
                break
            if opcode in (JUMP, JUMP_RELOAD):
                address = read(address + 4, 4)
            else:
                address += 4
        return tuple(frames), False


@dataclass
class Rider:
    """A sprite an actor carries, as the simulation left it."""
    label: str
    reward: int
    position: np.ndarray
    art: object = None


class Posed:
    """What one actor and the children near it drew, as the simulation
    left them, moved rigidly with the actor from then on - turned about
    its own position by however far the editor turns it."""

    def __init__(self, name, note, pieces, riders, position, yaw):
        from functions.actor_assembly import rot_y
        self._rot_y = rot_y
        self.name, self.note = name, note
        self.pieces = list(pieces)
        self.riders = list(riders)
        self.sources = tuple(p.source for p in self.pieces)
        self.origin = np.asarray(position, dtype=np.float64)
        self.yaw = yaw

    def _move(self, state):
        turn = self._rot_y(state.yaw - self.yaw)
        base = np.asarray(state.position, dtype=np.float64)
        return turn, base

    def pose(self, state):
        turn, base = self._move(state)
        return [(turn @ p.matrix, turn @ (p.position - self.origin) + base)
                for p in self.pieces]

    def sprites(self, state):
        turn, base = self._move(state)
        return [Rider(r.label, r.reward, turn @ (r.position - self.origin) + base,
                      r.art) for r in self.riders]

    def props(self):
        return []


def subtree(world, actor, actors=None):
    """The actor and everything it spawned, and they spawned."""
    children = {}
    for other in (world.actors if actors is None else actors):
        if other.spawner is not None:
            children.setdefault(other.spawner, []).append(other)
    out, queue = [], [actor]
    while queue:
        current = queue.pop(0)
        out.append(current)
        queue.extend(children.get(current.address, ()))
    return out


def simulate(exe_path, overlay_path, dat_path, idx_path, chunk, area_number,
             records, units, frames=FRAMES, spawner=None):
    """The area as it opens - its placed actors and all they spawned - and,
    given the area's scene spawner, every room it has."""
    world = World(exe_path, overlay_path, dat_path, idx_path, chunk, area_number)
    world.start_workers()
    for record in records:
        world.place(record, units)
    world.run(frames)
    world.harvest()
    if spawner:
        world.run_rooms(spawner, area_number, frames)
    return world

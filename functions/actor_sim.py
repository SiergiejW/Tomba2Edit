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
import collections
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
# g_ActorPool0FreeCount: every transient effect spawner gives up below 7,
# and the allocation hook never counts it down.
POOL_FREE = 0x800E7E7C
POOL_FREE_HELD = 0x40
NEW_GAME = 0x8007982C               # f_NewGameSetDefaultValues
INTRO_CUTSCENE = 0x800BF89C         # src_IntroCutscene: 2 at New Game,
INTRO_PLAYED = 4                    # 4 once the intro is over
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
# Opcodes installed_handlers follows: a constant built by lui and addiu/ori,
# then stored at +0x1C - a routine whose frame opens within PROLOGUE_REACH
# words (some load a global first).
LUI, ADDIU, ORI, SW, JR_RA = 0x0F, 0x09, 0x0D, 0x2B, 0x03E00008
PROLOGUE_REACH = 4
# An event's actor further than this from the origin in x or z placed itself.
UNPLACED = 256

# A room's enter routine (ENTER_INTERIOR) starts a cooperative worker with
# the room's resource index and waits for it: the worker loads that IDX
# trail resource - the room's NPC models - into slot 15
# (f_FinishA00InteriorEntryTransition and its twins). Run here at once, on
# a worker record of its own; outside a room entry a start still fails.
START_WORKER = 0x80044BD4           # f_StartCooperativeWorker
TERMINATE_WORKER = 0x80051FB4       # f_TerminateCurrentCooperativeWorker
CURRENT_WORKER = 0x1F800138         # g_CurrentCooperativeWorker
WORKER_ARG = 0x6E
WORKER_RECORD = 0x80
# A model read out of a slot a trail resource was loaded into is named
# (TRAIL_ID + resource index, group), not by the slot's own file id.
TRAIL_ID = 0x1000

# A purified area runs its cursed overlay with its bit set here.
PURIFIED_AREAS = 0x800BFE56         # src_PurifiedAreas

# A game with every event done: each byte of New Game's block an area's
# code reads set to 0xFF, the way the game marks a finished event
# (src_EventWinsWindmill == -1) - bar the bytes saying where the world is.
SAVE_BLOCK, SAVE_BLOCK_SIZE = AREA_NUMBER, 0x5F4
DONE = 0xFF
# src_Travelling (+0x80..+0x83): travelling, mini, invisible - how Tomba is,
# not what he has done - with the Pig Bag's count in its last byte.
TRAVELLING = AREA_NUMBER + 0x10
WORLD_BYTES = frozenset((*range(AREA_NUMBER, AREA_NUMBER + 4),
                         *range(TRAVELLING, TRAVELLING + 4),
                         PURIFIED_AREAS, PURIFIED_AREAS + 1, INTRO_CUTSCENE))
# The Pig Bag: f_AddInventoryQuantity appends items 0x17 to 0x1C and counts
# them. An Evil Pig Door stands only if its item (+0x7E) is in the bag - with
# every event done, all six are.
PIG_BAG_COUNT, PIG_BAG = TRAVELLING + 3, TRAVELLING + 4
PIG_BAG_ITEMS = range(0x17, 0x1D)
LOAD_SIZES = {0x20: 1, 0x24: 1, 0x21: 2, 0x25: 2, 0x23: 4}
# beq bne blez bgtz slti sltiu andi xori - see _tested.
TESTS = frozenset((0x04, 0x05, 0x06, 0x07, 0x0A, 0x0B, 0x0C, 0x0E))
TEST_LOOKAHEAD = 6

# Lines. Nothing in an MDAT or SMST is one: ropes, chains and fishing lines
# come out of an actor's draw routine (+0x18) straight into the primitive
# buffer. They are read back there - the camera made identity, and every
# vertex the GTE projects named by its screen position (psx_cpu GTE.capture).
DRAW = 0x18
PRIMITIVE_CURSOR = 0x800BF544       # g_UiPrimitiveCursor
ORDERING_TABLE = 0x800ED8C8         # g_RenderOrderingTables
CAMERA = 0x1F8000F8
OT_SLOTS = 0x800
PRIMITIVE_BYTES = 0x40000
LINE_REACH = 8000                   # longer than this is a misread vertex
ACTOR_REACH = 6000                  # a line point this far from its actor too
# A polygon's corners are named, never guessed: only a corner beyond this is
# dropped. A0E's waterfall spans 6000 either side of its actor and 9600 down.
POLY_REACH = 16000
TERMINATOR_MASK, TERMINATOR = 0xF000F000, 0x50005000
# Render kinds f_DrawClass4ActorRenderQueue draws itself rather than through
# +0x18 - kind 2 is each area's chain drawer (A01 FUN_80129114) - run on a
# queue holding the one actor: +0x136 set keeps last frame's queue, whose
# count and list are +0x152 and +0x14C.
CLASS4_QUEUE = 0x8003BCF4
QUEUE_HELD, QUEUE_COUNT, QUEUE_LIST = 0x1F800136, 0x1F800152, 0x1F80014C
QUEUED_KINDS = frozenset((1, 2, 3, 0x16, 0x17))
# A textured polygon's colour: 0x80 draws the texel as it is.
NEUTRAL = 128.0
# Captures after its actor ran that a pass may draw nothing in before it is
# no longer run.
QUIET_CAPTURES = 2
# While capturing, the routines that put out an actor's model and sprite
# parts do nothing: those are read from the parts and sequences already, and
# drawn a second time as captured polygons they cover the level.
PART_DRAWERS = (0x8003CDD8,         # f_BuildActorModelPartPrimitives
                0x8003F698,         # f_DrawModelPrimitiveStreamForCurrentArea
                0x8003C8F4,         # f_DrawActorSpriteParts
                0x8003C464,         # f_DrawActorSpritePartsWithScaleAndZRotation
                0x8003C2D4)         # f_DrawActorSpritePartsWithZRotation
# src_SpriteAnimationFrame: the counter draw routines step their cells by
# (A0E's waterfall: cell (frame >> 1) & 15). 0 while capturing - _uv_frames
# draws the other values a probe says matter.
ANIMATION_FRAME = 0x1F80017C
# (size, crc32) -> what format_detect makes of a loaded file; shared by loads.
FILE_KINDS = {}
FRAME_CYCLE = 64
FRAME_PROBES = (1, 2, 4, 8, 16, 32)

# A scene controller retires through f_RetireSceneController and calls its
# spawner twice (scene 0 as it starts, the next at each handoff); the
# spawner allocates with f_AllocateActor.
RETIRE_SCENE = 0x8007ADD0
ALLOCATE_ACTOR = 0x80072DDC

# A chest, as f_SpawnPersistentPickupPlacementTable stands one up.
CHEST_HANDLER = 0x80040558          # f_HandlePersistentChestActor
# f_HandleSecondaryItemPickup: what f_SpawnAreaIndexedPersistentPickup stands up
# - the cursed mine's ice boomerang in the lava, under a mudball carrier.
SECONDARY_ITEM_HANDLER = 0x8004C238
CHEST_KIND = 8
PICKUP_BIT = 0x0E
PLANE = 0x2A
PICKUP_BEHAVIOUR = 0x5E
CHEST_CONTENTS, CHEST_EFFECT, EFFECT_FLAGS = 0x60, 0x62, 0x64
ITEM_ID, INTERIOR_ID = 0x68, 0x6A
CONTENTS_MASK = 0xFFF
PERSIST_FLAG = 0x80

# The machine's five actor pools, as f_InitializeActorPools builds them:
# 0x34, 0x3A, 0x2A and 0x28 records, then five. f_AllocateActorRecordByType's
# first argument picks the pool, and allocation fails when it is empty - which
# is what stops a scene from standing up more than the game ever could.
# f_AllocateActorFromPool0 keeps POOL_RESERVE records back for emergencies.
POOL_SIZES = (0x34, 0x3A, 0x2A, 0x28, 5)
POOL_RESERVE = 3

ACTOR_SIZE = 0xC0
MAX_PARTS = 64
PART_SIZE = 0x44
ACTIVE, KIND, SLOT, LIFECYCLE = 0x01, 0x02, 0x03, 0x04
PART_FRAME, PART_COUNT = 0x08, 0x09
LINKED, CALLBACK, FLAGS = 0x10, 0x1C, 0x28
POSITION, SEQUENCE, BANK, TURN = 0x2C, 0x38, 0x3C, 0x54
CLASS = 0x0C                        # allocation class; 6 is a transient effect
EFFECT_CLASS = 6
RENDER_KIND, QUAD_KIND, QUAD_CORNERS = 0x0B, 0x14, 0x60
PARTS = 0xC0
DESTROY_STATE = 3

FRAMES = 4
BUDGET = 400_000
# Extra passes for actors spawned too late in a run to have run at all.
SETTLE_FRAMES = 2
# A follower still closing on its target as the run ends - the koma pig's hands
# (FUN_A06__8013560c) cut the gap by an eighth a frame - runs on alone until a
# step is this short, and is read there.
CONVERGE_FRAMES = 48
CONVERGED = 0.5
# Frames an actor with parts no run has drawn runs on while its state still
# moves: a purified area's ice cube (FUN_A05__8012a0c8) goes to its release
# state and is destroyed two frames on.
TRANSIT_FRAMES = 4

# A sprite sequence step: frame u16, then ticks and an opcode
# (f_AdvanceActorTimedFrameSequence).
TICKS = 0x3FFF
OPCODE = 0xC000
NEXT, JUMP, HOLD, JUMP_LOOP = 0x0000, 0x4000, 0x8000, 0xC000
# A scene table record: kind, variant, slot, reward, x, y, z (i16), the
# condition and its argument, the handler (u32) - 0xFF kind ends a list.
SCENE_RECORD = 16
SCENE_END = 0xFF
# Byte 11: 1 skips the record once the area is purified, 2 while time is
# stopped (f_Spawn*SceneActorsFromPlacementTable, every one of them).
ARG_CURSED_ONLY, ARG_TIME_RUNNING = 1, 2
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
    pool: int = 0                   # which actor pool its record came from
    record: object = None           # the Placement, for a placed actor
    spawner: int = None             # address of the actor that made it
    worker: int = None              # id of the scene worker that made it
    parts: list = field(default_factory=list)
    bank: int = None                # file id of its sprite bank
    sprite_bank: int = None         # the bank its code chose, sequence or not
    frames: tuple = ()              # ((frame, ticks), ...)
    loops: bool = False
    reward: int = 0
    position: np.ndarray = None
    error: str = ""
    dead: bool = False
    revived: int = 0                # how often its code tried to destroy it
    shown: bool = False             # a run of it queued it to be drawn
    # Destroyed by its code after building, before any run drew it: in a fresh
    # game, not in the level as it opens.
    discarded: bool = False
    changed: bool = False           # its last run moved +0x04/+0x05
    scene: int = None               # the room's scene index, None outside
    # Parts it allocated and then left with no model - an invisible
    # trigger, like A07's interior entrances, which load group 0 and clear
    # the pointer straight after.
    blank: int = 0
    # Every (file, group) its code attached to a part, whatever became of it.
    loaded: frozenset = frozenset()
    # Line primitives its draw routine put out: (a, b, colour a, colour b,
    # blended), points in game axes - see capture_lines.
    lines: tuple = ()
    # Textured polygons they put out: (corners, uvs, colours, CLUT word,
    # blended, page word) - see textured_primitives.
    polys: tuple = ()
    # {CLUT word: ((du, dv) per frame of ANIMATION_FRAME, ...)} for polygons
    # whose UVs step together with it; None until probed.
    uv_frames: dict = None
    # Per capture pass (DRAW, CALLBACK, None for the queue): how many
    # captures after it ran drew nothing, or None once one drew.
    silent: dict = field(default_factory=dict)
    ran: bool = False               # whether its handler has run at all
    read_ran: bool = False          # whether its kept reading is from after it ran
    # (handler, reward) as its first run found them; code rewrites both.
    born: tuple = None
    # Ends the run with parts but a draw count (+0x08) of 0: nothing drawn.
    hidden: bool = False
    # Its init never finished - still in lifecycle state 0, waiting on
    # something a fresh game doesn't have.
    waiting: bool = False
    # (condition, argument) of a scene record whose condition failed but
    # was spawned anyway - see enter_scene.
    gated: tuple = None
    pickup: object = None           # the Pickup record, for a chest
    # Render kind 0x14: a textured quad whose corners are its own shorts at
    # +0x60..+0x76 (f_DrawPresentationActorList) - 60.x's rope.
    quad: tuple = None
    # Its parts' offsets and turns at the last reading pass, and whether a
    # reading is owed because its run turned them after transforming.
    turns: bytes = None
    stale: bool = False
    restaled: bool = False


class World:
    """RAM laid out the way an area is running, and the actors in it."""

    def __init__(self, exe_path, overlay_path, dat_path, idx_path, chunk,
                 area_number, resident_chunks=(0, 1, 2), purified=False,
                 finished=()):
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
        # A fresh game is what New Game leaves - its defaults, not zeroed RAM
        # - with the intro played: no area can be walked before it ends.
        try:
            cpu.call(NEW_GAME, (), budget=BUDGET, sp=STACK)
        except EmuError:
            pass
        mem.write(INTRO_CUTSCENE, 1, INTRO_PLAYED)
        for address in finished:
            mem.write(address, 1, DONE)
        if finished:
            mem.write(PIG_BAG_COUNT, 1, len(PIG_BAG_ITEMS))
            for slot, item in enumerate(PIG_BAG_ITEMS):
                mem.write(PIG_BAG + slot, 1, item)
        mem.write(PART_BUDGET, 4, PART_BUDGET_HELD)
        mem.write(POOL_FREE, 1, POOL_FREE_HELD)
        mem.write(AREA_NUMBER, 1, area_number)
        self.purified = purified
        self.finished = tuple(finished)
        if purified:
            mem.write(PURIFIED_AREAS, 2,
                      mem.read(PURIFIED_AREAS, 2) | 1 << area_number)
        self.heap = HEAP
        self.actors = []
        self.by_address = {}
        self.running = None
        self.running_worker = None
        self.running_scene = None
        self.worker_errors = []
        self.rooms = {}                         # scene -> [Actor]
        self.events = []                        # (handler, [Actor]) - spawn_events
        self.pool_used = collections.Counter()  # pool -> records handed out
        # id(actor) -> (actor, RAM frame) whose lines are owed - see _owe_capture.
        self.owed = {}
        self.defer_capture = True               # False: draw at every reading
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
            START_WORKER: self._start_worker,
            TERMINATE_WORKER: lambda c: 0,
        })
        self.entering = False
        # slot -> IDX trail index a resource load put there; per room, as
        # it stood once the room was entered.
        self.trail_slots = {}
        self.room_trails = {}

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
        # An empty pool is how the game says no - without it a scene whose
        # code keeps asking (A07's circus, with every event done) stands up
        # thousands of actors the machine could never hold.
        pool = cpu.r[4] & 0xFF
        size = POOL_SIZES[pool] if pool < len(POOL_SIZES) else POOL_SIZES[0]
        if self.pool_used[pool] + (POOL_RESERVE if pool == 0 else 0) >= size:
            return 0
        return self._new_record(cpu)

    def _new_record(self, cpu):
        """A record, as the hook hands one out - but without asking the pool,
        which is how a placement table stands its own actors up."""
        pool = cpu.r[4] & 0xFF
        self.pool_used[pool] += 1
        address = self._alloc(ACTOR_SIZE + MAX_PARTS * 4)
        self.mem.write(address + 0x0A, 1, cpu.r[6])
        self.mem.write(address + 0x0C, 1, cpu.r[5])
        parent = self.by_address.get(self.running)
        actor = Actor(address, 0, pool=pool, spawner=self.running,
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
            self.trail_slots[group] = index
            self._map_cache = None
        except EmuError:
            pass
        return 0

    def _start_worker(self, cpu):
        """f_StartCooperativeWorker, while a room is being entered: the
        worker runs to its end now. Anywhere else it would wait forever on
        a thread nobody runs, so it fails as it always did."""
        entry, argument = cpu.r[4], cpu.r[5] & 0xFF
        if not self.entering:
            raise EmuError("started a cooperative worker")
        if entry & 3 or not (0x80010000 <= entry < EXE_END
                             or OVERLAY_BASE <= entry < AREA_BASE):
            return 0
        worker = self._alloc(WORKER_RECORD)
        self.mem.write(worker + WORKER_ARG, 1, argument)
        previous = self.mem.read(CURRENT_WORKER, 4)
        registers, hi, lo = list(cpu.r), cpu.hi, cpu.lo
        self.mem.write(CURRENT_WORKER, 4, worker)
        try:
            cpu.call(entry, (), budget=BUDGET, sp=(registers[29] - 0x400) & 0xFFFFFFFF)
        except EmuError:
            pass
        finally:
            cpu.r[:] = registers
            cpu.hi, cpu.lo = hi, lo
            self.mem.write(CURRENT_WORKER, 4, previous)
        return 0

    # --- running --------------------------------------------------------

    def place(self, record, units):
        """An actor for one placement record - FUN_80072a78's writes."""
        cpu = self.cpu
        address = self._new_record(cpu)
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

    def tag_chests(self, chests):
        """Hand each chest actor the area's own code already made its record,
        by save bit and kind; the records no actor carries."""
        read = self.mem.read
        waiting = {}
        for pickup in chests:
            waiting.setdefault((pickup.bit & 0xFFFF, pickup.reward & 0x7F), []).append(pickup)
        for actor in self.actors:
            handler = actor.born[0] if actor.born else actor.handler
            if actor.pickup is not None or handler != CHEST_HANDLER:
                continue
            key = (read(actor.address + PICKUP_BIT, 2), read(actor.address + SLOT, 1) & 0x7F)
            if waiting.get(key):
                actor.pickup = waiting[key].pop(0)
        return [p for group in waiting.values() for p in group]

    def place_chest(self, pickup, budget=BUDGET):
        """An actor for one chest record - f_SpawnPersistentPickupPlacementTable's
        allocation and writes - so its own handler builds the body and lid
        and stands them on the ground the way its behaviour byte says."""
        first = len(self.actors)
        self.running = None
        try:
            self.cpu.call(ALLOCATE_ACTOR, (0, pickup.type, pickup.alloc,
                                           pickup.persist & ~PERSIST_FLAG & 0xFF),
                          budget=budget, sp=STACK)
        except EmuError:
            return None
        if len(self.actors) <= first:
            return None
        actor = self.actors[first]
        a, w = actor.address, self.mem.write
        w(a + CALLBACK, 4, CHEST_HANDLER)
        w(a + KIND, 1, CHEST_KIND)
        for k, value in enumerate((pickup.x, pickup.y, pickup.z)):
            w(a + POSITION + k * 4, 4, (value << 16) & 0xFFFFFFFF)
        for at in (TURN, TURN + 2, TURN + 4):
            w(a + at, 2, 0)
        w(a + PLANE, 1, pickup.plane)
        w(a + SLOT, 1, pickup.reward & 0x7F)
        w(a + PICKUP_BIT, 2, pickup.bit & 0xFFFF)
        w(a + PICKUP_BEHAVIOUR, 1, pickup.behaviour)
        w(a + CHEST_CONTENTS, 2, pickup.config & CONTENTS_MASK)
        w(a + CHEST_EFFECT, 2, (s16(pickup.config) >> 12) & 0xFFFF)
        w(a + ITEM_ID, 2, pickup.reward >> 7)
        w(a + INTERIOR_ID, 2, pickup.persist)
        w(a + EFFECT_FLAGS, 2, 5 if pickup.persist & PERSIST_FLAG else 1)
        actor.pickup = pickup
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
        if only is not None:
            chosen = only

            def only(actor):
                # What a chosen actor spawns runs with it: Donglin's gate,
                # stood up by the gated pass, spawns its guard in its init.
                for _depth in range(16):
                    if actor is None:
                        return False
                    if chosen(actor):
                        return True
                    actor = self.by_address.get(actor.spawner)
                return False

        paths = {}                      # id(follower) -> [position per frame]
        for _frame in range(frames):
            if workers:
                self.run_workers(budget)
            for actor in list(self.actors):
                if actor.dead or (only is not None and not only(actor)):
                    continue
                self._run_actor(actor, budget)
                if actor.spawner is not None:
                    paths.setdefault(id(actor), []).append(self._position(actor.address))
            self._read()
        # Actors spawned during the last frame never ran; give them frames
        # of their own so what they build is there to read.
        for _settle in range(SETTLE_FRAMES):
            fresh = [a for a in self.actors if not a.ran and not a.dead
                     and (only is None or only(a))]
            if not fresh:
                break
            for actor in fresh:
                self._run_actor(actor, budget)
            self._read()
        chosen = [a for a in self.actors if not a.dead and (only is None or only(a))]
        self._transit(chosen, budget)
        self._converge(chosen, paths, budget)
        self.settle_captures()

    def _owe_capture(self, actors):
        """Lines and polygons owed for these actors, to be drawn from RAM as
        it stands now: a later reading of the same actor replaces its debt,
        so each is drawn once, from its last reading's frame."""
        if not self.defer_capture:
            self.capture_lines(actors)
            return
        frame = (bytes(self.mem.ram), bytes(self.mem.scratch))
        for actor in actors:
            self.owed[id(actor)] = (actor, frame)

    def settle_captures(self):
        """Draw every owed capture, each from its own frame; RAM is left as
        it was."""
        if not self.owed:
            return
        owed, self.owed = self.owed, {}
        frames = {}
        for actor, frame in owed.values():
            frames.setdefault(id(frame), (frame, []))[1].append(actor)
        ram, scratch = bytes(self.mem.ram), bytes(self.mem.scratch)
        try:
            for (frame_ram, frame_scratch), actors in frames.values():
                self.mem.ram[:] = frame_ram
                self.mem.scratch[:] = frame_scratch
                self.capture_lines(actors)
        finally:
            self.mem.ram[:] = ram
            self.mem.scratch[:] = scratch

    def _transit(self, actors, budget):
        """Actors with parts no run has drawn, their state still moving as the
        run ends, run on until it holds - see TRANSIT_FRAMES."""
        if self.finished:
            return
        for _frame in range(TRANSIT_FRAMES):
            moving = [a for a in actors if a.ran and a.changed and a.parts
                      and not (a.shown or a.dead or a.discarded)]
            if not moving:
                break
            for actor in moving:
                self._run_actor(actor, budget)

    def _converge(self, actors, paths, budget):
        """Followers whose last steps each came shorter, run on alone until
        they arrive - their turn held too - and read there. One whose step
        grows is wandering, not arriving, and keeps its reading."""
        mem, moving, arrived = self.mem, {}, []
        for actor in actors:
            path = paths.get(id(actor)) or ()
            if len(path) < 3 or not (actor.parts or actor.frames):
                continue
            last = np.abs(path[-1] - path[-2]).max()
            if CONVERGED < last < np.abs(path[-2] - path[-3]).max():
                moving[id(actor)] = (actor, last)
        for _frame in range(CONVERGE_FRAMES):
            if not moving:
                break
            for key, (actor, last) in list(moving.items()):
                was, turn = self._position(actor.address), mem.bytes(actor.address + TURN, 6)
                self._run_actor(actor, budget)
                step = np.abs(self._position(actor.address) - was).max()
                if actor.dead or step > last:
                    del moving[key]
                elif step <= CONVERGED and mem.bytes(actor.address + TURN, 6) == turn:
                    arrived.append(actor)
                    del moving[key]
                else:
                    moving[key] = (actor, step)
        if arrived:
            self._reread(arrived)

    def _reread(self, actors):
        """A fresh reading of these actors, and their lines."""
        models, banks = self._maps()
        for actor in actors:
            actor.parts = self._parts(actor, models)
            self._sprite(actor, banks)
            self._place(actor)
            actor.turns = self._turns(actor)
        self._owe_capture(actors)

    def _run_actor(self, actor, budget):
        cpu, mem = self.cpu, self.mem
        read, write = mem.read, mem.write
        handler = read(actor.address + CALLBACK, 4)
        if not handler:
            return
        if actor.born is None:
            actor.born = (handler, read(actor.address + SLOT, 1))
        actor.handler, actor.ran = handler, True
        # As FUN_8007a904 does: off screen until the call says otherwise.
        # And the part budget held where no init can refuse - a 16-bit
        # count that releases push past 0x7FFF goes negative and every
        # actor after that kills itself.
        write(actor.address + ACTIVE, 1, 0)
        write(PART_BUDGET, 2, PART_BUDGET_HELD)
        before = mem.bytes(actor.address, ACTOR_SIZE + MAX_PARTS * 4)
        self.running = actor.address
        try:
            cpu.call(handler, (actor.address, 0, 0), budget=budget, sp=STACK)
        except EmuError as e:
            actor.error = str(e)
            actor.dead = True
            self.pool_used[actor.pool] -= 1     # its record goes back
        finally:
            self.running = None
        actor.shown |= bool(read(actor.address + ACTIVE, 1))
        if read(actor.address + LIFECYCLE, 1) == DESTROY_STATE:
            actor.revived += 1
            state = before[LIFECYCLE]
            if state == DESTROY_STATE or not before[PART_COUNT]:
                # Killed before anything was built: keep what it has now,
                # and stop asking.
                write(actor.address + CALLBACK, 4, handler)
                if not read(actor.address + PART_COUNT, 1):
                    mem.load(actor.address, before)
                if not actor.dead:
                    self.pool_used[actor.pool] -= 1
                actor.dead = True
            else:
                actor.discarded |= not actor.shown and not self.finished
                mem.load(actor.address, before)
        actor.changed = (mem.bytes(actor.address + LIFECYCLE, 2)
                         != before[LIFECYCLE:LIFECYCLE + 2])

    def _snapshot(self):
        """Keep each actor's first complete reading - the pose it takes as
        the area opens, before anything has swung or wandered off. The
        actors whose reading it took."""
        models, banks = self._maps()
        taken = []
        for actor in self.actors:
            parts = self._parts(actor, models)
            score = (sum(p.posed for p in parts), len(parts))
            best = (sum(p.posed for p in actor.parts), len(actor.parts))
            turns = self._turns(actor)
            # A run that turns its parts after transforming them leaves its
            # reading a frame stale - the rare fish's stick bends that way,
            # and its line still ends on last frame's point - so it is read
            # once more.
            owed = actor.stale
            # A reading taken before its first run is no reading at all: a
            # sprite's init starts its sequence, which no part score sees.
            # Nor is one left at the origin: a linked child is only put
            # beside its parent on its second run.
            if (score > best or actor.position is None or owed
                    or (actor.ran and not actor.read_ran)
                    or not actor.position.any()):
                actor.stale = bool(not owed and not actor.restaled and parts
                                   and actor.turns is not None
                                   and turns != actor.turns)
                actor.restaled |= owed
                actor.read_ran = actor.ran
                actor.parts = parts
                self._sprite(actor, banks)
                self._place(actor)
                taken.append(actor)
            actor.turns = turns
        return taken

    def _turns(self, actor):
        """Its parts' own offsets and turns (+0x00..+0x0E), as bytes."""
        read, mem, a = self.mem.read, self.mem, actor.address
        out = bytearray()
        for n in range(min(read(a + PART_COUNT, 1), MAX_PARTS)):
            part = read(a + PARTS + n * 4, 4)
            if part:
                out += mem.bytes(part, 0x0E)
        return bytes(out)

    def _read(self):
        """A reading, and the lines the re-read actors draw in that same
        frame - a rope captured later has swung away from its pose."""
        taken = self._snapshot()
        if taken:
            # A parent's line can end on a child only now placed - the rare
            # fish on its stick - so its spawners draw again too.
            again, seen = list(taken), {id(a) for a in taken}
            for actor in taken:
                parent = self.by_address.get(actor.spawner)
                while parent is not None and id(parent) not in seen:
                    seen.add(id(parent))
                    again.append(parent)
                    parent = self.by_address.get(parent.spawner)
            self._owe_capture(again)

    # --- rooms ----------------------------------------------------------

    def snapshot(self):
        # Shallow: an actor's record must stay the very Placement the scene
        # holds - the scene finds its actor by that identity. What a run
        # changes on an actor is reassigned, never mutated in place.
        import copy
        return (bytes(self.mem.ram), bytes(self.mem.scratch),
                [copy.copy(a) for a in self.actors], self.heap, dict(self.files),
                dict(self.trail_slots), collections.Counter(self.pool_used))

    def restore(self, saved):
        import copy
        ram, scratch, actors, heap, files, trail_slots, pool_used = saved
        self.pool_used = collections.Counter(pool_used)
        self.mem.ram[:] = ram
        self.mem.scratch[:] = scratch
        self.actors = [copy.copy(a) for a in actors]
        self.by_address = {a.address: a for a in self.actors}
        self.heap, self.files = heap, dict(files)
        self.trail_slots = dict(trail_slots)
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
        None if there is no such table. Scene 0 is the area itself, for an
        area whose spawner stands everything up (the outro)."""
        write, read = self.mem.write, self.mem.read
        if scene:
            write(INTERIOR, 1, scene - 1)
            write(INSIDE, 1, 1)
            write(TRANSITION, 1, 2)
            enter = read(ENTER_INTERIOR + area_number * 4, 4)
            if self._code(enter):
                self.entering = True
                try:
                    self.cpu.call(enter, (), budget=budget, sp=STACK)
                except EmuError:
                    pass
                finally:
                    self.entering = False
        controller = self._alloc(ACTOR_SIZE + MAX_PARTS * 4)
        first = len(self.actors)
        self.running, self.running_scene = None, scene or None
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
        made.extend(self._spawn_gated(spawner, controller, scene, made, budget))
        return made

    def scene_records(self, spawner, scene):
        """[(kind, variant, slot, reward, x, y, z, condition, argument,
        handler)] of one scene's table, found through the spawner's own
        reference to its table of lists, or [] if none reads right."""
        read = self.mem.read
        best = []
        for table in self._spawner_tables(spawner):
            pointer = read(table + scene * 4, 4)
            records = self._records_at(pointer)
            if records is not None and len(records) > len(best):
                best = records
        return best

    def _spawner_tables(self, spawner):
        """Overlay addresses a spawner builds with lui/addiu or lui/lw in
        its first instructions - its table of scene lists among them."""
        read, registers, out = self.mem.read, {}, []
        for n in range(64):
            word = read(spawner + n * 4, 4)
            op, rs, rt, imm = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
            offset = imm - 0x10000 if imm & 0x8000 else imm
            if op == 0x0F:
                registers[rt] = imm << 16
            elif op in (0x09, 0x23) and rs in registers:
                target = (registers[rs] + offset) & 0xFFFFFFFF
                if OVERLAY_BASE <= target < AREA_BASE and target not in out:
                    out.append(target)
        return out

    def _scene_listed(self, spawner, scene):
        """False only when the spawner's own table of scene lists is found
        and holds nothing shaped like a list for `scene`: past the table's
        end the spawner walks garbage until its budget runs out - most of a
        load's instructions, before this."""
        read = self.mem.read
        # A table of scene lists: several of its first entries read as lists.
        # Its scene 0 may not (A05's starts at 1), so count rather than require.
        tables = [t for t in self._spawner_tables(spawner)
                  if sum(self._list_shaped(read(t + k * 4, 4)) for k in range(8)) >= 2]
        return not tables or any(self._list_shaped(read(t + scene * 4, 4)) for t in tables)

    def _list_shaped(self, pointer):
        """Whether `pointer` holds 16-byte scene records ending in SCENE_END,
        each handler an aligned address in code."""
        read = self.mem.read
        if not OVERLAY_BASE <= pointer < AREA_BASE:
            return False
        for n in range(64):
            if read(pointer + n * SCENE_RECORD, 1) == SCENE_END:
                return True
            handler = read(pointer + n * SCENE_RECORD + 12, 4)
            if handler & 3 or not (0x80010000 <= handler < EXE_END
                                   or OVERLAY_BASE <= handler < AREA_BASE):
                return False
        return False

    def _records_at(self, pointer):
        read = self.mem.read
        if not OVERLAY_BASE <= pointer < AREA_BASE:
            return None
        records = []
        while read(pointer, 1) != SCENE_END:
            if len(records) >= 64:
                return None
            raw = self.mem.bytes(pointer, SCENE_RECORD)
            kind, variant, slot, reward = raw[:4]
            x, y, z = struct.unpack_from("<3h", raw, 4)
            handler = struct.unpack_from("<I", raw, 12)[0]
            if not self._code(handler):
                return None
            records.append((kind, variant, slot, reward, x, y, z, raw[10], raw[11], handler))
            pointer += SCENE_RECORD
        return records

    def _spawn_gated(self, spawner, controller, scene, made, budget, moved=False):
        """The scene's records its spawner skipped - their condition does
        not hold in a fresh game - allocated the way the spawner allocates
        (f_AllocateActor, then position, reward and handler), marked gated.
        `moved`: `made` have run, so they are matched by what they were
        born as rather than where they stand."""
        read, write = self.mem.read, self.mem.write
        taken = {(read(a.address + CALLBACK, 4), read(a.address + SLOT, 1),
                  s16(read(a.address + POSITION + 2, 2)),
                  s16(read(a.address + POSITION + 10, 2))) for a in made}
        left = collections.Counter(
            a.born or (read(a.address + CALLBACK, 4), read(a.address + SLOT, 1))
            for a in made) if moved else None
        out = []
        for kind, variant, slot, reward, x, y, z, condition, argument, handler in (
                self.scene_records(spawner, scene)):
            if moved:
                if left[(handler, reward)] > 0:
                    left[(handler, reward)] -= 1
                    continue
            elif (handler, reward, x, z) in taken:
                continue
            # Byte 11 is 1 for a record every spawner skips in a purified
            # area: not gated there, just not in this variant.
            if argument == ARG_CURSED_ONLY and self.purified:
                continue
            first = len(self.actors)
            self.running_scene = scene or None
            try:
                self.cpu.call(ALLOCATE_ACTOR, (controller, kind, variant, slot),
                              budget=budget, sp=STACK)
            except EmuError:
                continue
            finally:
                self.running_scene = None
            for actor in self.actors[first:first + 1]:
                address = actor.address
                write(address + POSITION, 4, (x << 16) & 0xFFFFFFFF)
                write(address + POSITION + 4, 4, (y << 16) & 0xFFFFFFFF)
                write(address + POSITION + 8, 4, (z << 16) & 0xFFFFFFFF)
                for at in (TURN, TURN + 2, TURN + 4):
                    write(address + at, 2, 0)
                write(address + SLOT, 1, reward)
                write(address + CALLBACK, 4, handler)
                actor.gated = (condition, argument)
                actor.scene = scene or None
                out.append(actor)
        return out

    def spawn_area_gated(self, spawner, frames=FRAMES, budget=BUDGET):
        """Scene 0's records a fresh game skips - cursed Donglin's ghost
        guardians - stood up gated beside the area's own actors, and run."""
        controller = self._alloc(ACTOR_SIZE + MAX_PARTS * 4)
        # Shown beside actors the game never holds at once, so they count
        # against pools of their own rather than the full area's.
        self.pool_used = collections.Counter()
        gated = self._spawn_gated(spawner, controller, 0, list(self.actors),
                                  budget, moved=True)
        chosen = {a.address for a in gated}
        if chosen:
            self.run(frames, budget, only=lambda a: a.address in chosen,
                     workers=False)
        return gated

    def run_rooms(self, spawner, area_number, frames=FRAMES):
        """Every room the area's scene spawner has a table for, each run
        from the state the area opened in, into `rooms`."""
        base = self.snapshot()
        rooms = {}
        for scene in range(1, MAX_SCENES):
            if not self._scene_listed(spawner, scene):
                continue
            self.restore(base)
            # The area's actors are gone once a room is entered, and their
            # records with them: the room gets the pools to itself.
            self.pool_used = collections.Counter()
            if self.enter_scene(spawner, area_number, scene) is None:
                continue
            self.run(frames, only=lambda a, s=scene: a.scene == s,
                     workers=False)
            self.harvest()
            rooms[scene] = [a for a in self.actors if a.scene == scene]
            self.room_trails[scene] = dict(self.trail_slots)
        self.restore(base)
        self.rooms = rooms

    def spawn_events(self, handlers, frames=FRAMES, budget=BUDGET, reach=UNPLACED):
        """Each of `handlers` no run reached, stood up alone on a bare record
        in the area as it opened, into `events` when what it built stands
        `reach` or more from the origin in x or z - an event's actor, placed
        by its own code (A0F FUN_A0F__80117fa4: petrified Tabby, stood up as
        the Evil Pig falls). The world is left as it was."""
        seen = set()
        for actors in (self.actors, *self.rooms.values()):
            seen |= {a.handler for a in actors} | {a.born[0] for a in actors if a.born}
        base = self.snapshot()
        events = []
        for handler in sorted(set(handlers) - seen):
            self.restore(base)
            self.actors, self.by_address, self.running = [], {}, None
            self.pool_used = collections.Counter()
            address = self._alloc(ACTOR_SIZE + MAX_PARTS * 4)
            self.mem.load(address, bytes(ACTOR_SIZE + MAX_PARTS * 4))
            self.mem.write(address + CALLBACK, 4, handler)
            root = Actor(address, handler)
            self.actors.append(root)
            self.by_address[address] = root
            self.run(frames, budget=budget, only=lambda a, r=root: a is r, workers=False)
            # One whose code faults keeps what it built, as in run().
            tree = [a for a in subtree(self, root) if not a.discarded]
            if any((a.parts or a.frames) and a.position is not None
                   and max(abs(a.position[0]), abs(a.position[2])) >= max(reach, 1)
                   for a in tree):
                events.append((handler, tree))
        self.restore(base)
        self.events = events

    def capture_lines(self, actors=None, budget=BUDGET):
        """Run each actor's draw routine (+0x18) and then its update handler
        once with the GTE naming its vertices, and keep the line primitives
        that come out, on the actor - A00's ropes come from the first, A06's
        (f_UpdateLaughingCryingDoorTriggerActor) from the second. Leaves RAM
        as it was."""
        mem, cpu = self.mem, self.cpu
        ram, scratch, heap, count = bytes(mem.ram), bytes(mem.scratch), self.heap, len(self.actors)
        found = {}
        held = {address: cpu.hooks.get(address) for address in PART_DRAWERS}
        cpu.hooks.update({address: _draw_nothing for address in PART_DRAWERS})
        try:
            ot, primitives = self._alloc(OT_SLOTS * 4), self._alloc(PRIMITIVE_BYTES)
            queue = self._alloc(4)
            mem.load(CAMERA, struct.pack("<9h2x3i", 0x1000, 0, 0, 0, 0x1000, 0,
                                         0, 0, 0x1000, 0, 0, 0))
            mem.write(ORDERING_TABLE, 4, ot)
            mem.write(ANIMATION_FRAME, 2, 0)

            def draw(actor, offset, routine):
                """([line], [textured polygon]) one pass puts out."""
                mem.load(ot, bytes(OT_SLOTS * 4))
                mem.write(PRIMITIVE_CURSOR, 4, primitives)
                if offset == CALLBACK:
                    mem.write(actor.address + ACTIVE, 1, 0)
                    mem.write(PART_BUDGET, 2, PART_BUDGET_HELD)
                elif offset is None:
                    mem.write(queue, 4, actor.address)
                    mem.write(actor.address + ACTIVE, 1, 1)
                    mem.write(QUEUE_HELD, 1, 1)
                    mem.write(QUEUE_COUNT, 2, 1)
                    mem.write(QUEUE_LIST, 4, queue)
                cpu.gte.capture = {}
                self.running = actor.address
                try:
                    cpu.call(routine, (actor.address, 0, 0), budget=budget, sp=STACK)
                except EmuError:
                    pass
                finally:
                    self.running = None
                return self._read_primitives(ot, cpu.gte.capture)

            for actor in list(self.actors if actors is None else actors):
                lines, polys, passes, painters = [], [], [], []
                for offset in (DRAW, CALLBACK):
                    routine = mem.read(actor.address + offset, 4)
                    if self._code(routine) and not (offset == CALLBACK and actor.dead):
                        passes.append((offset, routine))
                if mem.read(actor.address + RENDER_KIND, 1) in QUEUED_KINDS:
                    passes.append((None, CLASS4_QUEUE))
                # Only a drawer that reads the frame counter is probed for UV steps.
                counted = [False]
                if actor.uv_frames is None:
                    def watched(address, size, read=type(mem).read, counted=counted):
                        if 0 <= (address & 0x1FFFFFFF) - ANIMATION_FRAME < 2:
                            counted[0] = True
                        return read(mem, address, size)
                    mem.read = watched
                for offset, routine in passes:
                    # A pass that has drawn nothing twice since its actor ran
                    # is not run again: re-reads repeat it for every actor.
                    quiet = actor.silent.get(offset, 0)
                    if quiet is not None and quiet >= QUIET_CAPTURES:
                        continue
                    drawn_lines, drawn_polys = draw(actor, offset, routine)
                    lines.extend(drawn_lines)
                    polys.extend(drawn_polys)
                    if drawn_polys:
                        painters.append((offset, routine))
                    if drawn_lines or drawn_polys:
                        actor.silent[offset] = None
                    elif actor.ran and quiet is not None:
                        actor.silent[offset] = quiet + 1
                mem.__dict__.pop("read", None)
                if painters and actor.uv_frames is None:
                    actor.uv_frames = self._uv_frames(
                        lambda: [p for o, r in painters for p in draw(actor, o, r)[1]]
                    ) if counted[0] else {}
                if (lines or polys) and actor.position is not None:
                    # Drawn frames after the pose was read: moved back with the
                    # actor, and a point nowhere near it is a misread vertex.
                    now = self._position(actor.address)
                    shift = actor.position - now

                    def near(point, reach=ACTOR_REACH):
                        return np.max(np.abs(point - actor.position)) <= reach

                    moved = []
                    for a, b, color_a, color_b, blended in lines:
                        a, b = np.asarray(a) + shift, np.asarray(b) + shift
                        if near(a) and near(b):
                            moved.append((tuple(a), tuple(b), color_a, color_b, blended))
                    lines = moved
                    moved = []
                    for corners, uvs, colours, clut, blended, page in polys:
                        corners = [np.asarray(c) + shift for c in corners]
                        if all(near(c, POLY_REACH) for c in corners):
                            moved.append((tuple(tuple(c) for c in corners), uvs, colours,
                                          clut, blended, page))
                    polys = moved
                if lines or polys:
                    found[id(actor)] = (actor, lines, polys)
        except EmuError:
            pass
        finally:
            mem.__dict__.pop("read", None)
            for address, hook in held.items():
                if hook is None:
                    cpu.hooks.pop(address, None)
                else:
                    cpu.hooks[address] = hook
            cpu.gte.capture = None
            mem.ram[:] = ram
            mem.scratch[:] = scratch
            self.heap = heap
            for extra in self.actors[count:]:
                self.by_address.pop(extra.address, None)
            del self.actors[count:]
            self._map_cache = None
        for actor in (self.actors if actors is None else actors):
            actor.lines = tuple(found[id(actor)][1]) if id(actor) in found else ()
            actor.polys = tuple(found[id(actor)][2]) if id(actor) in found else ()
        return len(found)

    def _uv_frames(self, paint):
        """{CLUT word: ((du, dv), ...)} - one period, a frame each, of how the
        UVs of the polygons `paint` puts out move together as ANIMATION_FRAME
        counts from 0. Every frame is drawn from the same RAM, put back after."""
        mem = self.mem
        ram, scratch = bytes(mem.ram), bytes(mem.scratch)

        def at(frame):
            mem.ram[:] = ram
            mem.scratch[:] = scratch
            mem.write(ANIMATION_FRAME, 2, frame)
            return paint()

        try:
            base = at(0)
            still = [p[1] for p in base]
            if all([p[1] for p in at(f)] == still for f in FRAME_PROBES):
                return {}
            frames = [base] + [at(f) for f in range(1, FRAME_CYCLE)]
        finally:
            mem.ram[:] = ram
            mem.scratch[:] = scratch
        moves = {}                      # CLUT word -> [{(du, dv)} per frame]
        for polys in frames:
            if len(polys) != len(base) or any(p[3] != b[3] for p, b in zip(polys, base)):
                return {}
            now = {}
            for poly, first in zip(polys, base):
                now.setdefault(first[3], set()).update(
                    (u - u0, v - v0) for (u, v), (u0, v0) in zip(poly[1], first[1]))
            for clut, found in now.items():
                moves.setdefault(clut, []).append(found)
        out = {}
        for clut, per_frame in moves.items():
            if any(len(found) != 1 for found in per_frame):
                continue
            steps = [next(iter(found)) for found in per_frame]
            if all(step == (0, 0) for step in steps):
                continue
            period = next(p for p in range(1, FRAME_CYCLE + 1) if FRAME_CYCLE % p == 0
                          and all(steps[k] == steps[k % p] for k in range(FRAME_CYCLE)))
            out[clut] = tuple(steps[:period])
        return out

    def _read_primitives(self, ot, points):
        """([line], [textured polygon]) linked into the ordering table."""
        out, polys = [], []
        for head in struct.unpack(f"<{OT_SLOTS}I", self.mem.bytes(ot, OT_SLOTS * 4)):
            address, walked = head & 0xFFFFFF, 0
            while address and address != 0xFFFFFF and walked < 4096:
                walked += 1
                base = 0x80000000 | address
                try:
                    tag = self.mem.read(base, 4)
                    length = tag >> 24
                    words = struct.unpack(f"<{length}I", self.mem.bytes(base + 4, length * 4))
                except EmuError:
                    break
                out.extend(line_primitives(words, points))
                polys.extend(textured_primitives(words, points))
                address = tag & 0xFFFFFF
        return out, polys

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
            source = (TRAIL_ID + self.trail_slots[file_id]
                      if file_id in self.trail_slots else file_id)
            count = first // 4 - 1
            for group in range(count):
                offset = read(address + 4 + group * 4, 4)
                if 0 < offset < size:
                    out.setdefault(address + offset, (source, group))
        return out

    def banks(self):
        """{address: file id} for the loaded sprite banks - +0x3C holds an
        ANMP on a character, so only a SPRT counts."""
        import zlib
        from functions import format_detect
        out = {}
        for file_id, (address, size) in self.files.items():
            data = self.mem.bytes(address, size)
            key = (size, zlib.crc32(data))
            if key not in FILE_KINDS:
                found = format_detect.best(data)
                FILE_KINDS[key] = found.kind if found is not None else None
            if FILE_KINDS[key] == "SPRT":
                out[address] = file_id
        return out

    def _maps(self):
        if getattr(self, "_map_cache", None) is None:
            self._map_cache = (self.models(), self.banks())
        return self._map_cache

    def _place(self, actor):
        read = self.mem.read
        actor.position = self._position(actor.address)
        if actor.quad is not None and not actor.position.any():
            # Drawn only where its corners are.
            actor.position = np.mean(actor.quad, axis=0)
        actor.reward = read(actor.address + SLOT, 1)

    def _position(self, address):
        read = self.mem.read
        if read(address + CLASS, 1) == EFFECT_CLASS:
            # f_SpawnTransientEffect*: whole shorts at +0x2C/+0x2E/+0x30.
            return np.array([float(s16(read(address + POSITION + k * 2, 2)))
                             for k in range(3)])
        return np.array([s32(read(address + POSITION + k * 4, 4)) / 65536.0
                         for k in range(3)])

    def _parts(self, actor, models):
        """Every part with a model. One its code never got round to
        posing - an actor stopped before its first update - is stood in
        its rest pose off its parent chain rather than left out."""
        from functions.actor_assembly import rot_xyz
        read, mem, a = self.mem.read, self.mem, actor.address
        count = read(a + PART_COUNT, 1)
        drawn = read(a + PART_FRAME, 1)
        # f_BuildActorModelPartPrimitives draws the first +0x08 of the +0x09
        # parts; a door that sets +0x08 to 0 draws nothing at all.
        actor.hidden = bool(count and not drawn)
        actor.waiting = actor.ran and read(a + LIFECYCLE, 1) == 0
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
        if bank is not None:
            actor.sprite_bank = bank
        if bank is not None and step:
            actor.bank = bank
            actor.frames, actor.loops = self._sequence(step)
        if read(actor.address + RENDER_KIND, 1) == QUAD_KIND:
            shorts = struct.unpack("<12h", self.mem.bytes(actor.address + QUAD_CORNERS, 24))
            corners = tuple(np.array(shorts[k:k + 3], dtype=np.float64) for k in range(0, 12, 3))
            actor.quad = corners if any(c.any() for c in corners) else None

    def harvest(self):
        """One last reading, for actors that never ran a frame."""
        self._map_cache = None
        self._read()

    def _sequence(self, address):
        """((frame, ticks), ...) and whether it loops, stepped the way
        f_AdvanceActorTimedFrameSequence steps it: 0x0000 moves on to the
        next pair, 0x4000 and 0xC000 jump through the pointer after it,
        0x8000 holds - and so does a step of 0 ticks, which never counts
        down to its end."""
        read, frames, seen = self.mem.read, [], set()
        while len(frames) < MAX_STEPS and address:
            if address in seen:
                return tuple(frames), True
            seen.add(address)
            frame, control = read(address, 2), read(address + 2, 2)
            frames.append((frame, control & TICKS))
            opcode = control & OPCODE
            if opcode == HOLD or not control & TICKS:
                break
            if opcode in (JUMP, JUMP_LOOP):
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
    quad: tuple = None              # four corners, game axes, for a kind-0x14 quad


class Posed:
    """What one actor and the children near it drew, as the simulation
    left them, moved rigidly with the actor from then on - turned about
    its own position by however far the editor turns it."""

    def __init__(self, name, note, pieces, riders, position, yaw, owners=None):
        from functions.actor_assembly import rot_y
        self._rot_y = rot_y
        self.name, self.note = name, note
        self.pieces = list(pieces)
        self.riders = list(riders)
        self.sources = tuple(p.source for p in self.pieces)
        self.origin = np.asarray(position, dtype=np.float64)
        self.yaw = yaw
        # Per piece, who drew it: (actor number, handler name, position).
        self.owners = list(owners or ())

    def owner_position(self, state, number):
        """Where the actor that drew piece `number` stands, moved with the
        rest."""
        turn, base = self._move(state)
        return turn @ (np.asarray(self.owners[number][2], dtype=np.float64)
                       - self.origin) + base

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
        return [Rider(r.label, r.reward, turn @ (r.position - self.origin) + base, r.art,
                      tuple(turn @ (c - self.origin) + base for c in r.quad)
                      if r.quad is not None else None)
                for r in self.riders]

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
             records, units, frames=FRAMES, spawner=None, purified=False,
             chests=(), finished=(), log=None):
    """The area as it opens - its placed actors, its chests and all they
    spawned - and, given the area's scene spawner, every room it has. An
    area with no placement records is its spawner's scene 0.

    `log(message, actors)` hears each stage as it starts and what it stood up."""
    say = log or (lambda _message, _actors=None: None)
    world = World(exe_path, overlay_path, dat_path, idx_path, chunk, area_number,
                  purified=purified, finished=finished)
    world.start_workers()
    say(f"placing {len(records)} record(s)")
    for record in records:
        world.place(record, units)
    if not records and spawner:
        say("no records: entering scene 0")
        world.enter_scene(spawner, area_number, 0)
    say(f"running the area, {frames} frame(s)")
    world.run(frames)
    say("the area stood up", world.actors)
    # An area's controller stands part of the chest table up itself; the
    # rest are stood up here, the way f_SpawnPersistentPickupPlacementTable does.
    missing = world.tag_chests(chests)
    placed = {a.address for a in (world.place_chest(p) for p in missing) if a is not None}
    if placed:
        say(f"running {len(placed)} chest(s) the area didn't place")
        world.run(frames, only=lambda a: a.address in placed, workers=False)
    if records and spawner:
        say("running scene 0's gated actors")
        gated = world.spawn_area_gated(spawner, frames)
        say("gated", gated)
    world.harvest()
    if spawner:
        say("running every room")
        world.run_rooms(spawner, area_number, frames)
        for scene, actors in sorted(world.rooms.items()):
            say(f"room {scene}", actors)
    say("looking for event actors")
    # An area with no table (the intro) is built round the origin by code.
    with open(overlay_path, "rb") as f:
        world.spawn_events(installed_handlers(f.read()), frames,
                           reach=UNPLACED if records or spawner else 1)
    say(f"{len(world.events)} event actor(s)", [a for _h, tree in world.events for a in tree])
    return world


def installed_handlers(overlay):
    """Every routine of `overlay` its own code writes into an actor's
    callback (+0x1C): a lui/addiu or lui/ori constant, stored there."""
    count = len(overlay) // 4
    words = struct.unpack(f"<{count}I", overlay[:count * 4])
    end = OVERLAY_BASE + count * 4
    consts, found = {}, set()
    for w in words:
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if w == JR_RA:
            consts = {}
        elif op == LUI:
            consts[rt] = imm << 16
        elif op in (ADDIU, ORI) and rs in consts:
            consts[rt] = ((consts[rs] | imm) if op == ORI
                          else (consts[rs] + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF)
        elif op == SW:
            value = consts.get(rt)
            at = (value - OVERLAY_BASE) // 4 if value is not None else -1
            if (imm == CALLBACK and OVERLAY_BASE <= (value or 0) < end and not value & 3
                    and any(w2 >> 16 == PROLOGUE for w2 in words[at:at + PROLOGUE_REACH])):
                found.add(value)
        elif op == 0:
            consts.pop((w >> 11) & 31, None)
        elif 0x08 <= op <= 0x0E or 0x20 <= op <= 0x26:
            consts.pop(rt, None)
    return found


def _tested(words, n, register):
    """Whether the value the load at `n` put in `register` is next tested -
    compared, masked or thresholded - rather than indexed or added with:
    0xFF finishes an event, it overruns a table."""
    for w in words[n + 1:n + 1 + TEST_LOOKAHEAD]:
        op, rs, rt, funct = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0x3F
        if op in TESTS and (rs == register or (op in (0x04, 0x05) and rt == register)):
            return True
        if op == 0x01 and rs == register:               # bltz / bgez
            return True
        if op == 0 and register in (rs, rt):
            if funct in (0x2A, 0x2B):                   # slt / sltu
                return True
            if funct in (0x21, 0x25) and 0 in (rs, rt):  # a move
                register = (w >> 11) & 31
                continue
            return False
        if op == 0 and (w >> 11) & 31 == register:
            return False
        if (op in LOAD_SIZES or 0x08 <= op <= 0x0F) and rt == register:
            return False
        if rs == register:
            return False
    return False


def progress_bytes(overlay):
    """The New Game block bytes an overlay's code loads (lui-based lb/lh/lw
    and unsigned twins) and then tests, less WORLD_BYTES: what `finished`
    sets to DONE."""
    words = struct.unpack_from(f"<{len(overlay) // 4}I", overlay)
    high, found = {}, set()
    for n, w in enumerate(words):
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == 0x0F:
            high[rt] = imm << 16
            continue
        if op in LOAD_SIZES and rs in high:
            address = (high[rs] + s16(imm)) & 0xFFFFFFFF
            if (SAVE_BLOCK <= address < SAVE_BLOCK + SAVE_BLOCK_SIZE
                    and _tested(words, n, rt)):
                found.update(range(address, address + LOAD_SIZES[op]))
        # Whatever else writes a register loses its high half.
        if op in LOAD_SIZES or 0x08 <= op <= 0x0E:
            high.pop(rt, None)
        elif op == 0 and 0x20 <= (w & 0x3F) <= 0x2B:
            high.pop((w >> 11) & 31, None)
    return sorted(found - WORLD_BYTES)


def line_primitives(words, points):
    """[(a, b, colour a, colour b, blended)] for the line primitives in one
    packet's words, joining the vertices `points` names."""
    out, k = [], 0
    while k < len(words):
        code = words[k] >> 24
        if code == 0 or 0xE1 <= code <= 0xE6:
            k += 1
            continue
        if not 0x40 <= code <= 0x5F:
            break
        gouraud, poly, blended = code & 0x10, code & 0x08, bool(code & 0x02)
        colour, k, vertices = words[k] & 0xFFFFFF, k + 1, []
        while k < len(words):
            if poly and (words[k] & TERMINATOR_MASK) == TERMINATOR:
                k += 1
                break
            if gouraud and vertices:
                colour, k = words[k] & 0xFFFFFF, k + 1
                if k >= len(words):
                    break
            vertices.append((words[k], colour))
            k += 1
            if not poly and len(vertices) == 2:
                break
        known = [(points[_xy(w)], c) for w, c in vertices if _xy(w) in points]
        for (a, ca), (b, cb) in zip(known, known[1:]):
            if max(abs(p - q) for p, q in zip(a, b)) <= LINE_REACH:
                out.append((a, b, _rgb(ca), _rgb(cb), blended))
    return out


def _draw_nothing(cpu):
    return 0


def textured_primitives(words, points):
    """[(corners, uvs, colours, CLUT word, blended, page word)] for the
    textured polygons (FT3/FT4/GT3/GT4) in one packet's words, the corners
    the points `points` names. The page word is the second UV's pad: the
    game writes the texture page attribute there (A01's chains +0x60, A0E's
    waterfall 0x2D)."""
    out, k = [], 0
    while k < len(words):
        code = words[k] >> 24
        if code == 0 or 0xE1 <= code <= 0xE6:
            k += 1
            continue
        if not (0x20 <= code <= 0x3F and code & 0x04):
            break
        gouraud, corners_wanted = code & 0x10, 4 if code & 0x08 else 3
        colour, k = words[k] & 0xFFFFFF, k + 1
        corners, uvs, colours, clut, page = [], [], [], 0, 0
        for corner in range(corners_wanted):
            if gouraud and corner:
                if k >= len(words):
                    return out
                colour, k = words[k] & 0xFFFFFF, k + 1
            if k + 1 >= len(words):
                return out
            xy, uv = words[k], words[k + 1]
            k += 2
            if not corner:
                clut = uv >> 16
            elif corner == 1:
                page = uv >> 16
            corners.append(points.get(_xy(xy)))
            uvs.append((uv & 0xFF, (uv >> 8) & 0xFF))
            colours.append((1.0, 1.0, 1.0) if code & 0x01 else
                           tuple(((colour >> shift) & 0xFF) / NEUTRAL for shift in (0, 8, 16)))
        # CLUT 0 names the top left of VRAM, which is the display, not a
        # palette; a polygon folded to a line or a point covers nothing.
        if (clut and all(c is not None for c in corners)
                and len(set(corners)) >= 3) and all(
                max(abs(p - q) for p, q in zip(a, b)) <= LINE_REACH
                for a in corners for b in corners):
            out.append((tuple(corners), tuple(uvs), tuple(colours), clut, bool(code & 0x02),
                        page))
    return out


def _xy(word):
    return s16(word), s16(word >> 16)


def _rgb(colour):
    return tuple(((colour >> shift) & 0xFF) / 255.0 for shift in (0, 8, 16))


def find_scene_spawner(overlay):
    """An overlay's scene spawner found by what its controller does - see
    RETIRE_SCENE - or None."""
    count = len(overlay) // 4
    words = struct.unpack_from(f"<{count}I", overlay)
    starts = [n for n, w in enumerate(words) if w >> 16 == PROLOGUE and w & 0x8000]
    bodies = {OVERLAY_BASE + s * 4: words[s:e]
              for s, e in zip(starts, starts[1:] + [count])}

    def calls(body):
        return [(w & 0x3FFFFFF) << 2 | 0x80000000 for w in body if w >> 26 == 3]

    for body in bodies.values():
        called = calls(body)
        if RETIRE_SCENE not in called:
            continue
        for target in sorted(set(called)):
            if (called.count(target) >= 2 and target in bodies
                    and ALLOCATE_ACTOR in calls(bodies[target])):
                return target
    return None

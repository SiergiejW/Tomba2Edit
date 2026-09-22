"""Props the code assembles part by part, and what they spawn.

functions/actor_models.py reads the one builder call most characters go
through. Some props skip it: their init allocates every part itself,
fills parent, offset and turn out of a table of its own, and then spawns
more actors that hang off those parts - an egg on a plank, an apple on a
barrel. Nothing generic finds that, so each one is read here the way its
own routine reads it. Addresses are A00.BIN (Town of the Fishermen) in
the US retail build, named after the US decomp.

A PART is the block f_AllocateActorModelPart hands out:

    +0  i16 x, y, z     offset on its parent
    +6  i16 parent      -1 for the actor itself
    +8  i16 rx, ry, rz  its own turn, 4096 to a circle
    +18 3x3 matrix      \  what the transform routines write
    +2C i32 x, y, z     /  world position
    +38 i16 scale x, y, z

Every transform routine composes them the same way,

    M = parent.M @ R(turn)          T = parent.M @ offset + parent.T

(MulMatrix0 leaves its first matrix in the GTE, which is what the
following ApplyRotMatrix turns the offset by), and they differ only in
how R is built: RotMatrix gives Rx.Ry.Rz, while RotMatrixZYX and the
RotMatrixX/Y/Z sequence give Rz.Ry.Rx. All in the game's own axes.
"""
import math
import struct
from dataclasses import dataclass, field

import numpy as np

from functions import game_build

OVERLAY_BASE = 0x80108F9C

ANGLE_UNITS = 4096


def _trig(angle):
    radians = (angle % ANGLE_UNITS) * 2.0 * math.pi / ANGLE_UNITS
    return math.cos(radians), math.sin(radians)


def rot_x(angle):
    c, s = _trig(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_y(angle):
    c, s = _trig(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_z(angle):
    c, s = _trig(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rot_xyz(turn):
    """RotMatrix / RotMatrix_gte."""
    return rot_x(turn[0]) @ rot_y(turn[1]) @ rot_z(turn[2])


def rot_zyx(turn):
    """RotMatrixZYX, and RotMatrixX then Y then Z on an identity."""
    return rot_z(turn[2]) @ rot_y(turn[1]) @ rot_x(turn[0])


def rcos(angle):
    return int(round(_trig(angle)[0] * ANGLE_UNITS))


def rsin(angle):
    return int(round(_trig(angle)[1] * ANGLE_UNITS))


def degrees_to_units(degrees):
    """What the placement spawner does to a record's angle: times 4096,
    divided by 360 towards zero, kept to 12 bits."""
    return int(int(round(degrees)) * ANGLE_UNITS / 360) & (ANGLE_UNITS - 1)


@dataclass
class Part:
    parent: int
    offset: list
    turn: list
    source: tuple                   # (file id, group)
    scale: tuple = (1.0, 1.0, 1.0)


@dataclass
class Sprite:
    """A pickup an assembly hangs somewhere on itself."""
    label: str
    reward: int
    position: np.ndarray            # world, game axes


@dataclass
class Prop:
    """Something an assembly spawns that stands on its own - it does not
    follow the actor that made it."""
    label: str
    sources: tuple
    pieces: list                    # [(matrix, world position)]
    note = ""

    @property
    def name(self):
        return self.label

    def pose(self, _state=None):
        return self.pieces

    def sprites(self, _state=None):
        return []

    def props(self):
        return []


@dataclass
class State:
    """Where an actor stands, in the game's axes and angle units."""
    position: np.ndarray
    yaw: int
    roll: int = 0


class Assembly:
    """One placed actor built out of several parts.

    `sources` never changes; `pose(state)` gives a (matrix, position)
    per source for wherever the actor stands now, so moving or turning
    it in the editor re-runs the same arithmetic the game does."""

    name = ""
    note = ""

    def __init__(self, sources):
        self.sources = tuple(sources)

    def pose(self, state):
        raise NotImplementedError

    def sprites(self, state):
        return []

    def props(self):
        return []


def _u8(data, address):
    return data[address - OVERLAY_BASE]


def _unpack(data, fmt, address):
    at = address - OVERLAY_BASE
    if at < 0 or at + struct.calcsize(fmt) > len(data):
        raise ValueError(f"0x{address:08X} is outside the overlay")
    return struct.unpack_from(fmt, data, at)


def _chain(parts, root_m, root_t, rotation):
    """World (matrix, position) per part, parents first."""
    out = []
    for part in parts:
        scale = np.diag(part.scale)
        r = rotation(part.turn) @ scale
        offset = np.asarray(part.offset, dtype=np.float64)
        if 0 <= part.parent < len(out):
            pm, pt = out[part.parent]
        else:
            pm, pt = root_m, root_t
        out.append((pm @ r, pm @ offset + pt))
    return out


# --- the barrel seesaws -------------------------------------------------
#
# f_HandleTownBarrelSeesawController -> f_InitializeTownBarrelSeesawController.
# The slot is the actor's reward_type and picks one of ten configs; the
# config's flags pick the layout:
#
#   bit 0   a plank on a post with a barrel at each end (else a single
#           arm, 3 or 7 parts)
#   bit 1   both barrels (12 parts rather than 7)
#   bit 2   lidded barrels - group 1 rather than 0 - and, on the single
#           arm, the four lid halves
#   bit 3   leans the post 0xAA
#   0x0F0   which post: group 5 + n
#   0xF00   which plank: group 2 + n
#
# then the plank tilts to the config's low stop and the barrels are
# pushed out along it by span / cos(tilt), which keeps them under where
# they hang level (f_UpdateBarrelSeesawSupportHeight).

SEESAW_HANDLER = 0x8012EB54
SEESAW_FILE = 21                    # fileTable[0x54 / 4], DAT_800ecfac
SEESAW_VARIANT = 0x8014A334         # u8 per slot
SEESAW_CONFIG = 0x8014A340          # i16 flags, effect, low, high, spare
SEESAW_TWIN = 0x8014A274            # 12 x i16 parent, x, y, z, group
SEESAW_SINGLE = 0x8014A2EC          # 7 x the same
SEESAW_SLOTS = 12
SEESAW_SPAN = 0x2DF

# What each seesaw spawns off itself.
ASSET_PACK = 12
EGG_GROUP, NEST_GROUP = 0x50, 0x51  # f_InitializeCarriedChickActor
EGG_RISE, EGG_LIFT = -60, -122
PIPE_FILE = 28                      # DAT_800ecfc8
PIPE_COUNTS = 0x8014A9A4            # u8 per slot
PIPE_LAYOUTS = 0x8014A994           # u32 per slot, 7 x i16 per part
PIPE_SLOTS = 3
PLANT_SPOTS = 0x8014A6B4            # i16 x, y, z per plant
PLANT_SQUASH = (0xC00 / 4096, 0x999 / 4096, 0xC00 / 4096)
INDEXED_PICKUPS = 0x801485BC        # u8 behaviour, u8 reward, i16 bit
APPLE_PICKUP = {0: 0, 1: 6}         # slot -> f_SpawnAreaIndexedPersistentPickupOnce
APPLE_BEHAVIOUR = 6                 # f_UpdateA00PickupHandlerCase6
BLUE_APPLE_BIT = 200
APPLE_RISE = -0x8C


class Seesaw(Assembly):
    name = "barrel seesaw"

    def __init__(self, data, record):
        self.slot = record.slot
        variant = _u8(data, SEESAW_VARIANT + self.slot)
        flags, effect, low, _high, _spare = _unpack(
            data, "<5h", SEESAW_CONFIG + variant * 10)
        self.flags, self.effect = flags & 0xFFFF, effect & 0xFFFF
        self.twin = bool(self.flags & 1)
        self.parts = self._parts(data, low)
        self.pipe = self._pipe(data)
        self.apple = self._apple(data)
        self.plants = self._plants(data)
        sources = [p.source for p in self.parts]
        if self.carries_egg:
            sources += [(ASSET_PACK, EGG_GROUP), (ASSET_PACK, NEST_GROUP)]
        sources += [p.source for p in self.pipe]
        super().__init__(sources)
        self.note = (f"assembled by f_InitializeTownBarrelSeesawController: "
                     f"config {variant}, flags 0x{self.flags:04X}, "
                     f"{len(self.parts)} parts of id {SEESAW_FILE}"
                     + (", an egg in its nest" if self.carries_egg else "")
                     + (f", a {len(self.pipe)}-part water pipe (id {PIPE_FILE})"
                        if self.pipe else ""))

    @property
    def carries_egg(self):
        """f_EnsureCarriedChickActor, before any chick is delivered."""
        return self.slot < 2

    def _parts(self, data, low):
        flags = self.flags
        if self.twin:
            count, table = (12 if flags & 2 else 7), SEESAW_TWIN
        else:
            count, table = (7 if flags & 4 else 3), SEESAW_SINGLE
        parts, row = [], 0
        for index in range(count):
            if self.twin and not flags & 2 and index == 3:
                row += 1               # no second barrel
            parent, x, y, z, group = _unpack(data, "<5h", table + row * 10)
            turn = [0, 0, 0]
            if row == 0:
                group = 5 + ((flags & 0xFF) >> 4)
            elif row == 1:
                group = 2 + ((flags >> 8) & 0xF)
            elif row == 2 or (self.twin and row == 3):
                group += (flags & 4) >> 2
                if row == 3:
                    turn[1] = 0x800
            parts.append(Part(parent, [x, y, z], turn, (SEESAW_FILE, group)))
            row += 1

        leaning = parts[1] if self.twin else parts[0]
        if flags & 8:
            leaning.turn[2] = 0xAA
        span = SEESAW_SPAN
        if self.slot == 3:
            parts[1].turn[0] = -0xC0
        elif self.slot == 0:
            span = 800
            parts[1].turn[1] = -170
            parts[1].turn[0] = 0xF0
        elif self.slot == 11:
            span = 0x2DE
            parts[1].turn[0] = 0
        else:
            parts[1].turn[0] = low
        parts[1].turn[0] &= 0xFFF

        # f_UpdateBarrelSeesawSupportHeight
        tilt = parts[1].turn[0]
        lift = int(span * ANGLE_UNITS / rcos(tilt)) + (abs(rsin(tilt)) >> 6)
        parts[2].offset[2] = -lift
        if flags & 2:
            parts[3].offset[2] = lift
        return parts

    def _pipe(self, data):
        """f_SpawnSegmentedTownActor -> f_InitializeSegmentedTownActor: a
        chain, each part on the one before, stood at the seesaw's own
        position plus the first offset and never turned with it."""
        if self.slot >= PIPE_SLOTS:
            return []
        count = _u8(data, PIPE_COUNTS + self.slot)
        (layout,) = _unpack(data, "<I", PIPE_LAYOUTS + self.slot * 4)
        parts = []
        for index in range(count):
            x, y, z, rx, ry, rz, group = _unpack(data, "<7h", layout + index * 14)
            parts.append(Part(index - 1, [x, y, z], [rx, ry, rz],
                              (PIPE_FILE, group)))
        return parts

    def _apple(self, data):
        index = APPLE_PICKUP.get(self.slot)
        if index is None:
            return None
        behaviour, reward, bit = _unpack(data, "<BBh",
                                         INDEXED_PICKUPS + index * 4)
        if behaviour & 0x7F != APPLE_BEHAVIOUR:
            return None
        return reward, bit

    def _plants(self, data):
        """f_SpawnAquaticPlantActors -> f_InitializeAquaticPlantActor."""
        if self.effect & 0xFF == 0xFF:
            return []
        first = self.effect & 0x7F
        wanted = [first]
        if (self.flags | (self.effect << 16)) & 0x800002 == 2:
            wanted.append(first + 1)
        out = []
        for index in wanted:
            if index > 5:
                continue
            x, y, z = _unpack(data, "<3h", PLANT_SPOTS + index * 6)
            squashed = 0 < index < 5
            group, scale = (5, PLANT_SQUASH) if squashed else (4, (1.0, 1.0, 1.0))
            out.append(Prop(f"sea plant {index}", ((ASSET_PACK, group),),
                            [(np.diag(scale), np.array([x, y, z], float))]))
        return out

    def _poses(self, state):
        yaw = state.yaw
        root_m = rot_xyz((0, yaw, state.roll))
        root_t = np.asarray(state.position, dtype=np.float64)
        leaning = self.parts[1] if self.twin else self.parts[0]
        out = []
        for index, part in enumerate(self.parts):
            offset = np.asarray(part.offset, dtype=np.float64)
            r = rot_xyz(part.turn)
            if part.parent < 0:
                out.append((root_m @ r, root_m @ offset + root_t))
                continue
            pm, pt = out[part.parent]
            if index == 2 or (index == 3 and self.flags & 2):
                # The barrels hang upright whatever the plank does.
                m = rot_y(yaw) @ rot_z(leaning.turn[2]) @ r
            else:
                m = pm @ (rot_zyx(part.turn) if self.twin else r)
            out.append((m, pm @ offset + pt))
        return out

    def pose(self, state):
        poses = self._poses(state)
        out = list(poses)
        if self.carries_egg:
            plank_t = poses[1][1]
            m = rot_xyz((0, self.parts[1].turn[1] + 0x400, 0))
            t = plank_t + np.array([0.0, EGG_RISE, 0.0])
            out.append((m, m @ np.array([0.0, EGG_LIFT, 0.0]) + t))
            out.append((m, t))
        if self.pipe:
            base = (np.asarray(state.position, dtype=np.float64)
                    + np.asarray(self.pipe[0].offset, dtype=np.float64))
            chain = [Part(p.parent, [0, 0, 0] if i == 0 else p.offset,
                          p.turn, p.source) for i, p in enumerate(self.pipe)]
            out.extend(_chain(chain, np.eye(3), base, rot_zyx))
        return out

    def sprites(self, state):
        if self.apple is None or len(self.parts) < 4:
            return []
        reward, bit = self.apple
        poses = self._poses(state)
        anchor = poses[3] if bit == BLUE_APPLE_BIT else poses[2]
        colour = "blue apple" if bit == BLUE_APPLE_BIT else "apple"
        return [Sprite(f"{colour} on seesaw {self.slot} (apple bit {bit})",
                       reward, anchor[1] + np.array([0.0, APPLE_RISE, 0.0]))]

    def props(self):
        return self.plants


# --- the fishing rod ----------------------------------------------------
#
# f_HandleRareFishOrBucketSuspension -> f_InitializeRareFishOrBucketController:
# thirteen parts of the asset pack from group 0x18, each on the one
# before and raised by a table, the last one drawn being twelve - the
# thirteenth is only where the line ends. The rod bends by giving every
# part a roll that grows along it, out of a spring the controller runs
# each frame (f_UpdateSuspendedPickupChainGeometry); it is run here until
# it settles.

ROD_HANDLER = 0x80128760
ROD_FIRST_GROUP = 0x18
ROD_PARTS = 13
ROD_DRAWN = 12
ROD_LENGTHS = 0x801498C4
ROD_YAW = {0: 0xD28, 1: 0x6A4}
ROD_REST = {0: 0x1A4, 1: 400}
ROD_PULL = 0x280
ROD_FRAMES = 1200
ROD_SETTLE = 256


def rod_rolls(rest, pull=ROD_PULL, parts=ROD_PARTS):
    delay, tension, history = 0, rest, []
    for _ in range(ROD_FRAMES):
        delay += pull
        if rest < tension:
            delay += (tension - rest) * -6
        if delay < 0:
            delay = min(delay + 100, 0)
        else:
            delay = max(delay - 100, 0)
        delay = max(-0x3C00, min(0x3C00, delay))
        tension += delay >> 8
        history.append(tension)
    tension = int(round(sum(history[-ROD_SETTLE:]) / ROD_SETTLE))
    k = ((tension * 5) >> 4) + 0x19
    return [((k * (i + 2)) >> 4) - (k >> 5) for i in range(parts)]


class FishingRod(Assembly):
    name = "fishing rod"

    def __init__(self, data, record):
        self.slot = record.slot
        lengths = _unpack(data, f"<{ROD_PARTS}h", ROD_LENGTHS)
        rolls = rod_rolls(ROD_REST.get(self.slot, ROD_REST[0]))
        self.yaw = ROD_YAW.get(self.slot)
        self.parts = [Part(i - 1, [0, lengths[i], 0], [0, 0, rolls[i]],
                           (ASSET_PACK, ROD_FIRST_GROUP + i))
                      for i in range(ROD_PARTS)]
        super().__init__(p.source for p in self.parts[:ROD_DRAWN])
        self.note = (f"assembled by f_InitializeRareFishOrBucketController: "
                     f"{ROD_DRAWN} parts of id {ASSET_PACK} from group "
                     f"{ROD_FIRST_GROUP}, bent {sum(rolls[:ROD_DRAWN])} units"
                     + (f", turned 0x{self.yaw:X} by its own code"
                        if self.yaw is not None else ""))

    def pose(self, state):
        yaw = self.yaw if self.yaw is not None else state.yaw
        root_m = rot_xyz((0, yaw, state.roll))
        return _chain(self.parts, root_m,
                      np.asarray(state.position, dtype=np.float64),
                      rot_xyz)[:ROD_DRAWN]


# --- which --------------------------------------------------------------

# US retail's A00.BIN; another build's while it is open (functions/game_build.py).
_BUILD = game_build.Addresses(globals(), main=("OVERLAY_BASE",), overlay={
    name: "A00" for name in (
        "SEESAW_HANDLER", "SEESAW_VARIANT", "SEESAW_CONFIG", "SEESAW_TWIN",
        "SEESAW_SINGLE", "PIPE_COUNTS", "PIPE_LAYOUTS", "PLANT_SPOTS",
        "INDEXED_PICKUPS", "ROD_HANDLER", "ROD_LENGTHS")})


_SLOTS = game_build.Addresses(globals(), slots=("SEESAW_FILE", "PIPE_FILE", "ASSET_PACK"))


def recipes():
    return {SEESAW_HANDLER: Seesaw, ROD_HANDLER: FishingRod}


def _looks_right(data):
    """The tables are where the US A00.BIN keeps them - any other overlay
    or build has something else at those addresses."""
    try:
        return (_unpack(data, "<5h", SEESAW_TWIN) == (-1, 0, 0, 0, 5)
                and _unpack(data, "<5h", SEESAW_SINGLE) == (-1, 0, 0, 0, 5)
                and _unpack(data, "<h", ROD_LENGTHS) == (0,))
    except ValueError:
        return False


def assemble(data, record):
    """The Assembly for one placement record, or None."""
    recipe = recipes().get(record.handler)
    if recipe is None or not data or not _looks_right(data):
        return None
    if recipe is Seesaw and not 0 <= record.slot < SEESAW_SLOTS:
        return None
    try:
        return recipe(data, record)
    except (ValueError, IndexError, struct.error, ZeroDivisionError):
        return None

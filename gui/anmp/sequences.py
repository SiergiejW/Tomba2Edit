"""Skeletal clips stored outside ANMP, decoded from the game's binaries.

US decomp: f_StartActorSkeletalAnimation (80077C40),
f_AdvanceActorSkeletalAnimation (80076D68), FUN_80076904.
Each entry is <pose, payload2, payload4, duration_and_flags> (four u16s).
Low 12 bits count ticks; 0x2000 tweens towards the next entry. High two
bits mean inline advance, indirect link, completion, or link + marker.
A link's u32 destination follows the eight-byte entry. Revisited entry
addresses identify loops, including intros which must play only once.

The payloads/events can drive actor logic; this viewer decodes skeletal
poses only. It does not run that logic or emulate fixed-point rounding.
"""
from bisect import bisect_right
from dataclasses import dataclass, field
import struct


class SequenceError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    address: int
    pose: int
    payload2: int
    payload4: int
    flags: int

    @property
    def ticks(self):
        return self.flags & 0xFFF

    @property
    def blend(self):
        return bool(self.flags & 0x2000)


@dataclass
class Clip:
    id: int
    steps: list
    loop_start: object = None
    name: str = ""
    starts: list = field(init=False)
    duration: int = field(init=False)

    def __post_init__(self):
        self.starts = []
        tick = 0
        for step in self.steps:
            self.starts.append(tick)
            # A terminal zero-duration pose exists in Tomba's bank (E4).
            # Expose it as a static pose, never divide or loop by zero.
            tick += max(step.ticks, 1)
        self.duration = tick

    def sample(self, tick):
        """(step index, next step index or None, interpolation fraction)."""
        tick = max(0, min(int(tick), self.duration - 1))
        row = bisect_right(self.starts, tick) - 1
        step = self.steps[row]
        nxt = row + 1 if row + 1 < len(self.steps) else self.loop_start
        amount = ((tick - self.starts[row]) / max(step.ticks, 1)
                  if step.blend and nxt is not None else 0.0)
        return row, nxt, amount

    def advance(self, tick):
        if tick + 1 < self.duration:
            return tick + 1
        if self.loop_start is not None:
            return self.starts[self.loop_start]
        return None


def scale_states(clip, frames_by_id):
    """Effective per-limb scale for every sequence step.

    Scale is actor state, not an interpolated channel.  FUN_80076904 only
    writes it when bit 6 is present, so an ordinary pose keeps the previous
    values.  When a step tweens, FUN_80075ff8 reads the target immediately
    and writes that target's scales before it starts advancing rotations.
    """
    largest = max((frame.limb_count for frame in frames_by_id.values()),
                  default=0)
    state = [(1.0, 1.0, 1.0)] * largest
    result = []

    def apply(frame):
        nonlocal state
        if len(state) < frame.limb_count:
            state += [(1.0, 1.0, 1.0)] * (frame.limb_count - len(state))
        if frame.scales:
            state[:frame.limb_count] = frame.scaling()

    for row, step in enumerate(clip.steps):
        frame = frames_by_id[step.pose]
        apply(frame)
        nxt = row + 1 if row + 1 < len(clip.steps) else clip.loop_start
        if step.blend and nxt is not None:
            target = frames_by_id[clip.steps[nxt].pose]
            if target.scales:
                apply(target)
        result.append(tuple(state[:frame.limb_count]))
    return result


@dataclass
class Bank:
    label: str
    address: int
    clips: list
    verified: bool = False


def read_clip(data, file_base, address, poses, clip_id=0, max_steps=4096):
    """file_base is the RAM address corresponding to file byte zero."""
    seen, steps = {}, []
    while address not in seen:
        at = address - file_base
        if address & 3 or not 0 <= at <= len(data) - 8:
            raise SequenceError("sequence entry is outside its source binary")
        pose, p2, p4, flags = struct.unpack_from("<4H", data, at)
        if pose not in poses:
            raise SequenceError(f"sequence references unavailable pose {pose}")
        control = flags & 0xC000
        if not flags & 0xFFF and control != 0x8000:
            raise SequenceError("nonterminal entry has no tick duration")
        # Bit 12 belongs to the runtime blend timer, not sequence data.
        if flags & 0x1000:
            raise SequenceError("unsupported sequence timer flag")
        if len(steps) >= max_steps:
            raise SequenceError("sequence exceeds the traversal limit")
        seen[address] = len(steps)
        steps.append(Step(address, pose, p2, p4, flags))
        if control == 0x8000:
            return Clip(clip_id, steps)
        if control in (0x4000, 0xC000):
            if at + 12 > len(data):
                raise SequenceError("truncated sequence link")
            address = struct.unpack_from("<I", data, at + 8)[0]
        else:
            address += 8
    return Clip(clip_id, steps, seen[address])


def tomba_bank(sources, anmp, resource_id=None):
    """Verified retail US table; unknown builds/banks stay in raw mode.

    Identity uses the executable header and resource ID/pose layout, not
    DAT offsets or user-editable display names. Translated US EXEs work.
    """
    if resource_id != 4 or len(anmp) != 1152:
        return None
    if dict(anmp.limb_counts) != {17: 1147, 19: 5}:
        return None
    from gui.mainbin.mainbin_editor import BUILDS, _prefix_digest
    build = BUILDS["en"]
    for label, data in sources:
        if not data.startswith(b"PS-X EXE"):
            continue
        if (len(data) != build["file_size"]
                or _prefix_digest(data) != build["prefix_sha256"]):
            continue
        base = struct.unpack_from("<I", data, 0x18)[0] - 0x800
        table = 0x80017FE8
        poses = {f.index for f in anmp.frames}
        try:
            pointers = struct.unpack_from("<239I", data, table - base)
            clips = [read_clip(data, base, p, poses, i)
                     for i, p in enumerate(pointers)]
        except (SequenceError, struct.error):
            return None
        # These are usage contexts established by named decompiled
        # callers. Other IDs deliberately retain their numeric names.
        clips[2].name = "Idle"
        for i in range(3, 0x11):
            clips[i].name = "Ground movement"
        clips[0xCA].name = "Airborne / fall"
        clips[0xCB].name = "Airborne / fall variant"
        return Bank("Tomba (US) — 239 animations", table, clips, True)
    return None


def candidate_banks(data, base, poses, label="Overlay", min_clips=3):
    """Find compatible pointer runs, not a proven actor association.

    Every complete clip must validate against the current ANMP's actual
    pose indices; no partial traversal is presented as a real animation.
    The viewer ranks compatible runs by pose coverage automatically.
    """
    cache, banks, run = {}, [], []
    run_at = 0

    def finish():
        if len(run) < min_clips:
            return
        unique = {s.pose for c in run for s in c.steps}
        if len(unique) < 2:
            return
        clips = [Clip(i, c.steps, c.loop_start) for i, c in enumerate(run)]
        banks.append(Bank(f"{label} 0x{base + run_at:08X} (candidate)",
                          base + run_at, clips))

    for at in range(0, len(data) - 3, 4):
        pointer = struct.unpack_from("<I", data, at)[0]
        clip = None
        if not pointer & 3 and base <= pointer <= base + len(data) - 8:
            if pointer not in cache:
                try:
                    cache[pointer] = read_clip(data, base, pointer, poses)
                except SequenceError:
                    cache[pointer] = None
            clip = cache[pointer]
        if clip is None:
            finish()
            run = []
        else:
            if not run:
                run_at = at
            run.append(clip)
    finish()
    return banks

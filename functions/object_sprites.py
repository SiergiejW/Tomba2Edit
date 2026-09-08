"""The objects that are sprites rather than models.

Most of what an overlay places is drawn from the asset-pack SMST, and
functions/handler_models.py reads which part out of the handler's code.
A minority are not models at all: the jumpable fish, the wrapped torch
that burns until it is put out. Those are flat pictures out of the
area's own sprite bank, and asking handler_models about one gets either
nothing or - worse - whatever unrelated model the handler happens to
touch.

What marks one is a call to the routine at 0x80077B38, the same
f_StartActorTimedFrameSequence the crystals go through
(functions/pickup_art.py). Its arguments are the answer:

    a1  which table of animations - the area's own, which is the
        pointer at 0x800A58FC[area] and lives in the overlay
    a2  which animation in it

and the frames those name are numbered in the area's SPRT, SDAT id 10.
A handler that starts more than one is a class with more than one state:
the torch reads sequence 8 - frames 15 to 20, looping - while it burns
and sequence 12 - frame 28, held - once it is out.

HOW THE ADDRESS WAS FOUND

Out of the pickup routine at 0x8004A828, which ends by calling it with
the sequence it looked up. Nothing here is guessed at: the sequence
walker is pickup_art's, and the two sequences above are what the fire
object in AREA_09 really shows.
"""
from functions import mips
from functions import pickup_art

# The routine every animated sprite goes through, and where its two
# interesting arguments arrive.
START_SEQUENCE = 0x80077B38
TABLE_ARG, INDEX_ARG = 5, 6        # a1, a2

# How far to read a handler before deciding it has no more to say. The
# longest on the disc is under 400 instructions.
HANDLER_SPAN = 512

# Where an overlay lands, and how far a handler can be inside one.
OVERLAY_BASE = pickup_art.OVERLAY_BASE


class Sequence:
    """One animation a class can put on: which table, which entry, and
    the frames it turned out to be."""

    __slots__ = ("table", "index", "frames", "loops")

    def __init__(self, table, index, frames, loops):
        self.table = table
        self.index = index
        self.frames = frames
        self.loops = loops

    @property
    def still(self):
        return len(self.frames) <= 1

    def describe(self):
        shown = "/".join(str(f.frame) for f in self.frames) or "none"
        return (f"sequence {self.index}: {shown}"
                + (" looping" if self.loops else ""))

    def __repr__(self):
        return f"Sequence({self.index}, {self.describe()})"


def _walk(overlay, address, span=HANDLER_SPAN):
    """The instructions of one handler, up to its return."""
    out = []
    for step in range(span):
        at = address + step * 4 - OVERLAY_BASE
        if at < 0 or at + 4 > len(overlay):
            break
        word = int.from_bytes(overlay[at:at + 4], "little")
        instruction = mips.Instruction(address + step * 4, word)
        out.append(instruction)
        # The delay slot after a return still runs, so it is kept.
        if instruction.name == "jr" and instruction.rs == 31 and len(out) > 1:
            break
    return out


def sequences_for(overlay, handler):
    """[Sequence, ...] every animation one handler starts, in the order
    the code starts them.

    Read forwards rather than by the backwards walk handler_models does:
    both arguments are set by a plain lui/addiu or an addiu from zero
    within a few instructions of the call, and nothing here branches
    between the two."""
    table = {}
    high = {}
    found = []
    for instruction in _walk(overlay, handler):
        name = instruction.name
        if name == "lui":
            high[instruction.rt] = (instruction.imm & 0xFFFF) << 16
            table.pop(instruction.rt, None)
        elif name == "addiu":
            if instruction.rs in high:
                table[instruction.rt] = (high[instruction.rs]
                                         + instruction.imm) & 0xFFFFFFFF
            elif instruction.rs == 0:
                table[instruction.rt] = instruction.imm
            else:
                table.pop(instruction.rt, None)
        elif name == "jal" and instruction.target == START_SEQUENCE:
            where = table.get(TABLE_ARG)
            index = table.get(INDEX_ARG)
            if where is None or index is None:
                continue
            if (where, index) not in found:
                found.append((where, index))
        elif name in ("lw", "lbu", "lhu", "lb", "lh"):
            table.pop(instruction.rt, None)
    return [_resolve(overlay, where, index) for where, index in found]


def _resolve(overlay, table, index):
    """Walk one entry of a sequence table that lives in an overlay."""
    frames, loops = (), False
    try:
        frames, loops = _sequence(overlay, table, index)
    except (IndexError, ValueError):
        pass
    return Sequence(table, index, frames, loops)


def _sequence(overlay, table, index):
    """The same four-byte steps pickup_art walks, over overlay bytes."""
    def word(address):
        at = address - OVERLAY_BASE
        if at < 0 or at + 4 > len(overlay):
            raise IndexError(address)
        return int.from_bytes(overlay[at:at + 4], "little")

    address, frames, seen = word(table + index * 4), [], set()
    while len(frames) < pickup_art.MAX_STEPS:
        if address in seen:
            return tuple(frames), True
        seen.add(address)
        at = address - OVERLAY_BASE
        if at < 0 or at + 4 > len(overlay):
            raise IndexError(address)
        frame = int.from_bytes(overlay[at:at + 2], "little")
        control = int.from_bytes(overlay[at + 2:at + 4], "little")
        frames.append(pickup_art.Frame(frame=frame,
                                       ticks=control & pickup_art.TICKS))
        opcode = control & pickup_art.OPCODE
        if opcode == pickup_art.STOP:
            return tuple(frames), False
        if opcode in (pickup_art.JUMP, pickup_art.JUMP_RELOAD):
            address = word(address + 4)
        elif opcode == pickup_art.GO_ON:
            address += 4
        else:
            return tuple(frames), False
    return tuple(frames), False


def as_art(sequence):
    """One sequence dressed as a pickup_art.RewardArt, so the same
    billboard machinery draws it.

    A sprite object keeps whatever palette its own pieces carry and is
    numbered in the area's bank, which is what clut AREA_BANK means."""
    return pickup_art.RewardArt(
        reward=-1, width=0, height=0, item=-1, sequence=sequence.index,
        clut=pickup_art.AREA_BANK, frames=sequence.frames,
        loops=sequence.loops, name="")


def first_state(sequences):
    """Which of a class's animations to show. The moving one, where
    there is one - a torch that burns is on fire before it is out."""
    for sequence in sequences:
        if not sequence.still:
            return sequence
    return sequences[0] if sequences else None


def by_handler(overlay, handlers):
    """{handler: [Sequence, ...]} for the ones that draw sprites, and
    nothing for the ones that draw models."""
    out = {}
    for handler in sorted(set(handlers)):
        if not OVERLAY_BASE <= handler < OVERLAY_BASE + len(overlay):
            continue
        found = [s for s in sequences_for(overlay, handler) if s.frames]
        if found:
            out[handler] = found
    return out

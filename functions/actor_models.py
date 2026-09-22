"""Whole enemies and NPCs, read out of the code that builds them.

functions/handler_models.py finds the model an object attaches ONE part
with, because that is what most classes do: the handler calls the
single-part routine and the rest of the character arrives later, with
the animation. A big actor - a koma pig, an armadillo, one of the evil
pigs - is built all at once instead, by

    f_InitializeMultiPartActorModel(actor, part_count, model_data,
                                    part_layout)      at 0x800519E0

and every one of its four arguments says something worth having:

    part_count    how many parts, which is how many groups of the file
    model_data    the file itself, loaded as fileTable[id] - so the id
                  is the offset of that load divided by four
    part_layout   EIGHT BYTES PER PART, in the overlay:

                      s16 parent      -1 for a root
                      s16 x, y, z     where it sits on its parent

                  which is the same shape as the bone tables
                  functions/skeleton.py reads out of MAIN.EXE, and is
                  this character's own skeleton rather than one fitted
                  to it by eye.

So a class built this way needs no guessing at all: the file, the number
of parts and the rest pose are all in the call.

WHY THE HANDLER ALONE IS NOT ENOUGH

The call is rarely in the handler. A handler switches on its lifecycle
byte and its first state calls an init of its own, which is where the
build happens - the koma pig's handler at 0x80124394 calls 0x8012415C,
and only that calls the builder. So this follows calls, which
handler_models deliberately does not (following them made its answers
worse, since it attaches single parts all over the place).
"""
import struct

from functions import game_build, mips

# The routine that builds a whole actor, and where its arguments land.
MULTI_PART = 0x800519E0
COUNT_ARG, MODEL_ARG, LAYOUT_ARG = 5, 6, 7      # a1, a2, a3
# US retail's; another build's while it is open (functions/game_build.py).
_BUILD = game_build.Addresses(globals(), main=("MULTI_PART",))

# One part of the layout: parent, then where it sits on that parent.
PART = struct.Struct("<hhhh")
PART_SIZE = PART.size                           # 8

# A sane build. The largest on the disc is under thirty parts.
MAX_PARTS = 64

# How far to read one routine, and how many calls deep to follow. Two is
# enough for every class on the disc: handler -> its init -> the build.
SPAN = 400
DEPTH = 2


class Build:
    """What one call to the builder says a character is."""

    __slots__ = ("file_id", "parts", "layout", "bones")

    def __init__(self, file_id, parts, layout, bones):
        self.file_id = file_id
        self.parts = parts
        self.layout = layout
        self.bones = bones

    def describe(self):
        return (f"id {self.file_id}, {self.parts} parts, layout at "
                f"0x{self.layout:08X}")

    def __repr__(self):
        return f"Build({self.describe()})"


def _walk(image, address, span=SPAN):
    """One routine's instructions, up to its return."""
    out = []
    for step in range(span):
        at = address + step * 4
        if at not in image:
            break
        instruction = image.at(at)
        out.append(instruction)
        if instruction.name == "jr" and instruction.rs == 31 and len(out) > 1:
            break
    return out


def _run(images, address, file_table, depth, seen):
    """Every build one routine reaches, itself or through its calls."""
    if depth < 0 or address in seen:
        return []
    seen.add(address)
    image = next((i for i in images if address in i), None)
    if image is None:
        return []

    found, regs, high, files = [], {}, {}, {}
    body = _walk(image, address)
    for at, instruction in enumerate(body):
        if instruction.name == "jal":
            # The delay slot runs before the call lands, so whatever it
            # sets is an argument too - and it is often the last one, a
            # lui/addiu pair split across the branch.
            if at + 1 < len(body):
                _apply(body[at + 1], regs, high, files, file_table)
            if instruction.target == MULTI_PART:
                build = _read(image, regs, files)
                if build is not None:
                    found.append(build)
            else:
                found.extend(_run(images, instruction.target, file_table,
                                  depth - 1, seen))
            continue
        _apply(instruction, regs, high, files, file_table)
    return found


def _apply(instruction, regs, high, files, file_table):
    """One instruction's effect on what is known about the registers."""
    name = instruction.name
    if name == "lui":
        high[instruction.rt] = (instruction.imm & 0xFFFF) << 16
        regs.pop(instruction.rt, None)
        files.pop(instruction.rt, None)
    elif name == "addiu":
        files.pop(instruction.rt, None)
        if instruction.rs in high:
            regs[instruction.rt] = ((high[instruction.rs]
                                     + instruction.imm) & 0xFFFFFFFF)
        elif instruction.rs == 0:
            regs[instruction.rt] = instruction.imm
        else:
            regs.pop(instruction.rt, None)
    elif name == "lw":
        # The one load worth reading: out of the file table, which turns
        # an offset into an SDAT id.
        files.pop(instruction.rt, None)
        regs.pop(instruction.rt, None)
        if regs.get(instruction.rs) == file_table and instruction.imm >= 0:
            files[instruction.rt] = instruction.imm // 4
    elif name in mips.LOADS:
        regs.pop(instruction.rt, None)
        files.pop(instruction.rt, None)


def _read(image, regs, files):
    """One builder call's arguments, turned into a Build."""
    parts = regs.get(COUNT_ARG)
    file_id = files.get(MODEL_ARG)
    layout = regs.get(LAYOUT_ARG)
    if not parts or file_id is None or not layout:
        return None
    if not 1 <= parts <= MAX_PARTS:
        return None
    bones = []
    for index in range(parts):
        at = layout + index * PART_SIZE - image.base
        if at < 0 or at + PART_SIZE > len(image.data):
            return None
        bones.append(PART.unpack_from(image.data, at))
    return Build(file_id, parts, layout, tuple(bones))


def builds_for(images, handler, file_table, depth=DEPTH):
    """[Build, ...] every whole character one handler puts together."""
    out, seen = [], set()
    for build in _run(images, handler, file_table, depth, seen):
        if all(b.layout != build.layout for b in out):
            out.append(build)
    return out


def rest_offsets(bones):
    """Where each part sits, in the game's own axes.

    Walked parent-first with no rotation, which is what the builder
    leaves them at: a part's base rotation comes out of the same layout
    and is zero on everything the disc builds this way."""
    out = []
    for index, (parent, x, y, z) in enumerate(bones):
        if 0 <= parent < index:
            px, py, pz = out[parent]
            out.append((px + x, py + y, pz + z))
        else:
            out.append((x, y, z))
    return out

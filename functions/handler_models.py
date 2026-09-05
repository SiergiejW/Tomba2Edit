"""Which model each placed object is drawn with, read out of the code.

The one thing an object's record does not say is what it looks like -
see functions/placement.py. That is settled by the routine the record
names, and the routine settles it with immediates, so it can be read
off the disc after all. No savestate, no guessing.

WHAT THE CODE DOES

Every object that draws a model reaches it through one routine in
MAIN.EXE, 0x80051B70 on the retail disc, which is what sets an object
up:

    lui   a0, %hi(0x800ECF58)     the area's file table, one pointer
    addiu a0, a0, %lo(0x800ECF58) per SDAT id
    sll   v1, s2, 2               s2 = a1, the SDAT id
    addu  v1, v1, a0
    lw    a0, 0(v1)               the file
    sll   v1, s3, 2               s3 = a2, the group number
    addu  v1, a0, v1
    lw    v1, 4(v1)               that group's offset, out of the SMST's
    addu  a0, a0, v1              own pointer table
    sw    a0, 64(a1)              into the drawing record

So a call to it carries the answer in two registers: a1 is the SDAT id
and a2 the group. Both are usually immediates. Where a class of object
draws with a different model per slot, a2 is a byte read out of a table
in the overlay indexed by the object's slot - AREA_04's doors are
`15 16 18 65 23 17`, one byte per door - and that table can be read
too.

The same code says what the drawing record looks like, which is worth
having written down: 68 bytes, the model pointer at +0x40 and the
matrix at +0x2C. Reading the model at +0 - which is where it appears to
be if you find the records by their matrices - gives you the PREVIOUS
record's model, and that is exactly the one-record shift
functions.placement.bindings_from_state has to undo.

WHAT IT DOES NOT COVER

An object whose handler never calls that routine draws nothing, or
draws through something else; those come back unbound and the Level
Editor shows them as markers. Everything here is checked against what
the savestates independently learned - see `python -m
functions.handler_models` - and where the two disagree the savestate is
usually looking at a slot the code proves is something else.
"""
import os
import struct

from functions import clut_anim
from functions.mips import Image

# The routine every placed object's model goes through, and the table it
# reads. Both are found in MAIN.EXE rather than assumed - see
# find_attach() - so a build that moved them still works.
ATTACH_HINT = 0x80051B70
FILE_TABLE_HINT = 0x800ECF58

# MAIN.EXE is a PS-EXE: a 0x800 header, then the body, loaded where the
# header says.
EXE_HEADER = 0x800
EXE_MAGIC = b"PS-X EXE"

# How far back from a call to look for what put a value in a register.
# An immediate is nearly always set within a handful of instructions;
# forty is generous enough to cross a branch or two.
LOOKBACK = 48

# How far a walk of one handler may wander from where it started.
FUNCTION_SPAN = 0x8000

# Registers by number, for the two arguments that matter.
A1, A2 = 5, 6


class Value:
    """What a register holds, as far as this can tell."""


class Const(Value):
    __slots__ = ("n",)

    def __init__(self, n):
        self.n = n

    def __repr__(self):
        return f"Const(0x{self.n:X})"

    def __eq__(self, other):
        return isinstance(other, Const) and other.n == self.n

    def __hash__(self):
        return hash(("const", self.n))


class Slot(Value):
    """The object's own slot number - its byte at +3."""

    def __repr__(self):
        return "Slot"

    def __eq__(self, other):
        return isinstance(other, Slot)

    def __hash__(self):
        return hash("slot")


class SlotIndex(Value):
    """An address with the slot added to it - a table being indexed."""

    __slots__ = ("base",)

    def __init__(self, base):
        self.base = base

    def __repr__(self):
        return f"SlotIndex(0x{self.base:08X})"


class SlotTable(Value):
    """One byte per slot, at this address."""

    __slots__ = ("address",)

    def __init__(self, address):
        self.address = address

    def __repr__(self):
        return f"SlotTable(0x{self.address:08X})"

    def __eq__(self, other):
        return isinstance(other, SlotTable) and other.address == self.address

    def __hash__(self):
        return hash(("table", self.address))


def load_exe(path):
    """MAIN.EXE as an Image, loaded where its own header says."""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(EXE_MAGIC):
        raise ValueError(f"{os.path.basename(path)} is not a PS-EXE")
    address = struct.unpack_from("<I", data, 0x18)[0]
    return Image(data[EXE_HEADER:], address)


def load_overlay(path, base=None):
    """One Axx.BIN as an Image. The load address comes from the BIN
    folder the same way functions/clut_anim.py gets it - every overlay
    on a disc loads at the same place, and the animation tables are what
    pin it down."""
    with open(path, "rb") as f:
        data = f.read()
    if base is None:
        base = clut_anim.folder_base(os.path.dirname(path))
    if not base:
        raise ValueError("nothing in the BIN folder says where an overlay "
                         "loads, so its code can't be placed")
    return Image(data, base)


def find_attach(exe):
    """(the model-attach routine, the file table it reads), found by the
    shape of the code rather than taken on trust.

    The giveaway is the pair of loads that turn an id and a group into a
    pointer: `lw a0, 0(table + id*4)` and then `lw v1, 4(a0 + group*4)`,
    with the second's base being the first's result. Only one routine on
    the disc does that."""
    for address in (ATTACH_HINT,):
        table = _reads_file_table(exe, address)
        if table is not None:
            return address, table
    return None, None


def _reads_file_table(exe, entry, limit=0x400):
    """The file table `entry` indexes, if it is the attach routine."""
    base = None
    for instruction in exe.walk(entry, limit):
        if instruction.name == "lui":
            base = instruction.unsigned << 16
        elif instruction.name == "addiu" and base is not None:
            candidate = base + instruction.imm
            if 0x80010000 <= candidate < 0x80200000:
                # Followed, within a few instructions, by a shift-by-two
                # and a load - the id being turned into a pointer.
                shifted = any(
                    later.name == "sll" and later.shift == 2
                    for later in exe.walk(instruction.address + 4, 8))
                if shifted:
                    return candidate
        elif instruction.name == "jr":
            break
    return None


class Reader:
    """Reads registers backwards from a point in the code."""

    def __init__(self, images):
        self.images = images

    def image_for(self, address):
        for image in self.images:
            if address in image:
                return image
        return None

    def byte(self, address):
        image = self.image_for(address)
        if image is None:
            return None
        return image.data[address - image.base]

    def argument(self, call, register):
        """What `register` holds when a call at `call` is made.

        The delay slot first. MIPS runs the instruction after a jump
        before the jump takes effect, and the compiler puts an argument
        there whenever it can - the door handler sets a1 to 12 in the
        slot after its call, so reading backwards from the call alone
        finds some older a1 and comes back with the wrong file."""
        image = self.image_for(call)
        if image is not None and call + 4 in image:
            delay = image.at(call + 4)
            if _writes(delay) == register:
                return self._evaluate(delay, 0)
        return self.value(call, register)

    def value(self, address, register, depth=0):
        """What `register` holds just before the instruction at
        `address`, as far as the immediates say."""
        if register == 0:
            return Const(0)
        if depth > 6:
            return None
        image = self.image_for(address)
        if image is None:
            return None
        for step in range(1, LOOKBACK + 1):
            at = address - step * 4
            if at not in image:
                return None
            instruction = image.at(at)
            written = _writes(instruction)
            if written != register:
                continue
            return self._evaluate(instruction, depth)
        return None

    def _evaluate(self, instruction, depth):
        name = instruction.name
        at = instruction.address
        if name == "lui":
            return Const(instruction.unsigned << 16)
        if name in ("addiu", "addi"):
            source = self.value(at, instruction.rs, depth + 1)
            if instruction.rs == 0:
                return Const(instruction.imm)
            if isinstance(source, Const):
                return Const((source.n + instruction.imm) & 0xFFFFFFFF)
            return None
        if name == "ori":
            source = self.value(at, instruction.rs, depth + 1)
            if isinstance(source, Const):
                return Const(source.n | instruction.unsigned)
            return None
        if name in ("addu", "add"):
            left = self.value(at, instruction.rs, depth + 1)
            right = self.value(at, instruction.rt, depth + 1)
            if instruction.rt == 0:
                return left
            if instruction.rs == 0:
                return right
            for a, b in ((left, right), (right, left)):
                if isinstance(a, Slot) and isinstance(b, Const):
                    return SlotIndex(b.n)
            if isinstance(left, Const) and isinstance(right, Const):
                return Const((left.n + right.n) & 0xFFFFFFFF)
            return None
        if name in ("lbu", "lb"):
            source = self.value(at, instruction.rs, depth + 1)
            if isinstance(source, SlotIndex):
                return SlotTable(source.base + instruction.imm)
            if isinstance(source, Const):
                byte = self.byte(source.n + instruction.imm)
                return Const(byte) if byte is not None else None
            # The object's slot lives at +3 of its own record, and that
            # is what every per-slot table on the disc is indexed by.
            if source is None and instruction.imm == 3:
                return Slot()
            return None
        return None


def _writes(instruction):
    """Which register an instruction writes, or None."""
    name = instruction.name
    if name is None:
        return None
    if name in ("lui", "addiu", "addi", "ori", "andi", "xori", "slti",
                "sltiu", "lb", "lbu", "lh", "lhu", "lw"):
        return instruction.rt
    if name in ("addu", "add", "subu", "sub", "and", "or", "xor", "nor",
                "slt", "sltu", "sll", "srl", "sra", "sllv", "srlv", "srav",
                "mfhi", "mflo"):
        return instruction.rd
    if name in ("jal", "bltzal", "bgezal"):
        return 31
    if name == "jalr":
        return instruction.rd
    return None


def calls_within(image, entry, target, span=FUNCTION_SPAN):
    """Every place inside the routine at `entry` that calls `target`.

    A walk of the routine rather than a straight read of it: the
    handlers jump about, and the call that matters is often past a
    branch. Bounded to `span` either side of the entry so a tail call
    into somebody else's code cannot run away with it."""
    seen, todo, found = set(), [entry], []
    low, high = entry - span, entry + span
    while todo:
        address = todo.pop()
        while True:
            if address in seen or address not in image:
                break
            if not low <= address <= high:
                break
            seen.add(address)
            instruction = image.at(address)
            name = instruction.name
            if name == "jal":
                if instruction.target == target:
                    found.append(address)
                address += 8         # over the delay slot
                continue
            if name == "jr":
                break
            if name == "j":
                todo.append(instruction.target)
                break
            if instruction.target is not None:      # a conditional branch
                todo.append(instruction.target)
            address += 4
    return found


def models_for_handlers(handlers, exe_path, overlay_path, overlay_base=None):
    """{handler address: (SDAT id, group)} - the model each class of
    object draws with, read out of the code.

    `group` is an int where the whole class draws with one model, or a
    dict {slot: group} where it draws a different one per slot. A
    handler that never reaches the attach routine is left out."""
    exe = load_exe(exe_path)
    overlay = load_overlay(overlay_path, overlay_base)
    attach, _table = find_attach(exe)
    if attach is None:
        return {}
    reader = Reader((exe, overlay))

    out = {}
    for handler in sorted(set(handlers)):
        image = reader.image_for(handler)
        if image is None:
            continue
        for call in calls_within(image, handler, attach):
            file_id = reader.argument(call, A1)
            group = reader.argument(call, A2)
            if not isinstance(file_id, Const):
                continue
            if isinstance(group, Const):
                out.setdefault(handler, (file_id.n, group.n))
            elif isinstance(group, SlotTable):
                out.setdefault(handler, (file_id.n, group))
    return out


def bindings_from_code(placements, exe_path, overlay_path, overlay_base=None):
    """{(kind, slot, handler): (SDAT id, group)} for a whole overlay's
    object table - the same shape functions.placement.load_bindings
    returns, worked out from the code instead of from a savestate."""
    found = models_for_handlers({p.handler for p in placements},
                                exe_path, overlay_path, overlay_base)
    exe = load_exe(exe_path)
    overlay = load_overlay(overlay_path, overlay_base)
    reader = Reader((exe, overlay))

    out = {}
    for placement in placements:
        model = found.get(placement.handler)
        if model is None:
            continue
        file_id, group = model
        if isinstance(group, SlotTable):
            byte = reader.byte(group.address + placement.slot)
            if byte is None:
                continue
            group = byte
        out[placement.key()] = (file_id, group)
    return out

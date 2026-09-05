"""Which model each placed object is drawn with, read out of the code.

The one thing an object's record does not say is what it looks like -
see functions/placement.py. That is settled by the routine the record
names, and the routine settles it with immediates, so it can be read
off the disc after all. No savestate, no guessing.

WHAT THE CODE DOES

Every object that draws a model reaches it through one of two routines
in MAIN.EXE, both of which take the object, an SDAT id and a group
number, and do this:

    lui   v0, %hi(0x800ECF58)     the area's file table, one pointer
    addiu v0, v0, %lo(0x800ECF58) per SDAT id
    sll   a1, a1, 2               a1 = the SDAT id
    addu  a1, a1, v0
    lw    v0, 0(a1)               the file
    sll   a2, a2, 2               a2 = the group number
    addu  a2, v0, a2
    lw    v1, 4(a2)               that group's offset, out of the SMST's
    addu  v0, v0, v1              own pointer table
    sw    v0, 64(a0)              into the drawing record

So a call carries the answer in two registers. find_attach() picks the
routines out by that shape rather than by address, so a build that
moved them still works.

WHERE THE GROUP COMES FROM

Three ways, and all three are readable:

    an immediate      the whole class draws one model
    table[slot]       a class with a model per object - AREA_04's doors
                      are the bytes `15 16 18 65 23 17`, one per door
    table[overlay]    a class that lives in MAIN.EXE and so serves every
                      area, picking its model by which one is loaded -
                      the signposts are `61 10 60 0 14 12 ...`, one
                      halfword per Axx.BIN, and 61 is AREA_04's

The object's slot is its own byte at +3. Which overlay is loaded is a
byte in MAIN.EXE's memory, at OVERLAY_NUMBER below: it holds 0 with
A00.BIN loaded and 1 with A01.BIN, so it is the number in the overlay's
name, which is known without running anything.

WHAT THE CODE ALSO SETTLES

The drawing record is 68 bytes with the model pointer at +0x40 and the
matrix at +0x2C. Reading the model at +0 - which is where it appears to
be if you find the records by their matrices - gives you the PREVIOUS
record's model, and that is exactly the one-record shift
functions.placement.bindings_from_state has to undo.

WHAT IS NOT COVERED

A handler that never reaches an attach routine draws nothing, or draws
through something this does not follow; those come back unbound and the
Level Editor shows them as markers. Run this module as a script to
check what it does say against everything the savestates learned.
"""
import os
import struct

from functions import clut_anim
from functions.mips import Image

# MAIN.EXE is a PS-EXE: a 0x800 header, then the body, loaded where the
# header says.
EXE_HEADER = 0x800
EXE_MAGIC = b"PS-X EXE"
EXE_ADDRESS = 0x18

# Where the game keeps one pointer per SDAT id for the loaded area, and
# where in a drawing record the model pointer goes. Both are read out of
# the attach routine rather than assumed; these are only what to expect.
FILE_TABLE_HINT = 0x800ECF58
MODEL_FIELD = 0x40

# The byte that says which overlay is loaded - 0 for A00.BIN, 1 for
# A01.BIN, which is the number in the name. Checked in savestates taken
# in AREA_04 and AREA_05; it is what MAIN.EXE's own object classes index
# their model tables by, since they serve every area at once.
OVERLAY_NUMBER = 0x800BF870

# The object's slot is a byte at +3 of its record - every per-slot table
# on the disc is indexed by that load.
SLOT_FIELD = 3

# How far back from a call to look for what put a value in a register,
# and how far a walk of one handler may wander from where it started.
LOOKBACK = 48
FUNCTION_SPAN = 0x8000

A0, A1, A2 = 4, 5, 6

# What a table is indexed by.
BY_SLOT = "slot"
BY_OVERLAY = "overlay"


class Value:
    """What a register holds, as far as the immediates say."""
    __slots__ = ()


class Const(Value):
    __slots__ = ("n",)

    def __init__(self, n):
        self.n = n

    def __repr__(self):
        return f"Const(0x{self.n:X})"


class Index(Value):
    """A number that is only known once an object is in hand - its slot,
    or which overlay is loaded."""
    __slots__ = ("kind", "shift")

    def __init__(self, kind, shift=0):
        self.kind = kind
        self.shift = shift

    def __repr__(self):
        return f"Index({self.kind}<<{self.shift})"


class Sum(Value):
    """A constant address with an index scaled onto it."""
    __slots__ = ("base", "index")

    def __init__(self, base, index):
        self.base = base
        self.index = index

    def __repr__(self):
        return f"Sum(0x{self.base:08X}, {self.index})"


class Table(Value):
    """One entry per index, at `address`, `width` bytes apart."""
    __slots__ = ("address", "kind", "stride", "width", "signed")

    def __init__(self, address, kind, stride, width, signed):
        self.address = address
        self.kind = kind
        self.stride = stride
        self.width = width
        self.signed = signed

    def __repr__(self):
        return (f"Table(0x{self.address:08X}, by {self.kind}, "
                f"{self.stride}-byte)")


def load_exe(path):
    """MAIN.EXE as an Image, loaded where its own header says."""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(EXE_MAGIC):
        raise ValueError(f"{os.path.basename(path)} is not a PS-EXE")
    return Image(data[EXE_HEADER:],
                 struct.unpack_from("<I", data, EXE_ADDRESS)[0])


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
                         "loads, so its code cannot be placed")
    return Image(data, base)


def overlay_number(path):
    """Which Axx.BIN this is, as the game counts them - A00 is 0."""
    name = os.path.basename(path).upper()
    if not name.startswith("A0") or not name.endswith(".BIN"):
        return None
    return "0123456789ABCDEFGHIJKL".find(name[2:-4])


# --------------------------------------------------------------------
# Finding the routines that attach a model
# --------------------------------------------------------------------

def _function_entry(image, address, limit=400):
    """The start of the routine containing `address` - the instruction
    two past the previous `jr ra`, which is where the last one ended."""
    for step in range(1, limit):
        at = address - step * 4
        if at not in image:
            break
        instruction = image.at(at)
        if instruction.name == "jr" and instruction.rs == 31:
            return at + 8
    return address


def find_attach(exe):
    """Every routine in MAIN.EXE that turns an SDAT id and a group into
    a model pointer, as {entry address: the file table it reads}.

    Found by shape: a routine that forms the file table, loads a group's
    offset out of an SMST's pointer table (`lw` at +4) and stores the
    result into a drawing record (`sw` at +0x40). Two on the retail
    disc, and nothing else on it does all three."""
    out = {}
    for i in range(len(exe.data) // 4):
        address = exe.base + i * 4
        instruction = exe.at(address)
        if instruction.name != "lui":
            continue
        table = None
        for step in range(1, 6):
            following = exe.at(address + step * 4)
            if (following.name == "addiu" and following.rs == instruction.rt):
                candidate = (instruction.unsigned << 16) + following.imm
                if 0x80010000 <= candidate < 0x80200000:
                    table = candidate
                break
        if table is None:
            continue
        window = [exe.at(address + k * 4) for k in range(18)]
        if not any(w.name == "lw" and w.imm == 4 for w in window):
            continue
        if not any(w.name == "sw" and w.imm == MODEL_FIELD for w in window):
            continue
        out[_function_entry(exe, address)] = table
    return out


# --------------------------------------------------------------------
# Reading a register back to the immediates that filled it
# --------------------------------------------------------------------

WRITES_RT = frozenset((
    "lui", "addiu", "addi", "ori", "andi", "xori", "slti", "sltiu",
    "lb", "lbu", "lh", "lhu", "lw"))
WRITES_RD = frozenset((
    "addu", "add", "subu", "sub", "and", "or", "xor", "nor", "slt", "sltu",
    "sll", "srl", "sra", "sllv", "srlv", "srav", "mfhi", "mflo"))

# Loads, as (width, signed).
LOAD_WIDTH = {"lb": (1, True), "lbu": (1, False), "lh": (2, True),
              "lhu": (2, False), "lw": (4, True)}


def _writes(instruction):
    name = instruction.name
    if name in WRITES_RT:
        return instruction.rt
    if name in WRITES_RD:
        return instruction.rd
    if name in ("jal", "bltzal", "bgezal"):
        return 31
    if name == "jalr":
        return instruction.rd
    return None


class Reader:
    """Reads registers backwards from a point in the code."""

    def __init__(self, images):
        self.images = tuple(images)

    def image_for(self, address):
        for image in self.images:
            if address in image:
                return image
        return None

    def read(self, address, width=1, signed=False):
        """A constant out of whichever image holds that address."""
        image = self.image_for(address)
        if image is None:
            return None
        at = address - image.base
        if at + width > len(image.data):
            return None
        code = {(1, False): "<B", (1, True): "<b", (2, False): "<H",
                (2, True): "<h", (4, True): "<I", (4, False): "<I"}[(width, signed)]
        return struct.unpack_from(code, image.data, at)[0]

    def argument(self, call, register):
        """What `register` holds when the call at `call` is made.

        The delay slot first. MIPS runs the instruction after a jump
        before the jump takes effect, and the compiler puts an argument
        there whenever it can - the door handler sets a1 to 12 in the
        slot after its call, so reading backwards from the call alone
        finds some older a1 and comes back with the wrong file."""
        image = self.image_for(call)
        if image is not None and call + 4 in image:
            delay = image.at(call + 4)
            if _writes(delay) == register:
                return self.evaluate(delay, 0)
        return self.value(call, register)

    def value(self, address, register, depth=0):
        if register == 0:
            return Const(0)
        if depth > 8:
            return None
        image = self.image_for(address)
        if image is None:
            return None
        for step in range(1, LOOKBACK + 1):
            at = address - step * 4
            if at not in image:
                return None
            instruction = image.at(at)
            if _writes(instruction) != register:
                continue
            return self.evaluate(instruction, depth)
        return None

    def evaluate(self, instruction, depth=0):
        name = instruction.name
        at = instruction.address
        if name == "lui":
            return Const(instruction.unsigned << 16)
        if name in ("addiu", "addi"):
            if instruction.rs == 0:
                return Const(instruction.imm)
            source = self.value(at, instruction.rs, depth + 1)
            if isinstance(source, Const):
                return Const((source.n + instruction.imm) & 0xFFFFFFFF)
            if isinstance(source, Sum):
                return Sum(source.base + instruction.imm, source.index)
            return None
        if name == "ori":
            source = self.value(at, instruction.rs, depth + 1)
            if isinstance(source, Const):
                return Const(source.n | instruction.unsigned)
            return None
        if name == "sll":
            source = self.value(at, instruction.rt, depth + 1)
            if isinstance(source, Index):
                return Index(source.kind, source.shift + instruction.shift)
            if isinstance(source, Const):
                return Const((source.n << instruction.shift) & 0xFFFFFFFF)
            return None
        if name in ("addu", "add"):
            if instruction.rt == 0:
                return self.value(at, instruction.rs, depth + 1)
            if instruction.rs == 0:
                return self.value(at, instruction.rt, depth + 1)
            left = self.value(at, instruction.rs, depth + 1)
            right = self.value(at, instruction.rt, depth + 1)
            for a, b in ((left, right), (right, left)):
                if isinstance(a, Index) and isinstance(b, Const):
                    return Sum(b.n, a)
            if isinstance(left, Const) and isinstance(right, Const):
                return Const((left.n + right.n) & 0xFFFFFFFF)
            return None
        if name in LOAD_WIDTH:
            width, signed = LOAD_WIDTH[name]
            source = self.value(at, instruction.rs, depth + 1)
            if isinstance(source, Sum):
                return Table(source.base + instruction.imm, source.index.kind,
                             1 << source.index.shift, width, signed)
            if isinstance(source, Const):
                where = source.n + instruction.imm
                if where == OVERLAY_NUMBER:
                    return Index(BY_OVERLAY)
                value = self.read(where, width, signed)
                return Const(value) if value is not None else None
            # An object's slot is its own byte at +3, and that is what
            # every per-slot table on the disc is indexed by.
            if source is None and width == 1 and instruction.imm == SLOT_FIELD:
                return Index(BY_SLOT)
            return None
        return None


# --------------------------------------------------------------------
# Walking a handler
# --------------------------------------------------------------------

def calls_within(image, entry, targets, span=FUNCTION_SPAN):
    """Every place inside the routine at `entry` that calls one of
    `targets`, as [(call address, target), ...].

    A walk of the routine rather than a straight read of it: the
    handlers jump about, and the call that matters is usually past a
    branch or two. Bounded to `span` either side of the entry so a tail
    call into somebody else's code cannot run away with it."""
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
                if instruction.target in targets:
                    found.append((address, instruction.target))
                address += 8            # over the delay slot
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


class CodeModels:
    """One disc's answer to "what does each object class draw with"."""

    def __init__(self, exe_path, overlay_path, overlay_base=None):
        self.exe = load_exe(exe_path)
        self.overlay = load_overlay(overlay_path, overlay_base)
        self.overlay_index = overlay_number(overlay_path)
        self.reader = Reader((self.exe, self.overlay))
        self.attach = set(find_attach(self.exe))

    def models_for(self, handler):
        """Every model a class of object can attach, as [(SDAT id,
        group), ...] where `group` is an int or a Table to be indexed by
        an object's slot.

        A list rather than an answer because a class can genuinely have
        several: AREA_1F's kind 79 attaches groups 62, 63 and 64 as the
        object goes through its states, and AREA_06's kind 26 picks
        between two by branching on the slot. Where the list has one
        entry the code has settled it; where it has more, the code says
        what the possibilities are and only watching the game says which
        is standing at a given moment."""
        image = self.reader.image_for(handler)
        if image is None or not self.attach:
            return []
        found = []
        for call, _target in calls_within(image, handler, self.attach):
            file_id = self.reader.argument(call, A1)
            group = self.reader.argument(call, A2)
            if not isinstance(file_id, Const):
                continue
            if isinstance(group, Table) and group.kind == BY_OVERLAY:
                resolved = self.entry(group, self.overlay_index)
                if resolved is None:
                    continue
                group = Const(resolved)
            if isinstance(group, Const):
                model = (file_id.n, group.n)
            elif isinstance(group, Table):
                model = (file_id.n, group)
            else:
                continue
            if model not in found:
                found.append(model)
        return found

    def model_for(self, handler):
        """The one model a class draws with, or None if the code names
        none or names several."""
        found = self.models_for(handler)
        return found[0] if len(found) == 1 else None

    def entry(self, table, index):
        """One entry of a table, or None."""
        if index is None:
            return None
        return self.reader.read(table.address + index * table.stride,
                                table.width, table.signed)

    def choices(self, placement, cache=None):
        """Every model one record's object could be drawn with, as
        [(SDAT id, group), ...] - a per-slot table resolved down to this
        record's own slot."""
        if cache is None:
            cache = {}
        if placement.handler not in cache:
            cache[placement.handler] = self.models_for(placement.handler)
        out = []
        for file_id, group in cache[placement.handler]:
            if isinstance(group, Table):
                group = self.entry(group, placement.slot)
                if group is None:
                    continue
            if (file_id, group) not in out:
                out.append((file_id, group))
        return out

    def bindings(self, placements):
        """{(kind, slot, handler): (SDAT id, group)} for a whole object
        table - the same shape functions.placement.load_bindings
        returns, worked out from the code instead of from a state.

        Only the records the code settles outright. A class that can
        attach several models is left out rather than guessed at; see
        choices() for what it does say about those."""
        cache, out = {}, {}
        for placement in placements:
            found = self.choices(placement, cache)
            if len(found) == 1:
                out[placement.key()] = found[0]
        return out


def bindings_from_code(placements, exe_path, overlay_path, overlay_base=None):
    """{(kind, slot, handler): (SDAT id, group)} for one overlay."""
    return CodeModels(exe_path, overlay_path, overlay_base).bindings(placements)


# --------------------------------------------------------------------
# Checking it against what the savestates worked out
# --------------------------------------------------------------------

def _check(cd_folder, bin_folder, exe_path):
    from functions import placement

    agree = disagree = fresh = missing = 0
    for name in sorted(os.listdir(bin_folder)):
        if not name.upper().startswith("A0"):
            continue
        path = os.path.join(bin_folder, name)
        records = placement.load_placements(path)
        if not records:
            continue
        try:
            found = bindings_from_code(records, exe_path, path)
        except ValueError as e:
            print(f"{name}: {e}")
            continue
        known = placement.load_bindings(name)
        both = set(found) & set(known)
        wrong = [k for k in both if found[k] != known[k]]
        agree += len(both) - len(wrong)
        disagree += len(wrong)
        fresh += len(set(found) - set(known))
        missing += len(set(known) - set(found))
        print(f"{name}: {len(found)}/{len(records)} objects from code, "
              f"{len(both) - len(wrong)} agree, {len(wrong)} disagree, "
              f"{len(set(found) - set(known))} new")
        for key in sorted(wrong):
            print(f"    {key[0]}.{key[1]} handler 0x{key[2]:08X}: "
                  f"code {found[key]}, states {known[key]}")
    print(f"\ntotal: {agree} agree, {disagree} disagree, {fresh} the states "
          f"never learned, {missing} the code does not reach")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 4:
        print(__doc__)
        print("usage: python -m functions.handler_models <CD folder> "
              "<BIN folder> <MAIN.EXE>")
        raise SystemExit(2)
    _check(*sys.argv[1:4])

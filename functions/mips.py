"""Just enough MIPS R3000 to read what the game's code does.

Not a disassembler for its own sake. The one thing that cannot be read
out of the disc's data is which model an object is drawn with - see
functions/placement.py - and the reason is that it is worked out by
code: a handler loads its area's file table, picks a file out of it,
and indexes that file's own group table. Every one of those steps is an
immediate in an instruction, so the answer is in the overlay after all,
one level down from where the data is.

This decodes the instructions and functions/handler_models.py follows
them. Only the opcodes the game's handlers actually use are named;
anything else comes back as `None` for its mnemonic, which is enough to
know it is not one of the ones being tracked.
"""
import struct

REGISTERS = (
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra",
)

# op -> mnemonic, for the I- and J-types.
OPCODES = {
    0x02: "j", 0x03: "jal", 0x04: "beq", 0x05: "bne", 0x06: "blez",
    0x07: "bgtz", 0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu",
    0x0C: "andi", 0x0D: "ori", 0x0E: "xori", 0x0F: "lui",
    0x20: "lb", 0x21: "lh", 0x22: "lwl", 0x23: "lw", 0x24: "lbu",
    0x25: "lhu", 0x26: "lwr", 0x28: "sb", 0x29: "sh", 0x2A: "swl",
    0x2B: "sw", 0x2E: "swr",
}

# funct -> mnemonic, for the R-types.
FUNCTS = {
    0x00: "sll", 0x02: "srl", 0x03: "sra", 0x04: "sllv", 0x06: "srlv",
    0x07: "srav", 0x08: "jr", 0x09: "jalr", 0x0C: "syscall", 0x10: "mfhi",
    0x11: "mthi", 0x12: "mflo", 0x13: "mtlo", 0x18: "mult", 0x19: "multu",
    0x1A: "div", 0x1B: "divu", 0x20: "add", 0x21: "addu", 0x22: "sub",
    0x23: "subu", 0x24: "and", 0x25: "or", 0x26: "xor", 0x27: "nor",
    0x2A: "slt", 0x2B: "sltu",
}

LOADS = frozenset(("lb", "lbu", "lh", "lhu", "lw"))
STORES = frozenset(("sb", "sh", "sw"))
BRANCHES = frozenset(("beq", "bne", "blez", "bgtz", "bltz", "bgez",
                      "j", "jal"))


class Instruction:
    """One decoded instruction. `rd`, `rs`, `rt` are register numbers,
    `imm` the sign-extended immediate, `target` a jump address."""

    __slots__ = ("address", "word", "name", "rs", "rt", "rd", "shift",
                 "imm", "target")

    def __init__(self, address, word):
        self.address = address
        self.word = word
        self.rs = (word >> 21) & 31
        self.rt = (word >> 16) & 31
        self.rd = (word >> 11) & 31
        self.shift = (word >> 6) & 31
        value = word & 0xFFFF
        self.imm = value - 0x10000 if value & 0x8000 else value
        self.target = None
        op = word >> 26
        if op == 0:
            self.name = FUNCTS.get(word & 0x3F)
        elif op == 1:
            # REGIMM: the branch is picked by the rt field.
            self.name = {0: "bltz", 1: "bgez", 16: "bltzal",
                         17: "bgezal"}.get(self.rt)
        elif op in (0x02, 0x03):
            self.name = OPCODES[op]
            self.target = (address & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
        else:
            self.name = OPCODES.get(op)
        if self.name in ("beq", "bne", "blez", "bgtz", "bltz", "bgez"):
            self.target = address + 4 + self.imm * 4

    @property
    def unsigned(self):
        return self.word & 0xFFFF

    def __repr__(self):
        return f"<{self.name or hex(self.word)} at 0x{self.address:08X}>"

    def text(self):
        """The instruction as a line of assembly, for reading by eye."""
        name = self.name or f".word 0x{self.word:08X}"
        r = REGISTERS
        if name in LOADS or name in STORES:
            return f"{name:6s} {r[self.rt]}, {self.imm}({r[self.rs]})"
        if name == "lui":
            return f"lui    {r[self.rt]}, 0x{self.unsigned:04X}"
        if name in ("addiu", "addi", "slti", "sltiu"):
            return f"{name:6s} {r[self.rt]}, {r[self.rs]}, {self.imm}"
        if name in ("andi", "ori", "xori"):
            return f"{name:6s} {r[self.rt]}, {r[self.rs]}, 0x{self.unsigned:04X}"
        if name in ("j", "jal"):
            return f"{name:6s} 0x{self.target:08X}"
        if name in ("beq", "bne"):
            return f"{name:6s} {r[self.rs]}, {r[self.rt]}, 0x{self.target:08X}"
        if name in ("blez", "bgtz", "bltz", "bgez"):
            return f"{name:6s} {r[self.rs]}, 0x{self.target:08X}"
        if name in ("sll", "srl", "sra"):
            return f"{name:6s} {r[self.rd]}, {r[self.rt]}, {self.shift}"
        if name == "jr":
            return f"jr     {r[self.rs]}"
        if name == "jalr":
            return f"jalr   {r[self.rd]}, {r[self.rs]}"
        if name in ("mfhi", "mflo"):
            return f"{name:6s} {r[self.rd]}"
        if name in ("mult", "multu", "div", "divu"):
            return f"{name:6s} {r[self.rs]}, {r[self.rt]}"
        return f"{name:6s} {r[self.rd]}, {r[self.rs]}, {r[self.rt]}"


class Image:
    """A loaded binary - the bytes, and where they sit in RAM."""

    def __init__(self, data, base):
        self.data = data
        self.base = base

    def __contains__(self, address):
        return self.base <= address < self.base + len(self.data) - 3

    def word(self, address):
        return struct.unpack_from("<I", self.data, address - self.base)[0]

    def at(self, address):
        return Instruction(address, self.word(address))

    def walk(self, address, limit):
        """Instructions from `address` on, stopping at the end of the
        image or after `limit` of them."""
        for _n in range(limit):
            if address not in self:
                return
            yield self.at(address)
            address += 4

    def text(self, address, count=32):
        """A stretch of code as lines, for reading by eye."""
        return [f"0x{i.address:08X}  {i.word:08X}  {i.text()}"
                for i in self.walk(address, count)]

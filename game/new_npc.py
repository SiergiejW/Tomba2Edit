"""Brand-new NPCs: a scene record plus a small handler generated here.

Native NPC handlers hard-code their model, so a new record cannot just pick a
model. Each new NPC gets its own generated handler (a copy of what the
Fishermen's Town talkers do: build the multi-part model, play animation 0,
turn on talk, run a script) with model, layout, animation table and script
as immediates - so the Event Editor finds and edits its page like a native one.

Everything lives in the overlay's free-RAM window, in the NPC region just
below the event region (see pickup_edit.TABLES_LIMIT):

    +0x000  tag
    +0x010  copy of the scene's record list, room for LIST_CAP records
    +0x1A0  NPC blocks of BLOCK bytes: handler code, then the default script
"""
import struct
from dataclasses import dataclass

from game import actor_models, game_build, pickup_edit
from psx import mips

BASE = game_build.US_OVERLAY_BASE
REGION_END = pickup_edit.EVENT_START - BASE
REGION_SIZE = 0x1000
REGION = REGION_END - REGION_SIZE
TAG = b"T2EDIT_NPCS_V1\0\0"
LIST_AT = REGION + 0x10
LIST_CAP = 24
RECORD = 16
BLOCKS_AT = LIST_AT + (LIST_CAP + 1) * RECORD
BLOCK = 0x200
CODE_SIZE = 0x180
MAX_NPCS = (REGION_END - BLOCKS_AT) // BLOCK
FIRST_VARIANT = 200
FILE_TABLE = 0x800ECF58

# The Fishermen's Town talkers' box, used when a model's own is unknown.
DEFAULT_BOX = (80, 160, 128, 224)
BOX_AT = (0x80, 0x82, 0x84, 0x86)
START_ANIMATION, START_BLEND = 0x80077C40, 0x80077CFC
SCRIPT_LOADER = 0x80040CDC
# f_AllocateActor's scene records for a talker: type, alloc, class, gate, arg.
TEMPLATE_HANDLER = 0x80121978


@dataclass
class ModelChoice:
    """One whole character an area's NPC code builds."""
    name: str
    handler: int
    file_id: int
    parts: int
    layout: int
    anim_file: int
    anim_table: int
    box: tuple = DEFAULT_BOX

    def describe(self):
        return f"{self.name} (file {self.file_id}, {self.parts} parts)"


def _walk(image, address):
    return actor_models._walk(image, address)


def _scan(images, address, depth, seen, out):
    """Collect, over one routine and the routines it calls: the builder's
    arguments, the animation table, the +0x3c file and the talk box."""
    if depth < 0 or address in seen:
        return
    seen.add(address)
    image = next((i for i in images if address in i), None)
    if image is None:
        return
    regs, high, files = {}, {}, {}
    body = _walk(image, address)
    apply = actor_models._apply
    for at, ins in enumerate(body):
        if ins.name == "jal":
            if at + 1 < len(body):
                apply(body[at + 1], regs, high, files, FILE_TABLE)
            target = ins.target
            if target == actor_models.MULTI_PART:
                build = actor_models._read(image, regs, files)
                if build is not None:
                    out.setdefault("builds", []).append(build)
            elif target in (START_ANIMATION, START_BLEND, SCRIPT_LOADER):
                table = regs.get(5)
                if table and table >= BASE:
                    out.setdefault("tables", []).append(table)
            else:
                _scan(images, target, depth - 1, seen, out)
            continue
        if ins.name == "sw" and ins.imm == 0x3C and ins.rt in files:
            out.setdefault("anim_files", []).append(files[ins.rt])
        elif ins.name == "sw" and ins.imm == 0x7C and regs.get(ins.rt, 0) >= BASE:
            out.setdefault("tables", []).append(regs[ins.rt])
        elif ins.name == "sh" and ins.imm in BOX_AT and ins.rt in regs:
            out.setdefault("box", {})[ins.imm] = regs[ins.rt]
        apply(ins, regs, high, files, FILE_TABLE)


def model_choices(exe, overlay, handlers, names=None):
    """[ModelChoice] for every handler in `handlers` that builds a whole
    character with a known animation table and animation file."""
    main = struct.unpack_from("<I", exe, 24)[0]
    images = (mips.Image(exe[0x800:], main), mips.Image(overlay, BASE))
    names = names or {}
    out, seen_layouts = [], set()
    for handler in sorted(set(handlers)):
        found = {}
        _scan(images, handler, actor_models.DEPTH, set(), found)
        builds, tables, files = (found.get("builds"), found.get("tables"),
                                 found.get("anim_files"))
        if not (builds and tables and files):
            continue
        build = builds[0]
        if build.layout in seen_layouts:
            continue
        seen_layouts.add(build.layout)
        box = found.get("box", {})
        box = tuple(box.get(o, d) for o, d in zip(BOX_AT, DEFAULT_BOX))
        out.append(ModelChoice(names.get(handler, f"NPC 0x{handler:08X}"), handler,
                               build.file_id, build.parts, build.layout,
                               files[0], tables[0], box))
    return out


# ---------------------------------------------------------------- the handler

def _lines(model, script, yaw):
    hi = lambda v: ((v + 0x8000) >> 16) & 0xFFFF
    lo = lambda v: ((v & 0xFFFF) ^ 0x8000) - 0x8000
    table = model.anim_table
    box = "\n".join(f"addiu v0, zero, {v}\nsh v0, {o}(s0)"
                    for o, v in zip(BOX_AT, model.box))
    return f"""
addiu sp, sp, -32
sw s0, 16(sp)
sw s1, 20(sp)
sw ra, 24(sp)
addu s0, a0, zero
lbu v0, 4(s0)
beq v0, zero, INIT
addiu v1, zero, 1
beq v0, v1, RUN
addiu v1, zero, 3
bne v0, v1, END
nop
jal 0x8007A624
addu a0, s0, zero
j END
nop
INIT:
lui v0, 0x{hi(FILE_TABLE):X}
addiu s1, v0, {lo(FILE_TABLE)}
addu a0, s0, zero
addiu a1, zero, {model.parts}
lw a2, {model.file_id * 4}(s1)
lui a3, 0x{hi(model.layout):X}
jal 0x800519E0
addiu a3, a3, {lo(model.layout)}
bne v0, zero, END
nop
lw v0, {model.anim_file * 4}(s1)
addu a0, s0, zero
lui a1, 0x{hi(table):X}
addiu a1, a1, {lo(table)}
sw v0, 60(s0)
jal 0x80077C40
addu a2, zero, zero
{box}
sb zero, 11(s0)
sb zero, 43(s0)
sb zero, 71(s0)
addiu v0, zero, {yaw & 0xFFF}
sh v0, 86(s0)
addiu v0, zero, 9
sb v0, 0(s0)
lbu v1, 4(s0)
nop
addiu v1, v1, 1
sb v1, 4(s0)
j END
nop
RUN:
lbu v1, 5(s0)
nop
bne v1, zero, SCRIPT
nop
lbu v0, 43(s0)
addiu v1, zero, 3
bne v0, v1, TAIL
addiu a0, zero, 1
jal 0x80042354
addiu a1, zero, 1
lui a1, 0x{hi(table):X}
addiu a1, a1, {lo(table)}
lui a2, 0x{hi(script):X}
addiu a2, a2, {lo(script)}
jal 0x80040CDC
addu a0, s0, zero
addiu v0, zero, 1
sb v0, 112(s0)
sb v0, 5(s0)
j TAIL
nop
SCRIPT:
jal 0x80041098
addu a0, s0, zero
lbu v1, 112(s0)
addiu v0, zero, 255
bne v1, v0, TAIL
nop
jal 0x80042310
nop
sb zero, 5(s0)
TAIL:
jal 0x8004190C
addu a0, s0, zero
jal 0x8007778C
addu a0, s0, zero
jal 0x800518FC
addu a0, s0, zero
sb zero, 43(s0)
END:
lw ra, 24(sp)
lw s1, 20(sp)
lw s0, 16(sp)
jr ra
addiu sp, sp, 32
"""


def handler_code(address, model, script, yaw=0):
    """The generated handler as bytes, placed at `address`."""
    rows, labels = [], {}
    for line in _lines(model, script, yaw).strip().splitlines():
        line = line.strip()
        if line.endswith(":"):
            labels[line[:-1]] = address + 4 * len(rows)
        else:
            rows.append(line)
    words = []
    for n, line in enumerate(rows):
        name, _, rest = line.partition(" ")
        if name in ("j", "beq", "bne"):
            parts = [p.strip() for p in rest.split(",")]
            parts[-1] = hex(labels.get(parts[-1], 0) or int(parts[-1], 0))
            line = f"{name} {', '.join(parts)}"
        words.append(mips.assemble(line, address + 4 * n))
    code = struct.pack(f"<{len(words)}I", *words)
    if len(code) > CODE_SIZE:
        raise ValueError("Generated NPC handler is too large.")
    return code


# Face Tomba, show dialogue 1 entry 16, back to rest animation 0 and end.
def default_script(dialogue=1, entry=16):
    return (struct.pack("<H3h", 0x1000 | 31, 1, 256, 0)
            + struct.pack("<H3h", 0x1000 | 0, dialogue, entry, 0)
            + struct.pack("<H3h", 0x8000 | 30, 0, 0, 4))


# ------------------------------------------------------------------- the region

def region_ready(overlay):
    return overlay[REGION:REGION + len(TAG)] == TAG


def used_blocks(overlay):
    """[block index, ...] already holding a handler."""
    if not region_ready(overlay):
        return []
    return [n for n in range(MAX_NPCS)
            if any(overlay[BLOCKS_AT + n * BLOCK:BLOCKS_AT + n * BLOCK + 8])]


def read_list(overlay, pointer):
    """[16-byte record, ...] of a 0xFF-terminated scene list."""
    at, out = pointer - BASE, []
    while overlay[at] != 0xFF:
        out.append(bytes(overlay[at:at + RECORD]))
        at += RECORD
    return out


def add_npc(overlay, table_at, scene, model, position, yaw=0, template=None):
    """(new overlay, handler address, variant) with one new NPC in `scene`.

    `table_at` is the overlay offset of the scene -> list pointer array.
    The scene's list is copied into the NPC region on first use and the
    pointer repointed; later NPCs append to that copy."""
    if game_build.current().name != "us-retail":
        raise ValueError("New NPCs need the US retail layout.")
    out = bytearray(overlay)
    if len(out) > REGION_END:
        raise ValueError("The area file already extends into the event region.")
    if len(out) < REGION_END:
        out.extend(bytes(REGION_END - len(out)))
    pointer_at = table_at + scene * 4
    pointer = struct.unpack_from("<I", out, pointer_at)[0]
    if not region_ready(out):
        if any(out[REGION:REGION_END]):
            raise ValueError("The NPC region is occupied by something else.")
        out[REGION:REGION + len(TAG)] = TAG
    if LIST_AT + BASE <= pointer < LIST_AT + BASE + (LIST_CAP + 1) * RECORD:
        records = read_list(out, pointer)
    else:
        records = read_list(out, pointer)
        if len(records) > LIST_CAP:
            raise ValueError("This scene's record list is too long to move.")
    if len(records) >= LIST_CAP:
        raise ValueError("This scene has no room for another record.")
    free = [n for n in range(MAX_NPCS) if n not in used_blocks(out)]
    if not free:
        raise ValueError(f"An area holds at most {MAX_NPCS} new NPCs.")
    n = free[0]
    block = BLOCKS_AT + n * BLOCK
    address = BASE + block
    script_at = BASE + block + CODE_SIZE
    variant = FIRST_VARIANT + n
    out[block:block + CODE_SIZE] = handler_code(address, model, script_at, yaw).ljust(CODE_SIZE, b"\0")
    script = default_script()
    out[block + CODE_SIZE:block + CODE_SIZE + len(script)] = script
    base = bytearray(template) if template else bytearray(RECORD)
    base[3] = variant
    base[10] = base[11] = 0
    struct.pack_into("<3h", base, 4, *(int(round(v)) for v in position))
    struct.pack_into("<I", base, 12, address)
    records.append(bytes(base))
    blob = b"".join(records) + b"\xFF" + bytes(RECORD - 1)
    out[LIST_AT:LIST_AT + len(blob)] = blob
    struct.pack_into("<I", out, pointer_at, BASE + LIST_AT)
    return bytes(out), address, variant

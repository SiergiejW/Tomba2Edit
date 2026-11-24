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
FIRST_VARIANT = 64        # below 0x80: native contact code branches on that bit
FILE_TABLE = 0x800ECF58
MAX_CUSTOM_PARTS = 32    # pool 4: 0x140-byte record, parts at +0xC0
TALKER_RECORD = bytes((3, 9, 12)) + bytes(RECORD - 3)

# The Fishermen's Town talkers' box, used when a model's own is unknown.
DEFAULT_BOX = (80, 160, 128, 224)
BOX_AT = (0x80, 0x82, 0x84, 0x86)
SCALE_AT = (0xB8, 0xBA, 0xBC)
TRANSFORMS = (0x80051844, 0x800518FC, 0x800517F8)
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
    scale: tuple = (4096, 4096, 4096)
    transform: int = 0x800518FC

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
                if table and any(table in i for i in images):
                    out.setdefault("tables", []).append(table)
            elif target in TRANSFORMS:
                out.setdefault("transforms", []).append(target)
            else:
                _scan(images, target, depth - 1, seen, out)
            continue
        if ins.name == "sw" and ins.imm == 0x3C and ins.rt in files:
            out.setdefault("anim_files", []).append(files[ins.rt])
        elif ins.name == "sw" and ins.imm == 0x7C and regs.get(ins.rt, 0) >= BASE:
            out.setdefault("tables", []).append(regs[ins.rt])
        elif ins.name == "sh" and ins.imm in BOX_AT and ins.rt in regs:
            out.setdefault("box", {})[ins.imm] = regs[ins.rt]
        elif ins.name == "sh" and ins.imm in SCALE_AT and ins.rt in regs:
            out.setdefault("scale", {})[ins.imm] = regs[ins.rt]
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
        scale = tuple(found.get("scale", {}).get(o, 4096) for o in SCALE_AT)
        out.append(ModelChoice(names.get(handler, f"NPC 0x{handler:08X}"), handler,
                               build.file_id, build.parts, build.layout,
                               files[0], tables[0], box, scale,
                               next(iter(found.get("transforms", ())), TRANSFORMS[1])))
    return out


# ---------------------------------------------------------------- the handler

def _lines(model, script, yaw, legacy=False):
    hi = lambda v: ((v + 0x8000) >> 16) & 0xFFFF
    lo = lambda v: ((v & 0xFFFF) ^ 0x8000) - 0x8000
    table = model.anim_table
    box = "\n".join(f"addiu v0, zero, {v}\nsh v0, {o}(s0)"
                    for o, v in zip(BOX_AT, model.box))
    scale = "" if legacy else "\n".join(
        f"addiu v0, zero, {v}\nsh v0, {o}(s0)"
        for o, v in zip(SCALE_AT, model.scale))
    file_reg = "s1" if legacy else "t0"
    # New handlers store the animation bank before the builder call, so
    # the file-table pointer can use a caller-saved register.
    bank = f"lw v0, {model.anim_file * 4}({file_reg})"
    save_bank = "sw v0, 60(s0)"
    phase = ("lbu v1, 4(s0)\nnop\naddiu v1, v1, 1" if legacy
             else "addiu v1, zero, 1")
    run = ("lbu v1, 5(s0)\nnop\nbne v1, zero, SCRIPT\nnop\n"
           "lbu v0, 43(s0)\naddiu v1, zero, 3" if legacy else
           "lbu v1, 5(s0)\nlbu v0, 43(s0)\nbne v1, zero, SCRIPT\n"
           "addiu v1, zero, 3")
    # The s0 copy fills the phase load's R3000 delay slot. The native
    # dispatcher enters with v0 holding this handler's address, not zero.
    return f"""
addiu sp, sp, -32
sw s0, 16(sp)
{'sw s1, 20(sp)' if legacy else ''}
sw ra, 24(sp)
lbu v0, 4(a0)
addu s0, a0, zero
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
addiu {file_reg}, v0, {lo(FILE_TABLE)}
addu a0, s0, zero
addiu a1, zero, {model.parts}
lw a2, {model.file_id * 4}({file_reg})
{'' if legacy else bank}
lui a3, 0x{hi(model.layout):X}
{'' if legacy else save_bank}
jal 0x800519E0
addiu a3, a3, {lo(model.layout)}
bne v0, zero, END
nop
{bank if legacy else ''}
addu a0, s0, zero
lui a1, 0x{hi(table):X}
addiu a1, a1, {lo(table)}
{save_bank if legacy else ''}
jal 0x80077C40
addu a2, zero, zero
{box}
{scale}
sb zero, 11(s0)
sb zero, 43(s0)
sb zero, 71(s0)
addiu v0, zero, {yaw & 0xFFF}
sh v0, 86(s0)
addiu v0, zero, 9
sb v0, 0(s0)
{phase}
sb v1, 4(s0)
j END
nop
RUN:
{run}
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
jal 0x{(TRANSFORMS[1] if legacy else model.transform):08X}
addu a0, s0, zero
sb zero, 43(s0)
END:
lw ra, 24(sp)
{'lw s1, 20(sp)' if legacy else ''}
lw s0, 16(sp)
jr ra
addiu sp, sp, 32
"""


def _handler_code(address, model, script, yaw=0, legacy=False):
    rows, labels = [], {}
    for line in _lines(model, script, yaw, legacy).strip().splitlines():
        line = line.strip()
        if not line:
            continue
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


def _validate_model(model):
    if not 1 <= model.parts <= MAX_CUSTOM_PARTS:
        raise ValueError(f"Custom NPCs support at most {MAX_CUSTOM_PARTS} model parts.")
    if not (0 <= model.file_id < 64 and 0 <= model.anim_file < 64):
        raise ValueError("NPC resources must use file slots 0 to 63.")
    if len(model.box) != 4 or len(model.scale) != 3:
        raise ValueError("NPC bounds need four values and scale needs three values.")
    if any(not -32768 <= v <= 32767 for v in (*model.box, *model.scale)):
        raise ValueError("NPC bounds and scale must fit signed 16-bit values.")
    if model.transform not in TRANSFORMS:
        raise ValueError("Unknown NPC model transform routine.")


def handler_code(address, model, script, yaw=0):
    """The generated handler as bytes, placed at `address`."""
    _validate_model(model)
    return _handler_code(address, model, script, yaw)


# Face Tomba, show dialogue 1 entry 16, back to rest animation 0 and end.
def default_script(dialogue=1, entry=16):
    return (struct.pack("<H3h", 0x1000 | 31, 1, 256, 0)
            + struct.pack("<H3h", 0x1000 | 0, dialogue, entry, 0)
            + struct.pack("<H3h", 0x8000 | 30, 0, 0, 4))


# ------------------------------------------------------------------- the region

def region_ready(overlay):
    return overlay[REGION:REGION + len(TAG)] == TAG


def _block_index(handler):
    offset = handler - BASE - BLOCKS_AT
    if offset < 0 or offset % BLOCK or offset // BLOCK >= MAX_NPCS:
        return None
    return offset // BLOCK


def is_generated(overlay, handler):
    """Whether `handler` is one of this area's editor-created NPCs."""
    index = _block_index(handler)
    return index is not None and index in used_blocks(overlay)


def allocation_pool(model):
    """The native record pool large enough for this model's part pointers."""
    _validate_model(model)
    return 3 if model.parts <= 18 else 4


def _editable_overlay(overlay):
    if game_build.current().name != "us-retail":
        raise ValueError("Custom NPCs need the US retail layout.")
    if len(overlay) > pickup_edit.WINDOW_LIMIT:
        raise ValueError("The area file extends past the editor extension window.")
    out = bytearray(overlay)
    if len(out) < REGION_END:
        out.extend(bytes(REGION_END - len(out)))
    if not region_ready(out):
        if any(out[REGION:REGION_END]):
            raise ValueError("The NPC region is occupied by something else.")
        out[REGION:REGION + len(TAG)] = TAG
    return out


def used_blocks(overlay):
    """[block index, ...] already holding a handler."""
    if not region_ready(overlay):
        return []
    return [n for n in range(MAX_NPCS)
            if any(overlay[BLOCKS_AT + n * BLOCK:BLOCKS_AT + n * BLOCK + 8])]


def read_list(overlay, pointer):
    """[16-byte record, ...] of a 0xFF-terminated scene list."""
    at, out = pointer - BASE, []
    while 0 <= at < len(overlay):
        if overlay[at] == 0xFF:
            return out
        if at + RECORD > len(overlay):
            break
        out.append(bytes(overlay[at:at + RECORD]))
        at += RECORD
    raise ValueError("The NPC scene list is outside the area file or has no terminator.")


def add_npc(overlay, table_at, scene, model, position, yaw=0, template=None,
            replace_at=None):
    """(new overlay, handler address, variant) with one new NPC in `scene`.

    `table_at` is the overlay offset of the scene -> list pointer array.
    The scene's list is copied into the NPC region on first use and the
    pointer repointed; later NPCs append to that copy. With `replace_at`,
    replaces that scene-record offset in place instead. This explicitly
    converts a native actor into a custom talker; its native behavior and
    spawn conditions are replaced by the custom NPC's editable page."""
    pool = allocation_pool(model)
    out = _editable_overlay(overlay)
    if replace_at is not None:
        if (replace_at < 0 or replace_at + RECORD > len(overlay)
                or overlay[replace_at] == 0xFF):
            raise ValueError("The NPC scene record is outside the area file.")
        template = overlay[replace_at:replace_at + RECORD]
        records = None
    else:
        pointer_at = table_at + scene * 4
        if pointer_at < 0 or pointer_at + 4 > len(overlay):
            raise ValueError("The NPC scene pointer is outside the area file.")
        pointer = struct.unpack_from("<I", out, pointer_at)[0]
        if (pointer != BASE + LIST_AT and any(out[LIST_AT:LIST_AT + RECORD])):
            raise ValueError("Another scene already uses this area's custom NPC list.")
        records = read_list(out, pointer)
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
    base = bytearray(template) if template else bytearray(TALKER_RECORD)
    if len(base) != RECORD:
        raise ValueError("An NPC scene record must contain 16 bytes.")
    base[:3] = bytes((pool, 9, 12))
    base[3] = variant
    base[10] = base[11] = 0
    struct.pack_into("<3h", base, 4, *(int(round(v)) for v in position))
    struct.pack_into("<I", base, 12, address)
    if replace_at is not None:
        out[replace_at:replace_at + RECORD] = base
    else:
        records.append(bytes(base))
        blob = b"".join(records) + b"\xFF" + bytes(RECORD - 1)
        out[LIST_AT:LIST_AT + len(blob)] = blob
        struct.pack_into("<I", out, pointer_at, BASE + LIST_AT)
    return bytes(out), address, variant


def replace_npc(overlay, record_at, model, yaw=0):
    """Convert one bound native scene record to a custom NPC at its position."""
    if record_at < 0 or record_at + RECORD > len(overlay):
        raise ValueError("The NPC scene record is outside the area file.")
    position = struct.unpack_from("<3h", overlay, record_at + 4)
    return add_npc(overlay, 0, 0, model, position, yaw, replace_at=record_at)


def change_model(exe, overlay, handler, model):
    """Change a generated NPC's visual, retaining its edited event pages.

    Known older handlers are migrated to the current layout. The script
    pointer and the loader call (including conditional-page stubs) survive;
    scripts, scene coordinates, yaw and every other NPC stay untouched.
    Native quest actors must explicitly be converted with `replace_npc`.
    """
    from game import event_script

    pool = allocation_pool(model)
    if not is_generated(overlay, handler):
        raise ValueError("This is a native NPC. Convert it to a custom NPC before changing its model.")
    current = model_choices(exe, overlay, (handler,))
    if len(current) != 1:
        raise ValueError("The custom NPC's current model could not be read.")
    current = current[0]
    pages = [b for b in event_script.discover(exe, overlay, handler) if not b.tests]
    if len(pages) != 1:
        raise ValueError("The custom NPC's event binding could not be identified uniquely.")
    page = pages[0]
    chain, loader = event_script._call_chain(event_script.reader(exe, overlay), page.caller)
    if loader != SCRIPT_LOADER:
        raise ValueError("The custom NPC's event loader is not the expected native routine.")
    at = handler - BASE
    old = overlay[at:at + CODE_SIZE]
    instructions = [mips.Instruction(handler + off, struct.unpack_from("<I", old, off)[0])
                    for off in range(0, len(old), 4)]
    yaw = [instructions[i - 1].unsigned for i, ins in enumerate(instructions)
           if i and ins.name == "sh" and ins.rs == 16 and ins.rt == 2 and ins.imm == 86
           and instructions[i - 1].name == "addiu"
           and instructions[i - 1].rt == 2 and instructions[i - 1].rs == 0]
    if len(yaw) != 1:
        raise ValueError("The custom NPC's rotation could not be read.")
    call_word = struct.unpack_from("<I", overlay, page.caller - BASE)[0]
    matched = False
    for legacy in (False, True):
        template = bytearray(_handler_code(handler, current, page.script, yaw[0], legacy))
        if len(template) != len(old):
            continue
        caller = next((off for off in range(0, len(template), 4)
                       if mips.Instruction(handler + off, struct.unpack_from("<I", template, off)[0]).target == SCRIPT_LOADER), None)
        if caller is None or handler + caller != page.caller:
            continue
        struct.pack_into("<I", template, caller, call_word)
        candidates = [bytes(template)]
        if legacy:
            unsafe = bytearray(template)
            struct.pack_into("<II", unsafe, 0x10,
                             mips.assemble("addu s0, a0, zero", handler + 0x10),
                             mips.assemble("lbu v0, 4(s0)", handler + 0x14))
            candidates.append(bytes(unsafe))
        if old in candidates:
            matched = True
            break
    if not matched:
        raise ValueError("The custom NPC handler has unsupported native-code edits.")
    code = bytearray(handler_code(handler, model, page.script, yaw[0]))
    caller = next(off for off in range(0, len(code), 4)
                  if mips.Instruction(handler + off, struct.unpack_from("<I", code, off)[0]).target == SCRIPT_LOADER)
    struct.pack_into("<I", code, caller, call_word)
    out = _editable_overlay(overlay)
    out[at:at + CODE_SIZE] = code.ljust(CODE_SIZE, b"\0")
    # Changing the skeleton can require the larger resident actor pool.
    # Only exact generated scene-record references are changed, never code
    # words or catalogue metadata containing the handler address.
    index = _block_index(handler)
    records = [off for off in range(0, len(overlay) - RECORD + 1, 4)
               if overlay[off] in (3, 4) and overlay[off + 1:off + 3] == b"\x09\x0C"
               and overlay[off + 3] in (FIRST_VARIANT + index, 200 + index)
               and struct.unpack_from("<I", overlay, off + 12)[0] == handler]
    if not records:
        raise ValueError("The custom NPC's scene record could not be found.")
    for off in records:
        out[off] = pool
    return bytes(out)


def probe(scene, spawner):
    """(handlers, scene-list table address) from a throwaway static run of
    scene 0 - the editor's own scene may hold no simulation (fast edit)."""
    import os
    from game import actor_sim
    idx = getattr(scene, "idx_path", None) or os.path.join(os.path.dirname(scene.dat_path), "TOMBA2.IDX")
    world = actor_sim.World(scene.exe_path, scene.overlay_path, scene.dat_path, idx,
                            scene.chunk_index, scene.area_index)
    world.static_edit = True
    world.start_workers()
    world.enter_scene(spawner, scene.area_index, 0)
    world.run(12, workers=False)
    handlers = {a.handler for a in world.actors if a.parts and not a.dead}
    table = next((t for t in world._scene_tables(spawner)
                  if world._records_at(world.mem.read(t, 4)) is not None), None)
    return handlers, table, world.mem.read(table, 4) if table else None

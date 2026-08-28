"""Working out what a DAT blob is from its own bytes.

Every row in the tree is typed from here. The IDX does give each
SDAT entry an id, and the tool used to read the type off a table of
them - but those tables are per build (the demo's id 6 is a level where
retail's is an animation, and its id 7 a drawmap where retail's is
collision), so they are wrong for any build nobody has written one for,
and the trailer at the end of each IDX chunk gives no id at all. The
bytes say the same thing on every build.

Nothing in these formats is tagged - there is no magic number anywhere
on the disc - so the only thing left to read is the shape. Each format
opens with a header that has to agree with the rest of the blob: a
count that has to match a table, a table whose last pointer has to name
something that ends where the file does. Those constraints are tight
enough that a blob which holds together as one format essentially never
holds together as another, which is what makes this worth doing at all.

WHAT EACH DETECTOR ACTUALLY CHECKS

    SMST  u16 0, u16 group count, then that many u32 offsets from the
          blob start. The first offset has to be the table's own end,
          they have to climb, and every group they name has to be
          16 + tris * 36 + quads * 44 bytes long - which, added up, has
          to come out at the size of the blob. Walked to the byte.
    MDAT  a DRWA grid (see gui/drwa/drwa_parser.py) whose pointers,
          taken in order, name polygon groups that run back to back
          from the end of the grid to the end of the blob. Walked to
          the byte, without decoding any vertices.
    SCLD  u16 entry count, then a u16 word-offset per entry, and on
          some of them a final one that closes the last entry. The
          first offset has to be the table's own end either way, they
          have to climb, and each entry's own four pointers have to
          climb inside it.
    DRWB  no header at all - the only one on the disc without one. All
          four are 52 x 52 bytes of flags, so the length is the first
          test and a row lining up with the row below it is the
          second.
    BGMP  0x14-byte header whose map_size field has to equal
          width * height * 2, with four fields that are 0, 0, 0 and 2
          on every background on the disc.
    SPRT  (u16 count, u16 offset) pairs up to where the lowest offset
          begins, every sprite's 16-byte pieces inside the blob.
    TXTD  u16 root, u16 count, twelve zero bytes. root is in words from
          0x10, and has to name the end of the count's own 4-byte
          table rounded up to 16. Which of the three shapes it is comes
          from following its pointers: TXTD's reach a sub-table and
          TXT1's and TXT2's reach text, and between those two it is the
          slot width that decides, 4 bytes or 2 - the header agrees
          with either, the pointers only with one.
    ANMP  a plain u32 table of (u24 offset, u8 tag), the offsets
          climbing, the first being the table's own length. This is
          what TANP, BETP, ALFD and the map's "MDAP"/"ALFP" all are -
          one container under several names, so ANMP is as far as the
          bytes go. A labels file that knows which name a given file
          goes by says so, and the tree follows it.

Confidence is what the walk got through, not a guess: CERTAIN means the
blob was accounted for to the last byte, STRONG that everything checked
held but something (usually slack at the end) was left over, LIKELY
that the header is right and the structure plausible.

Run as a script to check the whole thing against a hand-made map:

    python -m functions.format_detect tomba2/CD examples/TOMBAMAP_us.txt
"""
import os
import struct
from dataclasses import dataclass

CERTAIN = 1.0
STRONG = 0.8
LIKELY = 0.5

# Draw codes the geometry formats use, straight out of gui/mdat/mdat.py.
TRI_CODES = frozenset((32, 34, 37, 38, 39, 48, 50, 52, 54))
QUAD_CODES = frozenset((40, 42, 44, 45, 46, 47, 56, 58, 60, 62))

TRI_SIZE = 36
QUAD_SIZE = 44

# A group inside an SMST carries twelve bytes between its counts and its
# first packet; inside an MDAT it carries none. That is the whole
# difference between the two geometry formats at the group level.
SMST_GROUP_HEADER = 16
MDAT_GROUP_HEADER = 4

# Ceilings that only exist to stop a garbage header being multiplied out
# into an enormous read. The disc's largest are 25 SMST groups, a 64x64
# drawmap, 308 SCLD entries and 1152 ANMP pointers.
MAX_SMST_GROUPS = 1024
MAX_DRWA_CELLS = 1 << 16
MAX_SCLD_ENTRIES = 4096
MAX_ANMP_POINTERS = 1 << 16

EMPTY_CELL = 0xFFFF
POINTER_UNIT = 4


@dataclass
class Match:
    """One format a blob reads as."""

    kind: str
    confidence: float
    note: str

    def __str__(self):
        return f"{self.kind} ({self.confidence:.0%}) - {self.note}"


class FormatError(ValueError):
    """A detector's way of saying the blob isn't this format. Public
    because smst_groups() below is the SMST parser's own walk too, and
    it raises this when handed something that isn't one."""


def _need(condition, message):
    if not condition:
        raise FormatError(message)


def _u16(data, at):
    _need(at + 2 <= len(data), "ran off the end")
    return struct.unpack_from("<H", data, at)[0]


def _align(value, unit):
    return (value + unit - 1) // unit * unit


# --------------------------------------------------------------------
# SMST - a flat set of polygon groups, no drawmap
# --------------------------------------------------------------------

def smst_groups(data):
    """Every group in an SMST blob, as [(index, offset, tris, quads,
    size), ...]. Raises FormatError if the blob isn't one - which is what
    the detector below runs on, and what gui/smst/smst_parser.py builds
    its meshes from."""
    _need(len(data) >= 8, "too short for an SMST header")
    zero, count = struct.unpack_from("<HH", data, 0)
    _need(zero == 0, f"first word is {zero:#x}, an SMST's is 0")
    _need(1 <= count <= MAX_SMST_GROUPS, f"{count} groups")

    table_end = 4 + count * 4
    _need(table_end <= len(data), f"{count} offsets don't fit in {len(data):#x} bytes")
    offsets = struct.unpack_from(f"<{count}I", data, 4)
    _need(offsets[0] == table_end,
          f"first offset is {offsets[0]:#x}, the table ends at {table_end:#x}")

    groups = []
    for i, offset in enumerate(offsets):
        if i:
            _need(offset > offsets[i - 1],
                  f"offset {i} ({offset:#x}) doesn't climb")
        limit = offsets[i + 1] if i + 1 < count else len(data)
        _need(offset + SMST_GROUP_HEADER <= limit,
              f"group {i} has no room for a header")
        tris, quads = struct.unpack_from("<HH", data, offset)
        size = SMST_GROUP_HEADER + tris * TRI_SIZE + quads * QUAD_SIZE
        _need(offset + size <= limit,
              f"group {i} needs {size:#x} bytes and has {limit - offset:#x}")
        groups.append((i, offset, tris, quads, size))
    return groups


def _detect_smst(data):
    groups = smst_groups(data)
    _need(any(tris or quads for _, _, tris, quads, _ in groups),
          "every group is empty")

    codes = 0
    bad = 0
    for _, offset, tris, quads, _ in groups:
        at = offset + SMST_GROUP_HEADER
        for count, size, valid in ((tris, TRI_SIZE, TRI_CODES),
                                   (quads, QUAD_SIZE, QUAD_CODES)):
            for _ in range(count):
                codes += 1
                if data[at + 3] not in valid:
                    bad += 1
                at += size
    _need(bad * 4 <= codes, f"{bad} of {codes} packets have an unknown draw code")

    used = sum(size for _, _, _, _, size in groups) + 4 + len(groups) * 4
    slack = len(data) - used
    note = (f"{len(groups)} groups, {codes} packets"
            + (f", {slack} bytes slack" if slack else ", fills the blob exactly"))
    if bad:
        note += f", {bad} unknown draw codes"
    return Match("SMST", CERTAIN if not slack and not bad else STRONG, note)


# --------------------------------------------------------------------
# MDAT - a DRWA drawmap with the level's geometry behind it
# --------------------------------------------------------------------

def _detect_mdat(data):
    _need(len(data) >= 8, "too short for a drawmap header")
    # Rows first, columns second - see gui/drwa/drwa_parser.py.
    rows, columns = struct.unpack_from("<HH", data, 0)
    _need(rows and columns, f"empty {columns}x{rows} grid")
    cells = rows * columns
    _need(cells <= MAX_DRWA_CELLS, f"{columns}x{rows} is too big for a drawmap")
    grid_end = 4 + cells * 2
    _need(grid_end <= len(data),
          f"a {columns}x{rows} grid needs {grid_end:#x} bytes, blob is {len(data):#x}")

    grid = struct.unpack_from(f"<{cells}H", data, 4)
    data_start = _align(grid_end, POINTER_UNIT)
    pointers = sorted({v for v in grid if v != EMPTY_CELL})
    _need(pointers, "no cell in the grid points at anything")
    _need(pointers[0] * POINTER_UNIT >= data_start,
          f"the lowest pointer names {pointers[0] * POINTER_UNIT:#x}, inside the grid")

    at = data_start
    tris = quads = 0
    gaps = 0
    for value in pointers:
        offset = value * POINTER_UNIT
        _need(offset >= at, f"group at {offset:#x} overlaps the one before it")
        gaps += offset - at
        _need(offset + MDAT_GROUP_HEADER <= len(data),
              f"group at {offset:#x} starts past the end of the blob")
        gtris, gquads = struct.unpack_from("<hh", data, offset)
        _need(gtris >= 0 and gquads >= 0,
              f"group at {offset:#x} has a negative count")
        size = MDAT_GROUP_HEADER + gtris * TRI_SIZE + gquads * QUAD_SIZE
        _need(offset + size <= len(data),
              f"group at {offset:#x} needs {size:#x} bytes past the end of the blob")
        tris += gtris
        quads += gquads
        at = offset + size
    _need(tris or quads, "the grid points only at empty groups")

    slack = len(data) - at
    note = (f"{columns}x{rows} drawmap, {len(pointers)} groups, "
            f"{tris} tris, {quads} quads")
    if not gaps and not slack:
        return Match("MDAT", CERTAIN, note + ", fills the blob exactly")
    return Match("MDAT", STRONG,
                 note + f", {gaps} bytes between groups, {slack} bytes slack")


# --------------------------------------------------------------------
# SCLD - collision paths
# --------------------------------------------------------------------

def _detect_scld(data):
    count = _u16(data, 0)
    _need(1 <= count <= MAX_SCLD_ENTRIES, f"{count} entries")
    _need(2 + count * 2 <= len(data),
          f"{count} pointers don't fit in {len(data):#x} bytes")
    pointers = list(struct.unpack_from(f"<{count}H", data, 2))

    # A zero pointer is a hole - parse_scld() skips those entries - so
    # only the ones that name something have to climb.
    real = [p for p in pointers if p]
    _need(real, "every pointer is zero")
    _need(all(real[i] <= real[i + 1] for i in range(len(real) - 1)),
          "the pointers don't climb")

    # Two layouts on the disc: most files close the table with one more
    # offset (the last entry's end), the rest stop at `count` and let
    # the entry run to the end of the blob. Which one this is settles
    # itself - the first entry sits right behind whichever table it is.
    closed = real[0] * 2 == 2 + (count + 1) * 2
    _need(closed or real[0] * 2 == 2 + count * 2,
          f"the first entry is at {real[0] * 2:#x}, behind neither a "
          f"{2 + count * 2:#x}- nor a {2 + (count + 1) * 2:#x}-byte table")
    if closed:
        pointers.append(_u16(data, 2 + count * 2))
        real = [p for p in pointers if p]
    pointers.append(0)      # so the last entry runs to the end of the blob
    _need(real[-1] * 2 <= len(data),
          f"the last pointer names {real[-1] * 2:#x}, past the end of the blob")

    entries = 0
    extent = 2 + count * 2
    for i in range(count):
        base = pointers[i] * 2
        if not base:
            continue
        limit = pointers[i + 1] * 2 or len(data)
        _need(base + 20 <= limit, f"entry {i} has no room for a header")
        p1, p2, p3, p4 = struct.unpack_from("<4H", data, base + 12)
        _need(20 <= p1 * 2 <= p2 * 2 <= p3 * 2 <= p4 * 2,
              f"entry {i}'s four pointers don't climb")
        _need(base + p4 * 2 <= limit, f"entry {i} runs past the next one")
        entries += 1
        extent = max(extent, base + p4 * 2)

    slack = len(data) - extent
    return Match("SCLD", CERTAIN if slack < 0x800 else STRONG,
                 f"{entries} collision entries, {slack} bytes slack")


# --------------------------------------------------------------------
# BGMP - a tiled background
# --------------------------------------------------------------------

def _detect_bgmp(data):
    _need(len(data) >= 0x14, "too short for a BGMP header")
    (texpage, clut, clut_x, clut_y, unk1, unk2,
     width, height, map_size, unk3, unk4) = struct.unpack_from("<HHHHHHBBHHH", data, 0)
    _need(width and height, f"empty {width}x{height} map")
    _need(map_size == width * height * 2,
          f"map_size {map_size:#x} doesn't match {width}x{height}")
    _need(0x14 + map_size <= len(data),
          f"a {width}x{height} map needs {0x14 + map_size:#x} bytes")
    # Constant across every background on the disc - see
    # gui/bgmp/bgmp_parser.py. They are what stops a pointer table
    # happening to have a self-consistent width and height.
    _need((unk1, unk2, unk3, unk4) == (0, 0, 0, 2),
          f"header constants are {unk1}, {unk2}, {unk3}, {unk4}, not 0, 0, 0, 2")
    # The x/y fields spell out the packed CLUT, and agree with it on
    # every file on the disc.
    _need(((clut & 0x3F) * 16, (clut >> 6) & 0x1FF) == (clut_x, clut_y),
          f"CLUT {clut:#06x} doesn't agree with ({clut_x}, {clut_y})")
    return Match("BGMP", CERTAIN,
                 f"{width}x{height} tiles from page {texpage}, "
                 f"{len(data) - 0x14 - map_size} bytes slack")


# --------------------------------------------------------------------
# SPRT - sprite pieces
# --------------------------------------------------------------------

def _detect_sprt(data):
    pairs = []
    at = 0
    table_end = 4
    lowest = None
    while True:
        _need(at + 4 <= len(data), "ran out of blob before the table ended")
        amount, offset = struct.unpack_from("<HH", data, at)
        pairs.append((amount, offset))
        at += 4
        lowest = offset if lowest is None else min(lowest, offset)
        if table_end == lowest:
            break
        _need(table_end < lowest,
              f"the table reaches {table_end:#x}, past the first sprite at {lowest:#x}")
        table_end += 4

    table_size = len(pairs) * 4
    pieces = 0
    for index, (amount, offset) in enumerate(pairs):
        _need(amount, f"sprite {index} has no pieces")
        end = offset + amount * 0x10
        _need(offset >= table_size and end <= len(data),
              f"sprite {index}'s {amount} pieces at {offset:#x} fall outside the blob")
        pieces += amount

    covered = max(offset + amount * 0x10 for amount, offset in pairs)
    slack = len(data) - covered
    return Match("SPRT", CERTAIN if slack < 0x800 else LIKELY,
                 f"{len(pairs)} sprites, {pieces} pieces, {slack} bytes slack")


# --------------------------------------------------------------------
# TXTD - dialogue
# --------------------------------------------------------------------

def _text_run(data, at, limit=120):
    """How much of the run at `at` decodes as the game's own character
    set, up to its 0xFF terminator. 1.0 is text, and a pointer table
    read as text comes out nowhere near it."""
    from gui.txtd.tombadict import letters

    known = seen = 0
    for i in range(at, min(at + limit, len(data))):
        if data[i] == 0xFF:
            break
        seen += 1
        known += data[i] in letters
    return known / seen if seen else 0.0


def _table_reads(data, flat):
    """How well the master table reads at one of its two slot widths, as
    (fraction of pointers landing on text, pointers tried).

    TXT2's table is a flat list of 2-byte pointers; TXT1's and TXTD's is
    (adr, extra) pairs, 4 bytes a slot. The header is self-consistent
    either way - `root` counts the table's own slots, so both widths
    agree with it - so the only thing that tells them apart is following
    the pointers and seeing which width lands on text."""
    root, count = struct.unpack_from("<HH", data, 0)
    step = 2 if flat else 4
    entry_root = root * step + 0x10
    if not count or entry_root > len(data):
        return 0.0, 0
    good = tried = 0
    for i in range(count):
        at = 0x10 + i * step
        if at + step > len(data):
            break
        pointer = struct.unpack_from("<H", data, at)[0]
        if pointer == 0xFFFF:
            break
        tried += 1
        target = entry_root + pointer
        if target < len(data) and _text_run(data, target) > 0.85:
            good += 1
    return (good / tried if tried else 0.0), tried


def _detect_txtd(data):
    """The text container, and which of its three shapes this is.

    All three open the same way, so the header can't tell them apart.
    What does is where the master pointers land: TXTD's go to a
    sub-table of (pointer, speaker) pairs and only that sub-table's
    entries reach the text, while TXT1's and TXT2's go straight at it.
    Between those two it comes down to the slot width - see
    _table_reads. Both tests are just following the file's own pointers
    and asking whether what they name is text."""
    _need(len(data) >= 0x10, "too short for a TXTD header")
    root, amount = struct.unpack_from("<HH", data, 0)
    _need(data[4:0x10] == bytes(12), "the twelve bytes after the header aren't zero")
    _need(amount, "no master headers")
    expected = _align(amount * 4, 0x10)
    _need((root << 2) == expected,
          f"root names {(root << 2) + 0x10:#x}, the {amount}-entry table ends at "
          f"{expected + 0x10:#x}")
    _need(0x10 + expected <= len(data),
          f"{amount} master headers don't fit in {len(data):#x} bytes")

    first = struct.unpack_from("<H", data, 0x10)[0]
    if _text_run(data, (root << 2) + 0x10 + (first << 2)) <= 0.85:
        return Match("TXTD", STRONG,
                     f"{amount} master headers, each into its own sub-table")

    flat, flat_tried = _table_reads(data, flat=True)
    paired, paired_tried = _table_reads(data, flat=False)
    if flat > paired:
        return Match("TXT2", STRONG,
                     f"{flat_tried} messages, flat 2-byte pointer table "
                     f"({flat:.0%} reach text, {paired:.0%} read as pairs)")
    return Match("TXT1", STRONG,
                 f"{paired_tried} messages, paired 4-byte table "
                 f"({paired:.0%} reach text, {flat:.0%} read flat)")


# --------------------------------------------------------------------
# DRWB - the second drawmap, one flag byte per cell
# --------------------------------------------------------------------

# All four on the disc are exactly this square - see
# gui/drwb/drwb_parser.py, where the stride is measured rather than
# assumed.
DRWB_SIDE = 52
DRWB_SIZE = DRWB_SIDE * DRWB_SIDE


def _detect_drwb(data):
    _need(len(data) == DRWB_SIZE,
          f"{len(data)} bytes, not the {DRWB_SIZE} every DRWB on the disc is")
    set_cells = sum(1 for b in data if b)
    _need(set_cells, "every cell is zero")

    # With nothing in the file to check against itself, the shape is
    # the evidence: a DRWB draws connected regions, so a cell mostly
    # matches the one a row below it. Real DRWBs come out 0.59-0.83
    # here; 2704 bytes of anything else has no reason to.
    below = sum(1 for i in range(len(data) - DRWB_SIDE)
                if data[i] == data[i + DRWB_SIDE])
    agreement = below / (len(data) - DRWB_SIDE)
    _need(agreement >= 0.5,
          f"only {agreement:.0%} of cells match the one a row below")
    return Match("DRWB", STRONG if agreement >= 0.55 else LIKELY,
                 f"{DRWB_SIDE}x{DRWB_SIDE} flag cells, {set_cells} set, "
                 f"{agreement:.0%} row agreement")


# --------------------------------------------------------------------
# ANMP - the animation pointer table TANP/BETP/ALFD all share
# --------------------------------------------------------------------

def _detect_anmp(data):
    _need(len(data) >= 8, "too short for a pointer table")
    first = struct.unpack_from("<I", data, 0)[0] & 0xFFFFFF
    _need(first and first % 4 == 0, f"first pointer is {first:#x}, not a whole table")
    count = first // 4
    _need(2 <= count <= MAX_ANMP_POINTERS, f"{count} pointers")
    _need(first <= len(data), f"a {count}-pointer table doesn't fit in {len(data):#x} bytes")

    raw = struct.unpack_from(f"<{count}I", data, 0)
    pointers = [v & 0xFFFFFF for v in raw]
    tags = {v >> 24 for v in raw}
    _need(all(pointers[i] < pointers[i + 1] for i in range(count - 1)),
          "the pointers don't climb")
    _need(pointers[-1] < len(data),
          f"the last pointer names {pointers[-1]:#x}, past the end of the blob")

    # Every one of these on the disc keeps an even stride between its
    # pointers - it is a table of fixed-size animation records - which
    # is the check that stops arbitrary increasing data landing here.
    steps = {pointers[i + 1] - pointers[i] for i in range(count - 1)}
    _need(len(steps) <= max(2, count // 8),
          f"{len(steps)} different strides between {count} pointers")
    return Match("ANMP", STRONG,
                 f"{count} pointers, stride {min(steps)}"
                 + (f"-{max(steps)}" if len(steps) > 1 else "")
                 + f", tags {', '.join(f'{t:#04x}' for t in sorted(tags))}"
                 " (TANP/BETP/ALFD share this layout)")


_DETECTORS = (
    _detect_smst,
    _detect_mdat,
    _detect_bgmp,
    _detect_scld,
    _detect_txtd,
    _detect_sprt,
    _detect_drwb,
    _detect_anmp,
)


def identify(data):
    """Every format `data` reads as, best first. Empty if none of them
    hold - which is an answer too, and the honest one for the formats
    nothing here knows how to walk."""
    matches = []
    for detector in _DETECTORS:
        try:
            matches.append(detector(data))
        except (FormatError, struct.error, IndexError):
            continue
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches


def best(data):
    """The single best reading of `data`, or None."""
    matches = identify(data)
    return matches[0] if matches else None


def identify_at(dat_path, address, size):
    """Same, for a blob still sitting in the DAT."""
    with open(dat_path, "rb") as f:
        f.seek(address)
        return identify(f.read(size))


# What the tree calls a blob nothing here can read.
UNKNOWN = "bin"


def entry_type(dat_file, address, size):
    """The type to put on one DAT entry, and a line saying how it was
    reached - what the tree labels every file with, SDAT entries and
    trail files alike.

    `dat_file` is an open binary handle. There is deliberately no id
    involved: the IDX's ids mean different things on different builds
    (the demo's id 6 is a level where retail's is an animation), and
    reading the blob answers the question for any build without needing
    a table for it."""
    try:
        dat_file.seek(address)
        matches = identify(dat_file.read(size))
    except (OSError, ValueError) as e:
        return UNKNOWN, f"0x{address:X}, {size} bytes\ncouldn't be read: {e}"
    if not matches:
        return UNKNOWN, f"0x{address:X}, {size} bytes\nreads as no known format"
    detail = "\n".join(str(m) for m in matches)
    return matches[0].kind, f"0x{address:X}, {size} bytes\n{detail}"


def reasons(data):
    """Why each detector said no, for when a blob won't read as
    anything. Keyed by the format it was refused as."""
    out = {}
    for detector in _DETECTORS:
        kind = detector.__name__.replace("_detect_", "").upper()
        try:
            detector(data)
        except FormatError as e:
            out[kind] = str(e)
        except (struct.error, IndexError) as e:
            out[kind] = f"ran off the end ({e})"
    return out


# --------------------------------------------------------------------
# Checking the whole disc against a hand-made map
# --------------------------------------------------------------------

def read_tombamap(path):
    """A TOMBAMAP txt as [(start, end, type, name), ...]. The lines are
    fixed-width: "000000-00288F : SPRP : name"."""
    entries = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\n")
            if len(line) < 20:
                continue
            try:
                start, end = int(line[:6], 16), int(line[7:13], 16)
            except ValueError:
                continue
            entries.append((start, end + 1, line[16:20].strip(), line[23:].strip()))
    return entries


# The map spells some types differently from the tool - one container,
# two names - so a check against it has to know they're the same thing.
_MAP_ALIASES = {
    "TXT1": "TXTD", "TXT2": "TXTD",
    "SPRP": "SPRT",
    "SPRD": "SPRT",
    "TAND": "ANMP", "TANP": "ANMP",
    "BETP": "ANMP",
    "ALFD": "ANMP", "ALFP": "ANMP",
    "MDAD": "ANMP", "MDAP": "ANMP",
    "UNKB": "ANMP",
}


def _main(argv):
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-1])
        return 1
    cd_folder, map_path = argv[1], argv[2]
    dat_path = os.path.join(cd_folder, "TOMBA2.DAT")
    with open(dat_path, "rb") as f:
        dat = f.read()

    entries = read_tombamap(map_path)
    agreed = disagreed = unnamed = missed = 0
    for start, end, kind, name in entries:
        matches = identify(dat[start:end])
        got = _MAP_ALIASES.get(matches[0].kind, matches[0].kind) if matches else "----"
        want = _MAP_ALIASES.get(kind, kind)
        if want == "____":
            unnamed += 1
            if matches:
                print(f"  {start:06X}-{end - 1:06X} map says nothing, reads as "
                      f"{matches[0]}  [{name}]")
            continue
        if got == want:
            agreed += 1
        elif not matches:
            missed += 1
            print(f"! {start:06X}-{end - 1:06X} map says {kind}, reads as nothing"
                  f"  [{name}]")
        else:
            disagreed += 1
            print(f"X {start:06X}-{end - 1:06X} map says {kind}, reads as "
                  f"{matches[0]}  [{name}]")

    named = agreed + disagreed + missed
    print(f"\n{agreed}/{named} named entries agree, {disagreed} disagree, "
          f"{missed} read as nothing; {unnamed} the map leaves unnamed")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))

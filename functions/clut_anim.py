"""Animated palettes - the flowing water, the waterfalls, the fires.

A room's artwork does not move. What moves is the palette a polygon
draws through: once per frame the game copies a fresh 16-colour CLUT
over the one sitting in VRAM, and every face pointing at that CLUT
changes colour together. AREA_04's harbour is the clearest case - 631 of
the 5,522 triangles its MDAT draws, 11%, are painted this way.

WHERE IT LIVES

Not in TOMBA2.DAT. The tables are in the area's overlay, the Axx.BIN
that gui.main_window.overlay_for_area() finds, and the routine that
walks them is in the same file (A00.BIN's is at 0x80115B88, and every
other overlay carrying animations holds the same routine with different
constants). An overlay may hold more than one table: A06 has two and
picks between them, which is how one room animates differently cursed
and uncursed.

    record (12 bytes, in a run of them - the count is a constant in the
    routine, not in the data, so the run's length is read off the
    records themselves):

        u32 script   where this animation's step list starts
        u32 frames   where its palettes start
        u16 x        the CLUT's position in VRAM, in halfwords, exactly
        u16 y        as psx_vram.clut_xy() gives it

    script: pairs of bytes, `frame` then `ticks`, until a frame byte of
        0xFF ends it. That last pair's second byte is how far BACK to
        jump, in bytes, so a script can loop over all of itself or keep
        an intro out of the loop. Every script on the retail disc loops
        over the whole of itself.

    frames: the palettes, 16 BGR555 halfwords each, indexed by the
        script's frame byte. The array is exactly as long as the highest
        frame the script names.

The routine keeps one countdown byte per record. Each call it decrements
it; when it runs out, it copies frames[frame] into VRAM at (x, y) as a
16x1 rectangle, sets the countdown to that step's `ticks`, and moves the
cursor on two bytes.

HOW THIS WAS READ

From six PCSX savestates taken in AREA_04 seconds apart. Diffing their
VRAM left 12 palettes changing and nothing else outside the frame
buffer; the changing values all turned up verbatim in a static table in
RAM, and the only bytes moving next to that table were 12 cursors 12
apart. Replaying the scripts from A00.BIN reproduces all 72 palettes
those six states hold, exactly.

WHAT IS NOT KNOWN is the tick rate - the routine counts calls, and how
often it is called is in code this has not followed. See TICK_HZ in
gui/mdat/mdat_viewer.py for what the viewer assumes.
"""
import os
import struct
from dataclasses import dataclass, field

from functions import psx_vram

RECORD = struct.Struct("<IIHH")
RECORD_SIZE = RECORD.size

PALETTE_BYTES = 32          # 16 entries, one halfword each
END = 0xFF                  # the script's terminator, in the frame byte

# Where an overlay's own RAM addresses can point. The overlays load just
# past MAIN.EXE, around 0x80108000 on every retail build, and the largest
# is under 0x46000 long.
RAM_LOW = 0x80010000
RAM_HIGH = 0x801FFFFF

# A frame byte at or above this ends the parse. Real scripts stay in
# single figures - the longest palette array on the disc holds 16 - so
# anything higher is a run of bytes that isn't a script.
MAX_FRAME = 0x40
MAX_STEPS = 256

# How many records in a row a run needs before its addresses are trusted
# to pin down where the overlay loads. Three is enough to make a false
# positive vanishingly unlikely, and every overlay carrying a table of
# real length has one.
ANCHOR_RECORDS = 3


class ClutAnimError(ValueError):
    """Raised when an overlay can't be read for animations."""


@dataclass
class ClutAnimation:
    """One animated palette: where it goes, and what goes there."""
    x: int
    y: int
    steps: list                      # [(frame index, ticks), ...]
    loop_start: int                  # the step the terminator jumps back to
    frames: list = field(default_factory=list)   # 32 raw bytes each
    record_offset: int = 0           # where its record is in the overlay

    @property
    def address(self):
        """Byte address of this palette in VRAM - the same number an
        MDAT face's texture_info carries."""
        return self.x * 2 + self.y * psx_vram.VRAM_STRIDE

    @property
    def intro_ticks(self):
        """Ticks before the loop starts. 0 on every script on the disc."""
        return sum(t for _f, t in self.steps[:self.loop_start])

    @property
    def loop_ticks(self):
        return sum(t for _f, t in self.steps[self.loop_start:])

    def frame_at(self, tick):
        """Which frame is standing at `tick`, counting from the start."""
        if tick < self.intro_ticks:
            start, at = 0, tick
        else:
            at = (tick - self.intro_ticks) % self.loop_ticks
            start = self.loop_start
        for frame, ticks in self.steps[start:]:
            if at < ticks:
                return frame
            at -= ticks
        return self.steps[-1][0]

    def palette_at(self, tick):
        """The 32 palette bytes standing at `tick`, laid out exactly as
        VRAM holds them."""
        return self.frames[self.frame_at(tick)]


def _record_shape(data, offset):
    """The record at `offset` as (script, frames, x, y), judged without
    knowing where the overlay loads - or None."""
    if offset + RECORD_SIZE > len(data):
        return None
    script, frames, x, y = RECORD.unpack_from(data, offset)
    if not (RAM_LOW <= script <= RAM_HIGH and RAM_LOW <= frames <= RAM_HIGH):
        return None
    if script & 1 or frames & 3:
        return None
    if x >= psx_vram.VRAM_STRIDE // 2 or x % 16 or y >= psx_vram.VRAM_ROWS:
        return None
    return script, frames, x, y


def _read_script(data, at):
    """(steps, rewind) from the script at file offset `at`, or None if
    what's there doesn't read as one."""
    steps = []
    while at + 1 < len(data) and len(steps) <= MAX_STEPS:
        frame, ticks = data[at], data[at + 1]
        if frame == END:
            return steps, ticks
        if not ticks or frame >= MAX_FRAME:
            return None
        steps.append((frame, ticks))
        at += 2
    return None


def _read_record(data, base, offset):
    """The record at `offset` as a ClutAnimation, or None if it doesn't
    hold up once `base` is applied."""
    shape = _record_shape(data, offset)
    if shape is None:
        return None
    script, frames, x, y = shape
    script_at, frames_at = script - base, frames - base
    if not 0 <= script_at < len(data) or not 0 <= frames_at < len(data):
        return None
    read = _read_script(data, script_at)
    if read is None:
        return None
    steps, rewind = read
    if not steps or not rewind or rewind > 2 * len(steps) or rewind % 2:
        return None
    count = max(f for f, _t in steps) + 1
    end = frames_at + count * PALETTE_BYTES
    if end > len(data):
        return None
    return ClutAnimation(
        x=x, y=y, steps=steps, loop_start=len(steps) - rewind // 2,
        frames=[data[frames_at + i * PALETTE_BYTES:
                     frames_at + (i + 1) * PALETTE_BYTES] for i in range(count)],
        record_offset=offset,
    )


def _table_at(data, base, offset):
    """Every record from `offset` on, until one stops reading as one."""
    out = []
    while (record := _read_record(data, base, offset)) is not None:
        out.append(record)
        offset += RECORD_SIZE
    return out


def _shape_runs(data):
    """[(offset, [shape, ...]), ...] for every run of at least
    ANCHOR_RECORDS record-shaped rows, base not yet known."""
    runs, at = [], 0
    while at + RECORD_SIZE <= len(data):
        shape = _record_shape(data, at)
        if shape is None:
            at += 4
            continue
        run, end = [], at
        while (shape := _record_shape(data, end)) is not None:
            run.append(shape)
            end += RECORD_SIZE
        if len(run) >= ANCHOR_RECORDS:
            runs.append((at, run))
        at = end
    return runs


def overlay_base(data):
    """Where this overlay is loaded in RAM, worked out from its own
    animation table, or None if it hasn't got one long enough to say.

    The frame arrays are laid out in record order and run right up to
    the table itself, so the table's own address is the last array's
    start plus its length - and the address minus the offset the table
    was found at is where the file begins. Trying each possible length
    for that last array gives a few hundred candidate bases; the one to
    keep is whichever makes the most of the run parse as records, which
    for a real table is all of it.

    Every candidate is tried rather than the first that works, because a
    wrong base can still get three records past the checks - A0F.BIN has
    one 0x60 low that does - and it never gets the whole run."""
    best = None
    for offset, run in _shape_runs(data):
        last_frames = run[-1][1]
        for count in range(1, 512):
            base = last_frames + count * PALETTE_BYTES - offset
            if not 0 <= run[0][0] - base < len(data):
                continue
            score = len(_table_at(data, base, offset))
            if score < ANCHOR_RECORDS:
                continue
            if best is None or score > best[1]:
                best = (base, score)
            if score == len(run):
                break
    return best[0] if best else None


def find_animations(data, base=None):
    """(base, [ClutAnimation]) for one overlay's bytes.

    `base` is where the overlay loads in RAM. Left out, it is worked out
    from the file - which needs a table of at least ANCHOR_RECORDS
    records, so an overlay holding only one or two animations has to be
    told (every overlay on a disc loads at the same address, so
    folder_base() below can get it from a sibling).

    Returns (None, []) rather than raising when there is nothing to
    find: most areas have no animated palettes at all."""
    if base is None:
        base = overlay_base(data)
    if base is None:
        return None, []
    found, at = [], 0
    while at + RECORD_SIZE <= len(data):
        table = _table_at(data, base, at)
        if not table:
            at += 4
            continue
        found.extend(table)
        at += len(table) * RECORD_SIZE
    return base, found


def load_animations(overlay_path, base=None):
    """find_animations() for a path.

    The base comes from the whole BIN folder rather than from this file
    alone - see folder_base(). That covers the overlays holding only one
    or two animations, which have no run long enough to work it out
    themselves, and it outvotes a single file that works out the wrong
    one."""
    with open(overlay_path, "rb") as f:
        data = f.read()
    if base is None:
        base = folder_base(os.path.dirname(overlay_path)) or overlay_base(data)
    return find_animations(data, base)


_folder_bases = {}


def folder_base(folder):
    """Where the overlays in `folder` load, by majority vote of the ones
    that can say.

    Every overlay on a disc loads at the same address - all 11 retail
    ones with a table long enough to pin it down agree on 0x80108F9C, as
    does the ram_base gui/bins/sop_editor.py carries for SOP.BIN - so
    one answer covers the folder, and a file that disagrees with the
    rest is wrong rather than special.

    Cached: this reads the whole BIN folder to answer, and the answer is
    the same for every area on the disc."""
    if folder in _folder_bases:
        return _folder_bases[folder]
    votes = {}
    try:
        names = sorted(n for n in os.listdir(folder) if n.upper().endswith(".BIN"))
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(folder, name), "rb") as f:
                found = overlay_base(f.read())
        except OSError:
            continue
        if found is not None:
            votes[found] = votes.get(found, 0) + 1
    base = max(votes, key=votes.get) if votes else None
    _folder_bases[folder] = base
    return base

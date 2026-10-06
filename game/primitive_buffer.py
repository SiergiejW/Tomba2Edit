"""Room for heavy frames in the primitive buffer, for US retail MAIN.EXE.

THE LIMIT

Every GPU packet of a frame - actors, Tomba, scenery, effects, backdrop,
UI - is written into one of two 0x14000-byte buffers at 0x800BFE68. The
game swaps between them so the GPU can draw one frame while the next is
built. 81,920 bytes is therefore all a frame may hold, nothing checks
it, and Tomba's own state sits straight after the second buffer, so an
overflow lands on him.

The stock game runs close to it. Measured in DuckStation with the
widescreen hack, the Town opening peaks at 80,432 bytes on an untouched
disc - 1,488 to spare. Scenery denser than the original goes over there,
which is what froze the full-detail Village port.

THE PATCH

main()'s loop calls a short routine instead of working the buffer's
address out from the bank number. The routine looks at the frame that
was just built:

    65,536 bytes or less   the stock layout: this frame takes the half
                           the last one did not, and the GPU draws that
                           one meanwhile. Nothing changes.
    more                   DrawSync(0) first, then start at the bottom
                           with all 0x28000 bytes free.

Waiting for the GPU is what a frame over 81,920 needs anyway - two of
them cannot be in the buffer at once - and doing it only after a heavy
frame keeps everything else at full speed. Waiting on every frame was
tried and costs a third of the frame rate.

What it does not cover: a frame that jumps from under 65,536 to over
81,920 in one step is still in the stock layout and overflows as before.
Scenes get heavy gradually - the Village's worst frames all followed
frames over 75,000 - but a hard cut into a very heavy view could do it.

The routine lives in the GPU library's debug strings at 0x8001BE58,
which are printed only at debug level 2 and the game sets 0.
0x800BF4F4, which held the last frame's end and was never read, now
holds where the current frame starts.

    python -m game.primitive_buffer MAIN.EXE MAIN.patched.EXE
"""
import struct
import sys

HEADER = 0x800
LOOP = 0x80050C80
ROUTINE = 0x8001BE58
THRESHOLD = 0x10000

STOCK_BYTES = 0x14000
PATCHED_BYTES = 0x28000

_DRAWSYNC, _INPUT, _WORKERS = 0x80080F6C, 0x800788AC, 0x80051E60


def _jal(target):
    return (3 << 26) | ((target >> 2) & 0x3FFFFFF)


# lw cursor; lbu bank; ...; bank * 0x14000 + base; input; workers; DrawSync
LOOP_STOCK = struct.pack(
    "<18I", 0x8E83F544, 0x92240135, 0x3C02800C, 0xA6C0809C, 0xAC43F4F4,
    0x00041880, 0x00641821, 0x00031B80, 0x3C02800C, 0x2442FE68, 0x00621821,
    0x00651824, 0x0C01E22B, 0xAE83F544, 0x0C014798, 0x00000000, 0x0C0203DB,
    0x00002021)
# counter reset; the routine; input; workers; the old DrawSync stays
LOOP_PATCHED = struct.pack(
    "<18I", 0xA6C0809C, _jal(ROUTINE), 0, _jal(_INPUT), 0, _jal(_WORKERS), 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0x0C0203DB, 0x00002021)

STRINGS = (b"ResetGraph(%d)...\n\0\0"
           b"SetGraphDebug:level:%d,type:%d reverse:%d\n\0\0"
           b"SetGrapQue(%d)...\n\0\0"
           b"DrawSyncCallback(%08x)...\n\0\0")
ROUTINE_CODE = struct.pack(
    "<28I",
    0x3C08800C,                 # lui   t0, 0x800C
    0x8D03F544,                 # lw    v1, cursor        where the last frame ended
    0x8D09F4F4,                 # lw    t1, start         where it began
    0x3C0A000C,                 # lui   t2, 0x000C
    0x254AFE68,                 # addiu t2, t2, -408      the buffer
    0x00695823,                 # subu  t3, v1, t1        its length
    0x3C0C0000 | THRESHOLD >> 16,   # lui t4, THRESHOLD
    0x018B602B,                 # sltu  t4, t4, t3
    0x1180000B,                 # beq   t4, zero, light
    0x00000000,
    0x27BDFFE8,                 # heavy: DrawSync(0), then the bottom
    0xAFBF0010,
    _jal(_DRAWSYNC),
    0x00002021,
    0x8FBF0010,
    0x27BD0018,
    0x3C08800C,
    0x3C03000C,
    0x10000006,                 # b     store
    0x2463FE68,
    0x152A0004,                 # light: bne t1, t2, store   last was the top half
    0x01401821,                 # addu  v1, t2, zero         so take the bottom
    0x3C0C0001,
    0x358C4000,
    0x014C1821,                 # addu  v1, t2, t4           otherwise the top
    0xAD03F544,                 # store: sw v1, cursor
    0x03E00008,                 # jr    ra
    0xAD03F4F4)                 # sw    v1, start
assert len(ROUTINE_CODE) <= len(STRINGS)


class PatchError(ValueError):
    """Raised when the executable is not one this patch knows."""


def _offsets(exe):
    if exe[:8] != b"PS-X EXE":
        raise PatchError("this is not a PS-X executable")
    load = struct.unpack_from("<I", exe, 0x18)[0]
    return HEADER + LOOP - load, HEADER + ROUTINE - load


def is_applied(exe):
    loop, routine = _offsets(exe)
    return (bytes(exe[loop:loop + len(LOOP_PATCHED)]) == LOOP_PATCHED
            and bytes(exe[routine:routine + len(ROUTINE_CODE)]) == ROUTINE_CODE)


def apply(exe):
    """`exe` with the patch. Already patched comes back as is."""
    if is_applied(exe):
        return bytes(exe)
    loop, routine = _offsets(exe)
    if (bytes(exe[loop:loop + len(LOOP_STOCK)]) != LOOP_STOCK
            or bytes(exe[routine:routine + len(STRINGS)]) != STRINGS):
        raise PatchError(
            f"main()'s loop is not at 0x{LOOP:08X} - this patch is for US "
            "retail (SCUS-94454) only")
    out = bytearray(exe)
    out[loop:loop + len(LOOP_PATCHED)] = LOOP_PATCHED
    out[routine:routine + len(ROUTINE_CODE)] = ROUTINE_CODE
    return bytes(out)


def remove(exe):
    """`exe` with the stock loop and the strings back."""
    loop, routine = _offsets(exe)
    if bytes(exe[loop:loop + len(LOOP_STOCK)]) == LOOP_STOCK:
        return bytes(exe)
    if not is_applied(exe):
        raise PatchError("this executable does not carry the patch")
    out = bytearray(exe)
    out[loop:loop + len(LOOP_STOCK)] = LOOP_STOCK
    out[routine:routine + len(STRINGS)] = STRINGS
    return bytes(out)


def _main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    with open(argv[1], "rb") as f:
        exe = f.read()
    with open(argv[2], "wb") as f:
        f.write(apply(exe))
    print(f"{argv[2]}: heavy frames may use {PATCHED_BYTES:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

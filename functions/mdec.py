"""The PlayStation's video codec, in software.

The three movies on this disc - LOGO.STR, OP.STR, END.STR - are MDEC
video: 320x240, every frame coded on its own, no motion compensation and
nothing carried over from the frame before. That last part is what makes
a timeline worth having, since seeking to a frame costs exactly one
frame's work rather than a decode from the last keyframe.

A frame is a bitstream of MPEG-1 style run/level codes (ISO 11172-2
table B.14, the same table MPEG-1 uses for intra blocks) over 8x8 DCT
blocks, laid out as 4:2:0 macroblocks. Three things about it are the
PlayStation's own and catch people out:

  - the bitstream is stored as 16-bit little-endian words, so the bytes
    come in pairs the wrong way round and have to be swapped before a
    normal MSB-first bit reader will see the codes;
  - macroblocks run DOWN each column and then across, not across rows;
  - each block opens with its DC as a plain signed 10-bit number rather
    than as a difference from the block before, the way MPEG-1 codes it.
    One quantisation scale, out of the frame header, covers the whole
    frame. (That is version 2, which is what this disc uses; version 3
    codes the DC as a difference and is not handled here.)

Decoding is checked against ffmpeg's own mdec decoder frame for frame -
see the module test at the bottom of functions/psxstr.py.
"""
import numpy as np

VERSIONS = (2,)

# ISO 11172-2 table B.14 - the run/level codes. 111 of them, plus an
# escape and an End Of Block, and together they use up the whole code
# space bar the twelve leading zeroes reserved for start codes.
#
# Laid out the way the standard tabulates it: the (run, level) pairs in
# order, run by run, and the (code, bit length) each one is written as -
# with the sign bit that follows every code left off. The whole table is
# here rather than the codes these three movies happen to use, since a
# code that never appears in LOGO turns up in END.
EOB = "10"
ESCAPE = "000001"

_RUN_LEVEL = (
    [(0, level) for level in range(1, 41)]
    + [(1, level) for level in range(1, 19)]
    + [(2, level) for level in range(1, 6)]
    + [(3, level) for level in range(1, 5)]
    + [(4, level) for level in range(1, 4)]
    + [(5, level) for level in range(1, 4)]
    + [(6, level) for level in range(1, 4)]
    + [(run, level) for run in range(7, 17) for level in (1, 2)]
    + [(run, 1) for run in range(17, 32)]
)

_CODES = (
    (0x3, 2), (0x4, 4), (0x5, 5), (0x6, 7),
    (0x26, 8), (0x21, 8), (0xa, 10), (0x1d, 12),
    (0x18, 12), (0x13, 12), (0x10, 12), (0x1a, 13),
    (0x19, 13), (0x18, 13), (0x17, 13), (0x1f, 14),
    (0x1e, 14), (0x1d, 14), (0x1c, 14), (0x1b, 14),
    (0x1a, 14), (0x19, 14), (0x18, 14), (0x17, 14),
    (0x16, 14), (0x15, 14), (0x14, 14), (0x13, 14),
    (0x12, 14), (0x11, 14), (0x10, 14), (0x18, 15),
    (0x17, 15), (0x16, 15), (0x15, 15), (0x14, 15),
    (0x13, 15), (0x12, 15), (0x11, 15), (0x10, 15),
    (0x3, 3), (0x6, 6), (0x25, 8), (0xc, 10),
    (0x1b, 12), (0x16, 13), (0x15, 13), (0x1f, 15),
    (0x1e, 15), (0x1d, 15), (0x1c, 15), (0x1b, 15),
    (0x1a, 15), (0x19, 15), (0x13, 16), (0x12, 16),
    (0x11, 16), (0x10, 16), (0x5, 4), (0x4, 7),
    (0xb, 10), (0x14, 12), (0x14, 13), (0x7, 5),
    (0x24, 8), (0x1c, 12), (0x13, 13), (0x6, 5),
    (0xf, 10), (0x12, 12), (0x7, 6), (0x9, 10),
    (0x12, 13), (0x5, 6), (0x1e, 12), (0x14, 16),
    (0x4, 6), (0x15, 12), (0x7, 7), (0x11, 12),
    (0x5, 7), (0x11, 13), (0x27, 8), (0x10, 13),
    (0x23, 8), (0x1a, 16), (0x22, 8), (0x19, 16),
    (0x20, 8), (0x18, 16), (0xe, 10), (0x17, 16),
    (0xd, 10), (0x16, 16), (0x8, 10), (0x15, 16),
    (0x1f, 12), (0x1a, 12), (0x19, 12), (0x17, 12),
    (0x16, 12), (0x1f, 13), (0x1e, 13), (0x1d, 13),
    (0x1c, 13), (0x1b, 13), (0x1f, 16), (0x1e, 16),
    (0x1d, 16), (0x1c, 16), (0x1b, 16),
)

_TABLE = {format(code, f"0{width}b"): pair
          for pair, (code, width) in zip(_RUN_LEVEL, _CODES)}
assert len(_RUN_LEVEL) == len(_CODES) == len(_TABLE) == 111

# The MPEG-1 default intra quantisation matrix, in raster order, and the
# zig-zag that says which coefficient a code's position names.
QUANT = np.array([
    2, 16, 19, 22, 26, 27, 29, 34,
    16, 16, 22, 24, 27, 29, 34, 37,
    19, 22, 26, 27, 29, 34, 34, 38,
    22, 22, 26, 27, 29, 34, 37, 40,
    22, 26, 27, 29, 32, 35, 40, 48,
    26, 27, 29, 32, 35, 40, 48, 58,
    26, 27, 29, 34, 38, 46, 56, 69,
    27, 29, 35, 38, 46, 56, 69, 83,
], dtype=np.int32)

ZIGZAG = np.array([
    0,  1,  8, 16,  9,  2,  3, 10,
    17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
], dtype=np.int32)

# What a code means, looked up by the next 16 bits of the stream:
# (run, level, bits used by the code itself). A code is at most 16 bits
# long, so 16 bits is always enough to recognise one; the sign bit that
# follows it is read separately. run is -1 for End Of Block and -2 for
# the escape, which carries its own run and level.
_LOOKUP = [None] * 65536


def _build_lookup():
    for bits, (run, level) in _TABLE.items():
        _fill(bits, run, level)
    _fill(EOB, -1, 0)
    _fill(ESCAPE, -2, 0)


def _fill(bits, run, level):
    width = len(bits)
    base = int(bits, 2) << (16 - width)
    entry = (run, level, width)
    for i in range(1 << (16 - width)):
        _LOOKUP[base + i] = entry


_build_lookup()

# x -> u basis of the 8-point inverse DCT, so a block is A.T @ F @ A.
_C = np.ones(8)
_C[0] = 1.0 / np.sqrt(2.0)
_BASIS = (_C[:, None] * np.cos(
    (2 * np.arange(8)[None, :] + 1) * np.arange(8)[:, None] * np.pi / 16)) / 2.0


class MdecError(Exception):
    """Raised when a frame's bitstream does not decode."""


class _Bits:
    """An MSB-first bit reader over the byte-swapped bitstream.

    Kept as a byte string and a bit position rather than one big int:
    a frame is up to 10 KB and shifting an integer that wide once per
    code is far slower than slicing four bytes out of it."""

    __slots__ = ("data", "pos", "end")

    def __init__(self, data):
        # 16-bit little-endian words: swap each pair so an MSB-first
        # reader sees the codes the encoder wrote.
        if len(data) & 1:
            data = data + b"\0"
        swapped = bytearray(data)
        swapped[0::2], swapped[1::2] = data[1::2], data[0::2]
        # Padding, so a peek near the end never runs short.
        self.data = bytes(swapped) + b"\0\0\0\0"
        self.pos = 0
        self.end = len(data) * 8

    def peek16(self):
        byte = self.pos >> 3
        window = int.from_bytes(self.data[byte:byte + 4], "big")
        return (window >> (16 - (self.pos & 7))) & 0xFFFF

    def read(self, count):
        byte = self.pos >> 3
        window = int.from_bytes(self.data[byte:byte + 4], "big")
        value = (window >> (32 - count - (self.pos & 7))) & ((1 << count) - 1)
        self.pos += count
        return value


def _sign10(value):
    return value - 1024 if value & 0x200 else value


def frame_header(data):
    """(codes, quant_scale, version) from a demuxed frame's first eight
    bytes, or None if they are not an MDEC frame header."""
    if len(data) < 8:
        return None
    codes = int.from_bytes(data[0:2], "little")
    magic = int.from_bytes(data[2:4], "little")
    quant = int.from_bytes(data[4:6], "little")
    version = int.from_bytes(data[6:8], "little")
    if magic != 0x3800:
        return None
    return codes, quant, version


def decode(data, width, height):
    """One demuxed frame's bytes as an (h, w, 3) uint8 RGB array.

    `data` starts at the 8-byte frame header, which is what the STR
    demuxer hands over."""
    header = frame_header(data)
    if header is None:
        raise MdecError("no 0x3800 frame header - this is not an MDEC frame")
    _codes, quant, version = header
    if version not in VERSIONS:
        raise MdecError(f"MDEC version {version} is not handled (only "
                        f"{', '.join(str(v) for v in VERSIONS)})")

    mb_w, mb_h = (width + 15) // 16, (height + 15) // 16
    blocks = _decode_blocks(_Bits(data[8:]), mb_w * mb_h * 6, quant)
    return _to_rgb(blocks, mb_w, mb_h, width, height)


def _decode_blocks(bits, count, qscale):
    """Every block of a frame, as a (count, 64) array of dequantised
    coefficients in raster order.

    One Python loop over the codes - there is no way around that, the
    codes being variable length - and everything after it in numpy."""
    out = np.zeros((count, 64), dtype=np.float32)
    lookup = _LOOKUP
    zigzag = ZIGZAG
    quant = QUANT
    end = bits.end

    for block in range(count):
        # The DC, doubled because the matrix quantises it by 2, plus the
        # 1024 that becomes the +128 every plane is centred on.
        out[block, 0] = 2 * _sign10(bits.read(10)) + 1024
        index = 0
        while True:
            if bits.pos >= end:
                raise MdecError(f"ran off the end of the frame in block "
                                f"{block} of {count}")
            entry = lookup[bits.peek16()]
            if entry is None:
                raise MdecError(
                    f"unknown code {bits.peek16():016b} in block {block}")
            run, level, width = entry
            if run == -1:                       # end of block
                bits.pos += width
                break
            if run == -2:                       # escape: 6-bit run, 10-bit level
                bits.pos += width
                run = bits.read(6)
                level = _sign10(bits.read(10))
                index += run + 1
                if index > 63:
                    raise MdecError(f"coefficient {index} past the end of "
                                    f"block {block}")
                position = zigzag[index]
                magnitude = abs(level) * qscale * quant[position] >> 3
                out[block, position] = -magnitude if level < 0 else magnitude
                continue
            bits.pos += width
            negative = bits.read(1)
            index += run + 1
            if index > 63:
                raise MdecError(f"coefficient {index} past the end of "
                                f"block {block}")
            position = zigzag[index]
            magnitude = level * qscale * quant[position] >> 3
            out[block, position] = -magnitude if negative else magnitude
    return out


def _to_rgb(blocks, mb_w, mb_h, width, height):
    """Dequantised blocks -> an RGB image.

    Macroblocks are stored down each column and then across, and each
    one holds Cr, Cb and then its four luma quarters."""
    pixels = (_BASIS.T @ blocks.reshape(-1, 8, 8) @ _BASIS)

    # (mb_x, mb_y, block) is the order they were decoded in - down each
    # column, then across.
    pixels = pixels.reshape(mb_w, mb_h, 6, 8, 8)

    # Blocks 2..5 are the macroblock's four luma quarters, in reading
    # order, so they fold straight into a (2, 2) of 8x8 tiles.
    luma = (pixels[:, :, 2:6].reshape(mb_w, mb_h, 2, 2, 8, 8)
            .transpose(1, 2, 4, 0, 3, 5).reshape(mb_h * 16, mb_w * 16))

    def plane(index):
        return (pixels[:, :, index].transpose(1, 2, 0, 3)
                .reshape(mb_h * 8, mb_w * 8))

    cb = np.repeat(np.repeat(plane(1), 2, axis=0), 2, axis=1)
    cr = np.repeat(np.repeat(plane(0), 2, axis=0), 2, axis=1)

    y = np.clip(luma, 0, 255)
    cb = np.clip(cb, 0, 255) - 128.0
    cr = np.clip(cr, 0, 255) - 128.0

    rgb = np.empty(y.shape + (3,), dtype=np.float32)
    rgb[..., 0] = y + 1.402 * cr
    rgb[..., 1] = y - 0.344136 * cb - 0.714136 * cr
    rgb[..., 2] = y + 1.772 * cb
    return np.clip(rgb[:height, :width] + 0.5, 0, 255).astype(np.uint8)

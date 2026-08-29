"""Compression used by TOMBA2.IMG, both ways.

An IMG chunk holds one area's VRAM as a few rectangular shards, each
compressed on its own. A shard is a stream of instruction bytes:

    bits 7..3   amount, 0-31
    bits 2..0   how

`how` 0 means the next `amount` bytes are literal. Anything else copies
`amount` bytes from earlier in the output it is building, starting
`back(how)` bytes behind the write head:

    how  1  ->  1
    how  2  ->  magic          magic = width * 2, the shard's row in bytes
    how  3  ->  magic + 1
    how  4  ->  magic + 2
    how  5  ->  magic + 3
    how  6  ->  magic - 1
    how  7  ->  magic - 2

So it is LZ77 with the match distance restricted to those seven values:
the previous byte, or the row above give or take three columns. Copies
may overlap the write head - going back 1 for 8 bytes is a run of the
same byte - so they are made a byte at a time.

Reading before the start of the output yields zero, which is how the
first row compresses at all.

Thanks to vervalkon (Tomba Club) for working out the instruction format.
"""
import struct

# Longest run one instruction can carry (the amount field is 5 bits).
MAX_RUN = 31

# The seven copy distances, indexed by `how`. Entry 0 is the literal
# case and is never used as a distance.
_HOW_COUNT = 8


def distances(width):
    """The copy distance each `how` selects, for a shard of this width.

    Index 0 is the literal instruction and is None."""
    magic = width * 2
    return [None, 1, magic, magic + 1, magic + 2, magic + 3,
            magic - 1, magic - 2]


def decompress(data, offset, packed_size, width):
    """One shard's pixels, reading `packed_size` bytes from `offset`.

    `packed_size` is the room the shard was given rather than the length
    it actually uses, so decoding stops when that many bytes have been
    consumed."""
    back = distances(width)
    out = bytearray()
    pos = offset
    end = offset + packed_size
    while pos < end:
        control = data[pos]
        pos += 1
        amount = control >> 3
        how = control & 0x07
        if how == 0:
            out += data[pos:pos + amount]
            pos += amount
            continue
        start = len(out) - back[how]
        for i in range(amount):
            src = start + i
            out.append(out[src] if 0 <= src < len(out) else 0)
    return bytes(out)


def compress(pixels, width):
    """`pixels` as an instruction stream this module can decompress.

    Greedy: at each position take the longest of the seven copies, and
    fall back to literals where none of them saves anything. A copy is
    worth one byte against `amount` bytes of literal, so it pays from
    two bytes up - at one byte it only breaks even, and taking it would
    end a literal run that could have absorbed it for nothing."""
    back = distances(width)
    out = bytearray()
    literal = bytearray()
    size = len(pixels)
    pos = 0

    def flush():
        while literal:
            take = literal[:MAX_RUN]
            del literal[:MAX_RUN]
            out.append((len(take) << 3) | 0)
            out.extend(take)

    while pos < size:
        best_len = 0
        best_how = 0
        limit = min(MAX_RUN, size - pos)
        for how in range(1, _HOW_COUNT):
            src = pos - back[how]
            if src >= pos:
                continue
            n = 0
            while n < limit:
                i = src + n
                byte = pixels[i] if 0 <= i < size else 0
                if i >= pos and i >= size:
                    break
                if byte != pixels[pos + n]:
                    break
                n += 1
            if n > best_len:
                best_len = n
                best_how = how

        if best_len >= 2:
            flush()
            out.append((best_len << 3) | best_how)
            pos += best_len
        else:
            literal.append(pixels[pos])
            pos += 1
            if len(literal) == MAX_RUN:
                flush()
    flush()
    return bytes(out)


def read_chunk_header(data):
    """(x, y, width, height, packed size) per shard, and where the
    shard data starts.

    The header is a count followed by that many 12-byte records, padded
    out to 0x800."""
    count = struct.unpack_from("<I", data, 0)[0]
    shards = [struct.unpack_from("<HHHHI", data, 4 + i * 12)
              for i in range(count)]
    return shards, 0x800


def decompress_chunk(data):
    """Every shard of an IMG chunk as (header, pixels)."""
    shards, pos = read_chunk_header(data)
    out = []
    for x, y, width, height, packed in shards:
        out.append(((x, y, width, height, packed),
                    decompress(data, pos, packed, width)))
        pos += packed
    return out

"""Rebuilding a raw CD sector's error-correction fields.

A 2352-byte Mode 2 Form 1 sector carries, after its 2048 bytes of user
data, an EDC checksum and two blocks of Reed-Solomon parity. Change the
data and those stop matching, so anything that writes into a raw track
has to put them back.

    0..11      sync
    12..15     header - minute, second, frame, mode
    16..23     subheader, stored twice
    24..2071   user data
    2072..2075 EDC, over bytes 16..2071
    2076..2247 P parity
    2248..2351 Q parity

The parity is computed with the header treated as zero, which is what
Form 1 requires and what makes the same routine work whatever address a
sector sits at. Both algorithms are ECMA-130; the tables below are the
usual formulation of it.
"""
import struct

SECTOR = 2352
DATA_AT = 24
DATA_LEN = 2048
EDC_AT = 0x818          # 2072
P_AT = 0x81C            # 2076
Q_AT = 0x8C8            # 2248

MODE2_FORM1 = 1

# GF(2^8) with x^8 + x^4 + x^3 + x^2 + 1, and the EDC's reversed CRC-32.
_ECC_F = bytearray(256)
_ECC_B = bytearray(256)
_EDC = [0] * 256
for _i in range(256):
    _j = ((_i << 1) ^ (0x11D if _i & 0x80 else 0)) & 0xFF
    _ECC_F[_i] = _j
    _ECC_B[_i ^ _j] = _i
    _e = _i
    for _k in range(8):
        _e = (_e >> 1) ^ (0xD8018001 if _e & 1 else 0)
    _EDC[_i] = _e


def edc(data, seed=0):
    """The EDC checksum of a run of bytes."""
    value = seed
    for byte in data:
        value = (value >> 8) ^ _EDC[(value ^ byte) & 0xFF]
    return value


def _parity(sector, major_count, minor_count, major_mult, minor_inc, at):
    """One Reed-Solomon block, written into the sector at `at`."""
    size = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        a = b = 0
        for _minor in range(minor_count):
            temp = sector[12 + index]
            index += minor_inc
            if index >= size:
                index -= size
            a ^= temp
            b ^= temp
            a = _ECC_F[a]
        a = _ECC_B[_ECC_F[a] ^ b]
        sector[at + major] = a
        sector[at + major + major_count] = a ^ b


def rebuild(sector):
    """Put a Mode 2 Form 1 sector's EDC and parity back. Edits in place.

    The four header bytes are zeroed for the parity pass and restored
    after, which is what Form 1 specifies."""
    sector = bytearray(sector)
    struct.pack_into("<I", sector, EDC_AT, edc(sector[16:EDC_AT]))
    header = bytes(sector[12:16])
    sector[12:16] = b"\0\0\0\0"
    _parity(sector, 86, 24, 2, 86, P_AT)
    _parity(sector, 52, 43, 86, 88, Q_AT)
    sector[12:16] = header
    return sector


SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def _bcd(value):
    return ((value // 10) << 4) | (value % 10)


def msf(lba):
    """A sector's address as the header stores it: BCD minute, second,
    frame, counting from the 2-second lead-in."""
    total = lba + 150
    return (_bcd(total // 4500), _bcd((total % 4500) // 75), _bcd(total % 75))


def make(lba, payload):
    """A fresh Mode 2 Form 1 sector, for extending a track.

    Built rather than copied because past the end of the image there is
    nothing to copy: sync pattern, the sector's own address, a data
    subheader stored twice as Mode 2 requires, then the payload and its
    error correction."""
    sector = bytearray(SECTOR)
    sector[0:12] = SYNC
    minute, second, frame = msf(lba)
    sector[12:16] = bytes((minute, second, frame, 2))
    # file 1, channel 0, submode "data", coding 0 - and again, as Mode 2
    # keeps two copies of the subheader.
    sector[16:24] = bytes((1, 0, 0x08, 0, 1, 0, 0x08, 0))
    sector[DATA_AT:DATA_AT + DATA_LEN] = payload.ljust(DATA_LEN, b"\0")
    return rebuild(sector)


def is_form1(sector):
    """Whether a raw sector is Mode 2 Form 1 - the kind with parity."""
    return len(sector) == SECTOR and not (sector[18] & 0x20)

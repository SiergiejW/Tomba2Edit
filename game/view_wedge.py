"""Narrowing Town's view wedge, for a level denser than Town.

Every frame Town lists the drawmap cells inside a triangle in front of
the camera and transforms every polygon in them (see
formats/drawmaps/visibility.py). The triangle is 40 degrees either side
of the heading; the screen shows 24.6 (4:3) or 31.4 (DuckStation's 16:9
hack). The rest is transformed and thrown away, which costs nothing much
in Town - 860 to 1,170 polygons a frame along the opening route - and a
lot in a level that packs more into each cell: the full-detail Village
runs 1,400 to 2,000, and the frames come late.

At 32 degrees the Village is 760 to 1,480 and no cell with anything on
screen is dropped, in either aspect ratio, along that route. 28 degrees
is safe for 4:3 only. The triangle's sides stay as long as they were, so
a narrower one reaches about a tenth further straight ahead: in Town
itself that shows a few more distant polygons, never fewer.

The half-angle is an immediate in A00.BIN, set in two places of the one
routine (0x8013F190 for location 1, 0x8013F244 for the default), in the
GTE's 4096-to-a-turn angles.

    python -m game.view_wedge A00.BIN A00.narrow.BIN [degrees]
"""
import struct
import sys

# File offsets of `addiu rt, zero, 0x1C7` (overlay base 0x80108F9C).
SITES = (0x361F4, 0x362A8)
STOCK = 0x1C7
NARROW = 0x16C          # 32 degrees


class WedgeError(ValueError):
    """Raised when the overlay is not US retail's A00.BIN."""


def angle(degrees):
    return round(degrees * 4096 / 360)


def half_angle(overlay):
    """The half-angle the overlay sets, or raise if it is not A00.BIN."""
    found = set()
    for at in SITES:
        word = struct.unpack_from("<I", overlay, at)[0]
        if word >> 26 != 0x09 or (word >> 21) & 31:
            raise WedgeError("this is not US retail's A00.BIN")
        found.add(word & 0xFFFF)
    if len(found) != 1:
        raise WedgeError("the overlay's two half-angles differ")
    return found.pop()


def narrow(overlay, value=NARROW):
    """`overlay` with the view half-angle set to `value`."""
    half_angle(overlay)
    if not 0 < value <= STOCK:
        raise WedgeError("a half-angle wider than stock lists more, not less")
    out = bytearray(overlay)
    for at in SITES:
        struct.pack_into("<H", out, at, value)
    return bytes(out)


def _main(argv):
    if len(argv) not in (3, 4):
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    with open(argv[1], "rb") as f:
        overlay = f.read()
    value = angle(float(argv[3])) if len(argv) == 4 else NARROW
    with open(argv[2], "wb") as f:
        f.write(narrow(overlay, value))
    print(f"{argv[2]}: view half-angle {value * 360 / 4096:.1f} degrees")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

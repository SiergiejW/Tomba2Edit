"""The console's own 16x15 font, which is what the Japanese disc draws.

The Latin builds draw every character they show out of the font page in
TOMBA2.IMG (see functions/fontpage.py). SLPS-02350 cannot: its page has
an 8x8 kana system font and an 8x16 Latin grid, and not one kanji
anywhere on it, while its dialogue is full of them.


    layout      30 bytes a glyph, 15 rows of two bytes, bit 15 of the
                first byte being the leftmost pixel
    order       JIS X 0208 ku-ten, counting only the cells that are
                assigned - unassigned cells take no room. Index 0 is
                ku 1 ten 1.
    extent      3,549 glyphs, which is as many as fit: the table starts
                at ROM offset 0x66000 and ends 26 bytes short of the end
                of a 512 KB BIOS, part way into ku 48.

"""
import os
import struct
import sys
import zlib

GLYPH_W = 16
GLYPH_H = 15
GLYPH_BYTES = 30
COUNT = 3549

# Where the table sits in a Japanese BIOS ROM. Only ever used by
# extract(); nothing at runtime needs a BIOS.
BIOS_OFFSET = 0x66000

FONT_FILE = "psx-kanji.bin"

_font = None            # the raw table, once read
_index = None           # {sjis code: glyph number}


class BiosFontError(Exception):
    """Raised when the font table can't be read."""


def fonts_dir():
    """The built-in fonts/ folder, next to the code - or inside the
    bundle when this is running as a built exe."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "fonts")


def _kuten_to_sjis(ku, ten):
    """The Shift-JIS code for a ku/ten cell, assigned or not."""
    lead = (ku + 257) // 2 if ku <= 62 else (ku + 385) // 2
    if ku % 2:
        trail = ten + 63 + (1 if ten >= 64 else 0)
    else:
        trail = ten + 158
    return (lead << 8) | trail


def index():
    """{Shift-JIS code: glyph number}, built once.

    The codec decides what "assigned" means, which is the whole of the
    ordering: a cell Python can decode is a cell the ROM has a glyph
    for, and one it can't is a cell the ROM skips. That it agrees with
    the ROM is not assumed - four glyphs read out of a savestate all
    land on offset 0x66000 exactly, spanning ku 4 to ku 34, and every
    character the disc's own text uses resolves to a glyph that is
    drawn."""
    global _index
    if _index is None:
        table = {}
        for ku in range(1, 95):
            for ten in range(1, 95):
                code = _kuten_to_sjis(ku, ten)
                try:
                    bytes([code >> 8, code & 0xFF]).decode("shift_jis")
                except UnicodeDecodeError:
                    continue
                table[code] = len(table)
        _index = table
    return _index


def font():
    """The glyph table, read from fonts/ on first use."""
    global _font
    if _font is None:
        path = os.path.join(fonts_dir(), FONT_FILE)
        try:
            with open(path, "rb") as f:
                raw = zlib.decompress(f.read())
        except (OSError, zlib.error) as exc:
            raise BiosFontError(f"can't read {path}: {exc}") from exc
        if len(raw) != COUNT * GLYPH_BYTES:
            raise BiosFontError(
                f"{path} holds {len(raw)} bytes, not the "
                f"{COUNT * GLYPH_BYTES} a {COUNT}-glyph table is")
        _font = raw
    return _font


def index_for(sjis):
    """The glyph number for a Shift-JIS code, or None if the font has
    no glyph for it - an unassigned cell, or one of the level-2 kanji
    the ROM ran out of room for."""
    number = index().get(sjis)
    if number is None or number >= COUNT:
        return None
    return number


def rows(sjis):
    """One glyph as 15 sixteen-bit row masks, bit 15 leftmost - or None
    if the font has no glyph for that character."""
    number = index_for(sjis)
    if number is None:
        return None
    at = number * GLYPH_BYTES
    data = font()
    return struct.unpack_from(">15H", data, at)


def is_blank(sjis):
    """Whether the glyph is drawn at all. A space is a real cell with
    nothing in it, which is not the same as having no glyph."""
    drawn = rows(sjis)
    return drawn is not None and not any(drawn)


def extract(bios):
    """The glyph table out of a Japanese BIOS image, for remaking
    fonts/psx-kanji.bin:

        python -m functions.biosfont SCPH5500.BIN

    Raises BiosFontError unless the ROM has the table where a Japanese
    BIOS has it. A US or European BIOS has no kanji font at all, so this
    is what tells one apart from the other."""
    want = COUNT * GLYPH_BYTES
    if len(bios) < BIOS_OFFSET + want:
        raise BiosFontError(
            f"{len(bios)} bytes is too small to hold a font table at "
            f"{BIOS_OFFSET:#x} - a PlayStation BIOS is 512 KB")
    raw = bios[BIOS_OFFSET:BIOS_OFFSET + want]
    drawn = sum(1 for i in range(0, want, GLYPH_BYTES)
                if any(raw[i:i + GLYPH_BYTES]))
    if drawn < COUNT // 2:
        raise BiosFontError(
            f"only {drawn} of {COUNT} glyphs are drawn - this ROM has no "
            "kanji font, so it isn't a Japanese BIOS")
    return raw


def _main(argv):
    if len(argv) != 2:
        print(__doc__)
        print("usage: python -m functions.biosfont <BIOS image>")
        return 2
    with open(argv[1], "rb") as f:
        raw = extract(f.read())
    path = os.path.join(fonts_dir(), FONT_FILE)
    os.makedirs(fonts_dir(), exist_ok=True)
    with open(path, "wb") as f:
        f.write(zlib.compress(raw, 9))
    print(f"wrote {path}, {os.path.getsize(path)} bytes "
          f"({len(raw)} uncompressed)")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

"""The Japanese disc's text: halfword Shift-JIS, and the controls in it.

Every Latin build stores text as bytes through a 256-entry table (see
tombadict), one byte a character, 0xFF ending a message. SLPS-02350
cannot: it draws kanji, and a byte cannot name one. It stores text as
16-bit little-endian units instead, each holding a Shift-JIS code the
way Shift-JIS itself writes it - lead byte high. `62 98` in the file is
0x9862, which is 話.

    unit < 0x0100     a control the game acts on
    0x0100..0xFEFF    a Shift-JIS character
    unit >= 0xFF00    a control the game acts on
    0xFFFF            ends the message

Shift-JIS has no lead byte of 0xFF and none below 0x81, so neither
control range can collide with a character.

Pointers count units, not bytes: an entry pointer of 7 means the
seventh halfword. That is the only change to the container itself - the
master table is still `<< 2` from the header, exactly as the Latin
builds have it. See PTR_SHIFT.

WHERE THE CONTROL TABLE COMES FROM

Not from guessing at what a number might mean. Chunk 1's TXT1 is the
same file on both discs - 140 entries, every one of them who=0x02FF -
so the two read side by side, entry for entry:

      0   {$00B2}で奥へ移動          Use {$UP} to jump to the back
      6   {$00B1}か{$00B0}で…       Use {$LEFT} or {$RIGHT} to move
      2   {$00B2}＋{$FF05}ボタン…   Use {$UP} + {$CIRCLE} to talk
     23   {$FF05}{$FF02}{$FF06}…    Brake with {$CIRCLE}, {$CROSS}, and
          …{$FF07}ボタン            …use {$TRIANGLE}
     37   {$FF12}干し魚サンド{$FF10} {$BLUE}Dried Fish Sandwich{$WHITE}

which names the arrows, the four button icons and two of the colours
outright, and puts both colour runs at the same offset from their own
block - 0xFF10 + n against 0xF0 + n.

The rest is settled by counting. Across every TXTD message on each disc
the two histograms rank identically, and the three commonest come out
at almost the same absolute counts on 3,804 Japanese messages against
3,608 English ones:

    space    0xFF02   7,103     0xFB   42,888   (English needs far more)
    newline  0xFF00   5,761     0xFA    5,688
    end      0xFF01   5,032     0xF8    5,002
    pause    0xFF03   3,691     0xFC    1,422
    white    0xFF10     911     0xF0      751
    orange   0xFF11     437     0xF1      377
    pink     0xFF13     224     0xF3      199
    green    0xFF14     162     0xF4      137
    blue     0xFF12      75     0xF2       38

Codes not in the table below stay as {$XXXX}, which is what the Latin
readers do with a byte they have no name for, and re-encode to the unit
they came from.
"""
import struct

TERMINATOR = 0xFFFF

# One unit is two bytes, so a pointer counting units is a byte offset
# shifted by this.
PTR_SHIFT = 1

# {unit: token}, the twin of tombadict.letters. Tokens are the Latin
# builds' own, so a reader that knows what {$CIRCLE} means needs no
# second vocabulary and a translation moving between builds keeps its
# markup.
controls = {
    0x00B0: "{$RIGHT}",
    0x00B1: "{$LEFT}",
    0x00B2: "{$UP}",
    0x00B3: "{$DOWN}",
    0xFF00: "\n",
    0xFF01: "{$END}\n\n",
    0xFF02: " ",
    0xFF03: "{$PAUSE}",
    0xFF05: "{$CIRCLE}",
    0xFF06: "{$CROSS}",
    0xFF07: "{$TRIANGLE}",
    0xFF08: "{$SQUARE}",
    0xFF10: "{$WHITE}",
    0xFF11: "{$ORANGE}",
    0xFF12: "{$BLUE}",
    0xFF13: "{$PINK}",
    0xFF14: "{$GREEN}",
}

# The other direction. Longest token first, so {$END}\n\n is matched
# before the "\n" that starts its tail - and {$END} on its own still
# reads, for text that has been edited down to it.
_by_token = sorted(
    [(token, unit) for unit, token in controls.items()] + [("{$END}", 0xFF01)],
    key=lambda pair: -len(pair[0]))


class JapaneseTextError(ValueError):
    """Raised when text can't be written back as the game stores it."""


# The only bytes Shift-JIS starts a two-byte character with. Checking
# this before decoding matters: the codec reads a byte outside them as
# a character of its own, so 0x0101 comes back as two characters rather
# than raising, and would be read as text.
_LEADS = tuple(range(0x81, 0xA0)) + tuple(range(0xE0, 0xF0))


def is_char(unit):
    """Whether a unit is a character rather than a control. Only the
    codec decides - a lead byte in range with a trail byte that makes
    no character is not one."""
    if unit < 0x0100 or unit >= 0xFF00 or (unit >> 8) not in _LEADS:
        return False
    try:
        decoded = bytes([unit >> 8, unit & 0xFF]).decode("shift_jis")
    except UnicodeDecodeError:
        return False
    return len(decoded) == 1


def char_for(unit):
    """The character a unit draws, or None if it isn't one."""
    if not is_char(unit):
        return None
    return bytes([unit >> 8, unit & 0xFF]).decode("shift_jis")


def unit_for(char):
    """The unit a character is stored as. Raises for anything Shift-JIS
    writes as a single byte: those would land in the control range,
    which is why the disc uses the full-width forms throughout."""
    try:
        raw = char.encode("shift_jis")
    except UnicodeEncodeError:
        raise JapaneseTextError(
            f"{char!r} is not a Shift-JIS character, so the Japanese "
            "build has no way to store it") from None
    if len(raw) != 2:
        raise JapaneseTextError(
            f"{char!r} is a single Shift-JIS byte, which this build "
            "stores as a control rather than a letter - use the "
            "full-width form instead")
    return (raw[0] << 8) | raw[1]


def decode(data, at, limit=None):
    """(text, offset just past the terminator) from `at`.

    Runs to the end of the buffer without a terminator rather than
    raising: a file being read for display is worth showing even where
    a pointer has landed somewhere it shouldn't."""
    limit = len(data) if limit is None else min(limit, len(data))
    out = []
    while at + 2 <= limit:
        unit = struct.unpack_from("<H", data, at)[0]
        at += 2
        if unit == TERMINATOR:
            return "".join(out), at
        token = controls.get(unit)
        if token is not None:
            out.append(token)
            continue
        char = char_for(unit)
        out.append(char if char is not None else "{$%04X}" % unit)
    return "".join(out), at


def to_units(text):
    """Editor text as the units the game stores, terminator included."""
    units = []
    i = 0
    while i < len(text):
        for token, unit in _by_token:
            if text.startswith(token, i):
                units.append(unit)
                i += len(token)
                break
        else:
            if text.startswith("{$", i) and "}" in text[i:i + 12]:
                end = text.index("}", i) + 1
                body = text[i + 2:end - 1]
                try:
                    units.append(int(body, 16))
                except ValueError:
                    raise JapaneseTextError(
                        f"{text[i:end]} is not a control this build has "
                        "a name for, and not a {$XXXX} unit either") from None
                i = end
            else:
                units.append(unit_for(text[i]))
                i += 1
    units.append(TERMINATOR)
    return units


def encode(text):
    """Editor text back to the bytes the file holds."""
    units = to_units(text)
    return struct.pack("<%dH" % len(units), *units)


# --------------------------------------------------------------------
# TXT2 - the same container, a different alphabet
# --------------------------------------------------------------------
#
# TXT2 (SDAT id 3) is the item and status notices, and the Latin builds
# draw those in the page's 8x8 font rather than the 8x16 one. The
# Japanese disc keeps doing exactly that - so its TXT2 is not Shift-JIS
# at all. It is the same halfword container holding CELL NUMBERS into
# the page's own 8x8 grid, `code = row * 32 + column`, which is why the
# page carries a kana system font that nothing else on the disc uses.
#
# Read as Shift-JIS the file is 100% escapes and has no character run
# longer than three units anywhere in its 5,376 bytes. Read as cells
# the first two messages are
#
#     0x35 0xFF02 0x12 0x04 0x1F 0x108 0x0F 0x13 0x70
#     を    space  そ   う   ひ    ゛    し   た   ！
#
#     0x35 0xFF02 0x16 0x09 0x15 0x13 0x70
#     を    space  つ   か   っ   た   ！
#
# which is "を そうび した！" and "を つかった！" - equipped, and used.
# Note the dakuten: the 8x8 font has no が or び, so a voiced kana is
# the plain one followed by cell 0x108.
#
# The kana run is the ordinary gojuon with the small forms in front of
# their own vowel, hiragana then katakana, and the five confirmed cells
# above land where it says they should. Only 0x37 is not placed by that
# rule or confirmed by a string: hiragana starts at あ with no ぁ before
# it, and 0x37 is the one cell left between ん and ァ, so ぁ is where the
# displaced small a would have to go.
_KANA = (
    "あぃいぅうぇえぉお" "かきくけこ" "さしすせそ" "たちっつてと"
    "なにぬねの" "はひふへほ" "まみむめも" "ゃやゅゆょよ"
    "らりるれろ" "ゎわをん" "ぁ"
    "ァアィイゥウェエォオ" "カキクケコ" "サシスセソ" "タチッツテト"
    "ナニヌネノ" "ハヒフヘホ" "マミムメモ" "ャヤュユョヨ"
    "ラリルレロ" "ヮワヲン"
)

# Where the page's Latin block starts. Every one of the 58 cells that
# matches a US 8x8 glyph outright sits exactly 0x6F above the code the
# Latin builds give it, from '"' at 0x71 through '~' at 0xCD. The two
# cells after that carry the shapes the US page draws at 0x60 and 0x61,
# with nothing for the 0x5F between them - so the block is not one run.
_LATIN_AT = 0x6F
_LATIN_TAIL = {0xCE: 0x60, 0xCF: 0x61}

# The voicing marks and the long vowel, which are cells of their own
# rather than part of a kana. All three are read off the disc's own
# messages: "を そうひ+0x108した" is そうび, "たいりょくアッフ+0x107の" is
# アップ, and "まほうケ+0x108 0xDD シ+0x108か+0x108のひ+0x108た" is
# まほうゲージがのびた.
DAKUTEN = 0x108
HANDAKUTEN = 0x107
PROLONG = 0xDD

# Punctuation out of the page's symbol block, each placed by a message
# that uses it: 0xE7/0xE8 bracket a name in
# "{$PINK}[E7]さいこ[108][E8]のふういんふ[108]くろ{$WHITE}", and three
# 0xE2 in a row end "まったく はんのうか[108]ない" - which is ・・・. The
# rest of that block (0xD0 on, and 0x109-0x10B) is gauge and box art
# with no character behind it, and is left unnamed.
_SYMBOLS = {0xE2: "・", 0xE7: "「", 0xE8: "」"}

_cells = None


def cells():
    """{cell number: character} for the page's 8x8 grid.

    Cells past the alphabet - the gauge pieces, arrows and other art
    from 0xD0 on - are deliberately absent: they draw a shape with no
    character behind it, and naming them would make text that cannot be
    written back."""
    global _cells
    if _cells is None:
        from gui.txtd import dicts

        latin = dicts.for_build("us-retail")
        table = {i: char for i, char in enumerate(_KANA)}
        for code, char in latin.items():
            if 0x01 <= code <= 0x5E and len(char) == 1:
                table[_LATIN_AT + code] = char
        for cell, code in _LATIN_TAIL.items():
            char = latin.get(code)
            if char and len(char) == 1:
                table[cell] = char
        table[DAKUTEN] = "゛"
        table[HANDAKUTEN] = "゜"
        table[PROLONG] = "ー"
        table.update(_SYMBOLS)
        _cells = table
    return _cells


_by_char = None


def _cells_reverse():
    global _by_char
    if _by_char is None:
        _by_char = {}
        for cell, char in cells().items():
            _by_char.setdefault(char, cell)
    return _by_char


def decode_cells(data, at, limit=None):
    """(text, offset past the terminator) for a TXT2 message.

    Only 0xFF00 and up are controls here. The codes below 0x100 that
    the dialogue spends on the arrows are cells like any other - the two
    containers are drawn by different code, out of different fonts."""
    limit = len(data) if limit is None else min(limit, len(data))
    out = []
    table = cells()
    while at + 2 <= limit:
        unit = struct.unpack_from("<H", data, at)[0]
        at += 2
        if unit == TERMINATOR:
            return "".join(out), at
        if unit >= 0xFF00:
            out.append(controls.get(unit, "{$%04X}" % unit))
            continue
        char = table.get(unit)
        out.append(char if char is not None else "{$%04X}" % unit)
    return "".join(out), at


def encode_cells(text):
    """A TXT2 message back to bytes, terminator included."""
    table = _cells_reverse()
    units = []
    i = 0
    while i < len(text):
        for token, unit in _by_token:
            if unit >= 0xFF00 and text.startswith(token, i):
                units.append(unit)
                i += len(token)
                break
        else:
            if text.startswith("{$", i) and "}" in text[i:i + 12]:
                end = text.index("}", i) + 1
                try:
                    units.append(int(text[i + 2:end - 1], 16))
                except ValueError:
                    raise JapaneseTextError(
                        f"{text[i:end]} is not a control this build has a "
                        "name for, and not a {$XXXX} cell either") from None
                i = end
            else:
                cell = table.get(text[i])
                if cell is None:
                    raise JapaneseTextError(
                        f"{text[i]!r} is not on this disc's 8x8 page, which "
                        "is the only alphabet TXT2 can draw from")
                units.append(cell)
                i += 1
    units.append(TERMINATOR)
    return struct.pack("<%dH" % len(units), *units)


# --------------------------------------------------------------------
# MAIN.EXE and SOP.BIN
# --------------------------------------------------------------------
#
# These two are not the halfword format at all. Their string pools hold
# ordinary Shift-JIS, lead byte first, NUL-terminated - the same shape
# the Latin discs' pools have, with Shift-JIS where they have Latin-1.
# SOP.BIN's twelve story lines start at 0x58 as they do on every build;
# MAIN.EXE's pool starts at 0x0F14 rather than 0x680.

POOL_START = {"MAIN.EXE": 0x0F14, "SOP.BIN": 0x58}


def decode_pool(raw):
    """One NUL-terminated pool entry (without its terminator) -> text.

    A byte that starts no character is escaped rather than dropped, so
    what comes out can be written back unchanged."""
    out = []
    i = 0
    while i < len(raw):
        lead = raw[i]
        if i + 1 < len(raw) and (0x81 <= lead <= 0x9F or 0xE0 <= lead <= 0xEF):
            try:
                out.append(raw[i:i + 2].decode("shift_jis"))
                i += 2
                continue
            except UnicodeDecodeError:
                pass
        if lead == 0x0A:
            out.append("\n")
        elif 0x20 <= lead < 0x7F:
            out.append(chr(lead))
        else:
            out.append("{$%02X}" % lead)
        i += 1
    return "".join(out)


def encode_pool(text):
    """The other direction, without a terminator."""
    out = bytearray()
    i = 0
    while i < len(text):
        if text[i] == "\n":
            out.append(0x0A)
            i += 1
        elif text.startswith("{$", i) and "}" in text[i:i + 6]:
            end = text.index("}", i) + 1
            try:
                out.append(int(text[i + 2:end - 1], 16))
            except ValueError:
                raise JapaneseTextError(
                    f"{text[i:end]} is not a {{$XX}} byte") from None
            i = end
        else:
            try:
                out += text[i].encode("shift_jis")
            except UnicodeEncodeError:
                raise JapaneseTextError(
                    f"{text[i]!r} is not a Shift-JIS character") from None
            i += 1
    return bytes(out)

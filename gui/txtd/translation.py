"""A disc's character table, as a translation can change it.

The game's text is bytes, and what character a byte draws is decided in
two independent places:

    the font page   what shape sits in the grid cell (functions/fontpage)
    the table       what character that code means (tombadict)

A translation has to change both, and they have to agree. This module
owns the table half and persists it beside the disc, so a Polish build
keeps its own meanings for codes the US disc spends on symbols it never
prints.

Byte and cell are the same number for an ordinary glyph, but not for
everything: a space encodes to 0xFB and {$CIRCLE} to 0xCD, and the game
acts on those rather than looking them up in the grid. Codes that only
ever draw - which is every code a translation should be claiming - have
cell equal to byte, so a translation table needs only one mapping.

The table is applied by mutating tombadict.letters in place. Every
reader and both packers hold a reference to that one dict, so applying a
table reaches all of them at once; _BASE keeps the disc's own meanings so
a table can be swapped or dropped without reloading anything.
"""
import json
import os

from gui.txtd import tombadict

# The table as shipped, kept so applying a translation stays reversible.
_BASE = dict(tombadict.letters)

FILENAME = "tombadict.json"

# Codes the game acts on rather than draws. A translation must not claim
# these whatever the page shows at them, because the renderer never
# reaches the grid for them.
CONTROL_CODES = frozenset(
    {0x00, 0xC1, 0xC2, 0xFB, 0xFE, 0xFF}
    | set(range(0x60, 0x68))       # line and cursor controls
    | set(range(0xCD, 0xD1))       # the button icons
    | set(range(0xF0, 0xF5))       # the colour controls
)


class Table:
    """One translation's {code: character}, plus what it is called."""

    def __init__(self, name="", chars=None, glyph_top=None):
        self.name = name
        self.chars = dict(chars or {})
        self.glyph_top = glyph_top
        self._cells = None

    def letters(self):
        """The disc's own meanings with this table's laid over them."""
        merged = dict(_BASE)
        merged.update(self.chars)
        return merged

    def cells(self):
        """{character: code} for the codes that draw, cached.

        Controls are left out: they have no glyph behind them, so a
        renderer asking "which cell draws this" must not be given one."""
        if self._cells is None:
            self._cells = {}
            for code, text in self.letters().items():
                if code in CONTROL_CODES or not text:
                    continue
                self._cells.setdefault(text, code)
            # This table's own claims win, matching how the packer picks
            # a code, so the preview draws the cell the text will encode
            # to rather than one the disc happened to have already.
            for code, text in self.chars.items():
                if text and code not in CONTROL_CODES:
                    self._cells[text] = code
        return self._cells

    def claim(self, code, char):
        """Give a code a character. An empty character releases it."""
        if code in CONTROL_CODES:
            raise ValueError(
                f"Code 0x{code:02X} is a control the game acts on, not a "
                "glyph it draws. Claiming it would not show a character.")
        if char:
            self.chars[code] = char
        else:
            self.chars.pop(code, None)
        self._cells = None

    def to_json(self):
        return {
            "name": self.name,
            "glyph_top": self.glyph_top,
            # JSON keys are strings; written as hex so the file reads
            # the same way the codes are talked about everywhere else.
            "chars": {f"0x{c:02X}": s for c, s in sorted(self.chars.items())},
        }

    @classmethod
    def from_json(cls, blob):
        chars = {}
        for key, value in (blob.get("chars") or {}).items():
            chars[int(key, 0)] = value
        return cls(blob.get("name", ""), chars, blob.get("glyph_top"))


_active = Table()


def active():
    """The table in force."""
    return _active


def apply(table):
    """Make `table` the one in force, everywhere at once."""
    global _active
    _active = table or Table()
    tombadict.letters.clear()
    tombadict.letters.update(_active.letters())
    # The packers keep a reverse of the table, built once; it has to be
    # rebuilt now or text would still encode with the old meanings.
    from gui.txtd import txtd_packer
    txtd_packer.refresh_tables(_active.chars)
    return _active


def path_for(cd_folder):
    return os.path.join(cd_folder, FILENAME)


def load(cd_folder):
    """The translation saved beside this disc, applied. An empty table
    if there is none, which leaves the disc's own meanings in force."""
    path = path_for(cd_folder)
    if not os.path.exists(path):
        return apply(Table())
    with open(path, "r", encoding="utf-8") as f:
        return apply(Table.from_json(json.load(f)))


def save(cd_folder, table=None):
    """Write the table beside the disc it belongs to."""
    table = table or _active
    path = path_for(cd_folder)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(table.to_json(), f, ensure_ascii=False, indent=2)
    return path


def free_codes(page, used_codes=(), top=None):
    """Which codes a translation can take, and what taking each costs.

    Returns [(code, state, character)] where state is one of:

        "blank"   nothing drawn there and nothing names it - free
        "art"     a shape is drawn but no text on the disc asks for it,
                  so claiming it replaces art nobody sees
        "taken"   the disc's own text uses it

    `used_codes` is what the disc's text was measured to use; without it
    every named code counts as taken, which is the safe assumption."""
    from functions import fontpage

    if top is None:
        top = fontpage.GLYPH_TOP
    used = set(used_codes)
    out = []
    for code in range(256):
        if code in CONTROL_CODES:
            continue
        cell = fontpage.get_glyph(page, code, top)
        if cell is None:
            continue
        drawn = any(v for row in cell for v in row)
        if used:
            taken = code in used
        else:
            taken = code in _BASE
        if taken:
            state = "taken"
        elif drawn:
            state = "art"
        else:
            state = "blank"
        out.append((code, state, _active.letters().get(code)))
    return out

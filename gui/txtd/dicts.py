"""Character tables per build.

The dialogue font is a grid in the font page (see functions/fontpage.py),
`code = row * 32 + column`, and a build's table says which character each
code draws. The Latin builds share one encoding: every glyph US and the
European discs have in common sits at the same code on both, so a table
is the shared base plus whatever that build adds.

    codes   0.. 127   ASCII from '!', i.e. code = ord(char) - 32
    codes 160.. 191   accented capitals, chr(0xC0 + code - 160)
    codes 192.. 223   accented lowercase, chr(0xE0 + code - 192)
    codes 240..       colour and flow controls, see tombadict

The accented block is read off the German and Spanish pages, which draw
all 64 of them. Nine of those codes are also named in the US table from
in-game text, and agree with the block exactly.

The grid's row 5 - cells 160 to 191 - is where the builds part company.
Germany and Spain draw accented capitals there in the ordinary glyph
indices. The US page instead draws the button icons: cells 160 to 167
are the circle, cross, triangle and square, two cells to an icon since
they are 16 wide against the grid's 8, and they use palette indices 7
and up, which the text palettes all share.

Nothing has to choose between the two, because a build only reads the
codes its own text uses. US text reaches its icons through controls -
{$CIRCLE} encodes to 0xCD - and the byte a control encodes to is not a
cell number, so no US string asks for cell 160 as a letter.

Japanese is not covered. Its page shares no glyph shapes with the Latin
builds, so nothing here can be derived for it by matching.
"""
from gui.txtd import tombadict

# A COPY, taken at import, not a reference to the live dict.
#
# translation.apply() rewrites tombadict.letters in place so every
# reader and both packers see a translation at once. Aliasing that dict
# here meant for_build() built each build's table on top of whatever
# translation happened to be applied - and after apply() had cleared it,
# on top of nothing at all. The shipped meanings have to stay put.
_BASE = dict(tombadict.letters)

# Accented capitals and lowercase, as the European pages lay them out.
ACCENT_BLOCK = {}
for _k in range(32):
    ACCENT_BLOCK[160 + _k] = chr(0xC0 + _k)
    ACCENT_BLOCK[192 + _k] = chr(0xE0 + _k)

# Builds whose font page is the Latin grid. The value is what to add to
# the shared base; the base already names the codes US text uses.
_LATIN = {
    "us-retail": {},
    "us-demo": {},
    "de-retail": ACCENT_BLOCK,
    "sp-retail": ACCENT_BLOCK,
}

# Where each build's grid starts in its font page, found by matching the
# "@ABC..." row rather than assumed - see fontpage.find_glyph_top().
GLYPH_TOP = {
    "us-retail": 40,
    "us-demo": 40,
    # 64, not 66. Matching the European page against the US "@ABC" row
    # peaks at 66, but that finds where the LETTERS line up, not where
    # the cell begins: the US glyphs sit flush with the top of their
    # cell and the European ones sit two rows into theirs.
    #
    # The cell boundary is visible directly. Rows 64, 80, 96, 112 and
    # 160 of the German page are completely blank, and 127 and 143 are
    # the blank last rows of the cells above them - a 16-row pitch
    # starting at 64, which is also the only one of the candidates that
    # is itself a multiple of 16. Being two rows out put every selection
    # box two rows low, which is what showed on the capitals.
    "de-retail": 64,
    "sp-retail": 64,
}

# Cells the US page fills with button icons and the European pages with
# accented capitals. Only the European builds take the accent block, so
# the tables do not disagree; this is where a build's page has to be
# read rather than assumed.
ICON_CELLS = tuple(range(160, 168))

DEFAULT_BUILD = "us-retail"


def builds():
    """Build ids with a table, in a stable order."""
    return tuple(_LATIN)


def for_build(build=DEFAULT_BUILD):
    """That build's {code: character}.

    An unknown build falls back to the US table, which is the shared
    base every Latin build agrees with."""
    table = dict(_BASE)
    table.update(_LATIN.get(build, {}))
    return table


def glyph_top(build=DEFAULT_BUILD):
    """Where that build's dialogue grid starts in its font page."""
    return GLYPH_TOP.get(build, 40)


# Words only one language's executable has, used to tell the European
# discs apart. Both carry the same font page layout, so the page cannot
# say which language it is - the strings can.
_LANGUAGE_MARKS = (
    ("de-retail", b"Speichern"),
    ("sp-retail", b"Guardar"),
    ("us-retail", b"Save Error!"),
)


def detect(page, exe=b""):
    """Which build a disc is, read off the disc itself.

    Two signals, both measured, neither of them a filename:

    The US page keeps rows 224-239 empty; the European ones fill them
    with artwork. That is a difference of 0 inked texels against about
    1,600, so it separates the layouts outright rather than by a
    threshold anyone has to tune.

    Which European language it is cannot come from the page, because
    German and Spanish lay theirs out identically. It comes from a word
    that only one executable contains.

    Returns (build id, why), the reason being worth showing: a detector
    that silently picks the wrong layout is worse than one that says
    what it saw.
    """
    below = sum(1 for y in range(224, 240)
                for x in range(len(page[y])) if page[y][x])
    language = None
    for build, mark in _LANGUAGE_MARKS:
        if mark and exe and mark in exe:
            language = build
            break
    if below == 0:
        return (language if language and language.startswith("us")
                else DEFAULT_BUILD), (
            f"rows 224-239 are empty, which is the US layout"
            + (f", and MAIN.EXE agrees" if language == "us-retail" else ""))
    if language and not language.startswith("us"):
        return language, (
            f"rows 224-239 carry {below} texels of artwork, which is the "
            f"European layout, and MAIN.EXE names it")
    return "de-retail", (
        f"rows 224-239 carry {below} texels of artwork, so this is the "
        "European layout, but MAIN.EXE did not say which language - "
        "guessing German, which shares the layout")


def derive(page, reference_page, reference_table, top=None, reference_top=40):
    """Read a table off a font page by matching its glyphs.

    Every glyph in `page` that is drawn identically in `reference_page`
    takes the character the reference gives that shape, whichever code
    it sits at. Returns (table, unmatched codes) - the unmatched ones
    being glyphs this build draws that the reference has no name for,
    which is where a new language's own letters end up."""
    from functions.fontpage import glyphs, find_glyph_top, glyph_row

    if top is None:
        top, _score = find_glyph_top(page, glyph_row(reference_page, 1,
                                                     reference_top))
    mine = glyphs(page, top)
    theirs = glyphs(reference_page, reference_top)

    by_shape = {}
    for code, shape in theirs.items():
        if all(v == 0 for row in shape for v in row):
            continue
        name = reference_table.get(code)
        if name is not None:
            by_shape.setdefault(shape, set()).add(name)

    table, unmatched = {}, []
    for code, shape in mine.items():
        if all(v == 0 for row in shape for v in row):
            continue
        names = by_shape.get(shape)
        if names and len(names) == 1:
            table[code] = next(iter(names))
        else:
            unmatched.append(code)
    return table, unmatched

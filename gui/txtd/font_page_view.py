"""The whole font and menu page, drawn as the game would draw it.

Chunk 0 of TOMBA2.IMG is one 256x256 4bpp page and everything the game
writes on screen comes out of it - the two fonts, the menu words that
are artwork rather than text, and, in its last 32 rows, the palettes
themselves (see functions/fontpage.py for the layout).

The page cannot be shown as one picture without choosing, because a
4bpp page is not a picture: it is indices, and what colour index 3 is
depends on which palette the drawing code had selected at the time. The
same rows are white text in a dialogue box and orange artwork on a menu.
So a palette is picked here rather than assumed, and the regions are
marked, because "row 168 onwards is artwork" is the sort of thing that
is obvious once seen and invisible before.

Which palette each region actually wants was read off captures of the
game rather than guessed - see CLUTS below.
"""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTabBar, QVBoxLayout, QWidget,
)

import os

from functions import fontpage
from gui.txtd import dicts, translation
from gui.pixel_canvas import PixelCanvas, fit_zoom, zoom_label

# What lives where, in page rows. The names are the ones the module
# docstring of functions/fontpage.py uses.
def regions(glyph_top=fontpage.GLYPH_TOP):
    """What lives where, in page rows.

    Where the dialogue grid starts is NOT the same on every build: the
    US page puts it at row 40, the German and Spanish ones at 66,
    because those carry two extra rows of accented capitals above it
    (see gui/txtd/dicts.GLYPH_TOP). Everything below the grid sits at a
    fixed row, so only this boundary moves - but it moves the meaning of
    every cell beneath it, which is why it is asked for rather than
    assumed."""
    return (
        ("system font 8x8", fontpage.SYSTEM_TOP, glyph_top,
         QColor(90, 170, 255, 190)),
        ("dialogue font 8x16", glyph_top, 168,
         QColor(120, 220, 120, 190)),
        ("menu artwork", 168, fontpage.CLUT_TOP,
         QColor(240, 170, 60, 190)),
        ("palettes", fontpage.CLUT_TOP, fontpage.PAGE_H,
         QColor(230, 110, 110, 190)),
    )

# Where the menu artwork sits, and what palette each piece is drawn
# with. Both were read off the page and off captures of the game, not
# guessed - see the note on halves below.
#
#   (name, top row, bottom row, left column, right column)
SPRITES = (
    # "GET", the item-pickup word. It sits in the SYSTEM font's last
    # row - cells 0x99-0x9b - rather than in the artwork band, so
    # nothing about where it lives suggests it is a sprite. It is drawn
    # from indices 1-7, the low half, which is the giveaway: system
    # glyphs use the font palette and this does not.
    ("GET", 32, 40, 200, 224),
    # THE ARTWORK IS ON THE TEXT GRID
    #
    # Everything on this page is modular on the same 8-pixel column the
    # dialogue font is ruled into - the artwork included. The menu words
    # are whole numbers of those columns: Items 7, Event 8, Status 8,
    # Help 6, and the boundaries fall at 56, 120 and 184, all multiples
    # of 8.
    #
    # This is not the same as fitting a grid to the ink, which is what a
    # bounding box does and why these read as ragged before: the ink
    # starts at 1, 60, 122 and 186 with gaps of 59, 62 and 64, and ends
    # at 53, 117, 181 and 229 - a 64-pixel pitch with every end on the
    # same 16-pixel phase. Both descriptions contain the words; the
    # column one is the page's own, so it is the one used.
    ("Items",  176, 192,   0,  56),
    ("Event",  176, 192,  56, 120),
    ("Status", 176, 192, 120, 184),
    ("Help",   176, 192, 184, 232),
    # THE HEALTH ROW IS ON A 16-PIXEL GRID
    #
    # It looked like one wide sprite because it was measured by its ink,
    # and ink is ragged. It is not: from "2" onward every glyph sits in
    # its own 16-wide cell, the cells tile x24 to x200 without a gap, and
    # the ink lands 2-3 pixels in from the left of each and 4-5 from the
    # right - the same insets nine times over, which is not what
    # proportional spacing looks like. Full!! continues the same grid for
    # two more cells.
    #
    # "+" and "1" are the two that do not fit a 16-cell each: the plus is
    # centred in x0..16 and the 1 is 8 wide. The boxes below still tile
    # the row exactly, so every glyph is selectable on its own - which is
    # the point, since a translation redraws digits one at a time.
    ("+",      192, 208,   0,  16),
    ("digit 1", 192, 208,  16,  24),
    ("digit 2", 192, 208,  24,  40),
    ("digit 3", 192, 208,  40,  56),
    ("digit 4", 192, 208,  56,  72),
    ("digit 5", 192, 208,  72,  88),
    ("digit 6", 192, 208,  88, 104),
    ("digit 7", 192, 208, 104, 120),
    ("digit 8", 192, 208, 120, 136),
    ("digit 9", 192, 208, 136, 152),
    ("digit 0", 192, 208, 152, 168),
    ("Full!!", 192, 208, 168, 200),
    # Six columns each, and this pair says so more plainly than the row
    # above: cells at x0 and x48 put the ink exactly 3 pixels in from
    # the left of BOTH, which is a left bearing repeated rather than a
    # coincidence of packing.
    ("Load",   208, 224,   0,  48),
    ("Save",   208, 224,  48,  96),
    # The dialogue frame, which is modular too - and unlike the health
    # row, the game's own numbers say so: functions/fontpage.py has held
    # FRAME_PIECE_W = 18 and FRAME_PIECES = (11, 35, 59) all along, which
    # is three 18-wide pieces at a 24-pixel pitch from x176. Measuring
    # the art gives 187..205, 211..229 and 235..253 - the same three,
    # exactly, to the pixel.
    #
    # They are the shallow top, the section carrying the two side edges,
    # and the deeper top; the game builds every box out of them. Offered
    # one at a time because that is how they are drawn and how they have
    # to be redrawn.
    # On the text grid too, and putting it there settles where the
    # border begins. The art sits at 187, 211 and 235 - an 18-wide piece
    # every 24 - and 24 is exactly three columns, so the cells are
    # 184..208, 208..232 and 232..256. Those are grid cells 0xD7 to
    # 0xDF: the range this started out being described as, before the
    # ink's left edge was mistaken for the cell's.
    ("frame shallow top", fontpage.FRAME_Y, fontpage.FRAME_Y + 16,
     184, 208),
    ("frame sides", fontpage.FRAME_Y, fontpage.FRAME_Y + 16, 208, 232),
    ("frame deep top", fontpage.FRAME_Y, fontpage.FRAME_Y + 16, 232, 256),
)

# A PALETTE HERE IS TWO GRADIENTS, NOT ONE
#
# This is the thing that makes the artwork look wrong until it is known.
# Each of these palettes carries a gradient at indices 1-6 and another,
# unrelated one, at 10-15, and a sprite uses whichever half it was drawn
# against:
#
#   Items, Event, Status, Help, Load, Save   indices 10-15 only
#   Full!!                                   indices 1-11, BOTH halves
#   the health digits and +                  indices 1-7
#
# So the menu words take their entire colour from the high half, and
# looking at the low half - which is where a gradient normally is, and
# where every other palette in the page keeps one - shows a flat green
# and matches nothing. That is why three separate attempts to identify
# these by colour failed.
#
# Full!! is the case that proves it: it spans both halves of 241/2, so
# "Full" comes out of the low end - FFFF6A through to 310000, a yellow
# highlight over orange over dark red - while "!!" comes out of indices
# 8-11, which are 00F6F6 and 008BF6, cyan and blue. The game draws it
# exactly that way, and the same low half is what colours the health
# digits.
#
# The menu words appear in several colours because these are the menu's
# own states - a highlighted entry and an unhighlighted one are the same
# artwork under a different palette.
HIGH_HALF = {
    (240, 1): "yellow",
    (241, 1): "green",
    (242, 1): "blue",           # Status and Load, as captured
    (243, 1): "pink",           # Start Game, as captured
}

# What each sprite is drawn with by default. The menu words open on the
# blue state because that is what the captures show; the others have
# only one colour each.
# One palette per word, not one shared between them: the four high-half
# palettes are a colour each - 240/1 yellow, 241/1 green, 242/1 blue,
# 243/1 pink - and the menu spends one on every word rather than
# recolouring them all together. The title screen does exactly the same
# thing with its own four (see TITLE_CLUTS), which is what makes this
# look like the game's habit rather than a coincidence.
SPRITE_CLUTS = {
    # The frame is drawn in whichever palette the box it belongs to
    # uses: (255, 2) is the grey dialogue box, (255, 3) the pink one
    # item notices use, (254, 3) the pale yellow of the control hints.
    # Same art in all three - only the palette says which box it is.
    "frame shallow top": fontpage.FRAME_CLUT,
    "frame sides": fontpage.FRAME_CLUT,
    "frame deep top": fontpage.FRAME_CLUT,
    "GET": (241, 2),
    "Items": (240, 1),
    "Event": (241, 1),
    "Status": (242, 1),
    "Help": (243, 1),
    "Load": (242, 1),
    "Save": (243, 1),
    "Full!!": (241, 2),
}
# Every cell of the health row shares the digits' palette.
for _n in ("+", "digit 0", "digit 1", "digit 2", "digit 3", "digit 4",
           "digit 5", "digit 6", "digit 7", "digit 8", "digit 9"):
    SPRITE_CLUTS[_n] = (241, 2)

# The ground a transparent texel is seen against. Left to the canvas to
# paint rather than written into the image: drawn into the pixels it
# would line up with them and scale with the zoom, which makes it look
# like part of the glyph instead of like nothing.
#
# Darker than the canvas default, because the font is nearly all light
# pixels and a pale ground swallows them - but nowhere near black, or an
# erased texel cannot be told from one drawn in the page's darkest
# colour, which is the mistake worth avoiding here.
CHECKER_LIGHT = QColor(104, 104, 112)
CHECKER_DARK = QColor(88, 88, 96)

# The two fonts. Every dialogue capture - the NPC box, Zippo and Tomba's,
# the controls menu, the intro text - matches this one to within a mean
# colour error of 19 to 36.
FONT_CLUT = (240, 3)

# Codes drawn two cells wide. The dialogue grid is 8 pixels a cell, but
# this run holds kana and symbols that need 16, so each of them spans
# the cell named by the code and the one after it. Treating them as
# single cells - which is what a plain grid does - lets a click land on
# half a glyph and an export cut one down the middle.
DOUBLE_FIRST = 0xA0
# Through to the end of the row, not to 0xD5. The run does not stop
# where the kana do: the dialogue frame fills the rest of that grid row
# and is drawn at the same doubled width, so treating 0xD6 onwards as
# single cells cut the border in half the way it used to cut a kana.
DOUBLE_LAST = 0xDF

# AND THE RUN IS US-ONLY
#
# Those codes are 16 wide on the US disc because it spends them on kana
# and on the dialogue frame. The European discs spend the same codes on
# accented letters instead - all 64 of them, 32 to a grid row, 8 wide
# like every other letter. Checked rather than assumed: on the German
# page both 0xA0 and 0xC0 rows come back with 32 of 32 cells inked,
# which only fits single-width cells.
#
# So a build with no doubles gets None, and every cell on it is one
# column wide.
DOUBLE_RUN = {
    "us-retail": (DOUBLE_FIRST, DOUBLE_LAST),
    "us-demo": (DOUBLE_FIRST, DOUBLE_LAST),
    "de-retail": None,
    "sp-retail": None,
}

# THE TITLE SCREEN, chunk 2 of the IMG (AREA_02).
#
# Its menu - New Game, Load Game, Options, Start Game - is artwork, not
# text, and none of it is reachable through the font page. A build that
# translates every string and leaves this alone still opens in English,
# which is why the tab covers two pages rather than one.
#
# WHERE NEW GAME ENDS AND LOAD GAME BEGINS
#
# These two are packed edge to edge with no transparent column between
# them - the game cuts them apart with texture coordinates of its own,
# and the page carries no mark at all. The seam is still recoverable,
# because both words end in "Game": the row repeats itself at a shift
# equal to the width of "Load Game", and 86 wins that comparison
# outright, 0.71 against 0.50 for the next best.
#
# That puts the seam at x848 - and 848 is a multiple of 16, as is the
# x768 the row starts at, which is a second and independent reason to
# believe it: nothing about matching a row against a shifted copy of
# itself knows where a 16-pixel grid would fall. New Game is then 80
# wide, five cells of it.
#
# (Getting the transparency right matters here. This palette draws
# NOTHING for indices 0 through 7, not just for 0, so measuring ink as
# "index is non-zero" overstates every edge by a pixel or two - which is
# what put the seam at 849 the first time round.)
TITLE_SPRITES = (
    ("New Game", 1, 17, 768, 848),
    ("Load Game", 1, 17, 848, 936),
    ("Options", 1, 17, 936, 1008),
    ("Start Game", 17, 33, 768, 864),
)

# Four gradients, one per menu state - the same art recoloured, exactly
# as the font page's menu words are. 252/15 is the blue and cyan one the
# screen shows for the entry under the cursor; 251, 253 and 254 are the
# pink, yellow and green states.
#
# Index 1 is TRANSPARENT in all four. That is worth knowing before
# editing: in raw indices the words look like they sit on a solid panel,
# and in the game they do not.
# THE BACKGROUND IS 8BPP, AND THE MENU WORDS ARE 4BPP
#
# Everything left of x640 is the title picture, and it is not 4bpp at
# all: it is one 320x240 8bpp image, two of the page's texels to a
# pixel, drawn through a 256-entry palette made of row 255's sixteen
# slots laid end to end. Read as 4bpp it comes out as coloured noise,
# which is what it looked like until this was worked out.
#
# It is not edited a texel at a time - a brush that writes 4-bit indices
# has nothing sensible to do to an 8bpp photograph. It is replaced
# instead: export it, redraw it in something that edits pictures, and
# bring it back. A replacement gets a NEW 256-colour palette quantised
# from the picture itself and written back over row 255, so the import
# is not held to the colours the original happened to use.
#
# The box below is the VRAM extent, in 4bpp texels. The picture is half
# that wide - 320 pixels - and is drawn at its true size, not stretched
# across the whole box.
TITLE_DEEP = (("title picture", 0, 240, 0, 640),)
TITLE_DEEP_ROW = 255

# One palette per entry, not one palette per state: the title screen
# shows New Game yellow and Load Game green side by side, and Options
# blue on the screen after it. Start Game takes the pink that is left -
# the only one of the four not confirmed against a capture.
# THE EUROPEAN TITLE MENUS ARE FOUR COLUMNS OF TWO LINES
#
# Not the US layout at all. Each entry is two stacked words - "Neues" /
# "Spiel", "Spiel" / "laden" - so the page holds four columns and two
# rows rather than three long words and one. Reading a German page with
# the US boxes therefore slices across the columns.
#
# The fourth entry does not fit. "Optionen" runs to x1016 and the page
# ends at 1024, so the disc itself puts "Optione" on the first line and
# "n" on the second; Spanish does the same with "Opcione" and "es". That
# is in the artwork, not something this does to it - worth knowing
# before trying to make a longer translation fit.
DE_TITLE_SPRITES = (
    ("Neues / Spiel (New Game)", 0, 32, 768, 824),
    ("Spiel / laden (Load Game)", 0, 32, 824, 872),
    ("Spiel / starten (Start Game)", 0, 32, 872, 944),
    ("Optione + n (Options, wrapped)", 0, 32, 944, 1016),
)
SP_TITLE_SPRITES = (
    ("Nuevo / juego (New Game)", 0, 32, 768, 824),
    ("Cargar / juego (Load Game)", 0, 32, 824, 880),
    ("Comenzar / juego (Start Game)", 0, 32, 880, 952),
    ("Opcione + es (Options, wrapped)", 0, 32, 952, 1016),
)

TITLE_CLUT = (252, 15)
TITLE_CLUTS = {
    "New Game": (253, 15),
    "Load Game": (254, 15),
    "Options": (252, 15),
    "Start Game": (251, 15),
}

# Naming these after the sprites would be circular, so they are named by
# colour - which is what picking between them looks like.
TITLE_PALETTES = {
    (251, 15): "pink - Start Game",
    (252, 15): "blue - Options",
    (253, 15): "yellow - New Game",
    (254, 15): "green - Load Game",
}


# THE EUROPEAN PAGES PUT THE ARTWORK SOMEWHERE ELSE ENTIRELY
#
# The German and Spanish discs carry two extra rows of accented letters,
# which pushes their dialogue grid from row 40 down to row 66 - and the
# menu artwork with it. It does not simply move down: the words end up
# tucked into the right-hand end of grid rows and into rows 224-239,
# which on the US page are empty. Drawing a European page with the US
# boxes therefore cuts words in half, which is exactly what it looked
# like.
#
# Measured the same way as the US ones: the words are drawn from palette
# indices 10-15 and ordinary glyphs are not, so isolating the high half
# finds the artwork wherever it has been hidden. Every box below then
# snapped to the 8-pixel text column, and every one of them landed on it.
#
# Named where the word could be read off the page and confirmed against
# MAIN.EXE (which carries Laden/Speichern on the German disc and
# Cargar/Guardar on the Spanish). Two blocks per build sit in the system
# rows and are NOT identified - they are given as positions rather than
# guessed at, since a wrong name here is worse than an honest coordinate.
# The 8-row words first. These sit in the system-font half of the page,
# and the band really is 8 tall, not 16: restricting the search to the
# indices only the artwork uses puts them at y56..64 exactly. The 16-row
# box they had before reached up into the system glyphs above and
# dragged those into the selection - which is the green that had no
# business being there.
DE_SPRITES = (
    ("Ereignis (Event)", 56, 64, 96, 160),
    ("unnamed word, y56 x160", 56, 64, 160, 224),
    ("Laden (Load)", 128, 144, 200, 256),
    # One word per cell. They touch at x176, so a gap-tolerant scan
    # merged Hilfe and Speichern into one block; a scan that allows no
    # gap at all separates them and lands on 8-pixel columns anyway.
    ("Status", 176, 192, 64, 128),
    ("Hilfe (Help)", 176, 192, 128, 176),
    ("Speichern (Save)", 176, 192, 176, 240),
    # The health row is where the US page keeps it, cell for cell -
    # rows 224..240 here against 192..208 there, but the same 16-pixel
    # cells at the same x. "100%" stands where "Full!!" does.
    ("+", 224, 240, 0, 16),
    ("digit 1", 224, 240, 16, 24),
    ("digit 2", 224, 240, 24, 40),
    ("digit 3", 224, 240, 40, 56),
    ("digit 4", 224, 240, 56, 72),
    ("digit 5", 224, 240, 72, 88),
    ("digit 6", 224, 240, 88, 104),
    ("digit 7", 224, 240, 104, 120),
    ("digit 8", 224, 240, 120, 136),
    ("digit 9", 224, 240, 136, 152),
    ("digit 0", 224, 240, 152, 168),
    ("100%", 224, 240, 168, 200),
    ("Objekt (Items)", 224, 240, 200, 248),
)
SP_SPRITES = (
    ("unnamed word, y56 x64", 56, 64, 64, 128),
    ("unnamed word, y56 x128", 56, 64, 128, 192),
    ("Cargar (Load)", 128, 144, 200, 256),
    ("Estado (Status)", 176, 192, 64, 136),
    ("Ayuda (Help)", 176, 192, 136, 208),
    ("Guardar (Save)", 176, 192, 208, 256),
    ("+", 224, 240, 0, 16),
    ("digit 1", 224, 240, 16, 24),
    ("digit 2", 224, 240, 24, 40),
    ("digit 3", 224, 240, 40, 56),
    ("digit 4", 224, 240, 56, 72),
    ("digit 5", 224, 240, 72, 88),
    ("digit 6", 224, 240, 88, 104),
    ("digit 7", 224, 240, 104, 120),
    ("digit 8", 224, 240, 120, 136),
    ("digit 9", 224, 240, 136, 152),
    ("digit 0", 224, 240, 152, 168),
    ("100%", 224, 240, 168, 200),
    ("Objeto (Items)", 224, 240, 200, 256),
)
# The menu words take the same four high-half palettes on every build -
# 240/1 through 243/1 - so which one each European word uses has not
# been captured from the game. They open on the blue one, as the US
# words did before their colours were confirmed.
def _euro_cluts(sprites):
    """Blue for the menu words, the health palette for the health row."""
    out = {}
    for name, top, _b, _l, _r in sprites:
        out[name] = (241, 2) if top == 224 else (242, 1)
    return out


DE_CLUTS = _euro_cluts(DE_SPRITES)
SP_CLUTS = _euro_cluts(SP_SPRITES)


class PageKind:
    """A page the tab can show, and what is known to be in it.

    The font page and the title screen are both 4bpp pages of the same
    IMG and are edited the same way, but only one of them is a grid of
    glyphs. Everything that differs between them lives here, so the view
    below asks the kind rather than testing which page it is on."""

    def __init__(self, label, spec, sprites, cluts, default_clut, grid,
                 bands, palettes=None, deep=(), deep_row=None,
                 by_build=None, cluts_by_build=None):
        self.label = label
        # Per-build overrides, for pages whose artwork moves between
        # builds - see DE_SPRITES.
        self.by_build = by_build or {}
        self.cluts_by_build = cluts_by_build or {}
        self.palettes = palettes        # (row, slot) -> what it is for
        self.deep = deep                # 8bpp boxes: shown, not edited
        self.deep_row = deep_row        # the CLUT row they read through
        self.spec = spec                # functions.fontpage.PageSpec
        self.sprites = sprites
        self.cluts = cluts              # sprite name -> (row, slot)
        self.default_clut = default_clut
        self.grid = grid                # are there glyph cells to pick?
        self._bands = bands

    def regions(self, glyph_top):
        return self._bands(glyph_top)

    def sprites_for(self, build):
        return self.by_build.get(build, self.sprites)

    def cluts_for(self, build):
        return self.cluts_by_build.get(build, self.cluts)

    def deep_at(self, x, y):
        """The name of the 8bpp picture DRAWN over this texel, if any.

        An 8bpp pixel is two 4bpp texels, so the picture is drawn half
        the width of the VRAM it occupies - at its true size. This asks
        about what is on screen, which is what a click means."""
        for name, top, bottom, left, right in self.deep:
            if top <= y < bottom and left <= x < left + (right - left) // 2:
                return name
        return None

    def deep_covers(self, x, y):
        """Is this texel part of an 8bpp picture's VRAM?

        The whole extent, drawn half or not - nothing here may be
        rendered as 4bpp, or the picture's right half comes back as the
        coloured noise it looks like when read at the wrong depth."""
        for _name, top, bottom, left, right in self.deep:
            if top <= y < bottom and left <= x < right:
                return True
        return False

    def deep_box(self, name):
        """(x, y, w, h) of the picture as drawn, in page texels."""
        for this, top, bottom, left, right in self.deep:
            if this == name:
                return (left, top, (right - left) // 2, bottom - top)
        return None

    def sprites_in_row(self, y, build=None):
        """Is there anything 4bpp on this row of the page?"""
        return any(top <= y < bottom for _n, top, bottom, _l, _r
                   in self.sprites_for(build or dicts.DEFAULT_BUILD))

    def deep_palette(self, cluts):
        """The 8bpp palette: every slot of deep_row, end to end."""
        out = []
        for row, _slot, pal in cluts:
            if row == self.deep_row:
                out.extend(pal)
        return out

    def named(self):
        """Palette per named part, for annotating the palette list."""
        if self.palettes:
            return {name: key for key, name in self.palettes.items()}
        out = {}
        if self.grid:
            out["dialogue and system font"] = self.default_clut
        for name, where in self.cluts.items():
            out.setdefault(name, where)
        return out


def _title_bands(_glyph_top):
    return (
        ("artwork", 0, fontpage.TITLE.clut_top, QColor(240, 170, 60, 190)),
        ("palettes", fontpage.TITLE.clut_top, fontpage.TITLE.height,
         QColor(230, 110, 110, 190)),
    )


FONT_PAGE = PageKind(
    "AREA_00 Fonts", fontpage.FONTS, SPRITES, SPRITE_CLUTS, FONT_CLUT, True,
    regions,
    by_build={"de-retail": DE_SPRITES, "sp-retail": SP_SPRITES},
    cluts_by_build={"de-retail": DE_CLUTS, "sp-retail": SP_CLUTS})
DE_TITLE_CLUTS = {_s[0]: TITLE_CLUT for _s in DE_TITLE_SPRITES}
SP_TITLE_CLUTS = {_s[0]: TITLE_CLUT for _s in SP_TITLE_SPRITES}

TITLE_PAGE = PageKind("AREA_02 Main Title", fontpage.TITLE, TITLE_SPRITES,
                      TITLE_CLUTS, TITLE_CLUT, False, _title_bands,
                      TITLE_PALETTES, TITLE_DEEP, TITLE_DEEP_ROW,
                      by_build={"de-retail": DE_TITLE_SPRITES,
                                "sp-retail": SP_TITLE_SPRITES},
                      cluts_by_build={"de-retail": DE_TITLE_CLUTS,
                                      "sp-retail": SP_TITLE_CLUTS})
PAGE_KINDS = (FONT_PAGE, TITLE_PAGE)


class FontPageView(QWidget):
    """The page at a chosen palette, with its regions marked."""

    # What was clicked: ("glyph", code) or ("sprite", name).
    selected = pyqtSignal(str, object)
    # The page was written back to TOMBA2.IMG. Anything showing VRAM is
    # now looking at a stale copy of it - the font page IS chunk 0's
    # VRAM, so a saved glyph changes what AREA_00 holds.
    saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = None
        self.cluts = []
        self.cd_folder = None
        self.kind = FONT_PAGE     # which page is on show
        # Which disc this is. The page layout, where the dialogue grid
        # starts and which characters the cells mean all follow from it,
        # so getting it wrong shows up as artwork sliced in half.
        self.build = dicts.DEFAULT_BUILD
        self.detected = dicts.DEFAULT_BUILD
        self.detected_why = ""
        self.states = {}          # sprite name -> palette chosen for it
        # What has been changed since the page was read, as a list of
        # actions, newest last. An action is a list of (x, y, what was
        # there) - one press-drag-release, or one import - because that
        # is what a person did, and it is what Undo has to take back.
        self.history = []
        self.stroke = []
        self.original = None      # the page as the disc has it
        self.clipboard = None     # texels copied from a glyph
        self.table = None         # the disc's character table
        # Where this build's dialogue grid starts. 40 on the US discs,
        # 66 on the German and Spanish ones - see regions().
        self.glyph_top = fontpage.GLYPH_TOP
        self.selection = None     # ("glyph", code) | ("sprite", name)

        self.canvas = _PageCanvas()
        self.canvas.checker_light = CHECKER_LIGHT
        self.canvas.checker_dark = CHECKER_DARK
        self.canvas.clicked.connect(self._clicked)
        self.canvas.arrow.connect(self._move_selection)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.canvas)

        self.build_box = QComboBox()
        self.build_box.setToolTip(
            "Which disc's layout to read the page with. Auto works it "
            "out from the page itself - the US discs leave rows 224-239 "
            "empty and the European ones fill them with artwork - and "
            "from the words only one executable carries. Override it if "
            "a disc this has never seen guesses wrong.")
        self.build_box.addItem("Auto", None)
        for _b in dicts.builds():
            self.build_box.addItem(_b, _b)
        self.build_box.currentIndexChanged.connect(self._build_changed)

        self.page_tabs = QTabBar()
        self.page_tabs.setExpanding(False)
        self.page_tabs.setToolTip(
            "Which page to edit. The fonts are one 4bpp page of the IMG "
            "and the title screen is another - its menu is artwork, not "
            "text, so it can only be translated by redrawing it here.")
        for kind in PAGE_KINDS:
            self.page_tabs.addTab(kind.label)
        self.page_tabs.currentChanged.connect(self._page_changed)

        self.clut_box = QComboBox()
        self.clut_box.setToolTip(
            "Which of the page's own palettes to draw it with. A 4bpp "
            "page holds indices, not colours - the same rows are white "
            "text in a dialogue box and orange artwork on a menu - so "
            "this is a choice about how to look at it, not a property "
            "of the data.")
        self.clut_box.currentIndexChanged.connect(self._redraw)

        self.regions_check = QCheckBox("Regions")
        self.regions_check.setChecked(True)
        self.regions_check.setToolTip(
            "Mark where the two fonts, the menu artwork and the "
            "palettes sit in the page.")
        self.regions_check.toggled.connect(self._toggle_regions)

        self.cells_check = QCheckBox("What's selectable")
        self.cells_check.setChecked(True)
        self.cells_check.setToolTip(
            "Outline everything that can be picked - each glyph cell, "
            "each double-width glyph as one, and each menu sprite.")
        self.cells_check.toggled.connect(self._toggle_cells)

        self.export_button = QPushButton("Export...")
        self.export_button.setToolTip(
            "Write whatever is selected out as a PNG, in the colours it "
            "is drawn in here. Edit it anywhere, then bring it back with "
            "Import.")
        self.export_button.clicked.connect(self.export_selection)

        self.import_button = QPushButton("Import...")
        self.import_button.setToolTip(
            "Replace the selected part with a PNG of the same size. "
            "Every pixel is matched to the nearest colour in that part's "
            "own palette, because the page stores palette indices, not "
            "colours - so an edit can only use the colours the game has "
            "for it.")
        self.import_button.clicked.connect(self.import_selection)

        self.page_export = QPushButton("Export page...")
        self.page_export.setToolTip(
            "Write the WHOLE page out as one indexed PNG - every glyph, "
            "every sprite, the palettes and all. One pixel per texel, "
            "and the pixel values ARE the 4-bit indices, so a page "
            "exported and imported back is unchanged to the byte.")
        self.page_export.clicked.connect(self.export_page)

        self.page_import = QPushButton("Import page...")
        self.page_import.setToolTip(
            "Replace the whole page from an indexed PNG of the same "
            "size. It has to stay indexed (mode P): the pixels are CLUT "
            "indices, not colours, so a flattened RGB copy would have "
            "thrown away the only thing that matters.")
        self.page_import.clicked.connect(self.import_page)

        self.info = QLabel("No disc open.")

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.addWidget(QLabel("Build:"))
        bar.addWidget(self.build_box)
        bar.addWidget(QLabel("Palette:"))
        bar.addWidget(self.clut_box, 1)
        bar.addWidget(self.regions_check)
        bar.addWidget(self.cells_check)
        bar.addWidget(self.page_export)
        bar.addWidget(self.page_import)
        bar.addWidget(self.info, 2)

        self.export_button.setParent(None)
        self.import_button.setParent(None)

        self.detail = _Detail()
        self.detail.zoom_row.addWidget(self.export_button)
        self.detail.zoom_row.addWidget(self.import_button)
        self.detail.changed.connect(self._detail_edited)
        self.detail.edited.connect(self._remember)
        self.detail.stroke_ended.connect(self._end_stroke)
        self.detail.undo_wanted.connect(self.undo)
        self.detail.reset_wanted.connect(self.reset)
        self.detail.save_wanted.connect(self.save_page)
        self.detail.renamed.connect(self._rename_code)
        self.detail.copy_wanted.connect(self.copy_selection)
        self.detail.paste_wanted.connect(self.paste_selection)
        self.detail.palette_chosen.connect(self._palette_for_selection)
        self.detail.show_clut.connect(self._mark_clut)

        self._flash = QTimer(self)
        self._flash.setSingleShot(True)
        self._flash.timeout.connect(lambda: self._mark_clut(False))

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self.page_tabs)
        left_layout.addLayout(bar)
        left_layout.addWidget(self.scroll, 1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(split)

    def _build_changed(self, index):
        """Read the page as a different disc's layout.

        Auto puts back whatever the page itself said - see dicts.detect."""
        chosen = self.build_box.itemData(index)
        build = chosen or self.detected
        if build == self.build:
            return
        self.build = build
        self.glyph_top = dicts.glyph_top(build)
        self.canvas.glyph_top = self.glyph_top
        self.canvas.build = build
        self.selection = None
        self.canvas.selection = None
        self.canvas.clut_mark = None
        self.detail.show_selection(None, None, None, "Nothing selected")
        self._redraw()
        note = "" if chosen else f" (auto: {self.detected_why})"
        self.info.setText(f"Reading the page as {build}, "
                          f"grid at row {self.glyph_top}{note}")

    def _letters(self):
        """What each code means on this build, with claims laid over.

        The European discs draw 64 accented letters the US disc spends
        on button icons, so the base table is the build's, not one
        shared table with the US meanings baked in."""
        base = dicts.for_build(self.build)
        if self.table is not None:
            base.update(self.table.chars)
        return base

    def _page_changed(self, index):
        """Show a different page of the IMG.

        Edits are per-page and are written by Save, so switching with
        unsaved work in hand would drop it silently - hence the warning
        rather than a quiet re-read."""
        if not 0 <= index < len(PAGE_KINDS):
            return
        kind = PAGE_KINDS[index]
        if kind is self.kind:
            return
        self._end_stroke()
        if self.history:
            answer = QMessageBox.question(
                self, "Unsaved changes",
                f"{len(self.history)} change(s) to {self.kind.label} have "
                "not been written to the IMG. Leave them behind?")
            if answer != QMessageBox.StandardButton.Yes:
                self.page_tabs.blockSignals(True)
                self.page_tabs.setCurrentIndex(PAGE_KINDS.index(self.kind))
                self.page_tabs.blockSignals(False)
                return
        self.kind = kind
        self.selection = None
        self.states = {}
        self.canvas.selection = None
        self.canvas.clut_mark = None
        self.detail.show_selection(None, None, None, "Nothing selected")
        self.set_source(self.cd_folder)

    def _remember(self, x, y, was):
        """Note a texel's old value, as part of the stroke in progress."""
        self.stroke.append((x, y, was))

    def _end_stroke(self, name="drawing"):
        """Close the stroke in progress and make it one undoable action."""
        if self.stroke:
            self.history.append((name, self.stroke))
            self.stroke = []

    def undo(self):
        """Take back the last thing done - a whole stroke, or an import.

        Restored newest-first inside the action, so a texel painted
        twice in one stroke ends up with what it held before the stroke
        rather than what it held mid-way through."""
        self._end_stroke()
        if not self.history or not self.page:
            self.info.setText("Nothing to undo")
            return
        name, texels = self.history.pop()
        for x, y, was in reversed(texels):
            self.page[y][x] = was
        self._redraw()
        self._refresh_detail()
        left = f"{len(self.history)} left" if self.history else "nothing left"
        self.info.setText(f"Undid {name} ({len(texels)} texels) - {left}")

    def reset(self):
        """Put the selection back to how the disc has it.

        Only the selection, and without asking. Reset is what you reach
        for after making a mess of one glyph, so throwing away the whole
        page's work - and stopping to confirm it - is the wrong shape
        for the button entirely. It is undoable like any other action,
        which is what makes not asking safe.

        The palette goes back too. Trying four palettes on a word and
        wanting out of it is exactly as much a thing to undo as a bad
        brush stroke, and a Reset that silently kept the last one tried
        was answering a question nobody asked."""
        if not self.page or self.original is None:
            return
        box, _palette = self._selected_box()
        if box is None:
            self.info.setText("Select a glyph to reset")
            return
        kind, what = self.selection
        palette_was = self._palette_key(kind, what)
        if kind == "sprite":
            self.states.pop(what, None)
        else:
            for i, (row, slot, _pal) in enumerate(self.cluts):
                if (row, slot) == tuple(self.kind.default_clut):
                    self.clut_box.setCurrentIndex(i)
                    break
        palette_now = self._palette_key(kind, what)
        repalette = palette_now != palette_was
        self._end_stroke()
        x, y, width, height = box
        for row in range(height):
            for col in range(width):
                was = self.page[y + row][x + col]
                if was != self.original[y + row][x + col]:
                    self.stroke.append((x + col, y + row, was))
                    self.page[y + row][x + col] = self.original[y + row][x + col]
        touched = len(self.stroke)
        self._end_stroke("reset")
        self._redraw()
        if repalette:
            self.detail.set_palettes(self.cluts, palette_now)
        self._refresh_detail()
        said = describe(*self.selection, build=self.build)
        if touched and repalette:
            self.info.setText(f"Reset {said} - {touched} texel(s) put back, "
                              f"palette back to {palette_now[0]}/"
                              f"{palette_now[1]}")
        elif touched:
            self.info.setText(f"Reset {said} - {touched} texel(s) put back")
        elif repalette:
            self.info.setText(f"Reset {said} - palette back to "
                              f"{palette_now[0]}/{palette_now[1]}")
        else:
            self.info.setText(f"{said} was unchanged")

    def _mark_clut(self, on):
        """Outline the selected palette's own 16 words in the page.

        A palette is stored in the page like anything else - the last 32
        rows are four palettes each, sixteen 16-bit words apiece, which
        is 64 texels - so it can be pointed at. Held rather than
        toggled, because it is a "where is it" question, not a mode."""
        if not on or not self.selection:
            self.canvas.clut_mark = None
        else:
            row, slot = self._palette_key(*self.selection)
            words = fontpage.CLUT_ENTRIES * 4          # texels per palette
            self.canvas.clut_mark = (slot * words, row, words, 1)
        self.canvas.update()

    def _palette_for_selection(self, key):
        """Draw this part with the palette just picked.

        For a sprite it is remembered, so the page shows it that way
        too - the menu words are one artwork in four colours and the
        page cannot know which one is meant without being told.

        For a glyph the page-wide choice follows instead, because the
        fonts are drawn with ONE palette: picking one for a glyph and
        leaving the page on another would show the same glyph in two
        colours at once. Either way the page changes, which it did not
        before - the choice reached the panel and stopped there."""
        if not self.selection:
            return
        kind, what = self.selection
        if kind == "sprite":
            self.states[what] = tuple(key)
            self._redraw()
            self.info.setText(f"{what} drawn with palette "
                              f"{key[0]}/{key[1]}")
        else:
            for i, (row, slot, _pal) in enumerate(self.cluts):
                if (row, slot) == tuple(key):
                    self.clut_box.setCurrentIndex(i)   # redraws the page
                    break
            self.info.setText(f"Fonts drawn with palette {key[0]}/{key[1]}")
        self._flash_clut()

    def _flash_clut(self):
        """Point at the palette just picked, briefly.

        Same marking Show gives, without having to go and hold it: a
        palette change is exactly when "which sixteen words was that?"
        is worth answering, and it is over before it is in the way."""
        self._mark_clut(True)
        self._flash.start(1200)

    def copy_selection(self):
        """Keep the selected glyph's texels, to paste elsewhere."""
        box, _palette = self._selected_box()
        if box is None or not self.page:
            self.info.setText("Select a glyph to copy")
            return
        x, y, width, height = box
        self.clipboard = [[self.page[y + row][x + col] for col in range(width)]
                          for row in range(height)]
        self.info.setText(
            f"Copied {describe(*self.selection, build=self.build)} ({width}x{height})")

    def paste_selection(self):
        """Put the copied glyph into the selection.

        Aligned to the top-left and clipped, not scaled: a 16-wide glyph
        pasted into an 8-wide cell should lose its right half rather
        than be squashed into something nobody drew."""
        if not self.clipboard:
            self.info.setText("Nothing copied yet")
            return
        box, _palette = self._selected_box()
        if box is None or not self.page:
            self.info.setText("Select where to paste")
            return
        x, y, width, height = box
        self._end_stroke()
        for row in range(min(height, len(self.clipboard))):
            line = self.clipboard[row]
            for col in range(min(width, len(line))):
                was = self.page[y + row][x + col]
                if was != line[col]:
                    self.stroke.append((x + col, y + row, was))
                    self.page[y + row][x + col] = line[col]
        touched = len(self.stroke)
        self._end_stroke("paste")
        self._redraw()
        self._refresh_detail()
        self.info.setText(
            f"Pasted into {describe(*self.selection, build=self.build)} - {touched} texel(s)"
            if touched else "Pasted - nothing differed")

    def _letter_warning(self, code, char):
        """(what is wrong with this assignment, whether to apply it).

        Two different kinds of wrong. Something that cannot be a cell's
        letter at all is refused, because applying it would put a
        character into the table that no text could ever encode to.
        Claiming a letter another cell already draws is allowed - it is
        how a translation moves a letter to a cell it has redrawn - but
        it is worth saying out loud, since the cell left behind quietly
        stops being reachable."""
        if not char:
            return "", True
        if "{" in char or "}" in char:
            # Only this cell's own escape means anything, and that is
            # handled above as "leave it unassigned". Another cell's
            # would not even do what it looks like: the packer reads
            # {$81} as the byte 0x81 before it ever consults the letter
            # table, so the claim could never take effect.
            return (f"{char!r} is not something a cell can be assigned. "
                    f"{{${code:02X}}} - this cell's own - means it has no "
                    "letter; any other brace form encodes to the byte it "
                    "names, not to this cell.", False)
        if len(char) > 1:
            return (f"{char!r} is {len(char)} characters. A cell draws one, "
                    "so the rest could never appear.", False)
        others = sorted(c for c, t in self._letters().items()
                        if t == char and c != code
                        and c not in translation.CONTROL_CODES)
        if others:
            listed = ", ".join(f"{c:#04x}" for c in others[:4])
            more = "" if len(others) <= 4 else f" and {len(others) - 4} more"
            return (f"{char!r} is already drawn by {listed}{more}. Text will "
                    "encode to this cell now; the other keeps its artwork "
                    "but stops being reachable.", True)
        return "", True

    def _rename_code(self, code, char):
        """Give a code a different character - see translation.Table."""
        if self.table is None or code is None:
            return
        char = char.strip()
        # The placeholder an unassigned cell shows means "still
        # unassigned", not "assign it the literal text {$81}".
        if char == f"{{${code:02X}}}":
            char = ""
        # A refusal puts the field back to what the cell really says, so
        # the warning has to come after that - shown first, the refresh
        # wiped it and the edit vanished with no explanation at all.
        if code in translation.CONTROL_CODES:
            self._refresh_detail()
            self.detail.warn(
                f"{code:#04x} is a control the game acts on, not a glyph. "
                "It cannot be given a letter.")
            return
        warning, apply_it = self._letter_warning(code, char)
        if not apply_it:
            self._refresh_detail()
            self.detail.warn(warning)
            return
        try:
            self.table.claim(code, char)
        except ValueError as e:
            self._refresh_detail()
            self.detail.warn(str(e))
            return
        try:
            translation.save(self.cd_folder, self.table)
        except Exception as e:
            QMessageBox.critical(self, "Could not save the table", str(e))
            return
        if warning:
            self.detail.warn(warning)
        self.info.setText(
            f"{code:#04x} now draws {char!r}" if char
            else f"{code:#04x} released - text reaches it as {{${code:02X}}}")

    def _refresh_detail(self):
        """Show the current selection again after the page changed."""
        if not self.selection:
            return
        kind, what = self.selection
        box, palette = self._selected_box()
        if box:
            self.detail.show_selection(self.page, box, palette,
                                       describe(kind, what, self.build),
                                       *self._meaning(kind, what),
                                       deep=(kind == "deep"))

    def _palette_key(self, kind, what):
        """Which palette this part is currently drawn with."""
        if kind == "sprite":
            return (self.states.get(what) or self.kind.cluts_for(self.build).get(what)
                    or self._font_key())
        return self._font_key()

    def _meaning(self, kind, what):
        """(code, character, note) for a glyph selection.

        An unassigned cell reads back as {$XX} rather than as nothing,
        because that is what it actually is: no letter reaches it, and
        text that wants it has to name the raw byte. A blank field said
        the same thing far less clearly - it looked like a field waiting
        to be filled in rather than a fact about the cell.

        The note is for a cell whose {$XX} would still mislead. The
        button icons are the case that matters: they draw something the
        game very much still needs, and nothing about an unassigned cell
        says so."""
        if kind not in ("glyph", "system") or what is None:
            return None, "", ""
        letters = self._letters()
        char = letters.get(what, "")
        note = ""
        if what in translation.CONTROL_CODES:
            note = (f"{char} is a control the game acts on, not a glyph it "
                    "draws, so it cannot be reassigned. The cell may still "
                    "hold artwork.")
        elif not char:
            char = f"{{${what:02X}}}"
            icon = ICON_CELLS.get(what) or ICON_CELLS.get(what - 1)
            if icon:
                note = (f"Draws the button icon that {icon} prints. No "
                        "text reaches this cell as a letter, so it has no "
                        "assigned letter - but the artwork is still used.")
        return what, char, note

    def _detail_edited(self):
        """A texel was painted on the right - redraw the page."""
        self._redraw()
        self.info.setText("Edited - press Save to IMG to keep it")

    def set_source(self, cd_folder, keep_palette=False):
        """Read the page and its palettes off the disc.

        keep_palette holds the page-wide palette choice across a
        re-read. Saving goes through here, and without it every save
        threw the choice away and snapped back to the font palette -
        which looks exactly like the save having done nothing."""
        was = self._font_key() if self.cluts else None
        self.cd_folder = cd_folder
        self.history = []
        self.stroke = []
        self.original = None
        self.canvas.kind = self.kind
        try:
            self.table = translation.load(cd_folder) if cd_folder else None
        except Exception:
            self.table = None
        self.page = None
        self.cluts = []
        self.clut_box.blockSignals(True)
        self.clut_box.clear()
        if cd_folder:
            try:
                self.page = fontpage.read_page(cd_folder, self.kind.spec)
                # Kept as the disc has it, so one glyph can be put back
                # without re-reading and losing the rest of the work.
                self.original = [row[:] for row in self.page]
                self.cluts = fontpage.read_cluts(cd_folder, self.kind.spec)
            except Exception as e:
                self.info.setText(f"Could not read the page: {e}")
        # Which disc is this? Everything about the layout follows.
        #
        # Read the FONTS page for this specifically, never self.page -
        # self.page is whatever kind is currently on show, and detect()
        # reads rows 224-239 of it looking for the font page's own
        # empty-vs-artwork signal. Handed the Title page instead (its
        # rows 224-239 are the picture/menu area, never empty) it read
        # every disc as European the moment the Title tab was open,
        # which is a page-switch flipping the build underneath work
        # already in progress on the Fonts page.
        try:
            detect_page = (self.page if self.kind is FONT_PAGE
                          else fontpage.read_page(cd_folder, fontpage.FONTS))
        except Exception:
            detect_page = None
        if detect_page:
            exe = b""
            try:
                exe_path = os.path.join(cd_folder, "MAIN.EXE")
                if os.path.exists(exe_path):
                    with open(exe_path, "rb") as f:
                        exe = f.read()
            except Exception:
                exe = b""
            self.detected, self.detected_why = dicts.detect(detect_page, exe)
            if self.build_box.currentData() is None:
                self.build = self.detected
            self.glyph_top = dicts.glyph_top(self.build)
        self.canvas.glyph_top = self.glyph_top
        self.canvas.build = self.build
        wanted = was if (keep_palette and was) else self.kind.default_clut
        named = self.kind.named()
        at = 0
        for i, (row, slot, _pal) in enumerate(self.cluts):
            note = ""
            for name, where in named.items():
                if where == (row, slot):
                    note = f"  - {name}"
            if (row, slot) == wanted:
                at = i
            self.clut_box.addItem(f"row {row} slot {slot}{note}", i)
        self.clut_box.setCurrentIndex(at)
        self.clut_box.blockSignals(False)
        if self.page:
            spec = self.kind.spec
            self.info.setText(
                f"{spec.name}: {spec.width}x{spec.height} page, "
                f"{len(self.cluts)} palettes, read as {self.build} "
                f"(grid at row {self.glyph_top})")
        self._redraw()

    def what_is_at(self, x, y):
        """What the page holds at this texel: a sprite, a glyph, or the
        palette rows. Named rather than numbered where the page has a
        name for it."""
        spec = self.kind.spec
        for name, top, bottom, left, right in self.kind.sprites_for(self.build):
            if top <= y < bottom and left <= x < right:
                return ("sprite", name)
        deep = self.kind.deep_at(x, y)
        if deep:
            return ("deep", deep)
        if y >= spec.clut_top:
            # 16 words to a palette, four texels to a word.
            return ("palette", (y, x // (fontpage.CLUT_ENTRIES * 4)))
        if not self.kind.grid:
            return ("artwork", None)
        if y < self.glyph_top:
            # The system font is the dialogue grid at half height.
            code = (y // fontpage.SYSTEM_H) * fontpage.GLYPH_COLS + x // fontpage.GLYPH_W
            return ("system", code)
        if y < 168:
            row = (y - self.glyph_top) // fontpage.GLYPH_H
            code = row * fontpage.GLYPH_COLS + x // fontpage.GLYPH_W
            return ("glyph", first_of(code, self.build))
        return ("artwork", None)

    def _clicked(self, x, y):
        kind, what = self.what_is_at(x, y)
        self.selection = (kind, what)
        self.canvas.selection = self._box_for(kind, what)
        self.canvas.update()
        box, palette = self._selected_box()
        if box:
            self.detail.set_palettes(self.cluts, self._palette_key(kind, what))
            self.detail.show_selection(self.page, box, palette,
                                       describe(kind, what, self.build),
                                       *self._meaning(kind, what),
                                       deep=(kind == "deep"))
        self.selected.emit(kind, what)
        self.info.setText(f"({x}, {y})  {describe(kind, what, self.build)}")
        if box:
            # Same brief red marking a palette change gives. Picking a
            # part is the other moment "and which palette is that?" is
            # worth answering without being asked.
            self._flash_clut()

    def _move_selection(self, dx, dy):
        """Step the selection one part in that direction.

        Moving by the selection's OWN size rather than by a fixed cell is
        what makes one rule serve the whole page: a single glyph steps 8
        texels, a double-width one steps 16, and a sprite steps its own
        width, so the arrows always land on the next thing rather than
        inside the current one.

        Running off the right edge wraps to the next row down, because
        the grid is read that way and a code order that stops dead at
        column 31 is not the order anyone is looking in."""
        if not self.page:
            return
        spec = self.kind.spec
        if not self.selection:
            self._clicked(0, self.glyph_top if self.kind.grid else 0)
            return
        box = self._box_for(*self.selection)
        if box is None:
            return
        x, y, width, height = box
        if dx:
            x = x + width if dx > 0 else x - 1
        if dy:
            y = y + height if dy > 0 else y - 1
        if x >= spec.width:
            x, y = 0, y + height
        elif x < 0:
            x, y = spec.width - 1, y - height
        if not (0 <= y < spec.height) or not (0 <= x < spec.width):
            return
        self._clicked(x, y)
        moved = self.canvas.selection
        if moved:
            self.scroll.ensureVisible(self.canvas.scaled(moved[0]),
                                      self.canvas.scaled(moved[1]), 60, 60)

    def _box_for(self, kind, what):
        """The rectangle to outline for a selection, in page texels."""
        if kind == "deep":
            return self.kind.deep_box(what)
        if kind == "sprite":
            for name, top, bottom, left, right in self.kind.sprites_for(self.build):
                if name == what:
                    return (left, top, right - left, bottom - top)
        if kind == "glyph" and what is not None:
            row, col = divmod(what, fontpage.GLYPH_COLS)
            return (col * fontpage.GLYPH_W,
                    self.glyph_top + row * fontpage.GLYPH_H,
                    fontpage.GLYPH_W * glyph_cells(what, self.build),
                    fontpage.GLYPH_H)
        if kind == "system" and what is not None:
            row, col = divmod(what, fontpage.GLYPH_COLS)
            return (col * fontpage.GLYPH_W, row * fontpage.SYSTEM_H,
                    fontpage.GLYPH_W, fontpage.SYSTEM_H)
        return None

    def _selected_box(self):
        """The selection as (x, y, w, h) and the palette it is drawn
        with, or (None, None)."""
        if not self.selection:
            return None, None
        kind, what = self.selection
        box = self._box_for(kind, what)
        if box is None:
            return None, None
        if kind == "deep":
            return box, self.kind.deep_palette(self.cluts)
        key = (self.states.get(what) or self.kind.cluts_for(self.build).get(what)
               if kind == "sprite" else None) or self._font_key()
        by_key = {(r, s): p for r, s, p in self.cluts}
        return box, by_key.get(key)

    def _font_key(self):
        which = self.clut_box.currentData()
        if which is None or which >= len(self.cluts):
            return self.kind.default_clut
        return (self.cluts[which][0], self.cluts[which][1])

    def export_selection(self):
        """Write the selected part out as a PNG."""
        box, palette = self._selected_box()
        if box is None or not self.page:
            QMessageBox.information(self, "Nothing selected",
                                    "Click a glyph or a sprite first.")
            return
        x, y, width, height = box
        kind, what = self.selection
        stem = f"{what}" if kind in ("sprite", "deep") else f"{kind}_{what:#04x}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export selection", f"{stem}.png".replace("/", "-"),
            "PNG (*.png)")
        if not path:
            return
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(0)
        if kind == "deep":
            # One pixel per byte, so what comes out is the picture at the
            # size it is drawn rather than at the width of its VRAM.
            for row in range(height):
                line = self.page[y + row]
                for col in range(width):
                    at = x + col * 2
                    value = line[at] | (line[at + 1] << 4)
                    entry = palette[value] if value < len(palette) else None
                    if entry and entry[3]:
                        image.setPixelColor(col, row, QColor(*entry))
        else:
            for row in range(height):
                for col in range(width):
                    entry = (palette[self.page[y + row][x + col]]
                             if palette else None)
                    if entry and entry[3]:
                        image.setPixelColor(col, row, QColor(*entry))
        if not image.save(path):
            QMessageBox.critical(self, "Export failed", f"Couldn't write {path}")
            return
        self.info.setText(f"Wrote {width}x{height} to {path}")

    def import_selection(self):
        """Read a PNG back into the selected part.

        The page stores 4-bit palette indices, not colours, so an
        imported pixel becomes whichever entry of that part's own
        palette it is nearest. An edit can therefore only use the
        colours the game already has for it - which is a property of
        the format, not a limitation of this."""
        box, palette = self._selected_box()
        if box is None or not self.page:
            QMessageBox.information(self, "Nothing selected",
                                    "Click a glyph or a sprite first.")
            return
        if not palette:
            QMessageBox.warning(self, "No palette",
                                "That part has no palette to match against.")
            return
        x, y, width, height = box
        path, _ = QFileDialog.getOpenFileName(
            self, "Import into selection", "", "Images (*.png *.bmp *.jpg)")
        if not path:
            return
        if self.selection[0] == "deep":
            self._import_picture(path, box)
            return
        image = QImage(path)
        if image.isNull():
            QMessageBox.critical(self, "Import failed", "Could not read it.")
            return
        if image.width() != width or image.height() != height:
            answer = QMessageBox.question(
                self, "Different size",
                f"That image is {image.width()}x{image.height()} and the "
                f"selection is {width}x{height}. Scale it to fit?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            image = image.scaled(width, height,
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        self._end_stroke()
        for row in range(height):
            for col in range(width):
                self.stroke.append((x + col, y + row,
                                    self.page[y + row][x + col]))
                self.page[y + row][x + col] = _nearest(
                    image.pixelColor(col, row), palette)
        self._end_stroke("import")
        self._redraw()
        self.info.setText(f"Imported {width}x{height} - not written yet, "
                          "press Save to IMG")

    def _import_picture(self, path, box):
        """Replace an 8bpp picture, and give it a palette of its own.

        A 320x240 photograph cannot be held to sixteen colours picked for
        something else, so this does not match the new picture against
        the old palette the way a glyph import does. It quantises the
        picture to its own best 256 and writes THAT over the palette row,
        which is free to rewrite because nothing else on the page reads
        it - the menu words have four palettes of their own."""
        from PIL import Image

        x, y, width, height = box
        try:
            picture = Image.open(path).convert("RGB")
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return
        if picture.size != (width, height):
            answer = QMessageBox.question(
                self, "Different size",
                f"That picture is {picture.size[0]}x{picture.size[1]} and the "
                f"title screen is {width}x{height}. Scale it to fit?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            picture = picture.resize((width, height), Image.LANCZOS)

        quantised = picture.quantize(colors=256, method=Image.MEDIANCUT)
        rgb = quantised.getpalette()[:768]
        indices = list(quantised.getdata())

        self._end_stroke()
        # The picture itself, two texels to a byte.
        for row in range(height):
            line = self.page[y + row]
            for col in range(width):
                at = x + col * 2
                value = indices[row * width + col]
                for half, nibble in ((0, value & 0x0F), (1, value >> 4)):
                    if line[at + half] != nibble:
                        self.stroke.append((at + half, y + row, line[at + half]))
                        line[at + half] = nibble

        # Then its palette, over the row the picture reads through. One
        # entry is a 16-bit word, which is four texels, low nibble first.
        row_at = self.kind.deep_row
        line = self.page[row_at]
        for k in range(256):
            r, g, b = rgb[k * 3:k * 3 + 3]
            word = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
            # Word 0 is the page's "draw nothing", so a colour that lands
            # on pure black would punch a hole rather than be black.
            word = word or 1
            for half in range(4):
                nibble = (word >> (4 * half)) & 0x0F
                at = k * 4 + half
                if at < len(line) and line[at] != nibble:
                    self.stroke.append((at, row_at, line[at]))
                    line[at] = nibble

        touched = len(self.stroke)
        self._end_stroke("picture import")
        self.cluts = fontpage.read_cluts_from(self.page, self.kind.spec)
        self._redraw()
        self._refresh_detail()
        self.info.setText(
            f"Replaced the {width}x{height} picture and rebuilt its 256 "
            f"colours - {touched} texel(s) changed, press Save")

    def export_page(self):
        """The whole page out as one indexed PNG.

        Indexed rather than RGB on purpose. A 4bpp page IS indices, and
        the palette a viewer sees is a choice about how to look at it -
        so flattening to colour would throw away the page and keep the
        choice. Carrying the current palette in the PNG makes the file
        legible in an image editor without changing a pixel value."""
        if not self.page or not self.cd_folder:
            QMessageBox.information(self, "No disc open",
                                    "Open a disc first.")
            return
        spec = self.kind.spec
        stem = f"{spec.name.lower().replace(' ', '_')}_page.png"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {spec.name} page", stem, "PNG (*.png)")
        if not path:
            return
        which = self.clut_box.currentData()
        clut = (self.cluts[which][2]
                if which is not None and which < len(self.cluts) else None)
        try:
            fontpage.export_png(self.cd_folder, path, clut, spec)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        self.info.setText(
            f"Wrote the whole {spec.width}x{spec.height} page to {path}")

    def import_page(self):
        """Replace the whole page from an indexed PNG."""
        if not self.page or not self.cd_folder:
            QMessageBox.information(self, "No disc open",
                                    "Open a disc first.")
            return
        spec = self.kind.spec
        path, _ = QFileDialog.getOpenFileName(
            self, f"Import a {spec.name} page", "", "PNG (*.png)")
        if not path:
            return
        answer = QMessageBox.question(
            self, "Replace the whole page?",
            f"This writes every texel of {spec.name} into TOMBA2.IMG at "
            "once, palettes included, and it is not undoable here - "
            "Undo only takes back edits made in this tab.\n\nGo ahead?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            count = fontpage.import_png(self.cd_folder, path, spec)
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return
        self.history = []
        self.stroke = []
        self.set_source(self.cd_folder, keep_palette=True)
        if self.selection:
            self.detail.set_palettes(self.cluts,
                                     self._palette_key(*self.selection))
            self._refresh_detail()
        self.info.setText(
            f"Replaced the whole page from {os.path.basename(path)} - "
            f"{count} shard(s) rewritten")
        self.saved.emit()

    def save_page(self):
        """Write the page and the table back, then read them again.

        Reading back is the point: it is what proves the edit fits the
        room the shard was given, and it puts the view on what is
        actually on the disc rather than on what was drawn."""
        if not self.page or not self.cd_folder:
            return
        self._end_stroke()
        changes = len(self.history)
        try:
            fontpage.write_page(self.cd_folder, self.page,
                                spec=self.kind.spec)
            if self.table is not None:
                translation.save(self.cd_folder, self.table)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.set_source(self.cd_folder, keep_palette=True)
        # Re-reading builds a NEW page, and the detail panel was left
        # holding the old one - so after a save it was drawing, and
        # being drawn on, an orphan that nothing rendered. Point it at
        # what was actually read back.
        if self.selection:
            self.detail.set_palettes(self.cluts,
                                     self._palette_key(*self.selection))
            self._refresh_detail()
        self.info.setText(
            f"Wrote {changes} change(s) to TOMBA2.IMG - "
            "save the disc image to keep it")
        self.saved.emit()

    def _toggle_regions(self, on):
        self.canvas.show_regions = on
        self.canvas.update()

    def _toggle_cells(self, on):
        self.canvas.show_cells = on
        self.canvas.update()

    def _redraw(self):
        if not self.page:
            self.canvas.clear()
            return
        which = self.clut_box.currentData()
        font_clut = (self.cluts[which][0], self.cluts[which][1]) if (
            which is not None and which < len(self.cluts)
        ) else self.kind.default_clut
        image = _render(self.page, self.cluts, font_clut, self.states,
                        self.kind, self.build)
        self.canvas.set_image(image)
        self.canvas.set_zoom(fit_zoom((image.width(), image.height()),
                                      self.scroll.viewport().size(), 4))


def _nearest(colour, palette):
    """The palette entry an imported pixel becomes.

    A transparent pixel is index 0, which is what the page uses for
    "draw nothing"; everything else takes the nearest opaque entry."""
    if colour.alpha() < 8:
        return 0
    want = (colour.red(), colour.green(), colour.blue())
    best, at = None, 0
    for index, entry in enumerate(palette):
        if not entry[3]:
            continue
        gap = sum(abs(a - b) for a, b in zip(want, entry[:3]))
        if best is None or gap < best:
            best, at = gap, index
    return at


# The US page draws the four button icons in cells 0xA0-0xA7, two cells
# to an icon because they are 16 wide against the grid's 8. No US string
# reaches them as letters: text asks for them through a control, and the
# byte a control encodes to is not a cell number - {$CIRCLE} is 0xCD.
#
# So these cells have no assigned letter and never will, which looks
# exactly like a free cell unless something says otherwise. They are the
# last cells a translation should claim, since the artwork is still
# needed; hence the note.
ICON_CELLS = {
    0xA0: "{$CIRCLE} (0xCD)",
    0xA2: "{$CROSS} (0xCE)",
    0xA4: "{$TRIANGLE} (0xCF)",
    0xA6: "{$SQUARE} (0xD0)",
}


def glyph_cells(code, build=None):
    """How many 8-pixel cells this code is drawn across - see
    DOUBLE_RUN."""
    run = DOUBLE_RUN.get(build or dicts.DEFAULT_BUILD)
    if run and run[0] <= code <= run[1]:
        return 2
    return 1


def first_of(code, build=None):
    """The code a click belongs to, given the double-width run.

    Landing on the right half of a two-cell glyph names the glyph, not
    the cell, so a selection is always a whole character."""
    run = DOUBLE_RUN.get(build or dicts.DEFAULT_BUILD)
    if run and run[0] <= code <= run[1]:
        return run[0] + ((code - run[0]) // 2) * 2
    return code


def describe(kind, what, build=None):
    """What a selection is, in words."""
    if kind == "deep":
        return f"{what} (8bpp picture)"
    if kind == "sprite":
        return f"sprite: {what}"
    if kind in ("glyph", "system") and what is not None:
        wide = (" (double)" if kind == "glyph" and glyph_cells(what, build) > 1
                else "")
        return (f"{'dialogue' if kind == 'glyph' else 'system'} glyph "
                f"{what:#04x}{wide}")
    if kind == "palette" and what:
        return f"palette row {what[0]} slot {what[1]}"
    return "artwork"


def _palette_map(cluts, font_clut, states):
    """Which palette to draw each row of the page with.

    The page is not one picture. The fonts are drawn with one palette
    and each menu sprite with its own, so drawing the whole thing under
    a single one leaves most of it looking wrong - which is what it did
    before the halves were understood. This returns a palette per row,
    with the artwork rows overridden per sprite in _render."""
    by_key = {(row, slot): pal for row, slot, pal in cluts}
    return by_key.get(font_clut), by_key


def _render(page, cluts, font_clut=FONT_CLUT, states=None, kind=None,
            build=None):
    """The page as an image, each region under the palette it is really
    drawn with."""
    kind = kind or FONT_PAGE
    build = build or dicts.DEFAULT_BUILD
    states = states or {}
    font_pal, by_key = _palette_map(cluts, font_clut, states)
    width, height = kind.spec.width, kind.spec.height
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0)          # transparent; the canvas paints the ground

    # Which palette covers each artwork box.
    boxes = []
    for name, top, bottom, left, right in kind.sprites_for(build):
        key = states.get(name, kind.cluts_for(build).get(name, font_clut))
        boxes.append((top, bottom, left, right, by_key.get(key)))

    # The 8bpp pictures first: two texels make one pixel, read through
    # the wide palette. Drawn one pixel per column, so the picture comes
    # out its real 320 wide instead of smeared across the 640 texels of
    # VRAM it takes up.
    deep_pal = kind.deep_palette(cluts) if kind.deep else []
    for _name, top, bottom, left, right in kind.deep:
        for y in range(top, min(bottom, len(page))):
            line = page[y]
            for i in range((right - left) // 2):
                x = left + i * 2
                if x + 1 >= len(line):
                    break
                value = line[x] | (line[x + 1] << 4)
                if value >= len(deep_pal):
                    continue
                entry = deep_pal[value]
                if entry[3]:
                    image.setPixelColor(left + i, y, QColor(*entry))

    for y in range(min(height, len(page))):
        line = page[y]
        if kind.deep_covers(0, y) and not kind.sprites_in_row(y, build):
            continue                    # drawn above, at 8bpp
        for x in range(min(width, len(line))):
            if kind.deep_covers(x, y):
                continue
            index = line[x]
            palette = font_pal
            for top, bottom, left, right, pal in boxes:
                if top <= y < bottom and left <= x < right:
                    palette = pal
                    break
            entry = palette[index] if palette else None
            if entry is None:
                value = index * 17
                image.setPixelColor(x, y, QColor(value, value, value, 255))
            elif entry[3]:
                image.setPixelColor(x, y, QColor(*entry))
    return image


class _PageCanvas(PixelCanvas):
    """The page, with the region bands drawn over it."""

    # One arrow key: (dx, dy), each -1, 0 or 1.
    arrow = pyqtSignal(int, int)

    _ARROWS = {
        Qt.Key.Key_Left: (-1, 0),
        Qt.Key.Key_Right: (1, 0),
        Qt.Key.Key_Up: (0, -1),
        Qt.Key.Key_Down: (0, 1),
    }

    def __init__(self, parent=None):
        super().__init__(zoom=3, parent=parent)
        # Arrow keys walk the selection, which needs the canvas to be
        # able to hold focus. A grid of 4096 cells is miserable to cross
        # by clicking, and stepping through neighbours is how you find
        # the free ones a translation can take over.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.show_regions = True
        self.show_cells = True
        self.glyph_top = fontpage.GLYPH_TOP
        self.kind = None           # set by the view; which page is shown
        self.build = dicts.DEFAULT_BUILD
        self.selection = None      # (x, y, w, h) in page texels
        self.clut_mark = None      # a palette's own words, while Show is held

    def keyPressEvent(self, event):
        step = self._ARROWS.get(event.key())
        if step is None:
            super().keyPressEvent(event)
            return
        self.arrow.emit(*step)
        event.accept()

    def paint_overlays(self, painter, _area):
        scale = self.scaled
        kind = self.kind or FONT_PAGE
        build = self.build
        # What can be picked, drawn faintly. Without it the page is a
        # wall of glyphs with no sign that any of it is clickable, and
        # nothing says where one sprite stops and the next starts.
        if self.show_cells and self.zoom >= 2:
            if kind.grid:
                painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
                for y in range(0, self.glyph_top, fontpage.SYSTEM_H):
                    for x in range(0, kind.spec.width, fontpage.GLYPH_W):
                        painter.drawRect(scale(x), scale(y),
                                         scale(fontpage.GLYPH_W),
                                         scale(fontpage.SYSTEM_H))
                for row in range((168 - self.glyph_top) // fontpage.GLYPH_H):
                    y = self.glyph_top + row * fontpage.GLYPH_H
                    col = 0
                    while col < fontpage.GLYPH_COLS:
                        code = row * fontpage.GLYPH_COLS + col
                        cells = glyph_cells(code, build)
                        painter.drawRect(scale(col * fontpage.GLYPH_W),
                                         scale(y),
                                         scale(fontpage.GLYPH_W * cells),
                                         scale(fontpage.GLYPH_H))
                        col += cells
            painter.setPen(QPen(QColor(240, 170, 60, 150), 1))
            for _name, top, bottom, left, right in kind.sprites_for(build):
                painter.drawRect(scale(left), scale(top),
                                 scale(right - left), scale(bottom - top))
        # Where the palette in use lives, while Show is held or just
        # after one is picked. Set but never drawn until now, which is
        # why pressing Show appeared to do nothing at all.
        if self.clut_mark:
            x, y, w, h = self.clut_mark
            painter.fillRect(scale(x), scale(y), scale(w), max(2, scale(h)),
                             QColor(255, 40, 40, 110))
            painter.setPen(QPen(QColor(255, 60, 60), 2))
            painter.drawRect(scale(x) - 1, scale(y) - 1,
                             scale(w) + 2, max(3, scale(h)) + 2)
        if self.selection:
            x, y, w, h = self.selection
            painter.setPen(QPen(QColor(90, 200, 255), 2))
            painter.drawRect(scale(x) - 1, scale(y) - 1,
                             scale(w) + 2, scale(h) + 2)
        if not self.show_regions:
            return
        painter.setFont(painter.font())
        for name, top, bottom, colour in kind.regions(self.glyph_top):
            painter.setPen(QPen(colour, 1, Qt.PenStyle.DashLine))
            y = scale(top)
            painter.drawLine(0, y, scale(kind.spec.width), y)
            painter.setPen(QPen(colour, 1))
            painter.drawText(4, y + 12, f"{name}  (rows {top}-{bottom - 1})")


class _Detail(QWidget):
    """The selection, big, with the palette it is drawn in.

    Two things are wanted of a selected glyph that the page itself
    cannot give: seeing it at a size where individual texels are
    distinguishable, and saying which palette it should be read through.
    The second matters more than it sounds - a menu word is the same
    artwork in four colours, and a page-wide palette choice cannot say
    that Status is blue while Full!! is orange.
    """

    changed = pyqtSignal()                 # a texel was painted
    edited = pyqtSignal(int, int, int)     # page x, y, what was there
    stroke_ended = pyqtSignal()            # the mouse came back up
    save_wanted = pyqtSignal()
    reset_wanted = pyqtSignal()
    undo_wanted = pyqtSignal()
    copy_wanted = pyqtSignal()
    paste_wanted = pyqtSignal()
    palette_chosen = pyqtSignal(object)    # (row, slot) picked for this part
    show_clut = pyqtSignal(bool)           # mark where the palette lives
    renamed = pyqtSignal(object, str)      # code, the character it draws

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = None
        self.box = None                    # (x, y, w, h) in page texels
        self.palette = None
        self.code = None                   # the glyph code, when it is one
        self.cluts = []
        self.deep = False                  # is the selection 8bpp?
        self.index = 1                     # what the brush paints
        # Whether the zoom is still following the selection. Once it has
        # been set by hand it stays put, so picking through a row of
        # glyphs does not keep snapping the view back.
        self._fitted = True

        self.canvas = _DetailCanvas()
        self.canvas.checker_light = CHECKER_LIGHT
        self.canvas.checker_dark = CHECKER_DARK
        self.canvas.painted.connect(self._paint_at)
        self.canvas.picked.connect(self._pick_at)
        self.canvas.stroke_ended.connect(self.stroke_ended)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.canvas)
        # The pane keeps whatever width it was dragged to. Without this
        # the canvas sizes itself to the selection and the splitter
        # follows, so the editing pane jumped about as you clicked from
        # an 8-wide glyph to a 166-wide sprite - the view moving on its
        # own while you are working in it.
        self.scroll.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Ignored)
        self.scroll.setMinimumWidth(200)

        self.title = QLabel("Nothing selected")
        self.clut_box = QComboBox()
        self.clut_box.setToolTip(
            "Which palette to read the selection through. The menu words "
            "are one artwork in four colours - this is how to see, and "
            "set, which one you are editing.")
        self.clut_box.currentIndexChanged.connect(self._palette_changed)
        self.swatches = _Swatches()
        self.swatches.picked.connect(self._set_index)

        # What the selected code means in the game's text. A translation
        # has to change the shape AND the meaning, and they have to
        # agree - see gui/txtd/translation.py, which owns the second
        # half and keeps it beside the disc.
        self.char_edit = QLineEdit()
        # Long enough for the longest thing that can legitimately appear
        # here. A cell draws one character, but the control codes read
        # back as their names - {$TRIANGLE} is eleven - and a field four
        # wide turned {$ORANGE} into "{$OR", which looks like corrupt
        # data rather than a field too small.
        self.char_edit.setMaxLength(16)
        self.char_edit.setFixedWidth(120)
        self.char_edit.setToolTip(
            "The character this code draws. Type a new one to reassign "
            "it - a Polish build wanting 'a with ogonek' takes a code "
            "the disc spends on a symbol it never prints. {$XX} means "
            "the cell has no letter and text reaches it only as that raw "
            "byte; clearing the field puts it back to that.")
        self.char_edit.editingFinished.connect(self._rename)

        # A sprite is 30 to 166 texels wide against a glyph's 8, so one
        # fitted zoom cannot suit both - the digits arrive too small to
        # aim at unless the fit can be overridden.
        self.zoom_out = QPushButton("-")
        self.zoom_out.setFixedWidth(28)
        self.zoom_out.clicked.connect(lambda: self._zoom_by(-1))
        self.zoom_in = QPushButton("+")
        self.zoom_in.setFixedWidth(28)
        self.zoom_in.clicked.connect(lambda: self._zoom_by(1))
        self.zoom_fit = QPushButton("Fit")
        self.zoom_fit.setFixedWidth(40)
        self.zoom_fit.clicked.connect(self._fit)
        self.zoom_label = QLabel("")
        self.zoom_label.setMinimumWidth(48)

        self.grid_check = QCheckBox("Show grid")
        self.grid_check.setChecked(True)
        self.grid_check.setToolTip(
            "Rule the zoomed selection into texels. Counting them is the "
            "point of being zoomed in, but the lines sit over the art, so "
            "they come off when you want to see the shape rather than "
            "measure it.")
        self.grid_check.toggled.connect(self._toggle_grid)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #c8a04a;")

        self.show_button = QPushButton("Show")
        self.show_button.setToolTip(
            "Hold to mark where this palette sits in the page. The last "
            "32 rows are palettes rather than pixels, four to a row, and "
            "nothing on screen says which sixteen words belong to the "
            "one being used.")
        self.show_button.pressed.connect(lambda: self.show_clut.emit(True))
        self.show_button.released.connect(lambda: self.show_clut.emit(False))

        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip(
            "Take a copy of the selected glyph, to paste over another "
            "one. Making an accented letter starts as the plain letter.")
        self.copy_button.clicked.connect(self.copy_wanted)

        self.paste_button = QPushButton("Paste")
        self.paste_button.setToolTip(
            "Put the copied glyph into the selection, from its top-left "
            "corner. Anything past the edge is left off rather than "
            "wrapping.")
        self.paste_button.clicked.connect(self.paste_wanted)

        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Put back the last texel painted.")
        self.undo_button.clicked.connect(self.undo_wanted)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setToolTip(
            "Put the selected glyph back to how the disc has it. Only "
            "the selection, and undoable like anything else.")
        self.reset_button.clicked.connect(self.reset_wanted)

        self.save_button = QPushButton("Save")
        self.save_button.setToolTip(
            "Write the page and the character table back to the disc's "
            "files, then read them again so the view shows what was "
            "actually written. The disc image itself is still saved the "
            "usual way.")
        self.save_button.clicked.connect(self.save_wanted)

        head = QHBoxLayout()
        head.setContentsMargins(8, 4, 8, 0)
        self.palette_label = QLabel("Palette:")
        self.letter_label = QLabel("Assigned letter:")

        head.addWidget(self.palette_label)
        head.addWidget(self.clut_box, 1)
        head.addWidget(self.show_button)

        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(8, 0, 8, 0)
        zoom_row.addWidget(QLabel("Zoom:"))
        zoom_row.addWidget(self.zoom_out)
        zoom_row.addWidget(self.zoom_in)
        zoom_row.addWidget(self.zoom_fit)
        zoom_row.addWidget(self.zoom_label)
        zoom_row.addWidget(self.grid_check)
        zoom_row.addStretch(1)
        # Export/Import are added here by FontPageView. They act on the
        # selection, so they belong beside it rather than in the page's
        # own toolbar, where they read as page-wide.
        self.zoom_row = zoom_row

        # The assigned letter sits with the drawing tools rather than
        # up by the title: shape and meaning are the two halves of the
        # same edit, and this is the order they are done in - draw the
        # glyph, then say what it is.
        # The title sits with the letter rather than up by the palette:
        # "dialogue glyph 0x21" and "Assigned letter" are the same
        # question asked twice - which code is this, and what does it
        # spell - and they read as a pair.
        self.title.setContentsMargins(8, 0, 8, 0)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(8, 0, 8, 0)
        name_row.addWidget(self.letter_label)
        name_row.addWidget(self.char_edit)
        name_row.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(8, 0, 8, 6)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.paste_button)
        buttons.addStretch(1)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.reset_button)

        save_row = QHBoxLayout()
        save_row.setContentsMargins(8, 0, 8, 6)
        save_row.addStretch(1)
        save_row.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(head)
        layout.addLayout(zoom_row)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.title)
        layout.addLayout(name_row)
        layout.addLayout(buttons)
        layout.addWidget(self.note)
        layout.addWidget(self.swatches)
        layout.addLayout(save_row)

    def set_palettes(self, cluts, current=None):
        """Fill the palette chooser. Without this it sat empty, which is
        why it never did anything."""
        self.cluts = list(cluts or ())
        self.clut_box.blockSignals(True)
        self.clut_box.clear()
        at = 0
        for i, (row, slot, _pal) in enumerate(self.cluts):
            if current is not None and (row, slot) == tuple(current):
                at = i
            self.clut_box.addItem(f"row {row} slot {slot}", (row, slot))
        self.clut_box.setCurrentIndex(at)
        self.clut_box.blockSignals(False)

    def _palette_changed(self, index):
        key = self.clut_box.itemData(index)
        if key is None:
            return
        for row, slot, pal in self.cluts:
            if (row, slot) == tuple(key):
                self.palette = pal
                self.swatches.set_palette(pal)
                self._redraw()
                break
        self.palette_chosen.emit(tuple(key))

    def show_selection(self, page, box, palette, title, code=None, char="",
                       note="", deep=False):
        self.page, self.box, self.palette = page, box, palette
        self.code = code
        self.deep = deep
        self._picture_mode(deep)
        self.title.setText(title)
        self.note.setStyleSheet("color: #c8a04a;")
        self.note.setText(note)
        self.swatches.set_palette(None if deep else palette)
        self.char_edit.blockSignals(True)
        self.char_edit.setText(char)
        # A control has no glyph to name, so the field is shown but not
        # offered - refusing the edit afterwards says the same thing
        # later and worse.
        self.char_edit.setEnabled(
            code is not None and code not in translation.CONTROL_CODES)
        self.char_edit.blockSignals(False)
        self._redraw()

    def _picture_mode(self, on):
        """Show only what applies to an 8bpp picture.

        A palette chooser, sixteen swatches, a brush and an assigned
        letter are all answers to questions a photograph does not ask.
        What it wants is Export and Import, which stay."""
        for widget in (self.palette_label, self.clut_box, self.show_button,
                       self.swatches, self.letter_label, self.char_edit,
                       self.copy_button, self.paste_button, self.grid_check):
            widget.setVisible(not on)

    def _rename(self):
        """Reassign what the selected code draws."""
        if self.code is not None:
            self.renamed.emit(self.code, self.char_edit.text())

    def warn(self, text):
        """Say something is wrong with what was just typed."""
        self.note.setText(text)
        self.note.setStyleSheet("color: #e06060;")

    def _set_index(self, index):
        self.index = index

    def _toggle_grid(self, on):
        self.canvas.show_grid = on
        self.canvas.update()

    def _zoom_by(self, direction):
        self.canvas.zoom_by(direction)
        self._fitted = False
        self.zoom_label.setText(zoom_label(self.canvas.zoom))

    def _fit(self):
        """Back to whatever fills the pane."""
        if self.box:
            _x, _y, width, height = self.box
            self.canvas.set_zoom(fit_zoom((width, height),
                                          self.scroll.viewport().size(), 24))
        self._fitted = True
        self.zoom_label.setText(zoom_label(self.canvas.zoom))

    def _pick_at(self, col, row):
        """Take the palette index under the cursor as the brush."""
        if not self.page or not self.box or self.deep:
            return
        x, y, width, height = self.box
        if not (0 <= col < width and 0 <= row < height):
            return
        self.index = self.page[y + row][x + col]
        self.swatches.index = self.index
        self.swatches.update()

    def _paint_at(self, col, row, erasing=False):
        """Put a palette index into one texel of the page."""
        if not self.page or not self.box or self.deep:
            return
        x, y, width, height = self.box
        if not (0 <= col < width and 0 <= row < height):
            return
        want = 0 if erasing else self.index
        if self.page[y + row][x + col] == want:
            return
        self.edited.emit(x + col, y + row, self.page[y + row][x + col])
        self.page[y + row][x + col] = want
        self._redraw()
        self.changed.emit()

    def _redraw(self):
        if not self.page or not self.box:
            self.canvas.clear()
            return
        x, y, width, height = self.box
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(0)      # transparent; the canvas paints the ground
        if self.deep:
            # Two texels to a pixel, through the 256-entry palette.
            for row in range(height):
                line = self.page[y + row]
                for col in range(width):
                    at = x + col * 2
                    if at + 1 >= len(line):
                        break
                    value = line[at] | (line[at + 1] << 4)
                    entry = (self.palette[value]
                             if self.palette and value < len(self.palette)
                             else None)
                    if entry and entry[3]:
                        image.setPixelColor(col, row, QColor(*entry))
        else:
            for row in range(height):
                for col in range(width):
                    entry = (self.palette[self.page[y + row][x + col]]
                             if self.palette else None)
                    if entry and entry[3]:
                        image.setPixelColor(col, row, QColor(*entry))
        self.canvas.set_image(image)
        if self._fitted:
            self.canvas.set_zoom(fit_zoom((width, height),
                                          self.scroll.viewport().size(), 24))
        self.zoom_label.setText(zoom_label(self.canvas.zoom))


class _DetailCanvas(PixelCanvas):
    """The zoomed selection. Dragging paints; a grid keeps the texels
    countable, which is the whole point of being zoomed in."""

    painted = pyqtSignal(int, int)          # col, row
    picked = pyqtSignal(int, int)          # col, row - take its colour
    stroke_ended = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(zoom=12, parent=parent)
        self.show_grid = True

    def mousePressEvent(self, event):
        self._emit(event)

    def mouseReleaseEvent(self, event):
        """One press-drag-release is one action to undo.

        Undoing a texel at a time is not undoing anything anyone did -
        a stroke over a glyph is fifty of them, and taking them back one
        by one is worse than useless."""
        self.stroke_ended.emit()

    def mouseMoveEvent(self, event):
        if event.buttons():
            self._emit(event)

    def _emit(self, event):
        """Left paints the chosen index; right takes the one under the
        cursor.

        Picking beats erasing as the right button's job because it can
        do both: the transparent index is a swatch like any other, so
        right-clicking a hole in the glyph selects it and the left
        button then erases. An eraser cannot pick."""
        if self.image is None or self.zoom <= 0:
            return
        buttons = event.buttons() or event.button()
        pos = event.position() if hasattr(event, "position") else event.pos()
        col, row = int(pos.x() // self.zoom), int(pos.y() // self.zoom)
        if buttons & Qt.MouseButton.RightButton:
            self.picked.emit(col, row)
        else:
            self.painted.emit(col, row)

    def paint_overlays(self, painter, _area):
        if self.image is None or self.zoom < 6 or not self.show_grid:
            return
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
        for x in range(self.image.width() + 1):
            painter.drawLine(self.scaled(x), 0,
                             self.scaled(x), self.scaled(self.image.height()))
        for y in range(self.image.height() + 1):
            painter.drawLine(0, self.scaled(y),
                             self.scaled(self.image.width()), self.scaled(y))


class _Swatches(QWidget):
    """The sixteen palette entries, to pick what the brush paints.

    Index 0 is kept and shown as a hole rather than hidden: it is how
    the page says "draw nothing", so it is the eraser."""

    picked = pyqtSignal(int)

    SIZE = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.palette = None
        self.index = 1
        self.setFixedHeight(self.SIZE + 8)
        self.setToolTip("What the brush paints. 0 is transparent - the "
                        "page's way of drawing nothing - so picking it "
                        "makes the brush an eraser. Right-clicking the "
                        "glyph takes the colour under the cursor.")

    def set_palette(self, palette):
        self.palette = palette
        self.update()

    def mousePressEvent(self, event):
        pos = event.position() if hasattr(event, "position") else event.pos()
        index = int(pos.x()) // self.SIZE
        if 0 <= index < 16:
            self.index = index
            self.picked.emit(index)
            self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        for i in range(16):
            x = i * self.SIZE + 4
            entry = self.palette[i] if self.palette else None
            if entry and entry[3]:
                painter.fillRect(x, 4, self.SIZE - 4, self.SIZE - 4,
                                 QColor(*entry))
            else:
                painter.fillRect(x, 4, self.SIZE - 4, self.SIZE - 4,
                                 QColor(40, 40, 44))
                painter.setPen(QPen(QColor(120, 120, 120), 1))
                painter.drawLine(x, 4, x + self.SIZE - 5, self.SIZE - 1)
            painter.setPen(QPen(QColor(255, 255, 255) if i == self.index
                                else QColor(90, 90, 90),
                                2 if i == self.index else 1))
            painter.drawRect(x, 4, self.SIZE - 4, self.SIZE - 4)

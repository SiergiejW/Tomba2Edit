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
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QLineEdit, QPushButton, QScrollArea, QSplitter, QVBoxLayout,
    QWidget,
)

from functions import fontpage
from gui.txtd import translation
from gui.pixel_canvas import PixelCanvas, fit_zoom

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
    ("Items",  176, 191,   1,  53),
    ("Event",  176, 191,  60, 117),
    ("Status", 176, 191, 122, 181),
    ("Help",   176, 191, 186, 229),
    ("health digits and +", 193, 207,   2, 168),
    ("Full!!", 193, 207, 169, 199),
    ("Load",   208, 223,   3,  44),
    ("Save",   208, 223,  51,  95),
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
# Full!! is the case that proves it: it spans both halves of 241/3, so
# "Full" comes out orange from the low end while "!!" comes out blue
# from index 10 (00ACFF). The game draws it exactly that way.
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
SPRITE_CLUTS = {
    "Items": (242, 1),
    "Event": (242, 1),
    "Status": (242, 1),
    "Help": (242, 1),
    "Load": (242, 1),
    "Save": (242, 1),
    "Full!!": (241, 3),
    "health digits and +": (241, 3),
}

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
DOUBLE_LAST = 0xD5

CLUTS = {"dialogue and system font": FONT_CLUT}
for _name, _where in SPRITE_CLUTS.items():
    CLUTS.setdefault(_name, _where)


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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.canvas)

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

        self.export_button = QPushButton("Export selection...")
        self.export_button.setToolTip(
            "Write whatever is selected out as a PNG, in the colours it "
            "is drawn in here. Edit it anywhere, then bring it back with "
            "Import.")
        self.export_button.clicked.connect(self.export_selection)

        self.import_button = QPushButton("Import selection...")
        self.import_button.setToolTip(
            "Replace the selected part with a PNG of the same size. "
            "Every pixel is matched to the nearest colour in that part's "
            "own palette, because the page stores palette indices, not "
            "colours - so an edit can only use the colours the game has "
            "for it.")
        self.import_button.clicked.connect(self.import_selection)

        self.save_button = QPushButton("Save to IMG")
        self.save_button.setToolTip(
            "Write the page back into TOMBA2.IMG and read it again. The "
            "disc itself is written by the usual Save ISO/BIN.")
        self.save_button.clicked.connect(self.save_page)

        self.info = QLabel("No disc open.")

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.addWidget(QLabel("Palette:"))
        bar.addWidget(self.clut_box, 1)
        bar.addWidget(self.regions_check)
        bar.addWidget(self.cells_check)
        bar.addWidget(self.export_button)
        bar.addWidget(self.import_button)
        bar.addWidget(self.save_button)
        bar.addWidget(self.info, 2)

        self.detail = _Detail()
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

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
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
        """Put the selected glyph back to how the disc has it.

        Only the selection, and without asking. Reset is what you reach
        for after making a mess of one glyph, so throwing away the whole
        page's work - and stopping to confirm it - is the wrong shape
        for the button entirely. It is undoable like any other action,
        which is what makes not asking safe."""
        if not self.page or self.original is None:
            return
        box, _palette = self._selected_box()
        if box is None:
            self.info.setText("Select a glyph to reset")
            return
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
        self._refresh_detail()
        self.info.setText(
            f"Reset {describe(*self.selection)} - {touched} texel(s) put back"
            if touched else f"{describe(*self.selection)} was unchanged")

    def _palette_for_selection(self, key):
        """Draw this part with the palette just picked.

        For a sprite it is remembered, so the page shows it that way
        too - the menu words are one artwork in four colours and the
        page cannot know which one is meant without being told."""
        if not self.selection:
            return
        kind, what = self.selection
        if kind == "sprite":
            self.states[what] = tuple(key)
            self._redraw()
            self.info.setText(f"{what} drawn with palette "
                              f"{key[0]}/{key[1]}")

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
            f"Copied {describe(*self.selection)} ({width}x{height})")

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
            f"Pasted into {describe(*self.selection)} - {touched} texel(s)"
            if touched else "Pasted - nothing differed")

    def _rename_code(self, code, char):
        """Give a code a different character - see translation.Table."""
        if self.table is None or code is None:
            return
        self.table.claim(code, char)
        try:
            translation.save(self.cd_folder, self.table)
        except Exception as e:
            QMessageBox.critical(self, "Could not save the table", str(e))
            return
        self.info.setText(
            f"{code:#04x} now draws {char!r}" if char
            else f"{code:#04x} released")

    def _refresh_detail(self):
        """Show the current selection again after the page changed."""
        if not self.selection:
            return
        kind, what = self.selection
        box, palette = self._selected_box()
        if box:
            self.detail.show_selection(self.page, box, palette,
                                       describe(kind, what),
                                       *self._meaning(kind, what))

    def _palette_key(self, kind, what):
        """Which palette this part is currently drawn with."""
        if kind == "sprite":
            return self.states.get(what) or SPRITE_CLUTS.get(what) or self._font_key()
        return self._font_key()

    def _meaning(self, kind, what):
        """(code, character, note) for a glyph selection.

        The note is for a cell whose blank letter would otherwise read
        as "free". The button icons are the case that matters: they draw
        something the game very much still needs, and nothing about an
        empty letter field says so."""
        if kind not in ("glyph", "system") or what is None:
            return None, "", ""
        letters = self.table.letters() if self.table else {}
        char = letters.get(what, "")
        note = ""
        if kind == "glyph" and not char:
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

    def set_source(self, cd_folder):
        """Read the page and its palettes off the disc."""
        self.cd_folder = cd_folder
        self.history = []
        self.stroke = []
        self.original = None
        self.canvas.glyph_top = self.glyph_top
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
                self.page = fontpage.read_page(cd_folder)
                # Kept as the disc has it, so one glyph can be put back
                # without re-reading and losing the rest of the work.
                self.original = [row[:] for row in self.page]
                self.cluts = fontpage.read_cluts(cd_folder)
            except Exception as e:
                self.info.setText(f"Could not read the page: {e}")
        wanted = FONT_CLUT
        at = 0
        for i, (row, slot, _pal) in enumerate(self.cluts):
            note = ""
            for name, where in CLUTS.items():
                if where == (row, slot):
                    note = f"  - {name}"
            if (row, slot) == wanted:
                at = i
            self.clut_box.addItem(f"row {row} slot {slot}{note}", i)
        self.clut_box.setCurrentIndex(at)
        self.clut_box.blockSignals(False)
        if self.page:
            self.info.setText(
                f"{fontpage.PAGE_W}x{fontpage.PAGE_H} page, "
                f"{len(self.cluts)} palettes")
        self._redraw()

    def what_is_at(self, x, y):
        """What the page holds at this texel: a sprite, a glyph, or the
        palette rows. Named rather than numbered where the page has a
        name for it."""
        for name, top, bottom, left, right in SPRITES:
            if top <= y < bottom and left <= x < right:
                return ("sprite", name)
        if y >= fontpage.CLUT_TOP:
            row = y
            return ("palette", (row, x // (fontpage.PAGE_W // 4 * 4 // 16)))
        if y < self.glyph_top:
            # The system font is the dialogue grid at half height.
            code = (y // fontpage.SYSTEM_H) * fontpage.GLYPH_COLS + x // fontpage.GLYPH_W
            return ("system", code)
        if y < 168:
            row = (y - self.glyph_top) // fontpage.GLYPH_H
            code = row * fontpage.GLYPH_COLS + x // fontpage.GLYPH_W
            return ("glyph", first_of(code))
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
                                       describe(kind, what),
                                       *self._meaning(kind, what))
        self.selected.emit(kind, what)
        self.info.setText(f"({x}, {y})  {describe(kind, what)}")

    def _box_for(self, kind, what):
        """The rectangle to outline for a selection, in page texels."""
        if kind == "sprite":
            for name, top, bottom, left, right in SPRITES:
                if name == what:
                    return (left, top, right - left, bottom - top)
        if kind == "glyph" and what is not None:
            row, col = divmod(what, fontpage.GLYPH_COLS)
            return (col * fontpage.GLYPH_W,
                    self.glyph_top + row * fontpage.GLYPH_H,
                    fontpage.GLYPH_W * glyph_cells(what), fontpage.GLYPH_H)
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
        key = (self.states.get(what) or SPRITE_CLUTS.get(what)
               if kind == "sprite" else None) or self._font_key()
        by_key = {(r, s): p for r, s, p in self.cluts}
        return box, by_key.get(key)

    def _font_key(self):
        which = self.clut_box.currentData()
        if which is None or which >= len(self.cluts):
            return FONT_CLUT
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
        stem = f"{what}" if kind == "sprite" else f"{kind}_{what:#04x}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export selection", f"{stem}.png".replace("/", "-"),
            "PNG (*.png)")
        if not path:
            return
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(0)
        for row in range(height):
            for col in range(width):
                entry = palette[self.page[y + row][x + col]] if palette else None
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
            self, "Import into selection", "", "Images (*.png *.bmp)")
        if not path:
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
            fontpage.write_page(self.cd_folder, self.page)
            if self.table is not None:
                translation.save(self.cd_folder, self.table)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.set_source(self.cd_folder)
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
            which is not None and which < len(self.cluts)) else FONT_CLUT
        image = _render(self.page, self.cluts, font_clut, self.states)
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


def glyph_cells(code):
    """How many 8-pixel cells this code is drawn across - see
    DOUBLE_FIRST."""
    return 2 if DOUBLE_FIRST <= code <= DOUBLE_LAST else 1


def first_of(code):
    """The code a click belongs to, given the double-width run.

    Landing on the right half of a two-cell glyph names the glyph, not
    the cell, so a selection is always a whole character."""
    if DOUBLE_FIRST <= code <= DOUBLE_LAST:
        return DOUBLE_FIRST + ((code - DOUBLE_FIRST) // 2) * 2
    return code


def describe(kind, what):
    """What a selection is, in words."""
    if kind == "sprite":
        return f"sprite: {what}"
    if kind in ("glyph", "system") and what is not None:
        wide = " (double)" if kind == "glyph" and glyph_cells(what) > 1 else ""
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


def _render(page, cluts, font_clut=FONT_CLUT, states=None):
    """The page as an image, each region under the palette it is really
    drawn with."""
    states = states or {}
    font_pal, by_key = _palette_map(cluts, font_clut, states)
    width, height = fontpage.PAGE_W, fontpage.PAGE_H
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0)          # transparent; the canvas paints the ground

    # Which palette covers each artwork box.
    boxes = []
    for name, top, bottom, left, right in SPRITES:
        key = states.get(name, SPRITE_CLUTS.get(name, font_clut))
        boxes.append((top, bottom, left, right, by_key.get(key)))

    for y in range(min(height, len(page))):
        line = page[y]
        for x in range(min(width, len(line))):
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

    def __init__(self, parent=None):
        super().__init__(zoom=3, parent=parent)
        self.show_regions = True
        self.show_cells = True
        self.glyph_top = fontpage.GLYPH_TOP
        self.selection = None      # (x, y, w, h) in page texels

    def paint_overlays(self, painter, _area):
        scale = self.scaled
        # What can be picked, drawn faintly. Without it the page is a
        # wall of glyphs with no sign that any of it is clickable, and
        # nothing says where one sprite stops and the next starts.
        if self.show_cells and self.zoom >= 2:
            painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
            for y in range(0, self.glyph_top, fontpage.SYSTEM_H):
                for x in range(0, fontpage.PAGE_W, fontpage.GLYPH_W):
                    painter.drawRect(scale(x), scale(y),
                                     scale(fontpage.GLYPH_W),
                                     scale(fontpage.SYSTEM_H))
            for row in range((168 - self.glyph_top) // fontpage.GLYPH_H):
                y = self.glyph_top + row * fontpage.GLYPH_H
                col = 0
                while col < fontpage.GLYPH_COLS:
                    code = row * fontpage.GLYPH_COLS + col
                    cells = glyph_cells(code)
                    painter.drawRect(scale(col * fontpage.GLYPH_W), scale(y),
                                     scale(fontpage.GLYPH_W * cells),
                                     scale(fontpage.GLYPH_H))
                    col += cells
            painter.setPen(QPen(QColor(240, 170, 60, 150), 1))
            for _name, top, bottom, left, right in SPRITES:
                painter.drawRect(scale(left), scale(top),
                                 scale(right - left), scale(bottom - top))
        if self.selection:
            x, y, w, h = self.selection
            painter.setPen(QPen(QColor(90, 200, 255), 2))
            painter.drawRect(scale(x) - 1, scale(y) - 1,
                             scale(w) + 2, scale(h) + 2)
        if not self.show_regions:
            return
        painter.setFont(painter.font())
        for name, top, bottom, colour in regions(self.glyph_top):
            painter.setPen(QPen(colour, 1, Qt.PenStyle.DashLine))
            y = scale(top)
            painter.drawLine(0, y, scale(fontpage.PAGE_W), y)
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
    renamed = pyqtSignal(object, str)      # code, the character it draws

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = None
        self.box = None                    # (x, y, w, h) in page texels
        self.palette = None
        self.code = None                   # the glyph code, when it is one
        self.cluts = []
        self.index = 1                     # what the brush paints

        self.canvas = _DetailCanvas()
        self.canvas.checker_light = CHECKER_LIGHT
        self.canvas.checker_dark = CHECKER_DARK
        self.canvas.painted.connect(self._paint_at)
        self.canvas.stroke_ended.connect(self.stroke_ended)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.canvas)

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
        self.char_edit.setMaxLength(4)
        self.char_edit.setFixedWidth(60)
        self.char_edit.setToolTip(
            "The character this code draws. Type a new one to reassign "
            "it - a Polish build wanting 'a with ogonek' takes a code "
            "the disc spends on a symbol it never prints. Empty releases "
            "the code.")
        self.char_edit.editingFinished.connect(self._rename)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #c8a04a;")

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
        head.addWidget(self.title, 1)
        head.addWidget(QLabel("Palette:"))
        head.addWidget(self.clut_box, 1)

        # The assigned letter sits with the drawing tools rather than
        # up by the title: shape and meaning are the two halves of the
        # same edit, and this is the order they are done in - draw the
        # glyph, then say what it is.
        name_row = QHBoxLayout()
        name_row.setContentsMargins(8, 0, 8, 0)
        name_row.addWidget(QLabel("Assigned letter:"))
        name_row.addWidget(self.char_edit)
        name_row.addStretch(1)
        name_row.addWidget(self.copy_button)
        name_row.addWidget(self.paste_button)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(8, 0, 8, 6)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(head)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(name_row)
        layout.addWidget(self.note)
        layout.addWidget(self.swatches)
        layout.addLayout(buttons)

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
                       note=""):
        self.page, self.box, self.palette = page, box, palette
        self.code = code
        self.title.setText(title)
        self.note.setText(note)
        self.swatches.set_palette(palette)
        self.char_edit.blockSignals(True)
        self.char_edit.setText(char)
        self.char_edit.setEnabled(code is not None)
        self.char_edit.blockSignals(False)
        self._redraw()

    def _rename(self):
        """Reassign what the selected code draws."""
        if self.code is not None:
            self.renamed.emit(self.code, self.char_edit.text())

    def _set_index(self, index):
        self.index = index

    def _paint_at(self, col, row, erasing=False):
        """Put a palette index into one texel of the page."""
        if not self.page or not self.box:
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
        for row in range(height):
            for col in range(width):
                entry = (self.palette[self.page[y + row][x + col]]
                         if self.palette else None)
                if entry and entry[3]:
                    image.setPixelColor(col, row, QColor(*entry))
        self.canvas.set_image(image)
        self.canvas.set_zoom(fit_zoom((width, height),
                                      self.scroll.viewport().size(), 24))


class _DetailCanvas(PixelCanvas):
    """The zoomed selection. Dragging paints; a grid keeps the texels
    countable, which is the whole point of being zoomed in."""

    painted = pyqtSignal(int, int, bool)     # col, row, erasing
    stroke_ended = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(zoom=12, parent=parent)

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
        """Left paints the chosen index, right paints 0.

        Index 0 is the page's "draw nothing", so the right button is
        the eraser without needing to reach for a swatch - which is
        what most of hand-editing a glyph actually is."""
        if self.image is None or self.zoom <= 0:
            return
        buttons = event.buttons() or event.button()
        erasing = bool(buttons & Qt.MouseButton.RightButton)
        pos = event.position() if hasattr(event, "position") else event.pos()
        self.painted.emit(int(pos.x() // self.zoom),
                          int(pos.y() // self.zoom), erasing)

    def paint_overlays(self, painter, _area):
        if self.image is None or self.zoom < 6:
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
                        "page's way of drawing nothing - so it erases.")

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

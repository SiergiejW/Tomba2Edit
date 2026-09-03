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
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
    QWidget,
)

from functions import fontpage
from gui.pixel_canvas import PixelCanvas, fit_zoom

# What lives where, in page rows. The names are the ones the module
# docstring of functions/fontpage.py uses.
REGIONS = (
    ("system font 8x8", fontpage.SYSTEM_TOP, fontpage.GLYPH_TOP,
     QColor(90, 170, 255, 190)),
    ("dialogue font 8x16", fontpage.GLYPH_TOP, 168,
     QColor(120, 220, 120, 190)),
    ("menu artwork", 168, fontpage.CLUT_TOP,
     QColor(240, 170, 60, 190)),
    ("palettes", fontpage.CLUT_TOP, fontpage.PAGE_H,
     QColor(230, 110, 110, 190)),
)

# The palette each part of the page is drawn with in the game, matched
# against the captures in "ingame examples" by nearest colour.
#
# The dialogue captures agree strongly - the NPC box, Zippo and Tomba's
# box, the controls menu and the intro text all land on row 240 slot 3,
# the white-through-grey ramp, within a mean colour error of 19 to 36.
# "Full!!" lands on 241/2 at 25.
#
# The menu words are less certain and are offered rather than asserted:
# Status, Options and Event only match to about 95, because those
# captures are upscaled with the menu's own background bleeding through
# them. The chooser lists every palette in the page for that reason.
CLUTS = {
    "dialogue and system font": (240, 3),
    "Full!!": (241, 2),
}


class FontPageView(QWidget):
    """The page at a chosen palette, with its regions marked."""

    picked = pyqtSignal(int, int)          # page x, y

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = None
        self.cluts = []

        self.canvas = _PageCanvas()
        self.canvas.clicked.connect(self.picked)

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

        self.info = QLabel("No disc open.")

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.addWidget(QLabel("Palette:"))
        bar.addWidget(self.clut_box, 1)
        bar.addWidget(self.regions_check)
        bar.addWidget(self.info, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self.scroll, 1)

    def set_source(self, cd_folder):
        """Read the page and its palettes off the disc."""
        self.page = None
        self.cluts = []
        self.clut_box.blockSignals(True)
        self.clut_box.clear()
        if cd_folder:
            try:
                self.page = fontpage.read_page(cd_folder)
                self.cluts = fontpage.read_cluts(cd_folder)
            except Exception as e:
                self.info.setText(f"Could not read the page: {e}")
        wanted = CLUTS["dialogue and system font"]
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

    def _toggle_regions(self, on):
        self.canvas.show_regions = on
        self.canvas.update()

    def _redraw(self):
        if not self.page:
            self.canvas.clear()
            return
        which = self.clut_box.currentData()
        palette = (self.cluts[which][2] if which is not None
                   and which < len(self.cluts) else None)
        image = _render(self.page, palette)
        self.canvas.set_image(image)
        self.canvas.set_zoom(fit_zoom((image.width(), image.height()),
                                      self.scroll.viewport().size(), 4))


def _render(page, palette):
    """The page as an image, every index looked up in `palette`."""
    width, height = fontpage.PAGE_W, fontpage.PAGE_H
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0)
    for y in range(min(height, len(page))):
        line = page[y]
        for x in range(min(width, len(line))):
            entry = palette[line[x]] if palette else None
            if entry is None:
                value = line[x] * 17
                image.setPixelColor(x, y, QColor(value, value, value, 255))
            elif entry[3]:
                image.setPixelColor(x, y, QColor(*entry))
    return image


class _PageCanvas(PixelCanvas):
    """The page, with the region bands drawn over it."""

    def __init__(self, parent=None):
        super().__init__(zoom=3, parent=parent)
        self.show_regions = True

    def paint_overlays(self, painter, _area):
        if not self.show_regions:
            return
        scale = self.scaled
        painter.setFont(painter.font())
        for name, top, bottom, colour in REGIONS:
            painter.setPen(QPen(colour, 1, Qt.PenStyle.DashLine))
            y = scale(top)
            painter.drawLine(0, y, scale(fontpage.PAGE_W), y)
            painter.setPen(QPen(colour, 1))
            painter.drawText(4, y + 12, f"{name}  (rows {top}-{bottom - 1})")

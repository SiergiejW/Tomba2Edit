"""
App-wide visual themes, switchable from Settings > Theme.

- "modern": charcoal panels drawn as rounded cards, one accent colour, pill
  tabs, tree lines, thin scrollbars, and class-coloured dots for the level
  editor's rows (gui/widgets/dot_delegate.py). Default.
- "modern_bright": the same, light.
- "dark": the platform's default palette, completely unmodified - what
  this tool looked like before any theme system existed.
- "bright": the same native style, with an explicit light palette swapped
  in, otherwise identical to "dark".

The two modern themes take every colour that isn't grey from ACCENT below:
change it and the tabs, focus rings, selections, ticks and underlines all
follow.

Windows' native style renders popup menus (QMenu) via its own OS dark-mode
setting rather than Qt's QPalette, so on a system running in Windows dark
mode those popups stay dark even under "bright". A tiny color-only QSS rule
for QMenu forces it to follow the palette instead.

The 3D views' toolbars and overlay labels, and the panel captions, are
styled here by object name rather than inline, so switching theme restyles
them live. Glyphs the stylesheet needs (chevrons, ticks, tree lines,
splitter grips) are drawn into PNGs when a modern theme is applied - QSS
can't draw them, and SVG would need the Qt SVG module the build leaves out.
"""

# ---------------------------------------------------------------------------
# The modern themes' one colour. Any "#rrggbb": selected tab, focus ring,
# selection, ticks, the underline on a button that is on.
ACCENT = "#DB5986"
# ---------------------------------------------------------------------------

import os
import tempfile

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QApplication

THEMES = ("modern", "modern_bright", "modern_pink", "dark", "bright")
DEFAULT_THEME = "dark"
LABELS = {"dark": "Classic Dark (default)", "bright": "Classic Bright",
          "modern": "Modern Dark", "modern_bright": "Modern Bright",
          "modern_pink": "Strawberry Custard"}

_native_palette = None
_current_theme = DEFAULT_THEME

# Object names the stylesheets below key on.
VIEWER_TOOLBAR = "viewerToolbar"
VIEWER_OVERLAY = "viewerOverlay"
PANEL_TITLE = "panelTitle"
# A widget property naming the formats/audio/transport_icons.py glyph it shows, so a
# theme switch can repaint it in the new colours.
GLYPH_PROPERTY = "themeGlyph"
# The object name the script editors carry - MAIN.EXE, SOP, TXT2,
# TXTD - so the modern stylesheet can hand them back the fixed
# width font it would otherwise take off them. See SCRIPT_FONT.
SCRIPT_EDITOR = "scriptEditor"
# What those editors are set to in code, and what the classic
# themes therefore show. Kept here so the two agree.
SCRIPT_FONT = '"Courier New", Consolas, monospace'

# Applied in the two classic themes - tightens the native style's unusually
# wide gap between top-level menu bar entries, and gives them a visible
# hover/press background. The viewer toolbar/overlay/caption rules are what
# those widgets used to set inline, unchanged.
_BASE_QSS = """
QMenuBar::item {
    background-color: transparent;
    padding: 4px 6px;
    margin: 0px;
}
QMenuBar::item:selected, QMenuBar::item:pressed {
    background-color: palette(highlight);
    color: palette(highlighted-text);
}
QToolBar#viewerToolbar QToolButton {
    background-color: rgba(255, 255, 255, 128);
    color: black;
    border: none;
    padding: 5px;
    margin: 2px;
    border-radius: 4px;
}
QToolBar#viewerToolbar QToolButton:hover {
    background-color: rgba(255, 255, 255, 180);
}
QLabel#viewerOverlay {
    background-color: rgba(0, 0, 0, 128);
    color: white;
    padding: 4px 6px;
    border-radius: 4px;
    font-family: Consolas, monospace;
    font-size: 11px;
}
QLabel#panelTitle {
    color: #b8b8b8;
    font-size: 11px;
    padding: 2px 4px;
}
"""

# Windows' native style draws QMenu popups and item-view selection off the
# OS's own light/dark mode rather than QPalette; these colour-only overrides
# make "bright" follow its palette.
_BRIGHT_MENU_QSS = _BASE_QSS + """
QMenu {
    background-color: #ffffff;
    color: #202020;
}
QMenu::item:selected {
    background-color: #3399ff;
    color: #ffffff;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background-color: #3399ff;
    color: #ffffff;
}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {
    background-color: #f0f0f2;
}
"""

# --- modern ---------------------------------------------------------------

# The greys of each modern theme, by what they are for.
_GREYS = {
    "modern": {
        "ground": "#111111",       # the window behind everything
        "panel": "#161616",        # a pane: tree, list, editor
        "card": "#1c1c1c",         # a raised block inside a pane
        "raised": "#232323",       # a control's own face
        "hover": "#2a2a2a",
        "line": "#2a2a2a",         # borders
        "line_strong": "#3c3c3c",  # borders that must be seen: splitters
        "text": "#e8e8e8",
        "dim": "#8c8c8c",          # captions
        "faint": "#5a5a5a",        # disabled
    },
    "modern_bright": {
        "ground": "#f3f3f4",
        "panel": "#ffffff",
        "card": "#fafafa",
        "raised": "#ffffff",
        "hover": "#ebebed",
        "line": "#e0e0e3",
        "line_strong": "#c4c4ca",
        "text": "#1c1c1e",
        "dim": "#6c6c72",
        "faint": "#a8a8ae",
    },
    # Pink, with the custard yellow turning up wherever a second
    # colour is wanted: alternating rows, the played part of a seek
    # bar, a waveform. The neutrals are not grey - every one of them
    # leans a few points warm, which is what stops the pink reading as
    # a tint laid over a grey theme.
    "modern_pink": {
        "ground": "#fcdce9",        # a pink you can see, not an off-white
        "panel": "#fff4f8",
        "card": "#ffeec2",          # the custard
        "raised": "#fffafc",
        "hover": "#fbcede",
        "line": "#f3b9d1",
        "line_strong": "#e07ba7",
        "text": "#3f1d2b",
        "dim": "#8a4a63",
        "faint": "#c986a2",
    },
}

# A theme may have an accent of its own; the rest take ACCENT above.
_ACCENTS = {"modern_pink": "#e2447d"}
# The second colour, where a theme has one worth using - see
# colours()["accent2"]. Falls back to the accent itself, so anything
# painting with it works under every theme without asking which.
_ACCENTS_2 = {"modern_pink": "#f5b31a"}

# A modern 3D view's ground, per theme.
_VIEW_GROUND = {"modern": (0.0, 0.0, 0.0), "modern_bright": (0.9, 0.9, 0.92),
                "modern_pink": (0.94, 0.9, 0.92)}

# Which modern themes put dark text on a light ground. Asked by name
# rather than by measuring, because it also decides which way round the
# shading goes in things this module does not paint.
_BRIGHT_MODERN = {"modern": False, "modern_bright": True,
                  "modern_pink": True}


def _mix(a, b, t):
    """`a` moved `t` of the way to `b`, as #rrggbb."""
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t)).name()


def colours(name=None):
    """{role: "#rrggbb"} for a modern theme - its greys and ACCENT, with what
    ACCENT implies: a hover shade, a selection tint, and text that reads on
    it. The current theme's by default; "modern"'s under a classic one."""
    name = name or _current_theme
    if name not in _GREYS:
        name = "modern"
    c = dict(_GREYS[name])
    wanted = _ACCENTS.get(name, ACCENT)
    accent = QColor(wanted) if QColor(wanted).isValid() else QColor("#f28c28")
    bright = _BRIGHT_MODERN.get(name, False)
    c["accent"] = accent.name()
    c["accent_hover"] = _mix(c["accent"], "#000000" if bright else "#ffffff", 0.15)
    # A selected row: the accent, faint, over the pane.
    c["accent_dim"] = _mix(c["panel"], c["accent"], 0.18 if bright else 0.22)
    # A row under the mouse. Deliberately far weaker than accent_dim:
    # at the same strength the two are indistinguishable at a glance
    # and a list looks like it has two rows selected.
    c["row_hover"] = _mix(c["panel"], c["text"], 0.06)
    second = _ACCENTS_2.get(name)
    c["accent2"] = QColor(second).name() if second and QColor(second).isValid() \
        else c["accent"]
    luminance = (0.299 * accent.red() + 0.587 * accent.green() + 0.114 * accent.blue()) / 255
    c["accent_text"] = "#141414" if luminance > 0.55 else "#ffffff"
    return c


def _glyphs(name, c):
    """{glyph: path} of the PNGs the stylesheet needs, drawn in `c`'s colours."""
    folder = os.path.join(tempfile.gettempdir(), "tomba2edit-theme", name)
    os.makedirs(folder, exist_ok=True)
    paths = {}

    def image(glyph, width, height, draw):
        path = os.path.join(folder, f"{glyph}.png")
        picture = QImage(width, height, QImage.Format.Format_ARGB32)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw(painter)
        painter.end()
        picture.save(path)
        paths[glyph] = path.replace("\\", "/")

    def stroke(colour, width=2.6):
        pen = QPen(QColor(colour), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def polyline(points, colour, width=2.6):
        def draw(painter):
            painter.setPen(stroke(colour, width))
            painter.drawPolyline([QPointF(x, y) for x, y in points])
        return draw

    # Chevrons and a tick, drawn at 2x and shown at 7-9 px.
    for suffix, colour in (("", c["dim"]), ("_hot", c["text"])):
        image("down" + suffix, 18, 18, polyline(((3, 6), (9, 12), (15, 6)), colour))
        image("up" + suffix, 18, 18, polyline(((3, 12), (9, 6), (15, 12)), colour))
    image("tick", 18, 18, polyline(((3.5, 9.5), (7.5, 13.5), (14.5, 5)), c["accent_text"]))

    # Tree lines. Stretched over the branch cell as border-images, so they
    # are drawn on a 20x20 grid with the line through the middle; a 1px
    # line stays 1px because the cell is about that size.
    line = QColor(c["line_strong"])
    size, mid = 20, 10

    def lines(*segments):
        def draw(painter):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(line, 1))
            for x1, y1, x2, y2 in segments:
                painter.drawLine(x1, y1, x2, y2)
        return draw

    image("vline", size, size, lines((mid, 0, mid, size)))
    image("branch_more", size, size, lines((mid, 0, mid, size), (mid, mid, size, mid)))
    image("branch_end", size, size, lines((mid, 0, mid, mid), (mid, mid, size, mid)))

    # An expandable row's arrow, on a disc of the pane's colour so a line
    # running behind it doesn't cross it.
    def arrow(points, with_line):
        def draw(painter):
            if with_line:
                lines(*with_line)(painter)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["panel"]))
            painter.drawEllipse(QRectF(mid - 6, mid - 6, 12, 12))
            painter.setPen(stroke(c["dim"], 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline([QPointF(x, y) for x, y in points])
        return draw

    closed = ((mid - 2, mid - 4), (mid + 2, mid), (mid - 2, mid + 4))
    opened = ((mid - 4, mid - 2), (mid, mid + 2), (mid + 4, mid - 2))
    image("closed", size, size, arrow(closed, ()))
    image("open", size, size, arrow(opened, ()))
    image("closed_more", size, size, arrow(closed, ((mid, 0, mid, size),)))
    image("open_more", size, size, arrow(opened, ((mid, 0, mid, size),)))
    image("closed_end", size, size, arrow(closed, ((mid, 0, mid, mid),)))
    image("open_end", size, size, arrow(opened, ((mid, 0, mid, mid),)))

    # Splitter grips: three dots along the handle.
    # On the handle's own grey, so in the page's colour to stand out.
    image("grip_vertical_bar", 4, 32, _grip(c["ground"], vertical=True))
    image("grip_horizontal_bar", 32, 4, _grip(c["ground"], vertical=False))
    return paths


def _grip(colour, vertical):
    """Three dots down (vertical) or across a splitter handle."""
    def draw(painter):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        for n in range(3):
            centre = 6 + n * 10
            x, y = (2, centre) if vertical else (centre, 2)
            painter.drawEllipse(QRectF(x - 1.6, y - 1.6, 3.2, 3.2))
    return draw


def _modern_qss(name):
    c = colours(name)
    g = _glyphs(name, c)
    return f"""
* {{
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 12px;
    color: {c["text"]};
    outline: 0;
}}
QMainWindow, QDialog {{
    background-color: {c["ground"]};
}}
QWidget {{
    background-color: {c["ground"]};
    selection-background-color: {c["accent_dim"]};
    selection-color: {c["text"]};
}}
QToolTip {{
    background-color: {c["raised"]};
    color: {c["text"]};
    border: 1px solid {c["line_strong"]};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* menus */
QMenuBar {{
    background-color: {c["ground"]};
    border-bottom: 1px solid {c["line"]};
    padding: 2px 6px;
}}
QMenuBar::item {{
    background: transparent;
    color: {c["dim"]};
    padding: 5px 10px;
    margin: 2px 1px;
    border-radius: 12px;
}}
QMenuBar::item:selected, QMenuBar::item:pressed {{
    background-color: {c["hover"]};
    color: {c["text"]};
}}
QMenu {{
    background-color: {c["card"]};
    border: 1px solid {c["line_strong"]};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    background: transparent;
    padding: 6px 22px 6px 12px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: {c["hover"]};
}}
QMenu::item:disabled {{
    color: {c["faint"]};
}}
QMenu::separator {{
    height: 1px;
    background: {c["line"]};
    margin: 5px 8px;
}}
QMenu::indicator {{
    width: 12px;
    height: 12px;
    margin-left: 4px;
}}
QMenu::indicator:checked {{
    background-color: {c["accent"]};
    border-radius: 6px;
}}

/* tabs: pills, the chosen one in the accent */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {c["line"]};
    background: {c["ground"]};
}}
QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    color: {c["dim"]};
    font-weight: 600;
    padding: 6px 14px;
    margin: 5px 2px;
    border-radius: 13px;
    min-width: 20px;
}}
QTabBar::tab:hover {{
    background-color: {c["hover"]};
    color: {c["text"]};
}}
QTabBar::tab:selected {{
    background-color: {c["accent"]};
    color: {c["accent_text"]};
}}
QTabBar::scroller {{
    width: 36px;
}}
QTabBar QToolButton {{
    background-color: {c["raised"]};
    border: 1px solid {c["line"]};
    border-radius: 6px;
}}

/* buttons */
QPushButton {{
    background-color: {c["raised"]};
    color: {c["text"]};
    border: 1px solid {c["line"]};
    border-radius: 6px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    background-color: {c["hover"]};
    border-color: {c["line_strong"]};
}}
QPushButton:pressed {{
    background-color: {c["card"]};
}}
QPushButton:checked {{
    background-color: {c["raised"]};
    border-bottom: 2px solid {c["accent"]};
}}
QPushButton:default {{
    border-color: {c["accent"]};
    color: {c["accent"]};
}}
QPushButton:disabled {{
    color: {c["faint"]};
    background-color: {c["card"]};
    border-color: {c["card"]};
}}
/* a player's glyph button (formats/audio/transport_icons.py): square, round */
QPushButton[themeGlyph] {{
    padding: 4px;
    border-radius: 15px;
    min-width: 22px;
    min-height: 22px;
}}
QPushButton[themeGlyph="play"], QPushButton[themeGlyph="pause"] {{
    background-color: {c["accent"]};
    border-color: {c["accent"]};
}}
QPushButton[themeGlyph="play"]:hover, QPushButton[themeGlyph="pause"]:hover {{
    background-color: {c["accent_hover"]};
    border-color: {c["accent_hover"]};
}}
QToolButton {{
    background-color: transparent;
    color: {c["text"]};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 6px;
}}
QToolButton:hover {{
    background-color: {c["hover"]};
    border-color: {c["line"]};
}}
QToolButton:checked {{
    background-color: {c["raised"]};
    border-color: {c["line"]};
    border-bottom: 2px solid {c["accent"]};
}}
QToolButton:disabled {{
    color: {c["faint"]};
}}
QToolBar {{
    background-color: {c["ground"]};
    border: none;
    border-bottom: 1px solid {c["line"]};
    spacing: 4px;
    padding: 3px;
}}
QToolBar::separator {{
    background: {c["line"]};
    width: 1px;
    margin: 4px 6px;
}}

/* fields */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {c["panel"]};
    color: {c["text"]};
    border: 1px solid {c["line"]};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {c["accent"]};
    selection-color: {c["accent_text"]};
}}
/* spin boxes sit in tight form rows: less padding, or the digits clip */
QAbstractSpinBox, QSpinBox, QDoubleSpinBox {{
    background-color: {c["panel"]};
    color: {c["text"]};
    border: 1px solid {c["line"]};
    border-radius: 6px;
    padding: 1px 4px 1px 8px;
    min-height: 20px;
    selection-background-color: {c["accent"]};
    selection-color: {c["accent_text"]};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {c["line_strong"]};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {c["accent"]};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled {{
    color: {c["faint"]};
    background-color: {c["ground"]};
}}
QComboBox {{
    padding-right: 22px;
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: url({g["down"]});
    width: 9px;
    height: 9px;
    margin-right: 8px;
}}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{
    image: url({g["down_hot"]});
}}
QComboBox QAbstractItemView {{
    background-color: {c["card"]};
    border: 1px solid {c["line_strong"]};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {c["hover"]};
    selection-color: {c["text"]};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    border: none;
    background: transparent;
    width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({g["up"]});
    width: 7px;
    height: 7px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({g["down"]});
    width: 7px;
    height: 7px;
}}
QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {{
    image: url({g["up_hot"]});
}}
QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {{
    image: url({g["down_hot"]});
}}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    image: none;
}}
QCheckBox, QRadioButton {{
    background: transparent;
    spacing: 7px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {c["line_strong"]};
    background-color: {c["panel"]};
}}
QCheckBox::indicator {{
    border-radius: 4px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {c["accent"]};
    border-color: {c["accent"]};
}}
QCheckBox::indicator:checked {{
    image: url({g["tick"]});
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {c["line"]};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {c["accent"]};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {c["text"]};
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}}
QProgressBar {{
    background-color: {c["panel"]};
    border: 1px solid {c["line"]};
    border-radius: 6px;
    text-align: center;
    color: {c["dim"]};
}}
QProgressBar::chunk {{
    background-color: {c["accent"]};
    border-radius: 5px;
}}

/* lists, trees, tables: a pane with rows that light up */
QTreeView, QListView, QTableView, QTreeWidget, QListWidget, QTableWidget {{
    background-color: {c["panel"]};
    alternate-background-color: {c["card"]};
    border: 1px solid {c["line"]};
    border-radius: 8px;
    padding: 3px;
    gridline-color: {c["line"]};
}}
QTreeView::item, QListView::item, QTableView::item {{
    padding: 3px 4px;
    /* Transparent rather than none: the selected row draws a border,
       and without the space already reserved the text jumps a pixel
       the moment a row is clicked. */
    border: 1px solid transparent;
    border-radius: 5px;
}}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{
    background-color: {c["row_hover"]};
}}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected,
QTreeView::item:selected:hover, QListView::item:selected:hover,
QTableView::item:selected:hover {{
    background-color: {c["accent_dim"]};
    border-color: {c["accent"]};
    color: {c["text"]};
}}

/* tree structure lines */
QTreeView::branch {{
    background: transparent;
}}
QTreeView::branch:has-siblings:!adjoins-item {{
    border-image: url({g["vline"]}) 0;
}}
QTreeView::branch:has-siblings:adjoins-item {{
    border-image: url({g["branch_more"]}) 0;
}}
QTreeView::branch:!has-children:!has-siblings:adjoins-item {{
    border-image: url({g["branch_end"]}) 0;
}}
QTreeView::branch:has-children:!has-siblings:closed {{
    border-image: none;
    image: url({g["closed_end"]});
}}
QTreeView::branch:has-children:has-siblings:closed {{
    border-image: none;
    image: url({g["closed_more"]});
}}
QTreeView::branch:has-children:!has-siblings:open {{
    border-image: none;
    image: url({g["open_end"]});
}}
QTreeView::branch:has-children:has-siblings:open {{
    border-image: none;
    image: url({g["open_more"]});
}}

QHeaderView {{
    background-color: {c["panel"]};
    border: none;
}}
QHeaderView::section {{
    background-color: {c["panel"]};
    color: {c["dim"]};
    font-size: 11px;
    font-weight: 700;
    border: none;
    border-bottom: 1px solid {c["line"]};
    border-right: 1px solid {c["line"]};
    padding: 5px 8px;
}}
QHeaderView::section:last {{
    border-right: none;
}}
QTableCornerButton::section {{
    background-color: {c["panel"]};
    border: none;
}}

/* group boxes: a card with a small caption */
QGroupBox {{
    background-color: {c["card"]};
    border: 1px solid {c["line"]};
    border-radius: 8px;
    margin-top: 16px;
    padding: 10px 8px 8px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 4px;
    padding: 0 4px;
    color: {c["dim"]};
    font-size: 11px;
    font-weight: 700;
    background: transparent;
}}
QGroupBox QWidget {{
    background-color: transparent;
}}
QGroupBox QLineEdit, QGroupBox QAbstractSpinBox, QGroupBox QSpinBox,
QGroupBox QDoubleSpinBox, QGroupBox QComboBox, QGroupBox QPlainTextEdit,
QGroupBox QTextEdit {{
    background-color: {c["panel"]};
}}
QGroupBox QPushButton {{
    background-color: {c["raised"]};
}}

QScrollArea {{
    border: none;
    background: transparent;
}}
QLabel {{
    background: transparent;
}}
QStatusBar {{
    background-color: {c["ground"]};
    color: {c["dim"]};
    border-top: 1px solid {c["line"]};
}}
QStatusBar QLabel {{
    color: {c["dim"]};
    padding: 0 6px;
}}
QStatusBar::item {{
    border: none;
}}

/* splitters: a visible bar with a grip, lit when grabbed */
QSplitter::handle {{
    background-color: {c["line_strong"]};
    border-radius: 4px;
    margin: 1px;
}}
QSplitter::handle:horizontal {{
    width: 8px;
    image: url({g["grip_vertical_bar"]});
}}
QSplitter::handle:vertical {{
    height: 8px;
    image: url({g["grip_horizontal_bar"]});
}}
QSplitter::handle:hover {{
    background-color: {c["dim"]};
}}
QSplitter::handle:pressed {{
    background-color: {c["accent"]};
}}

/* scrollbars: thin, no arrows */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {c["line_strong"]};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:horizontal {{
    background: {c["line_strong"]};
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{
    background: {c["dim"]};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* the 3D views' own chrome */
QToolBar#viewerToolbar {{
    background-color: {c["ground"]};
    border-bottom: 1px solid {c["line"]};
    padding: 4px;
}}
QToolBar#viewerToolbar QToolButton {{
    background-color: transparent;
    color: {c["faint"]};
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    border-radius: 7px;
    padding: 4px 8px;
    margin: 1px 1px;
    font-size: 11px;
}}
QToolBar#viewerToolbar QToolButton:hover {{
    background-color: {c["hover"]};
    color: {c["text"]};
}}
/* on: lifted, with an accent underline - the accent kept to a stroke */
QToolBar#viewerToolbar QToolButton:checked {{
    background-color: {c["raised"]};
    color: {c["text"]};
    border: 1px solid {c["line"]};
    border-bottom: 2px solid {c["accent"]};
}}
QToolBar#viewerToolbar QToolButton:checked:hover {{
    background-color: {c["hover"]};
}}
QLabel#viewerOverlay {{
    background-color: rgba(17, 17, 17, 200);
    color: #b0b0b0;
    border: 1px solid rgba(255, 255, 255, 30);
    padding: 6px 8px;
    border-radius: 8px;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 11px;
}}
QLabel#panelTitle {{
    color: {c["dim"]};
    font-size: 11px;
    font-weight: 600;
    padding: 6px 4px 3px 4px;
}}

/* The script editors - MAIN.EXE, SOP, TXT2, TXTD.

   They are set to Courier New 12pt bold in code, which is what the
   classic themes show. The universal font rule at the top of this
   sheet is a stylesheet, and a stylesheet beats setFont(), so under a
   modern theme they silently lost it and came out in the UI's own
   proportional face. That is the wrong font for the job: these boxes
   hold text on a fixed byte budget, where a space and two spaces have
   to be told apart and columns have to line up. Put it back. */
QTextEdit#{SCRIPT_EDITOR}, QPlainTextEdit#{SCRIPT_EDITOR} {{
    font-family: {SCRIPT_FONT};
    font-size: 12pt;
    font-weight: bold;
}}
"""


def _bright_palette():
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#202020"))
    p.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#f0f0f0"))
    p.setColor(QPalette.ColorRole.Text, QColor("#202020"))
    p.setColor(QPalette.ColorRole.Button, QColor("#f0f0f0"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#202020"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#202020"))
    p.setColor(QPalette.ColorRole.Highlight, QColor("#3399ff"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return p


def _modern_palette(name):
    """Under the stylesheet, for whatever draws off the palette directly -
    custom-painted widgets, native dialogs."""
    c = colours(name)
    p = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "ground"),
                      (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "panel"),
                      (QPalette.ColorRole.AlternateBase, "card"),
                      (QPalette.ColorRole.Text, "text"),
                      (QPalette.ColorRole.Button, "raised"),
                      (QPalette.ColorRole.ButtonText, "text"),
                      (QPalette.ColorRole.ToolTipBase, "raised"),
                      (QPalette.ColorRole.ToolTipText, "text"),
                      (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "accent_text"),
                      (QPalette.ColorRole.PlaceholderText, "faint"),
                      (QPalette.ColorRole.Link, "accent"),
                      (QPalette.ColorRole.Mid, "line"),
                      (QPalette.ColorRole.Dark, "ground"),
                      (QPalette.ColorRole.Light, "line_strong")):
        p.setColor(role, QColor(c[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["faint"]))
    return p


def current_theme():
    return _current_theme


def is_modern():
    return _current_theme in _GREYS


def is_bright():
    """Whether the theme in use puts dark text on a light ground."""
    return _current_theme == "bright" or _BRIGHT_MODERN.get(
        _current_theme, False)


def roll_colours():
    """{role: QColor-able string} for the piano roll.

    Its own palette rather than the stylesheet's, because it is painted
    by hand: a canvas, lanes, grid lines and a playhead, none of which
    a widget style has a word for. Everything is derived from the
    theme's ground and text, so the shading is the same distance from
    the background whichever way round the two are - which is what
    makes the roll light under a light theme instead of a dark hole in
    the middle of a bright window.

    colours() answers with modern greys under the classic themes, so
    the base is chosen here by brightness rather than by name."""
    bright = is_bright()
    # A modern theme paints the roll in its own colours; a classic one
    # borrows the modern theme of the same brightness.
    c = colours(_current_theme if _current_theme in _GREYS
                else ("modern_bright" if bright else "modern"))
    ground = _mix(c["panel"], c["text"], 0.03)
    return {
        "ground": ground,
        "ruler": _mix(ground, c["text"], 0.10),
        # Black-key lanes: away from the ground in whichever direction
        # the ground is not, so they read as shaded either way.
        "lane": _mix(ground, c["text"], 0.07),
        "octave": _mix(ground, c["text"], 0.22),
        "bar": _mix(ground, c["text"], 0.24),
        "beat": _mix(ground, c["text"], 0.09),
        "mark": c["dim"],
        "text": c["dim"],
        # The playhead is the one thing that must never be lost against
        # the notes, so it keeps a colour of its own rather than taking
        # the accent, which a channel colour can sit right next to.
        "playhead": "#b8791a" if bright else "#e8b84b",
    }


# A 3D view's ground under the classic bright theme; the classic dark theme
# keeps each view's own dark grey. A room is always drawn on black - it is a
# room in the dark in the game too.
BRIGHT_VIEW = (0.9, 0.9, 0.92)
ROOM_VIEW = (0.0, 0.0, 0.0)


def view_background(dark=(0.1, 0.1, 0.1)):
    """(r, g, b) to clear a 3D view to: `dark` unless the theme says else."""
    if _current_theme == "bright":
        return BRIGHT_VIEW
    return _VIEW_GROUND.get(_current_theme, dark)


def apply_theme(app: QApplication, name: str):
    """Switches the app's palette and stylesheet. Call once at startup with
    the saved/default theme, and again whenever the user picks a
    different one from Settings > Theme."""
    global _native_palette, _current_theme
    if _native_palette is None:
        _native_palette = QPalette(app.palette())

    _current_theme = name if name in THEMES else DEFAULT_THEME
    if is_modern():
        app.setPalette(_modern_palette(_current_theme))
        app.setStyleSheet(_modern_qss(_current_theme))
    elif _current_theme == "bright":
        app.setPalette(_bright_palette())
        app.setStyleSheet(_BRIGHT_MENU_QSS)
    else:
        app.setPalette(_native_palette)
        app.setStyleSheet(_BASE_QSS)
    # Glyph buttons are painted in the theme's colours; custom-painted
    # widgets (the level list's dots, the 3D views' grounds) read the theme
    # when they paint - so repaint both.
    from formats.audio import transport_icons
    for widget in app.allWidgets():
        glyph = widget.property(GLYPH_PROPERTY)
        if glyph:
            transport_icons.refresh(widget)
        widget.update()

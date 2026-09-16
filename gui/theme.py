"""
App-wide visual themes, switchable from Settings > Theme.

- "dark": the platform's default palette, completely unmodified -
  confirmed against git history to be exactly what this tool looked
  like before any theme system existed. Default.
- "bright": the same native style, with an explicit light palette
  swapped in - a plain light/white look, guaranteed regardless of the
  system's own dark-mode setting, but otherwise identical to "dark" in
  every way that isn't color.
- "modern": a full stylesheet of its own - charcoal panels drawn as
  rounded cards, one orange accent, pill tabs, small uppercase headers,
  thin scrollbars - and class-coloured dots for the level editor's rows
  (gui/dot_delegate.py). Unlike the other two it does change geometry.

Windows' native style renders popup menus (QMenu, used for the
File/Settings dropdowns) via its own OS dark-mode setting rather than
Qt's QPalette, so on a system running in Windows dark mode those popups
stay dark even under "bright". A tiny color-only QSS rule for QMenu
(no padding/border/geometry changes) forces it to follow the palette
instead.

The 3D views' toolbars and overlay labels, and the panel captions, are
styled here by object name rather than inline, so switching theme
restyles them live.
"""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

THEMES = ("dark", "bright", "modern")
DEFAULT_THEME = "dark"
LABELS = {"dark": "Dark (default)", "bright": "Bright", "modern": "Modern"}

_native_palette = None
_current_theme = DEFAULT_THEME

# Object names the stylesheets below key on.
VIEWER_TOOLBAR = "viewerToolbar"
VIEWER_OVERLAY = "viewerOverlay"
PANEL_TITLE = "panelTitle"

# Applied in the two native themes - tightens the native style's unusually
# wide gap between top-level menu bar entries ("File", "Settings"), and
# gives them a visible hover/press background (palette(highlight) tracks
# whichever theme's palette is active, so this one rule works for both).
# The default (non-hover) state is given the same explicit
# padding/background here too - leaving it to fall back on the native
# style's own box model while only :selected/:pressed are styled is what
# caused the item to change size between its resting and hovered state.
#
# The viewer toolbar/overlay/caption rules are what those widgets used to
# set inline, unchanged.
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

# Windows' native ("windows11") style renders QMenu popups and
# QTreeView/QListView row selection/hover via its own light/dark visual
# style overlay rather than QPalette - calibrated for whatever mode the
# OS itself is in. On a system running in Windows dark mode, that makes
# both render dark (QMenu) or with a nearly-invisible highlight
# (item selection) even under "bright". These color-only overrides force
# them to follow the palette instead.
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
    background-color: #dceeff;
}
"""

# --- modern ---------------------------------------------------------------

# The modern theme's colours, by what they are for.
MODERN = {
    "ground": "#111111",       # the window behind everything
    "panel": "#161616",        # a pane: tree, list, editor
    "card": "#1c1c1c",         # a raised block inside a pane
    "raised": "#232323",       # a control's own face
    "hover": "#2a2a2a",
    "line": "#2a2a2a",         # borders
    "line_strong": "#3a3a3a",
    "text": "#e8e8e8",
    "dim": "#8c8c8c",          # captions, disabled
    "faint": "#5a5a5a",
    "accent": "#f28c28",       # the one colour: selected tab, primary action
    "accent_hover": "#ff9d40",
    "accent_dim": "#3a2410",   # accent under something - a selected row
    "accent_text": "#141414",
}

# A modern 3D view's ground: near-black, just off the window's.
MODERN_VIEW = (0.055, 0.055, 0.06)


def _arrow_images(c=MODERN):
    """{name: path} of the little glyphs the stylesheet needs - chevrons and a
    tick - drawn into PNGs once. Qt's QSS can't draw a CSS border triangle,
    and an SVG would need the Qt SVG module the build leaves out."""
    import os
    import tempfile
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QImage, QPainter, QPen

    folder = os.path.join(tempfile.gettempdir(), "tomba2edit-theme")
    os.makedirs(folder, exist_ok=True)
    size = 18                       # drawn at 2x, shown at 7-9 px
    shapes = {"down": ((3, 6), (9, 12), (15, 6)),
              "up": ((3, 12), (9, 6), (15, 12)),
              "tick": ((3.5, 9.5), (7.5, 13.5), (14.5, 5))}
    colours = {"": c["dim"], "_hot": c["text"]}
    paths = {}
    for shape, points in shapes.items():
        for suffix, colour in colours.items():
            if shape == "tick" and suffix:
                continue
            name = shape + suffix
            path = os.path.join(folder, f"{name}.png")
            image = QImage(size, size, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(c["accent_text"] if shape == "tick" else colour), 2.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline([QPointF(x, y) for x, y in points])
            painter.end()
            image.save(path)
            paths[name] = path.replace("\\", "/")
    return paths


def _modern_qss(c=MODERN):
    arrows = _arrow_images(c)
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

/* tabs: pills, the chosen one orange */
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
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit,
QAbstractSpinBox {{
    background-color: {c["panel"]};
    color: {c["text"]};
    border: 1px solid {c["line"]};
    border-radius: 6px;
    padding: 4px 8px;
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
    image: url({arrows["down"]});
    width: 9px;
    height: 9px;
    margin-right: 8px;
}}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{
    image: url({arrows["down_hot"]});
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
    image: url({arrows["up"]});
    width: 7px;
    height: 7px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({arrows["down"]});
    width: 7px;
    height: 7px;
}}
QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {{
    image: url({arrows["up_hot"]});
}}
QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {{
    image: url({arrows["down_hot"]});
}}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    image: none;
}}
QCheckBox::indicator:checked {{
    image: url({arrows["tick"]});
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
    padding: 4px 4px;
    border: none;
    border-radius: 5px;
}}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{
    background-color: {c["hover"]};
}}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background-color: {c["accent_dim"]};
    color: {c["text"]};
}}
QTreeView::branch {{
    background: transparent;
}}
QHeaderView {{
    background-color: {c["panel"]};
    border: none;
}}
QHeaderView::section {{
    background-color: {c["panel"]};
    color: {c["dim"]};
    font-size: 10px;
    font-weight: 700;
    border: none;
    border-bottom: 1px solid {c["line"]};
    padding: 6px 8px;
}}
QTableCornerButton::section {{
    background-color: {c["panel"]};
    border: none;
}}

/* group boxes: a card with a small uppercase caption */
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
    font-size: 10px;
    font-weight: 700;
    background: transparent;
}}
QGroupBox QWidget {{
    background-color: transparent;
}}
QGroupBox QLineEdit, QGroupBox QSpinBox, QGroupBox QDoubleSpinBox,
QGroupBox QComboBox, QGroupBox QPlainTextEdit, QGroupBox QTextEdit {{
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

/* splitters: a hairline you can still grab */
QSplitter::handle {{
    background-color: {c["ground"]};
}}
QSplitter::handle:horizontal {{
    width: 5px;
}}
QSplitter::handle:vertical {{
    height: 5px;
}}
QSplitter::handle:hover {{
    background-color: {c["accent_dim"]};
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
/* on: lifted, with an orange underline - the accent kept to a stroke */
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
    color: {c["dim"]};
    border: 1px solid {c["line"]};
    padding: 6px 8px;
    border-radius: 8px;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 11px;
}}
QLabel#panelTitle {{
    color: {c["dim"]};
    font-size: 10px;
    font-weight: 700;
    padding: 6px 4px 3px 4px;
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


def _modern_palette(c=MODERN):
    """Under the stylesheet, for whatever draws off the palette directly -
    custom-painted widgets, native dialogs."""
    p = QPalette()
    for role, name in ((QPalette.ColorRole.Window, "ground"),
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
        p.setColor(role, QColor(c[name]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["faint"]))
    return p


def current_theme():
    return _current_theme


def is_modern():
    return _current_theme == "modern"


# A 3D view's ground under the bright theme; the dark theme keeps each
# view's own dark grey. A room is always drawn on black - it is a room in
# the dark in the game too.
BRIGHT_VIEW = (0.9, 0.9, 0.92)
ROOM_VIEW = (0.0, 0.0, 0.0)


def view_background(dark=(0.1, 0.1, 0.1)):
    """(r, g, b) to clear a 3D view to: `dark` unless the theme says else."""
    if _current_theme == "bright":
        return BRIGHT_VIEW
    if _current_theme == "modern":
        return MODERN_VIEW
    return dark


def apply_theme(app: QApplication, name: str):
    """Switches the app's palette and stylesheet. Call once at startup with
    the saved/default theme, and again whenever the user picks a
    different one from Settings > Theme."""
    global _native_palette, _current_theme
    if _native_palette is None:
        _native_palette = QPalette(app.palette())

    _current_theme = name if name in THEMES else DEFAULT_THEME
    if _current_theme == "modern":
        app.setPalette(_modern_palette())
        app.setStyleSheet(_modern_qss())
    elif _current_theme == "bright":
        app.setPalette(_bright_palette())
        app.setStyleSheet(_BRIGHT_MENU_QSS)
    else:
        app.setPalette(_native_palette)
        app.setStyleSheet(_BASE_QSS)
    # Custom-painted widgets (the level list's dots, the 3D views' grounds)
    # read the theme when they paint, so make them paint.
    for widget in app.allWidgets():
        widget.update()

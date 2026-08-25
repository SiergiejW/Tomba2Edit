"""
App-wide visual themes, switchable from Settings > Theme.

Both themes use the platform's own native widget style with NO layout
stylesheet at all - no custom button/padding/border rendering, ever.
The only difference between them is the color palette:

- "dark": the platform's default palette, completely unmodified -
  confirmed against git history to be exactly what this tool looked
  like before any theme system existed. Default.
- "bright": the same native style, with an explicit light palette
  swapped in - a plain light/white look, guaranteed regardless of the
  system's own dark-mode setting, but otherwise identical to "dark" in
  every way that isn't color (same widget geometry, same hover
  behavior, same everything).

Windows' native style renders popup menus (QMenu, used for the
File/Settings dropdowns) via its own OS dark-mode setting rather than
Qt's QPalette, so on a system running in Windows dark mode those popups
stay dark even under "bright". A tiny color-only QSS rule for QMenu
(no padding/border/geometry changes) forces it to follow the palette
instead.
"""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

THEMES = ("dark", "bright")
DEFAULT_THEME = "dark"

_native_palette = None
_current_theme = DEFAULT_THEME

# Applied in both themes - tightens the native style's unusually wide
# gap between top-level menu bar entries ("File", "Settings"), and gives
# them a visible hover/press background (palette(highlight) tracks
# whichever theme's palette is active, so this one rule works for both).
# The default (non-hover) state is given the same explicit
# padding/background here too - leaving it to fall back on the native
# style's own box model while only :selected/:pressed are styled is what
# caused the item to change size between its resting and hovered state.
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


def apply_theme(app: QApplication, name: str):
    """Switches the app's color palette only. Call once at startup with
    the saved/default theme, and again whenever the user picks a
    different one from Settings > Theme."""
    global _native_palette, _current_theme
    if _native_palette is None:
        _native_palette = QPalette(app.palette())

    _current_theme = name
    app.setPalette(_bright_palette() if name == "bright" else _native_palette)
    app.setStyleSheet(_BRIGHT_MENU_QSS if name == "bright" else _BASE_QSS)

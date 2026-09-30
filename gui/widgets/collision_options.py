"""The collision menu every view shares: what to draw, how to colour it, and a legend.

One CollisionOptions per view holds its CollisionStyle (gui/widgets/collision_overlay.py)
and builds the menu that edits it. The choice is remembered between sessions and shared
by every view, so the level editor, the MDAT view and the SCLD viewer show the same
collision the same way - a view only reacts to `changed` by rebuilding its lines.
"""
from dataclasses import fields

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QLabel, QMenu, QToolButton, QWidgetAction

from gui.widgets import collision_overlay as overlay

SETTINGS = ("Tomba2Edit", "Tomba2Edit")
PREFIX = "collision/"

# (field, label, what it shows). Order is menu order; "" starts a new group.
ITEMS = (
    ("", "Surfaces (the plane's line, cell by cell)"),
    ("floors", "Floors",
     "What Tomba stands on (kind bit 0). A ramp's line rises across its cell by the "
     "record's `rise`, so slopes are drawn at their true heights."),
    ("ceilings", "Ceilings",
     "Surfaces overhead (kind bit 1). Gold ones can be gripped with the Grapple "
     "(kind bits 0x10 / 0x20) when colouring by type."),
    ("slopes", "Sloped faces",
     "Records with kind bit 0x80 that are not floors - a wall face that slants "
     "across its cell, tested by its height there."),
    ("walls", "Walls",
     "Records that block an actor moving one way along the plane (kind bits 0x04 / 0x08), "
     "drawn as a vertical from the record's height to height + rise where the game stops the "
     "probe. Gold ones are Grapple targets (kind bit 0x100)."),
    ("", "Game logic"),
    ("junctions", "Lane switches",
     "A cell where Up or Down takes Tomba to another plane. The arrow stands on the switch "
     "cell and points at the nearest point of the plane it leads to. Click it for the "
     "destination, walking / hop mode and the body-height window."),
    ("links", "Plane links",
     "The plane reached off each end of a plane (the header's ls / le; 0 means a wall), "
     "and cells that redirect to another plane."),
    ("", "Structure"),
    ("baselines", "Plane lines",
     "Each plane's own line, from its first endpoint to its last (arrow), at its typical "
     "floor height."),
    ("cells", "Cell grid",
     "The 64-unit cells that hold geometry, at their surface height."),
    ("samples", "Record crosses",
     "A cross at the middle of every record drawn - the original look, handy to count them."),
    ("fill", "Curtains",
     "A translucent strip hanging under each floor and rising over each ceiling, to read "
     "the planes as sheets."),
    ("numbers", "Plane numbers",
     "Each plane's number at its middle, in the colour it has in 'Plane' colouring "
     "(views that can draw text)."),
)


class CollisionOptions(QObject):
    """The collision style, with menus that edit it, remembered between sessions.

    One shared instance (CollisionOptions.shared()): every view's menu edits the same
    style, so a change in one shows in the others."""
    changed = pyqtSignal()
    _shared = None

    @classmethod
    def shared(cls):
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def __init__(self, parent=None, **defaults):
        super().__init__(parent)
        self.style = overlay.CollisionStyle(**defaults)
        self._defaults = overlay.CollisionStyle(**defaults)
        self._menus = []            # one bundle per menu built: (actions, mode actions, legend label)
        self._load()

    # -- persistence

    def _load(self):
        settings = QSettings(*SETTINGS)
        for f in fields(self.style):
            if f.name == "only_plane" or not settings.contains(PREFIX + f.name):
                continue
            value = settings.value(PREFIX + f.name)
            if isinstance(getattr(self.style, f.name), bool):
                setattr(self.style, f.name, str(value).lower() in ("true", "1"))
            elif f.name == "color" and value in {k for k, _l, _d in overlay.COLOR_MODES}:
                self.style.color = value

    def _save(self):
        settings = QSettings(*SETTINGS)
        for f in fields(self.style):
            if f.name != "only_plane":
                settings.setValue(PREFIX + f.name, getattr(self.style, f.name))

    # -- the menu

    def menu(self, parent=None, numbers=False):
        """A new menu editing the shared style. `numbers`: the view can draw plane numbers."""
        menu = QMenu(parent)
        menu.setToolTipsVisible(True)
        menu.setObjectName("collisionMenu")
        actions, modes = {}, {}
        for item in ITEMS:
            if len(item) == 2:
                menu.addSeparator()
                menu.addAction(item[1]).setEnabled(False)
                continue
            name, label, tip = item
            if name == "numbers" and not numbers:
                continue
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(getattr(self.style, name))
            action.setToolTip(tip)
            action.setStatusTip(tip)
            action.toggled.connect(lambda on, n=name: self._set(n, on))
            menu.addAction(action)
            actions[name] = action
        menu.addSeparator()
        colour = menu.addMenu("Colour by")
        colour.setToolTipsVisible(True)
        group = QActionGroup(colour)
        group.setExclusive(True)
        for key, label, tip in overlay.COLOR_MODES:
            action = QAction(label, colour)
            action.setCheckable(True)
            action.setChecked(self.style.color == key)
            action.setToolTip(tip)
            action.setStatusTip(tip)
            action.triggered.connect(lambda _c=False, k=key: self._set("color", k))
            group.addAction(action)
            colour.addAction(action)
            modes[key] = action
        menu.addSeparator()
        legend = QLabel()
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setContentsMargins(14, 4, 14, 4)
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(legend)
        menu.addAction(holder)
        menu.addSeparator()
        menu.addAction("Reset to defaults").triggered.connect(self.reset)
        self._menus.append((actions, modes, legend))
        self._sync()
        return menu

    def _set(self, name, value):
        if getattr(self.style, name) == value:
            return
        setattr(self.style, name, value)
        self._save()
        self._sync()
        self.changed.emit()

    def _sync(self):
        """Every menu built shows the style as it stands."""
        html = overlay.legend_html(self.style)
        for actions, modes, legend in self._menus:
            for name, action in actions.items():
                action.blockSignals(True)
                action.setChecked(getattr(self.style, name))
                action.blockSignals(False)
            for key, action in modes.items():
                action.blockSignals(True)
                action.setChecked(self.style.color == key)
                action.blockSignals(False)
            legend.setText(html)

    def reset(self):
        for f in fields(self._defaults):
            if f.name != "only_plane":
                setattr(self.style, f.name, getattr(self._defaults, f.name))
        self._save()
        self._sync()
        self.changed.emit()

    def set_only_plane(self, index):
        """Draw just this entry's plane (None: all). Not remembered."""
        if self.style.only_plane != index:
            self.style.only_plane = index
            self.changed.emit()


TOOLTIP = ("What the collision shows and how it is coloured: floors, ceilings, sloped "
           "faces, walls, lane switches, plane links, plane lines, the cell grid, record "
           "crosses, curtains - and a legend of the colours. The same menu in the level "
           "editor, the MDAT view and the SCLD viewer; the choice is remembered.")


def add_menu_button(toolbar, menu, icon, before=None, text="Collision options"):
    """A toolbar button that opens `menu` when clicked - next to the Collision toggle, in
    `toolbar` before action `before` (at the end when None). Returns its action."""
    action = QAction(icon, text, toolbar)
    action.setMenu(menu)
    action.setToolTip(TOOLTIP)
    if before is None:
        toolbar.addAction(action)
    else:
        toolbar.insertAction(before, action)
    button = toolbar.widgetForAction(action)
    if isinstance(button, QToolButton):
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    return action

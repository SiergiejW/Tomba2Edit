"""Small, draggable isometric presets for native level pickups."""
from PyQt6.QtCore import QMimeData, QPoint, QSize, Qt
from PyQt6.QtGui import QDrag, QIcon
from PyQt6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem

MIME = "application/x-tomba2-pickup"
PRESETS = (("chest", "Chest", "Red chest"),
           ("crystal", "Crystal", "100 AP crystal"),
           ("item", "Item", "Red Key"),
           ("apple", "Health", "Red apple"),
           ("weapon", "Weapon", "Wooden Boomerang"))


def preset_icon(kind):
    """US retail model renders and native pickup art, bundled with the app."""
    from icons import icons
    return QIcon(getattr(icons, "icon_placement_" + kind))


class PlacementPalette(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("placementPalette")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(88, 72))
        self.setGridSize(QSize(132, 112))
        self.setMinimumHeight(92)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setToolTip("Drag a preset into the level. Change its contents or type in Selected.")
        for kind, label, default in PRESETS:
            item = QListWidgetItem(preset_icon(kind), label, self)
            item.setData(Qt.ItemDataRole.UserRole, kind)
            item.setToolTip(f"{default} — drag into the viewport")

    def startDrag(self, actions):
        item = self.currentItem()
        if item is None:
            return
        mime = QMimeData()
        mime.setData(MIME, item.data(Qt.ItemDataRole.UserRole).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(self.iconSize()))
        drag.setHotSpot(QPoint(44, 52))
        drag.exec(Qt.DropAction.CopyAction)

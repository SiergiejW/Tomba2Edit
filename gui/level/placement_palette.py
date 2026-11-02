"""Small, draggable isometric presets for native level pickups."""
from PyQt6.QtCore import QMimeData, QPoint, QSize, Qt
from PyQt6.QtGui import QColor, QDrag, QIcon, QPainter, QPen, QPixmap, QPolygon
from PyQt6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem

MIME = "application/x-tomba2-pickup"
PRESETS = (("chest", "Chest", "Red chest"),
           ("crystal", "Crystal", "100 AP crystal"),
           ("item", "Item", "Red Key"),
           ("apple", "Health", "Red apple"),
           ("weapon", "Weapon", "Wooden Boomerang"))


def preset_icon(kind):
    """Code-native icons stay crisp and do not depend on a disc being open."""
    pix = QPixmap(88, 72)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#302b34"), 2))

    def poly(points, color):
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygon([QPoint(*point) for point in points]))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 55))
    p.drawEllipse(13, 52, 63, 13)
    p.setPen(QPen(QColor("#302b34"), 2))
    if kind == "chest":
        poly(((16, 28), (45, 16), (73, 29), (44, 43)), "#f36d65")
        poly(((16, 28), (44, 43), (44, 63), (16, 48)), "#a93641")
        poly(((44, 43), (73, 29), (73, 49), (44, 63)), "#d44a50")
        p.setPen(QPen(QColor("#eec875"), 5))
        p.drawLine(29, 24, 57, 37)
        p.drawLine(57, 37, 57, 54)
        p.drawLine(16, 39, 44, 53)
        p.drawLine(44, 53, 73, 39)
    elif kind == "crystal":
        poly(((45, 7), (63, 27), (45, 63), (26, 28)), "#ffa538")
        poly(((45, 7), (45, 63), (26, 28)), "#ffda79")
        poly(((45, 7), (63, 27), (51, 27)), "#fff0af")
    elif kind == "apple":
        p.setBrush(QColor("#e8464f"))
        p.drawEllipse(24, 25, 27, 34)
        p.drawEllipse(39, 25, 27, 34)
        p.setPen(QPen(QColor("#714b2e"), 4))
        p.drawLine(44, 29, 46, 16)
        poly(((46, 20), (61, 11), (58, 23)), "#78bd69")
        p.setPen(QPen(QColor("#ffaaa0"), 4))
        p.drawLine(31, 34, 29, 43)
    elif kind == "item":
        p.setPen(QPen(QColor("#fd6666"), 8))
        p.drawEllipse(20, 16, 22, 22)
        p.drawLine(38, 34, 65, 54)
        p.drawLine(57, 48, 63, 40)
        p.drawLine(64, 54, 71, 46)
    else:
        poly(((16, 22), (25, 17), (46, 36), (68, 19), (77, 26),
              (47, 57), (39, 52)), "#c58b50")
        p.setPen(QPen(QColor("#f2c17e"), 3))
        p.drawLine(25, 23, 45, 43)
        p.drawLine(45, 43, 69, 26)
    p.end()
    return QIcon(pix)


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

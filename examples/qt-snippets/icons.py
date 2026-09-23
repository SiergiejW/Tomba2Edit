import sys
from PyQt6.QtWidgets import QApplication, QGridLayout, QPushButton, QStyle, QWidget


class Window(QWidget):
    def __init__(self):
        super().__init__()

        # Get all standard pixmaps from QStyle
        icons = sorted([attr for attr in dir(QStyle.StandardPixmap) if attr.startswith("SP_")])
        layout = QGridLayout()

        for n, name in enumerate(icons):
            btn = QPushButton(name)

            # Get the pixmap attribute from QStyle.StandardPixmap
            pixmapi = getattr(QStyle.StandardPixmap, name)
            icon = self.style().standardIcon(pixmapi)
            btn.setIcon(icon)

            # Add button to grid layout
            layout.addWidget(btn, n // 4, n % 4)  # Fix integer division

        self.setLayout(layout)


app = QApplication(sys.argv)

w = Window()
w.show()

app.exec()



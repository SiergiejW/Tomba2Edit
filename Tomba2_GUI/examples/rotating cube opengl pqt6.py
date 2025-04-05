from PyQt6 import QtGui
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtOpenGLWidgets import QOpenGLWidget as OpenGLWidget
import OpenGL.GL as GL  # Python wrapping of OpenGL
import sys  # For running the Qt application
import numpy as np


class MainWindow(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__()

        self.resize(300, 300)
        self.setWindowTitle('Hello OpenGL Cube')

        width, height = 640, 480

        self.opengl = GLWidget(width, height)

        self.initGUI()

    def initGUI(self):
        self.button = QtWidgets.QPushButton('Rotate Cube', self)
        self.button.clicked.connect(self.opengl.toggle_rotation)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.opengl)
        mainLayout.addWidget(self.button)
        self.setLayout(mainLayout)


class GLWidget(OpenGLWidget):

    def __init__(self, width, height, parent=None):
        super().__init__(parent)
        self.setMinimumSize(width, height)
        self.angle = 0
        self.rotating = False
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_rotation)
        self.timer.start(16)  # Approx 60 FPS

    def initializeGL(self):
        GL.glEnable(GL.GL_DEPTH_TEST)
        self.init_geometry()

    def init_geometry(self):
        # Define the vertices for a cube
        self.vertices = np.array([
            # Front face
            -1.0, -1.0,  1.0,
             1.0, -1.0,  1.0,
             1.0,  1.0,  1.0,
            -1.0,  1.0,  1.0,

            # Back face
            -1.0, -1.0, -1.0,
             1.0, -1.0, -1.0,
             1.0,  1.0, -1.0,
            -1.0,  1.0, -1.0
        ], dtype=np.float32)

        self.indices = np.array([
            # Front face
            0, 1, 2, 2, 3, 0,
            # Back face
            4, 5, 6, 6, 7, 4,
            # Left face
            4, 0, 3, 3, 7, 4,
            # Right face
            1, 5, 6, 6, 2, 1,
            # Top face
            3, 2, 6, 6, 7, 3,
            # Bottom face
            4, 5, 1, 1, 0, 4
        ], dtype=np.uint32)

        self.vertex_buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vertex_buffer)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, self.vertices.nbytes, self.vertices, GL.GL_STATIC_DRAW)

        self.index_buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.index_buffer)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, self.indices.nbytes, self.indices, GL.GL_STATIC_DRAW)

    def resizeGL(self, width, height):
        aspect = width / height if height != 0 else 1
        GL.glViewport(0, 0, width, height)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GL.glFrustum(-aspect, aspect, -1.0, 1.0, 1.0, 10.0)

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        GL.glTranslatef(0.0, 0.0, -5.0)  # Move cube back
        GL.glRotatef(self.angle, 1.0, 1.0, 0.0)  # Rotate cube

        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vertex_buffer)
        GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)

        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.index_buffer)
        GL.glDrawElements(GL.GL_TRIANGLES, len(self.indices), GL.GL_UNSIGNED_INT, None)

        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)

    def update_rotation(self):
        if self.rotating:
            self.angle += 1
            self.angle %= 360
        self.update()

    def toggle_rotation(self):
        self.rotating = not self.rotating


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)

    win = MainWindow()
    win.show()

    sys.exit(app.exec())

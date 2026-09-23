from PyQt6 import QtGui
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtOpenGLWidgets import QOpenGLWidget as OpenGLWidget
import OpenGL.GL as GL  # Python wrapping of OpenGL
import sys  # For running the Qt application
import numpy as np
from PIL import Image


class MainWindow(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__()

        self.resize(300, 300)
        self.setWindowTitle('Tomba 2 Tools')

        width, height = 640, 480

        self.opengl = GLWidget(width, height)

        self.initGUI()

    def initGUI(self):
        self.button = QtWidgets.QPushButton('Rotate Cube', self)
        self.button2 = QtWidgets.QPushButton('Button', self)
        self.button.clicked.connect(self.opengl.toggle_rotation)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.opengl)
        mainLayout.addWidget(self.button)
        mainLayout.addWidget(self.button2)
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
        self.texture = None  # Initialize texture

    def initializeGL(self):
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_TEXTURE_2D)
        self.init_geometry()
        self.load_texture("test.png")  # Replace with your texture path

    def init_geometry(self):
        # Define the vertices, colors, and texture coordinates for a cube
        self.vertices = np.array([
            # Front face
            -1.0, -1.0,  1.0,  1.0, 1.0, 1.0,  0.0, 0.0,  # Red vertex, texture (0,0)
             1.0, -1.0,  1.0,  1.0, 1.0, 1.0,  1.0, 0.0,  # Texture (1,0)
             1.0,  1.0,  1.0,  1.0, 1.0, 1.0,  1.0, 1.0,  # Texture (1,1)
            -1.0,  1.0,  1.0,  1.0, 1.0, 1.0,  0.0, 1.0,  # Texture (0,1)

            # Back face
            -1.0, -1.0, -1.0,  0.0, 1.1, 0.5,  0.0, 0.0,
             1.0, -1.0, -1.0,  0.0, 0.1, 0.6,  1.0, 0.0,
             1.0,  1.0, -1.0,  1.0, 0.1, 0.7,  1.0, 1.0,
            -1.0,  1.0, -1.0,  0.0, 0.1, 1.8,  0.0, 1.0
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

    def load_texture(self, image_path):
        try:
            img = Image.open(image_path).convert("RGB")  # Ensure RGB format
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            img_data = np.array(img, dtype=np.uint8)

            self.texture = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGB,
                img.width, img.height, 0,
                GL.GL_RGB, GL.GL_UNSIGNED_BYTE, img_data
            )
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        except Exception as e:
            print(f"Failed to load texture: {e}")
            self.texture = None

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
        GL.glTranslatef(0.0, 0.0, -4.0)  # Move cube back
        GL.glRotatef(25, 1.0, 0.0, 0.0)  # Rotate cube
        GL.glRotatef(self.angle, 0.0, 1.0, 0.0)  # Rotate cube

        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_COLOR_ARRAY)
        GL.glEnableClientState(GL.GL_TEXTURE_COORD_ARRAY)

        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vertex_buffer)
        GL.glVertexPointer(3, GL.GL_FLOAT, 8 * self.vertices.itemsize, None)
        GL.glColorPointer(3, GL.GL_FLOAT, 8 * self.vertices.itemsize, GL.GLvoidp(3 * self.vertices.itemsize))
        GL.glTexCoordPointer(2, GL.GL_FLOAT, 8 * self.vertices.itemsize, GL.GLvoidp(6 * self.vertices.itemsize))

        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.index_buffer)

        # Bind texture only for the front face
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
        GL.glDrawElements(GL.GL_TRIANGLES, 6, GL.GL_UNSIGNED_INT, None)

        # Disable texture for the other faces
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glDrawElements(GL.GL_TRIANGLES, len(self.indices) - 6, GL.GL_UNSIGNED_INT,
                          GL.GLvoidp(6 * self.indices.itemsize))

        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glDisableClientState(GL.GL_COLOR_ARRAY)
        GL.glDisableClientState(GL.GL_TEXTURE_COORD_ARRAY)


    def update_rotation(self):
        if self.rotating:
            self.angle += 0.5
            self.angle %= 360
        self.update()

    def toggle_rotation(self):
        self.rotating = not self.rotating


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)

    win = MainWindow()
    win.show()

    sys.exit(app.exec())

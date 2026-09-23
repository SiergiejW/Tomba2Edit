from PyQt6 import QtGui
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtOpenGLWidgets import QOpenGLWidget as OpenGLWidget
import OpenGL.GL as GL  # python wrapping of OpenGL
from OpenGL import GLU  # OpenGL Utility Library, extends OpenGL functionality
import sys  # we'll need this later to run our Qt application
import random
import numpy as np


class MainWindow(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__()

        self.resize(300, 300)
        self.setWindowTitle('Hello OpenGL App')

        width, height = 640, 480

        self.opengl = GLWidget(width, height)

        self.initGUI()

    def initGUI(self):
        self.button = QtWidgets.QPushButton('Create Triangle', self)
        self.button.clicked.connect(self.draw_triangle)

        self.sliderX = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sliderX.valueChanged.connect(self.sliderXCallBack)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.opengl)
        mainLayout.addWidget(self.sliderX)
        mainLayout.addWidget(self.button)
        self.setLayout(mainLayout)

    def draw_triangle(self):
        gl_width, gl_height = self.opengl.size().width(), self.opengl.size().height()
        width = random.randint(50, 100)
        height = random.randint(50, 100)
        line = []
        for l in range(3):
            line.append(random.randint(0, gl_width - width))
            line.append(random.randint(0, gl_height - height))
            self.opengl.points.append(line)

    def sliderXCallBack(self):
        val = self.sliderX.value()
        pass


class GLWidget(OpenGLWidget):

    def __init__(self, width, height, parent=None):
        super().__init__(parent)
        self.setMinimumSize(width, height)
        self.init_geometry()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_graph)
        self.timer.setInterval(0)
        self.timer.start()

    def init_geometry(self):
        self.points = [[100, 100], [200, 350], [34, 260]]

    def initializeGL(self):
        vertices = np.array([0.0, 1.0, -1.0, -1.0, 1.0, -1.0], dtype=np.float32)

        bufferId = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, bufferId)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL.GL_STATIC_DRAW)

        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)

    # def init_ortho(self):
    #     screen_width = 800
    #     screen_height = 800
    #     ortho_left = -400
    #     ortho_right = 400
    #     ortho_top = -400
    #     ortho_bottom = 400
    #     gl.glMatrixMode(gl.GL_PROJECTION)
    #     gl.glLoadIdentity()

    def resizeGL(self, width, height):
        pass

    # def plot_point(self):
    #     gl.glBegin(gl.GL_POINTS)
    #     for p in self.points:
    #         gl.glVertex2f(p[0], p[1])
    #     gl.glEnd()

    # def plot_lines(self):
    #     localpoints = [[100, 100], [200, 350], [34, 260]]
    #     gl.glBegin(gl.GL_LINE_STRIP)
    #     gl.glVertex2d(100, 100)
    #     gl.glVertex2d(200, 350)
    #     gl.glVertex2d(34, 260)
    #     gl.glEnd()

    def paintGL(self):
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)

    def update_graph(self):
        self.update()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


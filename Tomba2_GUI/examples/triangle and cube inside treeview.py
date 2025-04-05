import sys
import random
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QStandardItem, QStandardItemModel, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTreeView,
    QWidget,
    QVBoxLayout,
    QLabel,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QStyle,
    QFileDialog,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GL import shaders
import numpy as np

version = "0.0.3"


class OpenGLShapeWidget(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.angle = 0.0  # Rotation angle
        self.colors = self.generate_random_colors()
        self.shape = "triangle"  # Default shape
        self.vertices = None  # Placeholder for vertex data
        self.colors_array = None  # Placeholder for color data
        self.vao = None  # Vertex Array Object
        self.vbo = None  # Vertex Buffer Object

    def generate_random_colors(self):
        return [random.random() for _ in range(12)]

    def reset_shape(self, shape):
        self.angle = 0.0
        self.colors = self.generate_random_colors()
        self.shape = shape
        if self.shape == "triangle":
            self.vertices = np.array([
                -0.5, -0.5, 0.0,
                 0.5, -0.5, 0.0,
                 0.0,  0.5, 0.0
            ], dtype=np.float32)
        elif self.shape == "square":
            self.vertices = np.array([
                -0.5, -0.5, 0.0,
                 0.5, -0.5, 0.0,
                 0.5,  0.5, 0.0,
                -0.5,  0.5, 0.0
            ], dtype=np.float32)
        self.colors_array = np.array(self.colors[:len(self.vertices)], dtype=np.float32)
        if self.vao and self.vbo:
            self.makeCurrent()
            glBindVertexArray(self.vao)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
            glBufferData(GL_ARRAY_BUFFER, self.vertices.nbytes + self.colors_array.nbytes, None, GL_STATIC_DRAW)
            glBufferSubData(GL_ARRAY_BUFFER, 0, self.vertices.nbytes, self.vertices)
            glBufferSubData(GL_ARRAY_BUFFER, self.vertices.nbytes, self.colors_array.nbytes, self.colors_array)
            glBindBuffer(GL_ARRAY_BUFFER, 0)
            glBindVertexArray(0)
            self.doneCurrent()

    def initializeGL(self):
        vertex_shader = shaders.compileShader("""
        #version 330 core
        layout(location = 0) in vec3 position;
        layout(location = 1) in vec3 color;
        out vec3 fragColor;
        uniform float angle;
        void main() {
            mat2 rotation = mat2(cos(angle), -sin(angle), sin(angle), cos(angle));
            vec2 rotated_position = rotation * position.xy;
            gl_Position = vec4(rotated_position, position.z, 1.0);
            fragColor = color;
        }
        """, GL_VERTEX_SHADER)

        fragment_shader = shaders.compileShader("""
        #version 330 core
        in vec3 fragColor;
        out vec4 outColor;
        void main() {
            outColor = vec4(fragColor, 1.0);
        }
        """, GL_FRAGMENT_SHADER)

        self.shader_program = shaders.compileProgram(vertex_shader, fragment_shader)
        self.vao = glGenVertexArrays(1)
        self.vbo = glGenBuffers(1)
        self.reset_shape("triangle")
        self.makeCurrent()
        glBindVertexArray(self.vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
        glBufferData(GL_ARRAY_BUFFER, self.vertices.nbytes + self.colors_array.nbytes, None, GL_STATIC_DRAW)
        glBufferSubData(GL_ARRAY_BUFFER, 0, self.vertices.nbytes, self.vertices)
        glBufferSubData(GL_ARRAY_BUFFER, self.vertices.nbytes, self.colors_array.nbytes, self.colors_array)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 0, None)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 0, ctypes.c_void_p(self.vertices.nbytes))
        glEnableVertexAttribArray(1)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindVertexArray(0)
        self.doneCurrent()
        self.timer.start(16)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glUseProgram(self.shader_program)
        glUniform1f(glGetUniformLocation(self.shader_program, "angle"), self.angle)
        glBindVertexArray(self.vao)
        if self.shape == "triangle":
            glDrawArrays(GL_TRIANGLES, 0, 3)
        elif self.shape == "square":
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
        glBindVertexArray(0)
        self.angle += 0.01

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Tomba2Edit v{version}")
        self.resize(800, 600)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self.splitter)
        self.tree_view = QTreeView()
        self.splitter.addWidget(self.tree_view)
        self.widgets_area = QStackedWidget()
        self.splitter.addWidget(self.widgets_area)
        self.setup_tree_view()
        self.setup_widgets()
        self.tree_view.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.setStatusBar(QStatusBar(self))
        container_widget = QWidget()
        container_layout = QVBoxLayout()
        toolbar = QToolBar("Main Toolbar")
        container_layout.addWidget(toolbar)
        action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open", self)
        action.triggered.connect(self.open_folder_dialog)
        toolbar.addAction(action)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.folder_info_label = QLabel("Select Tomba folder (with BIN, CD, MOVIE)")
        self.folder_info_label.setWordWrap(True)
        container_layout.addWidget(self.folder_info_label)
        container_widget.setLayout(container_layout)
        self.setMenuWidget(container_widget)
        initial_treeview_width = int(self.width() * 0.30)
        self.splitter.setSizes([initial_treeview_width, self.width() - initial_treeview_width])

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.folder_info_label.setText(f"Selected Folder: {folder}")
        else:
            self.folder_info_label.setText("Select Tomba folder (with BIN, CD, MOVIE)")

    def setup_tree_view(self):
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Name"])
        root_item = model.invisibleRootItem()
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        for i in range(3):
            folder = QStandardItem(folder_icon, f"Folder {i + 1}")
            for j in range(2):
                file = QStandardItem(file_icon, f"File {i + 1}-{j + 1}.txt")
                folder.appendRow(file)
            root_item.appendRow(folder)
        self.tree_view.setModel(model)

    def setup_widgets(self):
        self.folder_widget = QLabel("This is a folder")
        self.folder_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_widget = OpenGLShapeWidget()
        self.widgets_area.addWidget(self.folder_widget)
        self.widgets_area.addWidget(self.file_widget)

    def on_tree_selection_changed(self):
        selected_indexes = self.tree_view.selectionModel().selectedIndexes()
        if selected_indexes:
            selected_item = selected_indexes[0].data()
            if "Folder" in selected_item:
                self.widgets_area.setCurrentWidget(self.folder_widget)
            elif "File" in selected_item:
                self.widgets_area.setCurrentWidget(self.file_widget)
                if "3" in selected_item:
                    print(selected_item)
                    self.file_widget.reset_shape("square")
                else:
                    self.file_widget.reset_shape("triangle")


app = QApplication(sys.argv)
window = MainWindow()
window.show()
app.exec()

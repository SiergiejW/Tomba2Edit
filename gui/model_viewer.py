import numpy as np
import random
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GL import shaders


class OpenGLShapeWidget(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.angle = 0.0
        self.colors = self.generate_random_colors()
        self.shape = "triangle"
        self.vertices = None
        self.colors_array = None
        self.vao = None
        self.vbo = None

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
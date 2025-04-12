import numpy as np
from PyQt6.QtCore import Qt, QSize, QPoint, QTimer
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject,
    QOpenGLBuffer
)
from PyQt6.QtGui import QMatrix4x4, QCursor
from OpenGL import GL
import gui.mdat.mdat as mdat
import math


class MDATViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_data = None
        self.vao = QOpenGLVertexArrayObject()
        self.vertex_buffer = QOpenGLBuffer()
        self.color_buffer = QOpenGLBuffer()
        self.index_buffer = QOpenGLBuffer()
        self.shader_program = QOpenGLShaderProgram()

        # Initialize last_pos for mouse tracking
        self.last_pos = QPoint()

        # Camera variables
        self.camera_y = 0.0
        self.camera_x = 0.0
        self.camera_z = -5.0
        self.mouse_sensitivity = 0.1
        self.camera_speed = 0.1
        self.camera_speed_min = 0.001
        self.camera_speed_max = 4.0
        self.camera_angle_h = 0.0
        self.camera_angle_v = 0.0

        # Mouse tracking
        self.display_center = [self.width() // 2, self.height() // 2]
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Camera mode
        self.camera_mode = False

        # Key states
        self.keys_pressed = {
            Qt.Key.Key_W: False,
            Qt.Key.Key_S: False,
            Qt.Key.Key_A: False,
            Qt.Key.Key_D: False,
            Qt.Key.Key_E: False,
            Qt.Key.Key_Q: False,
            Qt.Key.Key_Shift: False,
        }

        # Timer for smooth movement
        self.key_timer = QTimer()
        self.key_timer.timeout.connect(self.handle_key_movement)
        self.key_timer.start(16)

    def load_mdat_data(self, dat_file_path, dat_start, offset):
        """Load MDAT data from the DAT file"""
        address = dat_start + offset
        print("at", address, f"({dat_start})")

        try:
            self.model_data = mdat.exportMDAT(address, dat_file_path)
            self.update()
            return True
        except Exception as e:
            print(f"Error loading MDAT data: {e}")
            return False

    def initializeGL(self):
        """Initialize OpenGL"""
        GL.glClearColor(0.1, 0.1, 0.1, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_CULL_FACE)
        GL.glCullFace(GL.GL_BACK)

        # Initialize shaders
        self.shader_program = QOpenGLShaderProgram()
        if not self.shader_program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Vertex,
                """
                #version 330 core
                layout(location = 0) in vec3 position;
                layout(location = 1) in vec3 color;
                uniform mat4 modelViewProjection;
                out vec3 fragColor;
                void main() {
                    gl_Position = modelViewProjection * vec4(position, 1.0);
                    fragColor = color;
                }
                """
        ):
            print("Vertex shader compilation failed:", self.shader_program.log())

        if not self.shader_program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment,
                """
                #version 330 core
                in vec3 fragColor;
                out vec4 outColor;
                void main() {
                    outColor = vec4(fragColor, 1.0);
                }
                """
        ):
            print("Fragment shader compilation failed:", self.shader_program.log())

        if not self.shader_program.link():
            print("Shader program linking failed:", self.shader_program.log())

        # Initialize VAO and buffers
        self.vao.create()
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.color_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.index_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)

    def resizeGL(self, w, h):
        """Handle window resize"""
        self.display_center = [w // 2, h // 2]
        GL.glViewport(0, 0, w, h)

    def paintGL(self):
        """Render the scene"""
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

        if not self.model_data or not self.model_data.get('vertices'):
            return

        # Set up matrices
        projection = QMatrix4x4()
        projection.perspective(45.0, self.width() / self.height(), 0.1, 100.0)

        view = QMatrix4x4()
        view.rotate(self.camera_angle_v, 1.0, 0.0, 0.0)
        view.rotate(self.camera_angle_h, 0.0, 1.0, 0.0)
        view.translate(self.camera_x, self.camera_y, self.camera_z)

        model_view_projection = projection * view

        # Bind shader
        if not self.shader_program.bind():
            print("Failed to bind shader program")
            return

        self.shader_program.setUniformValue("modelViewProjection", model_view_projection)

        # Prepare data
        try:
            vertices = np.array([
                [v[0] / 1000.0, v[1] / 1000.0, v[2] / 1000.0]
                for v in self.model_data['vertices']
            ], dtype=np.float32).flatten()

            colors = np.array([
                c for c in self.model_data['vertex_colors']
            ], dtype=np.float32).flatten()

            indices = []
            for face in self.model_data['faces']:
                if len(face) == 3:
                    indices.extend(face)
                elif len(face) == 4:
                    indices.extend([face[0], face[1], face[2]])
                    indices.extend([face[0], face[2], face[3]])
            indices = np.array(indices, dtype=np.uint32)
        except Exception as e:
            print(f"Error preparing data: {e}")
            return

        # Upload data
        self.vao.bind()

        try:
            # Vertex buffer
            self.vertex_buffer.create()
            self.vertex_buffer.bind()
            self.vertex_buffer.allocate(vertices.tobytes(), vertices.nbytes)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)

            # Color buffer
            self.color_buffer.create()
            self.color_buffer.bind()
            self.color_buffer.allocate(colors.tobytes(), colors.nbytes)
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)

            # Index buffer
            self.index_buffer.create()
            self.index_buffer.bind()
            self.index_buffer.allocate(indices.tobytes(), indices.nbytes)

            # Draw
            GL.glDrawElements(GL.GL_TRIANGLES, len(indices), GL.GL_UNSIGNED_INT, None)
        except Exception as e:
            print(f"Error uploading data: {e}")

        # Clean up
        self.vao.release()
        self.shader_program.release()

    def wheelEvent(self, event):
        """Handle mouse wheel"""
        if self.camera_mode:
            scroll_amount = event.angleDelta().y() / 120
            self.camera_speed = max(self.camera_speed_min,
                                    min(self.camera_speed_max,
                                        self.camera_speed + scroll_amount * 0.005))
        else:
            self.camera_z += event.angleDelta().y() * 0.01
        self.update()

    def mousePressEvent(self, event):
        """Toggle camera mode"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.camera_mode = not self.camera_mode
            if self.camera_mode:
                self.setCursor(QCursor(Qt.CursorShape.BlankCursor))
                QCursor.setPos(self.mapToGlobal(QPoint(*self.display_center)))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.last_pos = event.pos()

    def mouseMoveEvent(self, event):
        """Handle mouse movement"""
        if self.camera_mode:
            pos = event.position()
            dx = pos.x() - self.display_center[0]
            dy = pos.y() - self.display_center[1]

            self.camera_angle_h += dx * self.mouse_sensitivity
            self.camera_angle_v = max(-89.0, min(89.0,
                                                 self.camera_angle_v + dy * self.mouse_sensitivity))

            QCursor.setPos(self.mapToGlobal(QPoint(*self.display_center)))
        else:
            dx = event.pos().x() - self.last_pos.x()
            dy = event.pos().y() - self.last_pos.y()

            if event.buttons() & Qt.MouseButton.LeftButton:
                self.camera_angle_h += dx
                self.camera_angle_v = max(-89.0, min(89.0, self.camera_angle_v + dy))

        self.last_pos = event.pos()
        self.update()

    def keyPressEvent(self, event):
        """Handle key presses"""
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = True

    def keyReleaseEvent(self, event):
        """Handle key releases"""
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = False

    def handle_key_movement(self):
        """Handle camera movement"""
        if not self.camera_mode:
            return

        speed_multiplier = 4.0 if self.keys_pressed[Qt.Key.Key_Shift] else 1.0
        current_speed = self.camera_speed * speed_multiplier

        h_rad = -math.radians(self.camera_angle_h)
        v_rad = math.radians(self.camera_angle_v)

        forward_x = -math.sin(h_rad) * math.cos(v_rad)
        forward_y = -math.sin(v_rad)
        forward_z = -math.cos(h_rad) * math.cos(v_rad)

        right_x = -math.cos(h_rad)
        right_z = math.sin(h_rad)

        if self.keys_pressed[Qt.Key.Key_W]:
            self.camera_x -= forward_x * current_speed
            self.camera_y -= forward_y * current_speed
            self.camera_z -= forward_z * current_speed
        if self.keys_pressed[Qt.Key.Key_S]:
            self.camera_x += forward_x * current_speed
            self.camera_y += forward_y * current_speed
            self.camera_z += forward_z * current_speed
        if self.keys_pressed[Qt.Key.Key_A]:
            self.camera_x -= right_x * current_speed
            self.camera_z -= right_z * current_speed
        if self.keys_pressed[Qt.Key.Key_D]:
            self.camera_x += right_x * current_speed
            self.camera_z += right_z * current_speed
        if self.keys_pressed[Qt.Key.Key_Q]:
            self.camera_y += current_speed
        if self.keys_pressed[Qt.Key.Key_E]:
            self.camera_y -= current_speed

        self.update()
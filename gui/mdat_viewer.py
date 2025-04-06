import numpy as np
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtGui import QVector3D, QMatrix4x4, QVector4D, QOpenGLShaderProgram, QOpenGLShader, QOpenGLVersionProfile, QOpenGLVertexArrayObject, QOpenGLBuffer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSlider, QLabel
from OpenGL import GL
import gui.mdat.mdat as mdat  # Import the MDAT parser

class MDATViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_data = None
        self.vao = QOpenGLVertexArrayObject()
        self.vertex_buffer = QOpenGLBuffer()
        self.color_buffer = QOpenGLBuffer()
        self.index_buffer = QOpenGLBuffer()
        self.shader_program = QOpenGLShaderProgram()
        self.rotation_x = 0
        self.rotation_y = 0
        self.zoom = -5.0
        self.setMinimumSize(QSize(640, 480))

    def load_mdat_data(self, dat_file_path, dat_start, offset):
        """Load MDAT data from the DAT file"""
        #from mdat import exportMDAT  # Import the MDAT parser

        # Calculate the absolute address in the DAT file
        address = dat_start + offset

        try:
            # Parse the MDAT data
            self.model_data = mdat.exportMDAT(address, dat_file_path)
            self.update()  # Trigger a redraw
        except Exception as e:
            print(f"Error loading MDAT data: {e}")
            return False
        return True

    def initializeGL(self):
        """Initialize OpenGL"""
        GL.glClearColor(0.1, 0.1, 0.1, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)

        # Initialize shaders
        self.shader_program = QOpenGLShaderProgram()
        self.shader_program.addShaderFromSourceCode(
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
        )

        self.shader_program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment,
            """
            #version 330 core
            in vec3 fragColor;
            out vec4 outColor;

            void main() {
                outColor = vec4(fragColor, 1.0);
            }
            """
        )

        self.shader_program.link()

        # Initialize VAO and buffers
        self.vao.create()
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.color_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.index_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)

    def resizeGL(self, w, h):
        """Handle window resize"""
        GL.glViewport(0, 0, w, h)

    def paintGL(self):
        """Render the scene"""
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

        if not self.model_data or not self.model_data['vertices']:
            return

        # Set up projection matrix
        projection = QMatrix4x4()
        projection.perspective(45.0, self.width() / self.height(), 0.1, 100.0)

        # Set up view matrix
        view = QMatrix4x4()
        view.translate(0, 0, self.zoom)
        view.rotate(self.rotation_x, 1, 0, 0)
        view.rotate(self.rotation_y, 0, 1, 0)

        # Combine matrices
        model_view_projection = projection * view

        # Bind shader
        self.shader_program.bind()
        self.shader_program.setUniformValue("modelViewProjection", model_view_projection)

        # Prepare vertex data
        vertices = []
        for v in self.model_data['vertices']:
            vertices.extend([v[0] / 1000.0, v[1] / 1000.0, v[2] / 1000.0])  # Scale down

        colors = []
        for c in self.model_data['vertex_colors']:
            colors.extend(c)  # RGB colors

        # Prepare index data (convert quads to triangles)
        indices = []
        for face in self.model_data['faces']:
            if len(face) == 3:  # Triangle
                indices.extend(face)
            elif len(face) == 4:  # Quad - split into two triangles
                indices.extend([face[0], face[1], face[2]])
                indices.extend([face[0], face[2], face[3]])

        # Bind VAO
        self.vao.bind()

        # Upload vertex data
        self.vertex_buffer.create()
        self.vertex_buffer.bind()
        self.vertex_buffer.allocate(np.array(vertices, dtype=np.float32).tobytes(), len(vertices) * 4)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)

        # Upload color data
        self.color_buffer.create()
        self.color_buffer.bind()
        self.color_buffer.allocate(np.array(colors, dtype=np.float32).tobytes(), len(colors) * 4)
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)

        # Upload index data
        self.index_buffer.create()
        self.index_buffer.bind()
        self.index_buffer.allocate(np.array(indices, dtype=np.uint32).tobytes(), len(indices) * 4)

        # Draw the model
        GL.glDrawElements(GL.GL_TRIANGLES, len(indices), GL.GL_UNSIGNED_INT, None)

        # Clean up
        self.vao.release()
        self.shader_program.release()

    def wheelEvent(self, event):
        """Handle mouse wheel for zooming"""
        self.zoom += event.angleDelta().y() * 0.01
        self.update()

    def mousePressEvent(self, event):
        """Store mouse position for rotation"""
        self.last_pos = event.pos()

    def mouseMoveEvent(self, event):
        """Handle mouse movement for rotation"""
        dx = event.pos().x() - self.last_pos.x()
        dy = event.pos().y() - self.last_pos.y()

        if event.buttons() & Qt.MouseButton.LeftButton:
            self.rotation_y += dx
            self.rotation_x += dy
            self.update()

        self.last_pos = event.pos()
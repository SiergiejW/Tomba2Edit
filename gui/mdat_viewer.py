# mdat_viewer.py
import numpy as np
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject,
    QOpenGLBuffer
)
from PyQt6.QtGui import QMatrix4x4, QImage
from OpenGL import GL
import gui.mdat.mdat as mdat
from functions.camera_controls import CameraControls  # Importing the camera controls class

class MDATViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_data = None
        self.vao = QOpenGLVertexArrayObject()
        self.vertex_buffer = QOpenGLBuffer()
        self.color_buffer = QOpenGLBuffer()
        self.index_buffer = QOpenGLBuffer()
        self.shader_program = QOpenGLShaderProgram()
        self.texcoord_buffer = QOpenGLBuffer()
        self.vram_texture = None  # OpenGL texture ID
        # Initialize the camera controls
        self.camera_controls = CameraControls(self)

    def set_vram_image(self, qimage):
        print("Trying to set VRAM image...")

        self.makeCurrent()

        if qimage.format() != QImage.Format.Format_RGBA8888:
            qimage = qimage.convertToFormat(QImage.Format.Format_RGBA8888)

        ptr = qimage.bits()
        ptr.setsize(qimage.sizeInBytes())
        buf = ptr.asstring()  # ✅ convert to bytes

        self.vram_texture = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.vram_texture)

        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA,
                        qimage.width(), qimage.height(), 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, buf)

        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)

        print("✅ VRAM texture uploaded:", qimage.width(), "x", qimage.height())

    def load_mdat_data(self, dat_file_path, dat_start, offset):
        """Load MDAT data from the DAT file"""
        address = dat_start + offset
        print("at", address, f"({dat_start})")

        try:
            self.model_data = mdat.exportMDAT(address, dat_file_path)
            self.prepare_buffers()  # <- use self.
            self.update()
            return True
        except Exception as e:
            print(f"Error loading MDAT data: {e}")
            return False

    def prepare_buffers(self):
        if not self.model_data or not self.model_data.get("vertices"):
            return

        try:
            vertices = np.array([
                [v[0] / 1000.0, v[1] / 1000.0, v[2] / 1000.0]
                for v in self.model_data['vertices']
            ], dtype=np.float32).flatten()

            colors = np.array(self.model_data['vertex_colors'], dtype=np.float32).flatten()

            tex_coords = np.array([
                [t[0], t[1]] for t in self.model_data['texture_coords']
            ], dtype=np.float32).flatten()

            indices = []
            for face in self.model_data['faces']:
                if len(face) == 3:
                    indices.extend(face)
                elif len(face) == 4:
                    indices.extend([face[0], face[1], face[2]])
                    indices.extend([face[0], face[2], face[3]])
            indices = np.array(indices, dtype=np.uint32)

            self.vao.bind()

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

            # Texture coord buffer
            self.texcoord_buffer.create()
            self.texcoord_buffer.bind()
            self.texcoord_buffer.allocate(tex_coords.tobytes(), tex_coords.nbytes)
            GL.glEnableVertexAttribArray(2)
            GL.glVertexAttribPointer(2, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)

            # Index buffer
            self.index_buffer.create()
            self.index_buffer.bind()
            self.index_buffer.allocate(indices.tobytes(), indices.nbytes)

            self.vao.release()
        except Exception as e:
            print(f"Error preparing buffers: {e}")

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
                layout(location = 2) in vec2 texCoord;  // new
                uniform mat4 modelViewProjection;
                out vec3 fragColor;
                out vec2 fragTexCoord;  // new
                void main() {
                    gl_Position = modelViewProjection * vec4(position, 1.0);
                    fragColor = color;
                    fragTexCoord = texCoord;  // new
                }
                """
        ):
            print("Vertex shader compilation failed:", self.shader_program.log())

        if not self.shader_program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment,
                """
                #version 330 core
                in vec3 fragColor;
                in vec2 fragTexCoord;  // new
                out vec4 outColor;
                uniform sampler2D vramTexture;  // new
                void main() {
                    vec4 texColor = texture(vramTexture, fragTexCoord);  // new
                    outColor = texColor * vec4(fragColor, 1.0);  // combine or just use texColor
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
        self.texcoord_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)

    def resizeGL(self, w, h):
        """Handle window resize"""
        self.camera_controls.display_center = [w // 2, h // 2]
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
        view.rotate(self.camera_controls.camera_angle_v, 1.0, 0.0, 0.0)
        view.rotate(self.camera_controls.camera_angle_h, 0.0, 1.0, 0.0)
        view.translate(self.camera_controls.camera_x, self.camera_controls.camera_y, self.camera_controls.camera_z)

        model_view_projection = projection * view

        # Bind shader
        if not self.shader_program.bind():
            print("Failed to bind shader program")
            return

        self.shader_program.setUniformValue("modelViewProjection", model_view_projection)

        self.vao.bind()

        if self.vram_texture:
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.vram_texture)
            self.shader_program.setUniformValue("vramTexture", 0)
        else:
            print("⚠️ No VRAM texture bound.")

        # Draw call
        GL.glDrawElements(GL.GL_TRIANGLES, self.index_buffer.size() // 4, GL.GL_UNSIGNED_INT, None)

        self.vao.release()
        self.shader_program.release()

    def wheelEvent(self, event):
        self.camera_controls.wheelEvent(event)

    def mousePressEvent(self, event):
        self.camera_controls.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.camera_controls.mouseMoveEvent(event)

    def keyPressEvent(self, event):
        self.camera_controls.keyPressEvent(event)

    def keyReleaseEvent(self, event):
        self.camera_controls.keyReleaseEvent(event)

# mdat_viewer.py
import numpy as np
from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QFileDialog
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
from gui.mdat_export import export_mdat_to_gltf
from functions.camera_controls import CameraControls  # Importing the camera controls class
import ctypes
from PyQt6.QtWidgets import (
    QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle,
)


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
        self.clut_quad_tex = None
        self.clut_tri_tex = None
        self.clut_map = {}  # address -> GL texture ID
        self.clut_index_groups = {}  # address -> list of indices
        self.export_button = QPushButton("Export to GLTF", self)
        self.export_button.clicked.connect(self.export_to_glb)

        layout = QVBoxLayout(self)
        layout.addWidget(self.export_button)
        layout.addStretch()

    def export_to_glb(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save GLTF file", "", "GLTF Files (*.gltf)")
        if file_path:
            success = export_mdat_to_gltf(self.model_data, self.vram_qimage, self.clut_map, file_path)
            if success:
                QMessageBox.information(self, "Export Complete", "Exported model successfully!")
            else:
                QMessageBox.critical(self, "Export Failed", "Failed to export model.")
    def extract_clut_from_vram(self, clut_address, transparent=False):
        clut = []
        # Direct linear address usage (NO x, y calculation here!)
        addr = clut_address  # linear address directly!
        #print(f"\n[NEW SCRIPT] CLUT at VRAM address 0x{clut_address:06X}:")
        line = "  "
        for i in range(16):
            read_addr = addr + i * 2
            if read_addr + 1 >= len(self.vram_raw_bytes):
                b0, b1 = 0, 0
            else:
                b0 = self.vram_raw_bytes[read_addr]
                b1 = self.vram_raw_bytes[read_addr + 1]
            word = b0 | (b1 << 8)
            #print(f"    Bytes @ {read_addr:06X}: {b0:02X} {b1:02X} -> Word: ({word:04X})")
            R = (word & 0x1F) * 8
            G = ((word >> 5) & 0x1F) * 8
            B = ((word >> 10) & 0x1F) * 8
            A = 0 if (R == 0 and G == 0 and B == 0) else (128 if transparent else 255)
            clut.append([R, G, B, A])
        return np.array(clut, dtype=np.uint8)

    def set_vram_image(self, qimage, raw_vram=None):
        self.makeCurrent()

        if qimage.format() != QImage.Format.Format_RGBA8888:
            qimage = qimage.convertToFormat(QImage.Format.Format_RGBA8888)

        ptr = qimage.bits()
        ptr.setsize(qimage.sizeInBytes())
        self.vram_qimage = qimage
        # Store raw VRAM
        self.vram_raw_bytes = raw_vram if raw_vram else bytearray()
        if raw_vram is not None:
            self.vram_raw_bytes = raw_vram
        else:
            print("❌ WARNING: raw_vram missing when setting VRAM! CLUTs will fail.")
            self.vram_raw_bytes = bytearray()

        self.vram_bytes = ptr.asstring()
        self.vram_width = qimage.width()
        self.vram_height = qimage.height()

        # Upload as OpenGL index texture
        self.index_texture = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA,
                        qimage.width(), qimage.height(), 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, self.vram_bytes)

        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        print("VRAM image uploaded as 32-bit RGBA texture (used as index map).")


    def upload_clut(self, clut_array):
        tex_id = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
        GL.glTexImage1D(GL.GL_TEXTURE_1D, 0, GL.GL_RGBA, 16, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, clut_array)
        GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        return tex_id
    def load_mdat_data(self, dat_file_path, dat_start, offset):
        clut_quad = np.random.randint(0, 256, (16, 4), dtype=np.uint8)  # RGBA
        clut_tri = np.random.randint(0, 256, (16, 4), dtype=np.uint8)
        self.makeCurrent()
        self.clut_tri_tex = self.upload_clut(clut_tri)
        self.clut_quad_tex = self.upload_clut(clut_quad)
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
            self.clut_map = {}
            self.clut_index_groups = {}



            index_offset = 0
            indices = []
            vertices = np.array([
                [v[0] / 1000.0, v[1] / 1000.0, v[2] / 1000.0]
                for v in self.model_data['vertices']
            ], dtype=np.float32).flatten()

            colors = np.array(self.model_data['vertex_colors'], dtype=np.float32).flatten()

            tex_coords = np.array([
                [t[0], t[1]] for t in self.model_data['texture_coords']
            ], dtype=np.float32).flatten()

            for i, face in enumerate(self.model_data['faces']):
                tex_info = self.model_data['texture_info'][i]
                clut_address = tex_info[1]

                if clut_address not in self.clut_index_groups:
                    self.clut_index_groups[clut_address] = []

                    # Generate a fake CLUT (16 random RGBA values)
                    _, clut_address, is_transparent = tex_info
                    clut_array = self.extract_clut_from_vram(clut_address, is_transparent)
                    #print(f" CLUT 0x{clut_address:X}: {clut_array}")
                    self.clut_map[clut_address] = self.upload_clut(clut_array)

                self.clut_index_groups[clut_address].extend(face)

            # Flatten all indices to a single buffer, keep per-group offsets
            index_offsets = {}
            group_indices = []
            for clut_address, face_indices in self.clut_index_groups.items():
                face_indices = np.array(face_indices, dtype=np.uint32)
                index_offsets[clut_address] = len(group_indices) * 4  # byte offset
                group_indices.extend(face_indices)

            all_indices = np.array(group_indices, dtype=np.uint32)

            self.index_buffer.create()
            self.index_buffer.bind()
            self.index_buffer.allocate(all_indices.tobytes(), all_indices.nbytes)

            self.index_offsets = index_offsets
            self.index_counts = {k: len(v) for k, v in self.clut_index_groups.items()}

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

            # bind index buffer while VAO is bound
            self.index_buffer.bind()

            self.vao.release()

        except Exception as e:
            print(f"Error preparing buffers: {e}")

    def initializeGL(self):
        """Initialize OpenGL"""
        GL.glClearColor(0.1, 0.1, 0.1, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_CULL_FACE)
        GL.glCullFace(GL.GL_BACK)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

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
                in vec2 fragTexCoord;
                in vec3 fragColor;
                out vec4 outColor;
                
                uniform sampler2D indexTexture;
                uniform sampler1D clutTexture;
                
                void main() {
                    float index = texture(indexTexture, fragTexCoord).r * 15.0;
                    vec4 clutColor = texture(clutTexture, index / 16.0);
                    if (clutColor.a < 0.01)
                        discard;  // <-- throw away fully transparent pixels
                    outColor = clutColor * vec4(fragColor, 1.0);
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
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if not self.model_data:
            print("❌ No model data to draw.")
            return

        # Camera setup
        projection = QMatrix4x4()
        projection.perspective(45.0, self.width() / self.height(), 0.1, 100.0)
        view = QMatrix4x4()
        view.rotate(self.camera_controls.camera_angle_v, 1.0, 0.0, 0.0)
        view.rotate(self.camera_controls.camera_angle_h, 0.0, 1.0, 0.0)
        view.translate(self.camera_controls.camera_x, self.camera_controls.camera_y, self.camera_controls.camera_z)
        model_view_projection = projection * view

        if not self.shader_program.bind():
            print("❌ Shader bind failed.")
            return
        self.shader_program.setUniformValue("modelViewProjection", model_view_projection)

        self.vao.bind()

        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture)
        self.shader_program.setUniformValue("indexTexture", 0)

        index_type = GL.GL_UNSIGNED_INT
        stride = np.uint32().nbytes  # 4 bytes

        # Draw triangles
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_1D, self.clut_tri_tex)
        self.shader_program.setUniformValue("clutTexture", 1)

        # Final per-CLUT draw loop
        try:
            current_tex_id = None

            for clut_address, tex_id in self.clut_map.items():
                if tex_id != current_tex_id:
                    GL.glActiveTexture(GL.GL_TEXTURE1)
                    GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
                    self.shader_program.setUniformValue("clutTexture", 1)
                    current_tex_id = tex_id

                offset = self.index_offsets[clut_address]
                count = self.index_counts[clut_address]

                GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT, ctypes.c_void_p(offset))
        except Exception as e:
            print(f" Draw per-face CLUTs failed: {e}")

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

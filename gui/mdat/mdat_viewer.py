# mdat_viewer.py
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject,
    QOpenGLBuffer
)
from PyQt6.QtGui import QMatrix4x4, QImage, QIcon, QAction
from OpenGL import GL
import gui.mdat.mdat as mdat
from functions import gltf_export
from gui.origin_axes import OriginAxes
from functions.camera_controls import (
    CONTROLS_HINT, LEVEL_HEADING, LEVEL_PITCH, CameraControls,
    CameraEventMixin, scene_of,
)
from gui.scld.scld_parser import load_scld, find_area_scld_location
from gui.scld.scld_render import (
    UNIT_SCALE, SURFACE_LINE_WIDTH, build_points, build_lines, room_bounds,
    entries_in_bounds,
)
import ctypes
from PyQt6.QtWidgets import (
    QMainWindow, QTreeView, QWidget, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QStatusBar, QToolBar, QFileDialog, QMessageBox, QStyle,
)
class MDATViewer(CameraEventMixin, QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_data = None
        # How big the room on screen is, in GL units - the clip planes
        # are set from it, and so is every step the camera takes.
        self.scene_radius = 0.0
        # The world origin - see gui/origin_axes.py.
        self.show_origin = False
        self.origin_axes = OriginAxes()
        self.vao = QOpenGLVertexArrayObject()
        self.vertex_buffer = QOpenGLBuffer()
        self.color_buffer = QOpenGLBuffer()
        self.index_buffer = QOpenGLBuffer()
        self.shader_program = QOpenGLShaderProgram()
        self.texcoord_buffer = QOpenGLBuffer()
        self.vram_texture = None  # OpenGL texture ID
        # The palettes are read straight out of this at export time, and
        # a model can be exported before the view has ever been painted.
        self.vram_raw_bytes = bytearray()
        # Set by MainWindow from the tree row, so a save dialog opens
        # with the file's name in it rather than empty.
        self.export_name = None
        # Initialize the camera controls
        self.camera_controls = CameraControls(self)

        self.collision_data = None
        self.collision_vao = QOpenGLVertexArrayObject()
        self.collision_vbo = QOpenGLBuffer()
        self.collision_cbo = QOpenGLBuffer()
        self.collision_vertex_count = 0
        self.collision_point_vao = QOpenGLVertexArrayObject()
        self.collision_point_vbo = QOpenGLBuffer()
        self.collision_point_cbo = QOpenGLBuffer()
        self.collision_point_count = 0
        self.show_collision = False
        self.clut_quad_tex = None
        self.clut_tri_tex = None
        self.clut_map = {}  # address -> GL texture ID
        self.clut_index_groups = {}  # address -> list of indices
        self.toolbar = QToolBar(self)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.toolbar.setStyleSheet("""
            QToolButton {
                background-color: rgba(255, 255, 255, 128);  /* 50% opaque white */
                color: black;
                border: none;
                padding: 5px;
                margin: 2px;
                border-radius: 4px;
            }
            QToolButton:hover {
                background-color: rgba(255, 255, 255, 180);
            }
        """)

        # Texture mode button
        self.texture_mode_enabled = True  # Default to textured mode
        self.texture_mode_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton), "Texture Mode", self)
        self.texture_mode_action.setCheckable(True)
        self.texture_mode_action.setChecked(True)  # Start as enabled
        self.texture_mode_action.toggled.connect(self.toggle_texture_mode)
        self.toolbar.addAction(self.texture_mode_action)

        # Culling toggle button - on by default, so a room is looked
        # into rather than at the back of its own far wall.
        self.culling_enabled = True  # Track state
        self.culling_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "Backface Culling", self)
        self.culling_action.setCheckable(True)
        self.culling_action.setChecked(True)
        self.culling_action.toggled.connect(self.toggle_culling)
        self.toolbar.addAction(self.culling_action)

        # Collision overlay toggle - off by default, drawn once its SCLD
        # data has been loaded via load_collision_data().
        self.collision_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning), "Show Collision", self)
        self.collision_action.setCheckable(True)
        self.collision_action.setChecked(False)
        self.collision_action.toggled.connect(self.toggle_collision)
        self.toolbar.addAction(self.collision_action)

        frame_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView), "Frame Level", self)
        frame_action.setToolTip("Put the whole room back in shot")
        frame_action.triggered.connect(lambda: self.frame_level())
        self.toolbar.addAction(frame_action)

        self.origin_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
            "Origin", self)
        self.origin_action.setCheckable(True)
        self.origin_action.setChecked(self.show_origin)
        self.origin_action.setToolTip(
            "Mark the world origin: X red, Y green, Z blue, with the "
            "negative half of each axis dimmed. A room and its collision "
            "are placed against this point, so it is where to look when "
            "the two do not line up.")
        self.origin_action.toggled.connect(self.toggle_origin)
        self.toolbar.addAction(self.origin_action)

        # Export button
        self.export_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Export glTF", self)
        self.export_action.triggered.connect(self.export_to_glb)
        self.toolbar.addAction(self.export_action)

        # Stats overlay - tri/quad count (static per model) and live camera
        # position, updated once per frame in paintGL().
        self.stats_label = QLabel(self)
        self.stats_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 128);
                color: white;
                padding: 4px 6px;
                border-radius: 4px;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
        """)
        self.stats_label.raise_()

        # Controls hint overlay - static, bottom-right corner.
        self.controls_label = QLabel(self)
        self.controls_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 128);
                color: white;
                padding: 4px 6px;
                border-radius: 4px;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
        """)
        self.controls_label.setText(CONTROLS_HINT)
        self.controls_label.raise_()

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # No margins
        layout.addWidget(self.toolbar)
        layout.addStretch()

    def export_to_glb(self):
        """Write the level geometry out with its palettes baked in.

        No skeleton here - an MDAT (and the SCLD that reuses this view)
        is scenery, so it exports as one static mesh split by palette."""
        if not self.model_data:
            QMessageBox.warning(self, "Nothing to export", "No model is loaded.")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save model", (self.export_name or "model") + ".glb",
            "glTF binary (*.glb);;glTF (*.gltf)")
        if not file_path:
            return
        try:
            write = (gltf_export.write_gltf if file_path.lower().endswith(".gltf")
                     else gltf_export.write_glb)
            write(file_path, self.model_data, self.vram_raw_bytes,
                  name=self.export_name or "model")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        cluts = len({e[1] for e in self.model_data.get("texture_info") or ()})
        QMessageBox.information(
            self, "Exported", f"Wrote the model and {cluts} baked palette texture(s).")

    def toggle_origin(self, checked):
        self.show_origin = checked
        self.update()

    def toggle_culling(self, checked):
        # Applied in paintGL rather than here: this can be toggled (and
        # is set) before Qt has given the widget a usable context.
        self.culling_enabled = checked
        self.update()

    def toggle_texture_mode(self, checked):
        self.texture_mode_enabled = checked
        self.update()

    def toggle_collision(self, checked):
        self.show_collision = checked
        self.update()

    def load_collision_data(self, dat_file_path, dat_start, offset, size):
        """Load and buffer the SCLD collision data for the area currently
        on screen, so toggling "Show Collision" is instant. Safe to call
        with no matching SCLD (e.g. an area that has none) - just clears
        any previous overlay."""
        self.collision_data = None
        self.collision_vertex_count = 0
        self.collision_point_count = 0
        if dat_start is None:
            self.update()
            return False
        try:
            self.collision_data = load_scld(dat_file_path, dat_start, offset, size)
            self._prepare_collision_buffers()
            self.update()
            return True
        except Exception as e:
            print(f"Error loading collision data: {e}")
            return False

    def _prepare_collision_buffers(self):
        """Build the collision overlay for the room on screen.

        Geometry comes from gui.scld.scld_render, the same builders the
        SCLD viewer uses, so both draw whatever the parser currently
        decodes. Only the room filtering is particular to this viewer:
        one SCLD file covers more world than a single room, and a long
        entry can pass through several, so entries are cut against the
        room's bounds and then their points individually."""
        entries = self.collision_data.entries if self.collision_data else []
        bounds = room_bounds(self.model_data.get("vertices")
                             if self.model_data else None)
        entries = entries_in_bounds(entries, bounds)

        verts, colors = build_lines(self.collision_data, entries,
                                    bounds=bounds)
        point_verts, point_colors, _ranges, _pos = build_points(
            entries, bounds=bounds)

        self.makeCurrent()
        if verts:
            arr = (np.array(verts, dtype=np.float32) / UNIT_SCALE).flatten()
            carr = np.array(colors, dtype=np.float32).flatten()
        else:
            arr = np.zeros(0, dtype=np.float32)
            carr = np.zeros(0, dtype=np.float32)
        self.collision_vertex_count = len(verts)

        if not self.collision_vbo.isCreated():
            self.collision_vbo.create()
        if not self.collision_cbo.isCreated():
            self.collision_cbo.create()
        self.collision_vao.bind()
        self.collision_vbo.bind()
        self.collision_vbo.allocate(arr.tobytes(), arr.nbytes)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.collision_cbo.bind()
        self.collision_cbo.allocate(carr.tobytes(), carr.nbytes)
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.collision_vao.release()

        if point_verts:
            parr = (np.array(point_verts, dtype=np.float32) / UNIT_SCALE).flatten()
            pcarr = np.array(point_colors, dtype=np.float32).flatten()
        else:
            parr = np.zeros(0, dtype=np.float32)
            pcarr = np.zeros(0, dtype=np.float32)
        self.collision_point_count = len(point_verts)

        if not self.collision_point_vbo.isCreated():
            self.collision_point_vbo.create()
        if not self.collision_point_cbo.isCreated():
            self.collision_point_cbo.create()
        self.collision_point_vao.bind()
        self.collision_point_vbo.bind()
        self.collision_point_vbo.allocate(parr.tobytes(), parr.nbytes)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.collision_point_cbo.bind()
        self.collision_point_cbo.allocate(pcarr.tobytes(), pcarr.nbytes)
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.collision_point_vao.release()

    def extract_clut_from_vram(self, clut_address, transparent=False):
        """One 16-colour palette, read the way the hardware reads it.

    The PSX decides transparency per TEXEL, not per polygon. A palette
    entry is 16 bits: five each of B, G, R and, at the top, STP. What
    that bit means depends on the primitive:

      word == 0x0000            never drawn, whatever the primitive is
      STP set, primitive blends blended against what is behind it
      STP set, primitive opaque drawn opaque
      STP clear                 drawn opaque, ALWAYS

    The last line is the one that matters here. A primitive carrying the
    semi-transparency bit does not make the whole polygon see-through -
    it only enables blending for the texels whose palette entry asks for
    it. Every boss pig is built from faces that all carry that bit, and
    their palettes are about 95% STP-clear, so the hardware draws them
    solid; blending the lot made them ghosts. The water pig is the
    exception that proves it - 89% of its entries DO set STP, and it is
    meant to look like water."""
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
            if word == 0:
                A = 0
            elif transparent and (word & 0x8000):
                A = 128
            else:
                A = 255
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
            self.frame_level()
            self._update_stats_label()
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
                [v[0] / UNIT_SCALE, v[1] / UNIT_SCALE, v[2] / UNIT_SCALE]
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
                    _, clut_address, is_transparent = tex_info[0], tex_info[1], tex_info[2]
                    clut_array = self.extract_clut_from_vram(clut_address, is_transparent)
                    #print(f" CLUT 0x{clut_address:X}: {clut_array}")
                    self.clut_map[clut_address] = self.upload_clut(clut_array)
                    if not hasattr(self, 'clut_transparency'):
                        self.clut_transparency = {}
                    self.clut_transparency[clut_address] = is_transparent

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
        #GL.glEnable(GL.GL_CULL_FACE)
        #GL.glCullFace(GL.GL_BACK)
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
                uniform bool useTextures;  // <---- NEW UNIFORM
                uniform float alpha;

                void main() {
                    if (useTextures) {
                        // Round to the whole 4-bit index the atlas
                        // encodes, then read the middle of that palette
                        // entry rather than its edge - same reasoning as
                        // functions.psx_vram.atlas_uv, one level down.
                        float index = floor(texture(indexTexture, fragTexCoord).r * 15.0 + 0.5);
                        vec4 clutColor = texture(clutTexture, (index + 0.5) / 16.0);
                        if (clutColor.a < 0.01)
                            discard;
                        outColor = clutColor * vec4(fragColor, 1.0);
                        outColor.a *= alpha;
                    } else {
                        outColor = vec4(fragColor, alpha);  // Just vertex color
                    }
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

        self.collision_vao.create()
        self.collision_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.collision_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)

        self.collision_point_vao.create()
        self.collision_point_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.collision_point_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)

    def resizeGL(self, w, h):
        """Handle window resize"""
        self.camera_controls.display_center = [w // 2, h // 2]
        GL.glViewport(0, 0, w, h)
        self.stats_label.adjustSize()
        self.stats_label.move(6, h - self.stats_label.height() - 6)
        self.controls_label.adjustSize()
        self.controls_label.move(w - self.controls_label.width() - 6, h - self.controls_label.height() - 6)

    def frame_level(self, heading=LEVEL_HEADING, pitch=LEVEL_PITCH):
        """Open every room with the whole of it in shot, from the angle
        a level reads best at.

        This used to be one fixed position for every file, which suits
        the rooms that happen to be that size and leaves a small one as
        a speck in the corner and a big one running off the screen.
        Rooms on the disc differ by more than an order of magnitude in
        area, so the position is measured from the geometry - which also
        tells the camera how far a step should be (see
        functions/camera_controls.py)."""
        vertices = self.model_data.get("vertices") if self.model_data else None
        scene = scene_of(np.array(vertices, dtype=np.float32) / UNIT_SCALE
                         if vertices else ())
        if scene is None:
            return
        centre, radius = scene
        self.scene_radius = radius
        self.camera_controls.frame(centre, radius, heading, pitch)
        self.update()

    def _update_stats_label(self):
        """Tri/quad count (static per loaded model) plus the live camera
        position - refreshed every frame from paintGL() since the camera
        can move on every mouse/key event."""
        tri_count = self.model_data.get('tri_count', 0) if self.model_data else 0
        quad_count = self.model_data.get('quad_count', 0) if self.model_data else 0
        cam = self.camera_controls
        self.stats_label.setText(
            f"Tris: {tri_count}  Quads: {quad_count}\n" + cam.status_text()
        )
        self.stats_label.adjustSize()
        self.stats_label.move(6, self.height() - self.stats_label.height() - 6)

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if self.culling_enabled:
            GL.glEnable(GL.GL_CULL_FACE)
            GL.glCullFace(GL.GL_BACK)
        else:
            GL.glDisable(GL.GL_CULL_FACE)
        self._update_stats_label()
        if not self.model_data:
            print("❌ No model data to draw.")
            return

        # Camera setup
        # Clip planes off the room's own size: the disc's levels differ
        # by more than an order of magnitude, and a fixed 0.1-100
        # frustum clips the big ones away entirely.
        radius = self.scene_radius or 5.0
        projection = QMatrix4x4()
        projection.perspective(45.0, self.width() / max(self.height(), 1),
                               max(0.01, radius / 500), max(100.0, radius * 10))
        view = QMatrix4x4()
        view.rotate(self.camera_controls.camera_angle_v, 1.0, 0.0, 0.0)
        view.rotate(self.camera_controls.camera_angle_h, 0.0, 1.0, 0.0)
        view.translate(self.camera_controls.camera_x, self.camera_controls.camera_y, self.camera_controls.camera_z)
        model_view_projection = projection * view

        if not self.shader_program.bind():
            print("❌ Shader bind failed.")
            return
        self.shader_program.setUniformValue("modelViewProjection", model_view_projection)
        self.shader_program.setUniformValue("alpha", 1.0)

        self.vao.bind()

        self.shader_program.setUniformValue("useTextures", self.texture_mode_enabled)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture)
        self.shader_program.setUniformValue("indexTexture", 0)

        GL.glActiveTexture(GL.GL_TEXTURE1)
        self.shader_program.setUniformValue("clutTexture", 1)



        # First Pass: Opaque objects
        GL.glDepthMask(GL.GL_TRUE)

        current_tex_id = None
        for clut_address, tex_id in self.clut_map.items():
            if tex_id != current_tex_id:
                GL.glActiveTexture(GL.GL_TEXTURE1)
                GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
                self.shader_program.setUniformValue("clutTexture", 1)
                current_tex_id = tex_id

            offset = self.index_offsets[clut_address]
            count = self.index_counts[clut_address]

            # Check if this CLUT is transparent or not
            # You need to store transparency per CLUT! We'll fix that in a second!

            is_transparent = self.clut_transparency.get(clut_address, False)

            if not is_transparent:
                GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT, ctypes.c_void_p(offset))

        # Second Pass: the semi-transparent faces, added rather than
        # mixed - the PSX's "add" mode, which is what these draw types
        # ask for, and it is B + F.
        #
        # It used to be SRC_ALPHA, ONE, which is B + F/2, because the
        # palette hands a blended texel an alpha of 0.5. That halves the
        # surface's own brightness on top of blending it, and a model
        # built entirely from these faces comes out looking like a
        # ghost - which is exactly what the boss pigs did.
        GL.glDepthMask(GL.GL_FALSE)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE)

        current_tex_id = None
        for clut_address, tex_id in self.clut_map.items():
            if tex_id != current_tex_id:
                GL.glActiveTexture(GL.GL_TEXTURE1)
                GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
                self.shader_program.setUniformValue("clutTexture", 1)
                current_tex_id = tex_id

            offset = self.index_offsets[clut_address]
            count = self.index_counts[clut_address]

            is_transparent = self.clut_transparency.get(clut_address, False)

            if is_transparent:
                GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT, ctypes.c_void_p(offset))

        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)  # Restore normal alpha blending
        GL.glDepthMask(GL.GL_TRUE)  # Restore normal state

        self.vao.release()

        if self.show_collision and (self.collision_vertex_count or self.collision_point_count):
            # Two depth-tested passes instead of one depth-disabled x-ray:
            # collision geometry genuinely sits inside the model (e.g. a
            # spiral staircase inside its tower's solid walls), so the part
            # behind a wall needs to stay visible somehow - full x-ray
            # (depth test off) showed it at the same strength as the part
            # that's actually unobstructed, which reads as noise. Instead,
            # draw normally-depth-tested (GL_LESS) at full opacity for what
            # isn't blocked, then again with the depth test reversed
            # (GL_GREATER) at low alpha for the part a wall would otherwise
            # hide completely - a ghost-through-geometry look rather than
            # true x-ray.
            self.shader_program.setUniformValue("useTextures", False)
            GL.glDepthMask(GL.GL_FALSE)

            def draw_collision():
                if self.collision_vertex_count:
                    GL.glLineWidth(SURFACE_LINE_WIDTH)
                    self.collision_vao.bind()
                    GL.glDrawArrays(GL.GL_LINES, 0, self.collision_vertex_count)
                    self.collision_vao.release()
                if self.collision_point_count:
                    GL.glPointSize(6.0)
                    self.collision_point_vao.bind()
                    GL.glDrawArrays(GL.GL_POINTS, 0, self.collision_point_count)
                    self.collision_point_vao.release()

            self.shader_program.setUniformValue("alpha", 1.0)
            GL.glDepthFunc(GL.GL_LESS)
            draw_collision()

            self.shader_program.setUniformValue("alpha", 0.12)
            GL.glDepthFunc(GL.GL_GREATER)
            draw_collision()

            GL.glDepthFunc(GL.GL_LESS)
            GL.glDepthMask(GL.GL_TRUE)
            self.shader_program.setUniformValue("alpha", 1.0)

        if self.show_origin:
            self.shader_program.setUniformValue("useTextures", False)
            self.shader_program.setUniformValue("alpha", 1.0)
            self.origin_axes.draw(radius)

        self.shader_program.release()


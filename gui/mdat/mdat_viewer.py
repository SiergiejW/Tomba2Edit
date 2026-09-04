# mdat_viewer.py
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject,
    QOpenGLBuffer
)
from PyQt6.QtGui import (
    QMatrix4x4, QImage, QIcon, QAction, QVector2D, QVector4D)
from OpenGL import GL
import gui.mdat.mdat as mdat
from functions import gltf_export
from gui.clut_animation import ClutAnimationMixin
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

# The selection outline: yellow for the selected polygon, a dimmer amber
# for the rest of the drawmap entry it belongs to.
OUTLINE_WIDTH = 2.0
POLYGON_OUTLINE = (1.0, 0.92, 0.15)
ENTRY_OUTLINE = (0.80, 0.50, 0.05)

# How near a click has to land, in pixels, to still count as a click and
# not a camera drag.
CLICK_SLOP = 4


class MDATViewer(ClutAnimationMixin, CameraEventMixin, QOpenGLWidget):
    # (entry index or None, polygon index or None) whenever the
    # selection changes, so a list beside the view can follow a pick
    # made in the view itself.
    selection_changed = pyqtSignal(object, object)

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
        self.clut_transparency = {}  # address -> whether its faces blend

        # Animated textures - see gui/clut_animation.py.
        self.uv_offsets = {}        # CLUT address -> (du, dv) in atlas units
        self.init_clut_animation()

        # What is picked out of the drawmap - an entry, and optionally
        # one polygon inside it. Both are indices into model_data.
        self.selected_entry = None
        self.selected_polygon = None
        self.outline_vao = QOpenGLVertexArrayObject()
        self.outline_vbo = QOpenGLBuffer()
        self.outline_cbo = QOpenGLBuffer()
        self.outline_vertex_count = 0
        self._outline_arrays = None     # built on the CPU, uploaded in paintGL
        self._face_polygon = None       # triangle -> polygon, for picking
        self._pick_vertices = None
        self._pick_faces = None

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

        self.toolbar.addAction(self.make_animate_action())

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

    # --- picking out of the drawmap -----------------------------------

    def select(self, entry=None, polygon=None):
        """Select a drawmap entry, one polygon inside one, or neither.

        Passing a polygon selects its entry too - a polygon is only ever
        looked at as part of the entry it was read from."""
        polygons = (self.model_data or {}).get("polygons") or ()
        if polygon is not None and 0 <= polygon < len(polygons):
            entry = polygons[polygon]["entry"]
        else:
            polygon = None
        entries = (self.model_data or {}).get("entries") or ()
        if entry is not None and not 0 <= entry < len(entries):
            entry = None
        self.selected_entry = entry
        self.selected_polygon = polygon
        self._build_outline()
        self.update()
        self.selection_changed.emit(entry, polygon)

    def selected(self):
        """The selected polygon's record, or None."""
        polygons = (self.model_data or {}).get("polygons") or ()
        if self.selected_polygon is None:
            return None
        return polygons[self.selected_polygon]

    def _build_outline(self):
        """The line segments the selection is drawn with. Left on the
        CPU - a file can be picked in the tree before Qt has given this
        widget a context, and paintGL uploads it."""
        positions, colors = [], []
        model = self.model_data
        entries = (model or {}).get("entries") or ()
        polygons = (model or {}).get("polygons") or ()
        chosen = []
        if self.selected_entry is not None and self.selected_entry < len(entries):
            entry = entries[self.selected_entry]
            for i in range(entry["first_polygon"],
                           entry["first_polygon"] + entry["polygon_count"]):
                if i != self.selected_polygon:
                    chosen.append((polygons[i], ENTRY_OUTLINE))
        if self.selected_polygon is not None and self.selected_polygon < len(polygons):
            chosen.append((polygons[self.selected_polygon], POLYGON_OUTLINE))

        for polygon, color in chosen:
            first, count = polygon["first_vertex"], polygon["vertex_count"]
            ring = model["vertices"][first:first + count]
            for i, point in enumerate(ring):
                nxt = ring[(i + 1) % count]
                positions.extend(point)
                positions.extend(nxt)
                colors.extend(color)
                colors.extend(color)

        self._outline_arrays = (
            np.array(positions, dtype=np.float32) / UNIT_SCALE,
            np.array(colors, dtype=np.float32))

    def _sync_outline(self):
        positions, colors = self._outline_arrays
        self._outline_arrays = None
        self.outline_vertex_count = positions.size // 3
        if not self.outline_vertex_count:
            return
        if not self.outline_vao.isCreated():
            self.outline_vao.create()
        self.outline_vao.bind()
        for buffer, array, location in ((self.outline_vbo, positions, 0),
                                        (self.outline_cbo, colors, 1)):
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            buffer.allocate(array.tobytes(), array.nbytes)
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.outline_vao.release()

    def _model_view_projection(self):
        """The same matrix paintGL draws with, so a click can be turned
        back into a ray through the room."""
        radius = self.scene_radius or 5.0
        projection = QMatrix4x4()
        projection.perspective(45.0, self.width() / max(self.height(), 1),
                               max(0.01, radius / 500), max(100.0, radius * 10))
        view = QMatrix4x4()
        view.rotate(self.camera_controls.camera_angle_v, 1.0, 0.0, 0.0)
        view.rotate(self.camera_controls.camera_angle_h, 0.0, 1.0, 0.0)
        view.translate(self.camera_controls.camera_x,
                       self.camera_controls.camera_y,
                       self.camera_controls.camera_z)
        return projection * view

    def pick(self, x, y):
        """Which polygon is under the widget point, or None.

        Done against the triangles rather than by drawing an id buffer:
        the geometry is already in hand, it needs no context to be
        current, and there is nothing to get wrong about reading pixels
        back out of a framebuffer Qt owns."""
        model = self.model_data
        if not model or not model.get("polygons"):
            return None
        inverse, ok = self._model_view_projection().inverted()
        if not ok:
            return None

        def unproject(z):
            point = inverse.map(QVector4D(
                2.0 * x / max(self.width(), 1) - 1.0,
                1.0 - 2.0 * y / max(self.height(), 1), z, 1.0))
            if not point.w():
                return None
            return np.array([point.x() / point.w(), point.y() / point.w(),
                             point.z() / point.w()], dtype=np.float64)

        near, far = unproject(-1.0), unproject(1.0)
        if near is None or far is None:
            return None
        direction = far - near
        length = np.linalg.norm(direction)
        if length < 1e-9:
            return None
        direction /= length

        if self._face_polygon is None:
            self._build_face_index()
        vertices = self._pick_vertices
        faces = self._pick_faces
        a = vertices[faces[:, 0]]
        edge1 = vertices[faces[:, 1]] - a
        edge2 = vertices[faces[:, 2]] - a
        pvec = np.cross(direction, edge2)
        det = np.einsum("ij,ij->i", edge1, pvec)
        live = np.abs(det) > 1e-12
        if not live.any():
            return None
        inv = np.zeros_like(det)
        inv[live] = 1.0 / det[live]
        tvec = near - a
        u = np.einsum("ij,ij->i", tvec, pvec) * inv
        qvec = np.cross(tvec, edge1)
        v = np.einsum("ij,ij->i", direction, qvec) * inv
        t = np.einsum("ij,ij->i", edge2, qvec) * inv
        hit = live & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6) & (t > 1e-6)
        if not hit.any():
            return None
        return int(self._face_polygon[int(np.argmin(np.where(hit, t, np.inf)))])

    def _build_face_index(self):
        """Arrays the ray test needs, and which triangle belongs to which
        polygon - a quad contributes two."""
        model = self.model_data
        self._pick_vertices = (np.array(model["vertices"], dtype=np.float64)
                               / UNIT_SCALE)
        self._pick_faces = np.array(model["faces"], dtype=np.int64)
        lookup = np.zeros(len(model["faces"]), dtype=np.int64)
        for polygon in model["polygons"]:
            first = polygon["first_face"]
            lookup[first:first + polygon["face_count"]] = polygon["index"]
        self._face_polygon = lookup

    def mousePressEvent(self, event):
        # Ctrl+click picks. Plain left-click is already the camera's own
        # mouse-look toggle (see functions/camera_controls.py), so this
        # takes a modifier rather than the button.
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            point = event.position().toPoint()
            self.select(polygon=self.pick(point.x(), point.y()))
            return
        super().mousePressEvent(event)

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
        # Direct linear address usage (NO x, y calculation here!)
        addr = clut_address  # linear address directly!
        raw = bytes(self.vram_raw_bytes[addr:addr + 32])
        return self.palette_texture(raw, transparent)

    @staticmethod
    def palette_texture(raw, transparent=False):
        """32 raw VRAM bytes as the 16 RGBA entries the shader samples.

        Split out of extract_clut_from_vram() above, which still reads
        the bytes out of VRAM and hands them here: an animated palette's
        bytes come from the area's overlay instead (see
        functions/clut_anim.py) and have to be turned into a texture the
        same way, STP bit and all."""
        clut = []
        for i in range(16):
            at = i * 2
            if at + 1 >= len(raw):
                word = 0
            else:
                word = raw[at] | (raw[at + 1] << 8)
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

    @staticmethod
    def _replace_clut(tex_id, clut_array):
        """Rewrite a palette texture's 16 entries where they stand.

        The draw calls are already grouped and bound by palette (see
        prepare_buffers), so an animation only ever has to change what
        one of those textures holds - no buffer is rebuilt and nothing
        is regrouped to make the water move."""
        GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
        GL.glTexSubImage1D(GL.GL_TEXTURE_1D, 0, 0, 16, GL.GL_RGBA,
                           GL.GL_UNSIGNED_BYTE, clut_array)

    def animated_clut_addresses(self):
        """ClutAnimationMixin's hook - every palette this room draws
        through. The groups are built by prepare_buffers()."""
        return self.clut_map.keys()

    def animation_source(self):
        """ClutAnimationMixin's hook - what to look for UV animations in."""
        return self.vram_raw_bytes, self.model_data

    def apply_uv_offsets(self, offsets):
        """ClutAnimationMixin's hook - shift a group's UVs. Kept as a
        uniform rather than rewritten into the buffer: the draw calls
        are already grouped by palette, so this is one uniform per group
        and no geometry is touched."""
        self.uv_offsets.update(offsets)
        self.update()

    def _bind_uv_offset(self, clut_address):
        du, dv = self.uv_offsets.get(clut_address, (0.0, 0.0))
        self.shader_program.setUniformValue("uvOffset", QVector2D(du, dv))

    def apply_clut_palettes(self, palettes):
        """ClutAnimationMixin's hook - put palettes on screen.

        This view builds its palette textures as it prepares its
        buffers, so a frame can be uploaded straight away rather than
        waiting for the next paint. Nothing but the texture's 16 entries
        changes: the draw calls are already grouped and bound by
        palette, so no buffer is rebuilt to make the water move."""
        if not self.isValid():
            return
        self.makeCurrent()
        for address, raw in palettes:
            tex_id = self.clut_map.get(address)
            if tex_id is None:
                continue
            transparent = self.clut_transparency.get(address, False)
            self._replace_clut(tex_id, self.palette_texture(raw, transparent)
                               if raw is not None
                               else self.extract_clut_from_vram(address, transparent))
        self._update_stats_label()
        self.update()

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
            # A new room means new palette textures, so anything bound
            # to the old ones has to go; load_animations() rebinds once
            # the groups below exist.
            self.clear_clut_animations()
            self.uv_offsets = {}
            # Selections index the old model's arrays, so they go too.
            self.selected_entry = self.selected_polygon = None
            self._face_polygon = None
            self._build_outline()

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
                // Whole frames along the texture page, for the surfaces
                // that animate by UV - see functions/uv_anim.py. Zero
                // for everything else.
                uniform vec2 uvOffset;

                void main() {
                    if (useTextures) {
                        // Round to the whole 4-bit index the atlas
                        // encodes, then read the middle of that palette
                        // entry rather than its edge - same reasoning as
                        // functions.psx_vram.atlas_uv, one level down.
                        float index = floor(texture(indexTexture, fragTexCoord + uvOffset).r * 15.0 + 0.5);
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
        line = f"Tris: {tri_count}  Quads: {quad_count}"
        if self.clut_animations or self.uv_animations:
            drawn = sum(len(self.clut_index_groups.get(a, ())) // 3
                        for a in set(self.clut_animations) | set(self.uv_animations))
            parts = []
            if self.clut_animations:
                parts.append(f"{len(self.clut_animations)} palette(s)")
            if self.uv_animations:
                parts.append(f"{len(self.uv_animations)} UV")
            line += f"  Animated: {', '.join(parts)}, {drawn} tri(s)"
            if self.anim_timer.isActive():
                line += f"  tick {self.anim_tick}"
        self.stats_label.setText(line + "\n" + cam.status_text())
        self.stats_label.adjustSize()
        self.stats_label.move(6, self.height() - self.stats_label.height() - 6)

    def paintGL(self):
        if self._outline_arrays is not None:
            self._sync_outline()
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

            self._bind_uv_offset(clut_address)
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

            self._bind_uv_offset(clut_address)
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

        if self.outline_vertex_count:
            # Over everything, depth test off: a selected polygon is
            # usually the one you cannot see, and an outline that hides
            # behind the wall in front of it is no use for finding it.
            self.shader_program.setUniformValue("useTextures", False)
            self.shader_program.setUniformValue("alpha", 1.0)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glLineWidth(OUTLINE_WIDTH)
            self.outline_vao.bind()
            GL.glDrawArrays(GL.GL_LINES, 0, self.outline_vertex_count)
            self.outline_vao.release()
            GL.glLineWidth(1.0)
            GL.glEnable(GL.GL_DEPTH_TEST)

        if self.show_origin:
            self.shader_program.setUniformValue("useTextures", False)
            self.shader_program.setUniformValue("alpha", 1.0)
            self.origin_axes.draw(radius)

        self.shader_program.release()


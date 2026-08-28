"""SMST viewer - the MDAT 3D view, for a model set instead of a level.

Same shaders, same freecam, same CLUT-per-draw texturing as
gui/mdat/mdat_viewer.py, because the polygons are the same polygons.
What is different is what an SMST is: a list of parts, all of them
modelled around their own origin, so drawing one straight off draws
every part inside every other one (see gui/smst/smst_parser.py). The
two things this view adds are for that - a part list that can hide,
isolate and highlight any of them, and a Spread button that lays them
out on a grid so a model can be looked at part by part.
"""
import ctypes
import math

import numpy as np
from OpenGL import GL
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QImage, QMatrix4x4
from PyQt6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSplitter, QStyle, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from functions.camera_controls import (
    CONTROLS_HINT, MODEL_HEADING, MODEL_PITCH, CameraControls,
    CameraEventMixin, scene_of,
)
from functions.format_detect import FormatError
from gui.mdat.mdat_export import export_mdat_to_gltf
from gui.smst.smst_parser import load_smst

# World units per GL unit. A level MDAT is thousands of units across and
# is drawn at 1000 (gui/scld/scld_render.UNIT_SCALE); a character is
# about 200, so it gets its own scale rather than arriving invisible.
UNIT_SCALE = 100.0

# How far apart the parts sit when spread out, as a multiple of the
# biggest part - enough of a gap to see where one ends.
SPREAD_GAP = 1.35

# What the parts that aren't selected fade to while one is highlighted.
DIMMED_ALPHA = 0.15


class SMSTViewer(CameraEventMixin, QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_data = None
        self.camera_controls = CameraControls(self)

        self.vao = QOpenGLVertexArrayObject()
        self.vertex_buffer = QOpenGLBuffer()
        self.color_buffer = QOpenGLBuffer()
        self.texcoord_buffer = QOpenGLBuffer()
        self.index_buffer = QOpenGLBuffer()
        self.shader_program = QOpenGLShaderProgram()

        self.index_texture = None
        self.vram_raw_bytes = bytearray()
        self.vram_qimage = None
        self.clut_map = {}                  # CLUT address -> GL texture id

        # A QOpenGLWidget has no usable context until Qt has painted it
        # once, and a file can be selected in the tree well before that -
        # so everything here is worked out on the CPU when it's asked
        # for and uploaded from _sync_gl() at the top of paintGL.
        self._arrays = None                 # (positions, colors, texcoords, indices)
        self._clut_arrays = {}              # CLUT address -> 16x4 uint8
        self._geometry_dirty = False
        self._vram_dirty = False

        # One entry per (part, CLUT) run of indices: everything paintGL
        # needs to draw that run and decide whether to draw it at all.
        self.draw_ranges = []
        self.hidden_groups = set()
        self.highlighted_group = None
        # An animation frame's transforms, or None for the rest pose -
        # see set_pose and gui/anmp/anmp_viewer.py.
        self.pose = None
        self.pose_pivots = None
        # On by default: stacked at the origin is how the file has the
        # parts, but it is not how anyone wants to first see a model.
        self.spread = True
        # How big the loaded model is, in GL units - the clip planes are
        # set from it. A character is about 1.5 across and a level asset
        # pack over 200, and a fixed 0.1-100 frustum can only show one
        # of those.
        self.scene_radius = 0.0

        self.texture_mode_enabled = True
        self.culling_enabled = True

        self.toolbar = QToolBar(self)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.toolbar.setStyleSheet("""
            QToolButton {
                background-color: rgba(255, 255, 255, 128);
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

        self.texture_mode_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton),
            "Texture Mode", self)
        self.texture_mode_action.setCheckable(True)
        self.texture_mode_action.setChecked(True)
        self.texture_mode_action.toggled.connect(self.toggle_texture_mode)
        self.toolbar.addAction(self.texture_mode_action)

        self.culling_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            "Backface Culling", self)
        self.culling_action.setCheckable(True)
        self.culling_action.setChecked(self.culling_enabled)
        self.culling_action.toggled.connect(self.toggle_culling)
        self.toolbar.addAction(self.culling_action)

        self.spread_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Spread Parts", self)
        self.spread_action.setCheckable(True)
        self.spread_action.setChecked(self.spread)
        self.spread_action.setToolTip(
            "Lay the parts out on a grid. Nothing in an SMST says where a "
            "part belongs - that is in the animation data - so stacked at "
            "the origin is how the file actually has them.")
        self.spread_action.toggled.connect(self.toggle_spread)
        self.toolbar.addAction(self.spread_action)

        frame_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Frame Model", self)
        frame_action.triggered.connect(self.frame_model)
        self.toolbar.addAction(frame_action)

        export_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            "Export GLTF", self)
        export_action.triggered.connect(self.export_to_gltf)
        self.toolbar.addAction(export_action)

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

        self.controls_label = QLabel(self)
        self.controls_label.setStyleSheet(self.stats_label.styleSheet())
        self.controls_label.setText(CONTROLS_HINT)
        self.controls_label.raise_()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addStretch()

    # --- loading -----------------------------------------------------

    def set_vram(self, vram_bytes, qimage):
        """The VRAM this model's textures are cut out of. Handed the
        area's own VRAM with AREA_01's merged in - a trail model's
        pages are only ever in AREA_01's (see smst_parser)."""
        self.vram_raw_bytes = vram_bytes or bytearray()
        if qimage is not None and qimage.format() != QImage.Format.Format_RGBA8888:
            qimage = qimage.convertToFormat(QImage.Format.Format_RGBA8888)
        self.vram_qimage = qimage
        self._vram_dirty = True
        self.update()

    def load_smst_data(self, dat_file_path, address, size):
        """Load the SMST at `address`. Returns False (having said why)
        rather than raising, so a mislabelled row can't take the window
        down with it."""
        try:
            self.model_data = load_smst(dat_file_path, address, size)
        except (FormatError, OSError, ValueError) as e:
            print(f"Error loading SMST data at 0x{address:X}: {e}")
            self.model_data = None
            self.draw_ranges = []
            self.update()
            return False

        self.hidden_groups = set()
        self.highlighted_group = None
        # An animation frame's transforms, or None for the rest pose -
        # see set_pose and gui/anmp/anmp_viewer.py.
        self.pose = None
        self.pose_pivots = None
        self.prepare_buffers()
        self.frame_model()
        self.update()
        return True

    @property
    def groups(self):
        return self.model_data["groups"] if self.model_data else []

    # --- geometry ----------------------------------------------------

    def _spread_offsets(self):
        """Where each part's centre is moved to when spread out: a
        square grid in reading order, big enough for the biggest part.
        Empty parts take no cell, so a model with a placeholder group in
        the middle doesn't come out with a hole in it."""
        drawn = [g for g in self.groups if not g.empty]
        if not drawn:
            return {}
        # Sized off the 90th percentile rather than the largest part.
        # On a character they're the same number, but an asset pack has
        # a handful of enormous parts (a water surface across a whole
        # room) among small ones, and spacing the grid for those leaves
        # every other part a dot.
        step = max(float(np.percentile([g.radius * 2 for g in drawn], 90))
                   * SPREAD_GAP, 1.0)
        columns = max(1, math.ceil(math.sqrt(len(drawn))))
        rows = math.ceil(len(drawn) / columns)
        offsets = {}
        for n, group in enumerate(drawn):
            cx, cy, cz = group.centre
            col, row = n % columns, n // columns
            offsets[group.index] = (
                (col - (columns - 1) / 2) * step - cx,
                ((rows - 1) / 2 - row) * step - cy,
                -cz,
            )
        return offsets

    def set_pose(self, transforms, pivots):
        """Pose the parts from an animation frame.

        `transforms` is one (rotation, offset) per group, and `pivots`
        the point each group turns about - see gui/anmp/skeleton.py.
        None puts the model back in its rest pose. Spread and pose are
        mutually exclusive: a model laid out on a grid isn't a pose."""
        self.pose = transforms
        self.pose_pivots = pivots
        if transforms is not None and self.spread:
            self.spread_action.setChecked(False)   # rebuilds on its own
            return
        if self.model_data:
            self.prepare_buffers()
        self.update()

    def _positions(self):
        """Every vertex in GL units, with the spread or the pose applied."""
        verts = np.array(self.model_data["vertices"], dtype=np.float32)
        if self.pose is not None:
            for group in self.groups:
                if not group.vertex_count or group.index >= len(self.pose):
                    continue
                rotation, offset = self.pose[group.index]
                pivot = self.pose_pivots[group.index]
                at = group.first_vertex
                block = verts[at:at + group.vertex_count].astype(np.float64)
                verts[at:at + group.vertex_count] = (
                    (block - pivot) @ rotation.T + offset).astype(np.float32)
        elif self.spread:
            offsets = self._spread_offsets()
            for group in self.groups:
                offset = offsets.get(group.index)
                if offset is None or not group.vertex_count:
                    continue
                at = group.first_vertex
                verts[at:at + group.vertex_count] += np.array(offset, dtype=np.float32)
        return verts / UNIT_SCALE

    def prepare_buffers(self):
        """Work the vertex, colour, UV and index arrays out on the CPU.
        Uploading them is _sync_gl()'s job, since there may not be a
        context yet when a file is picked in the tree."""
        self.draw_ranges = []
        self._arrays = None
        self._clut_arrays = {}
        if not self.model_data or not self.model_data.get("vertices"):
            return

        # Indices are grouped by part first and by CLUT within it, so a
        # part is one contiguous stretch of the buffer and hiding it is
        # skipping a few draws rather than rebuilding anything.
        indices = []
        ranges = []
        faces = self.model_data["faces"]
        info = self.model_data["texture_info"]
        for group in self.groups:
            by_clut = {}
            for f in range(group.first_face, group.first_face + group.face_count):
                _page, clut, transparent = info[f]
                by_clut.setdefault(clut, ([], transparent))[0].extend(faces[f])
            for clut, (face_indices, transparent) in by_clut.items():
                ranges.append((group.index, clut, len(indices) * 4,
                               len(face_indices), transparent))
                indices.extend(face_indices)
                if clut not in self._clut_arrays:
                    self._clut_arrays[clut] = self._clut_from_vram(clut, transparent)

        self.draw_ranges = ranges
        self._arrays = (
            self._positions().flatten(),
            np.array(self.model_data["vertex_colors"], dtype=np.float32).flatten(),
            np.array(self.model_data["texture_coords"], dtype=np.float32).flatten(),
            np.array(indices, dtype=np.uint32),
        )
        self._geometry_dirty = True

    def _sync_gl(self):
        """Push whatever changed since the last frame into the context.
        Called from paintGL, which is the first place a QOpenGLWidget's
        context is reliably current."""
        if self._vram_dirty:
            self._vram_dirty = False
            if self.index_texture is not None:
                GL.glDeleteTextures([self.index_texture])
                self.index_texture = None
            if self.vram_qimage is not None:
                ptr = self.vram_qimage.bits()
                ptr.setsize(self.vram_qimage.sizeInBytes())
                self.index_texture = GL.glGenTextures(1)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture)
                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA,
                                self.vram_qimage.width(), self.vram_qimage.height(),
                                0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, ptr.asstring())
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER,
                                   GL.GL_NEAREST)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER,
                                   GL.GL_NEAREST)

        if not self._geometry_dirty:
            return
        self._geometry_dirty = False

        if self.clut_map:
            GL.glDeleteTextures(list(self.clut_map.values()))
        self.clut_map = {address: self._upload_clut(array)
                         for address, array in self._clut_arrays.items()}

        positions, colors, tex_coords, indices = self._arrays
        if not self.index_buffer.isCreated():
            self.index_buffer.create()
        self.index_buffer.bind()
        self.index_buffer.allocate(indices.tobytes(), indices.nbytes)

        self.vao.bind()
        for buffer, array, size, location in (
                (self.vertex_buffer, positions, 3, 0),
                (self.color_buffer, colors, 3, 1),
                (self.texcoord_buffer, tex_coords, 2, 2)):
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            buffer.allocate(array.tobytes(), array.nbytes)
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location, size, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.index_buffer.bind()
        self.vao.release()

    def _clut_from_vram(self, address, transparent=False):
        """One 16-colour palette out of the VRAM, in the same 5-bit
        BGR555 the PSX stores it as - the same read
        MDATViewer.extract_clut_from_vram does."""
        clut = []
        for i in range(16):
            at = address + i * 2
            if at + 1 >= len(self.vram_raw_bytes):
                word = 0
            else:
                word = self.vram_raw_bytes[at] | (self.vram_raw_bytes[at + 1] << 8)
            r = (word & 0x1F) * 8
            g = ((word >> 5) & 0x1F) * 8
            b = ((word >> 10) & 0x1F) * 8
            alpha = 0 if not (r or g or b) else (128 if transparent else 255)
            clut.append([r, g, b, alpha])
        return np.array(clut, dtype=np.uint8)

    @staticmethod
    def _upload_clut(clut_array):
        tex_id = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
        GL.glTexImage1D(GL.GL_TEXTURE_1D, 0, GL.GL_RGBA, 16, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, clut_array)
        GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        return tex_id

    # --- what's on screen --------------------------------------------

    def toggle_texture_mode(self, checked):
        self.texture_mode_enabled = checked
        self.update()

    def toggle_culling(self, checked):
        # Applied in paintGL rather than here, for the same reason the
        # uploads are - there may be no context yet.
        self.culling_enabled = checked
        self.update()

    def toggle_spread(self, checked):
        self.spread = checked
        if self.model_data:
            self.prepare_buffers()
            self.frame_model()
        self.update()

    def set_group_hidden(self, index, hidden):
        if hidden:
            self.hidden_groups.add(index)
        else:
            self.hidden_groups.discard(index)
        self.update()

    def set_hidden_groups(self, hidden):
        self.hidden_groups = set(hidden)
        self.update()

    def set_highlighted_group(self, index):
        self.highlighted_group = index
        self.update()

    def export_to_gltf(self):
        if not self.model_data:
            QMessageBox.warning(self, "Nothing to export", "No SMST is loaded.")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save GLTF file", "", "GLTF Files (*.gltf)")
        if not file_path:
            return
        if export_mdat_to_gltf(self.model_data, self.vram_qimage,
                               self.clut_map, file_path):
            QMessageBox.information(self, "Export Complete",
                                    "Exported model successfully!")
        else:
            QMessageBox.critical(self, "Export Failed", "Failed to export model.")

    # --- camera ------------------------------------------------------

    def frame_model(self, heading=MODEL_HEADING, pitch=MODEL_PITCH):
        """Put the whole model in shot, from the angle a character reads
        best at. Everything the camera does after that - how far a wheel
        notch moves it, how fast WASD flies - is measured off the size
        this finds (see functions/camera_controls.py)."""
        scene = scene_of(self._positions()) if self.model_data else None
        if scene is None:
            return
        centre, radius = scene
        self.scene_radius = radius
        self.camera_controls.frame(centre, radius, heading, pitch)
        self.update()

    # --- GL ----------------------------------------------------------

    def initializeGL(self):
        GL.glClearColor(0.1, 0.1, 0.1, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        self.shader_program = QOpenGLShaderProgram()
        if not self.shader_program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Vertex,
                """
                #version 330 core
                layout(location = 0) in vec3 position;
                layout(location = 1) in vec3 color;
                layout(location = 2) in vec2 texCoord;
                uniform mat4 modelViewProjection;
                out vec3 fragColor;
                out vec2 fragTexCoord;
                void main() {
                    gl_Position = modelViewProjection * vec4(position, 1.0);
                    fragColor = color;
                    fragTexCoord = texCoord;
                }
                """):
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
                uniform bool useTextures;
                uniform float alpha;

                void main() {
                    if (useTextures) {
                        // The atlas holds each 4-bit index as index * 17
                        // over 255, so this comes back a whisker either
                        // side of a whole number - round it to one, then
                        // read the MIDDLE of that palette entry. Sampling
                        // at index / 16.0 is the entry's own edge, and a
                        // whisker short of it is the entry before.
                        float index = floor(texture(indexTexture, fragTexCoord).r * 15.0 + 0.5);
                        vec4 clutColor = texture(clutTexture, (index + 0.5) / 16.0);
                        if (clutColor.a < 0.01)
                            discard;
                        outColor = clutColor * vec4(fragColor, 1.0);
                        outColor.a *= alpha;
                    } else {
                        outColor = vec4(fragColor, alpha);
                    }
                }
                """):
            print("Fragment shader compilation failed:", self.shader_program.log())

        if not self.shader_program.link():
            print("Shader program linking failed:", self.shader_program.log())

        self.vao.create()
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.color_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.texcoord_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.index_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)

    def resizeGL(self, w, h):
        self.camera_controls.display_center = [w // 2, h // 2]
        GL.glViewport(0, 0, w, h)
        self._place_labels()

    def _place_labels(self):
        self.stats_label.adjustSize()
        self.stats_label.move(6, self.height() - self.stats_label.height() - 6)
        self.controls_label.adjustSize()
        self.controls_label.move(self.width() - self.controls_label.width() - 6,
                                 self.height() - self.controls_label.height() - 6)

    def _update_stats_label(self):
        model = self.model_data
        cam = self.camera_controls
        parts = len(self.groups)
        shown = parts - len(self.hidden_groups)
        self.stats_label.setText(
            f"Parts: {shown}/{parts}  Tris: {model.get('tri_count', 0) if model else 0}"
            f"  Quads: {model.get('quad_count', 0) if model else 0}\n"
            + cam.status_text()
        )
        self._place_labels()

    def paintGL(self):
        self._sync_gl()
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if self.culling_enabled:
            GL.glEnable(GL.GL_CULL_FACE)
            GL.glCullFace(GL.GL_BACK)
        else:
            GL.glDisable(GL.GL_CULL_FACE)
        self._update_stats_label()
        if not self.model_data or not self.draw_ranges:
            return

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

        if not self.shader_program.bind():
            return
        self.shader_program.setUniformValue("modelViewProjection", projection * view)

        # No VRAM means no palettes, and an untextured model is far more
        # use than a model that discards every fragment it draws.
        textured = self.texture_mode_enabled and bool(self.vram_raw_bytes)
        self.shader_program.setUniformValue("useTextures", textured)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture or 0)
        self.shader_program.setUniformValue("indexTexture", 0)
        self.shader_program.setUniformValue("clutTexture", 1)

        self.vao.bind()
        self._draw_pass(transparent=False)
        # Semi-transparent primitives add rather than mix on the PSX, so
        # overlapping ones brighten - same second pass the MDAT view does.
        GL.glDepthMask(GL.GL_FALSE)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE)
        self._draw_pass(transparent=True)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glDepthMask(GL.GL_TRUE)
        self.vao.release()
        self.shader_program.release()

    def _draw_pass(self, transparent):
        bound = None
        alpha = None
        for group_index, clut, offset, count, is_transparent in self.draw_ranges:
            if is_transparent != transparent or group_index in self.hidden_groups:
                continue
            want = (DIMMED_ALPHA if self.highlighted_group not in (None, group_index)
                    else 1.0)
            if want != alpha:
                self.shader_program.setUniformValue("alpha", want)
                alpha = want
            tex_id = self.clut_map.get(clut)
            if tex_id and tex_id != bound:
                GL.glActiveTexture(GL.GL_TEXTURE1)
                GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
                bound = tex_id
            GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT,
                              ctypes.c_void_p(offset))



class SMSTPanel(QWidget):
    """Part list beside the 3D view.

    Nothing in the file names a part, so the columns are what can be
    measured - how many polygons it has, how big it is, and where it
    sits in the blob for a hex editor. Ticking a row hides that part,
    selecting one fades the others down, and between them that is how a
    part gets identified as a head or a left hand."""

    def __init__(self, viewer: SMSTViewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self._filling = False

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["Part", "Tris", "Quads", "Size", "Offset", "Extent"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)

        show_all = QPushButton("Show all", self)
        show_all.clicked.connect(self._show_all)
        isolate = QPushButton("Isolate selected", self)
        isolate.clicked.connect(self._isolate_selected)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(show_all)
        buttons.addWidget(isolate)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.table)
        left_layout.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 800])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def populate_table(self):
        self._filling = True
        groups = self.viewer.groups
        self.table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            name = QTableWidgetItem(f"{group.index}" + ("  (empty)" if group.empty else ""))
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(Qt.CheckState.Checked)
            name.setData(Qt.ItemDataRole.UserRole, group.index)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(str(group.tris)))
            self.table.setItem(row, 2, QTableWidgetItem(str(group.quads)))
            self.table.setItem(row, 3, QTableWidgetItem(str(group.size)))
            self.table.setItem(row, 4, QTableWidgetItem(f"0x{group.offset:X}"))
            if group.bounds:
                x0, x1, y0, y1, z0, z1 = group.bounds
                extent = f"{x1 - x0} x {y1 - y0} x {z1 - z0}"
            else:
                extent = "-"
            self.table.setItem(row, 5, QTableWidgetItem(extent))
        self._filling = False
        self.table.clearSelection()
        self.viewer.set_hidden_groups(())
        self.viewer.set_highlighted_group(None)

    def _on_item_changed(self, item):
        if self._filling or item.column() != 0:
            return
        self.viewer.set_group_hidden(item.data(Qt.ItemDataRole.UserRole),
                                     item.checkState() != Qt.CheckState.Checked)

    def _on_selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.viewer.set_highlighted_group(None)
            return
        item = self.table.item(rows[0].row(), 0)
        self.viewer.set_highlighted_group(item.data(Qt.ItemDataRole.UserRole))

    def _show_all(self):
        self._set_checks(lambda _row: True)

    def _isolate_selected(self):
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not rows:
            return
        self._set_checks(lambda row: row in rows)

    def _set_checks(self, keep):
        self._filling = True
        hidden = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            visible = keep(row)
            item.setCheckState(Qt.CheckState.Checked if visible
                               else Qt.CheckState.Unchecked)
            if not visible:
                hidden.add(item.data(Qt.ItemDataRole.UserRole))
        self._filling = False
        self.viewer.set_hidden_groups(hidden)

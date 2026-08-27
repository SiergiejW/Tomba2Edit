# scld_viewer.py
import colorsys
import math
import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject,
    QOpenGLBuffer,
)
from PyQt6.QtGui import QMatrix4x4, QAction, QVector3D, QPainter, QColor, QFont
from OpenGL import GL
from gui.scld.scld_parser import load_scld, SCLDEntry
from gui.scld.scld_render import (
    UNIT_SCALE, SCAFFOLD_ALPHA, build_points, build_lines, unkn_color,
)
from gui.mdat.mdat import exportMDAT, find_area_mdat_location
from functions.camera_controls import CameraControls
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QToolBar, QStyle, QWidget, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QCheckBox,
)


class SCLDViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scld_data = None
        self.show_markers = True
        # Join each walkable surface into a line along the entry - see
        # SCLDEntry.surfaces().
        self.show_surfaces = True
        # Number every record of the selected entry in 3D, so a specific
        # point can be named when comparing against the level.
        self.show_point_ids = False
        # Colour entries by their header's `unkn` value instead of by
        # index, to see whether entries sharing one have anything in
        # common on screen.
        self.color_by_unkn = False
        # entry.index -> [(x, y, z), ...] in record order, for those labels.
        self.entry_record_pos = {}

        # entry.base -> a hand-set direction, for checking one entry
        # against the level by eye. Entries absent from this are left to
        # place themselves from their own header (SCLDEntry.trace()).
        self.reversed_entries = {}

        # entry.index -> (start, count) into the point buffer, so a single
        # entry's points can be redrawn on their own for the highlight pulse.
        self.entry_point_ranges = {}
        self.entry_label_pos = {}
        self.highlighted_entry = None
        self._highlight_phase = 0.0
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setInterval(33)
        self._highlight_timer.timeout.connect(self._tick_highlight)

        self.line_vao = QOpenGLVertexArrayObject()
        self.line_vbo = QOpenGLBuffer()
        self.line_cbo = QOpenGLBuffer()
        self.point_vao = QOpenGLVertexArrayObject()
        self.point_vbo = QOpenGLBuffer()
        self.point_cbo = QOpenGLBuffer()
        self.grid_vao = QOpenGLVertexArrayObject()
        self.grid_vbo = QOpenGLBuffer()
        self.grid_cbo = QOpenGLBuffer()
        self.grid_vertex_count = 0

        self.line_vertex_count = 0
        self.point_vertex_count = 0

        # Untextured MDAT room mesh, shown alongside the collision points
        # for visual reference - see load_level_mesh()/toggle_level().
        self.mesh_vao = QOpenGLVertexArrayObject()
        self.mesh_vbo = QOpenGLBuffer()
        self.mesh_cbo = QOpenGLBuffer()
        self.mesh_ibo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self.mesh_index_count = 0
        self.show_level = False
        self._dat_file_path = None
        self._chunk_index = None
        self._level_loaded_for_chunk = None

        self.shader_program = QOpenGLShaderProgram()
        self.camera_controls = CameraControls(self)

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

        self.markers_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton),
            "Markers", self)
        self.markers_action.setCheckable(True)
        self.markers_action.setChecked(True)
        self.markers_action.toggled.connect(self.toggle_markers)
        self.toolbar.addAction(self.markers_action)

        self.view_level_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "View Level", self)
        self.view_level_action.setCheckable(True)
        self.view_level_action.setChecked(False)
        self.view_level_action.toggled.connect(self.toggle_level)
        self.toolbar.addAction(self.view_level_action)

        self.surfaces_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Surfaces", self)
        self.surfaces_action.setCheckable(True)
        self.surfaces_action.setChecked(True)
        self.surfaces_action.setToolTip(
            "Join each (seg_index, kind) into a line along the entry - the "
            "walkable surfaces - instead of leaving loose points")
        self.surfaces_action.toggled.connect(self.toggle_surfaces)
        self.toolbar.addAction(self.surfaces_action)

        self.unkn_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogHelpButton),
            "Colour by unkn", self)
        self.unkn_action.setCheckable(True)
        self.unkn_action.setChecked(False)
        self.unkn_action.setToolTip(
            "Colour entries by the header's unkn field - entries sharing a "
            "value are drawn alike, and unkn == 0 is grey")
        self.unkn_action.toggled.connect(self.toggle_color_by_unkn)
        self.toolbar.addAction(self.unkn_action)

        self.point_ids_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
            "Point IDs", self)
        self.point_ids_action.setCheckable(True)
        self.point_ids_action.setChecked(False)
        self.point_ids_action.setToolTip(
            "Number every record of the selected entry, so points can be "
            "referred to by index")
        self.point_ids_action.toggled.connect(self.toggle_point_ids)
        self.toolbar.addAction(self.point_ids_action)

        self.group_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView),
            "Group Placement", self)
        self.group_action.setCheckable(True)
        self.group_action.setChecked(SCLDEntry.use_table1_groups)
        self.group_action.setToolTip(
            "Place one station per table1 group (on) instead of one per "
            "table3 record (off, vervalkon's i/N)")
        self.group_action.toggled.connect(self.toggle_group_placement)
        self.toolbar.addAction(self.group_action)

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
        self.controls_label.setText(
            "Left-click: toggle freecam\n"
            "WASD: move | Q/E: up/down\n"
            "Shift: fast | Scroll: speed/zoom"
        )
        self.controls_label.raise_()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addStretch()

    def toggle_markers(self, checked):
        self.show_markers = checked
        self.update()

    def toggle_level(self, checked):
        self.show_level = checked
        if checked and self._level_loaded_for_chunk != self._chunk_index:
            self.load_level_mesh()
        self.update()

    def toggle_surfaces(self, checked):
        self.show_surfaces = checked
        if self.scld_data is not None:
            self.prepare_buffers()
        self.update()

    def toggle_color_by_unkn(self, checked):
        self.color_by_unkn = checked
        if self.scld_data is not None:
            self.prepare_buffers()
        self.update()

    def toggle_point_ids(self, checked):
        self.show_point_ids = checked
        self.update()

    def toggle_group_placement(self, checked):
        SCLDEntry.use_table1_groups = checked
        if self.scld_data is not None:
            self.prepare_buffers()
        self.update()

    def load_level_mesh(self):
        """Load this SCLD's matching MDAT room (no texture, just its
        base shading) so collision points can be checked against real
        level geometry without leaving this viewer."""
        if self._dat_file_path is None or self._chunk_index is None:
            print("No area info for this SCLD - can't find its MDAT room.")
            return
        try:
            import os
            idx_path = os.path.join(os.path.dirname(self._dat_file_path), "TOMBA2.IDX")
            loc = find_area_mdat_location(idx_path, self._chunk_index)
            if not loc:
                print(f"No MDAT found for AREA_{self._chunk_index:02X}")
                return
            dat_start, offset = loc
            model_data = exportMDAT(dat_start + offset, self._dat_file_path)
            self._upload_mesh(model_data)
            self._level_loaded_for_chunk = self._chunk_index
        except Exception as e:
            print(f"Error loading level mesh: {e}")

    def _upload_mesh(self, model_data):
        vertices = model_data.get("vertices") or []
        colors = model_data.get("vertex_colors") or []
        faces = model_data.get("faces") or []
        indices = []
        for face in faces:
            if len(face) == 3:
                indices.extend(face)
            elif len(face) == 4:
                indices.extend((face[0], face[1], face[2]))
                indices.extend((face[0], face[2], face[3]))

        self.makeCurrent()
        varr = (np.array(vertices, dtype=np.float32) / UNIT_SCALE).flatten() if vertices else np.zeros(0, dtype=np.float32)
        carr = np.array(colors, dtype=np.float32).flatten() if colors else np.zeros(0, dtype=np.float32)
        iarr = np.array(indices, dtype=np.uint32) if indices else np.zeros(0, dtype=np.uint32)

        if not self.mesh_vbo.isCreated():
            self.mesh_vbo.create()
        if not self.mesh_cbo.isCreated():
            self.mesh_cbo.create()
        if not self.mesh_ibo.isCreated():
            self.mesh_ibo.create()
        self.mesh_vao.bind()
        self.mesh_vbo.bind()
        self.mesh_vbo.allocate(varr.tobytes(), varr.nbytes)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.mesh_cbo.bind()
        self.mesh_cbo.allocate(carr.tobytes(), carr.nbytes)
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.mesh_ibo.bind()
        self.mesh_ibo.allocate(iarr.tobytes(), iarr.nbytes)
        self.mesh_vao.release()
        self.mesh_index_count = len(indices)

    def set_highlighted_entry(self, entry_index):
        """Pulse one entry's points (alpha oscillating 10%-100%) so it's
        easy to pick out among dozens of same-sized dots. Pass None to
        stop."""
        self.highlighted_entry = entry_index
        if entry_index is None:
            self._highlight_timer.stop()
        else:
            self._highlight_phase = 0.0
            self._highlight_timer.start()
        self.update()

    def _tick_highlight(self):
        self._highlight_phase += 0.12
        self.update()

    def set_entry_reversed(self, entry_base, reversed_):
        """Force this entry's direction, or pass None to hand it back to
        SCLDEntry.auto_reverse."""
        if reversed_ is None:
            self.reversed_entries.pop(entry_base, None)
        else:
            self.reversed_entries[entry_base] = reversed_
        self.prepare_buffers()
        self.update()

    def _reverse_for(self, entry):
        """This entry's manual override, or None to let the parser place
        it from its own header."""
        return self.reversed_entries.get(entry.base)

    def load_scld_data(self, dat_file_path, dat_start, offset, size, chunk_index=None):
        """Parse and load an SCLD blob. Every entry renders as one
        connected line along its branch (see SCLDEntry.trace()).
        `chunk_index` (the area's hex chunk number) is only needed for
        load_level_mesh() to find this area's matching MDAT room."""
        try:
            self.scld_data = load_scld(dat_file_path, dat_start, offset, size)
            self.reversed_entries = {}
            self._dat_file_path = dat_file_path
            self._chunk_index = chunk_index
            if chunk_index != self._level_loaded_for_chunk:
                self.mesh_index_count = 0
                self._level_loaded_for_chunk = None
                if self.show_level:
                    self.load_level_mesh()
            self.prepare_buffers()
            self._reset_camera_to_default()
            self._update_stats_label()
            self.update()
            return True
        except Exception as e:
            print(f"Error loading SCLD data: {e}")
            return False

    def prepare_buffers(self):
        if not self.scld_data:
            return

        self.entry_label_pos = {}
        entries = self.scld_data.entries
        tint = unkn_color if self.color_by_unkn else None
        line_verts, line_colors = build_lines(
            self.scld_data, entries, reverse_for=self._reverse_for,
            surfaces=self.show_surfaces, seams=self.show_surfaces,
            color_by=tint)
        (point_verts, point_colors, self.entry_point_ranges,
         self.entry_record_pos) = build_points(
            entries, reverse_for=self._reverse_for, color_by=tint)
        for index, pts in self.entry_record_pos.items():
            if pts:
                self.entry_label_pos[index] = pts[len(pts) // 2]

        self.makeCurrent()

        if line_verts:
            arr = (np.array(line_verts, dtype=np.float32) / UNIT_SCALE).flatten()
            carr = np.array(line_colors, dtype=np.float32).flatten()
        else:
            arr = np.zeros(0, dtype=np.float32)
            carr = np.zeros(0, dtype=np.float32)
        self.line_vertex_count = len(line_verts)
        self._upload(self.line_vao, self.line_vbo, self.line_cbo, arr, carr)

        if point_verts:
            parr = (np.array(point_verts, dtype=np.float32) / UNIT_SCALE).flatten()
            pcarr = np.array(point_colors, dtype=np.float32).flatten()
        else:
            parr = np.zeros(0, dtype=np.float32)
            pcarr = np.zeros(0, dtype=np.float32)
        self.point_vertex_count = len(point_verts)
        self._upload(self.point_vao, self.point_vbo, self.point_cbo, parr, pcarr)

    def _upload(self, vao, vbo, cbo, vertices, colors):
        if not vbo.isCreated():
            vbo.create()
        if not cbo.isCreated():
            cbo.create()
        vao.bind()
        vbo.bind()
        vbo.allocate(vertices.tobytes(), vertices.nbytes)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        cbo.bind()
        cbo.allocate(colors.tobytes(), colors.nbytes)
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        vao.release()

    def _build_grid(self, half_extent=10, step=1):
        verts = []
        colors = []
        col = (0.35, 0.35, 0.4)
        r = half_extent
        x = -r
        while x <= r:
            verts.append((x, 0.0, -r))
            verts.append((x, 0.0, r))
            colors.append(col)
            colors.append(col)
            x += step
        z = -r
        while z <= r:
            verts.append((-r, 0.0, z))
            verts.append((r, 0.0, z))
            colors.append(col)
            colors.append(col)
            z += step

        self.grid_vertex_count = len(verts)
        arr = np.array(verts, dtype=np.float32).flatten()
        carr = np.array(colors, dtype=np.float32).flatten()
        self._upload(self.grid_vao, self.grid_vbo, self.grid_cbo, arr, carr)

    def initializeGL(self):
        GL.glClearColor(0.08, 0.08, 0.1, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_LINE_SMOOTH)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

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
            print("SCLD vertex shader compilation failed:", self.shader_program.log())

        if not self.shader_program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment,
                """
                #version 330 core
                in vec3 fragColor;
                out vec4 outColor;
                uniform float alpha;
                uniform bool useOverrideColor;
                uniform vec3 overrideColor;
                void main() {
                    vec3 col = useOverrideColor ? overrideColor : fragColor;
                    outColor = vec4(col, alpha);
                }
                """
        ):
            print("SCLD fragment shader compilation failed:", self.shader_program.log())

        if not self.shader_program.link():
            print("SCLD shader program linking failed:", self.shader_program.log())

        for vao, vbo, cbo in (
            (self.line_vao, self.line_vbo, self.line_cbo),
            (self.point_vao, self.point_vbo, self.point_cbo),
            (self.grid_vao, self.grid_vbo, self.grid_cbo),
            (self.mesh_vao, self.mesh_vbo, self.mesh_cbo),
        ):
            vao.create()
        self.line_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.line_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.point_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.point_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.grid_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.grid_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.mesh_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.mesh_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.mesh_ibo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)

        self._build_grid()

        if self.show_level and self._chunk_index is not None:
            self.load_level_mesh()

    def resizeGL(self, w, h):
        self.camera_controls.display_center = [w // 2, h // 2]
        GL.glViewport(0, 0, w, h)
        self.stats_label.adjustSize()
        self.stats_label.move(6, h - self.stats_label.height() - 6)
        self.controls_label.adjustSize()
        self.controls_label.move(w - self.controls_label.width() - 6, h - self.controls_label.height() - 6)

    def _reset_camera_to_default(self):
        """Same fixed starting pose MDATViewer opens every model at, so
        switching between an area's MDAT and SCLD tree items doesn't
        reorient the camera."""
        cam = self.camera_controls
        cam.camera_x = 11.36
        cam.camera_y = -15.70
        cam.camera_z = 4.98
        cam.camera_angle_h = 134.5
        cam.camera_angle_v = 33.2

    def _update_stats_label(self):
        n_entries = len(self.scld_data.entries) if self.scld_data else 0
        n_points = sum(len(e.path) for e in self.scld_data.entries) if self.scld_data else 0
        cam = self.camera_controls
        self.stats_label.setText(
            f"Entries: {n_entries}  Path samples: {n_points}\n"
            f"Camera: {cam.camera_x:.2f}, {cam.camera_y:.2f}, {cam.camera_z:.2f}\n"
            f"Rotation: h {cam.camera_angle_h:.1f}°, v {cam.camera_angle_v:.1f}°"
        )
        self.stats_label.adjustSize()
        self.stats_label.move(6, self.height() - self.stats_label.height() - 6)

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        # Defensive reset: the QPainter used to draw the 3D entry-number
        # label (at the end of the previous frame) does its own GL work
        # and can leave blend/depth/polygon state different from what the
        # raw GL calls below assume - without this, that shows up next
        # frame as the mesh or every collision point rendering wrong.
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LESS)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
        self._update_stats_label()

        projection = QMatrix4x4()
        projection.perspective(45.0, self.width() / max(self.height(), 1), 0.05, 500.0)
        view = QMatrix4x4()
        view.rotate(self.camera_controls.camera_angle_v, 1.0, 0.0, 0.0)
        view.rotate(self.camera_controls.camera_angle_h, 0.0, 1.0, 0.0)
        view.translate(self.camera_controls.camera_x, self.camera_controls.camera_y, self.camera_controls.camera_z)
        mvp = projection * view

        if not self.shader_program.bind():
            return
        self.shader_program.setUniformValue("modelViewProjection", mvp)
        self.shader_program.setUniformValue("alpha", 1.0)
        self.shader_program.setUniformValue("useOverrideColor", False)

        if self.show_level and self.mesh_index_count:
            self.mesh_vao.bind()

            # Flat dark gray fill instead of the mesh's own per-vertex
            # shading - this is reference geometry for checking collision
            # points against, not a textured/lit render.
            self.shader_program.setUniformValue("useOverrideColor", True)
            self.shader_program.setUniformValue("overrideColor", QVector3D(0.34, 0.34, 0.34))
            GL.glDrawElements(GL.GL_TRIANGLES, self.mesh_index_count, GL.GL_UNSIGNED_INT, None)

            # Wireframe overlay: same mesh, line polygon mode, a flat dark
            # color (not the mesh's own shading, or it's invisible against
            # itself) and a slight offset toward the camera so it doesn't
            # z-fight the fill. Every triangle edge - including the second
            # triangle of a quad - gets an outline this way.
            GL.glEnable(GL.GL_POLYGON_OFFSET_LINE)
            GL.glPolygonOffset(-1.0, -1.0)
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE)
            GL.glLineWidth(1.0)
            self.shader_program.setUniformValue("useOverrideColor", True)
            self.shader_program.setUniformValue("overrideColor", QVector3D(0.0, 0.0, 0.0))
            self.shader_program.setUniformValue("alpha", 0.7)
            GL.glDrawElements(GL.GL_TRIANGLES, self.mesh_index_count, GL.GL_UNSIGNED_INT, None)
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
            GL.glDisable(GL.GL_POLYGON_OFFSET_LINE)
            self.shader_program.setUniformValue("useOverrideColor", False)
            self.shader_program.setUniformValue("alpha", 1.0)

            self.mesh_vao.release()

        if self.grid_vertex_count:
            self.grid_vao.bind()
            GL.glDrawArrays(GL.GL_LINES, 0, self.grid_vertex_count)
            self.grid_vao.release()

        if self.line_vertex_count:
            # The surfaces are the thing being read; the records are only
            # scaffolding under them, so the lines get the weight and full
            # opacity and the points are drawn back at SCAFFOLD_ALPHA.
            GL.glLineWidth(2.0)
            self.shader_program.setUniformValue("alpha", 1.0)
            self.line_vao.bind()
            GL.glDrawArrays(GL.GL_LINES, 0, self.line_vertex_count)
            self.line_vao.release()
            GL.glLineWidth(1.0)

        if self.show_markers and self.point_vertex_count:
            highlight_rng = None
            if self.highlighted_entry is not None:
                rng = self.entry_point_ranges.get(self.highlighted_entry)
                if rng and rng[1] > 0:
                    highlight_rng = rng

            def draw_points(alpha_scale):
                alpha_scale *= (SCAFFOLD_ALPHA if self.line_vertex_count else 1.0)
                GL.glPointSize(6.0)
                if highlight_rng is None:
                    self.shader_program.setUniformValue("alpha", alpha_scale)
                    GL.glDrawArrays(GL.GL_POINTS, 0, self.point_vertex_count)
                    return
                # Same points redrawn for the highlighted range - its own
                # opacity pulses, and it's drawn slightly larger, instead
                # of a second point stacked over it.
                start, count = highlight_rng
                self.shader_program.setUniformValue("alpha", alpha_scale)
                if start > 0:
                    GL.glDrawArrays(GL.GL_POINTS, 0, start)
                after = start + count
                if after < self.point_vertex_count:
                    GL.glDrawArrays(GL.GL_POINTS, after, self.point_vertex_count - after)

                pulse = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(self._highlight_phase))
                self.shader_program.setUniformValue("alpha", pulse * alpha_scale)
                GL.glPointSize(9.0)
                GL.glDrawArrays(GL.GL_POINTS, start, count)
                GL.glPointSize(6.0)

            GL.glPointSize(6.0)
            self.point_vao.bind()
            # Two depth-tested passes instead of always-on-top x-ray: full
            # opacity where the level mesh doesn't block a point, and a
            # dim ghost pass (reversed depth test) for points that are
            # actually behind geometry - same technique as MDATViewer's
            # collision overlay.
            GL.glDepthMask(GL.GL_FALSE)
            GL.glDepthFunc(GL.GL_LESS)
            draw_points(1.0)
            GL.glDepthFunc(GL.GL_GREATER)
            draw_points(0.18)
            GL.glDepthFunc(GL.GL_LESS)
            GL.glDepthMask(GL.GL_TRUE)
            self.shader_program.setUniformValue("alpha", 1.0)
            self.point_vao.release()

        self.shader_program.release()

        if self.highlighted_entry is not None:
            self._draw_entry_label(mvp)
            if self.show_point_ids:
                self._draw_point_ids(mvp)

    def _draw_entry_label(self, mvp):
        """Number of the selected entry, placed in 3D at that entry's own
        position (projected through the same MVP used to render it) -
        not a fixed 2D corner overlay."""
        pos = self.entry_label_pos.get(self.highlighted_entry)
        if pos is None:
            return
        ndc = mvp.map(QVector3D(*pos))
        if not (-1.5 < ndc.x() < 1.5 and -1.5 < ndc.y() < 1.5 and -1.0 < ndc.z() < 1.0):
            return
        sx = (ndc.x() * 0.5 + 0.5) * self.width()
        sy = (1.0 - (ndc.y() * 0.5 + 0.5)) * self.height()

        painter = QPainter(self)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setBold(True)
        font.setPointSize(13)
        painter.setFont(font)
        text = str(self.highlighted_entry)
        # thin dark outline so the number reads against any background color
        painter.setPen(QColor(0, 0, 0))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(int(sx) + dx, int(sy) + dy, text)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(int(sx), int(sy), text)
        painter.end()

    def _draw_point_ids(self, mvp):
        """Record index beside every point of the selected entry, so a
        specific one can be named. Only the selected entry is numbered -
        all of them at once is unreadable, and these are the records
        SCLDEntry.trace() returns, in file order."""
        recs = self.entry_record_pos.get(self.highlighted_entry)
        if not recs:
            return
        painter = QPainter(self)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(8)
        painter.setFont(font)
        for i, pos in enumerate(recs):
            ndc = mvp.map(QVector3D(*pos))
            if not (-1.0 < ndc.x() < 1.0 and -1.0 < ndc.y() < 1.0
                    and -1.0 < ndc.z() < 1.0):
                continue
            sx = int((ndc.x() * 0.5 + 0.5) * self.width()) + 5
            sy = int((1.0 - (ndc.y() * 0.5 + 0.5)) * self.height()) - 3
            text = str(i)
            painter.setPen(QColor(0, 0, 0))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(sx + dx, sy + dy, text)
            painter.setPen(QColor(255, 235, 140))
            painter.drawText(sx, sy, text)
        painter.end()

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


class _NoResetTriStateCheckBox(QCheckBox):
    """Tri-state checkbox that only cycles Unchecked -> one of the two
    "checked" states on the first click; after that, clicking only
    toggles between Checked and PartiallyChecked - it never goes back
    to Unchecked once a choice has actually been made."""

    def nextCheckState(self):
        cs = self.checkState()
        if cs == Qt.CheckState.Checked:
            self.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            self.setCheckState(Qt.CheckState.Checked)


class SCLDDebugPanel(QWidget):
    """Debug companion for SCLDViewer: an entry table next to the 3D
    view. Selecting a row pulses that entry's points (10%-100% alpha)
    so it's easy to pick out among however many other entries share the
    screen. Each row is addressable by its `ls_into_le` name (matches
    vervalkon's own OBJ object naming) and its byte offset into the
    SCLD blob, for cross-referencing against a hex editor."""

    def __init__(self, viewer: SCLDViewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["#", "Name (ls_into_le)", "Base", "Points", "unkn", "Rev"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        # entry.base -> Qt.CheckState.value for whichever row was last set;
        # absent means unvisited (unchecked/indeterminate default). Separate
        # from viewer.reversed_entries, which only tracks the render state -
        # this also remembers "checked and confirmed already correct" so it
        # isn't re-litigated next time this file is opened.
        self.verification_state = {}

        self.reverse_checkbox = _NoResetTriStateCheckBox("X/Z direction: not checked yet", self)
        self.reverse_checkbox.setTristate(True)
        self.reverse_checkbox.setEnabled(False)
        self.reverse_checkbox.stateChanged.connect(self._on_reverse_state_changed)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.table)
        left_layout.addWidget(self.reverse_checkbox)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 800])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    _REV_COLUMN_TEXT = {
        Qt.CheckState.Unchecked.value: "",
        Qt.CheckState.Checked.value: "Yes",
        Qt.CheckState.PartiallyChecked.value: "No",
    }
    _CHECKBOX_LABEL = {
        Qt.CheckState.Unchecked.value: "X/Z direction: not checked yet",
        Qt.CheckState.Checked.value: "X/Z direction: REVERSED (checked visually)",
        Qt.CheckState.PartiallyChecked.value: "X/Z direction: unreversed (checked visually)",
    }

    def populate_table(self):
        self.table.blockSignals(True)
        entries = self.viewer.scld_data.entries if self.viewer.scld_data else []
        self.table.setRowCount(len(entries))
        self.verification_state = {}
        for row, e in enumerate(entries):
            name = f"{e.ls:02X}_into_{e.le:02X}"
            index_item = QTableWidgetItem(str(e.index))
            index_item.setData(Qt.ItemDataRole.UserRole, e.index)
            index_item.setData(Qt.ItemDataRole.UserRole + 1, e.base)
            self.table.setItem(row, 0, index_item)
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(f"0x{e.base:X}"))
            self.table.setItem(row, 3, QTableWidgetItem(str(len(e.path))))
            self.table.setItem(row, 4, QTableWidgetItem(f"0x{e.unkn:04X}"))
            self.table.setItem(row, 5, QTableWidgetItem(""))
        self.table.blockSignals(False)
        self.reverse_checkbox.setEnabled(False)
        self.viewer.set_highlighted_entry(None)

    def _on_selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.reverse_checkbox.setEnabled(False)
            self.viewer.set_highlighted_entry(None)
            return
        item = self.table.item(rows[0].row(), 0)
        entry_index = item.data(Qt.ItemDataRole.UserRole)
        entry_base = item.data(Qt.ItemDataRole.UserRole + 1)
        self.viewer.set_highlighted_entry(entry_index)

        state = self.verification_state.get(entry_base, Qt.CheckState.Unchecked.value)
        self.reverse_checkbox.blockSignals(True)
        self.reverse_checkbox.setCheckState(Qt.CheckState(state))
        self.reverse_checkbox.setText(self._CHECKBOX_LABEL[state])
        self.reverse_checkbox.setEnabled(True)
        self.reverse_checkbox.blockSignals(False)

    def _on_reverse_state_changed(self, state):
        state = int(state)
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        entry_base = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)

        self.verification_state[entry_base] = state
        if state == Qt.CheckState.Unchecked.value:
            forced = None
        else:
            forced = state == Qt.CheckState.Checked.value
        self.viewer.set_entry_reversed(entry_base, forced)
        self.table.item(row, 5).setText(self._REV_COLUMN_TEXT[state])
        self.reverse_checkbox.setText(self._CHECKBOX_LABEL[state])

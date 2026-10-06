# scld_viewer.py
import colorsys
import math
import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject,
    QOpenGLBuffer,
)
from PyQt6.QtGui import QMatrix4x4, QAction, QVector3D, QPainter, QColor, QFont
from OpenGL import GL
from formats.collision.scld_parser import load_scld
from formats.collision.scld_render import UNIT_SCALE, build_points, build_lines
from formats.collision.scld_geometry import geometry
from gui import gl_profile
from gui import theme
from gui.widgets import collision_overlay
from gui.widgets.collision_options import CollisionOptions, add_menu_button
from formats.geometry.mdat import exportMDAT, find_area_mdat_location
from gui.widgets.camera_controls import (
    CONTROLS_HINT, LEVEL_HEADING, LEVEL_PITCH, CameraControls,
    CameraEventMixin, navigating, scene_of,
)
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QToolBar, QStyle, QWidget, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QTextBrowser,
)
from formats.models import gltf_export
from gui.widgets.origin_axes import OriginAxes


class SCLDViewer(CameraEventMixin, QOpenGLWidget):
    # A click on a record or lane switch: the entry's index and what the record is.
    record_picked = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scld_data = None
        # How big the collision on screen is, in GL units - the clip
        # planes are set from it, and so is every camera step.
        self.scene_radius = 0.0
        # The world origin - see gui/widgets/origin_axes.py.
        self.show_origin = False
        self.origin_axes = OriginAxes()
        self._scene_points = ()
        # What is drawn and how it is coloured: the menu on the Collision button,
        # shared with the level editor and the MDAT view.
        self.options = CollisionOptions.shared()
        self.options.changed.connect(self._style_changed)
        self.show_collision = True
        self.only_selected = False
        # entry.index -> [(x, y, z), ...] in record order, for those
        # labels, and the table3 record number behind each.
        self.entry_record_pos = {}
        self.entry_record_ids = {}

        # entry.index -> (start, count) into the point buffer, so a single
        # entry's points can be redrawn on their own for the highlight pulse.
        self.entry_point_ranges = {}
        self.entry_label_pos = {}
        self.highlighted_entry = None
        # Entries sharing the selected one's `unkn`, pulsed alongside it.
        self.related_entries = set()
        self._highlight_phase = 0.0
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setInterval(33)
        self._highlight_timer.timeout.connect(self._tick_highlight)

        # The collision itself, drawn the level editor's way.
        self.collision = collision_overlay.Overlay()
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

        # Set by MainWindow from the tree row, so a save dialog opens
        # with the file's name in it rather than empty.
        self.export_name = None

        self.toolbar = QToolBar(self)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.toolbar.setObjectName("viewerToolbar")

        self.collision_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton),
            "Collision", self)
        self.collision_action.setCheckable(True)
        self.collision_action.setChecked(True)
        self.collision_action.setToolTip(
            "Show or hide the collision. The Collision options button beside this "
            "one chooses what it shows (floors, ceilings, sloped faces, walls, lane "
            "switches, plane links, plane lines, the cell grid, record crosses, curtains, "
            "plane numbers) and how it is coloured, and holds the legend. Click a line "
            "for what the record is.")
        self.collision_action.toggled.connect(self.toggle_collision)
        self.toolbar.addAction(self.collision_action)
        add_menu_button(self.toolbar, self.options.menu(self, numbers=True),
                        self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))

        self.only_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Only plane", self)
        self.only_action.setCheckable(True)
        self.only_action.setToolTip(
            "Draw only the plane selected in the table. Its lane switches still "
            "point at the planes they lead to, which stay hidden.")
        self.only_action.toggled.connect(self.toggle_only_selected)
        self.toolbar.addAction(self.only_action)

        self.view_level_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Level", self)
        self.view_level_action.setCheckable(True)
        self.view_level_action.setChecked(False)
        self.view_level_action.setToolTip(
            "Show this area's MDAT room under the collision, flat grey with its "
            "edges, to check the collision against the real geometry. One SCLD "
            "can cover more ground than the one room shown.")
        self.view_level_action.toggled.connect(self.toggle_level)
        self.toolbar.addAction(self.view_level_action)

        frame_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Frame", self)
        frame_action.setToolTip("Put all of this file's collision back in shot, whatever is hidden")
        frame_action.triggered.connect(lambda: self.frame_collision())
        self.toolbar.addAction(frame_action)

        self.origin_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
            "Origin", self)
        self.origin_action.setCheckable(True)
        self.origin_action.setChecked(self.show_origin)
        self.origin_action.setToolTip(
            "Mark the world origin: X red, Y green, Z blue, with the "
            "negative half of each axis dimmed. Collision and the room "
            "it belongs to are placed against this point.")
        self.origin_action.toggled.connect(self.toggle_origin)
        self.toolbar.addAction(self.origin_action)

        self.export_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            "Export glTF", self)
        self.export_action.setToolTip(
            "Write this file's collision out as a glTF: a point per record, "
            "and every line shown here (floors, ceilings, walls, lane switches "
            "and the rest) in the colours they are drawn in.")
        self.export_action.triggered.connect(self.export_to_gltf)
        self.toolbar.addAction(self.export_action)

        self.stats_label = QLabel(self)
        self.stats_label.setObjectName("viewerOverlay")
        self.stats_label.raise_()

        self.controls_label = QLabel(self)
        self.controls_label.setObjectName("viewerOverlay")
        self.controls_label.setText(CONTROLS_HINT)
        self.controls_label.raise_()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addStretch()

    def toggle_origin(self, checked):
        self.show_origin = checked
        self.update()

    def toggle_collision(self, checked):
        self.show_collision = checked
        self.update()

    def toggle_level(self, checked):
        self.show_level = checked
        if checked and self._level_loaded_for_chunk != self._chunk_index:
            self.load_level_mesh()
        self.update()

    def toggle_only_selected(self, checked):
        self.only_selected = checked
        self._apply_plane_filter()

    def _apply_plane_filter(self):
        self.options.set_only_plane(
            self.highlighted_entry if self.only_selected else None)

    def _style_changed(self):
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

    def export_to_gltf(self):
        """Write the collision out, exactly as it is being shown: a point per
        record, and every line drawn."""
        if not self.scld_data:
            QMessageBox.warning(self, "Nothing to export",
                                "No SCLD is loaded.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save collision", (self.export_name or "collision") + ".glb",
            "glTF binary (*.glb)")
        if not path:
            return
        lines = collision_overlay.add_scld(
            collision_overlay.Lines(), self.scld_data.entries, style=self.options.style)
        pts, pt_colors, _r, _p, _ids = build_points(self.scld_data.entries)
        verts = lines.surface + lines.vertical
        colors = lines.surface_colors + lines.vertical_colors
        try:
            # Scaled by the exporter's unit, not this view's. The 3D
            # views do not share one: collision and level geometry are
            # drawn at 1000 because a room is thousands of units across,
            # while a character is drawn at 100. Exports have to agree
            # or a room and its collision land in Blender ten times
            # apart, so everything written out uses the exporter's.
            scale = gltf_export.UNIT_SCALE
            gltf_export.write_lines_glb(
                path,
                np.array(pts, dtype=np.float32) / scale, pt_colors,
                name=self.export_name or "collision",
                mode=gltf_export.POINTS,
                extra=((gltf_export.LINES,
                        np.array(verts, dtype=np.float32) / scale, colors)
                       if verts else None))
        except Exception as e:
            QMessageBox.critical(self, "Export failed",
                                 f"Couldn't write it:\n\n{e}")
            return
        QMessageBox.information(
            self, "Exported",
            f"Wrote {len(pts)} record points and {len(verts) // 2} lines.")

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
        """Pulse one entry's lines (alpha oscillating 10%-100%) so it's
        easy to pick out among dozens of planes.

        Every other entry sharing its non-zero `unkn` pulses with it, so
        whatever a value has in common is visible at once. Pass None to
        stop."""
        self.highlighted_entry = entry_index
        self.related_entries = set()
        if entry_index is not None and self.scld_data:
            by_index = {e.index: e for e in self.scld_data.entries}
            entry = by_index.get(entry_index)
            if entry is not None and entry.unkn:
                self.related_entries = {
                    e.index for e in self.scld_data.entries
                    if e.unkn == entry.unkn and e.index != entry_index}
        if entry_index is None:
            self._highlight_timer.stop()
        else:
            self._highlight_phase = 0.0
            self._highlight_timer.start()
        if self.only_selected:
            self._apply_plane_filter()
        self.update()

    def _tick_highlight(self):
        self._highlight_phase += 0.12
        self.update()

    def keyPressEvent(self, event):
        """F frames what is picked, the way every viewport does."""
        if event.key() == Qt.Key.Key_F and not event.isAutoRepeat():
            self.frame_selection()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        """A click picks the record or lane switch under it: what it is is printed,
        shown in the inspector, and its plane selected in the table.

        With the navigation key held the press is the camera's instead -
        that is how a trackpad orbits - and nothing is picked."""
        if (event.button() == Qt.MouseButton.LeftButton
                and not navigating(event)
                and getattr(self, "_lines", None)):
            point = event.position().toPoint()
            hit = collision_overlay.pick_sample(
                self._lines, self._model_view_projection(), UNIT_SCALE,
                point.x(), point.y(), self.width(), self.height())
            if hit is not None:
                entry, record = hit
                text = collision_overlay.sample_text(entry, record)
                print(f"selected: SCLD @ 0x{getattr(self, '_scld_address', 0):X}  " + text)
                self.record_picked.emit(entry.index, text)
        super().mousePressEvent(event)

    def load_scld_data(self, dat_file_path, dat_start, offset, size, chunk_index=None):
        """Parse and load an SCLD blob. Every record is drawn where its
        cell puts it (see SCLDEntry.trace()). `chunk_index` (the area's
        hex chunk number) is only needed for load_level_mesh() to find
        this area's matching MDAT room."""
        try:
            self.scld_data = load_scld(dat_file_path, dat_start, offset, size)
            self._scld_address = dat_start + offset
            self._dat_file_path = dat_file_path
            self._chunk_index = chunk_index
            if chunk_index != self._level_loaded_for_chunk:
                self.mesh_index_count = 0
                self._level_loaded_for_chunk = None
                if self.show_level:
                    self.load_level_mesh()
            # Framing is the whole file's, whatever is shown or hidden.
            points = np.array(build_points(self.scld_data.entries)[0], dtype=np.float32)
            self._scene_points = (points / UNIT_SCALE).flatten() if len(points) else ()
            self.prepare_buffers()
            self.frame_collision()
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
        lines = collision_overlay.add_scld(
            collision_overlay.Lines(), entries, style=self.options.style)
        self._lines = lines
        self.collision.set(lines, UNIT_SCALE)
        # entry -> its lines in the surface and vertical layers, for the highlight pulse.
        self.entry_point_ranges = lines.ranges
        self.entry_wall_ranges = lines.vranges
        (_points, _colors, _ranges, self.entry_record_pos,
         self.entry_record_ids) = build_points(entries)
        for entry in entries:
            g = geometry(entry)
            self.entry_label_pos[entry.index] = tuple(
                c / UNIT_SCALE for c in (((g.z1 + g.z2) / 2, -g.baseline_y, (g.x1 + g.x2) / 2)))
        self.point_vertex_count = len(lines.surface)
        self.line_vertex_count = len(lines.vertical)

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
            (self.grid_vao, self.grid_vbo, self.grid_cbo),
            (self.mesh_vao, self.mesh_vbo, self.mesh_cbo),
        ):
            vao.create()
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

    def frame_collision(self, heading=LEVEL_HEADING, pitch=LEVEL_PITCH):
        """Open every file with the whole of its collision in shot, from
        the same angle MDATViewer opens a room at - so switching between
        an area's MDAT and SCLD tree items doesn't reorient the camera,
        while a file that covers far more or far less world than the
        last one is still framed for its own size.

        Framed on the points rather than the level mesh: one SCLD covers
        more ground than the single room the mesh is, and it is the
        collision this view is for."""
        scene = scene_of(self._scene_points)
        if scene is None:
            return
        centre, radius = scene
        self.scene_radius = radius
        self.camera_controls.glide_frame(centre, radius, heading, pitch)
        self.update()

    def frame_selection(self):
        """Ease the camera onto the highlighted plane, keeping the angle
        it is looked at from - or onto the whole file when nothing is
        highlighted. What F does in every view."""
        self.camera_controls.glide_to_points(self._selection_points())
        self.update()

    def _selection_points(self):
        """The highlighted entry's collision, in view units, or every
        point of the file when no entry is highlighted.

        The line layers hold the points as they were built, in game
        units, and it is UNIT_SCALE that puts them where the view draws
        them - the same division _scene_points gets."""
        lines = getattr(self, "_lines", None)
        if self.highlighted_entry is None or lines is None:
            return self._scene_points
        points = []
        for ranges, layer in ((self.entry_point_ranges, lines.surface),
                              (self.entry_wall_ranges, lines.vertical)):
            first, count = ranges.get(self.highlighted_entry, (0, 0))
            points.extend(layer[first:first + count])
        if not points:
            return self._scene_points
        return np.array(points, dtype=np.float32) / UNIT_SCALE

    def _update_stats_label(self):
        entries = self.scld_data.entries if self.scld_data else []
        totals = {"floor": 0, "ceiling": 0, "slope": 0, "wall": 0, "junction": 0}
        for e in entries:
            for k, v in geometry(e).counts().items():
                if k in totals:
                    totals[k] += v
        cam = self.camera_controls
        self.stats_label.setText(
            f"Planes: {len(entries)}  Records: {sum(len(e.path) for e in entries)}\n"
            f"Floors {totals['floor']}  Ceilings {totals['ceiling']}  "
            f"Sloped faces {totals['slope']}  Walls {totals['wall']}  "
            f"Lane switches {totals['junction']}\n" + cam.status_text()
        )
        self.stats_label.adjustSize()
        self.stats_label.move(6, self.height() - self.stats_label.height() - 6)

    def _model_view_projection(self):
        # Clip planes off the file's own size - see MDATViewer.paintGL.
        radius = self.scene_radius or 5.0
        projection = QMatrix4x4()
        projection.perspective(45.0, self.width() / max(self.height(), 1),
                               max(0.01, radius / 500), max(500.0, radius * 10))
        view = QMatrix4x4()
        view.rotate(self.camera_controls.camera_angle_v, 1.0, 0.0, 0.0)
        view.rotate(self.camera_controls.camera_angle_h, 0.0, 1.0, 0.0)
        view.translate(self.camera_controls.camera_x, self.camera_controls.camera_y,
                       self.camera_controls.camera_z)
        return projection * view

    def paintGL(self):
        GL.glClearColor(*theme.view_background((0.08, 0.08, 0.1)), 1.0)
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

        mvp = self._model_view_projection()

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
            gl_profile.set_line_width(1.0)
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

        if self.show_collision:
            self.shader_program.setUniformValue("useOverrideColor", False)
            self.collision.draw(self.shader_program)
            if self.highlighted_entry is not None:
                # The selected plane, and any sharing its unkn, again over
                # everything and pulsing, so it can be found among the rest.
                pulse = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(self._highlight_phase))
                GL.glDisable(GL.GL_DEPTH_TEST)
                gl_profile.set_line_width(4.0)
                self.shader_program.setUniformValue("alpha", pulse)
                for index in [self.highlighted_entry] + sorted(self.related_entries):
                    first, count = self.entry_point_ranges.get(index, (0, 0))
                    self.collision.draw_surface(first, count)
                    first, count = self.entry_wall_ranges.get(index, (0, 0))
                    self.collision.draw_surface(first, count, layer=1)
                gl_profile.set_line_width(1.0)
                GL.glEnable(GL.GL_DEPTH_TEST)
                self.shader_program.setUniformValue("alpha", 1.0)

        if self.show_origin:
            self.shader_program.setUniformValue("alpha", 1.0)
            self.shader_program.setUniformValue("useOverrideColor", False)
            self.origin_axes.draw(radius)

        self.shader_program.release()

        if self.show_collision and self.options.style.numbers:
            self._draw_plane_numbers(mvp)
        if self.highlighted_entry is not None:
            self._draw_entry_label(mvp)
            self._draw_point_ids(mvp)

    def _draw_plane_numbers(self, mvp):
        """Every plane's number at its middle, in its own colour - a label is
        dropped when it falls off screen or behind the camera."""
        labels = getattr(getattr(self, "_lines", None), "labels", None)
        if not labels:
            return
        painter = QPainter(self)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setBold(True)
        font.setPointSize(11)
        painter.setFont(font)
        for position, text, rgb in labels[:400]:
            ndc = mvp.map(QVector3D(*(c / UNIT_SCALE for c in position)))
            if not (-1.0 < ndc.x() < 1.0 and -1.0 < ndc.y() < 1.0 and -1.0 < ndc.z() < 1.0):
                continue
            sx = int((ndc.x() * 0.5 + 0.5) * self.width())
            sy = int((1.0 - (ndc.y() * 0.5 + 0.5)) * self.height())
            painter.setPen(QColor(0, 0, 0))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(sx + dx, sy + dy, text)
            painter.setPen(QColor(*(int(c * 255) for c in rgb)))
            painter.drawText(sx, sy, text)
        painter.end()

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
        all of them at once is unreadable. The number is the table3
        record's own, which is not the point's position in the list: a
        record no cell claims is not drawn."""
        recs = self.entry_record_pos.get(self.highlighted_entry)
        if not recs:
            return
        ids = self.entry_record_ids.get(self.highlighted_entry) or []
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
            text = str(ids[i] if i < len(ids) else i)
            painter.setPen(QColor(0, 0, 0))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(sx + dx, sy + dy, text)
            painter.setPen(QColor(255, 235, 140))
            painter.drawText(sx, sy, text)
        painter.end()


class SCLDDebugPanel(QWidget):
    """Plane table and inspector beside the 3D view.

    Selecting a row pulses that plane's lines and numbers its records, along
    with any other plane sharing its `unkn`; the inspector below describes the
    plane (its line, where its ends lead, its cells, its lane switches) and then
    whatever record or switch was clicked in the view. Every column sorts. The
    byte offset into the blob is in the Base column, for cross-referencing
    against a hex editor."""

    HEADERS = ["Plane", "Start \u2192 end leads to", "Base", "Records", "Floors", "Ceil.",
               "Walls", "Switches", "Slope (unkn)"]
    TIPS = ["The plane's number, as ls / le and lane switches name it (the entry index is one less)",
            "What is reached off the plane's first and last end: another plane, or a wall "
            "(0). The header bytes ls_into_le are in the cell's tooltip",
            "Byte offset of this plane in the blob",
            "table3 records: every surface and wall the plane holds",
            "Floor records (kind bit 0)", "Ceiling records (kind bit 1)",
            "Wall records (kind bits 0x04 / 0x08)",
            "Lane switches: Up / Down cells that lead to another plane",
            "The plane's gradient across its shorter axis (unkn, 2.14 fixed point)"]

    def __init__(self, viewer: SCLDViewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer

        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        for column, tip in enumerate(self.TIPS):
            self.table.horizontalHeaderItem(column).setToolTip(tip)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.info = QTextBrowser(self)
        self.info.setOpenLinks(False)
        self.info.setMinimumHeight(120)
        viewer.record_picked.connect(self._record_picked)
        viewer.options.changed.connect(self._show)
        self._record_text = ""

        left = QSplitter(Qt.Orientation.Vertical, self)
        left.addWidget(self.table)
        left.addWidget(self.info)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 800])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def populate_table(self):
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        entries = self.viewer.scld_data.entries if self.viewer.scld_data else []
        self.table.setRowCount(len(entries))

        def ends(n):
            return f"plane {n}" if n else "wall"

        def number(row, column, value):
            item = QTableWidgetItem()
            item.setData(Qt.ItemDataRole.DisplayRole, value)
            self.table.setItem(row, column, item)
            return item

        for row, e in enumerate(entries):
            c = geometry(e).counts()
            index_item = number(row, 0, e.index + 1)
            index_item.setData(Qt.ItemDataRole.UserRole, e.index)
            index_item.setData(Qt.ItemDataRole.UserRole + 1, e.base)
            index_item.setToolTip(f"entry {e.index}")
            leads = QTableWidgetItem(f"{ends(e.ls)} \u2192 {ends(e.le)}")
            leads.setToolTip(f"{e.ls:02X}_into_{e.le:02X}")
            self.table.setItem(row, 1, leads)
            self.table.setItem(row, 2, QTableWidgetItem(f"0x{e.base:X}"))
            number(row, 3, len(e.path))
            number(row, 4, c["floor"])
            number(row, 5, c["ceiling"])
            number(row, 6, c["wall"])
            number(row, 7, c["junction"])
            # The plane's gradient across its shorter axis - `unkn` is it
            # in 2.14 fixed point. Sorts on the raw number.
            unkn_item = QTableWidgetItem(f"{e.slope:+.4f}  (0x{e.unkn:04X})")
            unkn_item.setData(Qt.ItemDataRole.UserRole, e.unkn)
            self.table.setItem(row, 8, unkn_item)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.viewer.set_highlighted_entry(None)
        self._record_text = ""
        self._show()

    def _selected_entry(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((e for e in (self.viewer.scld_data.entries if self.viewer.scld_data else ())
                     if e.index == index), None)

    def _show(self):
        """The inspector: the selected plane, the last record clicked, the legend."""
        import html
        parts = []
        entry = self._selected_entry()
        if entry is not None:
            lines = geometry(entry).describe()
            parts.append("<b>" + html.escape(lines[0]) + "</b><br>"
                         + "<br>".join(html.escape(line) for line in lines[1:]))
        if self._record_text:
            parts.append("<b>Clicked</b><br>" + html.escape(self._record_text))
        if not parts:
            parts.append("Select a plane in the table, or click a line in the view.")
        parts.append("<b>Colours</b><br>" + collision_overlay.legend_html(self.viewer.options.style))
        self.info.setHtml("<hr>".join(parts))

    def _record_picked(self, entry_index, text):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == entry_index:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                break
        # After the selection, which clears what was clicked before.
        self._record_text = text
        self._show()

    def _on_selection_changed(self):
        entry = self._selected_entry()
        if entry is None:
            self.viewer.set_highlighted_entry(None)
            self._show()
            return
        self._record_text = ""
        self.viewer.set_highlighted_entry(entry.index)
        self._show()
        g = geometry(entry)
        kinds = sorted({entry.path[r].kind for r in g.owner})
        print(f"selected: SCLD @ 0x{getattr(self.viewer, '_scld_address', 0):X}  plane {g.number}  "
              f"{entry.ls:02X}_into_{entry.le:02X}  base 0x{entry.base:X}  "
              f"{len(entry.path)} records  slope {entry.slope:+.4f}  "
              f"kinds {', '.join(f'0x{k:X}' for k in kinds[:12])}"
              + (" ..." if len(kinds) > 12 else ""))

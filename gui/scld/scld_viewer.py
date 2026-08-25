# scld_viewer.py
import colorsys
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject,
    QOpenGLBuffer,
)
from PyQt6.QtGui import QMatrix4x4, QAction
from OpenGL import GL
from gui.scld.scld_parser import load_scld
from functions.camera_controls import CameraControls
from PyQt6.QtWidgets import QVBoxLayout, QLabel, QToolBar, QStyle

# Same world-unit scale MDATViewer uses (raw PSX units / 1000), so an SCLD
# path lines up against an MDAT room rendered at the same camera position.
UNIT_SCALE = 1000.0

# Golden-ratio conjugate, used to space entry colors around the hue wheel -
# see the comment where it's used below.
GOLDEN_RATIO_CONJUGATE = 0.6180339887498949


class SCLDViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scld_data = None
        self.show_markers = True

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

    def load_scld_data(self, dat_file_path, dat_start, offset, size):
        """Parse and load an SCLD blob. Every entry renders as one
        connected line along its branch (see SCLDEntry.trace())."""
        try:
            self.scld_data = load_scld(dat_file_path, dat_start, offset, size)
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

        line_verts = []
        line_colors = []
        point_verts = []
        point_colors = []

        entries = self.scld_data.entries
        for entry in entries:
            # golden-ratio hue step: adjacent entries land far apart on the
            # wheel instead of fading into each other like i/n would with
            # dozens of entries - each one reads as its own color.
            hue = (entry.index * GOLDEN_RATIO_CONJUGATE) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.95)
            for run in entry.polylines():
                if len(run) >= 2:
                    for a, b_pt in zip(run, run[1:]):
                        line_verts.append(a)
                        line_verts.append(b_pt)
                        line_colors.append((r, g, b))
                        line_colors.append((r, g, b))
                elif run:
                    point_verts.append(run[0])
                    point_colors.append((r, g, b))

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
                void main() {
                    outColor = vec4(fragColor, 1.0);
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
        ):
            vao.create()
        self.line_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.line_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.point_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.point_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.grid_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.grid_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)

        self._build_grid()

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

        if self.grid_vertex_count:
            self.grid_vao.bind()
            GL.glDrawArrays(GL.GL_LINES, 0, self.grid_vertex_count)
            self.grid_vao.release()

        if self.line_vertex_count:
            GL.glLineWidth(1.0)  # thinnest width most GL drivers support
            self.line_vao.bind()
            GL.glDrawArrays(GL.GL_LINES, 0, self.line_vertex_count)
            self.line_vao.release()

        if self.show_markers and self.point_vertex_count:
            GL.glPointSize(6.0)
            self.point_vao.bind()
            GL.glDrawArrays(GL.GL_POINTS, 0, self.point_vertex_count)
            self.point_vao.release()

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

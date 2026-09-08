"""The level, drawn: background, room, and everything standing in it.

Built on gui/smst/smst_viewer.py rather than beside it. That view
already draws a pile of textured PSX polygons grouped by palette, with
the four blend modes done properly and the animated palettes and UVs
wired in - and a level is the same polygons, so the only things that
differ are what a "group" is (an instance, not a model's part), where
the vertices end up (each instance carries a transform), and the three
things a level has that a model does not:

    the background  the area's BGMP, drawn as a picture behind
                    everything, cycling its palettes if they cycle
    the markers     where an object stands whose model we don't know
    picking         click an instance to select it, drag it to move it

See gui/level/level_scene.py for how the scene is put together, and
functions/placement.py for where the objects' positions come from.
"""
import math

import numpy as np
from OpenGL import GL
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction, QMatrix4x4, QVector2D, QVector3D, QVector4D)
from PyQt6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QStyle

from functions import gltf_export
from functions.camera_controls import CONTROLS_HINT, LEVEL_HEADING, LEVEL_PITCH, scene_of
from gui.smst.smst_viewer import SMSTViewer

# World units per GL unit. A room is thousands of units across, so it
# gets the level scale gui/scld/scld_render.py uses rather than the
# SMST viewer's character-sized one.
UNIT_SCALE = 1000.0

# The selected instance's box, and the marker an unbound object gets.
SELECTION_COLOR = (1.0, 0.92, 0.15)
SELECTION_WIDTH = 2.0
MARKER_WIDTH = 2.0

# How near a click has to land, in pixels, to pick an object that has no
# geometry to hit - a marker is a few lines and a ray rarely meets one.
MARKER_PICK_PIXELS = 18.0

# The vertical field of view every view here draws with. Kept here as
# well as in paintGL's matrix because the background has to be hung at
# the same angles the geometry is seen through.
FIELD_OF_VIEW = 45.0

# How many degrees of looking up and down the background's full height
# covers. With the 45-degree field of view above, a screenful is a
# quarter of the picture, so the horizon sits in the middle of it and
# there is as much sky above as ground below.
BACKGROUND_PITCH_SPAN = 180.0

CONTROLS = ("Left-click: select | Left-drag: move it along the ground\n"
            "Shift+drag: move it up and down\n" + CONTROLS_HINT)


class LevelViewer(SMSTViewer):
    """One area on screen, and what is picked out of it."""

    # The index of the selected instance, or None.
    selection_changed = pyqtSignal(object)
    # An instance that has just been dragged somewhere, so the panel's
    # position boxes can follow it.
    instance_moved = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = None
        self.selected = None

        # An SMST is laid out on a grid because nothing says where its
        # parts go; a level says exactly where everything goes.
        self.spread = False
        self.spread_action.setVisible(False)
        self.export_action.setText("Export level")
        for action in self.toolbar.actions():
            if action.text() == "Frame Model":
                action.setText("Frame Level")
        self.controls_label.setText(CONTROLS)

        self.show_markers = True
        self.marker_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
            "Markers", self)
        self.marker_action.setCheckable(True)
        self.marker_action.setChecked(True)
        self.marker_action.setToolTip(
            "Mark the objects whose model isn't known - the record says "
            "where one stands and which routine runs it, but not what it "
            "is drawn with. See functions/placement.py.")
        self.marker_action.toggled.connect(self._toggle_markers)
        self.toolbar.insertAction(self.export_action, self.marker_action)

        self.show_background = True
        self.background_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon),
            "Background", self)
        self.background_action.setCheckable(True)
        self.background_action.setChecked(True)
        self.background_action.setToolTip(
            "Draw the area's BGMP behind the room.\n\n"
            "It is a tall strip rather than geometry, hung round the "
            "camera: look up and you see the top of it, level and you get "
            "the horizon in the middle, down and you get the ground. It "
            "repeats sideways as you turn, and keeps its own proportions "
            "rather than being stretched to the window.")
        self.background_action.toggled.connect(self._toggle_background)
        self.toolbar.insertAction(self.marker_action, self.background_action)

        self.sprite_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Pickups", self)
        self.sprite_action.setCheckable(True)
        self.sprite_action.setChecked(True)
        self.sprite_action.setToolTip(
            "Draw the crystals and apples.\n\n"
            "They are sprites out of the bank every area shares rather "
            "than models, animated and recoloured per reward the way the "
            "game does it - see gui/level/pickup_sprites.py. Chests are "
            "models and are drawn with the rest of the level.")
        self.sprite_action.toggled.connect(self._toggle_sprites)
        self.toolbar.insertAction(self.background_action, self.sprite_action)

        # Line overlays, both built on the CPU and uploaded from paintGL
        # for the same reason everything else here is: an area can be
        # picked before Qt has given this widget a context.
        self.marker_vao = QOpenGLVertexArrayObject()
        self.marker_vbo = QOpenGLBuffer()
        self.marker_cbo = QOpenGLBuffer()
        self.marker_count = 0
        self._marker_arrays = None
        self.selection_vao = QOpenGLVertexArrayObject()
        self.selection_vbo = QOpenGLBuffer()
        self.selection_cbo = QOpenGLBuffer()
        self.selection_count = 0
        self._selection_arrays = None

        # The background: its own tiny program, since it is an ordinary
        # RGB picture rather than the index-and-palette pair everything
        # else in this view samples.
        self.background_program = None
        self.background_vao = QOpenGLVertexArrayObject()
        self.background_vbo = QOpenGLBuffer()
        self.background_texture = None

        # The pickups drawn as billboards - see gui/level/pickup_sprites.py.
        self.show_sprites = True
        self.sprite_vao = QOpenGLVertexArrayObject()
        self.sprite_vbo = QOpenGLBuffer()      # centres
        self.sprite_obo = QOpenGLBuffer()      # corner offsets
        self.sprite_ubo = QOpenGLBuffer()      # texture coordinates
        self.sprite_texture = None
        self.sprite_count = 0
        self._sprite_atlas = None       # RGBA array, None for none
        self._sprite_atlas_dirty = False
        self._sprite_quads = []         # see set_sprites()
        self._sprite_tick = 0
        self._sprite_dirty = False
        self._background_image = None       # (h, w, 3) uint8, or None
        self._background_dirty = False

        # Picking, and dragging what was picked.
        self._pick_vertices = None
        self._pick_faces = None
        self._face_instance = None
        self._drag = None

    # --- loading ------------------------------------------------------

    def load_scene(self, scene, frame=True):
        """Show a gui.level.level_scene.LevelScene.

        `frame` puts the camera back over the whole level. Off when the
        scene is being rebuilt around a change the user has just made -
        binding an object to a different model rebuilds every array, but
        it is still the same level and they are still looking at the
        part of it they were looking at."""
        self.scene = scene
        self.selected = None
        self.hidden_groups = set()
        self.highlighted_group = None
        self.pose = None
        self.pose_pivots = None
        self.model_data = scene.build() if scene is not None else None
        self._face_instance = None
        # A drag in progress refers to the instance list being replaced,
        # so it cannot survive the reload - dragging on into the new
        # scene indexes a list that may be shorter.
        self._drag = None
        self.prepare_buffers()
        self.rebuild_markers()
        self._build_selection()
        if frame:
            self.frame_level()
        self.update()

    def set_background(self, image):
        """The picture to draw behind the room, as an (h, w, 3) uint8
        array, or None for none."""
        self._background_image = image
        self._background_dirty = True
        self.update()

    def set_sprites(self, atlas, quads):
        """Hang a set of billboards in the level.

        `atlas` is one RGBA array holding every frame, `quads` a list of
        (x, y, z, [Placed per tick-step], ticks per step, loops) in world
        units - what gui/level/pickup_sprites.py builds."""
        self._sprite_atlas = atlas
        self._sprite_atlas_dirty = True
        self._sprite_quads = list(quads)
        self._sprite_tick = 0
        self._sprite_dirty = True
        self.update()

    def advance_sprites(self, ticks=1):
        """Move every pickup's animation on, and redraw if any of them
        actually changed frame."""
        if not self._sprite_quads:
            return
        self._sprite_tick += ticks
        self._sprite_dirty = True
        self.update()

    def _toggle_sprites(self, checked):
        self.show_sprites = checked
        self.update()

    def rebuild_markers(self):
        self._marker_arrays = (self.scene.markers() if self.scene
                               else (np.zeros(0, np.float32),) * 2)
        positions, colors = self._marker_arrays
        self._marker_arrays = (positions / UNIT_SCALE, colors)

    # --- geometry -----------------------------------------------------

    def _positions(self):
        """Every vertex in GL units, with each instance's transform on
        it. Replaces the SMST viewer's spread/pose - a level says where
        its parts go, so there is nothing to lay out or animate here."""
        if self.scene is None or not self.model_data:
            return np.zeros((0, 3), dtype=np.float32)
        return self.scene.positions(self.model_data) / UNIT_SCALE

    def refresh_instance(self, index=None):
        """Rebuild after an instance has been moved - the cheap path, the
        same one a pose takes in the SMST viewer: same mesh, moved."""
        self.refresh_positions()
        self.rebuild_markers()
        self._build_selection()
        self._face_instance = None
        self.update()

    def frame_level(self):
        scene = scene_of(self._positions())
        if scene is None:
            return
        centre, radius = scene
        self.scene_radius = radius
        self.camera_controls.frame(centre, radius, LEVEL_HEADING, LEVEL_PITCH)
        self.update()

    def frame_model(self, *_args, **_kwargs):
        """The toolbar's Frame button, which on a level frames the level."""
        self.frame_level()

    # --- selection ----------------------------------------------------

    @property
    def instances(self):
        return (self.model_data or {}).get("groups") or ()

    def select(self, index):
        if index is not None and not 0 <= index < len(self.instances):
            index = None
        self.selected = index
        self._build_selection()
        self.update()
        self.selection_changed.emit(index)

    def _build_selection(self):
        """A box round the selected instance. Drawn over everything: the
        thing you are looking for is usually the one behind a wall."""
        positions, colors = [], []
        instance = (self.instances[self.selected]
                    if self.selected is not None
                    and self.selected < len(self.instances) else None)
        if instance is not None:
            box = self._instance_box(instance)
            if box is not None:
                (x0, x1, y0, y1, z0, z1) = box
                corners = [(x, y, z) for x in (x0, x1)
                           for y in (y0, y1) for z in (z0, z1)]
                edges = [(0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
                         (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7)]
                for a, b in edges:
                    positions.extend(corners[a])
                    positions.extend(corners[b])
                    colors.extend(SELECTION_COLOR)
                    colors.extend(SELECTION_COLOR)
        self._selection_arrays = (
            np.array(positions, dtype=np.float32) / UNIT_SCALE,
            np.array(colors, dtype=np.float32))

    def _instance_box(self, instance):
        """(x0, x1, y0, y1, z0, z1) round an instance in world units, or
        None. An object with no geometry gets a box round its marker."""
        if instance.role == "room":
            return None
        if instance.vertex_count:
            verts = self._positions()[
                instance.first_vertex:
                instance.first_vertex + instance.vertex_count] * UNIT_SCALE
            low, high = verts.min(axis=0), verts.max(axis=0)
            pad = max(20.0, float(np.max(high - low)) * 0.06)
            return (low[0] - pad, high[0] + pad, low[1] - pad, high[1] + pad,
                    low[2] - pad, high[2] + pad)
        r = 110.0
        return (instance.x - r, instance.x + r, instance.y - r,
                instance.y + r * 2, instance.z - r, instance.z + r)

    # --- picking ------------------------------------------------------

    def _model_view_projection(self):
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

    def _ray(self, x, y):
        """(origin, unit direction) through a widget point, in GL units,
        or None. Same unprojection MDATViewer.pick does."""
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
        return near, direction / length

    def _invalidate_pick_cache(self):
        """Clear the face->instance map along with the vertex arrays.

        SMSTViewer.prepare_buffers() invalidates the arrays the ray test
        uses; this class derives one more thing from them, and pick()
        rebuilds on _face_instance alone. Without this override the
        arrays go to None while _face_instance stays set, so the rebuild
        is skipped and the ray test subscripts None."""
        super()._invalidate_pick_cache()
        self._face_instance = None

    def _build_face_index(self):
        """Which instance each triangle belongs to, and the arrays the
        ray test needs."""
        self._pick_vertices = self._positions().astype(np.float64)
        self._pick_faces = np.array(self.model_data["faces"], dtype=np.int64)
        lookup = np.zeros(len(self._pick_faces), dtype=np.int64)
        for instance in self.instances:
            lookup[instance.first_face:
                   instance.first_face + instance.face_count] = instance.index
        self._face_instance = lookup

    def pick(self, x, y):
        """Which instance is under the widget point, or None.

        Two passes. The geometry is tested first, against the triangles
        rather than by reading an id buffer back - the same reasoning as
        MDATViewer.pick. Anything an object with no model is drawn with
        is a handful of lines that a ray will not meet, so those are
        picked by how near the click lands to where they stand."""
        if not self.model_data or not self.instances:
            return None
        ray = self._ray(x, y)
        if ray is None:
            return None
        origin, direction = ray

        hit_instance, hit_distance = None, np.inf
        if len(self.model_data.get("faces") or ()):
            if self._face_instance is None or self._pick_vertices is None:
                self._build_face_index()
            vertices, faces = self._pick_vertices, self._pick_faces
            a = vertices[faces[:, 0]]
            edge1 = vertices[faces[:, 1]] - a
            edge2 = vertices[faces[:, 2]] - a
            pvec = np.cross(direction, edge2)
            det = np.einsum("ij,ij->i", edge1, pvec)
            live = np.abs(det) > 1e-12
            inv = np.zeros_like(det)
            inv[live] = 1.0 / det[live]
            tvec = origin - a
            u = np.einsum("ij,ij->i", tvec, pvec) * inv
            qvec = np.cross(tvec, edge1)
            v = np.einsum("j,ij->i", direction, qvec) * inv
            t = np.einsum("ij,ij->i", edge2, qvec) * inv
            hit = (live & (u >= -1e-6) & (v >= -1e-6)
                   & (u + v <= 1 + 1e-6) & (t > 1e-6))
            if hit.any():
                which = int(np.argmin(np.where(hit, t, np.inf)))
                hit_instance = int(self._face_instance[which])
                hit_distance = float(t[which])

        near = self._pick_marker(x, y, origin, direction)
        if near is not None:
            index, distance = near
            # A marker in front of whatever the ray hit wins; one behind
            # it is something else's, standing further away.
            if hit_instance is None or distance < hit_distance:
                return index
        return hit_instance

    def _pick_marker(self, x, y, origin, direction):
        """(instance, distance along the ray) for the nearest marker the
        click landed on, or None."""
        matrix = self._model_view_projection()
        best = None
        for instance in self.instances:
            if not instance.movable or instance.face_count:
                continue
            point = matrix.map(QVector4D(instance.x / UNIT_SCALE,
                                         instance.y / UNIT_SCALE,
                                         instance.z / UNIT_SCALE, 1.0))
            if point.w() <= 0:
                continue
            sx = (point.x() / point.w() * 0.5 + 0.5) * self.width()
            sy = (0.5 - point.y() / point.w() * 0.5) * self.height()
            if math.hypot(sx - x, sy - y) > MARKER_PICK_PIXELS:
                continue
            here = np.array([instance.x, instance.y, instance.z],
                            dtype=np.float64) / UNIT_SCALE
            distance = float(np.dot(here - origin, direction))
            if best is None or distance < best[1]:
                best = (instance.index, distance)
        return best

    # --- moving what is picked ----------------------------------------

    def _plane_point(self, x, y, anchor, normal):
        """Where the ray through a widget point meets a plane through
        `anchor`, in world units, or None."""
        ray = self._ray(x, y)
        if ray is None:
            return None
        origin, direction = ray
        denominator = float(np.dot(direction, normal))
        if abs(denominator) < 1e-6:
            return None
        anchor = np.asarray(anchor, dtype=np.float64) / UNIT_SCALE
        t = float(np.dot(anchor - origin, normal)) / denominator
        if t <= 0:
            return None
        return (origin + direction * t) * UNIT_SCALE

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position().toPoint()
        index = self.pick(point.x(), point.y())
        if index != self.selected:
            self.select(index)
        instance = self.instances[index] if index is not None else None
        if instance is not None and instance.movable and not instance.authored:
            # Along the ground, or up and down with Shift - a level's
            # objects stand on it, so the ground is what a drag means
            # nearly every time.
            up = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            normal = (self._camera_plane_normal() if up
                      else np.array([0.0, 1.0, 0.0]))
            anchor = (instance.x, instance.y, instance.z)
            grab = self._plane_point(point.x(), point.y(), anchor, normal)
            if grab is not None:
                self._drag = (index, normal, np.asarray(anchor, dtype=np.float64)
                              - grab, bool(up))

    def _camera_plane_normal(self):
        """A plane facing the camera, for dragging up and down: upright,
        so the drag stays vertical, and turned to face where the camera
        is looking so it never goes edge-on."""
        heading = math.radians(self.camera_controls.camera_angle_h)
        return np.array([math.sin(heading), 0.0, -math.cos(heading)])

    def mouseMoveEvent(self, event):
        if self._drag is None:
            super().mouseMoveEvent(event)
            return
        index, normal, offset, vertical = self._drag
        if index >= len(self.instances):
            self._drag = None
            return
        instance = self.instances[index]
        point = event.position().toPoint()
        where = self._plane_point(point.x(), point.y(),
                                  (instance.x, instance.y, instance.z), normal)
        if where is None:
            return
        where = where + offset
        if vertical:
            instance.y = float(where[1])
        else:
            instance.x, instance.z = float(where[0]), float(where[2])
        instance.to_record()
        self.refresh_instance(index)
        self.instance_moved.emit(index)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag = None
            return
        super().mouseReleaseEvent(event)

    # --- what the toolbar toggles -------------------------------------

    def _toggle_markers(self, checked):
        self.show_markers = checked
        self.update()

    def _toggle_background(self, checked):
        self.show_background = checked
        self.update()

    def export_to_gltf(self):
        """Write the level out with everything standing where it does.

        The scene's own arrays hold each instance's geometry as it was
        modelled; what goes out is a copy with the transforms baked in,
        which is the thing on screen."""
        if not self.model_data or not self.model_data.get("vertices"):
            QMessageBox.warning(self, "Nothing to export", "No level is loaded.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save level", (self.export_name or "level") + ".glb",
            "glTF binary (*.glb);;glTF (*.gltf)")
        if not path:
            return
        placed = dict(self.model_data)
        placed["vertices"] = (self._positions() * UNIT_SCALE).tolist()
        placed.pop("groups", None)
        try:
            write = (gltf_export.write_gltf if path.lower().endswith(".gltf")
                     else gltf_export.write_glb)
            write(path, placed, self.vram_raw_bytes,
                  name=self.export_name or "level")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        QMessageBox.information(
            self, "Exported",
            f"Wrote the room and {sum(1 for i in self.instances if i.face_count) - 1} "
            f"placed object(s).")

    # --- GL -----------------------------------------------------------

    def initializeGL(self):
        super().initializeGL()
        self.marker_vao.create()
        self.marker_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.marker_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.selection_vao.create()
        self.selection_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.selection_cbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)

        self.background_program = QOpenGLShaderProgram()
        self.background_program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex,
            """
            #version 330 core
            layout(location = 0) in vec2 corner;
            out vec2 screen;
            void main() {
                screen = corner;
                gl_Position = vec4(corner, 0.0, 1.0);
            }
            """)
        self.background_program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment,
            """
            #version 330 core
            in vec2 screen;
            out vec4 outColor;
            uniform sampler2D picture;
            // tan(half the field of view), across and up, so a screen
            // position can be turned back into the angle it looks along.
            uniform vec2 halfFov;
            // Where the camera is pointing, in degrees.
            uniform vec2 look;
            // How many degrees the picture covers, across and up.
            uniform vec2 span;

            void main() {
                // The angle this pixel looks along, which is what
                // decides where in the picture it lands: up at the top
                // of it, down at the bottom, and round it as you turn.
                //
                // Both terms are subtracted. The view matrix rotates the
                // world by +h about Y, which puts a point at world
                // azimuth t on screen at s = -(t + h - 180): so the
                // azimuth under a pixel is t = 180 - h - s, and the
                // picture has to run the same way round in BOTH terms.
                // Adding the screen term came out mirrored.
                float yaw = look.x - degrees(atan(screen.x * halfFov.x));
                float pitch = look.y - degrees(atan(screen.y * halfFov.y));
                vec2 uv = vec2(yaw / span.x, 0.5 + pitch / span.y);
                outColor = vec4(texture(picture, uv).rgb, 1.0);
            }
            """)
        if not self.background_program.link():
            print("Background shader failed:", self.background_program.log())
        self.background_vao.create()
        self.background_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        corners = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype=np.float32)
        self.background_vao.bind()
        self.background_vbo.create()
        self.background_vbo.bind()
        self.background_vbo.allocate(corners.tobytes(), corners.nbytes)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.background_vao.release()

        self.sprite_program = QOpenGLShaderProgram()
        self.sprite_program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex,
            """
            #version 330 core
            layout(location = 0) in vec3 centre;
            layout(location = 1) in vec2 offset;
            layout(location = 2) in vec2 corner;
            out vec2 uv;
            uniform mat4 modelViewProjection;
            // The camera's own axes in world space, so a quad can be
            // turned to face it without a matrix per sprite.
            uniform vec3 right;
            uniform vec3 up;
            void main() {
                uv = corner;
                vec3 at = centre + right * offset.x + up * offset.y;
                gl_Position = modelViewProjection * vec4(at, 1.0);
            }
            """)
        self.sprite_program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment,
            """
            #version 330 core
            in vec2 uv;
            out vec4 outColor;
            uniform sampler2D atlas;
            void main() {
                vec4 texel = texture(atlas, uv);
                // A sprite is a cutout, not a blend: the PSX draws these
                // with the transparent index simply not written, so a
                // hard test keeps the edges crisp and lets the depth
                // buffer sort them against the room.
                if (texel.a < 0.5) discard;
                outColor = vec4(texel.rgb, 1.0);
            }
            """)
        if not self.sprite_program.link():
            print("Sprite shader failed:", self.sprite_program.log())
        self.sprite_vao.create()

    def _sync_sprite_atlas(self):
        if not self._sprite_atlas_dirty:
            return
        self._sprite_atlas_dirty = False
        if self.sprite_texture is not None:
            GL.glDeleteTextures([self.sprite_texture])
            self.sprite_texture = None
        atlas = self._sprite_atlas
        if atlas is None:
            return
        height, width = atlas.shape[:2]
        self.sprite_texture = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.sprite_texture)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, width, height, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE,
                        np.ascontiguousarray(atlas).tobytes())
        for name, value in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST),
                            (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
                            (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE),
                            (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, name, value)

    def _sync_sprites(self):
        """Lay this tick's frame of every pickup out as two triangles."""
        self._sync_sprite_atlas()
        if not self._sprite_dirty:
            return
        self._sprite_dirty = False
        rows = []
        for quad in self._sprite_quads:
            placed = quad.frame_now(self._sprite_tick)
            if placed is None:
                continue
            x, y, z = quad.x / UNIT_SCALE, quad.y / UNIT_SCALE, quad.z / UNIT_SCALE
            units = quad.units / UNIT_SCALE
            left = -placed.origin_x * units
            right = (placed.width - placed.origin_x) * units
            top = placed.origin_y * units
            bottom = (placed.origin_y - placed.height) * units
            # Counter-clockwise seen from the camera, which is what GL
            # calls front-facing - wound the other way they are back
            # faces and vanish the moment culling is on.
            for ox, oy, u, v in ((left, top, placed.u0, placed.v0),
                                 (left, bottom, placed.u0, placed.v1),
                                 (right, bottom, placed.u1, placed.v1),
                                 (left, top, placed.u0, placed.v0),
                                 (right, bottom, placed.u1, placed.v1),
                                 (right, top, placed.u1, placed.v0)):
                rows.append((x, y, z, ox, oy, u, v))
        if not rows:
            self.sprite_count = 0
            return
        data = np.array(rows, dtype=np.float32)
        if not self.sprite_vao.isCreated():
            self.sprite_vao.create()
        self.sprite_vao.bind()
        for buffer, columns, location in ((self.sprite_vbo, (0, 3), 0),
                                          (self.sprite_obo, (3, 5), 1),
                                          (self.sprite_ubo, (5, 7), 2)):
            first, last = columns
            part = np.ascontiguousarray(data[:, first:last])
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            buffer.allocate(part.tobytes(), part.nbytes)
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location, last - first, GL.GL_FLOAT,
                                     GL.GL_FALSE, 0, None)
        self.sprite_vao.release()
        self.sprite_count = len(rows)

    def _camera_axes(self):
        """The camera's right and up, in world space.

        The view turns the world by the pitch about X and then the
        heading about Y, so its rows are where those axes point."""
        h = math.radians(self.camera_controls.camera_angle_h)
        v = math.radians(self.camera_controls.camera_angle_v)
        right = (math.cos(h), 0.0, math.sin(h))
        up = (math.sin(v) * math.sin(h), math.cos(v),
              -math.sin(v) * math.cos(h))
        return right, up

    def draw_sprites(self):
        """The pickups, after the room so they sort against it."""
        self._sync_sprites()
        if not (self.show_sprites and self.sprite_count
                and self.sprite_texture is not None):
            return
        if not self.sprite_program.bind():
            return
        # A billboard is a picture, not a surface: it has no back to
        # cull, and it is turned to the camera every frame anyway.
        culling = GL.glIsEnabled(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_CULL_FACE)
        right, up = self._camera_axes()
        self.sprite_program.setUniformValue("modelViewProjection",
                                            self._model_view_projection())
        self.sprite_program.setUniformValue("right", QVector3D(*right))
        self.sprite_program.setUniformValue("up", QVector3D(*up))
        self.sprite_program.setUniformValue("atlas", 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.sprite_texture)
        self.sprite_vao.bind()
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, self.sprite_count)
        self.sprite_vao.release()
        self.sprite_program.release()
        if culling:
            GL.glEnable(GL.GL_CULL_FACE)

    @staticmethod
    def _upload_lines(arrays, vao, vbo, cbo):
        """Put one line overlay into its VAO, and say how many vertices
        it holds."""
        positions, colors = arrays
        if not vao.isCreated():
            vao.create()
        vao.bind()
        for buffer, array, location in ((vbo, positions, 0), (cbo, colors, 1)):
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            buffer.allocate(array.tobytes(), array.nbytes)
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location, 3, GL.GL_FLOAT, GL.GL_FALSE,
                                     0, None)
        vao.release()
        return positions.size // 3

    def _sync_lines(self):
        if self._marker_arrays is not None:
            self.marker_count = self._upload_lines(
                self._marker_arrays, self.marker_vao, self.marker_vbo,
                self.marker_cbo)
            self._marker_arrays = None
        if self._selection_arrays is not None:
            self.selection_count = self._upload_lines(
                self._selection_arrays, self.selection_vao, self.selection_vbo,
                self.selection_cbo)
            self._selection_arrays = None

    def _sync_background(self):
        if not self._background_dirty:
            return
        self._background_dirty = False
        if self.background_texture is not None:
            GL.glDeleteTextures([self.background_texture])
            self.background_texture = None
        image = self._background_image
        if image is None:
            return
        height, width = image.shape[:2]
        self.background_texture = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.background_texture)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB, width, height, 0,
                        GL.GL_RGB, GL.GL_UNSIGNED_BYTE,
                        np.ascontiguousarray(image).tobytes())
        for name, value in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR),
                            (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                            # Repeats sideways as the camera turns;
                            # clamped up and down, since the sky does
                            # not start again below the ground.
                            (GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT),
                            (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, name, value)

    def draw_backdrop(self):
        """The SMST viewer's hook: the area's background, drawn flat
        across the view after the clear and before anything else."""
        self._sync_background()
        if not self.show_background or self.background_texture is None:
            # Nothing behind the level, so leave it black rather than
            # the model viewer's grey: an area with no BGMP reads as a
            # room in the dark, which is what it is, and the grey looks
            # like a background that failed to load.
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            return
        height, width = self._background_image.shape[:2]
        if not self.background_program.bind():
            return
        # The picture is a tall strip - AREA_04's is 576 by 1152 - and
        # it is hung round the camera rather than pasted flat: its
        # height covers a whole look-up-to-look-down sweep, so the top
        # of it is the sky and the bottom the ground, and it repeats
        # sideways as the camera turns. How wide that makes one copy
        # follows from the picture's own shape, which is what keeps the
        # texels square instead of stretched.
        vertical = BACKGROUND_PITCH_SPAN
        horizontal = vertical * width / max(height, 1)
        aspect = self.width() / max(self.height(), 1)
        half = math.tan(math.radians(FIELD_OF_VIEW) / 2)
        # How far the picture slides as the camera turns. Hung at the
        # camera's own rate it goes round nearly four times in one turn,
        # which is what makes it look like it is racing - so instead the
        # turn is scaled to put exactly one copy of it round the whole
        # circle. It still moves with the level rather than against it,
        # just at the pace a distant backdrop should.
        parallax = horizontal / 360.0
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(GL.GL_FALSE)
        self.background_program.setUniformValue(
            "halfFov", QVector2D(half * aspect, half))
        self.background_program.setUniformValue(
            "look", QVector2D(-self.camera_controls.camera_angle_h * parallax,
                              self.camera_controls.camera_angle_v))
        self.background_program.setUniformValue(
            "span", QVector2D(horizontal, vertical))
        self.background_program.setUniformValue("picture", 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.background_texture)
        self.background_vao.bind()
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        self.background_vao.release()
        self.background_program.release()
        GL.glDepthMask(GL.GL_TRUE)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def paintGL(self):
        self._sync_lines()
        super().paintGL()
        self.draw_sprites()
        if not (self.marker_count or self.selection_count):
            return
        if not self.shader_program.bind():
            return
        self.shader_program.setUniformValue("modelViewProjection",
                                            self._model_view_projection())
        self.shader_program.setUniformValue("useTextures", False)
        self.shader_program.setUniformValue("alpha", 1.0)
        if self.show_markers and self.marker_count:
            GL.glLineWidth(MARKER_WIDTH)
            self.marker_vao.bind()
            GL.glDrawArrays(GL.GL_LINES, 0, self.marker_count)
            self.marker_vao.release()
        if self.selection_count:
            # Over everything, depth test off: what you have just picked
            # is often the thing behind the wall you are looking at.
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glLineWidth(SELECTION_WIDTH)
            self.selection_vao.bind()
            GL.glDrawArrays(GL.GL_LINES, 0, self.selection_count)
            self.selection_vao.release()
            GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glLineWidth(1.0)
        self.shader_program.release()

    def _update_stats_label(self):
        """The SMST viewer counts parts; a level counts what stands in
        it, and how much of that we can actually draw."""
        instances = self.instances
        objects = [i for i in instances if i.role in ("object", "pickup")]
        drawn = sum(1 for i in objects if i.face_count)
        model = self.model_data or {}
        line = (f"Objects: {drawn}/{len(objects)} placed  "
                f"Tris: {model.get('tri_count', 0)}  "
                f"Quads: {model.get('quad_count', 0)}")
        moving = set(self.clut_animations) | set(self.uv_animations)
        if moving:
            what = []
            if self.clut_animations:
                what.append(f"{len(self.clut_animations)} palette(s)")
            if self.uv_animations:
                what.append(f"{len(self.uv_animations)} UV")
            line += f"  Animated: {', '.join(what)}"
            if self.anim_timer.isActive():
                line += f", tick {self.anim_tick}"
        self.stats_label.setText(line + "\n"
                                 + self.camera_controls.status_text())
        self._place_labels()

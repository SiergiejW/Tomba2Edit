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
    picking         click an instance to select it, again for its part

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
from gui import collision_overlay, export_dialog, theme

# World units per GL unit. A room is thousands of units across, so it
# gets the level scale gui/scld/scld_render.py uses rather than the
# SMST viewer's character-sized one.
UNIT_SCALE = 1000.0

# The selected instance's box, and the marker an unbound object gets.
SELECTION_WIDTH = 2.0

# A selection box is drawn in its instance's colour - the dot in the list,
# the marker in the view (level_scene.instance_color) - and the box round one
# part of it in white.
PART_COLOR = (1.0, 1.0, 1.0)
PART_PAD = 6.0
# Half the width a stretched sprite quad gets when its corners meet in a
# line - kind 0x14's other branch writes them 4 either side.
ROPE_HALF_WIDTH = 4.0
# The hourglass drawn on anything gated or timed, in pixels - GL triangles,
# not a QPainter, which left its own state behind. Set inside the thing it
# marks rather than off its corner: BADGE_REACH of the way from the middle
# of it towards the top right.
BADGE_WIDTH, BADGE_HEIGHT = 6.0, 8.0
BADGE_REACH = 0.4
BADGE_COLOR = (1.0, 0.84, 0.0)
BADGE_OUTLINE = (0.0, 0.0, 0.0)


def hourglass(x, y):
    """(triangle points, outline segment points) for a badge whose top left
    is at pixel (x, y), y counted up from the bottom."""
    w, h = BADGE_WIDTH, BADGE_HEIGHT
    top = [(x, y), (x + w, y), (x + w / 2, y - h / 2)]
    bottom = [(x + w / 2, y - h / 2), (x + w, y - h), (x, y - h)]
    edges = [p for tri in (top, bottom)
             for a, b in zip(tri, tri[1:] + tri[:1]) for p in (a, b)]
    return top + bottom, edges
# F frames the selection no closer than this, world units.
FRAME_MIN_RADIUS = 150.0

# What export groups each kind under.
EXPORT_GROUPS = {
    "object": "Objects", "character": "Characters", "chest": "Chests",
    "item": "Items", "prop": "Spawned props", "scenery": "Scenery",
    "area": "Area",
}

# A whole character is one file's many groups; the asset pack is props.
CHARACTER_PARTS = 8
ASSET_PACK = 12


def selection_kind(instance):
    """What an instance is, for its colour and its export group."""
    if instance.role == "room":
        return "area"
    if instance.role == "pickup":
        pickup = instance.pickup
        return "chest" if pickup is not None and pickup.chest else "item"
    if instance.drawn_as_sprite:
        return "item"
    files = {f for f, _g in instance.sources}
    if (len(instance.sources) >= CHARACTER_PARTS and len(files) == 1
            and ASSET_PACK not in files):
        return "character"
    if getattr(instance, "scene", None) is not None:
        return "room"
    if instance.role == "scenery":
        return "scenery"
    return "object" if instance.role == "object" else "prop"
MARKER_WIDTH = 2.0

# How near a click has to land, in pixels, to pick an object that has no
# geometry to hit - a marker is a few lines and a ray rarely meets one.
MARKER_PICK_PIXELS = 18.0

# The vertical field of view every view here draws with. Kept here as
# well as in paintGL's matrix because the background has to be hung at
# the same angles the geometry is seen through.
FIELD_OF_VIEW = 45.0

# How many degrees of looking up and down the background's full height
# covers: the horizon sits in the middle of it, with as much sky above as
# ground below. It is also how big the picture reads - the 45-degree field
# of view above shows this fraction of it - so MORE degrees puts less of
# the picture on screen and bigger texels on it. Given for a strip
# BACKGROUND_ROWS tall; a shorter one covers proportionally less, so every
# area's texels come out the same size.
BACKGROUND_PITCH_SPAN = 145.0
BACKGROUND_ROWS = 1152


def clamped_background_pitch(camera_pitch, vertical_span,
                             field_of_view=FIELD_OF_VIEW):
    """Stop a BGMP at its top/bottom without stretching its edge texel.

    The background may scroll only while a full viewport still fits inside
    the picture. Beyond that point the level camera can continue looking,
    but the picture holds on its last complete window just as a bounded
    backdrop does.
    """
    limit = max(vertical_span / 2.0 - field_of_view / 2.0, 0.0)
    return max(-limit, min(limit, float(camera_pitch)))

CONTROLS = ("Left-click: select | click it again: the part under the "
            "cursor | F: frame it\n" + CONTROLS_HINT)


class LevelViewer(SMSTViewer):
    """One area on screen, and what is picked out of it."""

    # The index of the selected instance, or None.
    selection_changed = pyqtSignal(object)
    # An instance that has just been moved, so the panel's position boxes
    # can follow it.
    instance_moved = pyqtSignal(object)
    # (instance, part) when a part of the selection is picked, part None
    # when it goes back to the whole.
    part_changed = pyqtSignal(object, object)

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

        self.show_badges = True
        self.badge_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            "Timed", self)
        self.badge_action.setCheckable(True)
        self.badge_action.setChecked(True)
        self.badge_action.setToolTip(
            "Mark everything gated or timed with an hourglass - what only "
            "stands once an event is done, or only in one phase of a scene.")
        self.badge_action.toggled.connect(self._toggle_badges)
        self.toolbar.insertAction(self.sprite_action, self.badge_action)

        self.show_collision = True
        self.collision_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon),
            "Collision", self)
        self.collision_action.setCheckable(True)
        self.collision_action.setChecked(True)
        self.collision_action.setToolTip(
            "Draw the area's collision as lines.\n\n"
            "A SCLD area shows each plane's surface samples and the stacks "
            "standing on them. Coal Mining Town and Circus Village have no "
            "SCLD: their streets and rooms are planes built into the "
            "overlay, outlined here - green floor, red wall edge, yellow "
            "door - see functions/town_collision.py.")
        self.collision_action.toggled.connect(self._toggle_collision)
        self.toolbar.insertAction(self.sprite_action, self.collision_action)

        self.show_lines = True
        self.lines_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight),
            "Lines", self)
        self.lines_action.setCheckable(True)
        self.lines_action.setChecked(True)
        self.lines_action.setToolTip(
            "Draw the lines the actors' own code draws - ropes, chains, "
            "fishing lines.\n\nNo model holds them: they are read back out "
            "of the primitives each actor's draw routine puts out when it is "
            "run - see functions/actor_sim.py.")
        self.lines_action.toggled.connect(self._toggle_lines)
        self.toolbar.insertAction(self.collision_action, self.lines_action)

        # Line overlays, both built on the CPU and uploaded from paintGL
        # for the same reason everything else here is: an area can be
        # picked before Qt has given this widget a context.
        self.marker_vao = QOpenGLVertexArrayObject()
        self.marker_vbo = QOpenGLBuffer()
        self.marker_cbo = QOpenGLBuffer()
        self.marker_count = 0
        self._badge_cache = None            # [(instance index, box corners)]
        self.badge_vao = QOpenGLVertexArrayObject()
        self.badge_vbo = QOpenGLBuffer()
        self.badge_cbo = QOpenGLBuffer()
        self._marker_arrays = None
        self.selection_vao = QOpenGLVertexArrayObject()
        self.selection_vbo = QOpenGLBuffer()
        self.selection_cbo = QOpenGLBuffer()
        self.selection_count = 0
        self._selection_arrays = None
        self.collision = collision_overlay.Overlay()
        # What the panel's Show box is on: None the area, a scene number one
        # room, "all" both. Collision and the background follow it.
        self.view = None
        self.code_line_vao = QOpenGLVertexArrayObject()
        self.code_line_vbo = QOpenGLBuffer()
        self.code_line_cbo = QOpenGLBuffer()
        self.code_line_count = 0
        self._code_line_arrays = None
        # The semi-transparent ones, added to what is behind them.
        self.code_blend_vao = QOpenGLVertexArrayObject()
        self.code_blend_vbo = QOpenGLBuffer()
        self.code_blend_cbo = QOpenGLBuffer()
        self.code_blend_count = 0
        self._code_blend_arrays = None

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
        self._flips = []                # see load_scene()
        self._sprite_tick = 0
        self._sprite_dirty = False
        self._background_image = None       # (h, w, 3) uint8, or None
        self._background_dirty = False

        # Picking, and dragging what was picked.
        self._pick_vertices = None
        self._pick_faces = None
        self._face_instance = None
        self._drag = None
        self._last_face = None
        self.selected_part = None

    # --- loading ------------------------------------------------------

    def load_scene(self, scene, frame=True):
        """Show a gui.level.level_scene.LevelScene.

        `frame` puts the camera back over the whole level. Off when the
        scene is being rebuilt around a change the user has just made -
        binding an object to a different model rebuilds every array, but
        it is still the same level and they are still looking at the
        part of it they were looking at."""
        self.scene = scene
        # Each recorded effect's frame instances, in order - one shows a tick.
        self._flips = [i.flip_frames for i in scene.instances
                       if getattr(i, "flip_frames", ())]
        self.selected = None
        self.hidden_groups = set()
        self.highlighted_group = None
        self.pose = None
        self.pose_pivots = None
        self.model_data = scene.build() if scene is not None else None
        self._badge_cache = None
        self._face_instance = None
        # A drag in progress refers to the instance list being replaced,
        # so it cannot survive the reload - dragging on into the new
        # scene indexes a list that may be shorter.
        self._drag = None
        self.selected_part = None
        self.prepare_buffers()
        self.rebuild_markers()
        self.rebuild_code_lines()
        self._rebuild_collision()
        self._build_selection()
        if frame:
            self.frame_level()
        self.update()

    def set_background(self, image):
        """The picture to draw behind the room, as an (h, w, 3) uint8
        array, or None for none. Drawn as it is: AREA_0A's black above and
        below its trees is the art fading out, not a gap to fill."""
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
        if not (self._sprite_quads or self._flips):
            return
        self._sprite_tick += ticks
        self._sprite_dirty = True
        self.update()

    @property
    def animating(self):
        """Whether anything wants the tick timer: a sprite with several
        steps, or a recorded effect."""
        return bool(self._flips) or any(len(q.steps) > 1 for q in self._sprite_quads)

    def _flip_hidden(self):
        """Every recorded effect's frames but the one showing this tick - all
        of them where its first frame's row is hidden."""
        out = set()
        for frames in self._flips:
            now = frames[self._sprite_tick % len(frames)]
            gone = frames[0] in self.hidden_groups
            out.update(f for f in frames if gone or f != now)
        return out

    def _draw_pass(self, transparent, blend=None):
        if not self._flips:
            return super()._draw_pass(transparent, blend)
        kept = self.hidden_groups
        self.hidden_groups = kept | self._flip_hidden()
        try:
            super()._draw_pass(transparent, blend)
        finally:
            self.hidden_groups = kept

    def _toggle_sprites(self, checked):
        self.show_sprites = checked
        self.update()

    def set_hidden_groups(self, hidden):
        super().set_hidden_groups(hidden)
        self._sprite_dirty = True
        self.rebuild_markers()
        self.rebuild_code_lines()

    def set_group_hidden(self, index, hidden):
        super().set_group_hidden(index, hidden)
        self._sprite_dirty = True
        self.rebuild_markers()
        self.rebuild_code_lines()

    def _toggle_badges(self, checked):
        self.show_badges = checked
        self.update()

    def _toggle_collision(self, checked):
        self.show_collision = checked
        self._rebuild_collision()
        self.update()

    def _rebuild_collision(self):
        if not self.show_collision or self.scene is None:
            self.collision.clear()
            return
        self.collision.set(self.scene.collision(self.view, self._room_bounds()),
                           UNIT_SCALE)

    def set_view(self, view):
        """Follow the panel's Show box - see self.view."""
        self.view = view
        self._rebuild_collision()
        self.rebuild_code_lines()
        self.update()

    def _toggle_lines(self, checked):
        self.show_lines = checked
        self.update()

    def _visible_line_records(self):
        """The code-drawn lines that show: an owned line with its row, a
        loose one with its room."""
        out = []
        for line in getattr(self.scene, "lines", None) or ():
            if line.owner is not None:
                if line.owner in self.hidden_groups:
                    continue
            elif not (self.view == "all" or line.scene == self.view):
                continue
            out.append(line)
        return out

    def rebuild_code_lines(self):
        lines = self._visible_line_records()
        self._code_line_arrays = self._line_arrays(
            [l for l in lines if not getattr(l, "blended", False)])
        self._code_blend_arrays = self._line_arrays(
            [l for l in lines if getattr(l, "blended", False)])

    @staticmethod
    def _line_arrays(lines):
        return (np.array([p for l in lines for p in (l.a, l.b)],
                         dtype=np.float32).reshape(-1, 3) / UNIT_SCALE,
                np.array([c for l in lines for c in (l.color_a, l.color_b)],
                         dtype=np.float32).reshape(-1, 3))

    def _room_bounds(self):
        """{scene: (low, high)} round each room's instances, world units."""
        verts = self._positions() * UNIT_SCALE
        found = {}
        for instance in self.instances:
            scene = getattr(instance, "scene", None)
            if scene is None:
                continue
            if instance.vertex_count:
                points = verts[instance.first_vertex:
                               instance.first_vertex + instance.vertex_count]
            else:
                points = np.array([[instance.x, instance.y, instance.z]])
            low, high = points.min(axis=0), points.max(axis=0)
            if scene in found:
                low = np.minimum(low, found[scene][0])
                high = np.maximum(high, found[scene][1])
            found[scene] = (low, high)
        return found

    def enterEvent(self, event):
        # Keys go where the mouse is, so F frames a selection picked in the
        # list without clicking the view first.
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().enterEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F and not event.isAutoRepeat():
            self.frame_selection()
            return
        super().keyPressEvent(event)

    def frame_selection(self):
        """Ease the camera onto what is selected - the picked part of it if
        there is one - keeping the angle it is looked at from."""
        if self.selected is None or self.selected >= len(self.instances):
            return
        instance = self.instances[self.selected]
        box = (self._part_box(instance, self.selected_part)
               or self._instance_box(instance))
        if box is None and instance.vertex_count:
            verts = self._positions()[instance.first_vertex:
                                      instance.first_vertex
                                      + instance.vertex_count] * UNIT_SCALE
            low, high = verts.min(axis=0), verts.max(axis=0)
            box = (low[0], high[0], low[1], high[1], low[2], high[2])
        if box is None:
            return
        x0, x1, y0, y1, z0, z1 = box
        centre = ((x0 + x1) / 2 / UNIT_SCALE, (y0 + y1) / 2 / UNIT_SCALE,
                  (z0 + z1) / 2 / UNIT_SCALE)
        radius = max(math.dist((x0, y0, z0), (x1, y1, z1)) / 2,
                     FRAME_MIN_RADIUS) / UNIT_SCALE
        self.camera_controls.glide_to(centre, radius)

    def frame_visible(self):
        """Put the camera over what is showing - a room rather than the
        area around it."""
        if not self.model_data:
            return
        verts = self._positions()
        points = []
        for instance in self.instances:
            if instance.index in self.hidden_groups:
                continue
            if instance.vertex_count:
                points.append(verts[instance.first_vertex:
                                    instance.first_vertex + instance.vertex_count])
            elif instance.role != "room":
                points.append(np.array([[instance.x, instance.y, instance.z]],
                                       dtype=np.float32) / UNIT_SCALE)
        found = scene_of(np.concatenate(points)) if points else None
        if found is None:
            # An empty room: nothing to frame, so the shot stays.
            return
        centre, radius = found
        self.scene_radius = radius
        self.camera_controls.glide_frame(centre, radius, LEVEL_HEADING, LEVEL_PITCH)
        self.update()

    def rebuild_markers(self):
        self._marker_arrays = (self.scene.markers(self.hidden_groups)
                               if self.scene
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
        self._badge_cache = None
        self._face_instance = None
        self._sprite_dirty = True
        self.update()

    def frame_level(self):
        scene = scene_of(self._positions())
        if scene is None:
            return
        centre, radius = scene
        self.scene_radius = radius
        self.camera_controls.glide_frame(centre, radius, LEVEL_HEADING, LEVEL_PITCH)
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
        self.selected_part = None
        self._build_selection()
        self.update()
        self.selection_changed.emit(index)

    def _build_selection(self):
        """A box round the selected instance, in its class's colour, and a
        white one round the picked part of it. Drawn over everything: the
        thing you are looking for is usually the one behind a wall."""
        positions, colors = [], []
        instance = (self.instances[self.selected]
                    if self.selected is not None
                    and self.selected < len(self.instances) else None)
        if instance is not None:
            box = self._instance_box(instance)
            if box is not None:
                from gui.level.level_scene import instance_color
                self._box_lines(box, instance_color(instance), positions, colors)
            part = self._part_box(instance, self.selected_part)
            if part is not None:
                self._box_lines(part, PART_COLOR, positions, colors)
        self._selection_arrays = (
            np.array(positions, dtype=np.float32) / UNIT_SCALE,
            np.array(colors, dtype=np.float32))

    @staticmethod
    def _box_lines(box, color, positions, colors):
        (x0, x1, y0, y1, z0, z1) = box
        corners = [(x, y, z) for x in (x0, x1)
                   for y in (y0, y1) for z in (z0, z1)]
        for a, b in ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
                     (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7)):
            positions.extend(corners[a])
            positions.extend(corners[b])
            colors.extend(color)
            colors.extend(color)

    @staticmethod
    def sub_object(instance, part):
        """The pieces the same actor drew as piece `part` - the chest in an
        ice cube, the Toradako frozen in one - or just that piece."""
        owners = getattr(instance.assembly, "owners", None) or ()
        if part is None or not 0 <= part < len(owners):
            return [part]
        who = owners[part][0]
        return [n for n, owner in enumerate(owners) if owner[0] == who]

    def _part_box(self, instance, part):
        """The box round the sub-object one source of an instance belongs
        to, or None."""
        spans = instance.spans or ()
        if part is None or not 0 <= part < len(spans) or not spans[part]:
            return None
        positions = self._positions()
        pieces = [positions[spans[n][0]:spans[n][0] + spans[n][1]]
                  for n in self.sub_object(instance, part)
                  if 0 <= n < len(spans) and spans[n]]
        verts = np.concatenate(pieces) * UNIT_SCALE if pieces else np.zeros((0, 3))
        if not len(verts):
            return None
        low, high = verts.min(axis=0), verts.max(axis=0)
        return (low[0] - PART_PAD, high[0] + PART_PAD, low[1] - PART_PAD,
                high[1] + PART_PAD, low[2] - PART_PAD, high[2] + PART_PAD)

    def select_part(self, part):
        self.selected_part = part
        self._build_selection()
        self.update()
        self.part_changed.emit(self.selected, part)

    def _part_under(self, index):
        """Which source of an instance the last picked face belongs to."""
        instance = self.instances[index]
        if self._last_face is None or not instance.spans:
            return None
        vertex = int(self._pick_faces[self._last_face][0])
        for number, span in enumerate(instance.spans):
            if span and span[0] <= vertex < span[0] + span[1]:
                return number
        return None

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
        self._last_face = None
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
            hidden = self.hidden_groups | self._flip_hidden()
            if hidden:
                hit &= ~np.isin(self._face_instance,
                                np.fromiter(hidden, dtype=np.int64))
            if hit.any():
                which = int(np.argmin(np.where(hit, t, np.inf)))
                hit_instance = int(self._face_instance[which])
                # A recorded effect's frame is its first frame's row.
                flip = getattr(self.instances[hit_instance], "flip", None)
                if flip is not None:
                    hit_instance, self._last_face = flip[0], None
                hit_distance = float(t[which])
                self._last_face = which

        near = self._pick_marker(x, y, origin, direction)
        if near is not None:
            index, distance = near
            # A marker in front of whatever the ray hit wins; one behind
            # it is something else's, standing further away.
            if hit_instance is None or distance < hit_distance:
                self._last_face = None
                return index
        return hit_instance

    def _pick_marker(self, x, y, origin, direction):
        """(instance, distance along the ray) for the nearest marker the
        click landed on, or None."""
        matrix = self._model_view_projection()
        best = None
        for instance in self.instances:
            if (not instance.marked or instance.face_count
                    or instance.index in self.hidden_groups):
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
        """Pick, never move: a click selects the instance under the
        cursor, and a second click on it goes one deeper - the part under
        the cursor - then back to the whole."""
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position().toPoint()
        index = self.pick(point.x(), point.y())
        if index is not None and index == self.selected:
            part = self._part_under(index)
            self.select_part(None if part == self.selected_part else part)
            return
        self.select(index)

    # --- what the toolbar toggles -------------------------------------

    def _toggle_markers(self, checked):
        self.show_markers = checked
        self.update()

    def _toggle_background(self, checked):
        self.show_background = checked
        self.update()

    def export_to_gltf(self):
        """Write the level out with everything standing where it does, one
        object per instance - the area, each placed object, each character
        posed as it is here, each chest and item - grouped the way the
        view colours them. What is hidden stays out, so a room exports on
        its own."""
        if not self.model_data or not self.model_data.get("vertices"):
            QMessageBox.warning(self, "Nothing to export", "No level is loaded.")
            return
        path, unlit = export_dialog.ask_model_path(
            self, "Save level", self.export_name or "level", text=False)
        if not path:
            return
        try:
            written, groups = gltf_export.write_scene_glb(
                path, self.export_objects(), self.vram_raw_bytes,
                name=self.export_name or "level", unlit=unlit)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        QMessageBox.information(
            self, "Exported",
            f"Wrote {written} object(s) in {groups} group(s), each on its own "
            f"origin: models, sprites as cards, lines as edges, markers as "
            f"empties.")

    def export_objects(self):
        """Everything showing, for gltf_export.write_scene_glb: each instance
        on its own origin - split per actor and per file where its code
        built it out of several - the sprites as cards of the frame showing,
        the code-drawn lines per row, markers as empties, and the collision
        when it is on."""
        from gui.level.level_scene import game_state, view_point
        model = self.model_data or {}
        verts = self._positions() * UNIT_SCALE if model.get("vertices") else None
        out = []
        for instance in self.instances:
            if instance.index in self.hidden_groups:
                continue
            kind = selection_kind(instance)
            scene = getattr(instance, "scene", None)
            group = (f"Interior {scene - 1}" if kind == "room" and scene is not None
                     else EXPORT_GROUPS.get(kind, "Objects"))
            label = f"{instance.index:03d} {instance.label}"
            origin = ((0.0, 0.0, 0.0) if instance.role == "room"
                      else (instance.x, instance.y, instance.z))
            if instance.face_count and verts is not None:
                for suffix, spans, where in self._export_pieces(
                        instance, game_state, view_point):
                    out.append({"name": label + suffix, "group": group,
                                "origin": where or origin,
                                "model": self._export_model(instance, verts, spans)})
            elif instance.marked and not instance.drawn_as_sprite:
                out.append({"name": label, "group": "Markers", "origin": origin})
        out.extend(self._export_sprites())
        out.extend(self._export_lines())
        if self.show_collision and self.scene is not None:
            lines = self.scene.collision(self.view, self._room_bounds())
            for layer, points, colors in (
                    ("surface", lines.surface, lines.surface_colors),
                    ("vertical", lines.vertical, lines.vertical_colors)):
                if points:
                    out.append({"name": f"collision {layer}", "group": "Collision",
                                "origin": (0.0, 0.0, 0.0), "lines": (points, colors)})
        return out

    def _export_pieces(self, instance, game_state, view_point):
        """[(name suffix, [(first vertex, count)], origin or None)]: the
        instance whole, or one piece per (actor, file) where it is several -
        a creature and the block it is frozen in come out apart."""
        whole = [("", [(instance.first_vertex, instance.vertex_count)], None)]
        spans, sources = instance.spans or (), instance.sources
        if not spans or len(spans) != len(sources):
            return whole
        owners = getattr(instance.assembly, "owners", None) or ()
        pieces = {}
        for number, span in enumerate(spans):
            if not span:
                continue
            owner = owners[number][0] if number < len(owners) else 0
            pieces.setdefault((owner, sources[number][0]), []).append((number, span))
        if len(pieces) < 2:
            return whole
        state = game_state(instance) if owners else None
        out = []
        for (_owner, file_id), members in pieces.items():
            number = members[0][0]
            if number < len(owners):
                suffix = f" / {owners[number][1]} id {file_id}"
                where = view_point(instance.assembly.owner_position(state, number))
            else:
                suffix, where = f" / id {file_id}", None
            out.append((suffix, [span for _n, span in members], where))
        return out

    def _export_model(self, instance, verts, spans):
        """A model dict out of the scene arrays for the vertices in `spans`."""
        model = self.model_data
        keep = [v for first, count in spans for v in range(first, first + count)]
        remap = {old: new for new, old in enumerate(keep)}
        faces, info = [], []
        for f in range(instance.first_face, instance.first_face + instance.face_count):
            face = model["faces"][f]
            if face[0] in remap:
                faces.append([remap[v] for v in face])
                info.append(model["texture_info"][f])
        return {"vertices": verts[keep].tolist(),
                "vertex_colors": [model["vertex_colors"][v] for v in keep],
                "texture_coords": [model["texture_coords"][v] for v in keep],
                "faces": faces, "texture_info": info}

    def _export_sprites(self):
        """Each pickup and sprite object as a card of the frame showing now."""
        atlas = self._sprite_atlas
        if atlas is None or not self.show_sprites:
            return []
        height, width = atlas.shape[:2]
        instances, out = self.instances, []
        for quad in self._sprite_quads:
            if quad.index in self.hidden_groups:
                continue
            placed = quad.frame_now(self._sprite_tick)
            if placed is None:
                continue
            at = instances[quad.index] if 0 <= quad.index < len(instances) else quad
            x0, x1 = int(round(placed.u0 * width)), int(round(placed.u1 * width))
            y0, y1 = int(round(placed.v0 * height)), int(round(placed.v1 * height))
            if x1 <= x0 or y1 <= y0:
                continue
            units = quad.units
            out.append({
                "name": f"{quad.index:03d} {getattr(at, 'label', 'sprite')}",
                "group": "Items" if getattr(at, "pickup", None) is not None else "Sprites",
                "origin": (at.x, at.y, at.z),
                "sprite": (atlas[y0:y1, x0:x1], placed.width * units,
                           placed.height * units, placed.origin_x * units,
                           placed.origin_y * units)})
        return out

    def _export_lines(self):
        """The code-drawn lines, one object per row that owns them."""
        owned = {}
        for line in self._visible_line_records():
            owned.setdefault(line.owner, []).append(line)
        out = []
        for owner, lines in owned.items():
            points = [p for line in lines for p in (line.a, line.b)]
            colors = [c for line in lines for c in (line.color_a, line.color_b)]
            instance = (self.instances[owner]
                        if owner is not None and owner < len(self.instances) else None)
            out.append({
                "name": (f"{owner:03d} {instance.label} lines" if instance is not None
                         else "lines"),
                "group": "Lines",
                "origin": ((instance.x, instance.y, instance.z)
                           if instance is not None else points[0]),
                "lines": (points, colors)})
        return out

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
        instances = self.instances
        for quad in self._sprite_quads:
            if quad.index in self.hidden_groups:
                continue
            placed = quad.frame_now(self._sprite_tick)
            if placed is None:
                continue
            if quad.corners is not None:
                rows.extend(self._stretched(quad.corners, placed))
                continue
            # Live, so a dragged pickup - or an apple riding a seesaw -
            # takes its picture with it.
            at = (instances[quad.index] if 0 <= quad.index < len(instances)
                  else quad)
            x, y, z = at.x / UNIT_SCALE, at.y / UNIT_SCALE, at.z / UNIT_SCALE
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

    @staticmethod
    def _stretched(corners, placed):
        """Sprite rows for a picture stretched across four corners - the
        PSX's v0 v1 on top, v2 v3 below - once over the whole frame. A pair
        that meets in a point (a rope the game widens per frame) gets two
        crossed ribbons so it reads from any side."""
        c = [np.asarray(p, dtype=np.float64) / UNIT_SCALE for p in corners]
        uvs = ((placed.u0, placed.v0), (placed.u1, placed.v0),
               (placed.u0, placed.v1), (placed.u1, placed.v1))
        if np.allclose(c[0], c[1]) and np.allclose(c[2], c[3]):
            half = ROPE_HALF_WIDTH / UNIT_SCALE
            quads = [(c[0] - axis, c[0] + axis, c[2] - axis, c[2] + axis)
                     for axis in (np.array([half, 0.0, 0.0]), np.array([0.0, 0.0, half]))]
        else:
            quads = [tuple(c)]
        rows = []
        for v0, v1, v2, v3 in quads:
            for point, (u, v) in ((v0, uvs[0]), (v2, uvs[2]), (v3, uvs[3]),
                                  (v0, uvs[0]), (v3, uvs[3]), (v1, uvs[1])):
                rows.append((point[0], point[1], point[2], 0.0, 0.0, u, v))
        return rows

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

    def _draw_between_passes(self):
        """The pickups, after the room's solid faces and before its blended
        ones: an apple inside AREA_09's ice is behind the ice, the way the
        game draws it, rather than floating in front of it."""
        # Upload first: the count is only known once they are.
        self._sync_sprites()
        if not (self.show_sprites and self.sprite_count):
            return
        self.vao.release()
        self.draw_sprites()
        if self.shader_program.bind():
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture or 0)
            self.vao.bind()

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
        if self._code_line_arrays is not None:
            self.code_line_count = self._upload_lines(
                self._code_line_arrays, self.code_line_vao, self.code_line_vbo,
                self.code_line_cbo)
            self._code_line_arrays = None
        if self._code_blend_arrays is not None:
            self.code_blend_count = self._upload_lines(
                self._code_blend_arrays, self.code_blend_vao, self.code_blend_vbo,
                self.code_blend_cbo)
            self._code_blend_arrays = None

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
        for name, value in (# The picture is PSX art: its pixels stay pixels.
                            (GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST),
                            (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
                            # Repeats sideways as the camera turns. Vertical
                            # movement is bounded before sampling, with this
                            # clamp retained only as a rounding safeguard.
                            (GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT),
                            (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, name, value)

    def draw_backdrop(self):
        """The SMST viewer's hook: the area's background, drawn flat
        across the view after the clear and before anything else."""
        self._sync_background()
        room = self.view is not None and self.view != "all"
        if room or not self.show_background or self.background_texture is None:
            # Nothing behind the level: black rather than the model viewer's
            # grey, which looks like a background that failed to load - and
            # a room is always black, the sky outside is not in it. The
            # bright theme takes its light ground for an area.
            GL.glClearColor(*(theme.ROOM_VIEW if room
                              else theme.view_background((0.0, 0.0, 0.0))), 1.0)
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
        vertical = BACKGROUND_PITCH_SPAN * height / BACKGROUND_ROWS
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
        pitch = clamped_background_pitch(
            self.camera_controls.camera_angle_v, vertical)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(GL.GL_FALSE)
        self.background_program.setUniformValue(
            "halfFov", QVector2D(half * aspect, half))
        self.background_program.setUniformValue(
            "look", QVector2D(-self.camera_controls.camera_angle_h * parallax,
                              pitch))
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
        self._paint_level()
        self._paint_badges()

    def _badges(self):
        """[(instance index, its box's 8 corners as GL-unit xyzw)] for
        every timed instance."""
        if self._badge_cache is None:
            out, positions = [], None
            for instance in self.instances:
                if instance.role == "room" or not instance.timed:
                    continue
                if instance.vertex_count:
                    if positions is None:
                        positions = self._positions()
                    verts = positions[instance.first_vertex:
                                      instance.first_vertex + instance.vertex_count]
                    (x0, y0, z0), (x1, y1, z1) = verts.min(axis=0), verts.max(axis=0)
                else:
                    box = self._instance_box(instance)
                    if box is None:
                        continue
                    x0, x1, y0, y1, z0, z1 = (v / UNIT_SCALE for v in box)
                corners = np.array([(x, y, z, 1.0) for x in (x0, x1)
                                    for y in (y0, y1) for z in (z0, z1)])
                out.append((instance.index, corners))
            self._badge_cache = out
        return self._badge_cache

    def _paint_badges(self):
        """A yellow hourglass at the top right of each timed instance on
        screen, drawn in GL with the line shader. A QPainter over the widget
        left its depth mask off, so the next frame's depth never cleared
        and the whole level went see-through."""
        badges = self._badges() if self.show_badges and self.scene is not None else ()
        if not badges:
            return
        mvp = np.array(self._model_view_projection().data(),
                       dtype=np.float64).reshape(4, 4).T
        width, height = max(self.width(), 1), max(self.height(), 1)
        fill, edges = [], []
        for index, corners in badges:
            if index in self.hidden_groups:
                continue
            clip = corners @ mvp.T
            front = clip[:, 3] > 1e-6
            if not front.any():
                continue
            ndc = clip[front, :3] / clip[front, 3:4]
            if (ndc[:, 0].max() < -1 or ndc[:, 0].min() > 1
                    or ndc[:, 1].max() < -1 or ndc[:, 1].min() > 1
                    or ndc[:, 2].min() > 1):
                continue
            middle = ndc.mean(axis=0)
            x = ((middle[0] + (min(ndc[:, 0].max(), 1.0) - middle[0]) * BADGE_REACH)
                 + 1) * 0.5 * width
            y = ((middle[1] + (min(ndc[:, 1].max(), 1.0) - middle[1]) * BADGE_REACH)
                 + 1) * 0.5 * height + BADGE_HEIGHT / 2
            triangles, outline = hourglass(min(max(x, 1.0), width - BADGE_WIDTH - 1),
                                           min(max(y, BADGE_HEIGHT + 1), height - 1))
            fill.extend(triangles)
            edges.extend(outline)
        if not fill or not self.shader_program.bind():
            return

        def arrays(points, color):
            positions = np.array([(px / width * 2 - 1, py / height * 2 - 1, 0.0)
                                  for px, py in points], dtype=np.float32)
            return positions, np.tile(np.array(color, dtype=np.float32), (len(points), 1))

        depth = GL.glIsEnabled(GL.GL_DEPTH_TEST)
        cull = GL.glIsEnabled(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        self.shader_program.setUniformValue("modelViewProjection", QMatrix4x4())
        self.shader_program.setUniformValue("useTextures", False)
        self.shader_program.setUniformValue("alpha", 1.0)
        for points, color, mode in ((fill, BADGE_COLOR, GL.GL_TRIANGLES),
                                    (edges, BADGE_OUTLINE, GL.GL_LINES)):
            count = self._upload_lines(arrays(points, color), self.badge_vao,
                                       self.badge_vbo, self.badge_cbo)
            self.badge_vao.bind()
            GL.glDrawArrays(mode, 0, count)
            self.badge_vao.release()
        self.shader_program.release()
        if depth:
            GL.glEnable(GL.GL_DEPTH_TEST)
        if cull:
            GL.glEnable(GL.GL_CULL_FACE)

    def _paint_level(self):
        self._sync_lines()
        super().paintGL()
        if not (self.model_data and self.draw_ranges):
            # Nothing was drawn, so the mid-pass hook never ran.
            self.draw_sprites()
        collision = self.show_collision and self.collision.has_lines()
        lines = self.show_lines and (self.code_line_count or self.code_blend_count)
        if not (self.marker_count or self.selection_count or collision or lines):
            return
        if not self.shader_program.bind():
            return
        self.shader_program.setUniformValue("modelViewProjection",
                                            self._model_view_projection())
        self.shader_program.setUniformValue("useTextures", False)
        self.shader_program.setUniformValue("alpha", 1.0)
        if collision:
            self.collision.draw(self.shader_program)
        if lines:
            GL.glLineWidth(MARKER_WIDTH)
            if self.code_line_count:
                self.code_line_vao.bind()
                GL.glDrawArrays(GL.GL_LINES, 0, self.code_line_count)
                self.code_line_vao.release()
            if self.code_blend_count:
                # A PSX semi-transparent line adds itself to the screen.
                blend = GL.glIsEnabled(GL.GL_BLEND)
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE)
                GL.glDepthMask(GL.GL_FALSE)
                self.code_blend_vao.bind()
                GL.glDrawArrays(GL.GL_LINES, 0, self.code_blend_count)
                self.code_blend_vao.release()
                GL.glDepthMask(GL.GL_TRUE)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                if not blend:
                    GL.glDisable(GL.GL_BLEND)
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

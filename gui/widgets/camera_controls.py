# camera_controls.py
"""The freecam every 3D view is driven by, and the widget plumbing that
feeds it.

One module for both the MDAT and SMST views, so the two behave
identically: the same keys, the same mouse, and - the part that has to
be shared to work at all - the same idea of how far a step is.

SCALE

Nothing here moves the camera by an absolute distance. A level room is
thousands of world units across and a character a couple of hundred, and
at the scales the viewers draw them (formats/collision/scld_render.UNIT_SCALE and
formats/models/smst_viewer.UNIT_SCALE) one fixed step cannot suit both: the
0.1-per-notch zoom this used to have is a reasonable nudge across a
room and 0.6% of the way to a character, which reads as a scroll wheel
that does nothing at all. So every step is a fraction of the scene the
view is looking at, and a view says how big that is by calling
set_scene_radius() - or frame(), which does it for you.
"""

from PyQt6.QtCore import Qt, QTimer, QPoint, QEvent
from PyQt6.QtGui import QCursor
import math
import sys

import numpy as np

# What the camera is looking at, when nothing has said otherwise.
DEFAULT_SCENE_RADIUS = 5.0

# The angles each kind of thing is opened at. A level reads from above
# and off to one side - this is the fixed pose the MDAT and SCLD views
# used to sit at, kept now that only the angle is fixed and the position
# is measured.
#
# A model opens square on. These face +Z, so a heading of 0 looks
# straight at the front of a character rather than at the back of its
# head, and no pitch keeps the camera level with it - which is what an
# animation wants, since a limb swinging towards the camera is far
# easier to read against a straight-on silhouette than a three-quarter
# one. The camera is free afterwards; this is only where it starts.
LEVEL_HEADING, LEVEL_PITCH = 134.5, 33.2
MODEL_HEADING, MODEL_PITCH = 0.0, 0.0

# How far above the middle of a model the camera looks, as a fraction of
# its radius. Framing on the bounding box's centre puts the camera at a
# character's belly, which is a strange height to watch anything from
# now that the view is level rather than tilted down at them. This lifts
# it to about chest height without pitching, so the model still reads
# square on.
#
# Raising the camera pushes the model down the frame, so this is as far
# as it can go before feet start meeting the bottom edge: at 0.15 a tall
# character reaches 0.89 of the way down, where 0.22 puts it at 0.93.
MODEL_LIFT = 0.15

# One wheel notch moves the camera this much of the scene radius, so
# framed at frame()'s default margin it takes about fifteen notches to
# travel from the camera to the model. It follows the freecam speed: speed
# up while looking and the wheel strides further too.
ZOOM_FRACTION = 0.10

# A middle-drag's pivot sits at least this many wheel notches ahead, so a
# floor right under the camera doesn't make orbiting a look-around.
ORBIT_REACH = 4.0

# How far WASD travels per frame, as a fraction of the scene radius.
SPEED_FRACTION = 0.02

# Middle-drag orbits about whatever the middle of the view is looking at,
# the way every modelling program does it, and shift+middle-drag slides that
# point about. The pivot is held this far out at least, as a fraction of the
# scene radius, so a drag still circles something instead of spinning on the
# spot; the camera itself is never stopped - the wheel flies it straight
# through geometry.
MIN_ORBIT = 0.05

# The vertical field of view the views project with - see
# smst_viewer._model_view_projection. Panning solves against it so the
# scene stays exactly under the pointer instead of drifting.
FIELD_OF_VIEW = 45.0

# Freecam scrolling multiplies the speed rather than adding to it -
# five notches double it, at any scale.
# Framing a selection eases there over this many steps rather than jumping.
GLIDE_FRAMES = 12
GLIDE_INTERVAL_MS = 16

SPEED_STEP = 1.15
SPEED_RANGE = 50.0

# The key that hands a left-drag to the camera. A MacBook trackpad has no
# middle button and the middle button is what orbits, so on the machine
# this is most often run on the orbit was out of reach; Option (Alt) is
# what the rest of a Mac's 3D programs put their one-finger navigation
# on, and it costs a mouse nothing - every binding below still works.
NAV_MODIFIER = Qt.KeyboardModifier.AltModifier

# A trackpad's two-finger scroll arrives as pixels, in dozens of small
# events per gesture, with no notches to count. This much of that travel
# is one wheel notch: the same distance a click of a wheel covers, so
# ZOOM_FRACTION keeps its meaning and the two feel alike.
TRACKPAD_PIXELS_PER_NOTCH = 40.0

# However hard a gesture is flicked, it is worth no more than this many
# notches. A trackpad reports a whole gesture's travel in one or two
# events, and the camera should not jump the scene because a finger
# moved quickly.
MAX_SCROLL_NOTCHES = 3.0

# What one wheel notch is worth as pointer travel, for the pan a
# shift+scroll does.
WHEEL_PIXELS_PER_NOTCH = 40.0

# A right-drag with the navigation key slides the camera along its own
# line of sight - the wheel's zoom, at whatever rate suits the drag.
# This many pixels of drag is one wheel notch.
DOLLY_PIXELS_PER_NOTCH = 60.0

# A pinch arrives as a fraction of the gesture - 0.1 is a tenth of its
# travel - and this is how many wheel notches that tenth is worth.
PINCH_NOTCHES_PER_VALUE = 10.0

# Degrees of turn per pixel of pointer travel, and there are two of them
# because the two drags are not the same thing.
#
# An orbit drags the scene about a point you are looking at, in view,
# with the pointer where you left it - it wants to keep up with the hand.
# The freecam's look is measured from the middle of the view with the
# pointer hidden and warped back on every move, so it can turn forever:
# the same number there spins the view, and on a trackpad - whose pixels
# are small and arrive fast - the view span faster than it could be
# aimed. This is the one to turn up or down to taste.
ORBIT_SENSITIVITY = 0.1
# A mouse looks at TombaEdit's rate - the same 0.1 as orbiting, applied
# at once. Only a Mac trackpad keeps the slower one.
LOOK_SENSITIVITY = 0.035 if sys.platform == "darwin" else 0.1

# Framing one polygon, or one limb, should not fly the camera inside it.
# This is the smallest a frame may come in, as a fraction of the scene
# the view was last sized to.
MIN_FRAME_RADIUS = 0.05

# Shown in the corner of every 3D view. Here so the two views can't
# describe the same controls differently.
#
# What comes first depends on what the machine has. A MacBook trackpad
# has no middle button and no wheel, so there the drag with the
# navigation key leads and the hint says so; every binding listed works
# on every platform, the middle button included for a mouse plugged into
# the same Mac.
if sys.platform == "darwin":
    CONTROLS_HINT = ("Option-drag: orbit | Option+Shift-drag: pan\n"
                     "Option + right-drag: zoom | Pinch: zoom\n"
                     "Right-drag: look around | Scroll: zoom\n"
                     "Hold right + WASD: move | Q/E: up/down\n"
                     "Shift: fast | F: frame what is picked")
else:
    CONTROLS_HINT = ("Middle-drag: orbit | Shift + middle: pan\n"
                     "Right-drag: look around | Scroll: zoom, speed while looking\n"
                     "Hold right + WASD: move | Q/E: up/down\n"
                     "Shift: fast | F: frame what is picked")


def navigating(event):
    """Whether a press belongs to the camera rather than to what is drawn.

    The views with something to pick ask this first: with the navigation
    key held, the same left-drag orbits and nothing is selected - see
    MDATViewer.mousePressEvent."""
    return bool(event.modifiers() & NAV_MODIFIER)


def scene_of(points):
    """(centre, radius) around a cloud of points, or None if there are
    none.

    `points` is whatever the view is about to draw, in the units it
    draws it in - so divide by the view's UNIT_SCALE first. The radius
    is half the bounding box's diagonal, which is what frame() wants:
    the distance from the middle to the furthest corner."""
    array = np.asarray(points, dtype=np.float32)
    if array.size == 0:
        return None
    array = array.reshape(-1, 3)
    low, high = array.min(axis=0), array.max(axis=0)
    centre = tuple(float(v) for v in (low + high) / 2)
    return centre, float(np.linalg.norm(high - low)) / 2


def depth_pivot(widget):
    """What the middle of a view is looking at, in its drawing units, or
    None where nothing is drawn: the depth buffer read back through the
    inverse of the widget's _model_view_projection()."""
    mvp = getattr(widget, "_model_view_projection", None)
    if not callable(mvp) or not widget.isValid():
        return None
    from OpenGL import GL
    from PyQt6.QtGui import QVector4D
    widget.makeCurrent()
    try:
        ratio = widget.devicePixelRatioF()
        x = int(max(widget.width(), 1) * ratio) // 2
        y = int(max(widget.height(), 1) * ratio) // 2
        depth = GL.glReadPixels(x, y, 1, 1, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT)
    except Exception:
        return None
    finally:
        widget.doneCurrent()
    depth = float(np.asarray(depth).reshape(-1)[0])
    if not 0.0 < depth < 1.0:
        return None                     # sky, or nothing drawn yet
    inverted = mvp().inverted()
    matrix, ok = inverted if isinstance(inverted, tuple) else (inverted, True)
    if not ok:
        return None
    point = matrix.map(QVector4D(0.0, 0.0, depth * 2.0 - 1.0, 1.0))
    if not point.w():
        return None
    return (point.x() / point.w(), point.y() / point.w(), point.z() / point.w())


class CameraSettings:
    """Smooth camera, how quickly it catches up, and mouse sensitivity.

    One set for every 3D view, kept in the app's settings. Read live by
    each camera, so changing them in one view's Camera menu changes all."""
    KEYS = (("smooth", True, bool), ("responsiveness", 1.0, float),
            ("sensitivity", 1.0, float))

    def __init__(self):
        from PyQt6.QtCore import QSettings
        self._store = QSettings("Tomba2Edit", "Tomba2Edit")
        for name, default, kind in self.KEYS:
            value = self._store.value(f"camera/{name}", default)
            if kind is bool:
                value = value in (True, "true", "1", 1)
            try:
                setattr(self, name, kind(value))
            except (TypeError, ValueError):
                setattr(self, name, default)

    def set(self, name, value):
        setattr(self, name, value)
        self._store.setValue(f"camera/{name}", value)

    @property
    def catch_up(self):
        """Seconds a smoothed move takes to get most of the way there."""
        return 0.12 / max(self.responsiveness, 0.05)


_SETTINGS = None


def camera_settings():
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = CameraSettings()
    return _SETTINGS


# Below this much left to apply, a smoothed move just finishes.
SETTLE = 1e-4


class CameraControls:
    def __init__(self, widget, scene_radius=DEFAULT_SCENE_RADIUS):
        self.widget = widget

        # Camera variables
        self.camera_y = 0.0
        self.camera_x = 0.0
        self.camera_z = -5.0
        self.mouse_sensitivity = ORBIT_SENSITIVITY
        self.look_sensitivity = LOOK_SENSITIVITY
        self.camera_angle_h = 0.0
        self.camera_angle_v = 0.0

        self.scene_radius = DEFAULT_SCENE_RADIUS
        self.camera_speed = 0.0
        self.camera_speed_min = 0.0
        self.camera_speed_max = 0.0
        self.set_scene_radius(scene_radius)

        # Mouse tracking
        self.last_pos = QPoint()
        self.display_center = [widget.width() // 2, widget.height() // 2]
        self.widget.setMouseTracking(True)
        self.widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Whether the right button is down and the view is being looked
        # around. Held, not toggled: the left button belongs to whatever
        # the view has to select.
        self.camera_mode = False
        self._restore_pos = None

        # How far in front of the camera the point it orbits sits. Set
        # by frame() to whatever it framed at, and kept in step by the
        # wheel, so orbiting circles the thing on screen rather than
        # some arbitrary distance. Held for the length of a drag in
        # _orbit_pivot so the point cannot drift under the mouse.
        self.orbit_distance = DEFAULT_SCENE_RADIUS
        self.orbit_mode = None            # None, "orbit" or "pan"
        self._orbit_pivot = None
        # A view can name what to circle instead - the Level Editor's
        # selection, while its Orbit selection toggle is on. None, or a
        # callable returning a world point or None.
        self.orbit_target = None

        # Key states
        self.keys_pressed = {
            Qt.Key.Key_W: False,
            Qt.Key.Key_S: False,
            Qt.Key.Key_A: False,
            Qt.Key.Key_D: False,
            Qt.Key.Key_E: False,
            Qt.Key.Key_Q: False,
            Qt.Key.Key_Shift: False,
        }

        # What smoothing has still to apply: orbit/look degrees, pan
        # pixels, zoom notches, and the WASD velocity it eases towards.
        self._pending = {"orbit": [0.0, 0.0], "look": [0.0, 0.0],
                         "pan": [0.0, 0.0], "zoom": 0.0}
        self._pending_pivot = None
        self._pending_distance = None
        self._velocity = [0.0, 0.0, 0.0]

        # Timer for smooth movement
        self.key_timer = QTimer()
        self.key_timer.timeout.connect(self.handle_key_movement)
        self.key_timer.start(16)

    # --- scale ---

    def set_scene_radius(self, radius, extent=None):
        """How big the thing on screen is, in the units the view draws
        it at. Every step the camera takes is measured off this, so a
        view that loads something of a different size should say so -
        otherwise the wheel and WASD keep the last model's stride.

        `extent` is the size of the whole file, for a caller that is
        framing one part of it: the stride follows the part, and the
        smallest a frame may come in is measured against the whole -
        otherwise pressing F on something smaller each time would walk
        the stride down to nothing. Left out, the radius is the whole
        thing."""
        self.scene_radius = max(float(radius), 1e-6)
        self.scene_extent = (self.scene_radius if extent is None
                             else max(float(extent), 1e-6))
        self.camera_speed = self.scene_radius * SPEED_FRACTION
        self.camera_speed_min = self.camera_speed / SPEED_RANGE
        self.camera_speed_max = self.camera_speed * SPEED_RANGE

    @property
    def zoom_step(self):
        return self.camera_speed * ZOOM_FRACTION / SPEED_FRACTION

    def frame(self, centre, radius, heading=MODEL_HEADING,
              pitch=MODEL_PITCH, margin=2.5, lift=0.0):
        """Point the camera at a scene of `radius` around `centre`, from
        `heading` and `pitch`, far enough back to see all of it.

        The views build their matrix as rotate(pitch) * rotate(heading)
        * translate(camera), so this is that solved for the translation
        that lands `centre` `distance` in front of the camera. Also sets
        the scene radius, since it has just been told it.

        `lift` aims that far above the centre, as a fraction of the
        radius - see MODEL_LIFT."""
        self._clear_pending()
        self._velocity = [0.0, 0.0, 0.0]
        distance = max(radius * margin * 1.2, 1e-6)
        h, v = math.radians(heading), math.radians(pitch)
        aim_y = centre[1] + radius * lift
        self.camera_x = distance * math.cos(v) * math.sin(h) - centre[0]
        self.camera_y = -distance * math.sin(v) - aim_y
        self.camera_z = -distance * math.cos(v) * math.cos(h) - centre[2]
        self.camera_angle_h = heading
        self.camera_angle_v = pitch
        self.set_scene_radius(radius)
        # What a middle-drag will circle: exactly what was just framed.
        self.orbit_distance = distance
        self._framed = True

    def status_text(self):
        """The camera's own two lines of the stats overlay."""
        return (f"Camera: {self.camera_x:.2f}, {self.camera_y:.2f}, "
                f"{self.camera_z:.2f}\n"
                f"Rotation: h {self.camera_angle_h:.1f}°, "
                f"v {self.camera_angle_v:.1f}°")

    # --- movement ---

    def _forward(self):
        """Unit vector the camera is looking along."""
        h_rad = -math.radians(self.camera_angle_h)
        v_rad = math.radians(self.camera_angle_v)
        return (-math.sin(h_rad) * math.cos(v_rad),
                -math.sin(v_rad),
                -math.cos(h_rad) * math.cos(v_rad))

    def _eye(self):
        """Where the camera actually is, in world units.

        The views build their matrix as rotate * translate(camera), so
        a world point p lands at R * (p + camera) and the camera is
        wherever that comes out zero - which is -camera."""
        return (-self.camera_x, -self.camera_y, -self.camera_z)

    def _screen_axes(self):
        """(right, up) in world units - the directions a drag moves in.

        The view is Rx(pitch) * Ry(heading), so the world direction that
        appears as screen +X is Ry(-heading) applied to it, and screen
        +Y is that after Rx(-pitch)."""
        h = math.radians(self.camera_angle_h)
        v = math.radians(self.camera_angle_v)
        right = (math.cos(h), 0.0, math.sin(h))
        up = (math.sin(h) * math.sin(v), math.cos(v),
              -math.cos(h) * math.sin(v))
        return right, up

    def glide_to(self, centre, radius, margin=2.5, frames=GLIDE_FRAMES):
        """frame() on `centre` without the jump: the camera eases there over
        a few frames, keeping its angles and the scene's size."""
        self.flush()
        distance = max(radius * margin * 1.2, 1e-6)
        h = math.radians(self.camera_angle_h)
        v = math.radians(self.camera_angle_v)
        target = (distance * math.cos(v) * math.sin(h) - centre[0],
                  -distance * math.sin(v) - centre[1],
                  -distance * math.cos(v) * math.cos(h) - centre[2])
        self._glide = [(self.camera_x, self.camera_y, self.camera_z), target,
                       0, max(int(frames), 1)]
        self.orbit_distance = distance
        if getattr(self, "_glide_timer", None) is None:
            self._glide_timer = QTimer()
            self._glide_timer.timeout.connect(self._glide_step)
        self._glide_timer.start(GLIDE_INTERVAL_MS)

    def glide_to_points(self, points, margin=2.5):
        """Ease onto a cloud of points - what F does in every view.

        The selection, or the whole scene when nothing is picked, framed
        from the angle the camera already has. The stride the wheel and
        WASD take follows what is now on screen, the way it does when a
        view opens on a whole file; without that, zooming in on one
        polygon would still step by the size of the room around it.

        Points are in the units the view draws in, and a frame smaller
        than MIN_FRAME_RADIUS of the scene is framed at that instead, so
        one polygon does not put the camera inside it. Returns whether
        there was anything to frame."""
        scene = scene_of(points) if points is not None else None
        if scene is None:
            return False
        centre, radius = scene
        radius = max(radius, self.scene_extent * MIN_FRAME_RADIUS)
        self.set_scene_radius(radius, extent=self.scene_extent)
        self.glide_to(centre, radius, margin=margin)
        return True

    def glide_frame(self, centre, radius, heading=MODEL_HEADING,
                    pitch=MODEL_PITCH, margin=2.5, lift=0.0, frames=GLIDE_FRAMES):
        """frame(), eased in from wherever the camera is - position and
        angles both, the way F glides onto a selection. The first framing
        is set outright: the default camera sits under a level, and a
        glide from there shows it from below, culled see-through."""
        if not getattr(self, "_framed", False):
            self.frame(centre, radius, heading, pitch, margin, lift)
            return
        start = (self.camera_x, self.camera_y, self.camera_z,
                 self.camera_angle_h, self.camera_angle_v)
        self.frame(centre, radius, heading, pitch, margin, lift)
        target = (self.camera_x, self.camera_y, self.camera_z,
                  self.camera_angle_h, self.camera_angle_v)
        # The short way round.
        turn = (target[3] - start[3] + 180.0) % 360.0 - 180.0
        start = start[:3] + (target[3] - turn, start[4])
        (self.camera_x, self.camera_y, self.camera_z,
         self.camera_angle_h, self.camera_angle_v) = start
        self._glide = [start, target, 0, max(int(frames), 1)]
        if getattr(self, "_glide_timer", None) is None:
            self._glide_timer = QTimer()
            self._glide_timer.timeout.connect(self._glide_step)
        self._glide_timer.start(GLIDE_INTERVAL_MS)

    def _glide_step(self):
        start, target, step, frames = self._glide
        step += 1
        t = step / frames
        eased = t * t * (3.0 - 2.0 * t)
        values = [a + (b - a) * eased for a, b in zip(start, target)]
        self.camera_x, self.camera_y, self.camera_z = values[:3]
        if len(values) == 5:
            self.camera_angle_h, self.camera_angle_v = values[3:]
        self._glide[2] = step
        if step >= frames:
            self.stop_glide()
        self.widget.update()

    def stop_glide(self):
        timer = getattr(self, "_glide_timer", None)
        if timer is not None:
            timer.stop()

    def look_at(self, aim, distance):
        """Put the camera `distance` from `aim`, keeping its angles.

        The same solve frame() does, without touching the scene radius -
        which is what orbiting needs, since circling something does not
        change how big it is."""
        h, v = math.radians(self.camera_angle_h), math.radians(self.camera_angle_v)
        self.camera_x = distance * math.cos(v) * math.sin(h) - aim[0]
        self.camera_y = -distance * math.sin(v) - aim[1]
        self.camera_z = -distance * math.cos(v) * math.cos(h) - aim[2]

    def orbit_pivot(self):
        """The point a middle-drag circles: straight ahead, at the
        distance the view was last framed or zoomed to."""
        eye = self._eye()
        forward = self._forward()
        return tuple(eye[i] + forward[i] * self.orbit_distance
                     for i in range(3))

    def begin_orbit(self, panning=False):
        """Take the middle button. The pivot is worked out once, here,
        so it stays put for the whole drag instead of creeping forward
        as the camera moves."""
        if self.camera_mode:
            return                    # the freecam has the mouse
        self.flush()
        self.orbit_mode = "pan" if panning else "orbit"
        target = self.orbit_target() if (self.orbit_target and not panning) else None
        if target is not None:
            # Circle the named point: turned to face it, at the distance it
            # is now, so the first move does not jump the view.
            eye = self._eye()
            dx, dy, dz = (target[k] - eye[k] for k in range(3))
            self.orbit_distance = max(math.sqrt(dx * dx + dy * dy + dz * dz),
                                      self.scene_radius * MIN_ORBIT)
            flat = math.hypot(dx, dz)
            self.camera_angle_h = math.degrees(math.atan2(dx, -dz))
            self.camera_angle_v = max(-89.0, min(89.0, math.degrees(math.atan2(-dy, flat))))
            self._orbit_pivot = tuple(target)
            self.look_at(self._orbit_pivot, self.orbit_distance)
            self.widget.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        # What the middle of the view is looking at, out of the depth buffer,
        # so a drag circles the thing on screen - pushed out to ORBIT_REACH
        # when that is right in front of the camera.
        point = depth_pivot(self.widget)
        if point is not None:
            self.orbit_distance = max(math.dist(self._eye(), point),
                                      self.zoom_step * ORBIT_REACH,
                                      self.scene_radius * MIN_ORBIT)
        self._orbit_pivot = self.orbit_pivot()
        self.widget.setCursor(QCursor(
            Qt.CursorShape.SizeAllCursor if panning
            else Qt.CursorShape.ClosedHandCursor))

    def end_orbit(self):
        if self.orbit_mode is None:
            return
        self.orbit_mode = None
        self._orbit_pivot = None
        self.widget.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def begin_dolly(self):
        """Take the right button for zooming, on a trackpad.

        A drag is easier to hold at a rate than a flick of the scroll is
        to aim: this is the wheel's slide along the line of sight, in
        pixels of drag."""
        if self.camera_mode:
            return                    # the freecam has the mouse
        self.orbit_mode = "dolly"
        self._orbit_pivot = self.orbit_pivot()
        self.widget.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))

    def end_dolly(self):
        if self.orbit_mode == "dolly":
            self.orbit_mode = None
            self._orbit_pivot = None
            self.widget.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def dolly(self, dy):
        """Slide the camera along its line of sight by a drag: up towards
        what it is looking at, as a wheel scrolled up does."""
        self.zoom_notches(-dy / DOLLY_PIXELS_PER_NOTCH)

    @property
    def settings(self):
        return camera_settings()

    @property
    def smooth(self):
        return self.settings.smooth

    def orbit(self, dx, dy):
        """Swing around the held pivot by a mouse delta."""
        scale = self.mouse_sensitivity * self.settings.sensitivity
        if self.smooth:
            self._pending["orbit"][0] += dx * scale
            self._pending["orbit"][1] += dy * scale
            self._pending_pivot = self._orbit_pivot
            self._pending_distance = self.orbit_distance
            return
        self._apply_orbit(dx * scale, dy * scale, self._orbit_pivot, self.orbit_distance)

    def _apply_orbit(self, dh, dv, pivot, distance):
        self.camera_angle_h += dh
        self.camera_angle_v = max(-89.0, min(89.0, self.camera_angle_v + dv))
        if pivot is not None:
            self.look_at(pivot, distance)

    def _apply_look(self, dh, dv):
        self.camera_angle_h += dh
        self.camera_angle_v = max(-89.0, min(89.0, self.camera_angle_v + dv))

    def flush(self):
        """Finish every smoothed move at once - before something else
        takes the camera, so the rest of it cannot land afterwards."""
        pending = self._pending
        if any(pending["orbit"]):
            self._apply_orbit(*pending["orbit"], self._pending_pivot, self._pending_distance)
        if any(pending["look"]):
            self._apply_look(*pending["look"])
        if any(pending["pan"]):
            self._apply_pan(*pending["pan"])
        if pending["zoom"]:
            self._apply_zoom(pending["zoom"])
        self._clear_pending()

    def _clear_pending(self):
        self._pending = {"orbit": [0.0, 0.0], "look": [0.0, 0.0],
                         "pan": [0.0, 0.0], "zoom": 0.0}

    def _drain(self, fraction):
        """Apply `fraction` of each smoothed move; True if anything moved."""
        moved = False
        pending = self._pending
        def take(value):
            return value if abs(value) < SETTLE else value * fraction
        orbit = pending["orbit"]
        if any(orbit):
            dh, dv = take(orbit[0]), take(orbit[1])
            orbit[0] -= dh; orbit[1] -= dv
            self._apply_orbit(dh, dv, self._pending_pivot, self._pending_distance)
            moved = True
        look = pending["look"]
        if any(look):
            dh, dv = take(look[0]), take(look[1])
            look[0] -= dh; look[1] -= dv
            self._apply_look(dh, dv)
            moved = True
        pan = pending["pan"]
        if any(pan):
            dx, dy = take(pan[0]), take(pan[1])
            pan[0] -= dx; pan[1] -= dy
            self._apply_pan(dx, dy)
            moved = True
        if pending["zoom"]:
            step = take(pending["zoom"])
            pending["zoom"] -= step
            self._apply_zoom(step)
            moved = True
        return moved

    def pan(self, dx, dy):
        """Slide the view, and the point it orbits, with the mouse.

        The scene follows the pointer exactly rather than at some chosen
        rate: at the pivot's distance a viewport shows
        2 * distance * tan(fov / 2) of world, so one pixel is that over
        the widget's height. Whatever is under the cursor when the drag
        starts is still under it when it ends, at any zoom."""
        if self.smooth:
            self._pending["pan"][0] += dx
            self._pending["pan"][1] += dy
            return
        self._apply_pan(dx, dy)

    def _apply_pan(self, dx, dy):
        right, up = self._screen_axes()
        height = max(self.widget.height(), 1)
        span = 2.0 * self.orbit_distance * math.tan(math.radians(FIELD_OF_VIEW) / 2)
        step = span / height
        self.camera_x += (right[0] * dx - up[0] * dy) * step
        self.camera_y += (right[1] * dx - up[1] * dy) * step
        self.camera_z += (right[2] * dx - up[2] * dy) * step
        self._orbit_pivot = self.orbit_pivot()

    def handle_key_movement(self):
        """Every tick: ease pending moves in, then WASD."""
        fraction = 1.0 - math.exp(-0.016 / self.settings.catch_up)
        moved = self._drain(fraction) if self.smooth else False
        wanted = self._wasd() if self.camera_mode else (0.0, 0.0, 0.0)
        if self.smooth:
            self._velocity = [v + (w - v) * fraction for v, w in zip(self._velocity, wanted)]
            if not any(wanted) and max(map(abs, self._velocity)) < self.camera_speed * 0.01:
                self._velocity = [0.0, 0.0, 0.0]
        else:
            self._velocity = list(wanted)
        if any(self._velocity):
            self.camera_x += self._velocity[0]
            self.camera_y += self._velocity[1]
            self.camera_z += self._velocity[2]
            moved = True
        if moved:
            self.widget.update()

    def _wasd(self):
        """The step the held keys ask for this tick, as a camera delta."""
        speed_multiplier = 4.0 if self.keys_pressed[Qt.Key.Key_Shift] else 1.0
        current_speed = self.camera_speed * speed_multiplier

        h_rad = -math.radians(self.camera_angle_h)
        forward_x, forward_y, forward_z = self._forward()

        right_x = -math.cos(h_rad)
        right_z = math.sin(h_rad)

        x = y = z = 0.0
        if self.keys_pressed[Qt.Key.Key_W]:
            x -= forward_x * current_speed
            y -= forward_y * current_speed
            z -= forward_z * current_speed
        if self.keys_pressed[Qt.Key.Key_S]:
            x += forward_x * current_speed
            y += forward_y * current_speed
            z += forward_z * current_speed
        if self.keys_pressed[Qt.Key.Key_A]:
            x -= right_x * current_speed
            z -= right_z * current_speed
        if self.keys_pressed[Qt.Key.Key_D]:
            x += right_x * current_speed
            z += right_z * current_speed
        if self.keys_pressed[Qt.Key.Key_Q]:
            y += current_speed
        if self.keys_pressed[Qt.Key.Key_E]:
            y -= current_speed
        return (x, y, z)

    def zoom_notches(self, notches):
        """Move the camera along its line of sight, in wheel notches.

        Positive moves it towards what it is looking at, which is what
        one notch of a wheel scrolling up does. The wheel, a trackpad's
        scroll, a pinch and the right-drag dolly all come through here,
        so all four zoom at the same rate."""
        if not notches:
            return
        if self.camera_mode:
            self.camera_speed = max(
                self.camera_speed_min,
                min(self.camera_speed_max,
                    self.camera_speed * SPEED_STEP ** notches))
            self.widget.update()
            return
        if self.smooth:
            self._pending["zoom"] += notches
            return
        self._apply_zoom(notches)

    def _apply_zoom(self, notches):
        forward_x, forward_y, forward_z = self._forward()
        step = notches * self.zoom_step
        # Zooming walks the camera towards what it is orbiting, so the
        # pivot comes back by the same step - otherwise it runs ahead of
        # the camera and orbiting circles thin air. The camera is not
        # stopped at the pivot: it carries on through whatever is there.
        self.orbit_distance = max(self.scene_radius * MIN_ORBIT,
                                  self.orbit_distance - step)
        self.camera_x -= forward_x * step
        self.camera_y -= forward_y * step
        self.camera_z -= forward_z * step
        self.widget.update()

    def wheelEvent(self, event):
        """The wheel zooms, and so does a trackpad's two-finger scroll -
        with shift it pans instead, which is the one axis a trackpad has
        and a wheel mostly does not. In freecam the wheel sets how fast
        WASD moves rather than moving the camera. Both are measured
        against the scene, not in absolute units - see the module
        docstring."""
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            dx, dy = self._scroll_pixels(event)
            if dx or dy:
                self.pan(dx, dy)
                self.widget.update()
            return
        self.zoom_notches(self._scroll_notches(event))

    def _scroll_notches(self, event):
        """How far a scroll moved, in wheel notches.

        A trackpad reports pixels, dozens of small events per gesture,
        and has no notches to count: taking angleDelta there gives a
        fraction of a step per event and the view crawls. Pixels are used
        where they are offered, so a gesture moves the view by what the
        fingers did."""
        pixels = event.pixelDelta()
        if not pixels.isNull():
            return max(-MAX_SCROLL_NOTCHES,
                       min(MAX_SCROLL_NOTCHES,
                           pixels.y() / TRACKPAD_PIXELS_PER_NOTCH))
        return event.angleDelta().y() / 120.0

    def _scroll_pixels(self, event):
        """How far a scroll moved, in pixels - what pan() measures in."""
        pixels = event.pixelDelta()
        if not pixels.isNull():
            return pixels.x(), pixels.y()
        return (event.angleDelta().x() / 120.0 * WHEEL_PIXELS_PER_NOTCH,
                event.angleDelta().y() / 120.0 * WHEEL_PIXELS_PER_NOTCH)

    def native_gesture(self, event):
        """A trackpad's pinch: the zoom a wheel cannot give a Mac.

        Returns whether the gesture was one of ours, so the view can pass
        the rest of them on."""
        if event.type() != QEvent.Type.NativeGesture:
            return False
        if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_notches(event.value() * PINCH_NOTCHES_PER_VALUE)
            return True
        return False

    def begin_look(self):
        """Take the mouse for looking around: hide the pointer and warp
        it to the middle, which is where every move is measured from."""
        if self.camera_mode:
            return
        self.camera_mode = True
        # Where to put the pointer back when the button comes up. It is
        # warped to the middle on every move while looking, so it has to
        # be remembered here rather than read back later.
        self._restore_pos = QCursor.pos()
        self.display_center = [self.widget.width() // 2,
                               self.widget.height() // 2]
        self.widget.setCursor(QCursor(Qt.CursorShape.BlankCursor))
        QCursor.setPos(self.widget.mapToGlobal(QPoint(*self.display_center)))

    def end_look(self):
        """Give the mouse back, where it was picked up."""
        if not self.camera_mode:
            return
        self.camera_mode = False
        self.widget.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        if self._restore_pos is not None:
            QCursor.setPos(self._restore_pos)
            self._restore_pos = None

    def mousePressEvent(self, event):
        """The right button looks around, for as long as it is held; the
        middle button orbits, and pans with shift held.

        A trackpad has no middle button, so the same orbit is also there
        for the left button with the navigation key held (Option, on a
        Mac) - shift with it pans, and on the right button it slides the
        camera along its line of sight instead of looking around.

        The left button on its own is not touched here. A view with
        something to select uses it for that - see
        MDATViewer.mousePressEvent - and a view with nothing to select
        ignores it."""
        panning = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if event.button() == Qt.MouseButton.RightButton:
            if navigating(event):
                self.begin_dolly()
            else:
                self.begin_look()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.begin_orbit(panning=panning)
        elif (event.button() == Qt.MouseButton.LeftButton
                and navigating(event)):
            self.begin_orbit(panning=panning)
        self.last_pos = event.pos()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.end_dolly()
            self.end_look()
        elif event.button() in (Qt.MouseButton.MiddleButton,
                                Qt.MouseButton.LeftButton):
            self.end_orbit()

    def mouseMoveEvent(self, event):
        """Handle mouse movement"""
        if self.camera_mode:
            pos = event.position()
            dx = pos.x() - self.display_center[0]
            dy = pos.y() - self.display_center[1]

            # Looking is never smoothed: it follows the hand exactly, as
            # TombaEdit's does. Smoothing is for orbit, pan, zoom and WASD.
            scale = self.look_sensitivity * self.settings.sensitivity
            self._apply_look(dx * scale, dy * scale)

            QCursor.setPos(self.widget.mapToGlobal(QPoint(*self.display_center)))
            self.widget.update()
        elif self.orbit_mode is not None:
            # Plain deltas here, and the pointer left where it is: a
            # drag that circles something wants to be watched, unlike
            # the freecam's look, which warps the cursor to the middle
            # so it can turn forever.
            pos = event.pos()
            dx = pos.x() - self.last_pos.x()
            dy = pos.y() - self.last_pos.y()
            if self.orbit_mode == "pan":
                self.pan(dx, dy)
            elif self.orbit_mode == "dolly":
                self.dolly(dy)
            else:
                self.orbit(dx, dy)
            self.widget.update()

        self.last_pos = event.pos()

    def keyPressEvent(self, event):
        """Handle key presses"""
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = True
            if event.key() != Qt.Key.Key_Shift:
                self.stop_glide()

    def keyReleaseEvent(self, event):
        """Handle key releases"""
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = False


class CameraEventMixin:
    """Hands a widget's mouse and key events to its `camera_controls`.

    Inherited by every 3D view ahead of QOpenGLWidget, so none of them
    has to repeat these forwarding methods - and so a change to how the
    camera is driven lands in all of them at once. The widget only has
    to set self.camera_controls before any event can arrive, which for a
    QOpenGLWidget means in __init__."""

    def event(self, event):
        # A trackpad's pinch is a native gesture rather than a wheel, and
        # Qt hands it over as an event of its own - see
        # CameraControls.native_gesture. Everything else is none of our
        # business and goes where it would have gone.
        if (event.type() == QEvent.Type.NativeGesture
                and self.camera_controls.native_gesture(event)):
            return True
        return super().event(event)

    def wheelEvent(self, event):
        self.camera_controls.wheelEvent(event)

    def mousePressEvent(self, event):
        self.camera_controls.mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.camera_controls.mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        # Alt-tabbing away with the button down would otherwise leave
        # the pointer hidden and the view still turning - or, for the
        # middle button, the drag cursor stuck on.
        self.camera_controls.end_look()
        self.camera_controls.end_orbit()
        self.camera_controls.end_dolly()
        super().focusOutEvent(event)

    def mouseMoveEvent(self, event):
        self.camera_controls.mouseMoveEvent(event)

    def keyPressEvent(self, event):
        self.camera_controls.keyPressEvent(event)

    def keyReleaseEvent(self, event):
        self.camera_controls.keyReleaseEvent(event)


def selection_centre(points):
    """Middle of a selection's bounding box, or None for no selection."""
    if points is None:
        return None
    array = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(array):
        return None
    return tuple(float(v) for v in (array.min(axis=0) + array.max(axis=0)) / 2)


def install_camera_tools(viewer, selected_points=None, before=None):
    """Add the Camera menu and, given `selected_points`, an Orbit
    selection toggle to `viewer.toolbar`.

    `selected_points()` returns what is selected (faces, a part, an
    entry) in view units, or None when nothing is - then a middle-drag
    orbits the middle of the view as usual."""
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QMenu, QStyle, QToolButton, QWidgetAction, QSlider, QLabel, QWidget, QHBoxLayout
    toolbar = viewer.toolbar
    settings = camera_settings()
    if selected_points is not None:
        orbit = QAction(viewer.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
                        "Orbit selection", viewer)
        orbit.setCheckable(True)
        orbit.setChecked(True)
        orbit.setToolTip("Middle-drag circles the selected faces instead of whatever is "
                         "in the middle of the view. Off, or with nothing selected, "
                         "orbiting works as usual.")
        viewer.orbit_action = orbit
        viewer.camera_controls.orbit_target = lambda: (
            selection_centre(selected_points()) if orbit.isChecked() else None)
        toolbar.insertAction(before, orbit) if before is not None else toolbar.addAction(orbit)

    menu = QMenu(viewer)
    smooth = menu.addAction("Smooth camera")
    smooth.setCheckable(True)
    smooth.setToolTip("Ease orbit, pan, zoom and WASD movement instead of moving instantly. Right-drag looking is always instant.")

    def slider(title, name, low, high, tip):
        box = QWidget(menu)
        row = QHBoxLayout(box)
        row.setContentsMargins(24, 2, 10, 2)
        label = QLabel(title, box)
        value = QLabel(box)
        bar = QSlider(Qt.Orientation.Horizontal, box)
        bar.setRange(low, high)
        bar.setMinimumWidth(140)
        bar.setToolTip(tip)
        row.addWidget(label)
        row.addWidget(bar, 1)
        row.addWidget(value)

        def show(position):
            value.setText(f"{position / 100:.2f}×")

        def moved(position):
            settings.set(name, position / 100)
            show(position)
        bar.valueChanged.connect(moved)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)
        return bar, show

    responsiveness, show_r = slider("Responsiveness", "responsiveness", 25, 400,
                                    "How quickly the smoothed camera catches up. Higher is snappier.")
    sensitivity, show_s = slider("Mouse sensitivity", "sensitivity", 25, 300,
                                 "How far the camera turns per pixel of mouse movement.")

    def sync():
        smooth.setChecked(settings.smooth)
        for bar, show, name in ((responsiveness, show_r, "responsiveness"),
                                (sensitivity, show_s, "sensitivity")):
            bar.blockSignals(True)
            bar.setValue(round(getattr(settings, name) * 100))
            bar.blockSignals(False)
            show(bar.value())
        responsiveness.setEnabled(settings.smooth)

    def toggled(on):
        viewer.camera_controls.flush()
        settings.set("smooth", on)
        responsiveness.setEnabled(on)
    smooth.toggled.connect(toggled)
    menu.aboutToShow.connect(sync)
    sync()

    camera = QAction(viewer.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon),
                     "Camera", viewer)
    camera.setToolTip("Smooth camera, responsiveness and mouse sensitivity - shared by every 3D view.")
    camera.setMenu(menu)
    toolbar.insertAction(before, camera) if before is not None else toolbar.addAction(camera)
    button = toolbar.widgetForAction(camera)
    if isinstance(button, QToolButton):
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    viewer.camera_action = camera
    return camera

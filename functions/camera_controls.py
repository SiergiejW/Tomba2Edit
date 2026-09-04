# camera_controls.py
"""The freecam every 3D view is driven by, and the widget plumbing that
feeds it.

One module for both the MDAT and SMST views, so the two behave
identically: the same keys, the same mouse, and - the part that has to
be shared to work at all - the same idea of how far a step is.

SCALE

Nothing here moves the camera by an absolute distance. A level room is
thousands of world units across and a character a couple of hundred, and
at the scales the viewers draw them (gui/scld/scld_render.UNIT_SCALE and
gui/smst/smst_viewer.UNIT_SCALE) one fixed step cannot suit both: the
0.1-per-notch zoom this used to have is a reasonable nudge across a
room and 0.6% of the way to a character, which reads as a scroll wheel
that does nothing at all. So every step is a fraction of the scene the
view is looking at, and a view says how big that is by calling
set_scene_radius() - or frame(), which does it for you.
"""

from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QCursor
import math

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
# travel from the camera to the model.
ZOOM_FRACTION = 0.10

# How far WASD travels per frame, as a fraction of the scene radius.
SPEED_FRACTION = 0.02

# Freecam scrolling multiplies the speed rather than adding to it -
# five notches double it, at any scale.
SPEED_STEP = 1.15
SPEED_RANGE = 50.0

# Shown in the corner of every 3D view. Here so the two views can't
# describe the same controls differently.
CONTROLS_HINT = ("Right-drag: look around\n"
                 "Hold right + WASD: move | Q/E: up/down\n"
                 "Shift: fast | Scroll: zoom, speed while looking")


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


class CameraControls:
    def __init__(self, widget, scene_radius=DEFAULT_SCENE_RADIUS):
        self.widget = widget

        # Camera variables
        self.camera_y = 0.0
        self.camera_x = 0.0
        self.camera_z = -5.0
        self.mouse_sensitivity = 0.1
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

        # Timer for smooth movement
        self.key_timer = QTimer()
        self.key_timer.timeout.connect(self.handle_key_movement)
        self.key_timer.start(16)

    # --- scale ---

    def set_scene_radius(self, radius):
        """How big the thing on screen is, in the units the view draws
        it at. Every step the camera takes is measured off this, so a
        view that loads something of a different size should say so -
        otherwise the wheel and WASD keep the last model's stride."""
        self.scene_radius = max(float(radius), 1e-6)
        self.camera_speed = self.scene_radius * SPEED_FRACTION
        self.camera_speed_min = self.camera_speed / SPEED_RANGE
        self.camera_speed_max = self.camera_speed * SPEED_RANGE

    @property
    def zoom_step(self):
        return self.scene_radius * ZOOM_FRACTION

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
        distance = max(radius * margin, 1e-6)
        h, v = math.radians(heading), math.radians(pitch)
        aim_y = centre[1] + radius * lift
        self.camera_x = distance * math.cos(v) * math.sin(h) - centre[0]
        self.camera_y = -distance * math.sin(v) - aim_y
        self.camera_z = -distance * math.cos(v) * math.cos(h) - centre[2]
        self.camera_angle_h = heading
        self.camera_angle_v = pitch
        self.set_scene_radius(radius)

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

    def handle_key_movement(self):
        """Handle camera movement based on WASD keys"""
        if not self.camera_mode:
            return

        speed_multiplier = 4.0 if self.keys_pressed[Qt.Key.Key_Shift] else 1.0
        current_speed = self.camera_speed * speed_multiplier

        h_rad = -math.radians(self.camera_angle_h)
        forward_x, forward_y, forward_z = self._forward()

        right_x = -math.cos(h_rad)
        right_z = math.sin(h_rad)

        if self.keys_pressed[Qt.Key.Key_W]:
            self.camera_x -= forward_x * current_speed
            self.camera_y -= forward_y * current_speed
            self.camera_z -= forward_z * current_speed
        if self.keys_pressed[Qt.Key.Key_S]:
            self.camera_x += forward_x * current_speed
            self.camera_y += forward_y * current_speed
            self.camera_z += forward_z * current_speed
        if self.keys_pressed[Qt.Key.Key_A]:
            self.camera_x -= right_x * current_speed
            self.camera_z -= right_z * current_speed
        if self.keys_pressed[Qt.Key.Key_D]:
            self.camera_x += right_x * current_speed
            self.camera_z += right_z * current_speed
        if self.keys_pressed[Qt.Key.Key_Q]:
            self.camera_y += current_speed
        if self.keys_pressed[Qt.Key.Key_E]:
            self.camera_y -= current_speed

        self.widget.update()

    def wheelEvent(self, event):
        """In freecam the wheel sets how fast WASD moves; otherwise it
        moves the camera along its own line of sight. Both are measured
        against the scene, not in absolute units - see the module
        docstring."""
        scroll_amount = event.angleDelta().y() / 120
        if self.camera_mode:
            self.camera_speed = max(
                self.camera_speed_min,
                min(self.camera_speed_max,
                    self.camera_speed * SPEED_STEP ** scroll_amount))
        else:
            forward_x, forward_y, forward_z = self._forward()
            step = scroll_amount * self.zoom_step
            self.camera_x -= forward_x * step
            self.camera_y -= forward_y * step
            self.camera_z -= forward_z * step
        self.widget.update()

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
        """The right button looks around, for as long as it is held.

        The left button is not touched here. A view with something to
        select uses it for that - see MDATViewer.mousePressEvent - and a
        view with nothing to select ignores it."""
        if event.button() == Qt.MouseButton.RightButton:
            self.begin_look()
        self.last_pos = event.pos()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.end_look()

    def mouseMoveEvent(self, event):
        """Handle mouse movement"""
        if self.camera_mode:
            pos = event.position()
            dx = pos.x() - self.display_center[0]
            dy = pos.y() - self.display_center[1]

            self.camera_angle_h += dx * self.mouse_sensitivity
            self.camera_angle_v = max(-89.0, min(89.0,
                                                 self.camera_angle_v + dy * self.mouse_sensitivity))

            QCursor.setPos(self.widget.mapToGlobal(QPoint(*self.display_center)))
            self.widget.update()

        self.last_pos = event.pos()

    def keyPressEvent(self, event):
        """Handle key presses"""
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = True

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

    def wheelEvent(self, event):
        self.camera_controls.wheelEvent(event)

    def mousePressEvent(self, event):
        self.camera_controls.mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.camera_controls.mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        # Alt-tabbing away with the button down would otherwise leave
        # the pointer hidden and the view still turning.
        self.camera_controls.end_look()
        super().focusOutEvent(event)

    def mouseMoveEvent(self, event):
        self.camera_controls.mouseMoveEvent(event)

    def keyPressEvent(self, event):
        self.camera_controls.keyPressEvent(event)

    def keyReleaseEvent(self, event):
        self.camera_controls.keyReleaseEvent(event)

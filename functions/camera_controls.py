# camera_controls.py

from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QCursor
import math


class CameraControls:
    def __init__(self, widget):
        self.widget = widget

        # Camera variables
        self.camera_y = 0.0
        self.camera_x = 0.0
        self.camera_z = -5.0
        self.mouse_sensitivity = 0.1
        self.camera_speed = 0.1
        self.camera_speed_min = 0.001
        self.camera_speed_max = 4.0
        self.camera_angle_h = 0.0
        self.camera_angle_v = 0.0

        # Mouse tracking
        self.last_pos = QPoint()
        self.display_center = [widget.width() // 2, widget.height() // 2]
        self.widget.setMouseTracking(True)
        self.widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Camera mode
        self.camera_mode = False

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

    def handle_key_movement(self):
        """Handle camera movement based on WASD keys"""
        if not self.camera_mode:
            return

        speed_multiplier = 4.0 if self.keys_pressed[Qt.Key.Key_Shift] else 1.0
        current_speed = self.camera_speed * speed_multiplier

        h_rad = -math.radians(self.camera_angle_h)
        v_rad = math.radians(self.camera_angle_v)

        forward_x = -math.sin(h_rad) * math.cos(v_rad)
        forward_y = -math.sin(v_rad)
        forward_z = -math.cos(h_rad) * math.cos(v_rad)

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
        """Handle mouse wheel"""
        if self.camera_mode:
            scroll_amount = event.angleDelta().y() / 120
            self.camera_speed = max(self.camera_speed_min,
                                    min(self.camera_speed_max,
                                        self.camera_speed + scroll_amount * 0.005))
        else:
            scroll_amount = event.angleDelta().y() / 120
            h_rad = -math.radians(self.camera_angle_h)
            v_rad = math.radians(self.camera_angle_v)

            forward_x = -math.sin(h_rad) * math.cos(v_rad)
            forward_y = -math.sin(v_rad)
            forward_z = -math.cos(h_rad) * math.cos(v_rad)

            self.camera_x -= forward_x * scroll_amount * 0.1
            self.camera_y -= forward_y * scroll_amount * 0.1
            self.camera_z -= forward_z * scroll_amount * 0.1
        self.widget.update()

    def mousePressEvent(self, event):
        """Toggle camera mode"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.camera_mode = not self.camera_mode
            if self.camera_mode:
                self.widget.setCursor(QCursor(Qt.CursorShape.BlankCursor))
                QCursor.setPos(self.widget.mapToGlobal(QPoint(*self.display_center)))
            else:
                self.widget.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.last_pos = event.pos()

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
        else:
            dx = event.pos().x() - self.last_pos.x()
            dy = event.pos().y() - self.last_pos.y()

            if event.buttons() & Qt.MouseButton.LeftButton:
                self.camera_angle_h += dx
                self.camera_angle_v = max(-89.0, min(89.0, self.camera_angle_v + dy))

        self.last_pos = event.pos()
        self.widget.update()

    def keyPressEvent(self, event):
        """Handle key presses"""
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = True

    def keyReleaseEvent(self, event):
        """Handle key releases"""
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = False

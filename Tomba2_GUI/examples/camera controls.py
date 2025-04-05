from PyQt6 import QtGui, QtCore, QtWidgets
from PyQt6.QtOpenGLWidgets import QOpenGLWidget as OpenGLWidget
import OpenGL.GL as GL
import OpenGL.GLU as GLU
import sys
import numpy as np
from PIL import Image


class MainWindow(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__()

        self.resize(800, 600)
        self.setWindowTitle('First-Person Camera Control')

        self.opengl = GLWidget(self.width(), self.height())

        self.initGUI()

    def initGUI(self):
        self.rotate_button = QtWidgets.QPushButton('Rotate Cube', self)
        self.rotate_button.clicked.connect(self.opengl.toggle_cube_rotation)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.opengl)
        mainLayout.addWidget(self.rotate_button)
        self.setLayout(mainLayout)


class GLWidget(OpenGLWidget):

    def __init__(self, width, height, parent=None):
        super().__init__(parent)
        self.setMinimumSize(width, height)
        self.cube_angle = 0  # Separate angle for cube rotation
        self.rotating_cube = False
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_rotation)
        self.timer.start(16)  # Approx 60 FPS
        self.texture = None

        # Scaling factor for the cube
        self.scale_factor = 1.0
        self.scale_direction = -1

        # Camera variables
        self.camera_y = 0.0
        self.camera_x = 0.0
        self.camera_z = -4.0
        self.mouse_sensitivity = 0.1
        self.camera_speed = 0.1  # Smaller initial value
        self.camera_speed_min = 0.001  # Minimum camera speed
        self.camera_speed_max = 4.0  # Maximum camera speed
        self.camera_angle_h = 0.0  # Horizontal camera angle
        self.camera_angle_v = 0.0  # Vertical camera angle
        self.view_matrix = None

        # Mouse tracking
        self.mouse_move = [0, 0]
        self.display_center = [self.width() // 2, self.height() // 2]
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        # Camera mode
        self.camera_mode = False

        # Key states for smooth movement
        self.keys_pressed = {
            QtCore.Qt.Key.Key_W: False,
            QtCore.Qt.Key.Key_S: False,
            QtCore.Qt.Key.Key_A: False,
            QtCore.Qt.Key.Key_D: False,
            QtCore.Qt.Key.Key_E: False,
            QtCore.Qt.Key.Key_Q: False,
            QtCore.Qt.Key.Key_Shift: False,  # Track Shift key
        }

        # Timer for smooth key movement
        self.key_timer = QtCore.QTimer()
        self.key_timer.timeout.connect(self.handle_key_movement)
        self.key_timer.start(16)

    def initializeGL(self):
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_TEXTURE_2D)
        self.init_geometry()
        self.load_texture("test.png")

        # Initialize view matrix
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        self.view_matrix = GL.glGetFloatv(GL.GL_MODELVIEW_MATRIX)

    def init_geometry(self):
        # Your existing init_geometry code remains the same
        self.vertices = np.array([
            # Front face
            -1.0, -1.0,  1.0,  1.0, 1.0, 1.0,  0.0, 0.0,
             1.0, -1.0,  1.0,  1.0, 1.0, 1.0,  1.0, 0.0,
             1.0,  1.0,  1.0,  1.0, 1.0, 1.0,  1.0, 1.0,
            -1.0,  1.0,  1.0,  0.1, 0.1, 1.0,  0.0, 1.0,

            # Back face
            -1.0, -1.0, -1.0,  0.0, 1.1, 0.5,  0.0, 0.0,
             1.0, -1.0, -1.0,  0.0, 0.1, 0.6,  1.0, 0.0,
             1.0,  1.0, -1.0,  1.0, 0.1, 0.7,  1.0, 1.0,
            -1.0,  1.0, -1.0,  0.0, 0.1, 1.8,  0.0, 1.0
        ], dtype=np.float32)

        self.indices = np.array([
            # Front face
            0, 1, 2, 2, 3, 0,
            # Back face
            4, 5, 6, 6, 7, 4,
            # Left face
            4, 0, 3, 3, 7, 4,
            # Right face
            1, 5, 6, 6, 2, 1,
            # Top face
            3, 2, 6, 6, 7, 3,
            # Bottom face
            4, 5, 1, 1, 0, 4
        ], dtype=np.uint32)

        self.vertex_buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vertex_buffer)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, self.vertices.nbytes, self.vertices, GL.GL_STATIC_DRAW)

        self.index_buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.index_buffer)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, self.indices.nbytes, self.indices, GL.GL_STATIC_DRAW)

    def load_texture(self, image_path):
        try:
            img = Image.open(image_path).convert("RGB")
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            img_data = np.array(img, dtype=np.uint8)

            self.texture = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)

            # Set filtering for minification (distant textures use mipmaps)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)

            # Set filtering for magnification (close textures should be unfiltered)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)

            # Upload the texture data and generate mipmaps
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGB,
                img.width, img.height, 0,
                GL.GL_RGB, GL.GL_UNSIGNED_BYTE, img_data
            )
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        except Exception as e:
            print(f"Failed to load texture: {e}")
            self.texture = None

    def resizeGL(self, width, height):
        aspect = width / height if height != 0 else 1
        GL.glViewport(0, 0, width, height)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GLU.gluPerspective(45, aspect, 0.1, 50.0)

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()

        # Apply camera transformations
        GL.glRotatef(self.camera_angle_v, 1.0, 0.0, 0.0)  # Look up/down
        GL.glRotatef(self.camera_angle_h, 0.0, 1.0, 0.0)  # Look left/right
        GL.glTranslatef(self.camera_x, self.camera_y, self.camera_z)

        # Save the camera matrix
        GL.glPushMatrix()

        # Apply cube rotation (independent of camera)
        GL.glRotatef(self.cube_angle, 0.0, 1.0, 0.0)

        # Apply scaling to the cube
        #GL.glScalef(1, self.scale_factor, 1)

        # Draw the cube
        self.draw_cube()

        # Restore the camera matrix
        GL.glPopMatrix()

    def draw_cube(self):
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_COLOR_ARRAY)
        GL.glEnableClientState(GL.GL_TEXTURE_COORD_ARRAY)

        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vertex_buffer)
        GL.glVertexPointer(3, GL.GL_FLOAT, 8 * self.vertices.itemsize, None)
        GL.glColorPointer(3, GL.GL_FLOAT, 8 * self.vertices.itemsize, GL.GLvoidp(3 * self.vertices.itemsize))
        GL.glTexCoordPointer(2, GL.GL_FLOAT, 8 * self.vertices.itemsize, GL.GLvoidp(6 * self.vertices.itemsize))

        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.index_buffer)

        # Bind texture only for the front face
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
        GL.glDrawElements(GL.GL_TRIANGLES, 6, GL.GL_UNSIGNED_INT, None)

        # Disable texture for the other faces
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glDrawElements(GL.GL_TRIANGLES, len(self.indices) - 6, GL.GL_UNSIGNED_INT,
                          GL.GLvoidp(6 * self.indices.itemsize))

        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glDisableClientState(GL.GL_COLOR_ARRAY)
        GL.glDisableClientState(GL.GL_TEXTURE_COORD_ARRAY)

    def update_rotation(self):
        if self.rotating_cube:
            self.cube_angle -= 0.5  # Update cube rotation
            self.cube_angle %= 360

            # Oscillate the scaling factor
            #self.scale_factor += 0.01 * self.scale_direction
            #if self.scale_factor > 1.2 or self.scale_factor < 0.8:
            #    self.scale_direction *= -1

        self.update()

    def toggle_cube_rotation(self):
        self.rotating_cube = not self.rotating_cube

    def mousePressEvent(self, event):
        # Toggle camera mode on mouse click
        self.camera_mode = not self.camera_mode
        if self.camera_mode:
            self.setMouseTracking(True)
            self.cursor().setPos(self.mapToGlobal(QtCore.QPoint(*self.display_center)))
            self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.BlankCursor))  # Hide cursor
        else:
            self.setMouseTracking(False)
            self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.ArrowCursor))  # Show cursor

    def mouseMoveEvent(self, event):
        if self.camera_mode:
            pos = event.position()
            self.mouse_move = [pos.x() - self.display_center[0], pos.y() - self.display_center[1]]
            self.camera_angle_h += self.mouse_move[0] * self.mouse_sensitivity  # Update camera horizontal angle
            self.camera_angle_v += self.mouse_move[1] * self.mouse_sensitivity  # Update camera vertical angle

            # Center the mouse
            self.cursor().setPos(self.mapToGlobal(QtCore.QPoint(*self.display_center)))

    def wheelEvent(self, event):
        scroll_amount = event.angleDelta().y() / 120
        self.camera_speed += scroll_amount * 0.005  # Smaller increment for finer control
        self.camera_speed = max(self.camera_speed_min, min(self.camera_speed, self.camera_speed_max))  # Clamp speed

    def keyPressEvent(self, event):
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = True

    def keyReleaseEvent(self, event):
        if event.key() in self.keys_pressed:
            self.keys_pressed[event.key()] = False

    def handle_key_movement(self):
        if self.camera_mode:
            # Apply speed multiplier if Shift key is pressed
            speed_multiplier = 4.0 if self.keys_pressed[QtCore.Qt.Key.Key_Shift] else 1.0
            current_speed = self.camera_speed * speed_multiplier

            # Convert angles to radians
            h_rad = -np.radians(self.camera_angle_h)
            v_rad = np.radians(self.camera_angle_v)

            # Calculate normalized forward vector based on camera direction
            forward_x = -np.sin(h_rad) * np.cos(v_rad)
            forward_y = -np.sin(v_rad)
            forward_z = -np.cos(h_rad) * np.cos(v_rad)

            # Calculate right vector (perpendicular to forward in XZ plane)
            right_x = -np.cos(h_rad)
            right_z = np.sin(h_rad)

            # Apply movement in camera space
            if self.keys_pressed[QtCore.Qt.Key.Key_W]:
                self.camera_x -= forward_x * current_speed
                self.camera_y -= forward_y * current_speed
                self.camera_z -= forward_z * current_speed
            if self.keys_pressed[QtCore.Qt.Key.Key_S]:
                self.camera_x += forward_x * current_speed
                self.camera_y += forward_y * current_speed
                self.camera_z += forward_z * current_speed
            if self.keys_pressed[QtCore.Qt.Key.Key_A]:
                self.camera_x -= right_x * current_speed
                self.camera_z -= right_z * current_speed
            if self.keys_pressed[QtCore.Qt.Key.Key_D]:
                self.camera_x += right_x * current_speed
                self.camera_z += right_z * current_speed
            if self.keys_pressed[QtCore.Qt.Key.Key_Q]:
                self.camera_y += current_speed
            if self.keys_pressed[QtCore.Qt.Key.Key_E]:
                self.camera_y -= current_speed

            self.update()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


"""The world origin, drawn in the 3D views.

Every one of them shows something modelled around a point that is not
on screen anywhere: an SMST's parts each sit around their own origin,
an animation moves a skeleton about the root, and a room's collision
and its geometry only line up because they share one. Without a mark
there is nothing to say where that point is, or which way the axes run,
so "the model is offset" and "the camera is somewhere odd" look alike.

Three lines and nothing else: X red, Y green, Z blue, in the same
right-handed layout the rest of the program uses (see game_rest, where
a bone's game-space (x, y, z) becomes the viewer's (z, -y, x)). Each is
drawn both ways from the origin, with the negative half dimmed, so the
direction an axis runs is readable rather than guessed.

The viewers do not share a shader, but all three declare the same first
two attributes - position at 0, colour at 1 - so one buffer serves them
all. The caller binds its own program and sets whatever uniforms that
program needs before calling draw().
"""
import ctypes

import numpy as np
from OpenGL import GL
from PyQt6.QtOpenGL import QOpenGLBuffer, QOpenGLVertexArrayObject

# How long the arms are, as a fraction of the scene's radius - big
# enough to find, small enough not to sit over the thing being looked
# at. A model about 1.5 across gets arms of about 0.3.
LENGTH = 0.2

# What the half of each axis running the negative way is multiplied by.
# Present but clearly the back half, so the origin reads as a corner
# rather than a crossing.
BEHIND = 0.35

AXES = ((1.0, 0.25, 0.25),      # X
        (0.35, 1.0, 0.35),      # Y
        (0.35, 0.55, 1.0))      # Z


class OriginAxes:
    """The origin marker's buffers, built once per GL context."""

    def __init__(self):
        self.vao = QOpenGLVertexArrayObject()
        self.positions = QOpenGLBuffer()
        self.colors = QOpenGLBuffer()
        self._built = False
        self._radius = None

    def _build(self, radius):
        """(Re)fill the buffers for a scene this big."""
        arm = max(radius, 1e-6) * LENGTH
        points, shades = [], []
        for axis in range(3):
            bright = AXES[axis]
            dim = tuple(c * BEHIND for c in bright)
            for far, shade in ((arm, bright), (-arm, dim)):
                start = [0.0, 0.0, 0.0]
                end = [0.0, 0.0, 0.0]
                end[axis] = far
                points.extend(start + end)
                shades.extend(list(shade) + list(shade))

        if not self.vao.isCreated():
            self.vao.create()
        self.vao.bind()
        for buffer, data in ((self.positions, points), (self.colors, shades)):
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            block = np.array(data, dtype=np.float32)
            buffer.allocate(block.tobytes(), block.nbytes)
        # Both attributes come off their own buffer, so each is bound
        # again while pointing at it.
        self.positions.bind()
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.colors.bind()
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.vao.release()
        self._built = True
        self._radius = radius

    def draw(self, radius, width=2.0):
        """Draw the marker, sized to a scene of this radius.

        The caller's shader is already bound; nothing here touches a
        uniform, since the three views do not agree on what they have."""
        if not self._built or self._radius != radius:
            self._build(radius)
        self.vao.bind()
        previous = GL.glGetFloatv(GL.GL_LINE_WIDTH)
        GL.glLineWidth(width)
        GL.glDrawArrays(GL.GL_LINES, 0, 12)
        GL.glLineWidth(previous)
        self.vao.release()

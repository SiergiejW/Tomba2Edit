"""The OpenGL context this program asks for, and what that driver allows.

A QOpenGLWidget gets whatever QSurfaceFormat the application has as its
default, and Qt's own default is a legacy 2.1 context. On Windows and
Linux that default becomes a compatibility context, which is what the
shaders here (`#version 330 core`) and the 1D textures the palettes are
uploaded through (see formats/geometry/mdat_viewer.py) are both happy
with - so those platforms are deliberately left exactly as they are. On
macOS the same default is a real 2.1 context, and every shader fails:

    ERROR: 0:2: '' :  version '330' is not supported
    ERROR: 0:3: 'layout' : syntax error: syntax error

so the 3D views come up empty - MDAT geometry, SMST models, SCLD
collision and the level editor alike. macOS is asked for 3.3 core
instead, and answers with its 4.1 core profile, which still accepts the
1D textures and the sampler1D the shaders declare.

One more thing is not platform-specific. A core profile offers only the
line widths the driver reports in GL_ALIASED_LINE_WIDTH_RANGE, and macOS
reports [1, 1]; asking for the 2-pixel outlines and markers here is then
GL_INVALID_VALUE. PyOpenGL raises on that, and an exception out of
paintGL is an abort() with every pending edit gone (see main.py), not a
thin line. Every glLineWidth in the program therefore goes through
set_line_width(), which asks for the widest line the driver will take -
on a driver that allows the width asked for, that is the width asked
for, so nothing changes where it already worked.
"""
import sys

from OpenGL import GL
from PyQt6.QtGui import QSurfaceFormat

# What the shaders are written for: `#version 330 core` in
# formats/geometry/mdat_viewer.py, formats/models/smst_viewer.py,
# formats/collision/scld_viewer.py and gui/level/level_viewer.py.
GL_VERSION = (3, 3)

# The one platform whose default context these shaders cannot compile in.
# Asked for there, and nowhere else - see the module docstring.
WANTS_CORE_PROFILE = sys.platform == "darwin"


def surface_format(alpha_buffer_size=None):
    """The format a 3D view is built with.

    Built on the application's default, so a view that wants something
    extra keeps the version and the profile that default carries:
    a bare QSurfaceFormat() here is what used to drop them on macOS and
    leave the SMST view - and the level editor, which is also an
    SMSTViewer - with shaders that could not compile.

    `alpha_buffer_size` is for the SMST view, which draws a transparent
    background into it for the GIF export."""
    fmt = QSurfaceFormat.defaultFormat()
    if WANTS_CORE_PROFILE:
        fmt.setVersion(*GL_VERSION)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    if alpha_buffer_size is not None:
        fmt.setAlphaBufferSize(alpha_buffer_size)
    return fmt


def install_default_format():
    """Make sure Qt builds the 3D views with a context they can use.

    Has to run before the QApplication is constructed: the viewers are
    made in the window's constructor, and every widget keeps whatever
    default was in place when it was created. A no-op on every platform
    but macOS."""
    if not WANTS_CORE_PROFILE:
        return
    fmt = surface_format()
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)


def set_line_width(width):
    """glLineWidth, at the widest line this driver will take.

    Called from inside paintGL, so it never raises: a driver that
    refuses the width it reports gets a one-pixel line rather than the
    program's death."""
    try:
        low, high = GL.glGetFloatv(GL.GL_ALIASED_LINE_WIDTH_RANGE)
    except Exception:
        low, high = 1.0, 1.0
    try:
        GL.glLineWidth(min(max(float(width), float(low)), float(high)))
    except Exception:
        # A driver that will not even take what it reports gets a
        # one-pixel line; a driver that refuses that too gets nothing, and
        # the frame is drawn with whatever width was already set. Either
        # way paintGL finishes - a line width is not worth the abort an
        # exception out of it would cause.
        try:
            GL.glLineWidth(1.0)
        except Exception:
            pass

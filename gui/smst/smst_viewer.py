"""SMST viewer - the MDAT 3D view, for a model set instead of a level.

Same shaders, same freecam, same CLUT-per-draw texturing as
gui/mdat/mdat_viewer.py, because the polygons are the same polygons.
What is different is what an SMST is: a list of parts, all of them
modelled around their own origin, so drawing one straight off draws
every part inside every other one (see gui/smst/smst_parser.py). The
two things this view adds are for that - a part list that can hide,
isolate and highlight any of them, and a Spread button that lays them
out on a grid so a model can be looked at part by part.
"""
import ctypes
import math

import numpy as np
from OpenGL import GL
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QImage, QMatrix4x4, QVector2D
from PyQt6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QMessageBox, QPushButton, QSplitter, QStyle, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from functions.camera_controls import (
    CONTROLS_HINT, MODEL_HEADING, MODEL_LIFT, MODEL_PITCH, CameraControls,
    CameraEventMixin, scene_of,
)
from functions import psx_vram
from functions.format_detect import FormatError
from gui.clut_animation import ClutAnimationMixin
from gui.origin_axes import OriginAxes
from functions import gltf_export
from gui import export_dialog, polygon_pick, theme
from gui.texture_panel import TexturePanel
from gui.smst import smst_edit
from functions import labels
from functions import placement
from gui.smst.smst_parser import parse_smst, read_smst_bytes

# Which columns of the part table hold the name somebody typed and the
# checkbox that shows or hides a part. Name leads, since that's what a
# named model actually gets picked out by; the part number is what a
# hex editor or a print statement still needs, so it stays right next
# to it rather than disappearing.
NAME_COLUMN = 0
PART_COLUMN = 1

# World units per GL unit. A level MDAT is thousands of units across and
# is drawn at 1000 (gui/scld/scld_render.UNIT_SCALE); a character is
# about 200, so it gets its own scale rather than arriving invisible.
UNIT_SCALE = 100.0

# How far apart the parts sit when spread out, as a multiple of the
# biggest part - enough of a gap to see where one ends.
SPREAD_GAP = 1.35

# What the parts that aren't selected fade to while one is highlighted.
DIMMED_ALPHA = 0.15

# The PSX's four semi-transparency modes, held in bits 5-6 of a face's
# texture-page byte. B is what is already in the framebuffer, F the
# incoming pixel. They apply only where the face's draw code sets the
# semi-transparency bit AND the texel's palette entry sets STP.
HALF = 0        # B/2 + F/2
ADD = 1         # B + F
SUBTRACT = 2    # B - F
QUARTER = 3     # B + F/4

# What weight a blended texel is drawn at in each mode. The blend
# function supplies the rest of the sum; this is the part of it that
# varies per texel, so it goes through the shader instead.
WEIGHTS = {HALF: 0.5, ADD: 1.0, SUBTRACT: 1.0, QUARTER: 0.25}

# The selection outline, matching gui/mdat/mdat_viewer.py: yellow for the
# picked polygon, a dimmer amber for the rest of the part it sits in.
POLYGON_OUTLINE = (1.0, 1.0, 0.2)
GROUP_OUTLINE = (0.75, 0.55, 0.1)

# The placeholder drawn where a spread-out part has no geometry. Dim and
# grey on purpose: it marks a hole in the model, and should not read as
# something that is there.
EMPTY_MARKER_COLOR = (0.42, 0.45, 0.50)
# How much of its grid cell the marker fills.
EMPTY_MARKER_SIZE = 0.4

# PSX draw modes, by the type byte a packet carries - the same names
# gui/mdat/mdat_panel.py uses, for the same bits.
BLEND_NAMES = {0: "half", 1: "add", 2: "subtract", 3: "quarter"}


def _runs_text(numbers, limit=6):
    """[72, 73, 74, 75, 76, 77] as "72-77" - part numbers for the stats
    line, where an asset pack's animated water is a run of neighbours
    and listing them one by one fills the overlay."""
    runs = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    text = [f"{a}" if a == b else f"{a}-{b}" for a, b in runs[:limit]]
    if len(runs) > limit:
        text.append("...")
    return ", ".join(text) or "-"


class SMSTViewer(ClutAnimationMixin, CameraEventMixin, QOpenGLWidget):
    # (group index, polygon index), either of which may be None, so a
    # list beside the view can follow a pick in it.
    selection_changed = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_data = None
        self.source = None
        self.blob = None
        self.camera_controls = CameraControls(self)

        self.vao = QOpenGLVertexArrayObject()
        self.vertex_buffer = QOpenGLBuffer()
        self.color_buffer = QOpenGLBuffer()
        self.texcoord_buffer = QOpenGLBuffer()
        self.index_buffer = QOpenGLBuffer()
        self.shader_program = QOpenGLShaderProgram()

        self.index_texture = None
        self.vram_raw_bytes = bytearray()
        # Set by MainWindow when it knows which skeleton this area's
        # models are posed on, so an export can be rigged rather than a
        # heap of loose parts. None just means nobody has said.
        self.export_bones = None
        self.export_name = None
        self.vram_qimage = None
        self.clut_map = {}                  # CLUT address -> GL texture id

        # A QOpenGLWidget has no usable context until Qt has painted it
        # once, and a file can be selected in the tree well before that -
        # so everything here is worked out on the CPU when it's asked
        # for and uploaded from _sync_gl() at the top of paintGL.
        self._arrays = None                 # (positions, colors, texcoords, indices)
        self._clut_arrays = {}              # CLUT address -> 16x4 uint8
        self._clut_transparency = {}        # CLUT address -> whether it blends
        # Palettes whose 16 entries have changed since the last paint -
        # what an animation leaves behind, flushed by _sync_gl.
        self._cluts_dirty = set()
        self._geometry_dirty = False
        # Only the vertex positions changed - see refresh_positions. A
        # pose is the common case by far and it touches nothing else,
        # so it does not pay for the full rebuild.
        self._positions_dirty = False
        self._vram_dirty = False

        # One entry per (part, CLUT) run of indices: everything paintGL
        # needs to draw that run and decide whether to draw it at all.
        self.draw_ranges = []
        self.hidden_groups = set()
        self.highlighted_group = None

        # One polygon picked out of the model, and the outline it is
        # drawn with - see gui/polygon_pick.py, which MDAT shares.
        self.selected_polygon = None
        self.outline_vao = QOpenGLVertexArrayObject()
        self.outline_vbo = QOpenGLBuffer()
        self.outline_cbo = QOpenGLBuffer()
        self.outline_vertex_count = 0
        self._outline_arrays = None
        # The placeholders for parts with no geometry, drawn only while
        # spread out - see _empty_marker_lines.
        self.marker_vao = QOpenGLVertexArrayObject()
        self.marker_vbo = QOpenGLBuffer()
        self.marker_cbo = QOpenGLBuffer()
        self.marker_vertex_count = 0
        self._marker_arrays = None
        self._spread_step = 1.0
        # Rebuilt lazily, and thrown away whenever the pose or the
        # spread moves the vertices out from under it.
        self._pick_vertices = None
        self._pick_faces = None
        self._face_polygon = None
        # An animation frame's transforms, or None for the rest pose -
        # see set_pose and gui/anmp/anmp_viewer.py.
        self.pose = None
        self.pose_pivots = None
        # Which group the animation's first limb drives. Normally 0 -
        # limb i is group i - but an asset pack holds several objects
        # and an animation may drive one of them: the Machine
        # Animation's two limbs are an oven's base and lid, groups 12
        # and 13 of a twenty-group room. See ANMPViewer's First group.
        self.pose_first_group = 0
        # {spare part: the limb it stands in for}. A spare sits past the
        # animated limbs, so it has no transform of its own - without
        # this it stays put while the rest of the model moves, which is
        # what made a swapped-in variation look frozen.
        self.pose_spares = {}
        # On by default: stacked at the origin is how the file has the
        # parts, but it is not how anyone wants to first see a model.
        self.spread = True
        # How big the loaded model is, in GL units - the clip planes are
        # set from it. A character is about 1.5 across and a level asset
        # pack over 200, and a fixed 0.1-100 frustum can only show one
        # of those.
        self.scene_radius = 0.0

        self.texture_mode_enabled = True
        self.culling_enabled = True
        # The world origin, drawn over the model - see gui/origin_axes.py.
        self.show_origin = False
        self.origin_axes = OriginAxes()

        # Animated textures - see gui/clut_animation.py. An asset pack
        # animates the same way its area's room does, and out of the
        # same table: parts 72-77 of AREA_04's Fishermen's Town pack are
        # its water.
        self.uv_offsets = {}        # CLUT address -> (du, dv) in atlas units
        self.init_clut_animation()

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

        self.texture_mode_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton),
            "Texture Mode", self)
        self.texture_mode_action.setCheckable(True)
        self.texture_mode_action.setChecked(True)
        self.texture_mode_action.toggled.connect(self.toggle_texture_mode)
        self.toolbar.addAction(self.texture_mode_action)

        self.culling_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            "Backface Culling", self)
        self.culling_action.setCheckable(True)
        self.culling_action.setChecked(self.culling_enabled)
        self.culling_action.toggled.connect(self.toggle_culling)
        self.toolbar.addAction(self.culling_action)

        self.spread_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Spread Parts", self)
        self.spread_action.setCheckable(True)
        self.spread_action.setChecked(self.spread)
        self.spread_action.setToolTip(
            "Lay the parts out on a grid. Nothing in an SMST says where a "
            "part belongs - that is in the animation data - so stacked at "
            "the origin is how the file actually has them.")
        self.spread_action.toggled.connect(self.toggle_spread)
        self.toolbar.addAction(self.spread_action)

        frame_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Frame Model", self)
        frame_action.triggered.connect(self.frame_model)
        self.toolbar.addAction(frame_action)

        self.origin_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
            "Origin", self)
        self.origin_action.setCheckable(True)
        self.origin_action.setChecked(self.show_origin)
        self.origin_action.setToolTip(
            "Mark the world origin: X red, Y green, Z blue, with the "
            "negative half of each axis dimmed. An SMST's parts are each "
            "modelled around their own origin, so this is where they are "
            "all stacked before a skeleton stands them up.")
        self.origin_action.toggled.connect(self.toggle_origin)
        self.toolbar.addAction(self.origin_action)
        self.toolbar.addAction(self.make_animate_action())

        # Kept as an attribute so a view that embeds this one can take
        # it away: the ANMP viewer offers its own export, which writes
        # the animation as well, and two Export buttons on one screen
        # is one too many.
        self.export_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            "Export glTF", self)
        self.export_action.triggered.connect(self.export_to_gltf)
        self.toolbar.addAction(self.export_action)

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
        self.controls_label.setStyleSheet(self.stats_label.styleSheet())
        self.controls_label.setText(CONTROLS_HINT)
        self.controls_label.raise_()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addStretch()

    # --- loading -----------------------------------------------------

    def set_vram(self, vram_bytes, qimage):
        """The VRAM this model's textures are cut out of. Handed the
        area's own VRAM with AREA_01's merged in - a trail model's
        pages are only ever in AREA_01's (see smst_parser)."""
        self.vram_raw_bytes = vram_bytes or bytearray()
        if qimage is not None and qimage.format() != QImage.Format.Format_RGBA8888:
            qimage = qimage.convertToFormat(QImage.Format.Format_RGBA8888)
        self.vram_qimage = qimage
        self._vram_dirty = True
        self.update()

    def load_smst_data(self, dat_file_path, address, size):
        """Load the SMST at `address`. Returns False (having said why)
        rather than raising, so a mislabelled row can't take the window
        down with it."""
        try:
            # The same bytes twice over - once parsed for the model,
            # once kept whole for copy/paste (see gui/smst/smst_edit.py)
            # - so both come from read_smst_bytes() and neither can be
            # the disc's copy while the other is a staged edit's. That
            # split used to happen here: the model came from load_smst(),
            # which does check a staged edit, but self.blob was read
            # straight off disk regardless - so reselecting an edited
            # model after looking at something else showed it correctly
            # but pasted a SECOND part onto the pre-edit bytes, quietly
            # dropping the first paste.
            blob = read_smst_bytes(dat_file_path, address, size)
            self.model_data = parse_smst(blob, address=address)
        except (FormatError, OSError, ValueError) as e:
            print(f"Error loading SMST data at 0x{address:X}: {e}")
            self.model_data = None
            self.draw_ranges = []
            self.source = None
            self.blob = None
            self.update()
            return False
        self.source = (dat_file_path, address, size)
        self.blob = blob

        self._settle(reset_view=True)
        name = self._model_name()
        named = f' "{name}"' if name else ""
        print(f"selected: SMST @ 0x{address:X}{named}  size 0x{size:X}  "
              f"{len(self.model_data['groups'])} group(s)  "
              f"{self.model_data['tri_count']} tris  "
              f"{self.model_data['quad_count']} quads")
        self.frame_model()
        return True

    def _model_name(self):
        """This model's own name, if it has been given one - see
        gui/smst/smst_viewer.SMSTPanel._name_of, which does the same
        lookup for one of its parts."""
        if not self.blob:
            return ""
        content = labels.content_key(self.blob)
        if not content:
            return ""
        return placement.model_name(placement.load_model_names(), content)

    def _part_name(self, group_index):
        """One part's own name, the same way."""
        if not self.blob:
            return ""
        content = labels.content_key(self.blob)
        if not content:
            return ""
        return placement.model_name(placement.load_model_names(), content,
                                    group_index)

    def show_blob(self, blob):
        """Draw an SMST that is in memory rather than on the disc.

        What a paste needs: the edited bytes have to be on screen before
        anything is saved, or there is no way to tell whether the paste
        did what was wanted. The camera and what is hidden are left
        alone - the model is the same model with one part swapped, and
        re-framing it would throw away the view you were looking at it
        from."""
        try:
            model = parse_smst(bytes(blob), address=self.model_data["address"]
                               if self.model_data else 0)
        except (FormatError, ValueError) as e:
            print(f"Error reading the edited SMST: {e}")
            return False
        self.blob = bytes(blob)
        self.model_data = model
        self._settle(reset_view=False)
        return True

    def _settle(self, reset_view):
        """Rebuild everything that hangs off model_data.

        `reset_view` is for a genuinely new file; a paste keeps the
        camera, the hidden parts and the pose it already had."""
        if reset_view:
            self.hidden_groups = set()
            self.highlighted_group = None
            self.pose = None
            self.pose_pivots = None
        else:
            # The parts are the same parts, but a polygon index is not
            # the same polygon any more.
            self.hidden_groups = {g for g in self.hidden_groups
                                  if g < len(self.model_data["groups"])}
        self.selected_polygon = None
        self._outline_arrays = (np.zeros(0, dtype=np.float32),
                                np.zeros(0, dtype=np.float32))
        self._invalidate_pick_cache()
        self.prepare_buffers()
        self.update()

    @property
    def groups(self):
        return self.model_data["groups"] if self.model_data else []

    # --- geometry ----------------------------------------------------

    def _spread_offsets(self):
        """Where each part's centre is moved to when spread out: a
        square grid in reading order, big enough for the biggest part.

        EVERY part takes a cell, empty ones included. Skipping them - as
        this used to - closed the gap up, so the nth thing on screen was
        not part n and the grid could not be counted along to find a
        part by its number. An empty cell is drawn as an outline instead
        (see _empty_marker_lines), which says "part 7 is empty" where a
        closed-up grid said nothing at all."""
        groups = list(self.groups)
        drawn = [g for g in groups if not g.empty]
        if not drawn:
            return {}
        # Sized off the 90th percentile rather than the largest part.
        # On a character they're the same number, but an asset pack has
        # a handful of enormous parts (a water surface across a whole
        # room) among small ones, and spacing the grid for those leaves
        # every other part a dot.
        step = max(float(np.percentile([g.radius * 2 for g in drawn], 90))
                   * SPREAD_GAP, 1.0)
        columns = max(1, math.ceil(math.sqrt(len(groups))))
        rows = math.ceil(len(groups) / columns)
        self._spread_step = step
        offsets = {}
        for n, group in enumerate(groups):
            cx, cy, cz = group.centre
            col, row = n % columns, n // columns
            offsets[group.index] = (
                (col - (columns - 1) / 2) * step - cx,
                ((rows - 1) / 2 - row) * step - cy,
                -cz,
            )
        return offsets

    def _empty_marker_lines(self):
        """A dashed-looking square at each empty part's cell.

        Only while spread out: stacked at the origin every cell is the
        same point, and a pile of identical squares says nothing. An
        empty part has no vertices, so its offset IS its cell centre."""
        if not self.spread or not self.model_data:
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
        empties = [g for g in self.groups if g.empty]
        if not empties:
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
        offsets = self._spread_offsets()
        half = getattr(self, "_spread_step", 1.0) * 0.5 * EMPTY_MARKER_SIZE
        positions, colors = [], []
        for group in empties:
            at = offsets.get(group.index)
            if at is None:
                continue
            x, y, z = at
            corners = [(x - half, y - half, z), (x + half, y - half, z),
                       (x + half, y + half, z), (x - half, y + half, z)]
            # The square, plus its diagonals - an outline alone reads as
            # a part that happens to be flat, a crossed one does not.
            pairs = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2), (1, 3)]
            for a, b in pairs:
                positions.extend(corners[a])
                positions.extend(corners[b])
                colors.extend(EMPTY_MARKER_COLOR * 2)
        return (np.array(positions, dtype=np.float32) / UNIT_SCALE,
                np.array(colors, dtype=np.float32))

    def set_pose(self, transforms, pivots):
        """Pose the parts from an animation frame.

        `transforms` is one (rotation, offset) per group, and `pivots`
        the point each group turns about - see gui/anmp/skeleton.py.
        None puts the model back in its rest pose. Spread and pose are
        mutually exclusive: a model laid out on a grid isn't a pose."""
        self.pose = transforms
        self.pose_pivots = pivots
        if transforms is not None and self.spread:
            self.spread_action.setChecked(False)   # rebuilds on its own
            return
        if self.model_data:
            self.refresh_positions()
        self.update()

    def refresh_positions(self):
        """Recompute just the vertex positions for a new pose.

        Everything else a frame needs - the colours, the UVs, the index
        runs, the palettes - is the same from one frame to the next, so
        rebuilding it per frame was most of what playback cost. A
        twenty-part model took 4.3ms a frame where a six-part one took
        0.5, and at 90 ticks a second that is a third of the budget
        spent re-uploading textures that had not changed."""
        if self._arrays is None:
            self.prepare_buffers()
            return
        positions, colors, tex_coords, indices = self._arrays
        self._arrays = (self._positions().flatten(), colors, tex_coords, indices)
        self._positions_dirty = True
        # The vertices just moved, so the picking arrays and the outline
        # built off them are stale.
        self._invalidate_pick_cache()
        self._build_outline()

    def _positions(self):
        """Every vertex in GL units, with the spread or the pose applied."""
        verts = np.array(self.model_data["vertices"], dtype=np.float32)
        if self.pose is not None:
            for group in self.groups:
                if not group.vertex_count:
                    continue
                which = group.index - self.pose_first_group
                if not 0 <= which < len(self.pose):
                    # A spare part moves with whatever it replaces.
                    which = self.pose_spares.get(group.index)
                if which is None or not 0 <= which < len(self.pose):
                    continue
                rotation, offset = self.pose[which]
                pivot = self.pose_pivots[which]
                at = group.first_vertex
                block = verts[at:at + group.vertex_count].astype(np.float64)
                verts[at:at + group.vertex_count] = (
                    (block - pivot) @ rotation.T + offset).astype(np.float32)
        elif self.spread:
            offsets = self._spread_offsets()
            for group in self.groups:
                offset = offsets.get(group.index)
                if offset is None or not group.vertex_count:
                    continue
                at = group.first_vertex
                verts[at:at + group.vertex_count] += np.array(offset, dtype=np.float32)
        return verts / UNIT_SCALE

    def prepare_buffers(self):
        """Work the vertex, colour, UV and index arrays out on the CPU.
        Uploading them is _sync_gl()'s job, since there may not be a
        context yet when a file is picked in the tree."""
        self.draw_ranges = []
        self._arrays = None
        self._invalidate_pick_cache()
        # The spread grid is worked out in here, so the placeholders
        # standing on it are rebuilt at the same time.
        self._marker_arrays = self._empty_marker_lines()
        # A new model means new palette textures, so anything bound to
        # the old ones has to go; load_animations() rebinds once
        # the groups below exist.
        self.clear_clut_animations()
        self.uv_offsets = {}
        self._clut_arrays = {}
        self._clut_transparency = {}
        self._cluts_dirty = set()
        if not self.model_data or not self.model_data.get("vertices"):
            return

        # Indices are grouped by part first and by CLUT within it, so a
        # part is one contiguous stretch of the buffer and hiding it is
        # skipping a few draws rather than rebuilding anything.
        indices = []
        ranges = []
        faces = self.model_data["faces"]
        info = self.model_data["texture_info"]
        for group in self.groups:
            by_clut = {}
            for f in range(group.first_face, group.first_face + group.face_count):
                entry = info[f]
                clut, transparent = entry[1], entry[2]
                blend = entry[3] if len(entry) > 3 else 0
                # Split by blend mode as well as by palette: the mode is
                # per face, and the four of them cannot be drawn in one
                # call because each needs its own blend function.
                key = (clut, transparent, blend)
                by_clut.setdefault(key, []).extend(faces[f])
            for (clut, transparent, blend), face_indices in by_clut.items():
                ranges.append((group.index, clut, len(indices) * 4,
                               len(face_indices), transparent, blend))
                indices.extend(face_indices)
                if clut not in self._clut_arrays:
                    self._clut_transparency[clut] = transparent
                    self._clut_arrays[clut] = self._clut_from_vram(clut, transparent)

        self.draw_ranges = ranges
        self._arrays = (
            self._positions().flatten(),
            np.array(self.model_data["vertex_colors"], dtype=np.float32).flatten(),
            np.array(self.model_data["texture_coords"], dtype=np.float32).flatten(),
            np.array(indices, dtype=np.uint32),
        )
        self._geometry_dirty = True

    def _sync_gl(self):
        """Push whatever changed since the last frame into the context.
        Called from paintGL, which is the first place a QOpenGLWidget's
        context is reliably current."""
        if self._vram_dirty:
            self._vram_dirty = False
            if self.index_texture is not None:
                GL.glDeleteTextures([self.index_texture])
                self.index_texture = None
            if self.vram_qimage is not None:
                ptr = self.vram_qimage.bits()
                ptr.setsize(self.vram_qimage.sizeInBytes())
                self.index_texture = GL.glGenTextures(1)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture)
                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA,
                                self.vram_qimage.width(), self.vram_qimage.height(),
                                0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, ptr.asstring())
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER,
                                   GL.GL_NEAREST)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER,
                                   GL.GL_NEAREST)

        if self._cluts_dirty:
            # An animation moved a palette on a frame. Nothing but the
            # texture's 16 entries changes - the draw calls are already
            # grouped and bound by palette - so this rewrites them where
            # they stand. Skipped when the geometry is being rebuilt
            # below, which uploads every palette afresh anyway.
            if not self._geometry_dirty:
                for address in self._cluts_dirty:
                    tex_id = self.clut_map.get(address)
                    array = self._clut_arrays.get(address)
                    if tex_id is None or array is None:
                        continue
                    GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
                    GL.glTexSubImage1D(GL.GL_TEXTURE_1D, 0, 0, 16, GL.GL_RGBA,
                                       GL.GL_UNSIGNED_BYTE, array)
            self._cluts_dirty = set()

        if self._positions_dirty and not self._geometry_dirty:
            # The cheap path: same mesh, moved.
            self._positions_dirty = False
            if self._arrays is not None and self.vertex_buffer.isCreated():
                positions = self._arrays[0]
                self.vao.bind()
                self.vertex_buffer.bind()
                self.vertex_buffer.allocate(positions.tobytes(), positions.nbytes)
                GL.glEnableVertexAttribArray(0)
                GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
                self.index_buffer.bind()
                self.vao.release()
                return

        if not self._geometry_dirty:
            return
        self._geometry_dirty = False
        self._positions_dirty = False

        if self.clut_map:
            GL.glDeleteTextures(list(self.clut_map.values()))
        self.clut_map = {address: self._upload_clut(array)
                         for address, array in self._clut_arrays.items()}

        positions, colors, tex_coords, indices = self._arrays
        if not self.index_buffer.isCreated():
            self.index_buffer.create()
        self.index_buffer.bind()
        self.index_buffer.allocate(indices.tobytes(), indices.nbytes)

        self.vao.bind()
        for buffer, array, size, location in (
                (self.vertex_buffer, positions, 3, 0),
                (self.color_buffer, colors, 3, 1),
                (self.texcoord_buffer, tex_coords, 2, 2)):
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            buffer.allocate(array.tobytes(), array.nbytes)
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location, size, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        self.index_buffer.bind()
        self.vao.release()

    def _clut_from_vram(self, address, transparent=False):
        """One 16-colour palette out of the VRAM, in the same 5-bit
        BGR555 the PSX stores it as - the same read
        MDATViewer.extract_clut_from_vram does.

    The PSX decides transparency per TEXEL, not per polygon. A palette
    entry is 16 bits: five each of B, G, R and, at the top, STP. What
    that bit means depends on the primitive:

      word == 0x0000            never drawn, whatever the primitive is
      STP set, primitive blends blended against what is behind it
      STP set, primitive opaque drawn opaque
      STP clear                 drawn opaque, ALWAYS

    The last line is the one that matters here. A primitive carrying the
    semi-transparency bit does not make the whole polygon see-through -
    it only enables blending for the texels whose palette entry asks for
    it. Every boss pig is built from faces that all carry that bit, and
    their palettes are about 95% STP-clear, so the hardware draws them
    solid; blending the lot made them ghosts. The water pig is the
    exception that proves it - 89% of its entries DO set STP, and it is
    meant to look like water."""
        return self._clut_from_bytes(
            bytes(self.vram_raw_bytes[address:address + 32]), transparent)

    @staticmethod
    def _clut_from_bytes(raw, transparent=False):
        """32 raw BGR555 bytes as the 16 RGBA entries the shader
        samples. Split out of _clut_from_vram above because an animated
        palette's bytes come from the area's overlay rather than from
        VRAM (see functions/clut_anim.py) and have to be read the same
        way, STP bit and all."""
        clut = []
        for i in range(16):
            at = i * 2
            if at + 1 >= len(raw):
                word = 0
            else:
                word = raw[at] | (raw[at + 1] << 8)
            r = (word & 0x1F) * 8
            g = ((word >> 5) & 0x1F) * 8
            b = ((word >> 10) & 0x1F) * 8
            if word == 0:
                alpha = 0
            elif transparent and (word & 0x8000):
                alpha = 128
            else:
                alpha = 255
            clut.append([r, g, b, alpha])
        return np.array(clut, dtype=np.uint8)

    @staticmethod
    def _upload_clut(clut_array):
        tex_id = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
        GL.glTexImage1D(GL.GL_TEXTURE_1D, 0, GL.GL_RGBA, 16, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, clut_array)
        GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        return tex_id

    # --- animated palettes -------------------------------------------

    def animated_clut_addresses(self):
        """ClutAnimationMixin's hook - every palette this model draws
        through. The groups are built by prepare_buffers()."""
        return self._clut_arrays.keys()

    def animation_source(self):
        """ClutAnimationMixin's hook - what to look for UV animations in."""
        return self.vram_raw_bytes, self.model_data

    def apply_uv_offsets(self, offsets):
        """ClutAnimationMixin's hook - shift a group's UVs. A uniform set
        per draw range, so no geometry is rebuilt to make water flow."""
        self.uv_offsets.update(offsets)
        self.update()

    def apply_clut_palettes(self, palettes):
        """ClutAnimationMixin's hook - put palettes on screen.

        Written to the CPU-side arrays and left for _sync_gl, the way
        everything else in this view is: a file can be picked in the
        tree long before Qt has given the widget a context, so nothing
        here touches GL outside a paint."""
        for address, raw in palettes:
            if address not in self._clut_arrays:
                continue
            transparent = self._clut_transparency.get(address, False)
            self._clut_arrays[address] = (
                self._clut_from_bytes(raw, transparent) if raw is not None
                else self._clut_from_vram(address, transparent))
            self._cluts_dirty.add(address)
        self.update()

    # --- what's on screen --------------------------------------------

    def toggle_texture_mode(self, checked):
        self.texture_mode_enabled = checked
        self.update()

    def toggle_origin(self, checked):
        self.show_origin = checked
        self.update()

    def toggle_culling(self, checked):
        # Applied in paintGL rather than here, for the same reason the
        # uploads are - there may be no context yet.
        self.culling_enabled = checked
        self.update()

    def toggle_spread(self, checked):
        self.spread = checked
        if self.model_data:
            self.prepare_buffers()
            self.frame_model()
        self.update()

    def set_group_hidden(self, index, hidden):
        if hidden:
            self.hidden_groups.add(index)
        else:
            self.hidden_groups.discard(index)
        self.update()

    def set_hidden_groups(self, hidden):
        self.hidden_groups = set(hidden)
        self.update()

    def set_highlighted_group(self, index):
        self.highlighted_group = index
        self.update()

    # --- picking one polygon ------------------------------------------

    @property
    def polygons(self):
        return (self.model_data or {}).get("polygons") or ()

    def picked_polygon(self):
        """The picked polygon's record, or None.

        Not called `selected`: LevelViewer subclasses this and keeps the
        instance it has selected in `self.selected`, which shadowed the
        method and turned every call into "int is not callable" the
        moment anything was picked in the level editor."""
        if self.selected_polygon is None:
            return None
        return self.polygons[self.selected_polygon]

    def select(self, polygon=None):
        """Pick one polygon, or nothing.

        Picking one highlights the part it belongs to as well, since a
        polygon is only ever looked at as part of its group."""
        if polygon is not None and not 0 <= polygon < len(self.polygons):
            polygon = None
        self.selected_polygon = polygon
        group = self.polygons[polygon]["group"] if polygon is not None else None
        self._build_outline()
        self.update()
        self.selection_changed.emit(group, polygon)

    def describe_selection(self):
        """The picked polygon as one addressable line, or None."""
        polygon = self.picked_polygon()
        if polygon is None:
            return None
        model = self.model_data
        where = f"SMST @ 0x{model['address']:X}"
        group = model["groups"][polygon["group"]]
        name = self._part_name(group.index)
        label = f'group {group.index} ("{name}")' if name else f"group {group.index}"
        owner = f"{label} (@ 0x{model['address'] + group.offset:X})"
        return f"{where}  {polygon_pick.describe_polygon(polygon, owner=owner)}"

    def _invalidate_pick_cache(self):
        self._pick_vertices = None
        self._face_polygon = None

    def _model_view_projection(self):
        """The same matrix paintGL draws with, so a click can be turned
        back into a ray through the model."""
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

    def pick(self, x, y):
        """Which polygon is under the widget point, or None."""
        if not self.polygons:
            return None
        ray = polygon_pick.ray_through(self._model_view_projection(), x, y,
                                       self.width(), self.height())
        if ray is None:
            return None
        if self._pick_vertices is None:
            # The posed or spread positions, not the rest ones, so what
            # gets picked is what is actually on screen.
            self._pick_vertices = self._positions().astype(np.float64)
            self._pick_faces, self._face_polygon = polygon_pick.build_face_index(
                self.model_data["faces"], self.polygons,
                len(self.model_data["faces"]))
        # A hidden part is not on screen, so it cannot be clicked through
        # the gap where it is not being drawn.
        drawable = None
        if self.hidden_groups:
            hidden = np.array([p["group"] in self.hidden_groups
                               for p in self.polygons], dtype=bool)
            drawable = ~hidden[self._face_polygon]
        return polygon_pick.nearest_polygon(
            ray[0], ray[1], self._pick_vertices, self._pick_faces,
            self._face_polygon, drawable)

    def mousePressEvent(self, event):
        # Left-click picks. The camera is on the right button (see
        # functions/camera_controls.py), so the left one is free for it.
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            point = event.position().toPoint()
            self.select(self.pick(point.x(), point.y()))
            line = self.describe_selection()
            print(f"selected: {line}" if line else "selected: nothing")
            return
        super().mousePressEvent(event)

    def _build_outline(self):
        """The line segments the selection is drawn with. Left on the
        CPU - there may be no GL context yet - and uploaded by paintGL."""
        positions, colors = [], []
        polygon = self.picked_polygon()
        if polygon is not None:
            verts = (self._pick_vertices if self._pick_vertices is not None
                     else self._positions())
            group = self.model_data["groups"][polygon["group"]]
            for i in range(group.first_polygon,
                           group.first_polygon + group.polygon_count):
                other = self.polygons[i]
                color = (POLYGON_OUTLINE if i == self.selected_polygon
                         else GROUP_OUTLINE)
                segments = polygon_pick.outline_segments(other, verts)
                positions.extend(segments)
                colors.extend(color * (len(segments) // 3))
        self._outline_arrays = (np.array(positions, dtype=np.float32),
                                np.array(colors, dtype=np.float32))

    def _sync_outline(self):
        if self._outline_arrays is None:
            return
        positions, colors = self._outline_arrays
        self._outline_arrays = None
        self.outline_vertex_count = positions.size // 3
        if not self.outline_vertex_count:
            return
        if not self.outline_vao.isCreated():
            self.outline_vao.create()
        self.outline_vao.bind()
        for buffer, array, location in ((self.outline_vbo, positions, 0),
                                        (self.outline_cbo, colors, 1)):
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            buffer.allocate(array.tobytes(), array.nbytes)
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location, 3, GL.GL_FLOAT, GL.GL_FALSE,
                                     0, None)
        self.outline_vao.release()

    def _draw_outline(self):
        """The selection, drawn untextured and on top of the model."""
        self._sync_outline()
        if not self.outline_vertex_count:
            return
        self.shader_program.setUniformValue("useTextures", False)
        self.shader_program.setUniformValue("alpha", 1.0)
        GL.glDisable(GL.GL_DEPTH_TEST)
        self.outline_vao.bind()
        GL.glDrawArrays(GL.GL_LINES, 0, self.outline_vertex_count)
        self.outline_vao.release()
        GL.glEnable(GL.GL_DEPTH_TEST)

    def _sync_markers(self):
        if self._marker_arrays is None:
            return
        positions, colors = self._marker_arrays
        self._marker_arrays = None
        self.marker_vertex_count = positions.size // 3
        if not self.marker_vertex_count:
            return
        if not self.marker_vao.isCreated():
            self.marker_vao.create()
        self.marker_vao.bind()
        for buffer, array, location in ((self.marker_vbo, positions, 0),
                                        (self.marker_cbo, colors, 1)):
            if not buffer.isCreated():
                buffer.create()
            buffer.bind()
            buffer.allocate(array.tobytes(), array.nbytes)
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location, 3, GL.GL_FLOAT, GL.GL_FALSE,
                                     0, None)
        self.marker_vao.release()

    def _draw_empty_markers(self):
        """Where the parts with no geometry sit on the spread grid.

        Depth-tested, unlike the selection outline: a placeholder is a
        thing standing in the grid among the others, not an annotation
        over the top of them, so a part in front should hide it."""
        self._sync_markers()
        if not self.marker_vertex_count:
            return
        self.shader_program.setUniformValue("useTextures", False)
        self.shader_program.setUniformValue("alpha", 1.0)
        self.marker_vao.bind()
        GL.glDrawArrays(GL.GL_LINES, 0, self.marker_vertex_count)
        self.marker_vao.release()

    def export_to_gltf(self):
        """Write the model out, rigged if a skeleton has been found.

        The bones come from whatever the ANMP viewer worked out for this
        area, so a model exported while its animation is open carries
        the same skeleton it is being posed on here."""
        if not self.model_data:
            QMessageBox.warning(self, "Nothing to export", "No SMST is loaded.")
            return
        file_path, unlit = export_dialog.ask_model_path(
            self, "Save model", self.export_name or "model")
        if not file_path:
            return
        try:
            write = (gltf_export.write_gltf if file_path.lower().endswith(".gltf")
                     else gltf_export.write_glb)
            write(file_path, self.model_data, self.vram_raw_bytes,
                  groups=self.groups, bones=self.export_bones,
                  name=self.export_name or "model",
                  skip=self.hidden_groups, unlit=unlit)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        rigged = " with its skeleton" if self.export_bones else ""
        QMessageBox.information(
            self, "Exported",
            f"Wrote the model{rigged} and "
            f"{len({e[1] for e in self.model_data.get('texture_info') or ()})} "
            f"baked palette texture(s).")

    # --- camera ------------------------------------------------------

    def frame_model(self, heading=MODEL_HEADING, pitch=MODEL_PITCH):
        """Put the whole model in shot, from the angle a character reads
        best at. Everything the camera does after that - how far a wheel
        notch moves it, how fast WASD flies - is measured off the size
        this finds (see functions/camera_controls.py)."""
        scene = scene_of(self._positions()) if self.model_data else None
        if scene is None:
            return
        centre, radius = scene
        self.scene_radius = radius
        self.camera_controls.frame(centre, radius, heading, pitch,
                                   lift=MODEL_LIFT)
        self.update()

    # --- GL ----------------------------------------------------------

    def initializeGL(self):
        GL.glClearColor(0.1, 0.1, 0.1, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        self.shader_program = QOpenGLShaderProgram()
        if not self.shader_program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Vertex,
                """
                #version 330 core
                layout(location = 0) in vec3 position;
                layout(location = 1) in vec3 color;
                layout(location = 2) in vec2 texCoord;
                uniform mat4 modelViewProjection;
                out vec3 fragColor;
                out vec2 fragTexCoord;
                void main() {
                    gl_Position = modelViewProjection * vec4(position, 1.0);
                    fragColor = color;
                    fragTexCoord = texCoord;
                }
                """):
            print("Vertex shader compilation failed:", self.shader_program.log())

        if not self.shader_program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment,
                """
                #version 330 core
                in vec2 fragTexCoord;
                in vec3 fragColor;
                out vec4 outColor;

                uniform sampler2D indexTexture;
                uniform sampler1D clutTexture;
                uniform bool useTextures;
                uniform float alpha;
                // Which texels this pass wants: 0 all, 1 only the ones
                // the hardware draws opaque, 2 only the ones it blends.
                // A semi-transparent face carries both - the palette
                // decides per texel - and a blend function is per draw,
                // so they are drawn in separate passes.
                uniform int texelClass;
                // What weight a blended texel gets, which is the part of
                // the PSX's blend mode that a blend function cannot say.
                uniform float blendWeight;
                // Whole frames along the texture page, for the surfaces
                // that animate by UV - see functions/uv_anim.py.
                uniform vec2 uvOffset;

                void main() {
                    if (useTextures) {
                        // The atlas holds each 4-bit index as index * 17
                        // over 255, so this comes back a whisker either
                        // side of a whole number - round it to one, then
                        // read the MIDDLE of that palette entry. Sampling
                        // at index / 16.0 is the entry's own edge, and a
                        // whisker short of it is the entry before.
                        float index = floor(texture(indexTexture, fragTexCoord + uvOffset).r * 15.0 + 0.5);
                        vec4 clutColor = texture(clutTexture, (index + 0.5) / 16.0);
                        if (clutColor.a < 0.01)
                            discard;
                        // Alpha 0.5 out of the palette means the entry
                        // set its STP bit and the hardware blends it;
                        // 1.0 means it does not, whatever the primitive
                        // asked for.
                        bool blended = clutColor.a < 0.9;
                        if (texelClass == 1 && blended) discard;
                        if (texelClass == 2 && !blended) discard;
                        outColor = clutColor * vec4(fragColor, 1.0);
                        outColor.a = (blended ? blendWeight : 1.0) * alpha;
                    } else {
                        outColor = vec4(fragColor, alpha);
                    }
                }
                """):
            print("Fragment shader compilation failed:", self.shader_program.log())

        if not self.shader_program.link():
            print("Shader program linking failed:", self.shader_program.log())

        self.vao.create()
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.color_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.texcoord_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.index_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)

    def resizeGL(self, w, h):
        self.camera_controls.display_center = [w // 2, h // 2]
        GL.glViewport(0, 0, w, h)
        self._place_labels()

    def _place_labels(self):
        self.stats_label.adjustSize()
        self.stats_label.move(6, self.height() - self.stats_label.height() - 6)
        self.controls_label.adjustSize()
        self.controls_label.move(self.width() - self.controls_label.width() - 6,
                                 self.height() - self.controls_label.height() - 6)

    def _update_stats_label(self):
        model = self.model_data
        cam = self.camera_controls
        parts = len(self.groups)
        shown = parts - len(self.hidden_groups)
        line = (f"Parts: {shown}/{parts}  "
                f"Tris: {model.get('tri_count', 0) if model else 0}  "
                f"Quads: {model.get('quad_count', 0) if model else 0}")
        moving = set(self.clut_animations) | set(self.uv_animations)
        if moving:
            animated = sorted({group for group, clut, _o, _c, _t, _b
                               in self.draw_ranges if clut in moving})
            what = []
            if self.clut_animations:
                what.append(f"{len(self.clut_animations)} palette(s)")
            if self.uv_animations:
                what.append(f"{len(self.uv_animations)} UV")
            line += (f"  Animated: {', '.join(what)} on "
                     f"part(s) {_runs_text(animated)}")
        self.stats_label.setText(line + "\n" + cam.status_text())
        self._place_labels()

    def draw_backdrop(self):
        """Anything that goes behind the model, drawn after the clear
        and before the geometry. Nothing here; the level view puts the
        area's background picture there - see gui/level/level_viewer.py."""

    def paintGL(self):
        self._sync_gl()
        GL.glClearColor(*theme.view_background((0.1, 0.1, 0.1)), 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        self.draw_backdrop()
        if self.culling_enabled:
            GL.glEnable(GL.GL_CULL_FACE)
            GL.glCullFace(GL.GL_BACK)
        else:
            GL.glDisable(GL.GL_CULL_FACE)
        self._update_stats_label()
        if not self.model_data or not self.draw_ranges:
            return

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

        if not self.shader_program.bind():
            return
        self.shader_program.setUniformValue("modelViewProjection", projection * view)

        # No VRAM means no palettes, and an untextured model is far more
        # use than a model that discards every fragment it draws.
        textured = self.texture_mode_enabled and bool(self.vram_raw_bytes)
        self.shader_program.setUniformValue("useTextures", textured)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.index_texture or 0)
        self.shader_program.setUniformValue("indexTexture", 0)
        self.shader_program.setUniformValue("clutTexture", 1)

        self.vao.bind()
        self.shader_program.setUniformValue("texelClass", 0)
        self.shader_program.setUniformValue("blendWeight", 1.0)
        self._draw_pass(transparent=False)

        # A face flagged semi-transparent still draws most of its texels
        # solid - the palette decides, per texel. So its opaque half goes
        # down first, with depth writing, exactly like ordinary geometry.
        modes = sorted({r[5] for r in self.draw_ranges if r[4]})
        if modes:
            self.shader_program.setUniformValue("texelClass", 1)
            self._draw_pass(transparent=True)

            # Then the texels that really do blend, one pass per mode.
            # The PSX has four; treating them all as additive - which is
            # what this did - washes out everything that asked for the
            # half-and-half mix, and that is most of them. The boss pigs
            # are 70% HALF, which is why they came out as ghosts.
            GL.glDepthMask(GL.GL_FALSE)
            self.shader_program.setUniformValue("texelClass", 2)
            for mode in modes:
                self._set_blend(mode)
                self.shader_program.setUniformValue("blendWeight", WEIGHTS[mode])
                self._draw_pass(transparent=True, blend=mode)
            GL.glBlendEquation(GL.GL_FUNC_ADD)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glDepthMask(GL.GL_TRUE)
            self.shader_program.setUniformValue("texelClass", 0)
            self.shader_program.setUniformValue("blendWeight", 1.0)
        self.vao.release()
        self._draw_empty_markers()
        self._draw_outline()
        if self.show_origin:
            # Untextured and at full alpha, whatever the model is drawn
            # with - the marker is not part of the art.
            self.shader_program.setUniformValue("useTextures", False)
            self.shader_program.setUniformValue("alpha", 1.0)
            self.origin_axes.draw(radius)
        self.shader_program.release()

    @staticmethod
    def _set_blend(mode):
        """Put OpenGL into one of the PSX's four blend modes.

        The palette hands each texel an alpha of 0.5 when its STP bit is
        set and 1.0 when it is not, so the fixed-function blend does the
        rest: a texel the hardware would leave opaque comes through at
        full weight in every mode below."""
        GL.glBlendEquation(GL.GL_FUNC_ADD)
        if mode == ADD:                      # B + F
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE)
        elif mode == SUBTRACT:               # B - F
            GL.glBlendEquation(GL.GL_FUNC_REVERSE_SUBTRACT)
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE)
        elif mode == QUARTER:                # B + F/4
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE)
        else:                                # HALF: B/2 + F/2
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

    def _draw_pass(self, transparent, blend=None):
        bound = None
        alpha = None
        shifted = None
        for (group_index, clut, offset, count, is_transparent,
             face_blend) in self.draw_ranges:
            if is_transparent != transparent or group_index in self.hidden_groups:
                continue
            if blend is not None and face_blend != blend:
                continue
            want = (DIMMED_ALPHA if self.highlighted_group not in (None, group_index)
                    else 1.0)
            if want != alpha:
                self.shader_program.setUniformValue("alpha", want)
                alpha = want
            uv = self.uv_offsets.get(clut, (0.0, 0.0))
            if uv != shifted:
                self.shader_program.setUniformValue("uvOffset", QVector2D(*uv))
                shifted = uv
            tex_id = self.clut_map.get(clut)
            if tex_id and tex_id != bound:
                GL.glActiveTexture(GL.GL_TEXTURE1)
                GL.glBindTexture(GL.GL_TEXTURE_1D, tex_id)
                bound = tex_id
            GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT,
                              ctypes.c_void_p(offset))



class SMSTPanel(QWidget):
    """Part list beside the 3D view.

    Nothing in the file names a part, so the columns are what can be
    measured - how many polygons it has, how big it is, and where it
    sits in the blob for a hex editor. Ticking a row hides that part,
    selecting one fades the others down, and between them that is how a
    part gets identified as a head or a left hand."""

    def __init__(self, viewer: SMSTViewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self._filling = False

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Part", "Tris", "Quads", "Size", "Offset", "Extent"])
        self.table.setToolTip(
            "Double-click a Name to say what that part is.\n\n"
            "Kept against the file's own bytes rather than its id, so a "
            "model that appears in several areas is named once - see "
            "functions/placement.py. The Level Editor reads the same "
            "names.")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Double-click (or F2) to rename a part - NoEditTriggers here
        # left the tooltip's own instructions unreachable: nothing could
        # ever open the Name column's editor. Only that column is
        # actually editable (see populate_table), so this doesn't open
        # the Part column's checkbox cell up to being retyped too.
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._part_menu)
        self.viewer.selection_changed.connect(self._on_viewer_pick)
        # Set by MainWindow to stage a rebuilt blob as a file edit, the
        # same way any other whole-file replacement is staged. Left None
        # when nobody has said, and paste then says so rather than
        # pretending it wrote something.
        self.stage_edit = None
        # The disc's CD folder, set by MainWindow - the IMG and
        # IDX are needed to work out where a texture can go.
        self.cd_folder = None
        # Called when the migration dialog has written the IMG,
        # so MainWindow knows the export must carry it.
        self.img_written = None

        show_all = QPushButton("Show all", self)
        show_all.clicked.connect(self._show_all)
        isolate = QPushButton("Isolate selected", self)
        isolate.clicked.connect(self._isolate_selected)
        self.migrate = QPushButton("Textures...", self)
        self.migrate.setToolTip(
            "Where this model's textures live, and whether they are "
            "reachable from anywhere but the area they were packed "
            "for.\n\nA model whose art sits in one level's own IMG "
            "chunk draws wrong everywhere else; this copies what is "
            "missing into the resident chunk and rewrites the model's "
            "UVs to match.")
        self.migrate.clicked.connect(self._open_migrate)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(show_all)
        buttons.addWidget(isolate)
        buttons.addWidget(self.migrate)

        self.details = QLabel("Click a polygon in the view.", self)
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(66)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        # The same texture view the MDAT panel has - palette strip, the
        # page with this polygon's UVs ringed on it, and the two exports.
        self.texture = TexturePanel(viewer, stem=self._stem, parent=self)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.table, 1)
        left_layout.addLayout(buttons)
        left_layout.addWidget(self.details)
        left_layout.addWidget(self.texture)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 800])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def populate_table(self):
        self._filling = True
        groups = self.viewer.groups
        self.table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            part = QTableWidgetItem(f"{group.index}" + ("  (empty)" if group.empty else ""))
            # Checkable, not editable - a double-click here toggles
            # visibility (see _on_item_changed); it's the Name column
            # that opens a text editor now that the table allows one.
            part.setFlags((part.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                          & ~Qt.ItemFlag.ItemIsEditable)
            part.setCheckState(Qt.CheckState.Checked)
            part.setData(Qt.ItemDataRole.UserRole, group.index)
            self.table.setItem(row, PART_COLUMN, part)
            named = QTableWidgetItem(self._name_of(group.index))
            named.setFlags(named.flags() | Qt.ItemFlag.ItemIsEditable)
            named.setData(Qt.ItemDataRole.UserRole, group.index)
            self.table.setItem(row, NAME_COLUMN, named)
            self.table.setItem(row, 2, QTableWidgetItem(str(group.tris)))
            self.table.setItem(row, 3, QTableWidgetItem(str(group.quads)))
            self.table.setItem(row, 4, QTableWidgetItem(str(group.size)))
            self.table.setItem(row, 5, QTableWidgetItem(f"0x{group.offset:X}"))
            if group.bounds:
                x0, x1, y0, y1, z0, z1 = group.bounds
                extent = f"{x1 - x0} x {y1 - y0} x {z1 - z0}"
            else:
                extent = "-"
            self.table.setItem(row, 6, QTableWidgetItem(extent))
        self._filling = False
        self.table.clearSelection()
        self.viewer.set_hidden_groups(())
        self.viewer.set_highlighted_group(None)
        self._show_polygon(None)

    def _on_item_changed(self, item):
        if self._filling:
            return
        if item.column() == NAME_COLUMN:
            self._rename(item.data(Qt.ItemDataRole.UserRole), item.text())
            return
        if item.column() != PART_COLUMN:
            return
        self.viewer.set_group_hidden(item.data(Qt.ItemDataRole.UserRole),
                                     item.checkState() != Qt.CheckState.Checked)

    def _content(self):
        """The identity of the model on screen - a hash of its own bytes,
        which is what a name is filed under. Empty when nothing is
        loaded, or when it came from somewhere with no bytes to hash."""
        blob = getattr(self.viewer, "blob", None)
        return labels.content_key(blob) if blob else ""

    def _name_of(self, group):
        content = self._content()
        if not content:
            return ""
        return placement.model_name(placement.load_model_names(),
                                    content, group)

    def _rename(self, group, text):
        """Write one part's name out, or clear it."""
        content = self._content()
        if not content or group is None:
            return
        names = placement.load_model_names()
        key = placement.name_key(content, group)
        text = (text or "").strip()
        if text:
            names[key] = text
        else:
            names.pop(key, None)
        placement.save_model_names(names)

    def _on_selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.viewer.set_highlighted_group(None)
            return
        item = self.table.item(rows[0].row(), PART_COLUMN)
        index = item.data(Qt.ItemDataRole.UserRole)
        self.viewer.set_highlighted_group(index)
        if not self._filling:
            print(f"selected: {self._describe_group(index)}")

    def _part_label(self, index):
        """"part 3" or, once it's named, "part 3 (\"Left Leg\")" - the
        one phrase every print and menu below builds its text from, so
        a name shows up everywhere a part number used to stand alone."""
        name = self._name_of(index)
        return f'part {index} ("{name}")' if name else f"part {index}"

    def _describe_group(self, index):
        """One addressable line for a part: where it starts in the DAT,
        and what it holds."""
        model = self.viewer.model_data
        if not model:
            return f"SMST group {index}"
        group = model["groups"][index]
        return (f"SMST @ 0x{model['address']:X}  {self._part_label(index)} "
                f"@ 0x{model['address'] + group.offset:X} "
                f"(+0x{group.offset:X})  {group.tris} tris  {group.quads} "
                f"quads  size 0x{group.size:X}")

    # --- copy and paste a part ----------------------------------------

    def _part_menu(self, position):
        """Right-click a part: copy it, or paste one over it.

        The clipboard is module-level, so a part copied out of one model
        can be pasted into the next one opened - which is the whole
        point of it."""
        row = self.table.rowAt(position.y())
        if row < 0 or not self.viewer.blob:
            return
        index = self.table.item(row, PART_COLUMN).data(Qt.ItemDataRole.UserRole)
        label = self._part_label(index)
        menu = QMenu(self)
        copy = menu.addAction(f"Copy {label}")
        copy.triggered.connect(lambda: self._copy_part(index))
        clip = smst_edit.clipboard()
        if clip is None:
            paste = menu.addAction("Paste over this part")
            paste.setEnabled(False)
        else:
            clip_name = f'"{clip["name"]}", ' if clip.get("name") else ""
            paste = menu.addAction(
                f"Paste the copied part ({clip_name}{clip['tris']} tris, "
                f"{clip['quads']} quads) over {label}")
            paste.triggered.connect(lambda: self._paste_part(index))
        menu.addSeparator()
        clear = menu.addAction(f"Clear {label} (make it empty)")
        clear.setEnabled(not self.viewer.groups[index].empty
                         if index < len(self.viewer.groups) else False)
        clear.triggered.connect(lambda: self._clear_part(index))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _open_migrate(self):
        """Plan a texture migration for the whole model."""
        from gui.smst.migrate_dialog import MigrateDialog
        if not self.viewer.blob:
            return
        folder = self.cd_folder
        if not folder:
            QMessageBox.information(
                self, "No disc open",
                "The IMG and IDX have to be on hand to work out where a "
                "texture could go, so open a disc first.")
            return
        if not self.viewer.vram_raw_bytes:
            QMessageBox.information(
                self, "No VRAM",
                "This model has no VRAM loaded, so there is nothing to "
                "read its textures out of.")
            return
        dialog = MigrateDialog(self.viewer.blob, self.viewer.vram_raw_bytes,
                               folder, self.viewer.export_name or "model",
                               self)
        if dialog.exec() and dialog.new_blob is not None:
            self._apply_edit(
                dialog.new_blob,
                "the model now points at the migrated textures.",
                "Textures migrated", "migrated the model's textures")
            # The dialog wrote TOMBA2.IMG and TOMBA2.IDX, and an export
            # has to be told or it ships the original IMG beside the
            # rewritten IDX - see MainWindow.img_dirty.
            if self.img_written is not None:
                self.img_written()

    def _copy_part(self, index):
        try:
            clip = smst_edit.copy_group(self.viewer.blob, index)
        except Exception as e:
            QMessageBox.critical(self, "Copy failed", str(e))
            return
        model = self.viewer.model_data
        name = self._name_of(index)
        clip["name"] = name
        clip["from"] = f"SMST @ 0x{model['address']:X} {self._part_label(index)}"
        smst_edit.set_clipboard(clip)
        print(f"copied: {clip['from']}  {clip['tris']} tris  "
              f"{clip['quads']} quads  {len(clip['bytes'])} bytes")

    def _paste_part(self, index):
        clip = smst_edit.clipboard()
        if clip is None or not self.viewer.blob:
            return
        try:
            blob, note = smst_edit.paste_group(self.viewer.blob, index, clip)
        except Exception as e:
            QMessageBox.critical(self, "Paste failed", str(e))
            return
        label = self._part_label(index)
        self._apply_edit(blob, note, f"Pasted over {label}",
                         f"pasted a part over {label}",
                         extra=f"{clip['from']} -> {label}. ")

    def _clear_part(self, index):
        if not self.viewer.blob:
            return
        try:
            blob, note = smst_edit.clear_group(self.viewer.blob, index)
        except Exception as e:
            QMessageBox.critical(self, "Clear failed", str(e))
            return
        label = self._part_label(index)
        self._apply_edit(blob, note, f"Cleared {label}", f"cleared {label}")

    def _refresh_view(self, blob):
        """Show `blob` and bring the part list up to date with it.

        Shared by an edit made here and by refresh_if_showing() below,
        which is the same thing done from outside this tab - a swap, an
        imported replacement, anything else that can change the bytes
        of a model this tab happens to already have open. Either way,
        the parts that were hidden are put back afterwards: looking at
        one isolated part and then editing it should not undo that."""
        hidden = set(self.viewer.hidden_groups)
        if not self.viewer.show_blob(blob):
            return False
        self.populate_table()
        if hidden:
            self._set_checks(lambda row: self.table.item(row, PART_COLUMN).data(
                Qt.ItemDataRole.UserRole) not in hidden)
        return True

    def refresh_if_showing(self, address, blob):
        """Redraw this tab if it already has the model at `address`
        open and `blob` is not already what it is showing.

        For an edit that reaches this model from outside the SMST tab -
        MainWindow calls this after every staged file edit. A paste or
        a clear made from inside the tab already shows itself through
        _apply_edit(), and this stays a no-op for those: by the time
        stage_edit() runs, the view is already showing `blob`, so the
        blob check below skips the redundant rebuild."""
        model = self.viewer.model_data
        if not model or model.get("address") != address:
            return False
        if self.viewer.blob == blob:
            return False
        return self._refresh_view(blob)

    def _apply_edit(self, blob, note, heading, label, extra=""):
        """Show a rebuilt model, refresh the list, and stage the bytes.

        No dialog on the way in: the point of an edit here is to look at
        it, and nothing reaches the disc until the ISO or the files are
        saved, so a confirmation would buy nothing but a click."""
        if not self._refresh_view(blob):
            QMessageBox.critical(
                self, "Edit failed",
                "The rebuilt model wouldn't parse, so nothing was changed.")
            return
        if self.stage_edit is not None:
            self.stage_edit(blob, label)
            staged = "staged - save the ISO or the files to keep it"
        else:
            staged = ("NOT staged: this SMST wasn't opened from a disc row, "
                      "so it is on screen only")
        print(f"{label}: {extra}{note} ({staged})")
        self.details.setText(f"<b>{heading}</b><br>{note}<br><i>{staged}</i>")

    def _stem(self, polygon):
        """What an exported page or GIF is called: the part the polygon
        came out of, since that is how one is named here."""
        return (f"page{polygon['page']}_clut{polygon['clut']:06X}"
                f"_group{polygon['group']}_{polygon['kind']}{polygon['slot']}")

    def _on_viewer_pick(self, group, polygon_index):
        """Follow a pick in the 3D view: put the row for the part it
        landed in under the cursor, without printing it a second time -
        the viewer has already said what was picked."""
        self._show_polygon(polygon_index)
        if group is None:
            return
        self._filling = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, PART_COLUMN)
            if item.data(Qt.ItemDataRole.UserRole) == group:
                self.table.selectRow(row)
                break
        self._filling = False

    def _show_polygon(self, index):
        """Spell out what a picked polygon is, and show its texture."""
        polygons = self.viewer.polygons
        if index is None or index >= len(polygons):
            self.details.setText("Click a polygon in the view.")
            self.texture.show_polygon(None)
            return
        polygon = polygons[index]
        model = self.viewer.model_data
        x, y = psx_vram.clut_address_xy(polygon["clut"])
        self.details.setText(
            f"<b>{polygon['kind']} {polygon['slot']}</b> of "
            f"{self._part_label(polygon['group'])}<br>"
            f"packet at <b>0x{polygon['address']:X}</b>, draw type "
            f"{polygon['type']} "
            f"({'semi-transparent' if polygon['transparent'] else 'opaque'}, "
            f"{BLEND_NAMES.get(polygon['blend'], polygon['blend'])})<br>"
            f"texture page <b>{polygon['page']}</b>, CLUT "
            f"<b>0x{polygon['clut']:06X}</b> at ({x}, {y})<br>"
            f"UVs " + ", ".join(f"({u},{v})" for u, v in polygon["texels"]))
        self.texture.show_polygon(polygon)

    def _show_all(self):
        # Two things make a part invisible: its tick, and the highlight
        # that fades everything except the selected row. Clearing only
        # the ticks left a selected model still faded, which read as the
        # button doing nothing at all.
        self._set_checks(lambda _row: True)
        self.table.clearSelection()
        self.viewer.set_highlighted_group(None)

    def _isolate_selected(self):
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not rows:
            return
        self._set_checks(lambda row: row in rows)

    def _set_checks(self, keep):
        self._filling = True
        hidden = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, PART_COLUMN)
            visible = keep(row)
            item.setCheckState(Qt.CheckState.Checked if visible
                               else Qt.CheckState.Unchecked)
            if not visible:
                hidden.add(item.data(Qt.ItemDataRole.UserRole))
        self._filling = False
        self.viewer.set_hidden_groups(hidden)

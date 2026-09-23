"""What one polygon is drawn with: its palette, its page, its UVs.

Lifted out of formats/geometry/mdat_panel.py so the SMST view can have it too.
The two formats are the same packets reached different ways (see
formats/models/smst_parser.py's header) and gui/widgets/polygon_pick.py already picks
either of them the same way, so there was no reason the thing that shows
what got picked should exist only on one side.

It needs a viewer that carries VRAM and the animation tables - which
both the MDAT and SMST viewers do, through formats/animation/clut_animation.py - and a
polygon record from either parser. Nothing else about the format
reaches in here.
"""
from math import gcd

import numpy as np
from PIL import Image
from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout,
)

from psx import vram as psx_vram
from game import texture_window
from formats.animation import uv_anim
from formats.animation.clut_animation import TICK_HZ, UV_TICKS_PER_FRAME
from gui.widgets.pixel_canvas import PixelCanvas, fit_zoom

UV_OUTLINE = QColor(255, 235, 40)
UV_SHADOW = QColor(0, 0, 0, 160)

PAGE = psx_vram.UV_WRAP

# How often the preview looks at the viewer's tick to see whether the
# animation has moved on. Polled rather than driven, so the preview
# follows the Animate button instead of running a clock of its own.
PREVIEW_POLL_MS = 50

# A polygon's patch of texture is often only a few dozen texels across,
# so a GIF of it is blown up to be worth looking at.
GIF_SCALE = 4

# However long an animation's loop is, a GIF of it stops here.
MAX_GIF_FRAMES = 240
CELL = texture_window.CELL


class UVCanvas(PixelCanvas):
    """A texture page with one polygon's UVs drawn over it."""

    def __init__(self, parent=None):
        super().__init__(zoom=1, parent=parent)
        self.ring = ()

    def set_ring(self, ring):
        self.ring = tuple(ring)
        self.update()

    def paint_overlays(self, painter, area):
        if len(self.ring) < 2:
            return
        points = [(self.scaled(u) + self.scaled(1) // 2,
                   self.scaled(v) + self.scaled(1) // 2) for u, v in self.ring]
        for width, color in ((3, UV_SHADOW), (1, UV_OUTLINE)):
            painter.setPen(QPen(color, width))
            for i, (x, y) in enumerate(points):
                nx, ny = points[(i + 1) % len(points)]
                painter.drawLine(x, y, nx, ny)


def swatch(palette, height):
    """The 16 colours as one strip."""
    cell = 16
    pixmap = QPixmap(cell * 16, max(height, 8))
    pixmap.fill(QColor(0, 0, 0))
    painter = QPainter(pixmap)
    for i, color in enumerate(palette):
        painter.fillRect(QRect(i * cell, 0, cell, pixmap.height()),
                         QColor(color[0], color[1], color[2]))
    painter.end()
    return pixmap


class TexturePanel(QGroupBox):
    """The palette strip, the texture page with the UVs ringed on it, and
    the two exports.

    `stem` is asked for a filename for the polygon being shown; the
    formats name a polygon differently and that is the only thing about
    them this needs to know."""

    def __init__(self, viewer, stem=None, parent=None):
        super().__init__("Texture", parent)
        self.viewer = viewer
        self._stem_for = stem or (lambda p: f"page{p['page']}_clut{p['clut']:06X}")
        self._preview = None
        self._preview_frames = None
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._poll_animation)

        self.clut_strip = QLabel(self)
        self.clut_strip.setFixedHeight(18)

        self.uv_canvas = UVCanvas(self)
        self._page_scroll = QScrollArea(self)
        self._page_scroll.setWidgetResizable(False)
        self._page_scroll.setWidget(self.uv_canvas)
        self._page_scroll.setMinimumHeight(180)

        self.png_button = QPushButton("Save PNG...", self)
        self.png_button.setToolTip(
            "Write this polygon's texture page out as it is coloured here "
            "- 256x256, with its palette applied.")
        self.png_button.clicked.connect(self._save_png)
        self.gif_button = QPushButton("Save GIF...", self)
        self.gif_button.setToolTip(
            "Write the polygon's texture out as an animated GIF, one frame "
            "per step of whatever animates it - the palette being swapped, "
            "the UVs stepping across the page, or the whole 64x64 cell an "
            "area's cell drawer steps and scrolls it through.")
        self.gif_button.clicked.connect(self._save_gif)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.png_button)
        buttons.addWidget(self.gif_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.clut_strip)
        layout.addWidget(self._page_scroll)
        layout.addLayout(buttons)

    # --- what is being shown -------------------------------------------

    def show_polygon(self, polygon):
        """Show one polygon's texture, or nothing when given None."""
        self._preview = polygon
        self._preview_frames = None
        vram = getattr(self.viewer, "vram_raw_bytes", None)
        usable = (polygon is not None and vram
                  and len(vram) >= psx_vram.VRAM_SIZE)
        self.png_button.setEnabled(bool(usable))
        self.gif_button.setEnabled(bool(usable)
                                   and (any(self._animations(polygon))
                                        or self._window_rule(polygon) is not None))
        if not usable:
            self._preview_timer.stop()
            self.clut_strip.clear()
            self.uv_canvas.clear()
            self.uv_canvas.set_ring(())
            return
        self._draw_preview(fit=True)
        if self.gif_button.isEnabled():
            self._preview_timer.start(PREVIEW_POLL_MS)
        else:
            self._preview_timer.stop()

    def _animations(self, polygon):
        """(palette animation, UV animation) for a polygon, either None."""
        if polygon is None:
            return None, None
        clut = polygon["clut"]
        return (getattr(self.viewer, "clut_animations", {}).get(clut),
                getattr(self.viewer, "uv_animations", {}).get(clut))

    def _window_rule(self, polygon):
        """The texture-window rule (game/texture_window.py) the
        polygon's face flags put it under, or None."""
        if polygon is None:
            return None
        flags = ((getattr(self.viewer, "model_data", None) or {}).get("face_flags") or ())
        face = polygon.get("first_face")
        value = flags[face] if face is not None and face < len(flags) else 0
        for rule in getattr(self.viewer, "window_rules", ()) or ():
            if not value & rule.flag or (rule.skip and value & rule.skip == rule.skip):
                continue
            if value & texture_window.MODEL and not rule.models:
                continue
            return rule
        return None

    def _frame_state(self, polygon, tick):
        """(palette frame, UV frame) at `tick`, either None."""
        palette, uv = self._animations(polygon)
        return (palette.frame_at(tick) if palette else None,
                (tick // (uv.ticks or UV_TICKS_PER_FRAME)) % len(uv) if uv else None)

    def _palette_at(self, polygon, frame):
        """The 16 colours the polygon draws with on that frame."""
        animation, _uv = self._animations(polygon)
        if animation is not None and frame is not None:
            return psx_vram.read_palette(animation.frames[frame], 0, 16,
                                         transparent_zero=False)
        return psx_vram.read_palette(self.viewer.vram_raw_bytes,
                                     polygon["clut"], 16,
                                     transparent_zero=False)

    def _uv_offset(self, polygon, frame):
        _palette, uv = self._animations(polygon)
        return (uv.offset_at(frame)
                if uv is not None and frame is not None else (0, 0))

    def _page_rgb(self, polygon, palette_frame):
        texels = uv_anim.page_texels(self.viewer.vram_raw_bytes,
                                     polygon["page"])
        lut = np.array([c[:3] for c in self._palette_at(polygon, palette_frame)],
                       dtype=np.uint8)
        return lut[texels]

    def _poll_animation(self):
        self._draw_preview()

    def _draw_preview(self, fit=False):
        polygon = self._preview
        if polygon is None:
            return
        state = self._frame_state(polygon,
                                  getattr(self.viewer, "anim_tick", 0))
        if not fit and state == self._preview_frames:
            return
        self._preview_frames = state
        palette_frame, uv_frame = state

        self.clut_strip.setPixmap(
            swatch(self._palette_at(polygon, palette_frame),
                   self.clut_strip.height()))
        rgb = self._page_rgb(polygon, palette_frame)
        # QImage doesn't own the buffer it is handed, so copy it.
        self.uv_canvas.set_image(QImage(rgb.tobytes(), PAGE, PAGE, PAGE * 3,
                                        QImage.Format.Format_RGB888).copy())
        if fit:
            self.uv_canvas.set_zoom(
                fit_zoom((PAGE, PAGE), self._page_scroll.viewport().size()))
        du, dv = self._uv_offset(polygon, uv_frame)
        self.uv_canvas.set_ring([((u + du) % PAGE, (v + dv) % PAGE)
                                 for u, v in polygon["texels"]])

    # --- export ---------------------------------------------------------

    def _save_png(self):
        polygon = self._preview
        if polygon is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save texture page", self._stem_for(polygon) + ".png",
            "PNG image (*.png)")
        if not path:
            return
        rgb = self._page_rgb(polygon, self._frame_state(
            polygon, getattr(self.viewer, "anim_tick", 0))[0])
        Image.fromarray(rgb, "RGB").save(path)
        print(f"wrote {path}")

    def _save_gif(self):
        polygon = self._preview
        if polygon is None:
            return
        frames = self._gif_frames(polygon)
        if not frames:
            QMessageBox.information(self, "Nothing to animate",
                                    "This polygon's texture doesn't animate.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save animated texture", self._stem_for(polygon) + ".gif",
            "GIF image (*.gif)")
        if not path:
            return
        images = [image for image, _ms in frames]
        try:
            images[0].save(path, save_all=True, append_images=images[1:],
                           duration=[ms for _image, ms in frames], loop=0)
        except Exception as e:
            QMessageBox.critical(self, "Export failed",
                                 f"Couldn't write it:\n\n{e}")
            return
        QMessageBox.information(
            self, "Exported",
            f"Wrote {len(images)} frame(s) of {images[0].width // GIF_SCALE}"
            f"x{images[0].height // GIF_SCALE} texels.")

    def _gif_frames(self, polygon):
        """[(image, milliseconds), ...] over one loop of whatever animates
        this polygon - the palette, the UVs, or both.

        The crop is the polygon's own UV box rather than the whole page:
        what animates is the patch this face takes, and a page around it
        that never changes is just margin."""
        palette_animation, uv_animation = self._animations(polygon)
        rule = self._window_rule(polygon)
        if rule is not None:
            return self._cell_frames(polygon, rule, palette_animation)
        if palette_animation is None and uv_animation is None:
            return []
        palette_period = (palette_animation.loop_ticks
                          if palette_animation else 1)
        uv_period = ((len(uv_animation) * UV_TICKS_PER_FRAME)
                     if uv_animation else 1)
        period = palette_period * uv_period // gcd(palette_period, uv_period)

        us = [u for u, _v in polygon["texels"]]
        vs = [v for _u, v in polygon["texels"]]
        u0, u1 = min(us), max(us) + 1
        v0, v1 = min(vs), max(vs) + 1

        runs = []
        for tick in range(period):
            state = self._frame_state(polygon, tick)
            if not runs or runs[-1][0] != state:
                if len(runs) >= MAX_GIF_FRAMES:
                    break
                runs.append([state, 0])
            runs[-1][1] += 1

        # A run can come out looking exactly like the one before it - the
        # lava's sheet holds each of its frames twice - and PIL drops a
        # repeated frame while keeping its neighbour's duration, which
        # plays those parts of the loop at double speed. Merging them
        # here keeps the timing right and leaves PIL nothing to drop.
        patches = []
        for (palette_frame, uv_frame), ticks in runs:
            du, dv = self._uv_offset(polygon, uv_frame)
            rgb = self._page_rgb(polygon, palette_frame)
            patch = np.take(
                np.take(rgb, np.arange(v0 + dv, v1 + dv) % PAGE, axis=0),
                np.arange(u0 + du, u1 + du) % PAGE, axis=1)
            if patches and np.array_equal(patches[-1][0], patch):
                patches[-1][1] += ticks
            else:
                patches.append([patch, ticks])
        if len(patches) > 1 and np.array_equal(patches[0][0], patches[-1][0]):
            patches[0][1] += patches.pop()[1]

        return self._to_frames(patches)

    def _cell_frames(self, polygon, rule, palette_animation):
        """[(image, milliseconds), ...] of the whole 64x64 cell a cell
        drawer shows the polygon through, one game frame a tick: an E2
        window samples cell + ((uv + scroll) & 63); an added cell moves the
        face's own cell across the page."""
        cells = (len(rule.cells) if rule.cells else rule.columns * rule.rows) * rule.step
        scrolls = [CELL // gcd(CELL, abs(s)) * rule.scroll_step
                   for s in (rule.scroll_u, rule.scroll_v) if s]
        period = cells
        for other in scrolls + ([palette_animation.loop_ticks] if palette_animation else []):
            period = period * other // gcd(period, other)
        period = min(period, MAX_GIF_FRAMES)
        us = [u for u, _v in polygon["texels"]]
        vs = [v for _u, v in polygon["texels"]]
        home_u, home_v = min(us) // CELL * CELL, min(vs) // CELL * CELL
        patches = []
        for tick in range(period):
            cell_u, cell_v, scroll_u, scroll_v = texture_window.window_at(rule, tick)
            palette_frame = palette_animation.frame_at(tick) if palette_animation else None
            rgb = self._page_rgb(polygon, palette_frame)
            if rule.add:
                u0, v0 = home_u + int(cell_u), home_v + int(cell_v)
                rows, cols = np.arange(v0, v0 + CELL), np.arange(u0, u0 + CELL)
            else:
                rows = int(cell_v) + (np.arange(CELL) + int(scroll_v)) % CELL
                cols = int(cell_u) + (np.arange(CELL) + int(scroll_u)) % CELL
            patch = np.take(np.take(rgb, rows % PAGE, axis=0), cols % PAGE, axis=1)
            if patches and np.array_equal(patches[-1][0], patch):
                patches[-1][1] += 1
            else:
                patches.append([patch, 1])
        if len(patches) > 1 and np.array_equal(patches[0][0], patches[-1][0]):
            patches[0][1] += patches.pop()[1]
        return self._to_frames(patches)

    @staticmethod
    def _to_frames(patches):
        frames = []
        for patch, ticks in patches:
            image = Image.fromarray(patch, "RGB")
            frames.append((image.resize((image.width * GIF_SCALE,
                                         image.height * GIF_SCALE),
                                        Image.NEAREST),
                           max(20, round(ticks * 1000 / TICK_HZ))))
        return frames

"""The cursed Kujara Ranch's falling snow, as the game makes it (US retail A04).

Area effect 0x500 (spawned by the ranch's scene controller 0x801169E8) has two routines:

  update 0x8013AB0C - the wind. Every tick the effect's own position drifts by (-8, +8, w): the flakes
      blow along X and fall. Each 150 / 300 / 450 ticks (random) it rolls: three times in four a gust
      (sound 0x81), else calm. In a gust the flake count climbs 3 a tick to 1024 and w leans to -40 one
      step every 4 ticks; when calm the count sinks to 341 and w eases back to -8.
  draw 0x8013B118 -> FUN_A04__8013AD90 - the flakes. One fixed pseudo-random sequence (LCG seed
      0x8A7210CA, multiplier 0x7D2B89DD, +1) places count+1 points in a 2048-unit cube that sits ahead of
      the camera (its corner at camera - 1024 + forward * 2048); each point is that random offset plus
      the drift, wrapped to the cube, so the snow is anchored in the world and repeats every 2048 units.
      Points at world depth (z) 10001 and on are skipped, except below height -8999 up to z 11500.
      Each is a white TILE (0x60FFFFFF): 2 x 2 pixels when nearer than 1536, else 1 x 1, on a 320-wide
      screen, never blended.

Nothing here is invented: the numbers are the routines' own. The editor's Animate switch runs it.
"""
import random

M = 0x7D2B89DD
SEED = -0x758DEF36
CUBE = 0x7FF
FIRST = (0x12, 0x18BE, 0xB5AF)          # the vector the loop's pipelined first projection uses


def _s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class KujaraSnow:
    """The effect's state (actor +0x2C/+0x2E/+0x30 drift, +0x50 timer, +0x52 count, +0x54 wind, +0x05 phase)."""

    def __init__(self, seed=0):
        self.rand = random.Random(seed)
        self.x = self.y = self.z = 0
        self.timer, self.count, self.wind, self.phase = 0, 0x155, 0, 0

    def _rand(self):
        return self.rand.randrange(0x8000)          # the PSX rand(): 0..0x7FFF

    def tick(self):
        """One game tick of update 0x8013AB0C (the cursed, near-the-ranch branch)."""
        if self.phase == 0:
            if self.count > 0x155:
                self.count -= 3
            if self.wind < -8 and (self.timer & 3) == 0:
                self.wind += 1
        else:
            if self.count < 0x400:
                self.count += 3
            if self.wind > -0x28 and (self.timer & 3) == 0:
                self.wind -= 1
        self.x = _s16(self.x - 8)
        self.y = _s16(self.y + 8)
        self.z = _s16(self.z + self.wind)
        self.timer = _s16(self.timer - 1)
        if self.timer < 0:
            self.phase = 1 if self._rand() < 0x6001 else 0      # the gust plays sound 0x81 here
            roll = (self._rand() >> 10) & 0xF
            self.timer = 0x96 if roll <= 3 else 300 if roll <= 9 else 0x1C2

    def flakes(self, camera, forward):
        """[(world x, height, depth)] of the points drawn this tick, for a camera at `camera` (game units)
        looking along `forward` (a unit vector, game axes). Mirrors FUN_A04__8013AD90 step for step,
        including its one-behind projection (each pass projects the point the pass before kept)."""
        origin = [int(camera[k]) - 0x400 + int(forward[k] * 0x800) for k in range(3)]
        origin = [_s16(v) for v in origin]
        dx, dy, dz = (_s16(self.x - origin[0]), _s16(self.y - origin[1]), _s16(self.z - origin[2]))
        held = ((dx + FIRST[0]) & CUBE, (dy + FIRST[1]) & CUBE, (dz + FIRST[2]) & CUBE)
        out = []
        state = SEED
        for _ in range(max(self.count, -1) + 1):
            first = _s32(state * M + 1)
            state = _s32(first * M + 1)
            ly = ((state >> 16) + dy) & CUBE
            state = _s32(state * M + 1)
            lz = ((state >> 16) + dz) & CUBE
            depth = origin[2] + lz
            if depth < 0x2711 or (origin[1] + ly < -8999 and depth < 0x2CED):
                out.append((origin[0] + held[0], origin[1] + held[1], origin[2] + held[2]))
                held = (((first >> 16) + dx) & CUBE, ly, lz)
        return out


from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget


class SnowOverlay(QWidget):
    """Paints KujaraSnow's flakes over a LevelViewer, projected with its own camera, ticking at 30 Hz."""

    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.snow = KujaraSnow()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.hide()

    def run(self, on):
        if on:
            self.setGeometry(0, 0, self.viewer.width(), self.viewer.height())
            self.show(); self.raise_(); self.timer.start(33)
        else:
            self.timer.stop(); self.hide()

    def _tick(self):
        self.snow.tick()
        self.setGeometry(0, 0, self.viewer.width(), self.viewer.height())
        self.update()

    def paintEvent(self, _event):
        import numpy as np
        from gui.level.level_viewer import UNIT_SCALE
        cam = self.viewer.camera_controls
        e, f = cam._eye(), cam._forward()
        camera = (e[2] * UNIT_SCALE, -e[1] * UNIT_SCALE, e[0] * UNIT_SCALE)      # GL -> game axes
        forward = (f[2], -f[1], f[0])
        points = self.snow.flakes(camera, forward)
        if not points:
            return
        world = np.array([(p[2], -p[1], p[0]) for p in points], dtype=np.float64) / UNIT_SCALE
        mvp = np.array(self.viewer._model_view_projection().data(), dtype=np.float64).reshape(4, 4).T
        clip = np.c_[world, np.ones(len(world))] @ mvp.T
        w, h = self.width(), self.height()
        scale = max(1, round(w / 320))                   # the game's pixels on a 320-wide screen
        eye = np.array(e)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        for (x, y, z, cw), point in zip(clip, world):
            if cw <= 1e-6:
                continue
            sx, sy = (x / cw + 1) * 0.5 * w, (1 - y / cw) * 0.5 * h
            if not (0 <= sx < w and 0 <= sy < h):
                continue
            near = np.linalg.norm(point - eye) * UNIT_SCALE < 0x600
            size = (2 if near else 1) * scale
            painter.drawRect(int(sx), int(sy), size, size)
        painter.end()

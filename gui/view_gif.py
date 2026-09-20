"""An animated GIF of what a 3D viewer draws, one game tick a frame.

The viewers animate on game ticks (gui/clut_animation.py's anim_tick for
palettes, UV strips and texture-window cells; the level viewer's sprite tick
for sprites and recorded effects). Setting those clocks by hand and grabbing
the framebuffer each tick records exactly what the view shows - whatever
moves it, whole cells and all - without a second renderer to keep in step.
"""
from math import gcd

from PIL import Image
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from functions import texture_window
from gui.clut_animation import TICK_HZ, UV_TICKS_PER_FRAME

# However long the loop, a GIF stops here - eight seconds of game time.
MAX_FRAMES = 240


def lcm(values):
    out = 1
    for value in values:
        value = max(int(value), 1)
        out = out * value // gcd(out, value)
    return out


def rule_ticks(rule):
    """Game frames one texture-window rule takes to come round."""
    cells = (len(rule.cells) if rule.cells else rule.columns * rule.rows) * rule.step
    scrolls = [texture_window.CELL // gcd(texture_window.CELL, abs(s)) * rule.scroll_step
               for s in (rule.scroll_u, rule.scroll_v) if s]
    return lcm([cells, *scrolls])


def animation_ticks(viewer, cluts=None, windows=True):
    """[loop length in ticks] of every palette, UV strip and texture-window
    animation the viewer runs - only those on `cluts` if given."""
    out = []
    for address, animation in getattr(viewer, "clut_animations", {}).items():
        if cluts is None or address in cluts:
            out.append(animation.loop_ticks)
    for address, animation in getattr(viewer, "uv_animations", {}).items():
        if cluts is None or address in cluts:
            out.append(len(animation) * (animation.ticks or UV_TICKS_PER_FRAME))
    if windows:
        out.extend(rule_ticks(rule) for rule in getattr(viewer, "window_rules", ()) or ())
    return out


def _pil(image, alpha=False):
    image = image.convertToFormat(
        QImage.Format.Format_RGBA8888 if alpha else QImage.Format.Format_RGB888)
    width, height = image.width(), image.height()
    raw = bytes(image.constBits().asstring(image.sizeInBytes()))
    mode = "RGBA" if alpha else "RGB"
    return Image.frombytes(mode, (width, height), raw, "raw", mode,
                           image.bytesPerLine())


def record(viewer, ticks, set_tick, *, rate=TICK_HZ, limit=MAX_FRAMES,
           transparent=False):
    """[(PIL image, milliseconds), ...] over `ticks` game ticks: `set_tick(t)`
    puts every clock at t, then the view is grabbed. Frames that come out
    the same are held rather than repeated."""
    timer = getattr(viewer, "anim_timer", None)
    running = timer is not None and timer.isActive()
    if running:
        timer.stop()
    frames = []
    try:
        for tick in range(min(max(ticks, 1), max(limit, 1))):
            set_tick(tick)
            image = _pil(viewer.grabFramebuffer(), alpha=transparent)
            if frames and frames[-1][0].tobytes() == image.tobytes():
                frames[-1][1] += 1
            else:
                frames.append([image, 1])
    finally:
        if running:
            timer.start()
    if len(frames) > 1 and frames[0][0].tobytes() == frames[-1][0].tobytes():
        frames[0][1] += frames.pop()[1]
    return [(image, max(20, round(n * 1000 / max(rate, 1))))
            for image, n in frames]


def _transparent_gif(image):
    """GIF has one transparent palette entry, not real alpha.

    Keep 255 colours for the model and reserve palette index 255 for the
    alpha-cleared ANMP backdrop.  Semi-transparent edge pixels remain model
    pixels; only genuinely clear pixels are keyed out.
    """
    rgba = image.convert("RGBA")
    indexed = rgba.convert("RGB").quantize(colors=255)
    pixels = bytearray(indexed.tobytes())
    alpha = rgba.getchannel("A").tobytes()
    for offset, value in enumerate(alpha):
        if value == 0:
            pixels[offset] = 255
    indexed.frombytes(bytes(pixels))
    palette = indexed.getpalette()
    palette[255 * 3:255 * 3 + 3] = [0, 0, 0]
    indexed.putpalette(palette)
    return indexed


def save(parent, frames, name, *, transparent=False):
    """Ask where, and write `frames` there. True if written."""
    if not frames:
        QMessageBox.information(parent, "Nothing to record",
                                "Nothing here comes out any different from one "
                                "game frame to the next.")
        return False
    path, _ = QFileDialog.getSaveFileName(parent, "Save animated GIF", name + ".gif",
                                          "GIF image (*.gif)")
    if not path:
        return False
    images = [(_transparent_gif(image) if transparent else image)
              for image, _ms in frames]
    try:
        options = {"save_all": True, "append_images": images[1:],
                   "duration": [ms for _image, ms in frames], "loop": 0,
                   "disposal": 2}
        if transparent:
            options["transparency"] = 255
        images[0].save(path, **options)
    except Exception as e:
        QMessageBox.critical(parent, "Export failed", f"Couldn't write it:\n\n{e}")
        return False
    print(f"wrote {path}: {len(images)} frame(s)")
    return True


def set_anim_tick(viewer, tick):
    """The palette/UV/cell clock at `tick`."""
    viewer.anim_tick = tick
    viewer._apply_animation(force=True)


def save_view(viewer, name):
    """The whole view over one loop of everything it animates."""
    ticks = lcm(animation_ticks(viewer))
    if ticks <= 1:
        return save(viewer, [], name)
    tick = viewer.anim_tick
    try:
        frames = record(viewer, ticks, lambda t: set_anim_tick(viewer, t))
    finally:
        set_anim_tick(viewer, tick)
    return save(viewer, frames, name)

"""Will a replaced Town fit the frame's primitive buffer? (US retail)

WHAT FREEZES

Every packet of a frame goes into one 81,920-byte half of the buffer at
0x800BFE68 and Tomba's own state follows the second half (see
game/primitive_buffer.py). A frame that runs over while it is in that
half writes GPU packets across him and the game stops. It is not a
question of how many polygons the level has: what counts is what one
camera catches of them, on top of everything else in the frame - and
the stock opening is already at 75,504 of the 81,920, with only some
10,000 of that scenery.

HOW IT IS FORECAST

Replacing the MDAT leaves collision, actors, scripts and camera Town's,
so every frame's camera is the stock game's. town_views.json has them,
recorded from the US disc through the opening and a walk each way along
the street: the camera, what the whole frame wrote, and how much of that
was Town's own scenery by the game's rule (formats/drawmaps/visibility.py).
A new MDAT's frame is then

    everything else, as recorded   +   the new scenery under that camera

which is measured, not a density guess. Where it is blind: views that
were not recorded - other parts of the level, other events.

    python -m game.frame_budget MDAT.bin DRWB.bin
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys

import numpy as np

from formats.drawmaps import visibility
from game import primitive_buffer

LIMIT = primitive_buffer.STOCK_BYTES            # one half: a stock frame's all
PATCHED = primitive_buffer.PATCHED_BYTES        # what the patch gives a heavy frame
THRESHOLD = primitive_buffer.THRESHOLD          # the frame size that switches it on
REACH, HALF_ANGLE = 0x3700, 0x1C7               # DAT_800a3f90, and A00's immediate
VIEWS = Path(__file__).with_name("town_views.json")
WIDE = (3, 4)                                   # DuckStation's 16:9 hack on screen X


@dataclass
class View:
    """One recorded frame. `other` is what it wrote that was not scenery."""
    frame: int
    scene: str
    eye: tuple
    yaw: int
    pitch: int
    rotation: tuple
    translation: tuple
    region: int
    total: int
    other: int


@dataclass
class Forecast:
    peak: int = 0                   # the heaviest frame, bytes
    at: View = None
    stock: int = 0                  # what the stock game wrote in that frame
    over: int = 0                   # frames past LIMIT
    frames: int = 0
    cuts: list = field(default_factory=list)    # frames the patch does not catch
    scenes: dict = field(default_factory=dict)  # scene -> (peak, frames over)
    heavy: int = 0                  # frames past THRESHOLD: the patch waits for the GPU there
    scenery: int = 0                # the new scenery's share of the heaviest frame
    objects: list = field(default_factory=list)     # [(Blender object, bytes)] in that frame
    wide: "Forecast" = None         # the same with an emulator's 16:9 hack, if asked for

    @property
    def fits(self):
        return self.peak <= LIMIT

    @property
    def safe(self):
        """Fits, widescreen hack or not. The hack's figure is a floor
        (the stock opening: 79,752 forecast, 80,432 measured), so it gets
        a margin."""
        return self.fits and (self.wide is None or self.wide.peak <= LIMIT - 1024)

    def text(self):
        if not self.frames:
            return ""
        hack = self.wide
        if self.fits:
            text = (f"Frame buffer: fits. The heaviest of {self.frames:,} recorded frames needs "
                    f"{self.peak:,} of the game's {LIMIT:,} bytes (the stock level: {self.stock:,} there).")
            if hack is not None and not hack.fits:
                text += (f" NOT with an emulator's 16:9 widescreen hack, which puts more on screen: {hack.over:,} "
                         f"frames need up to {hack.peak:,}, and the game freezes there unless it is patched.")
                if hack.objects:
                    text += (" Costliest objects in the heaviest of those: "
                             + ", ".join(f"{name or '(unnamed)'} {size:,}" for name, size in hack.objects) + ".")
            return text
        text = self._heavy_text()
        if hack is not None:
            text += f" With an emulator's 16:9 widescreen hack: {hack.over:,} frames, up to {hack.peak:,}."
        return text

    def _heavy_text(self):
        worst = ", ".join(f"{name} up to {peak:,}" for name, (peak, over) in self.scenes.items() if over)
        text = (f"Frame buffer: TOO HEAVY. {self.over:,} of {self.frames:,} recorded frames need more than the "
                f"game's {LIMIT:,} bytes ({worst}). In the heaviest, {self.scenery:,} bytes are this scenery "
                f"and {self.peak - self.scenery:,} everything else the game draws; the stock level's whole "
                f"frame there is {self.stock:,}. Unpatched, the game freezes.")
        if self.objects:
            text += (" Costliest objects in that frame: "
                     + ", ".join(f"{name or '(unnamed)'} {size:,}" for name, size in self.objects) + ".")
        if self.peak > PATCHED:
            return text + f" Even the frame-buffer patch's {PATCHED:,} is not enough."
        if self.cuts:
            text += (f" The frame-buffer patch covers frames that get heavy gradually; {len(self.cuts)} "
                     "of these come straight after a light frame (a scene cut), which it cannot see coming.")
        return text


_record = {}


def _recording():
    if not _record:
        data = json.loads(VIEWS.read_text())
        _record.update(stock=data.get("stock"), views=[
            View(v[0], data["scenes"][v[1]], tuple(v[2:4]), v[4], v[5],
                 tuple(tuple(v[6 + 3 * r:9 + 3 * r]) for r in range(3)), tuple(v[15:18]),
                 v[18], v[19], v[20]) for v in data["views"]])
    return _record


def views():
    return _recording()["views"]


def scenery_bytes(scenery, view, drwb, wide=False):
    """What `scenery` writes in a frame seen from `view`."""
    corners = visibility.triangle(view.eye[0], view.eye[1], view.yaw, view.pitch, REACH, HALF_ANGLE)
    pointers, _held = scenery.listed(corners, drwb, view.region)
    if not pointers:
        return 0
    return scenery.emitted_bytes(pointers, view.rotation, view.translation,
                                 x_scale=WIDE if wide else (1, 1))


def costliest(scenery, mdat, drwb, view, objects, wide=False, count=6):
    """[(Blender object, bytes)] of what `view` draws, largest first.
    `objects` is ImportResult.objects."""
    from formats.drawmaps.drwa_parser import parse_drwa
    corners = visibility.triangle(view.eye[0], view.eye[1], view.yaw, view.pitch, REACH, HALF_ANGLE)
    pointers, _held = scenery.listed(corners, drwb, view.region)
    if not pointers:
        return []
    sizes = scenery.drawn(pointers, view.rotation, view.translation, x_scale=WIDE if wide else (1, 1))
    names = [name for g in parse_drwa(mdat).groups for name in objects.get(g.cell, ("",) * (g.tris + g.quads))]
    if len(names) != len(sizes):
        return []
    totals = {}
    for name, size in zip(names, sizes):
        if size:
            totals[name] = totals.get(name, 0) + int(size)
    return sorted(totals.items(), key=lambda item: -item[1])[:count]


def forecast(mdat, drwb, wide=False, recorded=None, objects=None):
    """Forecast for an MDAT and the DRWB it will be drawn with.

    `wide` is the emulator widescreen hack: more of the scenery is on
    screen. What else such a frame gains is not recorded, so that figure
    is a floor. `objects` (ImportResult.objects) names what is in the
    heaviest frame."""
    scenery = visibility.Scenery(mdat)
    out = Forecast()
    previous, seen = None, {}
    for view in recorded if recorded is not None else views():
        camera = (view.eye, view.yaw, view.pitch, view.rotation, view.translation, view.region)
        if camera not in seen:          # a held camera is thousands of frames
            seen[camera] = scenery_bytes(scenery, view, drwb, wide)
        need = view.other + seen[camera]
        out.frames += 1
        peak, over = out.scenes.get(view.scene, (0, 0))
        out.scenes[view.scene] = (max(peak, need), over + (need > LIMIT))
        out.heavy += need > THRESHOLD
        if need > LIMIT:
            out.over += 1
            # the patch decides by the frame before; consecutive records are consecutive frames
            if previous is not None and previous[0] == view.frame - 1 and previous[1] <= THRESHOLD:
                out.cuts.append(view)
        if need > out.peak:
            out.peak, out.at, out.stock, out.scenery = need, view, view.total, seen[camera]
        previous = (view.frame, need)
    if objects and out.at is not None and not out.fits:
        out.objects = costliest(scenery, mdat, drwb, out.at, objects, wide)
    return out


def town(dat_path, idx_path, area=4):
    """(MDAT, DRWB) of Town in a DAT/IDX pair."""
    from formats.archive import format_detect, repacker
    chunk = repacker.parse_idx(idx_path)[area]
    pointers = chunk["sdat_pointers"]
    found = {}
    with open(dat_path, "rb") as dat:
        for slot, (_id, offset) in enumerate(pointers):
            end = pointers[slot + 1][1] if slot + 1 < len(pointers) else chunk["dat_end"] - chunk["dat_start"]
            dat.seek(chunk["dat_start"] + offset)
            blob = dat.read(end - offset)
            match = format_detect.best(blob)
            if match and match.kind in ("MDAT", "DRWB"):
                found.setdefault(match.kind, blob)
    return found["MDAT"], found["DRWB"]


def forecast_both(mdat, drwb, objects=None):
    """The forecast as a console draws it, with the 16:9 hack's as `.wide`."""
    out = forecast(mdat, drwb, objects=objects)
    out.wide = forecast(mdat, drwb, wide=True, objects=objects)
    return out


def forecast_disc(dat_path, idx_path):
    """Forecast for the Town in a DAT/IDX pair. The stock one is known."""
    import hashlib
    mdat, drwb = town(dat_path, idx_path)
    if hashlib.sha1(mdat + drwb).hexdigest() == _recording()["stock"]:
        recorded = views()
        return Forecast(max(v.total for v in recorded), None, 0, 0, len(recorded))
    return forecast_both(mdat, drwb)


def _main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    mdat, drwb = (Path(a).read_bytes() for a in argv[1:])
    found = forecast_both(mdat, drwb)
    print(found.text())
    print(f"With the frame-buffer patch, {found.heavy:,} of {found.frames:,} frames wait for the GPU "
          f"({found.wide.heavy:,} with the 16:9 hack).")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

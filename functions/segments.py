"""Where one animation stops and the next begins.

An ANMP holds every move a character has, end to end, with nothing
separating them. Tomba's TANP is 1152 frames covering idle, run, jump
and the rest, and the file says nothing about which is which:

  - the pointer table is 1152 entries of offset-plus-tag, and that is
    the whole header. No count, no loop marker, no terminator.
  - the offsets are strictly monotonic, with no duplicate and no gap -
    1145 of 1146 frames sit flush against the next, so there is not
    even alignment padding to read a seam from.
  - the tag, which carries the limb count, changes four times in 1152
    frames. Three of those do fall on real boundaries, but three out of
    twenty-eight is not a segmentation.

So the boundaries were never in the file. They belong to whatever code
drives the animation, and that table has not been found - the only
frame-index-shaped array in MAIN.EXE is a ramp stepping by 6 and 7,
which is arithmetic, not segments.

That leaves reading them off the animation, which is what this module
stores the results of. Boundaries found by eye are the good ones; see
guess() for what can be had automatically and where it falls down.

Segments are keyed by the animation's content hash, the same identity
functions/labels.py gives a DAT entry, so an animation packed into
several areas is segmented once.
"""
import json
import os
import sys

FOLDER = "segments"


def folder():
    """Where segments are written - beside the program, not inside it,
    since a frozen build unpacks somewhere temporary that is wiped."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, FOLDER)


def bundled_folder():
    """Segments shipped inside a frozen build, read only."""
    inside = getattr(sys, "_MEIPASS", None)
    return os.path.join(inside, FOLDER) if inside else None


def path_for(disc):
    return os.path.join(folder(), f"{disc}.json")


def load(disc):
    """{content key: [{name, start, end}, ...]}, empty when none."""
    if not disc:
        return {}
    tries = [path_for(disc)]
    inside = bundled_folder()
    if inside:
        tries.append(os.path.join(inside, f"{disc}.json"))
    for path in tries:
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            continue
        out = {}
        for key, spans in stored.items():
            if key == "disc" or not isinstance(spans, list):
                continue
            out[key] = [s for s in spans if _sane(s)]
        return out
    return {}


def save(disc, segments):
    """Write them back, dropping any animation left with none."""
    if not disc:
        return None
    os.makedirs(folder(), exist_ok=True)
    out = {"disc": disc}
    for key, spans in sorted(segments.items()):
        kept = sorted((s for s in spans if _sane(s)),
                      key=lambda s: (s["start"], s["end"]))
        if kept:
            out[key] = kept
    path = path_for(disc)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def _sane(span):
    try:
        return (isinstance(span, dict)
                and int(span["start"]) >= 0
                and int(span["end"]) >= int(span["start"]))
    except (KeyError, TypeError, ValueError):
        return False


def parse(text, frames=None):
    """Ranges typed as "1-14, 15-19, 20-32" into [{name, start, end}].

    One-based and inclusive on the way in, because that is how the
    frame list is numbered on screen; stored zero-based. A bare number
    is a segment of one frame. Anything unreadable is skipped rather
    than refused, so one typo does not lose the rest of a long list."""
    out = []
    for piece in text.replace("\n", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        name = ""
        if "=" in piece:                       # "run = 20-32"
            name, piece = piece.split("=", 1)
            name, piece = name.strip(), piece.strip()
        try:
            if "-" in piece:
                first, last = piece.split("-", 1)
                start, end = int(first) - 1, int(last) - 1
            else:
                start = end = int(piece) - 1
        except ValueError:
            continue
        if start < 0 or end < start:
            continue
        if frames is not None:
            if start >= frames:
                continue
            end = min(end, frames - 1)
        out.append({"name": name, "start": start, "end": end})
    return out


def as_text(spans):
    """The inverse of parse, for showing what is stored."""
    out = []
    for span in sorted(spans, key=lambda s: s["start"]):
        first, last = span["start"] + 1, span["end"] + 1
        where = f"{first}" if first == last else f"{first}-{last}"
        out.append(f"{span['name']} = {where}" if span.get("name") else where)
    return ", ".join(out)


def guess(frames, sensitivity=2.0):
    """Candidate boundaries, as zero-based first-frame indices.

    A finding aid, not an answer. Measured against 28 boundaries marked
    by eye in Tomba's TANP, the best this manages is 18 of them with 6
    false positives, and it fails in a way worth knowing about: it finds
    a boundary by the pose jumping, so a run of short moves that flow
    into one another is invisible to it. Four consecutive 6-frame
    segments there were missed entirely, and so were three consecutive
    8-frame ones.

    Both signals earn their place. The limbs turning is the strong one;
    the root moving catches boundaries the limbs alone miss, at a
    quarter of the weight because it is noisier. Peaks are taken rather
    than every crossing, because a transition takes a few frames to
    play out and would otherwise be counted several times over."""
    import numpy as np

    from gui.anmp.anmp_parser import _shortest_step

    if len(frames) < 4:
        return []

    def angles(frame):
        out = []
        for limb in frame.limbs:
            out.extend(limb)
        return out

    turn = []
    for a, b in zip(frames, frames[1:]):
        first, second = angles(a), angles(b)
        count = min(len(first), len(second))
        turn.append(0.0 if not count else
                    sum(abs(_shortest_step(first[i], second[i]))
                        for i in range(count)) / count)
    turn = np.array(turn)
    moves = np.array([f.translation() for f in frames])
    walk = np.linalg.norm(np.diff(moves, axis=0), axis=1)

    score = (turn / max(float(np.median(turn)), 1e-6)
             + 0.25 * walk / max(float(np.median(walk)), 1e-6))
    cut = float(np.median(score)) * sensitivity

    found = []
    for i in range(len(score)):
        if score[i] <= cut:
            continue
        window = score[max(0, i - 2):i + 3]
        if score[i] == window.max() and (not found or i - found[-1] >= 2):
            found.append(i)
    return [i + 1 for i in found if i + 1 < len(frames)]


def between(boundaries, frames):
    """Boundaries turned into whole segments covering every frame."""
    marks = sorted({0} | {b for b in boundaries if 0 < b < frames})
    edges = marks + [frames]
    return [{"name": "", "start": a, "end": b - 1}
            for a, b in zip(edges, edges[1:]) if b > a]

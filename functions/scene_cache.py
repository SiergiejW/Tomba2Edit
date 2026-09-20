"""A finished level kept on disc, so opening it again is instant.

Running an area's own code is what a level costs - seconds of it. Nothing
about that changes between two openings of the same area, so the built scene
is written out under a key of everything it was made from: the disc files it
read (size and modified time) and the source of every module that builds one.
Edit the disc or the code and the key changes, so a stale scene is never
served; there is nothing to invalidate by hand.

Kept in the system temp folder, newest CACHE_KEEP files, so it never grows
without bound and never lands in the project.
"""
import hashlib
import os
import pickle
import sys
import tempfile

CACHE = os.path.join(tempfile.gettempdir(), "tomba2-scene-cache")
# A kept scene has no `world` - the simulation itself is not carried over -
# so anything that wants to look at the actors turns this off first.
enabled = True
# Fresh, events-done and their merged view for every playable area fit at
# once. The old 64-entry limit evicted early areas while a translation project
# was still being explored, turning an exact result back into a cold load.
CACHE_KEEP = 256
# Bigger than this and reading it back costs more than running the area.
CACHE_MAX_BYTES = 256 << 20
_code = None


def _stamp(path):
    """A file by where it is, how big it is and when it changed. The path is
    normalised, so the same file spelled two ways is one key."""
    name = os.path.normcase(os.path.abspath(path))
    try:
        info = os.stat(path)
        return f"{name}:{info.st_size}:{info.st_mtime_ns}"
    except OSError:
        return f"{name}:-"


def _code_stamp():
    """Every module that builds a scene, by size and modified time."""
    global _code
    if _code is None and getattr(sys, "frozen", False):
        # A built exe carries no sources: the exe itself is the code.
        _code = hashlib.sha1(_stamp(sys.executable).encode()).hexdigest()[:16]
    if _code is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        parts = []
        for folder in ("functions", "gui"):
            for base, _dirs, names in os.walk(os.path.join(root, folder)):
                if "__pycache__" in base:
                    continue
                parts += [_stamp(os.path.join(base, n))
                          for n in sorted(names) if n.endswith(".py")]
        _code = hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
    return _code


def key(*parts):
    """A name for the scene these inputs build. A part that names a file is
    keyed by its size and modified time, so editing the disc loads afresh."""
    stamps = [_stamp(p) if isinstance(p, str) and os.path.isfile(p) else str(p)
              for p in parts]
    return (hashlib.sha1("|".join(stamps).encode()).hexdigest()[:24]
            + "-" + _code_stamp())


def read(name):
    """The scene's state dict, or None."""
    if not enabled:
        return None
    path = os.path.join(CACHE, name + ".pickle")
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (OSError, pickle.PickleError, AttributeError, EOFError, ValueError):
        return None


def write(name, state):
    """Keep this state under `name`, quietly doing nothing if it can't be."""
    if not enabled:
        return False
    path = os.path.join(CACHE, name + ".pickle")
    try:
        os.makedirs(CACHE, exist_ok=True)
        raw = pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)
        if len(raw) > CACHE_MAX_BYTES:
            return False
        # Interactive background loads and bulk preloading may finish the
        # same scene concurrently. Each writer owns its temporary file.
        with tempfile.NamedTemporaryFile(dir=CACHE, suffix=".part", delete=False) as f:
            temporary = f.name
            f.write(raw)
        os.replace(temporary, path)
        _prune()
        return True
    except (OSError, pickle.PickleError, TypeError, AttributeError, RecursionError):
        return False


def _prune():
    files = [os.path.join(CACHE, n) for n in os.listdir(CACHE) if n.endswith(".pickle")]
    for path in sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)[CACHE_KEEP:]:
        try:
            os.remove(path)
        except OSError:
            pass

"""Names for the audio on the disc, kept per release.

Separate from functions/labels.py, which names files inside TOMBA2.DAT
and is scored against a disc's IDX. These name things that are not DAT
entries at all - a channel of VOICE.XA, a track inside BGM.XA, a
waveform in a sound bank - so they have their own store and their own
folder. Dropping them in labels/ would put a file with no "entries" in
front of a loader that expects one.

The audio on the disc has no names in it. A dialogue channel is
"channel 3", a piece of music is "BGM.XA channel 1, track 2", a sound
effect is waveform 46 of bank 0 - true, and useless for finding the one
you want. So they can be renamed, and the names are kept here.

Names are stored against the disc's own serial, read out of SYSTEM.CNF:
the US retail disc boots SCUS_944.54, and a European or Japanese release
boots something else. Keying on that means one file per release, and
opening a different disc will not show you names written for another one
- the channel numbering is not promised to match across releases, and
quietly reusing names would be worse than having none.

Keys name the thing rather than its position in a list, so inserting or
reordering entries cannot shuffle the names onto the wrong audio:

    music       BGM.XA:1:2          file, channel, track
                TRACK2              the CD audio track
    dialogue    VOICE.XA:3          channel
    sfx         0:46                bank, waveform
"""
import json
import os
import re
import sys

FOLDER = "audio_names"
SECTIONS = ("music", "dialogue", "sfx")

# BOOT = cdrom:\SCUS_944.54;1
_BOOT = re.compile(rb"BOOT\s*=\s*cdrom:?\\?([A-Z0-9_.]+)\s*;", re.I)


def disc_id(image_path):
    """The serial the disc boots, or None.

    Read from SYSTEM.CNF rather than from the file name, so a renamed
    rip still finds its own names."""
    from functions import voice

    try:
        cnf = voice.extract_file(image_path, "SYSTEM.CNF")
    except Exception:
        return None
    if not cnf:
        return None
    found = _BOOT.search(cnf)
    if not found:
        return None
    return found.group(1).decode("ascii", "replace").upper()


def folder():
    """Where names are written - beside the program, not inside it.

    A frozen build unpacks itself somewhere temporary that is wiped on
    exit, so anything written there would be lost."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, FOLDER)


def bundled_folder():
    """The names shipped inside a frozen build, if there are any.

    A build carries the catalogued names with it, but writes go beside
    the executable - so this is read only, and only when nothing has
    been written for that disc yet."""
    inside = getattr(sys, "_MEIPASS", None)
    return os.path.join(inside, FOLDER) if inside else None


def path_for(disc):
    return os.path.join(folder(), f"{disc}.json")


def load(disc):
    """{section: {key: name}} for one disc, empty when there is no file."""
    empty = {name: {} for name in SECTIONS}
    if not disc:
        return empty
    tries = [path_for(disc)]
    inside = bundled_folder()
    if inside:
        tries.append(os.path.join(inside, f"{disc}.json"))
    stored = None
    for path in tries:
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            break
        except (OSError, ValueError):
            continue
    if stored is None:
        return empty
    for name in SECTIONS:
        value = stored.get(name)
        if isinstance(value, dict):
            empty[name] = {str(k): str(v) for k, v in value.items()}
    return empty


def save(disc, names):
    """Write the names back, dropping any that were cleared."""
    if not disc:
        return None
    os.makedirs(folder(), exist_ok=True)
    out = {"disc": disc}
    for name in SECTIONS:
        out[name] = {k: v for k, v in sorted(names.get(name, {}).items()) if v}
    path = path_for(disc)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path

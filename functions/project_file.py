"""A translation project as one file.

A project is a small tree - the game's three data files, the character
table, MAIN.EXE, SOP.BIN and a manifest - and it was a folder. That
works, but it puts the burden of understanding the layout on whoever
opens it: the thing you must select is not the folder you just saved but
a particular one inside it, nothing says so, and picking wrong gets you
an error about TOMBA2.DAT rather than an explanation.

So a project is also a .t2p, which is a zip of exactly that tree. One
file to open, to move, to put in version control, to send to whoever is
doing the translating. Nothing about the layout has to be explained
because nothing about it is visible.

Opening one unpacks it to a working directory and hands that back, so
every part of the editor that wants a folder keeps getting a folder and
none of them had to learn about archives. Saving packs the working
directory up again. The tree inside is byte for byte the folder layout,
which is what lets an old folder project be zipped into a new one and a
.t2p be unpacked into a folder with no conversion step either way.

The disc image is deliberately not in here. It is 400 MB, it is the one
part nobody edits, and the manifest identifies it well enough to find it
again - see functions/source_disc.
"""
import json
import os
import shutil
import zipfile

EXTENSION = ".t2p"
MANIFEST = "tomba2project.json"
FORMAT = "tomba2edit-translation-project"

FILTER = "Tomba 2 project (*.t2p)"

# Manifest keys naming a file that belongs to the project, beside the
# game folder every project has. Anything an editor needs kept that
# does not live inside TOMBA2.DAT or TOMBA2.IMG is added here - which
# is how the voice edits and the tree's names got in, and is where
# anything similar should go rather than growing a second container.
EXTRA_KEYS = ("main_exe", "sop_bin", "labels",
              "voice_index", "voice_blob")

# Deflate, not store. The DAT and IMG are mostly tables and indexed
# artwork and give up about half their size; a project comes out around
# 16 MB against the ~32 MB it holds, which is the difference between
# something you can attach to a message and something you cannot.
COMPRESSION = zipfile.ZIP_DEFLATED


class ProjectFileError(Exception):
    """Raised when a .t2p can't be read or written."""


def is_project_file(path):
    """Whether this is one of ours, judged by looking inside it.

    Not by extension: the point of the format is that people stop having
    to know what they are holding, and a .zip someone renamed by hand
    should still open."""
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as archive:
            return MANIFEST in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def read_manifest(path):
    """The manifest alone, without unpacking 32 MB to look at it."""
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open(MANIFEST) as f:
                return json.loads(f.read().decode("utf-8"))
    except KeyError:
        raise ProjectFileError(
            f"{os.path.basename(path)} is a zip, but there is no "
            f"{MANIFEST} in it - it isn't a translation project.")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ProjectFileError(
            f"{os.path.basename(path)} can't be read: {exc}") from exc


def members(folder, manifest):
    """[(file on disk, name inside the archive)] for one project.

    Chosen from the manifest rather than by walking the folder. A folder
    project very often has the disc image sitting in it - that is where
    people keep it, and it is the obvious place - and sweeping the whole
    directory would quietly pack 400 MB of it into a file whose entire
    point is being small enough to send. Anything not named here is not
    part of the project."""
    folder = os.path.abspath(folder)
    found = [(os.path.join(folder, MANIFEST), MANIFEST)]

    game = manifest.get("cd_folder") or ""
    game_dir = os.path.join(folder, game) if game else folder
    if os.path.isdir(game_dir):
        for name in sorted(os.listdir(game_dir)):
            full = os.path.join(game_dir, name)
            if os.path.isfile(full):
                inside = f"{game}/{name}" if game else name
                found.append((full, inside))

    for key in EXTRA_KEYS:
        relative = manifest.get(key)
        if not relative:
            continue
        full = os.path.join(folder, relative)
        if os.path.isfile(full):
            found.append((full, relative.replace(os.sep, "/")))
    return found


def pack(folder, path, manifest=None):
    """Write `folder` out as a project file, replacing what was there.

    Written beside the target and moved into place, so an interrupted
    save cannot leave a half-written project where a good one was - the
    file being overwritten is usually the only copy of the work."""
    folder = os.path.abspath(folder)
    manifest_path = os.path.join(folder, MANIFEST)
    if not os.path.isfile(manifest_path):
        raise ProjectFileError(
            f"{folder} has no {MANIFEST} in it, so it isn't a project "
            "that can be packed.")
    if manifest is None:
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError) as exc:
            raise ProjectFileError(
                f"{MANIFEST} can't be read: {exc}") from exc

    temporary = path + ".writing"
    try:
        with zipfile.ZipFile(temporary, "w", COMPRESSION) as archive:
            for full, inside in members(folder, manifest):
                archive.write(full, inside)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise ProjectFileError(str(exc)) from exc
    return path


def unpack(path, folder):
    """Unpack a project file into `folder`, which is emptied first.

    Returns its manifest. Entry names are checked rather than trusted:
    a zip can name paths outside the folder it is being written to, and
    a project file is exactly the kind of thing people pass around."""
    manifest = read_manifest(path)
    folder = os.path.abspath(folder)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if member.endswith("/"):
                    continue
                target = os.path.abspath(os.path.join(folder, member))
                if os.path.commonpath((folder, target)) != folder:
                    raise ProjectFileError(
                        f"{os.path.basename(path)} tries to write outside "
                        f"the folder it is opened into ({member}) - refusing "
                        "to unpack it.")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.open(member) as source, \
                        open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ProjectFileError(
            f"{os.path.basename(path)} couldn't be unpacked: {exc}") from exc
    return manifest


def game_folder(folder, manifest=None):
    """Where the game's files sit inside an unpacked project.

    The manifest names it, because version 1 projects called it CD and
    later ones do not; the fallbacks cover a project whose manifest has
    lost the name, and a folder that was never a project at all."""
    if manifest is None:
        try:
            with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            manifest = {}
    names = [manifest.get("cd_folder"), "Tomba 2 Game Files", "CD", ""]
    for name in names:
        if name is None:
            continue
        candidate = os.path.join(folder, name) if name else folder
        if os.path.isfile(os.path.join(candidate, "TOMBA2.DAT")):
            return candidate
    return None

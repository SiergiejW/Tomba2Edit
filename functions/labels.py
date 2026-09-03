"""Names for the files on the disc.

Nothing in TOMBA2.DAT carries a filename. format_detect reads what a
file IS out of its own bytes, on any build - but "the MDAT at
0x1B724" is as close as that gets to saying WHICH one it is. The names
are knowledge that only exists outside the disc, worked out by hand by
people opening files and looking at them.

A LABELS FILE is where that knowledge lives: a list of files in one
build of TOMBA2.DAT, each with the name someone gave it. They live in
the labels/ folder, one file per build, and any of them can be replaced
or added to without touching code - a new translation, a prototype, a
build nobody has mapped yet.

    {
      "name":  "Tomba! 2: The Evil Swine Return (USA)",
      "build": "us-retail",
      "dat_size": 9537536,
      "entries": [
        {"content": "3f7a9c1e5b0d2468", "start": "053724", "end": "075FDB",
         "type": "MDAT", "name": "Town of the Fishermen"},
        ...
      ]
    }

`content` is what an entry is actually keyed and looked up by: a short
hash of its own bytes. It has to be, not the address `start`/`end` come
from - a level's SDAT gets its own full copy of a character it reuses,
baked in once per area at build time rather than pointed at a shared
one the way the trail's files are, so the same asset - the same name -
sits at a different address in every area that has it. Keying by what
the bytes ARE rather than where one copy of them HAPPENS to be is what
lets a rename reach every copy at once, the same way it already does
for a trail file's genuinely-shared single copy.

`start`/`end` stay on each entry as where the FIRST copy this file
named was found - useful to a person reading the json, not looked up
by. `type` is what the person who mapped it recorded; the tool works
the type out for itself and only uses this to notice when the two
disagree.

WHICH FILE FITS A DISC

Not by version string or checksum - the tool edits and repacks these
discs, so a checksum would stop matching the moment it was used in
anger, and an address would too, for the reason above. A labels file is
instead scored against the disc in front of it: how many of the
CONTENT HASHES it names are really on the disc, which survives both.
The right file for a retail disc scores near 1.0 and the demo's scores
about 0.02, which is a wide enough gap to decide on and a direct
measure of the only thing that matters - whether these names will land
on the right files.

Run as a script to turn a TOMBAMAP txt into one of these. Pass --dat to
also rekey it by content hash against that build's real TOMBA2.DAT (and
--idx if TOMBA2.IDX isn't sitting right beside it) - without it the
entries come out address-keyed-as-a-fallback (see load()) rather than
the app landing a rename on every area a file happens to sit in:

    python -m functions.labels convert examples/TOMBAMAP_us.txt \\
        labels/us-retail.json --name "Tomba! 2 (USA)" --build us-retail \\
        --dat path/to/TOMBA2.DAT
"""
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field

# A labels file has to name at least this fraction of its own content on
# a disc before it is offered for it. Retail labels on a retail disc
# score 1.00 and on the demo 0.02, so anything in between is a disc
# that has been repacked far enough that the names can't be trusted.
MATCH_THRESHOLD = 0.5

# Entries the TOMBAMAP files leave unnamed are written as a bare "_".
UNNAMED = "_"

# How much of a sha256 an entry is keyed by - 64 bits, which is not
# remotely tight for the ~1,300 entries a disc has (the odds of two
# unrelated ones colliding are a rounding error), and short enough that
# the json stays readable by a person.
HASH_LENGTH = 16


def content_key(data):
    """A file's identity: a short hash of its own bytes.

    Two copies of the same character's model in two different areas
    hash the same and are the same labelled entry; two different files
    that happen to start the same way at a glance do not, because the
    hash is of the whole thing."""
    return hashlib.sha256(data).hexdigest()[:HASH_LENGTH]


class LabelError(ValueError):
    """Raised when a file doesn't read as a labels file."""


@dataclass
class Label:
    content: str = ""       # what this entry is keyed and looked up by
    start: int = 0           # where the first copy of it was found
    end: int = 0              # inclusive, as the source maps write it
    kind: str = ""            # the type whoever mapped it recorded
    name: str = ""


@dataclass
class LabelSet:
    name: str = ""
    build: str = ""
    serial: str = ""                             # disc serial, e.g. SCUS-94454
    source: str = ""
    dat_size: int = 0
    path: str = ""                               # where it was loaded from
    entries: dict = field(default_factory=dict)  # content hash -> Label
    areas: dict = field(default_factory=dict)    # chunk index -> area name
    bins: dict = field(default_factory=dict)     # BIN filename -> what it is
    # (chunk index, file index) -> content hash, for the build this set
    # was written against. See by_slot().
    slots: dict = field(default_factory=dict)

    def __len__(self):
        return len(self.entries)

    def by_slot(self, chunk_index, file_index):
        """The Label for the file in this slot, or None.

        A localised disc holds the same assets in the same order, but
        not the same bytes: its text is translated, and PAL retimes some
        animations, so those files hash differently and a content-keyed
        lookup misses them - 45 of them on the German and Spanish discs,
        including every single text file.

        Position finds them anyway. Every area holds the same file types
        in the same order on all three retail discs, so the file in slot
        N of area X is the same asset whatever language it speaks. This
        is the fallback, never the first choice: content is what
        identifies a file, and position only says which file to look at
        when the bytes have legitimately changed."""
        content = self.slots.get((chunk_index, file_index))
        return self.entries.get(content) if content else None

    def area_name(self, chunk_index):
        return self.areas.get(chunk_index, "")

    def bin_name(self, filename):
        return self.bins.get(filename.upper(), "")

    def rename(self, content, name, kind="", end=0, start=0):
        """Give the file with this content hash a name, adding an entry
        for it if this set has never heard of it - which is how a build
        with no labels of its own gets its first ones, typed into the
        tree. `start` is recorded only the first time, as where the
        entry was first seen - it plays no further part once the entry
        exists, since every copy sharing this hash is the same rename.

        An empty name clears it back to unnamed rather than recording an
        empty string, so an entry that was named by mistake goes back to
        being address-only like any other."""
        label = self.entries.get(content)
        if label is None:
            label = Label(content=content, start=start, end=end, kind=kind)
            self.entries[content] = label
        label.name = name.strip()
        if kind and not label.kind:
            label.kind = kind
        if end and not label.end:
            label.end = end
        if start and not label.start:
            label.start = start
        return label

    def rename_area(self, chunk_index, name):
        """Name an AREA folder, or clear it back to whatever the level
        inside it is called (see idx_parser._area_name_from_mdat)."""
        name = name.strip()
        if name:
            self.areas[chunk_index] = name
        else:
            self.areas.pop(chunk_index, None)

    @property
    def named(self):
        """How many entries actually carry a name - the TOMBAMAP files
        leave a lot of addresses recorded but unnamed."""
        return sum(1 for label in self.entries.values() if label.name)

    def get(self, content):
        return self.entries.get(content)

    def label_for(self, content):
        """The name for this content hash, or None."""
        label = self.entries.get(content)
        return label.name if label and label.name else None

    def score(self, hashes):
        """How much of this labels file is really on the disc, as a
        fraction of its own entries. `hashes` is every content hash the
        disc's own entries have (see idx_parser.content_hashes)."""
        if not self.entries:
            return 0.0
        found = sum(1 for content in self.entries if content in hashes)
        return found / len(self.entries)


def labels_dir():
    """The built-in labels/ folder, next to the code - or inside the
    bundle when this is running as a built exe."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "labels")


def load(path):
    """Read one labels file. Raises LabelError if it isn't one."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise LabelError(f"couldn't read {os.path.basename(path)}: {e}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise LabelError(f"{os.path.basename(path)} has no \"entries\" list")

    entries = {}
    for i, item in enumerate(raw["entries"]):
        try:
            start = int(str(item["start"]), 16)
            end = int(str(item.get("end", "0")), 16)
        except (KeyError, TypeError, ValueError) as e:
            raise LabelError(f"entry {i} has no readable start address: {e}") from e
        name = str(item.get("name", "") or "")
        content = str(item.get("content", "") or "")
        if not content:
            # A file saved before entries were keyed by content hash -
            # or hand-edited without one. Keyed on its address instead,
            # with a prefix no real hash can produce, so it neither
            # collides with one nor silently matches every disc: it
            # just sits unmatched until the file is next saved, which
            # writes a real hash once a disc to compute it from is open.
            content = f"addr:{start:06X}"
        entries[content] = Label(content=content, start=start, end=end,
                                 kind=str(item.get("type", "") or ""),
                                 name="" if name == UNNAMED else name)

    return LabelSet(
        name=str(raw.get("name", "") or os.path.basename(path)),
        build=str(raw.get("build", "") or ""),
        serial=str(raw.get("serial", "") or ""),
        source=str(raw.get("source", "") or ""),
        dat_size=int(raw.get("dat_size", 0) or 0),
        path=path,
        entries=entries,
        areas=_expand_areas(raw.get("areas") or {}, path),
        bins={str(k).upper(): str(v) for k, v in (raw.get("bins") or {}).items()},
        slots=_expand_slots(raw.get("slots") or {}),
    )


def _expand_slots(raw):
    """The "slots" table - "AREA:INDEX" (hex:decimal) to content hash."""
    out = {}
    for key, value in raw.items():
        try:
            chunk, index = str(key).split(":")
            out[(int(chunk, 16), int(index))] = str(value)
        except (ValueError, AttributeError):
            continue
    return out


def _expand_areas(raw, path):
    """The "areas" table - hex chunk numbers to names."""
    areas = {}
    for key, value in raw.items():
        try:
            areas[int(str(key), 16)] = str(value)
        except ValueError:
            print(f"Ignoring area key {key!r} in {os.path.basename(path)}")
    return areas


def builtin():
    """Every labels file in the labels/ folder, unreadable ones skipped
    with a printed reason rather than taking the app down."""
    folder = labels_dir()
    sets = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return sets
    for filename in names:
        if not filename.lower().endswith(".json"):
            continue
        try:
            sets.append(load(os.path.join(folder, filename)))
        except LabelError as e:
            print(f"Skipping labels file {filename}: {e}")
    return sets


def choose(hashes, candidates=None):
    """The labels file that fits this disc, as (LabelSet, score), or
    (None, best score seen) if none of them reach MATCH_THRESHOLD.

    `hashes` is every content hash the disc's own entries have - see
    idx_parser.content_hashes(), which builds it from the tree that has
    just been parsed rather than this reading the disc a second time."""
    sets = builtin() if candidates is None else candidates
    if not sets:
        return None, 0.0
    scored = sorted(((s.score(hashes), s) for s in sets),
                    key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    return (best, best_score) if best_score >= MATCH_THRESHOLD else (None, best_score)


# --------------------------------------------------------------------
# Turning a hand-written TOMBAMAP txt into one of these
# --------------------------------------------------------------------

def from_tombamap(txt_path, name="", build="", dat_size=0):
    """Read a TOMBAMAP txt - fixed-width "000000-00288F : SPRT : name"
    lines - into a LabelSet."""
    entries = {}
    with open(txt_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\n")
            if len(line) < 20:
                continue
            try:
                start, end = int(line[:6], 16), int(line[7:13], 16)
            except ValueError:
                continue
            label_name = line[23:].strip()
            entries[start] = Label(
                start=start, end=end, kind=line[16:20].strip(),
                name="" if label_name == UNNAMED else label_name)
    entries = {f"addr:{start:06X}": label for start, label in entries.items()}
    return LabelSet(name=name or os.path.basename(txt_path), build=build,
                    source=os.path.basename(txt_path), dat_size=dat_size,
                    entries=entries)


def idx_entry_sizes(idx_path):
    """{address: size} for every SDAT and trail entry the IDX names -
    the real byte length of each, the way idx_parser.parse_idx_file()
    itself works it out. Not from a hand-written `end`: a TOMBAMAP txt's
    end column turns out not to agree with itself on inclusive versus
    exclusive between one source and the next, which only matters once
    something actually reads `end - start` bytes rather than treating
    `end` as a label to print - which is exactly what rekeying by
    content is the first thing to do."""
    import struct

    chunk_size = 0x800
    trailer = 0x700
    sizes = {}
    size = os.path.getsize(idx_path)
    with open(idx_path, "rb") as idx:
        for chunk in range(size // chunk_size):
            idx.seek(chunk * chunk_size)
            _, _, dat_start, dat_end, count = struct.unpack("<5I", idx.read(20))
            if count:
                offsets = [v & 0xFFFFFF for v in
                          struct.unpack(f"<{count}I", idx.read(count * 4))]
                for i, offset in enumerate(offsets):
                    following = offsets[i + 1] if i + 1 < count else dat_end - dat_start
                    sizes[dat_start + offset] = following - offset
            idx.seek(chunk * chunk_size + (chunk_size - trailer))
            trail = struct.unpack(f"<{trailer >> 2}I", idx.read(trailer))
            for i in range(0, len(trail), 2):
                if trail[i + 1] - trail[i]:
                    sizes[trail[i]] = trail[i + 1] - trail[i]
    return sizes


def rekey_by_content(label_set, idx_path, dat_path):
    """An address-keyed (or address-fallback-keyed) LabelSet, rekeyed by
    the real content hash of each entry's bytes in `dat_path`.

    This is the migration a labels file needs exactly once: a fresh
    TOMBAMAP conversion has no hashes at all (from_tombamap has nowhere
    to read bytes from), and a labels file saved before entries were
    keyed by content still has its old address-fallback keys (see
    load()). Both are turned into real hashes here, against one build's
    actual disc - which has to be the SAME build the file's addresses
    were mapped against, since a wrong one would hash the wrong bytes at
    each address and silently mislabel everything.

    Sizes come from the IDX (see idx_entry_sizes), not from `end`; an
    entry whose `start` isn't a real IDX address any more - stale, or
    from a different build - is left out and returned as orphaned
    rather than guessed at.

    Two different old addresses turning out to hold identical bytes -
    which happens for real: an area's own copy of a shared character can
    coincidentally have been named twice under two different guesses
    before anyone noticed they were the same file - collapse into one
    entry. Whichever had a name first wins; the rest are returned as
    (old_address, dropped_name, kept_name) so the caller can decide
    whether to look at them rather than silently losing one.

    Returns (rekeyed_set, conflicts, orphaned_addresses)."""
    sizes = idx_entry_sizes(idx_path)
    merged = {}
    conflicts = []
    orphaned = []
    with open(dat_path, "rb") as dat:
        for label in sorted(label_set.entries.values(), key=lambda l: l.start):
            entry_size = sizes.get(label.start)
            if not entry_size:
                orphaned.append(label.start)
                continue
            dat.seek(label.start)
            content = content_key(dat.read(entry_size))
            existing = merged.get(content)
            if existing is None:
                merged[content] = Label(
                    content=content, start=label.start,
                    end=label.start + entry_size - 1, kind=label.kind,
                    name=label.name)
            elif not existing.name:
                # The first copy of this content happened to be
                # unnamed; a later duplicate fills the name in - not a
                # conflict, since there was nothing to disagree with.
                existing.name = label.name
                if not existing.kind:
                    existing.kind = label.kind
            elif label.name and label.name != existing.name:
                conflicts.append((label.start, label.name, existing.name))

    rekeyed = LabelSet(
        name=label_set.name, build=label_set.build, serial=label_set.serial,
        source=label_set.source, dat_size=label_set.dat_size,
        path=label_set.path, entries=merged,
        areas=dict(label_set.areas), bins=dict(label_set.bins))
    return rekeyed, conflicts, orphaned


def save(label_set, path, keep_existing=True):
    """Write a LabelSet out as a labels file, first-seen address order.

    `keep_existing` merges into whatever is already at `path`, replacing
    only the entry list - the ids, areas and bins sections are written
    by hand and there is nothing in a TOMBAMAP txt to regenerate them
    from, so converting a map again must not wipe them."""
    document = {}
    if keep_existing and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                document = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Couldn't merge into {path}, writing it fresh: {e}")
            document = {}

    document.update({
        "name": label_set.name or document.get("name", ""),
        "build": label_set.build or document.get("build", ""),
        "serial": label_set.serial or document.get("serial", ""),
        "source": label_set.source or document.get("source", ""),
        "dat_size": label_set.dat_size or document.get("dat_size", 0),
        "slots": {f"{c:02X}:{i}": h
                  for (c, i), h in sorted(label_set.slots.items())},
        "areas": {f"{index:02X}": name
                  for index, name in sorted(label_set.areas.items())},
        "bins": dict(sorted(label_set.bins.items())),
        "entries": [
            {
                "content": label.content,
                "start": f"{label.start:06X}",
                "end": f"{label.end:06X}",
                "type": label.kind,
                "name": label.name,
            }
            for label in sorted(label_set.entries.values(), key=lambda l: l.start)
        ],
    })
    # "entries" is long; keep it last so the sections a person edits are
    # at the top of the file.
    document = {k: document[k] for k in
                sorted(document, key=lambda k: k == "entries")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=1, ensure_ascii=False)
        f.write("\n")


def _main(argv):
    if len(argv) < 4 or argv[1] != "convert":
        print(__doc__.strip().splitlines()[-2].strip())
        return 1
    txt_path, out_path = argv[2], argv[3]
    options = dict(zip(argv[4::2], argv[5::2]))
    label_set = from_tombamap(
        txt_path,
        name=options.get("--name", ""),
        build=options.get("--build", ""),
        dat_size=int(options.get("--dat-size", 0)),
    )
    dat_path = options.get("--dat")
    if dat_path:
        # A TOMBAMAP txt has no content hashes to give the entries - it
        # is text, not disc access - so without this a fresh conversion
        # comes out address-keyed-as-a-fallback (see load()) and won't
        # match cross-area duplicates until it's rekeyed some other way.
        idx_path = options.get("--idx") or os.path.join(
            os.path.dirname(dat_path), "TOMBA2.IDX")
        label_set, conflicts, orphaned = rekey_by_content(
            label_set, idx_path, dat_path)
        for start, dropped, kept in conflicts:
            print(f"  same content as an earlier entry: 0x{start:X} "
                  f"named {dropped!r}, kept {kept!r}")
        if orphaned:
            print(f"  {len(orphaned)} entries weren't at a real IDX "
                  "address any more and were dropped")
    save(label_set, out_path)
    print(f"{len(label_set)} entries ({label_set.named} named) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))

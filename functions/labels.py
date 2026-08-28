"""Names for the files on the disc.

Nothing in TOMBA2.DAT carries a filename. The IDX gives every SDAT
entry a type id, format_detect works the type out of the bytes for the
trail, and between them the tree can say what a file IS - but "the
MDAT at 0x1B724" is as close as either gets to saying WHICH one it is.
The names are knowledge that only exists outside the disc, worked out
by hand by people opening files and looking at them.

A LABELS FILE is where that knowledge lives: a list of addresses in one
build of TOMBA2.DAT, each with the name someone gave it. They live in
the labels/ folder, one file per build, and any of them can be replaced
or added to without touching code - a new translation, a prototype, a
build nobody has mapped yet.

    {
      "name":  "Tomba! 2: The Evil Swine Return (USA)",
      "build": "us-retail",
      "dat_size": 9537536,
      "entries": [
        {"start": "053724", "end": "075FDB", "type": "MDAT",
         "name": "Town of the Fishermen"},
        ...
      ]
    }

`start` and `end` are hex offsets into TOMBA2.DAT, `end` inclusive, as
the hand-written TOMBAMAP txt files they came from wrote them. `type`
is what the person who mapped it recorded; the tool works the type out
for itself and only uses this to notice when the two disagree.

WHICH FILE FITS A DISC

Not by version string or checksum - the tool edits and repacks these
discs, so a checksum would stop matching the moment it was used in
anger. A labels file is instead scored against the disc in front of it:
how many of the addresses it names are really there in the IDX. The
right file for a retail disc scores near 1.0 and the demo's scores
about 0.02, which is a wide enough gap to decide on and a direct
measure of the only thing that matters - whether these names will land
on the right files.

Run as a script to turn a TOMBAMAP txt into one of these:

    python -m functions.labels convert examples/TOMBAMAP_us.txt \\
        labels/us-retail.json --name "Tomba! 2 (USA)" --build us-retail
"""
import json
import os
import sys
from dataclasses import dataclass, field

# A labels file has to name at least this fraction of its own addresses
# on a disc before it is offered for it. Retail labels on a retail disc
# score 1.00 and on the demo 0.02, so anything in between is a disc
# that has been repacked far enough that the names can't be trusted.
MATCH_THRESHOLD = 0.5

# Entries the TOMBAMAP files leave unnamed are written as a bare "_".
UNNAMED = "_"


class LabelError(ValueError):
    """Raised when a file doesn't read as a labels file."""


@dataclass
class Label:
    start: int
    end: int = 0            # inclusive, as the source maps write it
    kind: str = ""          # the type whoever mapped it recorded
    name: str = ""


@dataclass
class LabelSet:
    name: str = ""
    build: str = ""
    source: str = ""
    dat_size: int = 0
    path: str = ""                              # where it was loaded from
    entries: dict = field(default_factory=dict)  # start address -> Label

    def __len__(self):
        return len(self.entries)

    @property
    def named(self):
        """How many entries actually carry a name - the TOMBAMAP files
        leave a lot of addresses recorded but unnamed."""
        return sum(1 for label in self.entries.values() if label.name)

    def get(self, address):
        return self.entries.get(address)

    def label_for(self, address):
        """The name at `address`, or None."""
        label = self.entries.get(address)
        return label.name if label and label.name else None

    def score(self, addresses):
        """How much of this labels file is really on the disc, as a
        fraction of its own entries. `addresses` is every entry address
        the IDX gives (see idx_addresses)."""
        if not self.entries:
            return 0.0
        found = sum(1 for start in self.entries if start in addresses)
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
        entries[start] = Label(start=start, end=end,
                               kind=str(item.get("type", "") or ""),
                               name="" if name == UNNAMED else name)

    return LabelSet(
        name=str(raw.get("name", "") or os.path.basename(path)),
        build=str(raw.get("build", "") or ""),
        source=str(raw.get("source", "") or ""),
        dat_size=int(raw.get("dat_size", 0) or 0),
        path=path,
        entries=entries,
    )


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


def idx_addresses(idx_path):
    """Every absolute DAT address the IDX names, SDAT entries and trail
    files alike - what a labels file is scored against."""
    import struct

    chunk_size = 0x800
    trailer = 0x700
    addresses = set()
    size = os.path.getsize(idx_path)
    with open(idx_path, "rb") as idx:
        for chunk in range(size // chunk_size):
            idx.seek(chunk * chunk_size)
            _, _, dat_start, _, count = struct.unpack("<5I", idx.read(20))
            if count:
                for value in struct.unpack(f"<{count}I", idx.read(count * 4)):
                    addresses.add(dat_start + (value & 0xFFFFFF))
            idx.seek(chunk * chunk_size + (chunk_size - trailer))
            trail = struct.unpack(f"<{trailer >> 2}I", idx.read(trailer))
            for i in range(0, len(trail), 2):
                if trail[i + 1] - trail[i]:
                    addresses.add(trail[i])
    return addresses


def choose(idx_path, candidates=None):
    """The labels file that fits this disc, as (LabelSet, score), or
    (None, best score seen) if none of them reach MATCH_THRESHOLD."""
    sets = builtin() if candidates is None else candidates
    if not sets:
        return None, 0.0
    try:
        addresses = idx_addresses(idx_path)
    except (OSError, Exception) as e:      # a truncated IDX shouldn't be fatal
        print(f"Couldn't read {idx_path} to match labels against: {e}")
        return None, 0.0
    scored = sorted(((s.score(addresses), s) for s in sets),
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
    return LabelSet(name=name or os.path.basename(txt_path), build=build,
                    source=os.path.basename(txt_path), dat_size=dat_size,
                    entries=entries)


def save(label_set, path):
    """Write a LabelSet out as a labels file, addresses in order."""
    document = {
        "name": label_set.name,
        "build": label_set.build,
        "source": label_set.source,
        "dat_size": label_set.dat_size,
        "entries": [
            {
                "start": f"{label.start:06X}",
                "end": f"{label.end:06X}",
                "type": label.kind,
                "name": label.name,
            }
            for label in sorted(label_set.entries.values(), key=lambda l: l.start)
        ],
    }
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
    save(label_set, out_path)
    print(f"{len(label_set)} entries ({label_set.named} named) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))

"""Text out to be translated, and back in again.

A translation is not done in this tool - it is done by whatever the
translator uses, on the whole script at once. So the script leaves as
one file, comes back as one file, and every entry has to survive the
round trip by name rather than by position: a translator reorders
nothing, but a machine one drops entries, and an entry that comes back
under a name nothing on the disc has is better reported than guessed at.

    id      "txtd:00078A08:1:0"   file address, master, entry
            "txt2:000081D4:5"     file address, entry
            "mainexe:000E92"      pool offset
            "sop:000058"          pool offset

The address is the DAT address the file sits at, which is what every
other part of the tool keys a text file by (see idx_parser's
address_locations) - the same file reached from four areas is one entry
here, not four.

TWO FILE SHAPES, THE SAME RECORDS

JSON is the one to hand a translator. Plain text is for reading and for
hand editing, and its blocks are delimited by the id line alone rather
than by blank lines, because an entry's own text has blank lines in it -
"{$END}\\n\\n" is the commonest string on the disc.

WHAT MUST NOT BE TRANSLATED

Every {$TAG}. They are the colour changes, the button icons, the line
breaks and the end markers, and the packer refuses text that has lost
them. The exported note says so; nothing here can enforce it, but
importing tells you which entries stopped encoding.
"""
import json
import os
import re

FORMAT = "tomba2-text"
VERSION = 1

NOTE = ("Translate the \"text\" field only. Leave \"id\" and \"source\" "
        "alone, and keep every {$TAG} exactly as it is - they are colour, "
        "button and line-break controls, not words.")

# Which kinds there are, and how many numbers follow the address.
KINDS = {"txtd": 2, "txt2": 1, "mainexe": 0, "sop": 0}

_ID_RE = re.compile(r"^(%s)((?::[0-9A-Fa-f]+)+)$" % "|".join(KINDS))

# A block marker has to start with one of the kinds. Any bracketed line
# would do as a delimiter until the disc's own text turns out to have
# one - MAIN.EXE's pool alone is full of bracketed menu words - and an
# entry that swallows the next entry's marker is a corruption that
# reads like a translation mistake.
_BLOCK_RE = re.compile(r"^\[((?:%s):[^\[\]]*)\]$" % "|".join(KINDS))


class TranslationIOError(ValueError):
    """Raised when a file isn't one of these, or can't be written."""


def make_id(kind, address, *rest):
    """The name one entry travels under."""
    parts = ["%08X" % address] + ["%d" % n for n in rest]
    return "%s:%s" % (kind, ":".join(parts))


def parse_id(text):
    """(kind, address, *rest), or None if that isn't one of ours."""
    match = _ID_RE.match(text.strip())
    if not match:
        return None
    kind = match.group(1)
    numbers = match.group(2).lstrip(":").split(":")
    if len(numbers) != KINDS[kind] + 1:
        return None
    try:
        return (kind, int(numbers[0], 16)) + tuple(
            int(n, 10) for n in numbers[1:])
    except ValueError:
        return None


# --------------------------------------------------------------------
# Collecting
# --------------------------------------------------------------------

def txtd_records(address, data, label="", only=None):
    """One parsed TXTD (txtd.preview's output) as records.

    `only` narrows it to a set of (master, entry) locations - one
    entry's worth, for exporting just what is selected.

    The END sentinel entries carry no text and are left out - they are
    table structure, and a translator handed one would translate the
    word "END!"."""
    out = []
    for m_idx, group in enumerate(data.get("entries", [])):
        for e_idx, entry in enumerate(group.get("entries", [])):
            if only is not None and (m_idx, e_idx) not in only:
                continue
            if entry.get("adr") == 0xFFFF and entry.get("extra") == 0xFFFF:
                continue
            record = {
                "id": make_id("txtd", address, m_idx, e_idx),
                "source": entry.get("text", ""),
                "text": entry.get("text", ""),
            }
            if label:
                record["file"] = label
            speaker = entry.get("extra")
            if isinstance(speaker, int):
                record["speaker"] = "%04X" % speaker
            out.append(record)
    return out


def txt2_records(address, data, label="", only=None):
    """One parsed TXT2/TXT1 (txt2.preview's output) as records.

    A gap fragment is un-addressed text the file carries anyway, and the
    packer writes it back, so it goes out to be translated like any
    other entry."""
    out = []
    for e_idx, entry in enumerate(data.get("entries", [])):
        if only is not None and e_idx not in only:
            continue
        if entry.get("text") == "END!":
            continue
        record = {
            "id": make_id("txt2", address, e_idx),
            "source": entry.get("text", ""),
            "text": entry.get("text", ""),
        }
        if label:
            record["file"] = label
        if entry.get("is_gap"):
            record["gap"] = True
        out.append(record)
    return out


def pool_records(kind, entries, label=""):
    """MAIN.EXE's or SOP.BIN's string pool as records."""
    out = []
    for entry in entries:
        record = {
            "id": make_id(kind, entry["offset"]),
            "source": entry.get("text", ""),
            "text": entry.get("text", ""),
        }
        if label:
            record["file"] = label
        out.append(record)
    return out


# --------------------------------------------------------------------
# Files
# --------------------------------------------------------------------

def dump(records, path, build="", note=NOTE):
    """Write records out. Plain text if the name says .txt, JSON
    otherwise - JSON is what a translator is handed, so a name with no
    extension on it should be that and not the other one."""
    meta = {"format": FORMAT, "version": VERSION, "build": build,
            "entries": len(records), "note": note}
    if os.path.splitext(path)[1].lower() != ".txt":
        blob = dict(meta)
        blob["entries"] = records
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False, indent=2)
        return len(records)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# %s %d  build=%s  entries=%d\n" %
                (FORMAT, VERSION, build or "?", len(records)))
        f.write("# %s\n" % note)
        f.write("# One entry per [id] line; its text runs to the next one.\n")
        for record in records:
            f.write("[%s]\n%s\n" % (record["id"], record["text"]))
    return len(records)


def load(path):
    """(meta, records) out of either shape. Raises TranslationIOError.

    Which shape it is comes from the bytes rather than the name: a
    translator hands back whatever their tool saved, and it is often
    not called what it was called going out."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        raise TranslationIOError(str(exc)) from exc
    if raw.lstrip()[:1] == "{":
        return _load_json(raw, path)
    return _load_text(raw, path)


def _load_json(raw, path):
    try:
        blob = json.loads(raw)
    except ValueError as exc:
        raise TranslationIOError(
            f"{os.path.basename(path)} is not valid JSON: {exc}") from exc
    if not isinstance(blob, dict) or not isinstance(blob.get("entries"), list):
        raise TranslationIOError(
            f"{os.path.basename(path)} has no \"entries\" list - this is not "
            "a text export from this tool.")
    records = []
    for item in blob["entries"]:
        if not isinstance(item, dict) or "id" not in item:
            continue
        records.append({"id": str(item["id"]),
                        "text": item.get("text", ""),
                        "source": item.get("source", "")})
    meta = {k: v for k, v in blob.items() if k != "entries"}
    return meta, records


def _load_text(raw, path):
    meta = {}
    records = []
    current = None
    body = []
    for line in raw.split("\n"):
        if current is None and line.startswith("#"):
            head = re.match(r"^#\s*%s\s+(\d+)\s+build=(\S+)" % FORMAT, line)
            if head:
                meta = {"format": FORMAT, "version": int(head.group(1)),
                        "build": head.group(2)}
            continue
        match = _BLOCK_RE.match(line)
        if match:
            if current is not None:
                records.append({"id": current, "text": "\n".join(body),
                                "source": ""})
            current = match.group(1)
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        # Every block but the last is closed by the next marker line,
        # which is what consumes the newline dump() writes after the
        # text. The last one has no marker after it, so that newline
        # shows up as an empty line of its own and comes off here -
        # without which every entry whose text really does end in a
        # newline would lose it, and the pool is full of those.
        if raw.endswith("\n") and body and body[-1] == "":
            body.pop()
        records.append({"id": current, "text": "\n".join(body), "source": ""})
    if not records:
        raise TranslationIOError(
            f"{os.path.basename(path)} holds no [id] blocks - this is not a "
            "text export from this tool.")
    return meta, records


# --------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------

def group_by_kind(records):
    """{kind: {key: text}} with every id parsed, plus the ids that are
    not ours at all. The key is (address, *rest)."""
    grouped = {}
    bad = []
    for record in records:
        parsed = parse_id(record["id"])
        if parsed is None:
            bad.append(record["id"])
            continue
        kind, key = parsed[0], parsed[1:]
        grouped.setdefault(kind, {})[key] = record.get("text", "")
    return grouped, bad


def apply_txtd(data, address, texts):
    """Put `texts` ({(address, master, entry): text}) into a parsed TXTD.

    Returns (changed locations, ids that named nothing in this file).
    An entry whose text is already what the import says is not counted:
    an import that changes nothing should leave nothing marked edited."""
    changed = set()
    missed = []
    groups = data.get("entries", [])
    for key, text in texts.items():
        if key[0] != address:
            continue
        _addr, m_idx, e_idx = key
        try:
            entry = groups[m_idx]["entries"][e_idx]
        except (IndexError, KeyError, TypeError):
            missed.append(make_id("txtd", *key))
            continue
        if entry.get("text") != text:
            entry["text"] = text
            changed.add((m_idx, e_idx))
    return changed, missed


def apply_txt2(data, address, texts):
    """The same for a parsed TXT2/TXT1."""
    changed = set()
    missed = []
    entries = data.get("entries", [])
    for key, text in texts.items():
        if key[0] != address:
            continue
        _addr, e_idx = key
        if e_idx >= len(entries):
            missed.append(make_id("txt2", *key))
            continue
        entry = entries[e_idx]
        if entry.get("text") != text:
            entry["text"] = text
            changed.add(e_idx)
    return changed, missed


def apply_pool(entries, texts):
    """The same for a MAIN.EXE or SOP.BIN pool, keyed by offset.

    Returns ({offset: text} for the ones that changed, missed ids)."""
    changed = {}
    missed = []
    by_offset = {e["offset"]: e for e in entries}
    for key, text in texts.items():
        offset = key[0]
        entry = by_offset.get(offset)
        if entry is None:
            missed.append("%08X" % offset)
            continue
        if entry.get("text") != text:
            changed[offset] = text
    return changed, missed

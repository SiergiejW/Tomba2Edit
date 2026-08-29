"""Which character codes a disc's own text actually uses.

A translation has to put its letters somewhere, and the only cells it can
safely take are the ones no string on the disc asks for. That cannot be
read off the font page - a cell can hold a finished glyph and still be
unreachable, which on the US disc is true of most of them - so it is
measured from the text instead: every TXTD, TXT1 and TXT2 file on the
disc is decoded and the codes they resolve to are counted.

Raw-byte escapes are not counted. A text file's entries run into regions
that are not text, and those bytes come back as {$XX}; counting them
would mark nearly every code used and hide the room that is really
there. Only bytes that decode to a character or a named token count.

The answer is cached beside the disc, since the scan reads every text
file on it.
"""
import contextlib
import io
import json
import os
import re
import struct

CACHE = "codeuse.json"

# A byte with no character, written out by the readers as {$XX}. These
# are data the scanner walked into, not text.
_HEX_ESCAPE = re.compile(r"^\{\$[0-9A-Fa-f]{2}\}")

_TEXT_KINDS = ("TXTD", "TXT1", "TXT2")


def text_files(dat_path):
    """[(kind, start, end)] for every text file the IDX points at."""
    from functions import format_detect

    folder = os.path.dirname(dat_path)
    idx_path = os.path.join(folder, "TOMBA2.IDX")
    found = set()
    with open(idx_path, "rb") as idx, open(dat_path, "rb") as dat:
        chunks = os.path.getsize(idx_path) // 0x800
        for chunk in range(chunks):
            idx.seek(chunk * 0x800)
            head = idx.read(20)
            if len(head) < 20:
                break
            _is, _ie, dat_start, dat_end, count = struct.unpack("<5I", head)
            if not count or count > 500:
                continue
            raw = idx.read(count * 4)
            if len(raw) < count * 4:
                continue
            # The pointer's top byte is an id; the rest is the offset.
            offsets = [v & 0xFFFFFF
                       for v in struct.unpack(f"<{count}I", raw)]
            offsets.append(dat_end - dat_start)
            for i in range(count):
                start = dat_start + offsets[i]
                end = dat_start + offsets[i + 1]
                if end <= start or end - start > 0x200000:
                    continue
                found.add((start, end))

        out = []
        for start, end in sorted(found):
            try:
                kind = format_detect.entry_type(dat, start, end - start)[0]
            except Exception:
                continue
            if kind in _TEXT_KINDS:
                out.append((kind, start, end))
    return out


class _Discard:
    """Somewhere for the readers' progress printing to go.

    They print a line per entry, and there are tens of thousands, so
    this has to throw the text away rather than collect it."""

    def write(self, _text):
        return 0

    def flush(self):
        pass


def _entries(dat_path, kind, start, end):
    """Every decoded string in one text file."""
    from gui.txtd import txt2 as txt2_mod, txtd as txtd_mod

    sink = _Discard()
    with contextlib.redirect_stdout(sink):
        if kind == "TXT2":
            blob = txt2_mod.preview(dat_path, start, end - start, 3)
        else:
            blob = txtd_mod.preview(dat_path, start)

    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "text" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(blob)
    return found


def measure(dat_path):
    """{code: how many times the disc's text uses it}."""
    from gui.txtd.txtd_packer import _REVERSE_LETTERS, _TOKENS

    counts = {}
    seen = set()
    for kind, start, end in text_files(dat_path):
        try:
            entries = _entries(dat_path, kind, start, end)
        except Exception:
            continue
        for text in entries:
            if text in seen:
                continue
            seen.add(text)
            i = 0
            while i < len(text):
                if _HEX_ESCAPE.match(text[i:i + 5]):
                    i += 5
                    continue
                for token in _TOKENS:
                    if text.startswith(token, i):
                        code = _REVERSE_LETTERS[token]
                        counts[code] = counts.get(code, 0) + 1
                        i += len(token)
                        break
                else:
                    i += 1
    return counts


def cached(dat_path):
    """The saved measurement, or None if this disc has not been measured.

    Measuring reads every text file on the disc and takes over a minute,
    so anything on the GUI thread must take this and run the scan in the
    background if it comes back empty."""
    path = os.path.join(os.path.dirname(dat_path), CACHE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {int(k, 0) for k in json.load(f)["counts"]}
    except Exception:
        return None


def used_codes(dat_path, refresh=False):
    """The codes the disc's text uses, cached beside the disc."""
    path = os.path.join(os.path.dirname(dat_path), CACHE)
    if not refresh:
        hit = cached(dat_path)
        if hit is not None:
            return hit
    counts = measure(dat_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"counts": {f"0x{c:02X}": n
                                  for c, n in sorted(counts.items())}}, f,
                      indent=2)
    except OSError:
        pass
    return set(counts)

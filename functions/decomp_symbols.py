"""Addresses for the names in the annotated decomp, read back off the disc.

decomp/MAIN.EXE_US_DECOMP.c names most functions but drops their
addresses; only FUN_8xxxxxxx and FUN_Axx__8xxxxxxx keep theirs, and a
named overlay function still carries its LAB_Axx__ labels. Running the
game's code needs addresses (functions/actor_sim.py hooks a handful), so
they are recovered the way a person would:

    a function whose start is known is disassembled, its `jal` targets
    are listed in address order, and when that list is as long as the
    decomp's list of calls in the body - and pairs every repeated name
    with the same target - each pair is a vote for "this name lives
    there".

A name settled that way makes its own body a source of votes, so it runs
to a fixed point. Starts come from `jal` targets, the FUN_ names and the
labels (a named overlay function starts at the last known start at or
before its lowest label). Overlays all load at the same address, so
every address is qualified by its image: "MAIN" or "A00".."A0L".
"""
import collections
import json
import os
import re
import struct

from functions.mips import Image

OVERLAY_BASE = 0x80108F9C
EXE_HEADER = 0x800
MAIN = "MAIN"

_HEADER = re.compile(r"\n\n((?:[^\n;{}]*\n)?[^\n;{}]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\))\s*\n\{\n")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_LABEL = re.compile(r"\b(?:LAB|switchD|code_r0x|FUN|DAT|PTR_DAT)_(A[0-9A-L]{2})__([0-9a-f]{8})")
_FUN_MAIN = re.compile(r"^FUN_(8[0-9a-f]{7})$")
_FUN_OVL = re.compile(r"^FUN_(A[0-9A-L]{2})__([0-9a-f]{8})$")
_LOCAL_LABEL = re.compile(r"\b(?:LAB|switchD|code_r0x)_(A[0-9A-L]{2})__([0-9a-f]{8})")
_NOT_CALLS = frozenset((
    "if", "while", "for", "switch", "return", "sizeof", "trap", "halt_baddata",
    "code", "void", "int", "uint", "short", "ushort", "char", "uchar", "byte",
    "undefined", "undefined1", "undefined2", "undefined4", "long", "ulong",
    "longlong", "ulonglong", "bool", "float", "double"))
_NOT_CALL_PREFIX = ("gte_", "CONCAT", "SUB", "ZEXT", "SEXT", "CARRY", "SBORROW",
                    "POPCOUNT", "LZCOUNT", "setCopReg", "getCopReg", "SCARRY")


class Function:
    __slots__ = ("name", "image", "calls", "labels")

    def __init__(self, name, image, calls, labels):
        self.name, self.image, self.calls, self.labels = name, image, calls, labels


def parse(decomp_text):
    """[Function, ...] out of the decomp, in file order."""
    out = []
    heads = list(_HEADER.finditer(decomp_text))
    for n, m in enumerate(heads):
        name = m.group(2)
        start = m.end()
        stop = decomp_text.find("\n}\n", start)
        if stop < 0:
            continue
        body = decomp_text[start:stop]
        calls = [c for c in _CALL.findall(body)
                 if c not in _NOT_CALLS and not c.startswith(_NOT_CALL_PREFIX)
                 and c != name]
        labels = collections.defaultdict(list)
        for tag, address in _LOCAL_LABEL.findall(body):
            labels[tag].append(int(address, 16))
        image = None
        fm, fo = _FUN_MAIN.match(name), _FUN_OVL.match(name)
        if fo:
            image = fo.group(1)
        elif fm:
            image = MAIN
        elif labels:
            image = max(labels, key=lambda t: len(labels[t]))
        out.append(Function(name, image, calls, dict(labels)))
    return out


def load_images(exe_path, bin_folder):
    images = {}
    with open(exe_path, "rb") as f:
        data = f.read()
    images[MAIN] = Image(data[EXE_HEADER:], struct.unpack_from("<I", data, 0x18)[0])
    for tag in (f"A0{c}" for c in "0123456789ABCDEFGHIJKL"):
        path = os.path.join(bin_folder, f"{tag}.BIN")
        if os.path.exists(path):
            with open(path, "rb") as f:
                images[tag] = Image(f.read(), OVERLAY_BASE)
    return images


def _owner(images, tag, address):
    """Which image an address in `tag`'s code refers to."""
    if tag != MAIN and address in images[tag]:
        return tag
    if address in images[MAIN]:
        return MAIN
    return None


JR_RA = 0x03E00008


def _starts(images):
    """{tag: function starts} from every jal target, and from every
    stack frame opened right after a return - which is how a routine
    only ever called through a pointer, a placement handler, shows."""
    found = collections.defaultdict(set)
    for tag, image in images.items():
        data, base = image.data, image.base
        words = struct.unpack_from(f"<{len(data) // 4}I", data)
        for n, word in enumerate(words):
            if word >> 26 == 3:
                target = ((base + n * 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
                owner = _owner(images, tag, target)
                if owner is not None:
                    found[owner].add(target)
            elif (word >> 16 == 0x27BD and word & 0x8000 and n >= 2
                  and JR_RA in (words[n - 2], words[n - 3] if n >= 3 else 0)):
                found[tag].add(base + n * 4)
    return found


def _calls_in(image, tag, images, start, end):
    out = []
    for at in range(start, min(end, image.base + len(image.data) - 3), 4):
        word = image.word(at)
        op = word >> 26
        if op in (2, 3):
            target = (at & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
            if op == 2 and start <= target < end:
                continue                    # a jump inside the routine
            owner = _owner(images, tag, target)
            if owner is not None:
                out.append((owner, target))
    return out


def resolve(decomp_path, exe_path, bin_folder, rounds=12):
    """{name: (image tag, address)}."""
    with open(decomp_path, encoding="utf-8", errors="replace") as f:
        functions = parse(f.read())
    images = load_images(exe_path, bin_folder)
    starts = _starts(images)
    known = {}
    for fn in functions:
        fm, fo = _FUN_MAIN.match(fn.name), _FUN_OVL.match(fn.name)
        if fm:
            known[fn.name] = (MAIN, int(fm.group(1), 16))
        elif fo and fo.group(1) in images:
            known[fn.name] = (fo.group(1), int(fo.group(2), 16))
    for tag, addresses in list(starts.items()):
        addresses.update(a for t, a in known.values() if t == tag)
    ordered = {tag: sorted(a) for tag, a in starts.items()}

    def start_before(tag, address):
        seq = ordered.get(tag, [])
        lo, hi = 0, len(seq)
        while lo < hi:
            mid = (lo + hi) // 2
            if seq[mid] <= address:
                lo = mid + 1
            else:
                hi = mid
        return seq[lo - 1] if lo else None

    for fn in functions:
        if fn.name in known or not fn.labels or fn.image not in images:
            continue
        first = min(fn.labels[fn.image])
        at = start_before(fn.image, first)
        if at is not None:
            known[fn.name] = (fn.image, at)

    def extent(tag, start):
        seq = ordered.get(tag, [])
        for a in seq:
            if a > start:
                return a
        return start + 0x4000

    by_name = {fn.name: fn for fn in functions}
    for _round in range(rounds):
        votes = collections.defaultdict(collections.Counter)
        for name, (tag, start) in known.items():
            fn = by_name.get(name)
            if fn is None or not fn.calls:
                continue
            image = images[tag]
            jals = _calls_in(image, tag, images, start, extent(tag, start))
            if len(jals) != len(fn.calls):
                continue
            pairs = {}
            consistent = True
            for callee, target in zip(fn.calls, jals):
                if pairs.setdefault(callee, target) != target:
                    consistent = False
                    break
            if not consistent or len(set(pairs.values())) != len(pairs):
                continue
            for callee, target in pairs.items():
                votes[callee][target] += 1
        added = 0
        taken = collections.Counter(known.values())
        for callee, counter in votes.items():
            if callee in known:
                continue
            (target, best), *rest = counter.most_common(2) + [(None, 0)]
            second = rest[0][1] if rest else 0
            if best >= 1 and best > second and taken[target] == 0:
                known[callee] = target
                taken[target] += 1
                added += 1
                tag, address = target
                if address not in starts[tag]:
                    starts[tag].add(address)
                    ordered[tag] = sorted(starts[tag])
        if not added:
            break
    return known


def load(cache_path, decomp_path, exe_path, bin_folder):
    """The symbols, from a cache next to the decomp when it is newer."""
    if (os.path.exists(cache_path) and os.path.getmtime(cache_path)
            >= os.path.getmtime(decomp_path)):
        with open(cache_path) as f:
            return {k: tuple(v) for k, v in json.load(f).items()}
    known = resolve(decomp_path, exe_path, bin_folder)
    with open(cache_path, "w") as f:
        json.dump(known, f, indent=0, sort_keys=True)
    return known


if __name__ == "__main__":
    import sys
    known = resolve(*sys.argv[1:4])
    named = {k: v for k, v in known.items() if not k.startswith("FUN_")}
    print(f"{len(known)} addressed, {len(named)} of them named")
    for probe in sys.argv[4:]:
        v = known.get(probe)
        print(probe, "->", (v[0], hex(v[1])) if v else None)

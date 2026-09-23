"""Which build of the game is open, and where US retail's addresses went in it.

Everything that runs or reads the game's code - game/actor_sim.py,
placement.py, pickup_art.py and the rest - names routines and variables by
their US retail address, as the annotated decomp found them. Every other
build (the European and Japanese releases, the two preview discs) is the
same program compiled again: a routine grew or shrank a few words, a
variable moved, and everything after it shifted.

So nothing is looked up by hand. Each build's code is lined up with US
retail's word by word (`align`), with the parts that move when code is
relinked masked out - a jal's target, an immediate - and the pairs that line
up say where each US instruction now is. Variables come from the code that
uses them: a `lui`/`addiu` (or `lui`/`lw`) pair in US names one address, the
same pair in the build names another, and every such pair is a vote.

A routine that changed too much to line up whole is settled on its own
(_realign_routines): its callers' jals, the handler tables that point at it
and, for the JP demo, its own annotated decomp all say where it might start,
and the candidate whose first words are most like US retail's wins. One that
still lands in the middle of another routine is recorded as missing, and
translates to nothing rather than to a guess - the demos have no
f_LoadResourceChunkIntoGroup at all.

The demos also number the area's file table differently (the MDAT is slot 6,
not 8); the code that loads each slot says how, and Build.slot translates.

`generate` does all that once with both discs to hand and keeps the result
in decomp/builds/<build>.json; opening a build needs only that file. US
retail itself needs none - its map is the identity. To make them again:

    python -m game.game_build <US retail folder> eu-retail=<folder> ...
        jp-demo=<folder>,decomp/MAIN.EXE_JPDEMO_DECOMP.c

An address in an overlay is only meaningful in that overlay - all of them
load at the same address - so overlay addresses are translated per image
("A00".."A0L", "SOP", ...). MAIN.EXE's are image "MAIN".
"""
import bisect
import collections
import difflib
import hashlib
import json
import os
import struct
import sys

EXE_HEADER = 0x800
T_ADDR, T_SIZE = 0x18, 0x1C
MAIN = "MAIN"
REFERENCE = "us-retail"

# US retail's own layout - what every address in the code is written in.
US_OVERLAY_BASE = 0x80108F9C
US_AREA_BASE = 0x8018A000
# The area's file table: one pointer per SDAT id ("slot"). The demos number
# their slots differently, and the code says how (Build.slot).
US_FILE_TABLE = 0x800ECF58
FILE_SLOTS = 64
RAM_LOW, RAM_HIGH = 0x80000000, 0x80200000
SCRATCH_LOW, SCRATCH_HIGH = 0x1F800000, 0x1F800400

MAPS = os.path.join(
    getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "decomp", "builds")

# Every build a map is kept for. A map is found by its MAIN.EXE's header
# (header_digest) - the entry point in it is one no two builds share.
LABELS = {
    "us-retail": "Tomba! 2 (USA)",
    "eu-retail": "Tombi! 2 (Europe)",
    "fr-retail": "Tombi! 2 (France)",
    "it-retail": "Tombi! 2 (Italy)",
    "de-retail": "Tombi! 2 (Germany)",
    "es-retail": "Tombi! 2 (Spain)",
    "jp-retail": "Tomba! The Wild Adventures (Japan)",
    "proto-0928": "Tomba! 2 prototype (28 Sep 1999)",
    "proto-1111": "Tomba! 2 prototype (11 Nov 1999)",
    "us-demo": "Tomba! 2 (USA) demo",
    "jp-demo": "Tomba! The Wild Adventures (Japan) taikenban",
}

# Overlay images, by file name.
OVERLAYS = tuple(f"A0{c}" for c in "0123456789ABCDEFGHIJKL") + (
    "SOP", "OPN", "CRD", "GAME", "DEMO", "START")

# Anchors: runs of this many masked words found once in each build.
ANCHOR = 10
# Gaps between anchors are lined up by difflib up to this many word pairs,
# with shorter anchors first above it.
GAP_CELLS = 4_000_000
# A matched run shorter than this whose shift neither neighbour shares is
# chance (a stray nop in data) and is dropped.
SHORT_RUN = 4
# How far past its last lui a register's high half is still believed.
HI_REACH = 48
# How far from a known variable an unreferenced one may be and still take
# its shift.
DATA_REACH = 0x800


def header_digest(exe):
    """The 2048-byte header with its size field cleared - what tells builds
    apart even after a text edit has regrown the file."""
    head = bytearray(exe[:EXE_HEADER])
    head[T_SIZE:T_SIZE + 4] = bytes(4)
    return hashlib.sha256(bytes(head)).hexdigest()


# --- lining two builds up ----------------------------------------------

def _mask(word):
    """What of an instruction survives relinking: jumps lose their target,
    I-types their immediate; R-types and GTE ops are kept whole."""
    op = word >> 26
    if op == 0 or op == 0x12:
        return word
    if op in (2, 3):
        return op << 26
    return word & 0xFFFF0000


def _words(data):
    return struct.unpack_from(f"<{len(data) // 4}I", data)


def _unique(seq, k):
    seen, repeated = {}, set()
    for i in range(len(seq) - k + 1):
        gram = seq[i:i + k]
        if gram in seen:
            repeated.add(gram)
        else:
            seen[gram] = i
    return {g: i for g, i in seen.items() if g not in repeated}


def _chain(pairs):
    """The longest run of pairs increasing in both - patience diff's LIS."""
    tails, ends, back = [], [], [None] * len(pairs)
    for n, (_i, j) in enumerate(pairs):
        p = bisect.bisect_left(tails, j)
        if p == len(tails):
            tails.append(j)
            ends.append(n)
        else:
            tails[p], ends[p] = j, n
        back[n] = ends[p - 1] if p else None
    out, n = [], ends[-1] if ends else None
    while n is not None:
        out.append(pairs[n])
        n = back[n]
    return out[::-1]


def _align(a, b, lo_a, hi_a, lo_b, hi_b, k, out):
    """Pairs (i, j) of a[lo_a:hi_a] and b[lo_b:hi_b] that line up."""
    if hi_a <= lo_a or hi_b <= lo_b:
        return
    if (hi_a - lo_a) * (hi_b - lo_b) <= GAP_CELLS or k < 4:
        if (hi_a - lo_a) * (hi_b - lo_b) > GAP_CELLS * 4:
            return
        matcher = difflib.SequenceMatcher(None, a[lo_a:hi_a], b[lo_b:hi_b],
                                          autojunk=False)
        for i, j, size in matcher.get_matching_blocks():
            out.extend((lo_a + i + t, lo_b + j + t) for t in range(size))
        return
    ua = _unique(a[lo_a:hi_a], k)
    ub = _unique(b[lo_b:hi_b], k)
    pairs = sorted((lo_a + i, lo_b + ub[g]) for g, i in ua.items() if g in ub)
    last_i, last_j = lo_a, lo_b
    for i, j in _chain(pairs):
        if i < last_i or j < last_j:
            continue
        _align(a, b, last_i, i, last_j, j, k // 2, out)
        out.extend((i + t, j + t) for t in range(k))
        last_i, last_j = i + k, j + k
    _align(a, b, last_i, hi_a, last_j, hi_b, k // 2, out)


def align(a_words, b_words):
    """[(i, j)] - word i of the first image is word j of the second."""
    a = tuple(_mask(w) for w in a_words)
    b = tuple(_mask(w) for w in b_words)
    out = []
    _align(a, b, 0, len(a), 0, len(b), ANCHOR, out)
    return out


def _runs(pairs, base_a, base_b, keep=()):
    """[[address in a, address in b, words]] out of word pairs, chance
    matches dropped - bar those starting at an index in `keep`."""
    runs = []
    for i, j in sorted(pairs):
        if runs and i == runs[-1][0] + runs[-1][2] and j == runs[-1][1] + runs[-1][2]:
            runs[-1][2] += 1
        else:
            runs.append([i, j, 1])
    kept = []
    for n, (i, j, size) in enumerate(runs):
        if size < SHORT_RUN and i not in keep:
            shift = j - i
            near = [r[1] - r[0] for r in runs[max(0, n - 1):n] + runs[n + 1:n + 2]]
            if shift not in near:
                continue
        kept.append([base_a + i * 4, base_b + j * 4, size])
    return kept


def references(words, base):
    """{word index: address} for every instruction that finishes a
    lui-built address - the addiu, ori, load or store after the lui."""
    hi = {}
    out = {}
    for n, w in enumerate(words):
        op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        if op == 0x0F:                                  # lui
            hi[rt] = (w << 16 & 0xFFFFFFFF, n)
            continue
        if op in (0x09, 0x0D, 0x08) or 0x20 <= op <= 0x2E or op in (0x32, 0x3A):
            if rs in hi and n - hi[rs][1] <= HI_REACH:
                imm = w & 0xFFFF
                if op != 0x0D and imm & 0x8000:
                    imm -= 0x10000
                out[n] = (hi[rs][0] + imm) & 0xFFFFFFFF
            # A load or an addiu/ori overwrites rt; a store only reads it.
            if not (0x28 <= op <= 0x2E or op == 0x3A):
                hi.pop(rt, None)
        elif op == 0:
            hi.pop((w >> 11) & 31, None)
        elif op in (0x0A, 0x0B, 0x0C, 0x0E):
            hi.pop(rt, None)
        elif op == 0x12 and rs in (0, 2):                # mfc2/cfc2
            hi.pop(rt, None)
        elif op == 3:
            hi.pop(31, None)
    return out


def _pointer(word):
    return RAM_LOW + 0x10000 <= word < RAM_HIGH


def _data_votes(pairs, a_words, b_words, base_a, base_b, votes):
    """Add (US address, build address) votes from lined-up code that builds
    an address, and from lined-up pointer words in data."""
    ra, rb = references(a_words, base_a), references(b_words, base_b)
    for i, j in pairs:
        x, y = ra.get(i), rb.get(j)
        if x is not None and y is not None:
            votes[x][y] += 1
        elif _pointer(a_words[i]) and _pointer(b_words[j]):
            votes[a_words[i]][b_words[j]] += 1


def _ranges(pairs):
    """[[US low, US high, shift]] - sorted (US, build) pairs as runs of one
    shift."""
    out = []
    for us, there in sorted(pairs):
        shift = there - us
        if out and out[-1][2] == shift:
            out[-1][1] = us
        else:
            out.append([us, us, shift])
    return out


# --- a build ----------------------------------------------------------

class Build:
    """One build's addresses in terms of US retail's."""

    def __init__(self, name, label, exe_base=0x80010000,
                 overlay_base=US_OVERLAY_BASE, area_base=US_AREA_BASE,
                 images=None, slots=None):
        self.name, self.label = name, label
        self.exe_base = exe_base
        self.overlay_base = overlay_base
        self.area_base = area_base
        # {US slot: this build's}, where the code says; the rest are the same.
        self._slots = {int(k): v for k, v in (slots or {}).items()}
        # image -> ([code run starts], runs, [data range starts], ranges)
        self._images = {}
        # image -> US routine starts this build has no routine for.
        self._missing = {}
        for image, table in (images or {}).items():
            code = sorted(table.get("code", ()))
            data = sorted(table.get("data", ()))
            self._images[image] = ([c[0] for c in code], code,
                                   [d[0] for d in data], data)
            self._missing[image] = frozenset(table.get("unsettled", ()))
        self._back = None

    @property
    def identity(self):
        return not self._images

    @property
    def japanese(self):
        """Whether its MAIN.EXE's strings are Shift-JIS."""
        return self.name.startswith("jp-")

    @property
    def demo(self):
        """Whether it is one of the two demo discs - an older engine, which
        numbers the file table its own way and keeps a longer reward
        record than the full builds."""
        return self.name.endswith("-demo")

    @property
    def same_layout(self):
        """Whether its areas keep US retail's files in US retail's slots -
        every build but the demos, whose file tables are numbered anew."""
        return not self._slots

    def __repr__(self):
        return f"Build({self.name!r})"

    # -- US -> this build -------------------------------------------

    def main(self, address):
        """A US MAIN.EXE address - code, data, BSS or scratchpad - here."""
        return self._translate(MAIN, address)

    def overlay(self, image, address):
        """A US address inside overlay `image` ("A04", "SOP") here."""
        return self._translate(image, address)

    def address(self, address, image=None):
        """Either: overlay addresses need their image, the rest are MAIN's."""
        if US_OVERLAY_BASE <= address < US_AREA_BASE:
            if not image or image == MAIN:
                raise ValueError(f"0x{address:08X} is an overlay address - which one?")
            return self.overlay(image, address)
        return self.main(address)

    def _translate(self, image, address):
        if self.identity:
            return address
        if address == US_OVERLAY_BASE:
            return self.overlay_base
        if address == US_AREA_BASE:
            return self.area_base
        table = self._images.get(image)
        if table is None or address in self._missing[image]:
            return None
        starts, runs, dstarts, ranges = table
        n = bisect.bisect_right(starts, address) - 1
        if n >= 0:
            us, there, size = runs[n]
            if address < us + size * 4:
                return there + (address - us)
        n = bisect.bisect_right(dstarts, address) - 1
        if n >= 0 and address <= ranges[n][1]:
            return address + ranges[n][2]
        # Nothing names this address itself: it takes the shift of what
        # sits nearest before it - a field of the variable a lui names, a
        # word of a routine whose run was split off.
        best = None
        if n >= 0 and address - ranges[n][1] <= DATA_REACH:
            best = (address - ranges[n][1], ranges[n][2])
        n = bisect.bisect_right(starts, address) - 1
        if n >= 0:
            us, there, size = runs[n]
            gap = address - (us + size * 4 - 4)
            if gap <= DATA_REACH and (best is None or gap < best[0]):
                best = (gap, there - us)
        if best is None:
            return None
        return address + best[1]

    def slot(self, us_slot):
        """The file-table slot this build keeps US retail's `us_slot` in."""
        return self._slots.get(us_slot, us_slot)

    # -- this build -> US -------------------------------------------

    def us(self, address, image=MAIN):
        """Back: an address here, in `image`, as US retail has it."""
        if self.identity:
            return address
        if address == self.overlay_base:
            return US_OVERLAY_BASE
        if address == self.area_base:
            return US_AREA_BASE
        if self._back is None:
            self._back = {}
            for name, (_s, runs, _d, ranges) in self._images.items():
                back = sorted((there, us, size) for us, there, size in runs)
                data = sorted((lo + shift, hi + shift, -shift) for lo, hi, shift in ranges)
                self._back[name] = ([b[0] for b in back], back,
                                    [d[0] for d in data], data)
        table = self._back.get(image)
        if table is None:
            return None
        starts, runs, dstarts, ranges = table
        n = bisect.bisect_right(starts, address) - 1
        if n >= 0:
            there, us, size = runs[n]
            if address < there + size * 4:
                return us + (address - there)
        n = bisect.bisect_right(dstarts, address) - 1
        if n >= 0 and address <= ranges[n][1]:
            return address + ranges[n][2]
        return None


US = Build(REFERENCE, LABELS[REFERENCE])
_LOADED = {}


def _load_maps():
    if _LOADED:
        return _LOADED
    _LOADED[None] = None
    if not os.path.isdir(MAPS):
        return _LOADED
    for name in sorted(os.listdir(MAPS)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(MAPS, name), encoding="utf-8") as f:
                table = json.load(f)
        except (OSError, ValueError):
            continue
        _LOADED[table["header"]] = table
    return _LOADED


def _from_table(table):
    images = dict(table["images"])
    return Build(table["build"], table.get("label", table["build"]),
                 table["exe_base"], table["overlay_base"], table["area_base"],
                 images, table.get("slots"))


_BUILDS = {}


def identify(exe_path):
    """The Build a MAIN.EXE is, US retail when it is that or unknown."""
    if not exe_path:
        return US
    try:
        with open(exe_path, "rb") as f:
            head = f.read(EXE_HEADER)
    except OSError:
        return US
    digest = header_digest(head)
    if digest in _BUILDS:
        return _BUILDS[digest]
    table = _load_maps().get(digest)
    build = _from_table(table) if table else US
    _BUILDS[digest] = build
    return build


# --- the build everything is using -------------------------------------

_current = US
_listeners = []


def current():
    return _current


def on_change(listener):
    """Call `listener(build)` now and whenever another build is put in use."""
    _listeners.append(listener)
    listener(_current)


def slot(us_slot):
    """The open build's file-table slot for US retail's `us_slot`."""
    return _current.slot(us_slot)


def overlay_offset(image, address):
    """Where US address `address` in overlay `image` is in the open build's
    copy of that file, or None if the build has nothing there."""
    build = _current
    there = build.overlay(image, address)
    return None if not there else there - build.overlay_base


def use(exe_path):
    """Put the build `exe_path` is in force for every module that listens.
    Cheap to call again with the same file."""
    global _current
    build = identify(exe_path)
    if build is not _current:
        _current = build
        for listener in _listeners:
            listener(build)
    return build


class Addresses:
    """A module's US addresses, rebound in its globals for the build in use.

        _BUILD = game_build.Addresses(globals(),
            main=("PLAYER", "FILE_TABLE"), overlay={"KUJARA_GHOST": "A04"})

    `main` names hold a MAIN.EXE address or a tuple/frozenset/dict of them;
    `overlay` maps a name to its image; `per_area` names a dict keyed by area
    number, each value in overlay A0<area>; `slots` names hold a file-table
    slot (an SDAT id). A name whose address a build
    does not have becomes 0 - never equal to anything the code compares it
    with - rather than the US value, which could be something else there."""

    def __init__(self, module_globals, main=(), overlay=None, per_area=(),
                 slots=()):
        self.globals = module_globals
        self.main = tuple(main)
        self.overlay = dict(overlay or {})
        self.per_area = tuple(per_area)
        self.slots = tuple(slots)
        self.us = {name: module_globals[name]
                   for name in (*self.main, *self.overlay, *self.per_area,
                                *self.slots)}
        on_change(self.rebind)

    def rebind(self, build):
        for name, value in self.us.items():
            if name in self.slots:
                self.globals[name] = build.slot(value)
            elif name in self.main:
                self.globals[name] = _each(value, build.main)
            elif name in self.overlay:
                image = self.overlay[name]
                self.globals[name] = _each(
                    value, lambda a, image=image: build.overlay(image, a))
            else:
                # {area number: address or tuple}, each in its own overlay.
                self.globals[name] = {
                    area: _each(v, lambda a, area=area: build.overlay(
                        area_image(area), a))
                    for area, v in value.items()}


def area_image(area):
    return f"A0{'0123456789ABCDEFGHIJKL'[area]}"


def _each(value, translate):
    if isinstance(value, int):
        # A count or a variant riding along in a tuple is not an address.
        if value < SCRATCH_LOW:
            return value
        return translate(value) or 0
    if isinstance(value, tuple):
        return tuple(_each(v, translate) for v in value)
    if isinstance(value, frozenset):
        return frozenset(_each(v, translate) for v in value)
    if isinstance(value, dict):
        return {k: _each(v, translate) for k, v in value.items()}
    return value


# --- making a map -------------------------------------------------------

def _read(path):
    with open(path, "rb") as f:
        return f.read()


def overlay_base(data, low=0x800F0000, high=0x80120000):
    """Where an overlay is loaded: the base that lands most of its own jal
    targets on a routine's opening `addiu sp, sp, -n`."""
    words = _words(data)
    prologues = [n * 4 for n, w in enumerate(words)
                 if w >> 16 == 0x27BD and w & 0x8000]
    targets = collections.Counter(0x80000000 | (w & 0x3FFFFFF) << 2
                                  for w in words if w >> 26 == 3)
    votes = collections.Counter()
    for target, count in targets.items():
        for at in prologues:
            if low <= target - at <= high:
                votes[target - at] += count
    return votes.most_common(1)[0][0] if votes else None


def _bins(folder):
    for base in (os.path.join(folder, "BIN"), folder):
        if os.path.isdir(base):
            return base
    return folder


def _targets(images, us=True, pointers=False):
    """{image: routine starts its callers name} - a jal (or, with
    `pointers`, a pointer word) in an overlay names a routine of that
    overlay or of MAIN.EXE, never of another overlay: they all load at the
    same address."""
    out = {name: set() for name in images}
    for name, image in images.items():
        words, base = (image.a, image.us_base) if us else (image.b, image.base)
        for w in words:
            if w >> 26 == 3:
                t = _jal(w, base)
            elif pointers and _pointer(w) and not w & 3:
                t = w
            else:
                continue
            out[name if name != MAIN and image.holds(t, us) else MAIN].add(t)
    return out


def _unsettled(images, anchors=None):
    """{image: [US routine starts]} that land in the middle of one of the
    build's routines - it has no such routine, or none this could find -
    so that translating one gives nothing rather than a guess."""
    anchors = anchors or {}
    us_targets = _targets(images)
    # A routine only ever called through a table starts where it points.
    targets = _targets(images, us=False, pointers=True)
    for name, known in anchors.items():
        if name in images:
            us_targets[name].update(known)
            targets[name].update(known.values())
    out = {}
    for name, image in images.items():
        starts = set(_starts(image.b, image.base, targets[name]))
        paired = sorted(image.pairs)
        bad = []
        for ta in _starts(image.a, image.us_base, us_targets[name]):
            i0 = (ta - image.us_base) // 4
            j = image.pairs.get(i0)
            if j is None:
                n = bisect.bisect_right(paired, i0) - 1
                if n < 0:
                    continue
                j = image.pairs[paired[n]] + i0 - paired[n]
            if image.base + j * 4 not in starts:
                bad.append(ta)
        if bad:
            out[name] = bad
    return out


class _Image:
    """One image of both builds, and the word pairs lining them up."""

    def __init__(self, us_words, us_base, words, base):
        self.a, self.us_base = us_words, us_base
        self.b, self.base = words, base
        self.pairs = dict(align(us_words, words))
        # Routine starts settled by _realign_routines, kept however short.
        self.starts = set()

    def holds(self, address, us=True):
        base, words = (self.us_base, self.a) if us else (self.base, self.b)
        return base <= address < base + len(words) * 4


def _starts(words, base, targets):
    """Routine starts: called ones, and a frame opened right after a return."""
    found = {t for t in targets if base <= t < base + len(words) * 4 and not t & 3}
    for n in range(2, len(words)):
        if (words[n] >> 16 == 0x27BD and words[n] & 0x8000
                and JR_RA in (words[n - 2], words[n - 3] if n >= 3 else 0)):
            found.add(base + n * 4)
    return sorted(found)


JR_RA = 0x03E00008
# Rounds of routine realignment.
CALL_ROUNDS = 3
# How many of a routine's first words say which candidate start is it, how
# alike they must be at all, and by how much a new start must beat the one
# the whole-image pass gave.
BODY = 48
ALIKE = 0.5
BETTER = 0.1
# A decomp's own name for a routine is evidence of its own - its callers
# were matched by name - so its start needs less likeness.
NAMED_ALIKE = 0.25
# Routine starts either side of a mid-routine landing tried in its place.
NEIGHBOURS = 2


def _jal(word, at):
    return (at & 0xF0000000) | (word & 0x3FFFFFF) << 2


def _next(starts, address, end):
    n = bisect.bisect_right(starts, address)
    return starts[n] if n < len(starts) else end


def _alike(image, i0, i1, j0, j1):
    a = [_mask(w) for w in image.a[i0:min(i1, i0 + BODY)]]
    b = [_mask(w) for w in image.b[j0:min(j1, j0 + BODY)]]
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _realign_routines(images, anchors=None):
    """Where each routine starts in the build, settled one routine at a time.

    Candidates are where the whole-image pass put it, where its lined-up
    callers' jals go, and a decomp's own address for it (`anchors`, {image:
    {US address: address}}); the one whose first words are most like US
    retail's wins. A routine that changed too much for the whole-image pass
    is lined up again from there."""
    anchors = anchors or {}
    for _round in range(CALL_ROUNDS):
        votes = {name: collections.defaultdict(collections.Counter) for name in images}
        us_targets, targets = _targets(images), _targets(images, us=False)
        for name, image in images.items():
            for i, j in image.pairs.items():
                wa, wb = image.a[i], image.b[j]
                if wa >> 26 == 3 and wb >> 26 == 3:
                    ta = _jal(wa, image.us_base + i * 4)
                    tb = _jal(wb, image.base + j * 4)
                elif _pointer(wa) and _pointer(wb) and not (wa | wb) & 3:
                    # A handler table: a routine only ever called through it.
                    ta, tb = wa, wb
                else:
                    continue
                owner = name if image.holds(ta) and name != MAIN else MAIN
                if images[owner].holds(ta) and images[owner].holds(tb, us=False):
                    votes[owner][ta][tb] += 1
        named = set()
        for name, known in anchors.items():
            for ta, tb in known.items():
                if name in images:
                    votes[name][ta][tb] += 1
                    named.add((name, ta, tb))
                    us_targets[name].add(ta)
                    targets[name].add(tb)
        changed = 0
        for name, image in images.items():
            us_starts = _starts(image.a, image.us_base, us_targets[name])
            starts = _starts(image.b, image.base, targets[name])
            us_end = image.us_base + len(image.a) * 4
            end = image.base + len(image.b) * 4
            reverse = {j: i for i, j in image.pairs.items()}
            # A routine lined up with the middle of one: the starts either
            # side of where it landed are candidates too.
            begun = set(starts)
            paired = sorted(image.pairs)
            for ta in us_starts:
                i0 = (ta - image.us_base) // 4
                now = image.pairs.get(i0)
                if now is None:
                    # Unpaired: where the pair before it would put it.
                    n = bisect.bisect_right(paired, i0) - 1
                    if n < 0:
                        continue
                    now = image.pairs[paired[n]] + i0 - paired[n]
                if image.base + now * 4 in begun:
                    continue
                n = bisect.bisect_right(starts, image.base + now * 4)
                for tb in starts[max(0, n - NEIGHBOURS):n + NEIGHBOURS]:
                    votes[name][ta][tb] += 0
            us_begun = set(us_starts)
            for ta, counter in votes[name].items():
                # Only routines: a pointer into data is no routine's start.
                if ta not in us_begun:
                    continue
                i0 = (ta - image.us_base) // 4
                now = image.pairs.get(i0)
                candidates = {(tb - image.base) // 4 for tb in counter
                              if tb in begun} - {now}
                if not candidates:
                    continue
                i1 = (_next(us_starts, ta, us_end) - image.us_base) // 4

                def score(j0):
                    j1 = (_next(starts, image.base + j0 * 4, end) - image.base) // 4
                    return _alike(image, i0, i1, j0, j1), j1
                best = max(candidates, key=lambda j: (score(j)[0], counter[image.base + j * 4]))
                alike, j1 = score(best)
                enough = (NAMED_ALIKE if (name, ta, image.base + best * 4) in named
                          else ALIKE)
                if alike < enough or (now is not None and alike < score(now)[0] + BETTER):
                    continue
                j0 = best
                if (i1 - i0) * (j1 - j0) > GAP_CELLS or i1 <= i0 or j1 <= j0:
                    continue
                for i in range(i0, i1):
                    j = image.pairs.pop(i, None)
                    if j is not None:
                        reverse.pop(j, None)
                for j in range(j0, j1):
                    i = reverse.pop(j, None)
                    if i is not None:
                        image.pairs.pop(i, None)
                matcher = difflib.SequenceMatcher(
                    None, [_mask(w) for w in image.a[i0:i1]],
                    [_mask(w) for w in image.b[j0:j1]], autojunk=False)
                for i, j, size in matcher.get_matching_blocks():
                    for t in range(size):
                        image.pairs[i0 + i + t] = j0 + j + t
                        reverse[j0 + j + t] = i0 + i + t
                # The start is settled even where its first words differ.
                if image.pairs.get(i0) != j0:
                    reverse.pop(image.pairs.get(i0), None)
                    stale = reverse.pop(j0, None)
                    if stale is not None:
                        image.pairs.pop(stale, None)
                    image.pairs[i0], reverse[j0] = j0, i0
                image.starts.add(i0)
                changed += 1
        if not changed:
            break


def anchors_from_symbols(us_symbols, symbols):
    """{image: {US address: address}} for every routine two decomps both
    name ({name: (image, address)}, as game/decomp_symbols.py makes)."""
    out = collections.defaultdict(dict)
    for name, (image, address) in symbols.items():
        if name.startswith("FUN_") or name not in us_symbols:
            continue
        us_image, us_address = us_symbols[name]
        if us_image == image:
            out[image][us_address] = address
    return dict(out)


def generate(reference_dir, build_dir, name, out_dir=MAPS, anchors=None):
    """Line a build up with US retail and write decomp/builds/<name>.json.

    Both folders are extracted discs: MAIN.EXE at the top, the overlays in
    BIN/. `anchors` ({image: {US address: address}}) are routine starts a
    decomp of the build names - candidates, weighed like the rest."""
    us_exe = _read(os.path.join(reference_dir, "MAIN.EXE"))
    exe = _read(os.path.join(build_dir, "MAIN.EXE"))
    us_base = struct.unpack_from("<I", us_exe, T_ADDR)[0]
    base = struct.unpack_from("<I", exe, T_ADDR)[0]
    images = {MAIN: _Image(_words(us_exe[EXE_HEADER:]), us_base,
                           _words(exe[EXE_HEADER:]), base)}

    us_bins, bins = _bins(reference_dir), _bins(build_dir)
    # The overlay base is in no header: the overlay's own calls say it.
    first = os.path.join(bins, OVERLAYS[0] + ".BIN")
    loaded = overlay_base(_read(first)) if os.path.exists(first) else None
    if loaded is None:
        loaded = US_OVERLAY_BASE
    for image in OVERLAYS:
        us_path = os.path.join(us_bins, image + ".BIN")
        path = os.path.join(bins, image + ".BIN")
        if os.path.exists(us_path) and os.path.exists(path):
            images[image] = _Image(_words(_read(us_path)), US_OVERLAY_BASE,
                                   _words(_read(path)), loaded)
    _realign_routines(images, anchors)
    unsettled = _unsettled(images, anchors)

    tables = {}
    main_votes = collections.defaultdict(collections.Counter)
    overlay_votes = {}
    sizes = {}
    for image, lined in images.items():
        pairs = sorted(lined.pairs.items())
        tables[image] = {"code": _runs(pairs, lined.us_base, lined.base, lined.starts)}
        if image in unsettled:
            tables[image]["unsettled"] = unsettled[image]
        votes = collections.defaultdict(collections.Counter)
        _data_votes(pairs, lined.a, lined.b, lined.us_base, lined.base, votes)
        if image == MAIN:
            for us, there in votes.items():
                main_votes[us].update(there)
            continue
        sizes[image] = len(lined.a) * 4
        overlay_votes[image] = votes
        # What an overlay names below the overlay base is MAIN's.
        for us, there in votes.items():
            if us < US_OVERLAY_BASE or us >= US_AREA_BASE:
                main_votes[us].update(there)
    images = tables

    def settle(votes, keep):
        return _ranges((us, there.most_common(1)[0][0])
                       for us, there in votes.items() if keep(us))

    in_main = lambda us: ((RAM_LOW <= us < US_OVERLAY_BASE or us >= US_AREA_BASE)
                          and us < RAM_HIGH or SCRATCH_LOW <= us < SCRATCH_HIGH)
    images[MAIN]["data"] = settle(main_votes, in_main)
    area_votes = main_votes.get(US_AREA_BASE)
    area_base = area_votes.most_common(1)[0][0] if area_votes else US_AREA_BASE
    # A slot is whatever the code that loads US retail's slot loads here,
    # counted from the table's first slot - read inside a run of references
    # of one shift, never off the nearest one.
    slots = {}
    ranges = images[MAIN]["data"]
    lows = [r[0] for r in ranges]

    def exact(us):
        n = bisect.bisect_right(lows, us) - 1
        return us + ranges[n][2] if n >= 0 and us <= ranges[n][1] else None
    file_table = exact(US_FILE_TABLE)
    if file_table is not None:
        for us_slot in range(FILE_SLOTS):
            there = exact(US_FILE_TABLE + us_slot * 4)
            if there is None:
                continue
            offset = there - file_table
            if offset % 4 == 0 and 0 <= offset // 4 < FILE_SLOTS and offset // 4 != us_slot:
                slots[us_slot] = offset // 4
    table = {
        "build": name,
        "label": LABELS.get(name, name),
        "header": header_digest(exe),
        "exe_base": base,
        "overlay_base": loaded,
        "area_base": area_base,
        "slots": slots,
        "images": images,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(table, f, separators=(",", ":"))
    _LOADED.clear()
    _BUILDS.clear()
    return table


def decomp_anchors(decomp_path, build_dir, name, out_dir=None):
    """Routine starts an annotated decomp of the build names, as anchors for
    generate(): its names are placed on the build's own code (game/
    decomp_symbols.py) and kept as decomp/symbols_<name>.json, then paired
    with US retail's (decomp/symbols_us.json)."""
    from game import decomp_symbols
    folder = out_dir or os.path.dirname(MAPS)
    exe = os.path.join(build_dir, "MAIN.EXE")
    bins = _bins(build_dir)
    first = os.path.join(bins, OVERLAYS[0] + ".BIN")
    base = overlay_base(_read(first)) if os.path.exists(first) else US_OVERLAY_BASE
    symbols = decomp_symbols.resolve(decomp_path, exe, bins, overlay_base=base)
    with open(os.path.join(folder, f"symbols_{name}.json"), "w") as f:
        json.dump(symbols, f, indent=0, sort_keys=True)
    with open(os.path.join(folder, "symbols_us.json")) as f:
        us_symbols = {k: tuple(v) for k, v in json.load(f).items()}
    return anchors_from_symbols(us_symbols, symbols)


if __name__ == "__main__":
    # python -m game.game_build <US retail folder> <name>=<folder>[,<decomp.c>] ...
    #
    # Each folder is an extracted disc (MAIN.EXE, BIN/). A build with an
    # annotated decomp of its own - the JP demo's, decomp/
    # MAIN.EXE_JPDEMO_DECOMP.c - gives it after a comma: its routine names
    # are weighed with the rest (see _realign_routines).
    reference = sys.argv[1]
    for arg in sys.argv[2:]:
        name, folder = arg.split("=", 1)
        folder, _comma, decomp = folder.partition(",")
        anchors = decomp_anchors(decomp, folder, name) if decomp else None
        t = generate(reference, folder, name, anchors=anchors)
        print(f"{name}: overlay base 0x{t['overlay_base']:08X}, area base "
              f"0x{t['area_base']:08X}, {sum(len(i['code']) for i in t['images'].values())} "
              f"code runs, {sum(len(i.get('data', ())) for i in t['images'].values())} data ranges, "
              f"{sum(len(i.get('unsettled', ())) for i in t['images'].values())} routines missing")

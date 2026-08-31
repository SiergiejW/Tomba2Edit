"""Playing the voice that belongs to a line of text.

Three things decide which audio a TXTD entry speaks, and all of them are
worked out rather than configured:

    which table   a master's lines run through one of the overlay's clip
                  tables, and the table has exactly as many entries as
                  the master has clips - which identifies it outright
    which clip    the entry's `extra` low byte is its FIRST clip, and
                  every {$END} segment after the first takes the next
                  index along, so an entry of three segments speaks
                  clips lo, lo+1 and lo+2
    which channel the table's own block boundaries fall in the gaps
                  between spoken lines only on the channel it describes

The segment rule is what makes an entry with several boxes play all of
them instead of just the first, and it is also why the indices in a
master skip: the gaps are the extra segments.
"""
import json
import os

from functions import voice, xa

NO_VOICE = 0xFFFF
GAP = 0.25          # seconds of silence inserted between an entry's boxes

# Working out an overlay's channels means decoding all 32 of them across
# the whole span its tables cover - the better part of a minute. It only
# depends on the overlay and the disc, so it is done once and kept.
CACHE_NAME = "voicechannels.json"


def segments(text):
    """How many boxes a TXTD entry shows - one clip each."""
    return max((text or "").count("{$END}"), 1)


def clips_needed(master):
    """How many clips a master's lines use in total.

    This is what names its table: a master using N clips runs through
    the table with N entries."""
    most = 0
    for entry in master.get("entries", ()):
        extra = entry.get("extra")
        if extra in (None, NO_VOICE):
            continue
        most = max(most, (extra & 0xFF) + segments(entry.get("text")))
    return most


def _align(needs, sizes):
    """Longest order-preserving match of [(master, clips)] to table
    sizes, matching only on equality. Either side may be skipped."""
    n, m = len(needs), len(sizes)
    if not n or not m:
        return {}
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = 1 + best[i + 1][j + 1] if needs[i][1] == sizes[j] else 0
            best[i][j] = max(best[i + 1][j], best[i][j + 1], take)
    out = {}
    i = j = 0
    while i < n and j < m:
        if needs[i][1] == sizes[j] and best[i][j] == 1 + best[i + 1][j + 1]:
            out[needs[i][0]] = j
            i += 1
            j += 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


class VoiceLink:
    """Resolves a TXTD entry to its clips, and decodes them."""

    def __init__(self):
        self.image = None
        self.lba = 0
        self.sectors = 0
        self.tables = []
        self.overlay = None
        self._channels = {}
        self._by_master = {}

    def ready(self):
        return bool(self.image and self.tables)

    def set_image(self, path):
        """Point at the disc's data track. Returns an error string, or
        None when it worked."""
        try:
            self.lba, self.sectors = voice.find_track(path)
        except Exception as exc:
            self.image = None
            return str(exc)
        self.image = path
        self._channels.clear()
        return None

    def set_overlay(self, path):
        """Point at the area's Axx.BIN and read its voice dispatch.

        The overlay chooses a master's clip table and channel with a jump
        table in its own code, so both come straight out of it - no
        probing, no cache, and it covers masters whose table size matches
        nothing."""
        self.overlay = path
        self.dispatch = {}
        try:
            if path:
                self.dispatch = voice.read_dispatch(path)
        except Exception:
            self.dispatch = {}
        self.tables = {}
        self.default_table = None
        for master, (table_at, channel, block_offset) in self.dispatch.items():
            rows = voice.read_clip_table(path, table_at)
            if rows:
                # Every start in the table is relative to the master's
                # own base block; A00 happens to use 0 throughout, which
                # is why it worked before this was read.
                rows = [(start + block_offset, length) for start, length in rows]
                if master == -1:
                    # the case every master without one of its own uses
                    self.default_table = (rows, channel)
                else:
                    self.tables[master] = (rows, channel)
        return len(self.tables)

    def set_masters(self, masters):   # kept for callers; nothing to do
        return len(self.tables)

    def _unused_set_masters(self, masters):
        """Work out which table each master speaks through.

        The overlay's tables are in the same order as the masters that
        use them, and a table has exactly as many entries as its master
        has clips. Matching the two sequences in order - allowing either
        side to skip, since some masters have no voice and some tables
        go unused - pins nearly all of them: 112 of the 120 tables
        across the disc's areas, and it reproduces the assignment proved
        against savestates for AREA_04.

        A master that comes out unmatched is left with no voice rather
        than given the nearest table, which would only play some other
        conversation."""
        self._by_master = {}
        if not masters or not self.tables:
            return 0
        sizes = [len(entries) for _off, entries in self.tables]
        needs = [(i, clips_needed(m)) for i, m in enumerate(masters)]
        needs = [(i, n) for i, n in needs if n]
        self._by_master = _align(needs, sizes)
        return len(self._by_master)

    def table_for_index(self, master_index):
        if master_index in self.tables or self.default_table:
            return master_index
        return None

    # --- channels -----------------------------------------------------

    def _cache_key(self):
        return f"{os.path.basename(self.overlay or '')}:{len(self.tables)}"

    def _cache_path(self):
        return os.path.join(os.path.dirname(self.image or ""), CACHE_NAME)

    def load_cached_channels(self):
        """Take a previous run's answer if there is one for this overlay."""
        try:
            with open(self._cache_path(), "r", encoding="utf-8") as f:
                found = json.load(f).get(self._cache_key())
        except Exception:
            return False
        if not found or len(found) != len(self.tables):
            return False
        self._channels = {i: c for i, c in enumerate(found)}
        return True

    def resolve_channels(self, progress=None):
        """Work out every table's channel. Slow - run it off the GUI
        thread - and cached afterwards, so it happens once per disc."""
        if not self.ready():
            return False
        found = voice.resolve_channels(self.image, self.lba, self.tables,
                                       progress, self.sectors)
        self._channels = {i: c for i, c in enumerate(found)}
        try:
            path = self._cache_path()
            store = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    store = json.load(f)
            store[self._cache_key()] = found
            with open(path, "w", encoding="utf-8") as f:
                json.dump(store, f, indent=2)
        except OSError:
            pass
        return True

    def channels_known(self):
        return bool(self.tables)

    def channel(self, table_index):
        return self._channels.get(table_index)

    def clip_for(self, entry, master_index):
        """(samples, rate, note) for one TXTD entry, all its boxes.

        Every {$END} segment gets its own clip; they are joined with a
        short gap so an entry plays the way it reads."""
        extra = entry.get("extra")
        if extra is None or extra == NO_VOICE:
            return None, 0, "This line has no voice."
        if not self.ready():
            return None, 0, ("No disc yet - open the data track (Track 1), "
                             "the only place the voice survives.")
        found = self.tables.get(master_index) or self.default_table
        if found is None:
            return None, 0, ("This overlay's dispatch has no voice for this "
                             "master.")
        entries, channel = found
        first = extra & 0xFF
        count = segments(entry.get("text"))
        rate = 18900
        samples = []
        played = []
        frame = xa.framing(self.image) or xa.RAW
        with open(self.image, "rb") as f:
            for n in range(count):
                index = first + n
                if index >= len(entries):
                    break
                block = xa.decode_channel(
                    f, self.lba,
                    voice.clip_sectors(entries[index], channel, self.sectors),
                    frame=frame)
                if samples:
                    samples.extend([0] * int(GAP * block[1]))
                samples.extend(block[0])
                rate = block[1]
                played.append(index)
        if not played:
            return None, 0, (f"Clip {first} is past the end of this master's "
                             f"table ({len(entries)} entries).")
        which = (f"clip {played[0]}" if len(played) == 1
                 else f"clips {played[0]}-{played[-1]}")
        return samples, rate, (
            f"master {master_index}, channel {channel}, {which} "
            f"({count} box{'es' if count > 1 else ''}) - "
            f"{len(samples) / rate:.2f}s")

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


class VoiceLink:
    """Resolves a TXTD entry to its clips, and decodes them."""

    def __init__(self):
        self.image = None
        self.lba = 0
        self.sectors = 0
        self.tables = []
        self.overlay = None
        self._channels = {}

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
        """Point at the area's Axx.BIN and find its clip tables."""
        try:
            self.tables = voice.find_tables(path) if path else []
        except Exception:
            self.tables = []
        self.overlay = path
        self._channels.clear()
        if self.image:
            self.load_cached_channels()
        return len(self.tables)

    def table_for(self, master):
        """Which table a master's lines run through, by clip count.

        An exact match is the answer. Failing that the smallest table
        with room is used, which covers the short masters that have too
        few lines for their count to name a table on its own."""
        need = clips_needed(master)
        if not need or not self.tables:
            return None
        sizes = [(len(entries), i) for i, (_off, entries) in
                 enumerate(self.tables)]
        for size, i in sizes:
            if size == need:
                return i
        roomy = sorted((s, i) for s, i in sizes if s >= need)
        return roomy[0][1] if roomy else None

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
                                       progress)
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
        return len(self._channels) == len(self.tables) and bool(self.tables)

    def channel(self, table_index):
        return self._channels.get(table_index)

    def clip_for(self, entry, master):
        """(samples, rate, note) for one TXTD entry, all its boxes.

        Every {$END} segment gets its own clip; they are joined with a
        short gap so an entry plays the way it reads."""
        extra = entry.get("extra")
        if extra is None or extra == NO_VOICE:
            return None, 0, "This line has no voice."
        if not self.ready():
            return None, 0, ("No disc yet - open the data track (Track 1), "
                             "the only place the voice survives.")
        table_index = self.table_for(master)
        if table_index is None:
            return None, 0, "No clip table in this overlay fits this master."
        _off, entries = self.tables[table_index]
        channel = self.channel(table_index)
        if channel is None:
            return None, 0, ("Still working out which channel each table "
                             "uses - this happens once per disc.")

        first = extra & 0xFF
        count = segments(entry.get("text"))
        rate = 18900
        samples = []
        played = []
        with open(self.image, "rb") as f:
            for n in range(count):
                index = first + n
                if index >= len(entries):
                    break
                block = xa.decode_channel(
                    f, self.lba, voice.clip_sectors(entries[index], channel))
                if samples:
                    samples.extend([0] * int(GAP * block[1]))
                samples.extend(block[0])
                rate = block[1]
                played.append(index)
        if not played:
            return None, 0, (f"Clip {first} is past the end of table "
                             f"{table_index} ({len(entries)} entries).")
        which = (f"clip {played[0]}" if len(played) == 1
                 else f"clips {played[0]}-{played[-1]}")
        return samples, rate, (
            f"table {table_index}, channel {channel}, {which} "
            f"({count} box{'es' if count > 1 else ''}) - "
            f"{len(samples) / rate:.2f}s")

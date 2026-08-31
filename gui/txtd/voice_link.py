"""Playing the voice line that belongs to a line of text.

A TXTD entry's `extra` says which clip speaks it (see functions/voice.py
for the format and how it was confirmed). Resolving that to audio needs
three things this class holds together:

    the raw data track   - the only place the audio survives intact
    the area's overlay   - which carries the clip tables
    a channel per table  - worked out from the table's own boundaries

The channel is resolved once per table and kept, since it costs a decode
of 200 blocks on each of the 32 channels.

Which of an overlay's several tables a given master draws from is not
established, so the table is exposed as a choice rather than guessed;
table 0 is the default because that is the one confirmed against
savestates for the first master of AREA_04.
"""
import struct

from functions import voice, xa

NO_VOICE = 0xFFFF


class VoiceLink:
    """Resolves a TXTD entry to its clip, and decodes it."""

    def __init__(self):
        self.image = None
        self.lba = 0
        self.sectors = 0
        self.tables = []
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
            self.tables = voice.find_tables(path)
        except Exception:
            self.tables = []
        self._channels.clear()
        return len(self.tables)

    def channel(self, table_index):
        """Which VOICE.XA channel a table describes, resolved once."""
        if table_index in self._channels:
            return self._channels[table_index]
        _off, entries = self.tables[table_index]
        found, _score = voice.best_channel(self.image, self.lba, entries)
        self._channels[table_index] = found
        return found

    def clip_for(self, extra, table_index=0):
        """(samples, rate, note) for a TXTD entry's `extra`.

        Returns (None, 0, why) when there is nothing to play."""
        if extra is None or extra == NO_VOICE:
            return None, 0, "This line has no voice."
        if not self.ready():
            return None, 0, ("Open the disc's data track in the Voice tab "
                             "first - the audio is only intact there.")
        if table_index >= len(self.tables):
            return None, 0, "That table is not in this overlay."
        index = extra & 0xFF
        _off, entries = self.tables[table_index]
        if index >= len(entries):
            return None, 0, (f"Clip {index} is past the end of table "
                             f"{table_index} ({len(entries)} entries).")
        channel = self.channel(table_index)
        if channel is None:
            return None, 0, "Could not tell which channel this table uses."
        sectors = voice.clip_sectors(entries[index], channel)
        with open(self.image, "rb") as f:
            samples, rate = xa.decode_channel(f, self.lba, sectors)
        start, length = entries[index]
        note = (f"clip {index}: block {start}, {length} blocks, "
                f"channel {channel} - {len(samples) / rate:.2f}s")
        return samples, rate, note

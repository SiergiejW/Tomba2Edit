"""Editing TOMBA2.SND, and keeping those edits in a project.

The music and the sound effects are not in TOMBA2.DAT. They are in
TOMBA2.SND: ten sequences at the front, then the effects VAB, the music
VAB, and one VAB per area. So an edited sequence or a swapped sound is
an edit to that file, the same way translated menu text is an edit to
MAIN.EXE - and it is kept the same way, by holding the whole edited file
in the project and handing it to the disc builder as a replacement.

Holding the whole 3.6 MB file rather than a list of patches is the point
rather than a shortcut: it means a project can be opened, edited and
saved with no disc anywhere near it, which is already true of MAIN.EXE
and SOP.BIN and should not stop being true the moment the work is music.

THE SEQUENCES HAVE A BUDGET

They sit back to back from 0x30 up to wherever the first VAB starts,
with their offsets listed in the last sector of the resident span. That
table is ours to rewrite, so a sequence may grow or shrink and the rest
slide along to suit - but the whole run still has to fit in front of the
first VAB, because moving a VAB would mean rewriting every pointer in
the game that reaches into it. On the US disc that is 9,860 bytes for
all ten. compute() reports what is left the way the MAIN.EXE pool does,
so an editor can say "no" while it is still cheap to say.
"""
import struct

from functions import seq, sfx

SECTOR = seq.SECTOR
TABLE_ENTRIES = seq.RESIDENT_SEQS
# Where the first sequence begins. Fixed: the file opens with the span
# table and the sequences start immediately after it.
FIRST_SEQ = 0x30


class SndEditError(Exception):
    """Raised when an edit can't be applied, before anything changes."""


def table_offset(data):
    """Where the resident sequence offsets are listed."""
    span = struct.unpack_from("<H", data, 0)[0]
    return (span - 1) * SECTOR


def seq_region(data):
    """(first byte, last byte + 1) the resident sequences may occupy.

    They end where the first VAB begins, and that VAB cannot move."""
    banks = sfx.find_banks(data)
    if not banks:
        raise SndEditError(
            "No VAB in this TOMBA2.SND - it isn't one this can edit.")
    return FIRST_SEQ, banks[0]["offset"]


class SndEdits:
    """The disc's TOMBA2.SND with staged replacements over the top."""

    def __init__(self):
        self.data = None            # the file as the project holds it
        self.sequences = {}         # slot -> replacement SEQ bytes
        self.sounds = {}            # (bank, index) -> replacement VAG bytes
        self._offsets = {}          # slot -> where it is in self.data

    # -- loading -------------------------------------------------------

    def set_source(self, data):
        """Take a TOMBA2.SND. Staged edits are dropped: a sequence slot
        on a different disc is a different piece of music."""
        self.data = bytes(data) if data else None
        self.sequences.clear()
        self.sounds.clear()
        self._offsets = (dict(seq.resident(self.data)) if self.data else {})

    def loaded(self):
        return self.data is not None

    def slots(self):
        return sorted(self._offsets)

    # -- reading -------------------------------------------------------

    def sequence(self, slot):
        """The bytes of one sequence - the staged replacement if there
        is one, otherwise the disc's own."""
        if slot in self.sequences:
            return self.sequences[slot]
        at = self._offsets.get(slot)
        if at is None:
            return None
        return self.data[at:at + seq.length(self.data, at)]

    def is_edited(self, slot):
        return slot in self.sequences

    def count(self):
        return len(self.sequences) + len(self.sounds)

    # -- the sounds ----------------------------------------------------

    def waveforms(self):
        """[(bank, index, offset, length)] for every sound in the file."""
        return sfx.samples(self.data) if self.data else []

    def sound(self, bank, index):
        """One waveform's bytes - staged replacement or the disc's."""
        if (bank, index) in self.sounds:
            return self.sounds[(bank, index)]
        for b, i, at, size in self.waveforms():
            if (b, i) == (bank, index):
                return self.data[at:at + size]
        return None

    def sound_is_edited(self, bank, index):
        return (bank, index) in self.sounds

    def stage_sound(self, bank, index, blob):
        """Replace one waveform. Raises if the bank's sounds would no
        longer fit in front of the next one."""
        if self.data is None:
            raise SndEditError("No TOMBA2.SND is loaded.")
        if self.sound(bank, index) is None:
            raise SndEditError(f"There is no sound {index} in bank {bank}.")
        was = self.sounds.get((bank, index))
        self.sounds[(bank, index)] = bytes(blob)
        state = self.compute_sounds(bank)
        if state["free"] < 0:
            if was is None:
                self.sounds.pop((bank, index), None)
            else:
                self.sounds[(bank, index)] = was
            raise SndEditError(
                f"That sound is {-state['free']} byte(s) too big. The bank's "
                f"waveforms share {state['capacity']} bytes before the next "
                "one begins, and the banks cannot move. Use a shorter "
                "sample - a lower-rate one is resampled back up to the "
                "rate the slot plays at and comes out the same size.")
        return state

    def clear_sound(self, bank, index):
        self.sounds.pop((bank, index), None)

    def compute_sounds(self, bank):
        """{"used", "capacity", "free"} for one bank's waveform area."""
        members, start, end = _group_of(self.data, bank)
        used = 0
        for b, i, _at, size in self.waveforms():
            if b not in members:
                continue
            blob = self.sound(b, i)
            used += len(blob) if blob is not None else size
        return {"used": used, "capacity": end - start,
                "free": (end - start) - used}

    # -- staging -------------------------------------------------------

    def stage_sequence(self, slot, blob):
        """Replace one sequence. Raises if it isn't a SEQ, or if the set
        of them would no longer fit."""
        if self.data is None:
            raise SndEditError("No TOMBA2.SND is loaded.")
        if slot not in self._offsets:
            raise SndEditError(f"There is no sequence in slot {slot}.")
        if not blob or blob[:4] != seq.MAGIC:
            raise SndEditError(
                "That isn't a SEQ - it has to start with \"pQES\".")
        was = self.sequences.get(slot)
        self.sequences[slot] = bytes(blob)
        state = self.compute()
        if state["free"] < 0:
            if was is None:
                self.sequences.pop(slot, None)
            else:
                self.sequences[slot] = was
            raise SndEditError(
                f"That sequence is {-state['free']} byte(s) too big. All ten "
                f"share {state['capacity']} bytes in front of the sound bank, "
                f"and the bank cannot be moved. Shorten it, or shorten "
                "another one.")
        return state

    def would_fit(self, slot, blob):
        """What the budget would be with `blob` in `slot`, staging
        nothing. What an editor asks on every keystroke."""
        was, had = self.sequences.get(slot), slot in self.sequences
        self.sequences[slot] = bytes(blob)
        try:
            return self.compute()
        finally:
            if had:
                self.sequences[slot] = was
            else:
                self.sequences.pop(slot, None)

    def clear_sequence(self, slot):
        self.sequences.pop(slot, None)

    def clear(self):
        self.sequences.clear()

    # -- budget --------------------------------------------------------

    def compute(self):
        """{"used", "capacity", "free"} for the sequence region."""
        start, end = seq_region(self.data)
        used = 0
        for slot in self.slots():
            blob = self.sequence(slot)
            used += _aligned(len(blob or b""))
        capacity = end - start
        return {"used": used, "capacity": capacity, "free": capacity - used}

    # -- writing -------------------------------------------------------

    def rebuild(self):
        """TOMBA2.SND with the staged sequences in it.

        The sequences are laid back down in the order the disc had them
        and the offset table is rewritten to match, so one growing only
        pushes the ones after it along. Everything from the first VAB on
        is untouched - which is all the instruments and every area's
        bank, none of which this moves."""
        if self.data is None:
            raise SndEditError("No TOMBA2.SND is loaded.")
        if not self.sequences and not self.sounds:
            return self.data

        state = self.compute()
        if state["free"] < 0:
            raise SndEditError(
                f"The sequences need {state['used']} bytes and only "
                f"{state['capacity']} are available.")

        start, end = seq_region(self.data)
        out = bytearray(self.data)
        # In the order they sit in the file, which is the order the
        # offset table lists them in.
        order = sorted(self._offsets, key=lambda s: self._offsets[s])
        written, at = {}, start
        region = bytearray()
        for slot in order:
            blob = self.sequence(slot) or b""
            written[slot] = at
            region += blob + bytes(_aligned(len(blob)) - len(blob))
            at += _aligned(len(blob))
        region += bytes((end - start) - len(region))
        out[start:end] = region

        table = table_offset(self.data)
        for position, slot in enumerate(seq.RESIDENT_ORDER):
            if slot in written:
                struct.pack_into("<I", out, table + position * 4,
                                 written[slot])

        self._rebuild_sounds(out)
        return bytes(out)

    def _rebuild_sounds(self, out):
        """Lay each edited group's waveforms back down, in place.

        The size table is what the game reads to find them, so it is
        rewritten from the lengths actually written rather than from
        what was asked for - a waveform is a whole number of 16-byte
        blocks and the table counts in units of eight, so the two have
        to be derived from the same bytes or a sound plays half of the
        next one."""
        if not self.sounds:
            return
        touched = {bank for bank, _index in self.sounds}
        for members, start, end in groups(self.data):
            if not touched & set(members):
                continue
            state = self.compute_sounds(members[0])
            if state["free"] < 0:
                raise SndEditError(
                    f"The sounds in bank {members[0]} need {state['used']} "
                    f"bytes and only {state['capacity']} are available.")

            region = bytearray()
            lengths = {}
            for bank, index, _at, size in self.waveforms():
                if bank not in members:
                    continue
                blob = self.sound(bank, index)
                blob = self.data[_at:_at + size] if blob is None else blob
                lengths.setdefault(bank, []).append(len(blob))
                region += blob
            region += bytes((end - start) - len(region))
            out[start:end] = region

            for bank in members:
                table = _size_table(self.data, bank)
                for position, size in enumerate(lengths.get(bank, []), start=1):
                    struct.pack_into("<H", out, table + position * 2, size // 8)


def _aligned(size):
    """Sequences start on a four-byte boundary, as the disc has them."""
    return (size + 3) & ~3


# ----------------------------------------------------------------------
# The sounds
# ----------------------------------------------------------------------
#
# A VAB's waveforms sit end to end after its header, and the header's
# size table says how long each one is. Swapping one therefore means
# rewriting the whole run after it and the table with it - the same
# fixed-budget repack the sequences get, with a different wall at the
# end: the next bank's header, which cannot move because the file's own
# sector table says where each area's bank begins.
#
# Banks whose headers run straight into one another share one waveform
# area (the effects and music banks do), so the group is the unit.

def groups(data):
    """[(bank numbers, waveform area start, area end)] in file order."""
    banks = sfx.find_banks(data)
    out, group = [], []
    for n, bank in enumerate(banks):
        group.append((n, bank))
        packed = (n + 1 < len(banks)
                  and banks[n + 1]["offset"] == bank["end"])
        if packed:
            continue
        start = group[-1][1]["end"] + sfx.BLANK
        # Everything up to the next group's header belongs to this
        # group's waveforms; the last group runs to the end of the file.
        following = banks[n + 1]["offset"] if n + 1 < len(banks) else len(data)
        out.append(([number for number, _b in group], start, following))
        group = []
    return out


def _group_of(data, bank):
    for members, start, end in groups(data):
        if bank in members:
            return members, start, end
    raise SndEditError(f"There is no sound bank {bank} in this file.")


def _size_table(data, bank_index):
    """Where a bank's waveform sizes are listed."""
    bank = sfx.find_banks(data)[bank_index]
    return (bank["offset"] + sfx.HEADER + sfx.PROGRAM_TABLE
            + bank["programs"] * sfx.TONE_TABLE)

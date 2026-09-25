"""Playing a MIDI through a SoundFont, so an export can be heard first.

A sequence exported as MIDI carries note numbers and program numbers,
and a program number means "whatever instrument the VAB has in that
slot" - which is the game's sound bank, not General MIDI. Opened
anywhere else it plays on whatever that program lands on, and that is
the thing worth hearing BEFORE the file is saved rather than after.

So: a small SF2 reader and renderer. It is deliberately a preview
synth, not a mixing desk. What it does:

    zones       preset -> instrument -> sample, picked by key and
                velocity, with the generators that decide pitch
    pitch       root key, coarse and fine tune, scale tuning
    loops       sampleModes, so a sustained instrument sustains
    envelope    delay/attack/hold/decay/sustain/release on volume
    level       initialAttenuation and velocity
    pan

What it does not: filters, modulators, LFOs, chorus, reverb, or any
kind of effect. Those change the character of a patch, and a preview
that pretends to be exact would be worse than one that is honestly
rough - what this answers is "which instrument is that, and is it the
one I want", not "is this master quality".

Nothing here needs a library. numpy makes it quick; without it the
renderer refuses rather than taking minutes, because a preview nobody
waits for is not a preview.
"""
import os
import struct

try:
    import numpy as np
except ImportError:                                  # pragma: no cover
    np = None

RATE = 44100
# Generators this understands. The numbers are the SF2 spec's.
GEN_START_OFFSET = 0
GEN_END_OFFSET = 1
GEN_STARTLOOP_OFFSET = 2
GEN_ENDLOOP_OFFSET = 3
GEN_START_COARSE = 4
GEN_PAN = 17
GEN_DELAY = 33
GEN_ATTACK = 34
GEN_HOLD = 35
GEN_DECAY = 36
GEN_SUSTAIN = 37
GEN_RELEASE = 38
GEN_INSTRUMENT = 41
GEN_KEY_RANGE = 43
GEN_VEL_RANGE = 44
GEN_STARTLOOP_COARSE = 45
GEN_KEYNUM = 46
GEN_VELOCITY = 47
GEN_ATTENUATION = 48
GEN_ENDLOOP_COARSE = 50
GEN_COARSE_TUNE = 51
GEN_FINE_TUNE = 52
GEN_SAMPLE_ID = 53
GEN_SAMPLE_MODES = 54
GEN_SCALE_TUNING = 56
GEN_END_COARSE = 12
GEN_ROOT_KEY = 58

# What a zone starts from when it says nothing. Only the ones that
# matter to a preview; anything else is read if present and ignored.
DEFAULTS = {
    GEN_PAN: 0, GEN_ATTENUATION: 0, GEN_COARSE_TUNE: 0, GEN_FINE_TUNE: 0,
    GEN_SCALE_TUNING: 100, GEN_SAMPLE_MODES: 0, GEN_ROOT_KEY: -1,
    GEN_KEYNUM: -1, GEN_VELOCITY: -1,
    # Timecents. -12000 is a hundredth of a second, which is the
    # spec's "immediately"; sustain is in centibels of attenuation.
    GEN_DELAY: -12000, GEN_ATTACK: -12000, GEN_HOLD: -12000,
    GEN_DECAY: -12000, GEN_SUSTAIN: 0, GEN_RELEASE: -12000,
    GEN_START_OFFSET: 0, GEN_END_OFFSET: 0,
    GEN_STARTLOOP_OFFSET: 0, GEN_ENDLOOP_OFFSET: 0,
    GEN_START_COARSE: 0, GEN_END_COARSE: 0,
    GEN_STARTLOOP_COARSE: 0, GEN_ENDLOOP_COARSE: 0,
}

DRUM_BANK = 128
DRUM_CHANNEL = 9


class SoundFontError(Exception):
    """Raised when a file isn't a SoundFont this can use."""


def available():
    """Whether rendering is possible at all here."""
    return np is not None


def _chunks(blob, at, end):
    """(tag, start, size) of each chunk in [at, end)."""
    out = []
    while at + 8 <= end:
        tag = blob[at:at + 4]
        size = struct.unpack_from("<I", blob, at + 4)[0]
        out.append((tag, at + 8, size))
        at += 8 + size + (size & 1)
    return out


def _timecents(value):
    return 2.0 ** (value / 1200.0)


class Zone:
    """One sample, and the generators that say how to play it."""
    __slots__ = ("gens", "sample")

    def __init__(self, gens, sample):
        self.gens = gens
        self.sample = sample

    def get(self, which):
        return self.gens.get(which, DEFAULTS.get(which, 0))

    def matches(self, key, velocity):
        low, high = self.gens.get(GEN_KEY_RANGE, (0, 127))
        if not low <= key <= high:
            return False
        low, high = self.gens.get(GEN_VEL_RANGE, (0, 127))
        return low <= velocity <= high


class Sample:
    __slots__ = ("name", "start", "end", "loop_start", "loop_end",
                 "rate", "root", "correction")

    def __init__(self, name, start, end, loop_start, loop_end, rate, root,
                 correction):
        self.name = name
        self.start, self.end = start, end
        self.loop_start, self.loop_end = loop_start, loop_end
        self.rate = rate or RATE
        self.root = root
        self.correction = correction


class SoundFont:
    """An .sf2, read far enough to play it."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        with open(path, "rb") as f:
            blob = f.read()
        self._parse(blob)

    # -- reading -------------------------------------------------------

    def _parse(self, blob):
        if blob[:4] != b"RIFF" or blob[8:12] != b"sfbk":
            raise SoundFontError("That isn't a SoundFont (.sf2) file.")
        total = struct.unpack_from("<I", blob, 4)[0]
        lists = _chunks(blob, 12, min(len(blob), 8 + total))
        pdta = sdta = info = None
        for tag, start, size in lists:
            if tag != b"LIST":
                continue
            kind = blob[start:start + 4]
            body = (start + 4, start + size)
            if kind == b"pdta":
                pdta = body
            elif kind == b"sdta":
                sdta = body
            elif kind == b"INFO":
                info = body
        if pdta is None or sdta is None:
            raise SoundFontError(
                "That SoundFont has no sample or preset data in it.")

        self.title = self._title(blob, info)
        self._read_samples(blob, sdta)
        self._read_presets(blob, pdta)

    def _title(self, blob, info):
        if info is None:
            return self.name
        for tag, start, size in _chunks(blob, *info):
            if tag == b"INAM":
                return blob[start:start + size].split(b"\0")[0].decode(
                    "latin-1", "replace") or self.name
        return self.name

    def _read_samples(self, blob, sdta):
        raw = half = None
        for tag, start, size in _chunks(blob, *sdta):
            if tag == b"smpl":
                raw = (start, size)
            elif tag == b"sm24":
                half = (start, size)
        if raw is None:
            raise SoundFontError("That SoundFont has no sample data.")
        start, size = raw
        self.pcm = np.frombuffer(blob, dtype="<i2", count=size // 2,
                                 offset=start).astype(np.float32) / 32768.0
        # 24-bit soundfonts carry the low byte separately. Ignored: the
        # extra eight bits are below what a preview can be heard to
        # need, and reading them doubles the memory for nothing.
        self._has_24 = half is not None

    def _read_presets(self, blob, pdta):
        parts = {}
        for tag, start, size in _chunks(blob, *pdta):
            parts[tag] = (start, size)
        for needed in (b"phdr", b"pbag", b"pgen", b"inst", b"ibag",
                       b"igen", b"shdr"):
            if needed not in parts:
                raise SoundFontError(
                    f"That SoundFont is missing its {needed.decode()} "
                    "chunk, so it cannot be played.")

        self.samples = []
        start, size = parts[b"shdr"]
        for i in range(size // 46 - 1):          # the last is the EOS marker
            at = start + i * 46
            name = blob[at:at + 20].split(b"\0")[0].decode("latin-1", "replace")
            (begin, end, loop_start, loop_end, rate) = struct.unpack_from(
                "<IIIII", blob, at + 20)
            root, correction = struct.unpack_from("<Bb", blob, at + 40)
            self.samples.append(Sample(name, begin, end, loop_start, loop_end,
                                       rate, root, correction))

        pgen = self._gen_list(blob, parts[b"pgen"])
        igen = self._gen_list(blob, parts[b"igen"])
        pbag = self._bag_list(blob, parts[b"pbag"])
        ibag = self._bag_list(blob, parts[b"ibag"])

        instruments = []
        start, size = parts[b"inst"]
        for i in range(size // 22 - 1):
            at = start + i * 22
            name = blob[at:at + 20].split(b"\0")[0].decode("latin-1", "replace")
            bag = struct.unpack_from("<H", blob, at + 20)[0]
            nxt = struct.unpack_from("<H", blob, at + 42)[0] \
                if i + 1 < size // 22 else len(ibag)
            instruments.append((name, bag, nxt))

        # (bank, program) -> [Zone]
        self.presets = {}
        self.preset_names = {}
        start, size = parts[b"phdr"]
        count = size // 38
        for i in range(count - 1):
            at = start + i * 38
            name = blob[at:at + 20].split(b"\0")[0].decode("latin-1", "replace")
            program, bank, bag = struct.unpack_from("<HHH", blob, at + 20)
            nxt = struct.unpack_from("<H", blob, at + 38 + 24)[0]
            zones = self._preset_zones(pbag, pgen, ibag, igen, instruments,
                                       bag, nxt)
            if zones:
                self.presets[(bank, program)] = zones
                self.preset_names[(bank, program)] = name

    @staticmethod
    def _gen_list(blob, part):
        start, size = part
        out = []
        for i in range(size // 4):
            oper, amount = struct.unpack_from("<HH", blob, start + i * 4)
            out.append((oper, amount))
        return out

    @staticmethod
    def _bag_list(blob, part):
        start, size = part
        return [struct.unpack_from("<HH", blob, start + i * 4)[0]
                for i in range(size // 4)]

    def _preset_zones(self, pbag, pgen, ibag, igen, instruments, bag, nxt):
        """Every playable zone under one preset, generators resolved.

        A preset's own generators are added to the instrument's, which
        is what the spec says for everything a preview cares about -
        the difference between add and replace only shows on ranges,
        and those are handled as a filter rather than a sum."""
        zones = []
        preset_global = {}
        for b in range(bag, min(nxt, len(pbag))):
            gens = self._read_gens(pgen, pbag, b)
            if GEN_INSTRUMENT not in gens:
                # A zone with no instrument is the preset's global one.
                preset_global = gens
                continue
            index = gens[GEN_INSTRUMENT]
            if not 0 <= index < len(instruments):
                continue
            _name, ibag_start, ibag_end = instruments[index]
            inst_global = {}
            for ib in range(ibag_start, min(ibag_end, len(ibag))):
                inner = self._read_gens(igen, ibag, ib)
                if GEN_SAMPLE_ID not in inner:
                    inst_global = inner
                    continue
                merged = dict(DEFAULTS)
                merged.update(inst_global)
                merged.update(inner)
                # The preset layer offsets what the instrument set.
                for oper, value in preset_global.items():
                    if oper in (GEN_KEY_RANGE, GEN_VEL_RANGE):
                        continue
                    merged[oper] = merged.get(oper, 0) + value
                for oper, value in gens.items():
                    if oper in (GEN_KEY_RANGE, GEN_VEL_RANGE, GEN_INSTRUMENT):
                        continue
                    merged[oper] = merged.get(oper, 0) + value
                # Ranges: the narrower of the two layers wins.
                for which in (GEN_KEY_RANGE, GEN_VEL_RANGE):
                    outer = gens.get(which) or preset_global.get(which)
                    inner_range = inner.get(which) or inst_global.get(which)
                    got = inner_range or outer
                    if outer and inner_range:
                        got = (max(outer[0], inner_range[0]),
                               min(outer[1], inner_range[1]))
                    if got:
                        merged[which] = got
                sample_id = inner[GEN_SAMPLE_ID]
                if 0 <= sample_id < len(self.samples):
                    zones.append(Zone(merged, self.samples[sample_id]))
        return zones

    @staticmethod
    def _read_gens(gens, bags, index):
        """One bag's generators, as {oper: value}."""
        start = bags[index]
        end = bags[index + 1] if index + 1 < len(bags) else len(gens)
        out = {}
        for oper, amount in gens[start:min(end, len(gens))]:
            if oper in (GEN_KEY_RANGE, GEN_VEL_RANGE):
                out[oper] = (amount & 0xFF, amount >> 8)
            elif oper in (GEN_PAN, GEN_COARSE_TUNE, GEN_FINE_TUNE,
                          GEN_DELAY, GEN_ATTACK, GEN_HOLD, GEN_DECAY,
                          GEN_RELEASE):
                out[oper] = amount - 0x10000 if amount > 0x7FFF else amount
            else:
                out[oper] = amount
        return out

    # -- what is in it -------------------------------------------------

    def programs(self, bank=0):
        """[(program, name)] in one bank, in order."""
        return sorted((program, self.preset_names[(b, program)])
                      for (b, program) in self.presets if b == bank)

    def zone_for(self, bank, program, key, velocity):
        zones = self.presets.get((bank, program))
        if zones is None:
            # Fall back the way a GM player does: the same program in
            # bank 0, then anything at all, so an unusual bank still
            # makes a sound instead of silence.
            zones = self.presets.get((0, program))
        if zones is None and self.presets:
            zones = next(iter(self.presets.values()))
        if not zones:
            return None
        for zone in zones:
            if zone.matches(key, velocity):
                return zone
        return zones[0]


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

def render(events, resolution, tempo=500000, font=None, rate=RATE,
           seconds_cap=600.0, progress=None):
    """A seq/midi event stream through `font`, as float32 stereo.

    `events` is [(delta, status, a, b)] - what formats.audio.seq.events
    and formats.audio.midi.read both produce - so the same stream the
    piano roll edits is what gets auditioned here."""
    if np is None:
        raise SoundFontError(
            "Rendering a SoundFont needs numpy, which isn't installed.")
    if font is None:
        raise SoundFontError("No SoundFont chosen.")

    notes, length = _schedule(events, resolution, tempo, seconds_cap)
    total = int(length * rate) + rate          # a second of tail for releases
    left = np.zeros(total, dtype=np.float32)
    right = np.zeros(total, dtype=np.float32)

    for index, note in enumerate(notes):
        _voice(font, note, left, right, rate)
        if progress is not None and not index % 32:
            progress(index / max(1, len(notes)))
    peak = float(max(np.abs(left).max(initial=0.0),
                     np.abs(right).max(initial=0.0)))
    if peak > 1.0:
        left /= peak
        right /= peak
    return left, right


def _schedule(events, resolution, tempo, cap):
    """[(start, end, channel, key, velocity, program)] in seconds."""
    per_tick = tempo / 1e6 / max(1, resolution)
    programs = [0] * 16
    open_notes = {}
    out = []
    time = 0.0
    for delta, status, a, b in events:
        time += delta * per_tick
        if time > cap:
            break
        if status == 0xFF:
            per_tick = a / 1e6 / max(1, resolution)
            continue
        kind, channel = status & 0xF0, status & 0x0F
        if kind == 0xC0:
            programs[channel] = a
        elif kind == 0x90 and b:
            open_notes[(channel, a)] = (time, b, programs[channel])
        elif kind in (0x80, 0x90):
            held = open_notes.pop((channel, a), None)
            if held is not None:
                start, velocity, program = held
                out.append((start, time, channel, a, velocity, program))
    for (channel, key), (start, velocity, program) in open_notes.items():
        out.append((start, min(time, cap), channel, key, velocity, program))
    end = max((n[1] for n in out), default=0.0)
    return out, end


def _voice(font, note, left, right, rate):
    """One note, mixed in place."""
    start, stop, channel, key, velocity, program = note
    bank = DRUM_BANK if channel == DRUM_CHANNEL else 0
    zone = font.zone_for(bank, program, key, velocity)
    if zone is None:
        return
    sample = zone.sample

    begin = (sample.start + zone.get(GEN_START_OFFSET)
             + zone.get(GEN_START_COARSE) * 32768)
    end = (sample.end + zone.get(GEN_END_OFFSET)
           + zone.get(GEN_END_COARSE) * 32768)
    begin = max(0, min(begin, len(font.pcm)))
    end = max(begin + 1, min(end, len(font.pcm)))

    root = zone.get(GEN_ROOT_KEY)
    if root < 0:
        root = sample.root
    played = zone.get(GEN_KEYNUM)
    if played < 0:
        played = key
    scale = zone.get(GEN_SCALE_TUNING) / 100.0
    cents = ((played - root) * 100.0 * scale
             + zone.get(GEN_COARSE_TUNE) * 100.0
             + zone.get(GEN_FINE_TUNE) + sample.correction)
    step = (2.0 ** (cents / 1200.0)) * (sample.rate / float(rate))
    if step <= 0:
        return

    held = max(0.0, stop - start)
    release = _timecents(zone.get(GEN_RELEASE))
    span = held + min(release, 3.0)
    count = int(span * rate)
    if count <= 0:
        return
    at = int(start * rate)
    if at >= len(left):
        return
    count = min(count, len(left) - at)

    positions = np.arange(count, dtype=np.float64) * step
    loops = zone.get(GEN_SAMPLE_MODES) & 1
    loop_start = (sample.loop_start + zone.get(GEN_STARTLOOP_OFFSET)
                  + zone.get(GEN_STARTLOOP_COARSE) * 32768) - begin
    loop_end = (sample.loop_end + zone.get(GEN_ENDLOOP_OFFSET)
                + zone.get(GEN_ENDLOOP_COARSE) * 32768) - begin
    body = end - begin
    if loops and 0 <= loop_start < loop_end <= body:
        run = loop_end - loop_start
        over = positions >= loop_start
        positions[over] = loop_start + np.mod(positions[over] - loop_start, run)
    else:
        positions = positions[positions < body - 1]
        count = len(positions)
        if count <= 0:
            return

    # Linear interpolation. A preview does not need better, and better
    # costs more than the whole rest of this put together.
    low = positions.astype(np.int64)
    frac = (positions - low).astype(np.float32)
    low = np.clip(low, 0, body - 2)
    data = font.pcm[begin:end]
    wave = data[low] * (1.0 - frac) + data[low + 1] * frac

    wave *= _envelope(zone, held, count, rate)
    attenuation = 10.0 ** (-zone.get(GEN_ATTENUATION) / 200.0)
    wave *= attenuation * (velocity / 127.0) ** 2

    pan = max(-500, min(500, zone.get(GEN_PAN))) / 1000.0
    left[at:at + count] += wave * (0.5 - pan)
    right[at:at + count] += wave * (0.5 + pan)


def _envelope(zone, held, count, rate):
    """The volume envelope over `count` samples, as float32."""
    delay = _timecents(zone.get(GEN_DELAY))
    attack = _timecents(zone.get(GEN_ATTACK))
    hold = _timecents(zone.get(GEN_HOLD))
    decay = _timecents(zone.get(GEN_DECAY))
    release = _timecents(zone.get(GEN_RELEASE))
    # Sustain is an attenuation in centibels, not a level.
    sustain = 10.0 ** (-max(0, zone.get(GEN_SUSTAIN)) / 200.0)

    t = np.arange(count, dtype=np.float32) / float(rate)
    out = np.zeros(count, dtype=np.float32)

    attack_end = delay + attack
    hold_end = attack_end + hold
    decay_end = hold_end + decay

    rising = (t >= delay) & (t < attack_end)
    out[rising] = (t[rising] - delay) / max(attack, 1e-6)
    out[(t >= attack_end) & (t < hold_end)] = 1.0
    falling = (t >= hold_end) & (t < decay_end)
    out[falling] = 1.0 - (1.0 - sustain) * (
        (t[falling] - hold_end) / max(decay, 1e-6))
    out[t >= decay_end] = sustain

    # The release runs from wherever the envelope had got to when the
    # key came up, which is what stops a short note on a slow patch
    # from clicking.
    if held < count / float(rate):
        tail = t >= held
        if tail.any():
            level = out[tail][0] if out[tail].size else sustain
            out[tail] = level * np.clip(
                1.0 - (t[tail] - held) / max(release, 1e-6), 0.0, 1.0)
    return out


def to_wav_bytes(left, right, rate=RATE):
    """Stereo float32 as a 16-bit WAV, for the player."""
    from formats.audio import xa

    frames = np.empty(len(left) * 2, dtype=np.int16)
    frames[0::2] = np.clip(left * 32767.0, -32768, 32767).astype(np.int16)
    frames[1::2] = np.clip(right * 32767.0, -32768, 32767).astype(np.int16)
    return xa.wav_bytes_raw(frames.tobytes(), rate, 2)

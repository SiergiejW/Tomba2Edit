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

Two file formats, one renderer. .sf2 is what people download; .dls is
what Windows already has - gm.dls, the sound set behind the Microsoft
GS Wavetable synth, sitting in System32\\drivers on every machine. They
are different formats but the same idea, so the DLS reader converts
into the SF2 reader's units and everything downstream is shared. That
is what lets the export dialog open with a General MIDI voice already
chosen instead of asking for a file nobody has yet.

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


def load(path):
    """Whichever kind of sound set `path` is."""
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:4] != b"RIFF":
        raise SoundFontError("That isn't a sound set this can read.")
    if head[8:12] == b"DLS ":
        return DlsFont(path)
    if head[8:12] == b"sfbk":
        return SoundFont(path)
    raise SoundFontError(
        "That is a RIFF file, but not a SoundFont (.sf2) or a "
        "downloadable sound set (.dls).")


def system_fonts():
    """[(label, path)] of sound sets already on this machine.

    Windows ships gm.dls, which is the General MIDI voice every other
    program on the machine plays a .mid through - so it is the right
    thing to preview against, and the right default."""
    out = []
    for path, label in (
            (os.path.join(os.environ.get("SystemRoot", r"C:\\Windows"),
                          "System32", "drivers", "gm.dls"),
             "Windows General MIDI (gm.dls)"),
            ("/usr/share/sounds/sf2/FluidR3_GM.sf2", "FluidR3 General MIDI"),
            ("/usr/share/soundfonts/default.sf2", "System default"),
            ("/Library/Audio/Sounds/Banks/gs_instruments.dls",
             "macOS General MIDI"),
    ):
        if os.path.isfile(path):
            out.append((label, path))
    return out


class SoundFont:
    """An .sf2, read far enough to play it."""

    kind = "sf2"

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
# DLS
# ----------------------------------------------------------------------
#
# The same idea in a different shape. An instrument holds regions; a
# region names a key range, a velocity range, a wave and how to tune
# it. The wave pool is a list of ordinary RIFF WAVEs, found through a
# cue table of offsets.
#
# Everything is converted into the generator units the SF2 side
# already speaks, so Zone, Sample and the renderer are shared. The
# conversions worth naming:
#
#     attenuation   DLS gain is 1/655360 dB and positive means louder;
#                   SF2 wants centibels of attenuation, positive
#                   quieter. So cB = -gain / 65536.
#     envelope      DLS articulation carries timecents as 16.16 fixed,
#                   and a sustain LEVEL in 0.1% units where SF2 wants
#                   an attenuation in centibels.
#     drums         the high bit of the bank number, not a bank of its
#                   own - mapped onto SF2's bank 128 so one lookup
#                   serves both formats.

CONN_DST_ATTENUATION = 0x0001
CONN_DST_PAN = 0x0004
CONN_DST_EG1_ATTACK = 0x0206
CONN_DST_EG1_DECAY = 0x0207
CONN_DST_EG1_RELEASE = 0x0209
CONN_DST_EG1_SUSTAIN = 0x020A
CONN_SRC_NONE = 0x0000

DLS_DRUM_FLAG = 0x80000000


class DlsFont(SoundFont):
    """A .dls - Windows' gm.dls and anything shaped like it."""

    kind = "dls"

    def _parse(self, blob):
        if blob[:4] != b"RIFF" or blob[8:12] != b"DLS ":
            raise SoundFontError("That isn't a DLS sound set.")
        if np is None:
            raise SoundFontError(
                "Reading a sound set needs numpy, which isn't installed.")
        end = min(len(blob), 8 + struct.unpack_from("<I", blob, 4)[0])
        top = _chunks(blob, 12, end)

        lins = wvpl = ptbl = info = None
        for tag, start, size in top:
            if tag == b"ptbl":
                ptbl = (start, size)
            elif tag == b"LIST":
                kind = blob[start:start + 4]
                if kind == b"lins":
                    lins = (start + 4, start + size)
                elif kind == b"wvpl":
                    wvpl = (start + 4, start + size)
                elif kind == b"INFO":
                    info = (start + 4, start + size)
        if lins is None or wvpl is None or ptbl is None:
            raise SoundFontError(
                "That DLS has no instruments or no wave pool in it.")

        self.title = self._title(blob, info)
        self._read_waves(blob, wvpl, ptbl)
        self._read_instruments(blob, lins)

    def _read_waves(self, blob, wvpl, ptbl):
        """Every wave in the pool, concatenated into one float array.

        One array rather than one per wave so Sample keeps meaning the
        same thing it does for an .sf2 - a start and an end into a
        pool - and the renderer needs no idea which format it came
        from."""
        start, size = ptbl
        _cb, cues = struct.unpack_from("<II", blob, start)
        offsets = [struct.unpack_from("<I", blob, start + 8 + i * 4)[0]
                   for i in range(cues)]
        base = wvpl[0]

        pool = []
        self.samples = []
        self._wave_loops = []
        at = 0
        for offset in offsets:
            here = base + offset
            if here + 12 > len(blob) or blob[here:here + 4] != b"LIST":
                self.samples.append(None)
                self._wave_loops.append(None)
                continue
            size = struct.unpack_from("<I", blob, here + 4)[0]
            body = _chunks(blob, here + 12, here + 8 + size)
            fmt = data = wsmp = None
            for tag, s0, n in body:
                if tag == b"fmt ":
                    fmt = (s0, n)
                elif tag == b"data":
                    data = (s0, n)
                elif tag == b"wsmp":
                    wsmp = (s0, n)
            if fmt is None or data is None:
                self.samples.append(None)
                self._wave_loops.append(None)
                continue
            _tag, channels, rate, _br, _align, bits = struct.unpack_from(
                "<HHIIHH", blob, fmt[0])
            frames = self._pcm(blob, data, channels, bits)
            pool.append(frames)
            self.samples.append(Sample("wave", at, at + len(frames), 0, 0,
                                       rate, 60, 0))
            self._wave_loops.append(
                self._wsmp(blob, wsmp) if wsmp else None)
            at += len(frames)
        self.pcm = (np.concatenate(pool) if pool
                    else np.zeros(1, dtype=np.float32))

    @staticmethod
    def _pcm(blob, data, channels, bits):
        start, size = data
        if bits == 8:
            raw = np.frombuffer(blob, dtype=np.uint8, count=size,
                                offset=start).astype(np.float32)
            frames = (raw - 128.0) / 128.0
        else:
            count = size // 2
            frames = np.frombuffer(blob, dtype="<i2", count=count,
                                   offset=start).astype(np.float32) / 32768.0
        if channels > 1:
            usable = (len(frames) // channels) * channels
            frames = frames[:usable].reshape(-1, channels).mean(axis=1)
        return np.ascontiguousarray(frames, dtype=np.float32)

    @staticmethod
    def _wsmp(blob, wsmp):
        """(unity, fine, attenuation cB, loop start, loop end) or None."""
        start, size = wsmp
        cb, unity, fine, gain, _opts, loops = struct.unpack_from(
            "<IHhiII", blob, start)
        loop_start = loop_end = 0
        if loops:
            at = start + cb
            if at + 16 <= start + size:
                _lcb, _kind, begin, length = struct.unpack_from(
                    "<IIII", blob, at)
                loop_start, loop_end = begin, begin + length
        return (unity, fine, max(0, -gain // 65536), loop_start, loop_end,
                bool(loops))

    def _read_instruments(self, blob, lins):
        self.presets = {}
        self.preset_names = {}
        for tag, start, size in _chunks(blob, *lins):
            if tag != b"LIST" or blob[start:start + 4] != b"ins ":
                continue
            self._read_instrument(blob, start + 4, start + size)

    def _read_instrument(self, blob, at, end):
        insh = lrgn = info = None
        art = {}
        for tag, start, size in _chunks(blob, at, end):
            if tag == b"insh":
                insh = start
            elif tag == b"LIST":
                kind = blob[start:start + 4]
                if kind == b"lrgn":
                    lrgn = (start + 4, start + size)
                elif kind in (b"lart", b"lar2"):
                    art = self._articulation(blob, start + 4, start + size)
                elif kind == b"INFO":
                    info = (start + 4, start + size)
        if insh is None or lrgn is None:
            return
        _regions, bank, program = struct.unpack_from("<III", blob, insh)
        key = (DRUM_BANK if bank & DLS_DRUM_FLAG else (bank >> 8) & 0x7F,
               program & 0x7F)

        zones = []
        for tag, start, size in _chunks(blob, *lrgn):
            if tag != b"LIST" or blob[start:start + 4] not in (b"rgn ",
                                                               b"rgn2"):
                continue
            zone = self._read_region(blob, start + 4, start + size, art)
            if zone is not None:
                zones.append(zone)
        if zones:
            self.presets[key] = zones
            self.preset_names[key] = self._name(blob, info) or f"{key}"

    def _read_region(self, blob, at, end, inherited):
        rgnh = wsmp = wlnk = None
        art = dict(inherited)
        for tag, start, size in _chunks(blob, at, end):
            if tag == b"rgnh":
                rgnh = start
            elif tag == b"wsmp":
                wsmp = (start, size)
            elif tag == b"wlnk":
                wlnk = start
            elif tag == b"LIST" and blob[start:start + 4] in (b"lart",
                                                              b"lar2"):
                art.update(self._articulation(blob, start + 4, start + size))
        if rgnh is None or wlnk is None:
            return None
        key_low, key_high, vel_low, vel_high = struct.unpack_from(
            "<HHHH", blob, rgnh)
        index = struct.unpack_from("<HHII", blob, wlnk)[3]
        if not 0 <= index < len(self.samples) or self.samples[index] is None:
            return None
        sample = self.samples[index]

        gens = dict(DEFAULTS)
        gens.update(art)
        gens[GEN_KEY_RANGE] = (key_low, key_high)
        gens[GEN_VEL_RANGE] = (vel_low, vel_high)

        # A region's own wsmp wins over the wave's; either may be
        # absent, in which case the wave plays at its own pitch.
        tuning = (self._wsmp(blob, wsmp) if wsmp
                  else self._wave_loops[index])
        if tuning:
            unity, fine, attenuation, loop_start, loop_end, loops = tuning
            gens[GEN_ROOT_KEY] = unity
            gens[GEN_FINE_TUNE] = fine
            gens[GEN_ATTENUATION] = gens.get(GEN_ATTENUATION, 0) + attenuation
            if loops and loop_end > loop_start:
                gens[GEN_SAMPLE_MODES] = 1
                # Sample carries pool-absolute positions, as the SF2
                # side does; the region's are relative to the wave.
                gens[GEN_STARTLOOP_OFFSET] = (
                    sample.start + loop_start - sample.loop_start)
                gens[GEN_ENDLOOP_OFFSET] = (
                    sample.start + loop_end - sample.loop_end)
        return Zone(gens, sample)

    @staticmethod
    def _articulation(blob, at, end):
        """The connection blocks that set the volume envelope and pan.

        Only the unconditional ones - source and control both none.
        Anything driven by a controller or an LFO is past what a
        preview shows."""
        out = {}
        for tag, start, size in _chunks(blob, at, end):
            if tag not in (b"art1", b"art2"):
                continue
            cb, count = struct.unpack_from("<II", blob, start)
            for i in range(count):
                block = start + cb + i * 12
                if block + 12 > start + size:
                    break
                source, control, destination, _transform, scale = \
                    struct.unpack_from("<HHHHi", blob, block)
                if source != CONN_SRC_NONE or control != CONN_SRC_NONE:
                    continue
                if destination == CONN_DST_EG1_ATTACK:
                    out[GEN_ATTACK] = scale // 65536
                elif destination == CONN_DST_EG1_DECAY:
                    out[GEN_DECAY] = scale // 65536
                elif destination == CONN_DST_EG1_RELEASE:
                    out[GEN_RELEASE] = scale // 65536
                elif destination == CONN_DST_EG1_SUSTAIN:
                    # 0.1% units of LEVEL; SF2 wants centibels of
                    # attenuation, so a full-level sustain is zero.
                    level = max(0.0, min(1.0, scale / 65536.0 / 1000.0))
                    out[GEN_SUSTAIN] = (0 if level >= 1.0 else
                                        (1440 if level <= 0.0 else
                                         int(-200.0 * _log10(level))))
                elif destination == CONN_DST_ATTENUATION:
                    out[GEN_ATTENUATION] = max(0, -scale // 65536)
                elif destination == CONN_DST_PAN:
                    # 0.1% of full right; SF2 pans in tenths of a
                    # percent either side of centre, same scale.
                    out[GEN_PAN] = max(-500, min(500, scale // 65536))
        return out

    @staticmethod
    def _name(blob, info):
        if info is None:
            return ""
        for tag, start, size in _chunks(blob, *info):
            if tag == b"INAM":
                return blob[start:start + size].split(b"\0")[0].decode(
                    "latin-1", "replace")
        return ""

    def _title(self, blob, info):
        return self._name(blob, info) or self.name


def _log10(value):
    import math
    return math.log10(value)


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

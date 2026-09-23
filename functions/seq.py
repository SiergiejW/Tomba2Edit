"""Sony SEQ music, out of TOMBA2.SND and the overlays, played on a VAB.

TOMBA2.SND opens with u16 sector numbers: where each area's span starts,
the resident span in front of them all. The resident span holds ten SEQs,
the effects VAB and the music VAB, and its last sector lists their
offsets. Each area's span is that area's own VAB
(f_LoadAreaAudioResources).

What plays each slot is MAIN.EXE's: f_PlaySoundEffect turns sounds
0x70-0x79 into slots 2-13, the pause menu opens 0 and 1, GAME.BIN plays
8 and 9. Slots 10-12 are filled per area: A05, A0A-A0F and A0K open SEQs
of their own on the music VAB, A0L on its area VAB. The scripted ones
(sounds 0x73, 0x75, 0x76-0x7C) are what dialogue scripts play.

A SEQ is "pQES", a version, then big-endian: resolution (u16), tempo in
microseconds per quarter (3 bytes), rhythm (2 bytes), then one MIDI track
- delta times, running status, programs indexing the VAB, NRPN 20 and 30
marking a loop's start and end (data entry: the count, 0 or 127 for ever).
"""
import struct
from dataclasses import dataclass

import numpy as np

from functions import sfx

MAGIC = b"pQES"
HEADER = 15
SECTOR = 0x800
RATE = 44100                # the SPU's; a tone at its centre note plays at it
OVERLAY_BASE = 0x80108F9C
RESIDENT_SEQS = 10
# Resident SEQ offsets are listed in this slot order.
RESIDENT_ORDER = (3, 2, 1, 0, 4, 5, 6, 7, 8, 9)
MUSIC_BANK = 1              # sfx.find_banks order: effects, music, one per area
FIRST_AREA_BANK = 2
LOOP_START, LOOP_END = 20, 30
FOREVER = (0, 127)
LOOP_PASSES = 2             # how often an endless loop is played through
RELEASE_LIMIT = 3.0         # seconds a released note may ring on
MAX_SECONDS = 600.0
ENVELOPE_BLOCK = 32

RESIDENT_USES = {
    0: "Pause menu opens", 1: "Pause menu closes",
    2: "Event discovered", 3: "Event cleared",
    4: "Sound 0x72: windmill cutscene", 5: "Sound 0x73: scripted",
    6: "Sound 0x74: Let's make a pot", 7: "Sound 0x75: scripted",
    8: "Game start", 9: "Loading between areas",
}
AREA_USES = {10: "Sound 0x76/0x7A: area", 11: "Sound 0x77/0x7B: area",
             12: "Sound 0x78/0x7C: area"}

# f_OpenSequencedMusicSlot's calls in the overlays: SEQ address -> slot.
OVERLAY_SLOTS = {
    "A05": {0x801406AC: 11, 0x80140508: 12},
    "A0A": {0x801271F4: 10}, "A0B": {0x801246AC: 10}, "A0C": {0x801261E0: 10},
    "A0D": {0x80125160: 10}, "A0E": {0x80125474: 10}, "A0F": {0x80129134: 10},
    "A0K": {0x8011D5D0: 12, 0x8011D774: 11},
    "A0L": {0x801179E0: 10, 0x80118AA8: 11, 0x80119AA0: 12},
}
# Overlays whose SEQs play on their own area's VAB, by area number.
AREA_VAB = {"A0L": 0x15}


@dataclass
class Tone:
    volume: int
    pan: int
    center: int
    shift: int
    low: int
    high: int
    bend_down: int
    bend_up: int
    adsr1: int
    adsr2: int
    program: int
    vag: int


def resident(snd):
    """[(slot, offset)] of the SEQs in TOMBA2.SND."""
    span = struct.unpack_from("<H", snd, 0)[0]
    offsets = struct.unpack_from(f"<{RESIDENT_SEQS}I", snd, (span - 1) * SECTOR)
    return sorted((slot, at) for slot, at in zip(RESIDENT_ORDER, offsets)
                  if snd[at:at + 4] == MAGIC)


def overlay(name, data):
    """[(slot or None, offset)] of the SEQs an overlay carries."""
    known = OVERLAY_SLOTS.get(name.upper()[:3], {})
    out, at = [], data.find(MAGIC)
    while at >= 0:
        out.append((known.get(OVERLAY_BASE + at), at))
        at = data.find(MAGIC, at + 4)
    return out


def bank_for(name):
    """The sfx bank an overlay's SEQs play on."""
    area = AREA_VAB.get(name.upper()[:3])
    return MUSIC_BANK if area is None else FIRST_AREA_BANK + area


def header(data, at):
    """(ticks per quarter, microseconds per quarter)."""
    resolution = struct.unpack_from(">H", data, at + 8)[0]
    return resolution or 480, int.from_bytes(data[at + 10:at + 13], "big") or 500000


def events(data, at):
    """[(delta ticks, status, a, b)] up to the end of the track."""
    out, pos, running, end = [], at + HEADER, 0, len(data)
    while pos < end:
        delta = 0
        while pos < end:
            byte = data[pos]
            pos += 1
            delta = delta << 7 | byte & 0x7F
            if not byte & 0x80:
                break
        if pos >= end:
            break
        status = data[pos]
        if status & 0x80:
            pos += 1
            if status < 0xF0:
                running = status
        else:
            status = running
        if status == 0xFF:
            kind = data[pos] if pos < end else 0x2F
            if kind != 0x51:
                break
            out.append((delta, 0xFF, int.from_bytes(data[pos + 1:pos + 4], "big"), 0))
            pos += 4
            continue
        if not status:
            break
        if status & 0xF0 in (0xC0, 0xD0):
            out.append((delta, status, data[pos], 0))
            pos += 1
        else:
            out.append((delta, status, data[pos], data[pos + 1] if pos + 1 < end else 0))
            pos += 2
    return out


def length(data, at):
    """How many bytes the SEQ at `at` actually occupies.

    The distance to the next SEQ is not the same thing: they are laid
    out with slack between them, and repacking against that slack would
    spend a budget on padding. This walks the track the way events()
    does and stops after the end marker."""
    pos, running, end = at + HEADER, 0, len(data)
    while pos < end:
        while pos < end:
            byte = data[pos]
            pos += 1
            if not byte & 0x80:
                break
        if pos >= end:
            break
        status = data[pos]
        if status & 0x80:
            pos += 1
            if status < 0xF0:
                running = status
        else:
            status = running
        if status == 0xFF:
            kind = data[pos] if pos < end else 0x2F
            if kind != 0x51:
                # End of track: the marker and its length byte.
                return min(end, pos + 2) - at
            pos += 4
            continue
        if not status:
            break
        pos += 1 if status & 0xF0 in (0xC0, 0xD0) else 2
    return min(pos, end) - at


def instruments(snd, bank):
    """{program: (volume, pan, [Tone])} of one VAB. Each program with tones
    has a block of 16 VagAtr after the program table, in program order."""
    base = sfx.find_banks(snd)[bank]["offset"]
    count = struct.unpack_from("<H", snd, base + 18)[0]
    programs = base + sfx.HEADER
    tones = programs + sfx.PROGRAM_TABLE
    out, block = {}, 0
    for program in range(128):
        entry = programs + program * 16
        used = snd[entry]
        if not used:
            continue
        if block >= count:
            break
        found = []
        for t in range(min(used, 16)):
            (_prior, _mode, volume, pan, center, shift, low, high, _vw, _vt,
             _pw, _pt, down, up, _r1, _r2, adsr1, adsr2, prog, vag) = struct.unpack_from(
                "<16BHHhh", snd, tones + block * sfx.TONE_TABLE + t * 32)
            if vag > 0:
                found.append(Tone(volume, pan, center, shift, low, high, down, up,
                                  adsr1, adsr2, prog, vag))
        out[program] = (snd[entry + 1], snd[entry + 4], found)
        block += 1
    return out


def waveform(snd, offset, size):
    """(float32 samples, loop start sample or None)."""
    samples = np.asarray(sfx.decode(snd, offset, size), dtype=np.float32) / 32768.0
    loop = start = None
    for n, at in enumerate(range(offset, offset + size, sfx.BLOCK)):
        flags = snd[at + 1]
        if flags & sfx.LOOP:
            start = n * sfx.BLOCK_SAMPLES
        if flags & sfx.END:
            if flags & sfx.REPEAT:
                loop = start if start is not None else 0
            break
    return samples, loop


def _advance(level, counter, shift, step, exponential, samples):
    """(level, counter) after `samples` of one ADSR phase, the SPU's rules."""
    add = step << max(0, 11 - shift)
    rate = 0x8000 >> max(0, shift - 11)
    if exponential and step > 0 and level > 0x6000:
        if shift < 10:
            add >>= 2
        elif shift >= 11:
            rate >>= 2
        else:
            add >>= 1
            rate >>= 1
    if exponential and step < 0:
        add = add * level >> 15
    total = counter + rate * samples
    level = max(0, min(0x7FFF, level + add * (total >> 15)))
    return level, total & 0x7FFF


def envelope(adsr1, adsr2, held):
    """Envelope 0..1 per sample for a note held `held` samples, then released
    until silent."""
    attack = ((adsr1 >> 10) & 0x1F, 7 - ((adsr1 >> 8) & 3), bool(adsr1 & 0x8000))
    decay = ((adsr1 >> 4) & 0xF, -8, True)
    sustain_level = min(0x7FFF, ((adsr1 & 0xF) + 1) * 0x800)
    down = bool(adsr2 & 0x4000)
    step = (adsr2 >> 6) & 3
    sustain = ((adsr2 >> 8) & 0x1F, -8 + step if down else 7 - step, bool(adsr2 & 0x8000))
    release = (adsr2 & 0x1F, -8, bool(adsr2 & 0x20))
    levels, level, counter, phase, n = [], 0, 0, 0, 0
    limit = held + int(RELEASE_LIMIT * RATE)
    while n < limit:
        if n >= held and phase < 3:
            phase, counter = 3, 0
        rule = (attack, decay, sustain, release)[phase]
        level, counter = _advance(level, counter, *rule, ENVELOPE_BLOCK)
        if phase == 0 and level >= 0x7FFF:
            phase, counter = 1, 0
        elif phase == 1 and level <= sustain_level:
            phase, counter = 2, 0
        levels.append(level)
        n += ENVELOPE_BLOCK
        if phase == 3 and level <= 0:
            break
    return np.repeat(np.asarray(levels, dtype=np.float32) / 0x7FFF, ENVELOPE_BLOCK)


def _voice(samples, loop, step, frames):
    """`frames` output samples of a waveform stepped `step` samples at a time."""
    n = len(samples)
    if n < 2:
        return np.zeros(0, dtype=np.float32)
    position = np.arange(frames, dtype=np.float64) * step
    if loop is not None and loop < n - 1:
        span = n - loop
        over = position >= n
        position[over] = loop + np.mod(position[over] - loop, span)
    else:
        position = position[position < n - 1]
    index = position.astype(np.int64)
    fraction = (position - index).astype(np.float32)
    following = index + 1
    if loop is not None:
        following[following >= n] = loop
    return samples[index] * (1.0 - fraction) + samples[np.minimum(following, n - 1)] * fraction


def perform(data, at):
    """([start s, end s, key, velocity, channel state], length s) for every
    note the SEQ plays - an endless loop LOOP_PASSES times."""
    resolution, tempo = header(data, at)
    track = events(data, at)
    channels = [{"program": 0, "volume": 127, "expression": 127, "pan": 64,
                 "bend": 8192} for _ in range(16)]
    per_tick = tempo / 1e6 / resolution
    notes, sounding = [], {}
    time, i, nrpn = 0.0, 0, None
    loop_at, loop_left, passes = None, 0, 1
    while i < len(track) and time < MAX_SECONDS:
        delta, status, a, b = track[i]
        i += 1
        time += delta * per_tick
        kind, channel = status & 0xF0, status & 0x0F
        state = channels[channel]
        if status == 0xFF:
            per_tick = a / 1e6 / resolution
        elif kind == 0x90 and b:
            if (channel, a) in sounding:
                sounding.pop((channel, a))[1] = time
            note = [time, None, a, b, dict(state)]
            notes.append(note)
            sounding[(channel, a)] = note
        elif kind == 0x80 or kind == 0x90:
            if (channel, a) in sounding:
                sounding.pop((channel, a))[1] = time
        elif kind == 0xB0:
            if a == 99:
                nrpn = b
                if b == LOOP_END and loop_at is not None:
                    if loop_left in FOREVER:
                        if passes < LOOP_PASSES:
                            passes += 1
                            i = loop_at
                        else:
                            break
                    elif loop_left > 1:
                        loop_left -= 1
                        i = loop_at
            elif a == 6 and nrpn == LOOP_START:
                loop_at, loop_left, nrpn = i, b, None
            elif a == 7:
                state["volume"] = b
            elif a == 10:
                state["pan"] = b
            elif a == 11:
                state["expression"] = b
        elif kind == 0xC0:
            state["program"] = a
        elif kind == 0xE0:
            state["bend"] = a | b << 7
    for note in sounding.values():
        note[1] = time
    return notes, time


def played(data, at, snd, bank):
    """{(bank, vag)} of the waveforms the SEQ sounds."""
    programs = instruments(snd, bank)
    notes, _length = perform(data, at)
    return {(bank, tone.vag) for _s, _e, key, _v, state in notes
            for tone in programs.get(state["program"], (0, 0, ()))[2]
            if tone.low <= key <= tone.high}


def render(data, at, snd, bank):
    """(stereo float32 (n, 2), {(bank, vag) played}) for the SEQ at `at`."""
    notes, _length = perform(data, at)
    programs = instruments(snd, bank)
    where = {(b, v): (o, s) for b, v, o, s in sfx.samples(snd)}
    cache, used, pieces, length = {}, set(), [], 0
    for start, end, key, velocity, state in notes:
        volume, pan, tones = programs.get(state["program"], (127, 64, ()))
        for tone in tones:
            if not tone.low <= key <= tone.high or (bank, tone.vag) not in where:
                continue
            if tone.vag not in cache:
                cache[tone.vag] = waveform(snd, *where[(bank, tone.vag)])
            samples, loop = cache[tone.vag]
            bend = (state["bend"] - 8192) / 8192.0
            bend *= tone.bend_up if bend > 0 else tone.bend_down
            semitones = key - tone.center + tone.shift / 100.0 + bend
            level = envelope(tone.adsr1, tone.adsr2, int((end - start) * RATE))
            sound = _voice(samples, loop, 2.0 ** (semitones / 12.0), len(level))
            if not len(sound):
                continue
            sound = sound * level[:len(sound)] * (
                velocity * tone.volume * volume * state["volume"] * state["expression"]
                / 127.0 ** 5)
            side = max(-64, min(63, (tone.pan - 64) + (pan - 64) + (state["pan"] - 64)))
            left, right = min(1.0, (63 - side) / 63.0), min(1.0, (64 + side) / 64.0)
            first = int(start * RATE)
            pieces.append((first, sound, left, right))
            length = max(length, first + len(sound))
            used.add((bank, tone.vag))
    out = np.zeros((length, 2), dtype=np.float32)
    for first, sound, left, right in pieces:
        out[first:first + len(sound), 0] += sound * left
        out[first:first + len(sound), 1] += sound * right
    peak = float(np.abs(out).max()) if length else 0.0
    if peak > 0.98:
        out *= 0.98 / peak
    return out, used


def pcm(stereo):
    """Interleaved 16-bit little-endian PCM bytes."""
    return (np.clip(stereo, -1.0, 1.0) * 32767).astype("<i2").tobytes()

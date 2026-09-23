"""CD-XA audio: reading it off a disc image and decoding it to PCM.

The streamed audio - BGM.XA, VOICE.XA, DEMO.XA and the audio inside the
STR movies - is not a file you can read the ordinary way. It lives in
Mode 2 Form 2 sectors, which carry 2324 bytes of payload where a normal
sector carries 2048, and several streams are interleaved through the same
file: every sector names the channel it belongs to, and the drive plays
one channel while skipping the rest.

That has two consequences worth knowing before using any of this:

  - The XA files extracted into a CD folder, and any 2048-byte .iso, are
    unusable. Extracting at 2048 bytes a sector throws away 276 bytes of
    every sector, so what is left is not decodable audio. Read from a
    raw 2352-byte image instead - a BIN track, which is what a bin/cue
    rip gives you.
  - A "clip" is a run of sectors on one channel, so pulling one out means
    deinterleaving first.

The audio itself is 4-bit ADPCM. A sector's payload is 18 sound groups
of 128 bytes; each group is a 16-byte header and 112 bytes of samples,
holding 8 sound units of 28 samples each - 4032 samples a sector.

The header stores its 8 parameters twice, which is how the layout was
confirmed here rather than assumed: on this disc header[0:4] equals
header[4:8] and header[8:12] equals header[12:16] in every group
checked, so the live copies are the second of each pair.
"""
import array
import struct

from disc import cdsector

SECTOR = 2352
SUBHEADER = 16          # after the 12-byte sync and 4-byte header
PAYLOAD = 24            # Form 2 payload starts here
FORM2_LEN = 2324
GROUPS = 18
GROUP_LEN = 128
UNITS = 8               # sound units per group, 4-bit
UNIT_SAMPLES = 28
SAMPLES_PER_SECTOR = GROUPS * UNITS * UNIT_SAMPLES      # 4032

# The four ADPCM predictors, as sixty-fourths.
FILTERS = ((0, 0), (60, 0), (115, -52), (98, -55))


def coding(byte):
    """What a sector's coding byte says: (channels, rate, bits)."""
    return (2 if byte & 3 else 1,
            18900 if (byte >> 2) & 3 else 37800,
            8 if (byte >> 4) & 3 else 4)


# How a source frames its sectors: (stride, payload offset, has subheader).
# A disc track carries whole 2352-byte sectors, each naming its own file
# and channel. A VOICE.XA extracted properly is just the payloads back to
# back, with nothing to say which channel a sector belongs to - but the
# interleave is fixed, so the sector's position gives it away.
RAW = (SECTOR, PAYLOAD, True)
FLAT = (FORM2_LEN, 0, False)
CHANNELS = 32


def framing(path):
    """How to read this file, from its size.

    A flat file whose length divides by 2324 is a clean Form 2
    extraction. One that divides by 2048 instead was extracted as if it
    were an ordinary file, which discarded 276 bytes of every sector -
    that one is not decodable and is refused by the caller."""
    import os

    size = os.path.getsize(path)
    if size % SECTOR == 0:
        return RAW
    if size % FORM2_LEN == 0:
        return FLAT
    return None


def sectors(image, lba, count):
    """Yield (index, subheader, payload) for a run of raw sectors.

    `image` is an open file on a 2352-byte-per-sector track."""
    image.seek(lba * SECTOR)
    for i in range(count):
        raw = image.read(SECTOR)
        if len(raw) < SECTOR:
            return
        yield i, raw[SUBHEADER:SUBHEADER + 8], raw[PAYLOAD:PAYLOAD + FORM2_LEN]


def channel_map(image, lba, count, frame=RAW):
    """{(file, channel): [sector index, ...]} for one XA file.

    Only Form 2 audio sectors are listed; the rest of a file - padding,
    or the video sectors of an STR - is left out. A flat extraction has
    no subheaders to ask, so the interleave position is used instead."""
    stride, _off, has_sub = frame
    if not has_sub:
        out = {}
        for i in range(count):
            out.setdefault((1, i % CHANNELS), []).append(i)
        return out
    out = {}
    for i, sub, _payload in sectors(image, lba, count):
        fileno, chan, submode = sub[0], sub[1], sub[2]
        if not (submode & 0x20) or not (submode & 0x04):
            continue                      # not Form 2, or not audio
        out.setdefault((fileno, chan), []).append(i)
    return out


def decode_sector(payload, state=None, stereo=False):
    """One sector's payload as 16-bit samples, and the filter state to
    carry into the next sector of the same channel.

    Passing the previous sector's state back in is what keeps a clip
    continuous; starting fresh mid-stream clicks.

    In stereo the sound units alternate between the speakers - even units
    are left, odd are right - and each side predicts from its own history,
    so the state is two pairs rather than one. The samples come out
    interleaved, which is what a WAV wants. The voice track is mono; the
    music, BGM.XA and DEMO.XA, is stereo."""
    if stereo:
        return _decode_stereo(payload, state)
    old, older = state or (0, 0)
    out = []
    for g in range(GROUPS):
        base = g * GROUP_LEN
        header = payload[base:base + 16]
        data = payload[base + 16:base + GROUP_LEN]
        for unit in range(UNITS):
            # The live parameters are the second copy of each pair.
            param = header[4 + unit] if unit < 4 else header[12 + unit - 4]
            shift = param & 0x0F
            filt = param >> 4
            if filt >= len(FILTERS):
                filt = 0                  # a stream can carry a spare index
            if shift > 12:
                # Only ever seen when reading something that is not XA
                # audio - past the end of the file, or a video sector.
                # Clamped rather than left to shift by a negative amount,
                # which raises and takes the caller down with it.
                shift = 12
            k0, k1 = FILTERS[filt]
            for s in range(UNIT_SAMPLES):
                byte = data[s * 4 + (unit >> 1)]
                nibble = (byte >> (4 * (unit & 1))) & 0x0F
                if nibble > 7:
                    nibble -= 16
                sample = nibble << (12 - shift)
                sample += (old * k0 + older * k1 + 32) >> 6
                sample = -32768 if sample < -32768 else (
                    32767 if sample > 32767 else sample)
                older, old = old, sample
                out.append(sample)
    return out, (old, older)


def _decode_stereo(payload, state=None):
    """A stereo sector: units alternate speakers, output interleaved."""
    oldl, olderl, oldr, olderr = state or (0, 0, 0, 0)
    out = []
    for g in range(GROUPS):
        base = g * GROUP_LEN
        header = payload[base:base + 16]
        data = payload[base + 16:base + GROUP_LEN]
        for pair in range(UNITS // 2):
            params = []
            for unit in (pair * 2, pair * 2 + 1):
                p = header[4 + unit] if unit < 4 else header[12 + unit - 4]
                shift = p & 0x0F
                filt = p >> 4
                if filt >= len(FILTERS):
                    filt = 0
                if shift > 12:
                    shift = 12
                params.append((shift, FILTERS[filt]))
            for s in range(UNIT_SAMPLES):
                byte = data[s * 4 + pair]
                for side in (0, 1):
                    nibble = (byte >> (4 * side)) & 0x0F
                    if nibble > 7:
                        nibble -= 16
                    shift, (k0, k1) = params[side]
                    if side == 0:
                        old, older = oldl, olderl
                    else:
                        old, older = oldr, olderr
                    v = (nibble << (12 - shift)) + ((old * k0 + older * k1
                                                     + 32) >> 6)
                    v = -32768 if v < -32768 else (32767 if v > 32767 else v)
                    if side == 0:
                        olderl, oldl = oldl, v
                    else:
                        olderr, oldr = oldr, v
                    out.append(v)
    return out, (oldl, olderl, oldr, olderr)


def decode_channel(image, lba, indices, limit=None, frame=RAW, overrides=None):
    """Decode one channel's sectors into (samples, rate).

    `indices` are sector numbers within the file, as channel_map gives
    them, so the interleave is already gone.

    `overrides` is {absolute lba: raw 2352-byte sector} - a voice edit
    staged in formats/audio/voice_edit.VoiceEditStore but not yet written
    to any file. Checked ahead of the image itself, sector by sector,
    so a line just imported plays back the replacement immediately -
    including after navigating away and back - without needing the
    disc image on disk to already carry it."""
    stride, payload_at, has_sub = frame
    samples = []
    state = None
    rate = 18900 if not has_sub else 37800
    speakers = 1
    for n, index in enumerate(indices):
        if limit is not None and n >= limit:
            break
        abs_lba = lba + index
        raw = overrides.get(abs_lba) if overrides else None
        if raw is None:
            image.seek(abs_lba * stride)
            raw = image.read(stride)
        if len(raw) < stride:
            break
        if has_sub:
            speakers, rate, bits = coding(raw[SUBHEADER + 3])
            if bits != 4:
                continue                  # 8-bit XA is not used on this disc
        block, state = decode_sector(
            raw[payload_at:payload_at + FORM2_LEN], state, speakers == 2)
        samples.extend(block)
    return samples, rate, speakers


def _clamp16(v):
    return -32768 if v < -32768 else (32767 if v > 32767 else v)


def _encode_unit(samples, old, older):
    """The (filter, shift, nibbles, old, older) that reproduces 28
    samples best, simulating the decoder sample by sample so the state
    handed back is exactly what a real decode would carry forward - a
    naive encode-from-the-original-signal drifts, since decode_sector
    always predicts from its own reconstructed history, not the source.

    Tries all 4 filters and all 13 shifts and keeps the smallest total
    squared error; correct but not fast, which is fine for something a
    user runs once per replaced line rather than every frame."""
    best = None
    for filt, (k0, k1) in enumerate(FILTERS):
        for shift in range(13):
            o, ol = old, older
            scale = 1 << (12 - shift)
            nibbles = []
            err = 0
            for s in samples:
                predicted = (o * k0 + ol * k1 + 32) >> 6
                diff = s - predicted
                nib = (diff + (scale >> 1)) // scale
                nib = -8 if nib < -8 else (7 if nib > 7 else nib)
                decoded = _clamp16((nib << (12 - shift)) + predicted)
                err += (decoded - s) ** 2
                nibbles.append(nib & 0x0F)
                ol, o = o, decoded
            if best is None or err < best[0]:
                best = (err, filt, shift, nibbles, o, ol)
                if err == 0:
                    break
        if best and best[0] == 0:
            break
    return best[1], best[2], best[3], best[4], best[5]


def encode_sector(samples, state=None):
    """SAMPLES_PER_SECTOR (4032) mono samples, padded with silence if
    short, into one sector's worth of ADPCM groups - the exact inverse
    of decode_sector's layout (same group/unit/sample order, same
    doubled header), so what this writes decodes back through
    decode_sector unchanged bar quantization.

    Returns (2304-byte group data, new (old, older) state) - the caller
    pads that out to a full Form 2 payload; the last 20 bytes of one are
    reserved and left zero, same as a real sector's own."""
    if len(samples) < SAMPLES_PER_SECTOR:
        samples = list(samples) + [0] * (SAMPLES_PER_SECTOR - len(samples))
    old, older = state or (0, 0)
    payload = bytearray(GROUPS * GROUP_LEN)
    pos = 0
    for g in range(GROUPS):
        base = g * GROUP_LEN
        header = bytearray(16)
        data = bytearray(112)
        for pair in range(4):
            pair_nibbles = [None, None]
            for half in range(2):
                unit = pair * 2 + half
                chunk = samples[pos:pos + UNIT_SAMPLES]
                pos += UNIT_SAMPLES
                filt, shift, nibbles, old, older = _encode_unit(
                    chunk, old, older)
                param = (filt << 4) | shift
                if unit < 4:
                    header[unit] = param
                    header[4 + unit] = param
                else:
                    header[8 + (unit - 4)] = param
                    header[12 + (unit - 4)] = param
                pair_nibbles[half] = nibbles
            lo, hi = pair_nibbles
            for s in range(UNIT_SAMPLES):
                data[s * 4 + pair] = (hi[s] << 4) | lo[s]
        payload[base:base + 16] = header
        payload[base + 16:base + GROUP_LEN] = data
    return bytes(payload), (old, older)


def encode_full_sector(original_sector, samples, state=None):
    """A raw 2352-byte Mode 2 Form 2 sector with fresh ADPCM audio in
    place of `original_sector`'s own - same sync, header and subheader
    (so it still names the same file, channel and coding), only the
    payload and its checksum change.

    `samples` should be exactly SAMPLES_PER_SECTOR long; shorter is
    padded with silence by encode_sector, longer is silently cut by
    slicing before this is called - the caller is the one that knows
    whether the user should be asked about either."""
    sector = bytearray(original_sector)
    body, new_state = encode_sector(samples[:SAMPLES_PER_SECTOR], state)
    payload = body + b"\0" * (FORM2_LEN - len(body))
    sector[PAYLOAD:PAYLOAD + FORM2_LEN] = payload
    sector = cdsector.rebuild_form2(sector)
    return bytes(sector), new_state


def wav_bytes_raw(pcm, rate, channels=1):
    """A WAV around PCM that is already bytes - a CD audio track, which
    needs no decoding at all."""
    byte_rate = rate * channels * 2
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                                    byte_rate, channels * 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def wav_bytes(samples, rate, channels=1):
    """16-bit PCM as a WAV file's bytes.

    Handed to a player as a file in memory, which is what lets decoded
    audio use the same transport - seeking, duration, position - as a
    track read off disk."""
    body = array.array("h", samples).tobytes()
    byte_rate = rate * channels * 2
    return (b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                                    byte_rate, channels * 2, 16)
            + b"data" + struct.pack("<I", len(body)) + body)


def write_wav(path, samples, rate, channels=1):
    """Write mono 16-bit PCM out as a plain WAV."""
    with open(path, "wb") as f:
        f.write(wav_bytes(samples, rate, channels))
    return path

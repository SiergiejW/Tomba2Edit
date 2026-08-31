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


def decode_channel(image, lba, indices, limit=None, frame=RAW):
    """Decode one channel's sectors into (samples, rate).

    `indices` are sector numbers within the file, as channel_map gives
    them, so the interleave is already gone."""
    stride, payload_at, has_sub = frame
    samples = []
    state = None
    rate = 18900 if not has_sub else 37800
    speakers = 1
    for n, index in enumerate(indices):
        if limit is not None and n >= limit:
            break
        image.seek((lba + index) * stride)
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

"""Encoding samples into the SPU's ADPCM, the inverse of sfx.decode.

A waveform is 16-byte blocks: a shift and a filter in the first byte,
flags in the second, then 28 samples as 4-bit nibbles. Decoding is a
fixed formula (see formats/audio/sfx.decode); encoding is a search, because
nothing says which of the five filters and which shift will reproduce a
block best - so every combination is tried and the one with the least
error wins. Forty tries per 28 samples is nothing next to how long it
takes to read the file they came from.

Two details matter and are easy to get wrong:

    THE PREDICTOR CARRIES. A block's filter works on the two samples
    before it, which are the last two of the previous block *as the
    decoder reconstructed them*, not as they were in the source. So the
    encoder decodes its own output as it goes and predicts from that,
    or the error compounds and the tail of a long sound drifts into
    noise.

    THE FLAGS ARE THE STRUCTURE. The last block says END, and says
    REPEAT when the sound sustains; the block a repeat returns to says
    LOOP. Getting these wrong does not distort the audio, it changes
    whether the sound stops - a missing END plays on into whatever
    waveform is stored next.
"""
import struct

from formats.audio.sfx import BLOCK, BLOCK_SAMPLES, END, FILTERS, LOOP, REPEAT

SHIFT_RANGE = range(13)         # 13..15 are invalid; the SPU treats them as 9


def _encode_block(samples, old, older, shift, filter_index):
    """(bytes of nibble data, old, older, squared error) for one try."""
    k0, k1 = FILTERS[filter_index]
    nibbles = []
    error = 0
    for wanted in samples:
        predicted = (old * k0 + older * k1) >> 6
        # What the nibble has to carry, rounded to nearest rather than
        # truncated: truncation biases every block the same direction
        # and the bias is audible as a DC drift on quiet sounds.
        target = wanted - predicted
        step = 1 << (12 - shift)
        nibble = (target + (step >> 1)) // step if step else 0
        nibble = -8 if nibble < -8 else (7 if nibble > 7 else nibble)
        value = (nibble << (12 - shift)) + predicted
        value = -32768 if value < -32768 else (
            32767 if value > 32767 else value)
        older, old = old, value
        nibbles.append(nibble & 0x0F)
        difference = wanted - value
        error += difference * difference
    packed = bytearray()
    for i in range(0, len(nibbles), 2):
        low = nibbles[i]
        high = nibbles[i + 1] if i + 1 < len(nibbles) else 0
        packed.append(low | high << 4)
    return bytes(packed), old, older, error


def encode_block(samples, old=0, older=0):
    """The best 16-byte block for up to 28 samples, and the decoder
    state after it. Flags are left at zero for the caller to set."""
    block = list(samples[:BLOCK_SAMPLES])
    if len(block) < BLOCK_SAMPLES:
        block += [0] * (BLOCK_SAMPLES - len(block))

    best = None
    for filter_index in range(len(FILTERS)):
        for shift in SHIFT_RANGE:
            packed, new_old, new_older, error = _encode_block(
                block, old, older, shift, filter_index)
            if best is None or error < best[0]:
                best = (error, packed, new_old, new_older, shift, filter_index)
            if not error:
                break
        if best and not best[0]:
            break
    _error, packed, new_old, new_older, shift, filter_index = best
    return (bytes((filter_index << 4 | shift, 0)) + packed,
            new_old, new_older)


def encode(samples, loop_start=None, repeat=False):
    """A whole waveform as SPU ADPCM.

    `loop_start` is the sample the sound returns to when it sustains;
    `repeat` says whether it sustains at all. A waveform always opens
    with the silent block the hardware expects to land on, which is what
    every waveform on this disc does and what makes a loop point land
    where the size table says it does."""
    data = bytearray()
    old = older = 0
    count = max(1, -(-len(samples) // BLOCK_SAMPLES))
    loop_block = (None if loop_start is None
                  else min(count - 1, max(0, loop_start // BLOCK_SAMPLES)))

    for index in range(count):
        chunk = samples[index * BLOCK_SAMPLES:(index + 1) * BLOCK_SAMPLES]
        block, old, older = encode_block(chunk, old, older)
        block = bytearray(block)
        if loop_block is not None and index == loop_block:
            block[1] |= LOOP
        if index == count - 1:
            block[1] |= END
            if repeat:
                block[1] |= REPEAT
        data += block
    return bytes(data)


def from_wav(blob):
    """(samples, rate) out of a PCM .wav, mixed down to one channel.

    Only what the SPU can actually play is accepted - 8 or 16 bit PCM -
    because anything else would have to be guessed at, and a sound that
    silently comes out wrong is worse than one that refuses to load."""
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError("That isn't a WAV file.")
    at = 12
    fmt = None
    while at + 8 <= len(blob):
        tag = blob[at:at + 4]
        size = struct.unpack_from("<I", blob, at + 4)[0]
        body = blob[at + 8:at + 8 + size]
        if tag == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", body, 0)
        elif tag == b"data" and fmt:
            encoding, channels, rate, _bps, _align, bits = fmt
            if encoding not in (1, 0xFFFE):
                raise ValueError(
                    "That WAV is compressed. Save it as plain PCM.")
            if bits not in (8, 16):
                raise ValueError(
                    f"That WAV is {bits}-bit. Save it as 8- or 16-bit PCM.")
            return _mono(body, channels, bits), rate
        at += 8 + size + (size & 1)
    raise ValueError("That WAV has no audio in it.")


def _mono(body, channels, bits):
    if bits == 8:
        values = [(b - 128) << 8 for b in body]
    else:
        count = len(body) // 2
        values = list(struct.unpack_from("<%dh" % count, body, 0))
    if channels <= 1:
        return values
    out = []
    for i in range(0, len(values) - channels + 1, channels):
        out.append(sum(values[i:i + channels]) // channels)
    return out


def resample(samples, rate, target):
    """Linear resampling. Good enough for a sound effect, and the SPU
    resamples again on playback anyway when a tone is keyed off its
    centre note."""
    if not samples or rate == target or not rate:
        return list(samples)
    count = max(1, int(len(samples) * target / rate))
    out = []
    for i in range(count):
        position = i * rate / target
        left = int(position)
        if left + 1 >= len(samples):
            out.append(samples[-1])
            continue
        fraction = position - left
        out.append(int(samples[left] * (1 - fraction)
                       + samples[left + 1] * fraction))
    return out

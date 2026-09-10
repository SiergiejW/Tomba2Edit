"""Writing decoded audio out as a file.

Everything in the tool that plays audio already has it as a WAV in
memory, so saving one is a write. MP3 needs an encoder, and there are
two worth trying in this order:

    lameenc     a LAME build that pip installs as a wheel. It has no
                outside dependencies, which is the point: it still works
                in a frozen build on a machine with nothing installed.
    ffmpeg      on PATH. Covers the case where the wheel is missing but
                the user has ffmpeg anyway.

If neither is there, saying so beats writing a WAV with an .mp3 on the
end and letting the user find out later.
"""
import os
import struct
import subprocess
import wave

BITRATE = 192
FFMPEG = "ffmpeg"


def parse_wav(data):
    """(pcm, rate, channels) out of WAV bytes."""
    import io

    with wave.open(io.BytesIO(data)) as w:
        return (w.readframes(w.getnframes()), w.getframerate(),
                w.getnchannels())


def load_wav(path):
    """([int16 samples...], rate, channels) from a WAV file on disk -
    the read side of parse_wav, for importing a replacement clip."""
    import array

    with open(path, "rb") as f:
        pcm, rate, channels = parse_wav(f.read())
    samples = array.array("h")
    # A WAV can be 8-bit or float PCM too; only 16-bit is handled here,
    # which is what every WAV this app writes and most tools default to.
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples.frombytes(pcm)
    return list(samples), rate, channels


def to_mono(samples, channels):
    """Average multi-channel frames down to one - VOICE.XA is mono."""
    if channels <= 1 or not samples:
        return list(samples)
    out = []
    for i in range(0, len(samples) - channels + 1, channels):
        out.append(sum(samples[i:i + channels]) // channels)
    return out


def resample(samples, from_rate, to_rate):
    """Linear resample - good enough for a spoken line, not a music
    mastering tool. A no-op when the rates already match."""
    if from_rate == to_rate or not samples or from_rate <= 0:
        return list(samples)
    ratio = to_rate / from_rate
    n = max(1, int(len(samples) * ratio))
    last = len(samples) - 1
    out = []
    for i in range(n):
        pos = i / ratio
        lo = int(pos)
        hi = min(lo + 1, last)
        frac = pos - lo
        out.append(int(samples[lo] * (1 - frac) + samples[hi] * frac))
    return out


def have_mp3():
    """Whether an MP3 encoder can be reached at all."""
    try:
        import lameenc                                  # noqa: F401
        return True
    except ImportError:
        pass
    return _ffmpeg_path() is not None


def _ffmpeg_path():
    from shutil import which

    return which(FFMPEG)


def to_mp3(wav_data, bitrate=BITRATE):
    """WAV bytes to MP3 bytes, or raise RuntimeError."""
    pcm, rate, channels = parse_wav(wav_data)
    try:
        import lameenc
    except ImportError:
        lameenc = None
    if lameenc is not None:
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(bitrate)
        encoder.set_in_sample_rate(rate)
        encoder.set_channels(channels)
        encoder.set_quality(2)
        return bytes(encoder.encode(pcm)) + bytes(encoder.flush())

    if _ffmpeg_path():
        done = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "wav",
             "-i", "pipe:0", "-b:a", f"{bitrate}k", "-f", "mp3", "pipe:1"],
            input=wav_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode == 0 and done.stdout:
            return done.stdout
        raise RuntimeError(
            done.stderr.decode("utf-8", "replace").strip() or "ffmpeg failed")

    raise RuntimeError(
        "No MP3 encoder available. Install one with \"pip install lameenc\", "
        "or put ffmpeg on PATH. Saving as WAV needs neither.")


def save(path, wav_data, bitrate=BITRATE):
    """Write WAV bytes to `path`, encoding to MP3 if that is the suffix."""
    if os.path.splitext(path)[1].lower() == ".mp3":
        payload = to_mp3(wav_data, bitrate)
    else:
        payload = wav_data
    with open(path, "wb") as f:
        f.write(payload)
    return path


def safe_name(text, fallback="audio"):
    """A file name a user's label can be dropped into."""
    keep = []
    for ch in text.strip():
        if ch.isalnum() or ch in " -_.,()[]'":
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip(" .")
    while "__" in out:
        out = out.replace("__", "_")
    return out[:100] or fallback

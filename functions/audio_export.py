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

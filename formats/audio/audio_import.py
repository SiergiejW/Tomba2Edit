"""Reading someone's audio file in, to put it on the disc.

A piece of music arrives as whatever the user has - a WAV out of an
editor, an MP3 - at whatever rate, and the disc wants 37800 Hz stereo.

    load()        the file as 16-bit frames and its rate. A plain PCM WAV
                  is read directly; anything else goes through Qt's
                  decoder, which the app has anyway for playback, and
                  ffmpeg is tried if that fails and there is one on PATH
    resample()    to another rate through a windowed sinc. Going down
                  (44100 to 37800) it filters at the new Nyquist first,
                  which is the difference between this and
                  audio_export.resample()'s straight line: fine for a
                  spoken line, audible on cymbals

All of it in numpy, so nothing has to be installed for it.
"""
import os
import subprocess
import wave

import numpy as np

EXTENSIONS = ("wav", "mp3", "ogg", "flac", "m4a", "aac", "wma", "opus")
FILTER = ("Audio (" + " ".join(f"*.{e}" for e in EXTENSIONS) + ");;All files (*)")


class AudioImportError(ValueError):
    """Raised when a file can't be read as audio."""


def _wav(path):
    with wave.open(path, "rb") as w:
        if w.getcomptype() != "NONE":
            raise AudioImportError("compressed WAV")
        width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 1:
        data = (np.frombuffer(raw, np.uint8).astype(np.int16) - 128) << 8
    elif width == 2:
        data = np.frombuffer(raw, "<i2")
    elif width == 3:
        b = np.frombuffer(raw[:len(raw) // 3 * 3], np.uint8).reshape(-1, 3)
        data = (b[:, 1].astype(np.int16) | (b[:, 2].astype(np.int8).astype(np.int16) << 8))
    elif width == 4:
        data = (np.frombuffer(raw, "<i4") >> 16).astype(np.int16)
    else:
        raise AudioImportError(f"{width * 8}-bit WAV")
    return data[:len(data) // channels * channels].reshape(-1, channels).astype(np.int16), rate


def _qt(path):
    """Through QAudioDecoder, with a local event loop. Needs a Qt
    application object, which the editor always has."""
    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PyQt6.QtMultimedia import QAudioDecoder, QAudioFormat

    if QCoreApplication.instance() is None:
        raise AudioImportError("no Qt application")
    decoder = QAudioDecoder()
    wanted = QAudioFormat()
    wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    decoder.setAudioFormat(wanted)                      # rate and channels stay the file's
    decoder.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
    loop = QEventLoop()
    chunks, failed, shape = [], [], {}

    def take():
        buffer = decoder.read()
        if not buffer.isValid():
            return
        made = buffer.format()
        shape.setdefault("rate", made.sampleRate())
        shape.setdefault("channels", made.channelCount())
        raw = buffer.constData().asstring(buffer.byteCount())
        kind = made.sampleFormat()
        if kind == QAudioFormat.SampleFormat.Int16:
            data = np.frombuffer(raw, "<i2")
        elif kind == QAudioFormat.SampleFormat.Int32:
            data = (np.frombuffer(raw, "<i4") >> 16).astype(np.int16)
        elif kind == QAudioFormat.SampleFormat.Float:
            data = (np.clip(np.frombuffer(raw, "<f4"), -1, 1) * 32767).astype(np.int16)
        elif kind == QAudioFormat.SampleFormat.UInt8:
            data = (np.frombuffer(raw, np.uint8).astype(np.int16) - 128) << 8
        else:
            failed.append("an unknown sample format")
            return
        chunks.append(data.copy())

    decoder.bufferReady.connect(take)
    decoder.finished.connect(loop.quit)
    decoder.error.connect(lambda *_: (failed.append(decoder.errorString() or "decoder error"), loop.quit()))
    # A decoder that neither finishes nor fails would hang the window.
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: (failed.append("the decoder stopped answering"), loop.quit()))
    watchdog.start(120000)
    decoder.start()
    loop.exec()
    watchdog.stop()
    decoder.stop()
    if failed or not chunks:
        raise AudioImportError(failed[0] if failed else "nothing was decoded")
    channels = max(1, shape["channels"])
    data = np.concatenate(chunks)
    return data[:len(data) // channels * channels].reshape(-1, channels), shape["rate"]


def _ffmpeg(path):
    from shutil import which

    if which("ffmpeg") is None:
        raise AudioImportError("no ffmpeg on PATH")
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-f", "s16le",
         "-acodec", "pcm_s16le", "-ac", "2", "-ar", "44100", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode or not done.stdout:
        raise AudioImportError(done.stderr.decode("utf-8", "replace").strip() or "ffmpeg failed")
    return np.frombuffer(done.stdout[:len(done.stdout) // 4 * 4], "<i2").reshape(-1, 2), 44100


def load(path):
    """(int16 frames shaped (n, channels), rate) from an audio file."""
    if not os.path.isfile(path):
        raise AudioImportError(f"{os.path.basename(path)} is not there.")
    tried = []
    readers = ([_wav] if path.lower().endswith(".wav") else []) + [_qt, _ffmpeg]
    for reader in readers:
        try:
            frames, rate = reader(path)
        except (AudioImportError, wave.Error, EOFError, OSError, ImportError) as exc:
            tried.append(str(exc))
            continue
        if len(frames) and rate > 0:
            return frames, rate
        tried.append("no audio in it")
    raise AudioImportError(
        f"{os.path.basename(path)} could not be read as audio ({'; '.join(t for t in tried if t)}). "
        "A 16-bit WAV always works.")


def stereo(frames):
    """Two channels: mono on both sides, anything wider cut to its first two."""
    if frames.shape[1] == 1:
        return np.repeat(frames, 2, axis=1)
    return frames[:, :2]


PHASES = 1024


def resample(frames, from_rate, to_rate, taps=24):
    """`frames` (n, channels) at another rate, by a Kaiser-windowed sinc
    `taps` zero crossings either side.

    An output sample falls between two source samples at one of a few
    fractions - six of them going from 44100 to 37800 - so the filter is
    worked out once for each (for up to PHASES of them; an odd ratio is
    rounded to the nearest) and the rest is a table and a sum."""
    if from_rate == to_rate or not len(frames):
        return frames
    from math import gcd

    up, down = to_rate // gcd(to_rate, from_rate), from_rate // gcd(to_rate, from_rate)
    cutoff = min(1.0, up / down) * 0.97                 # of the source's Nyquist
    half = int(np.ceil(taps / cutoff))
    count = len(frames) * up // down
    phases = min(up, PHASES)
    offsets = np.arange(-half + 1, half + 1)
    distance = offsets[None, :] - (np.arange(phases) / phases)[:, None]
    window = np.i0(8.6 * np.sqrt(np.clip(1 - (distance / half) ** 2, 0, 1))) / np.i0(8.6)
    table = cutoff * np.sinc(cutoff * distance) * window
    table /= table.sum(axis=1, keepdims=True)           # unity at DC, whatever the phase
    source = np.concatenate([np.zeros((half, frames.shape[1]), np.float32), frames.astype(np.float32),
                             np.zeros((half + 1, frames.shape[1]), np.float32)])
    table = table.astype(np.float32)
    out = np.empty((count, frames.shape[1]), np.int16)
    for start in range(0, count, 1 << 15):
        n = np.arange(start, min(count, start + (1 << 15)), dtype=np.int64)
        base = n * down // up
        if phases == up:
            phase = n * down % up
        else:
            phase = np.minimum(phases - 1, np.rint((n * down % up) * (phases / up)).astype(np.int64))
        windows = source[(base + half)[:, None] + offsets[None, :]]     # (n, taps, channels)
        made = np.einsum("ntc,nt->nc", windows, table[phase])
        out[start:start + len(n)] = np.clip(np.rint(made), -32768, 32767)
    return out


def trim_end(frames, floor=24):
    """Without the silence a file ends on: a loop that waits out two
    seconds of nothing is not a loop. `floor` is the loudest sample that
    still counts as silence."""
    loud = np.nonzero(np.abs(frames).max(axis=1) > floor)[0]
    return frames[:loud[-1] + 1] if len(loud) else frames[:0]


def fade_out(frames, length):
    """The last `length` frames brought down to nothing."""
    length = min(length, len(frames))
    if length <= 0:
        return frames
    out = frames.astype(np.float64)
    out[-length:] *= np.linspace(1, 0, length)[:, None]
    return np.rint(out).astype(np.int16)

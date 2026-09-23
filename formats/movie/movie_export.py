"""Writing a movie back out: stills, a PNG sequence, the soundtrack, a
playable video file, and the raw sectors.

The video writer needs ffmpeg, and only that one does. Everything else
here is a plain file write, which matters because a frozen build on a
machine with nothing installed should still be able to get the frames
and the audio out - see formats/audio/audio_export.py, which draws the same
line for MP3.
"""
import os
import subprocess

from formats.audio import audio_export

FFMPEG = "ffmpeg"

# What a video export can be written as. MP4 is the one to hand someone;
# AVI with a lossless stream is the one to take into an editor.
#
# The disc's audio is 37800 Hz, which AAC cannot carry - left to itself
# ffmpeg drops it to the nearest rate it has, 32000, and takes the top
# of the sound with it. Resampling up to 44100 instead keeps all of it.
# MKV and AVI carry 37800 as it is, so neither says anything about rate.
FORMATS = {
    ".mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
             "-c:a", "aac", "-b:a", "192k", "-ar", "44100"],
    ".mkv": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
             "-c:a", "flac"],
    ".avi": ["-c:v", "ffv1", "-c:a", "pcm_s16le"],
}


class ExportError(Exception):
    """Raised when an export cannot be written."""


def have_ffmpeg():
    from shutil import which

    return which(FFMPEG) is not None


def save_png(path, rgb):
    """One frame as a PNG."""
    from PIL import Image

    Image.fromarray(rgb).save(path)
    return path


def safe_name(text):
    return audio_export.safe_name(text, fallback="movie")


def write_video(path, movie, frames, wav=None, progress=None):
    """Write `frames` - an iterable of (h, w, 3) uint8 arrays - as a
    video file, with `wav` as its soundtrack if there is one.

    The frames go to ffmpeg down a pipe as raw RGB rather than through a
    folder of PNGs: a 24 MB movie is 1200 frames, and writing them all
    out first only to read them straight back costs a couple of hundred
    megabytes of disc for nothing.

    The audio cannot go down the same pipe, so it is written to a
    temporary WAV beside the output and given to ffmpeg as a second
    input."""
    if not have_ffmpeg():
        raise ExportError(
            "Saving a video needs ffmpeg, and there isn't one on PATH. "
            "Saving the frames as PNGs and the sound as a WAV needs "
            "nothing, and loses none of the movie.")
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in FORMATS:
        raise ExportError(f"{suffix or 'that'} is not a format this writes - "
                          f"use one of {', '.join(sorted(FORMATS))}.")

    import tempfile

    audio_path = None
    try:
        command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{movie.width}x{movie.height}",
                   "-r", f"{movie.fps:.6f}", "-i", "pipe:0"]
        if wav:
            handle, audio_path = tempfile.mkstemp(suffix=".wav")
            with os.fdopen(handle, "wb") as f:
                f.write(wav)
            command += ["-i", audio_path]
        command += FORMATS[suffix]
        if wav:
            command += ["-shortest"]
        command += [path]

        done = subprocess.Popen(
            command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for number, rgb in enumerate(frames):
                done.stdin.write(rgb.tobytes())
                if progress is not None and not progress(number):
                    done.stdin.close()
                    done.terminate()
                    raise ExportError("Cancelled.")
            done.stdin.close()
        except BrokenPipeError:
            pass                        # ffmpeg died; its stderr says why
        errors = done.stderr.read().decode("utf-8", "replace").strip()
        if done.wait() != 0:
            raise ExportError(errors or "ffmpeg failed")
        return path
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass


def write_sequence(folder, stem, frames, progress=None):
    """Every frame as its own PNG, numbered. Returns how many landed."""
    from PIL import Image

    os.makedirs(folder, exist_ok=True)
    written = 0
    for number, rgb in enumerate(frames):
        Image.fromarray(rgb).save(
            os.path.join(folder, f"{stem}_{number:05d}.png"))
        written += 1
        if progress is not None and not progress(number):
            break
    return written


def write_str(path, movie):
    """The movie's own sectors, unchanged."""
    with open(path, "wb") as f:
        f.write(movie.sectors_bytes())
    return path

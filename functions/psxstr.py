"""The STR movies: finding them, and taking them apart.

Tomba 2 has three - LOGO.STR, OP.STR and END.STR, in the disc's MOVIE
folder. An STR is not a container in any general sense; it is a run of
CD sectors written in the order the drive will read them, with the video
and the audio interleaved through each other so that neither has to be
buffered. Seven video sectors, one audio sector, over and over.

A video sector carries a 32-byte header saying which frame it belongs to
and how many sectors that frame spans, then 2016 bytes of the frame's
bitstream. Putting a frame back together is concatenating its sectors'
2016-byte pieces; decoding it is functions/mdec.py.

An audio sector is ordinary CD-XA - the same Form 2 ADPCM as the music
and the voice - so functions/xa.py decodes it unchanged.

Two sources work, and they are not equally good:

    a bin/cue data track     whole 2352-byte sectors. Both the video and
                             the audio survive, which is what you want.
    an extracted MOVIE\\*.STR 2048 bytes a sector, because that is what
                             copying a file off a CD gives you. The video
                             is untouched - it only ever used 2048 of the
                             sector - but the audio sectors held 2324
                             bytes and 276 of every one of them is gone.
                             Video only, from these.
"""
import os
import struct

from functions import mdec, xa

# The magic on a video sector's header: 0x0160, then 0x8001 for video.
VIDEO_MAGIC = 0x80010160
SECTOR_HEADER = 32
FLAT_SECTOR = 2048          # what an extracted STR has
FORM1_PAYLOAD = 24          # sync + header + subheader, in a raw sector

# The movies, in the order they are shown.
NAMES = ("LOGO.STR", "OP.STR", "END.STR")

# The drive plays a movie at double speed: 150 sectors a second. Used to
# work out the frame rate when a movie has no audio to measure against.
SECTORS_PER_SECOND = 150.0


class StrError(Exception):
    """Raised when a file cannot be read as an STR."""


class Movie:
    """One STR, indexed but not yet decoded.

    Indexing walks every sector once, which is a second or so for the
    24 MB END.STR, and after it a frame can be decoded on its own -
    every frame in an STR stands alone, so seeking is free."""

    def __init__(self, name, path, lba, count, raw):
        self.name = name
        self.path = path
        self.lba = lba              # 0 for an extracted file
        self.count = count          # sectors
        self.raw = raw              # 2352-byte sectors, audio included
        self.width = 0
        self.height = 0
        self.version = 0
        self.frames = []            # [[sector index, ...], ...]
        self.audio_sectors = []
        self.rate = 37800
        self.channels = 2
        self._index()

    # --- reading sectors ------------------------------------------------

    def _sector(self, handle, index):
        if self.raw:
            handle.seek((self.lba + index) * xa.SECTOR)
            return handle.read(xa.SECTOR)
        handle.seek(index * FLAT_SECTOR)
        return handle.read(FLAT_SECTOR)

    @staticmethod
    def _payload(sector, raw):
        """The 2048 bytes of user data in a sector, however it is framed."""
        if raw:
            return sector[FORM1_PAYLOAD:FORM1_PAYLOAD + FLAT_SECTOR]
        return sector

    def _index(self):
        """Walk the movie once and note where every frame's sectors are.

        Frames are keyed by the number in their sector headers rather
        than by counting: a movie that is cut short mid-frame, or one
        whose last frame is short a sector, should still list the frames
        that are whole."""
        frames = {}
        order = []
        with open(self.path, "rb") as f:
            for index in range(self.count):
                sector = self._sector(f, index)
                if len(sector) < (xa.SECTOR if self.raw else FLAT_SECTOR):
                    break
                if self.raw:
                    submode = sector[xa.SUBHEADER + 2]
                    if submode & 0x24 == 0x24:          # Form 2 audio
                        self.audio_sectors.append(index)
                        self.rate, self.channels = _coding(
                            sector[xa.SUBHEADER + 3])
                        continue
                payload = self._payload(sector, self.raw)
                if len(payload) < SECTOR_HEADER:
                    continue
                magic, chunk, chunks, number, size, width, height = \
                    struct.unpack_from("<IHHIIHH", payload, 0)
                if magic != VIDEO_MAGIC:
                    continue
                if not self.width:
                    self.width, self.height = width, height
                    header = mdec.frame_header(payload[SECTOR_HEADER:])
                    self.version = header[2] if header else 0
                if number not in frames:
                    frames[number] = [None] * chunks
                    order.append(number)
                pieces = frames[number]
                if chunk < len(pieces):
                    pieces[chunk] = index
        self.frames = [frames[n] for n in order
                       if all(piece is not None for piece in frames[n])]

    # --- what the movie is ----------------------------------------------

    @property
    def has_audio(self):
        return bool(self.audio_sectors)

    @property
    def audio_samples(self):
        """Samples per channel in the movie's audio."""
        return (len(self.audio_sectors) * xa.SAMPLES_PER_SECTOR
                // max(self.channels, 1))

    @property
    def fps(self):
        """Frames a second.

        Measured against the audio where there is any: the movie is as
        long as its soundtrack, and dividing the frames by that is the
        only figure that keeps the two together in an export. Without
        audio there is nothing to measure against, so it falls back to
        the rate the drive reads at."""
        if not self.frames:
            return 0.0
        if self.has_audio and self.audio_samples:
            return len(self.frames) / (self.audio_samples / self.rate)
        return len(self.frames) * SECTORS_PER_SECOND / max(self.count, 1)

    @property
    def duration(self):
        """Seconds."""
        return len(self.frames) / self.fps if self.fps else 0.0

    # --- getting the pictures and the sound out -------------------------

    def frame_bytes(self, number):
        """The demuxed bitstream of frame `number`, header and all."""
        pieces = self.frames[number]
        out = bytearray()
        with open(self.path, "rb") as f:
            for index in pieces:
                payload = self._payload(self._sector(f, index), self.raw)
                out += payload[SECTOR_HEADER:]
        # The header says how much of that is really the frame; the rest
        # is whatever was in the sector when it was written.
        size = struct.unpack_from("<I", self._first_header(pieces), 0x0C)[0]
        return bytes(out[:size]) if 0 < size <= len(out) else bytes(out)

    def _first_header(self, pieces):
        with open(self.path, "rb") as f:
            return self._payload(self._sector(f, pieces[0]), self.raw)

    def frame(self, number):
        """Frame `number` as an (h, w, 3) uint8 RGB array."""
        return mdec.decode(self.frame_bytes(number), self.width, self.height)

    def wav(self):
        """The movie's soundtrack as WAV bytes, or None if it has none."""
        if not self.has_audio:
            return None
        with open(self.path, "rb") as f:
            samples, rate, speakers = xa.decode_channel(
                f, self.lba, self.audio_sectors)
        return xa.wav_bytes(samples, rate, speakers)

    def sectors_bytes(self):
        """The movie's own sectors, as they sit on the disc.

        A raw copy, so what comes out of a BIN is a 2352-byte STR that
        ffmpeg and the emulators read directly."""
        stride = xa.SECTOR if self.raw else FLAT_SECTOR
        with open(self.path, "rb") as f:
            f.seek(self.lba * stride if self.raw else 0)
            return f.read(self.count * stride)


def _coding(byte):
    """(rate, channels) from an XA sector's coding byte."""
    channels, rate, _bits = xa.coding(byte)
    return rate, channels


def find(source):
    """Every movie in `source`, as [Movie, ...].

    `source` is a bin/cue data track, or a folder - either the disc's
    root with a MOVIE in it, or the MOVIE folder itself."""
    if source and os.path.isdir(source):
        return _from_folder(source)
    return _from_image(source)


def _from_folder(folder):
    """Movies out of an extracted CD folder. Video only - see the module
    docstring on what a 2048-byte extraction costs."""
    places = [folder, os.path.join(folder, "MOVIE")]
    out = []
    for place in places:
        if not os.path.isdir(place):
            continue
        listing = {name.upper(): name for name in os.listdir(place)}
        for wanted in NAMES:
            name = listing.get(wanted)
            if not name:
                continue
            path = os.path.join(place, name)
            size = os.path.getsize(path)
            if size % FLAT_SECTOR:
                continue
            try:
                out.append(Movie(wanted, path, 0, size // FLAT_SECTOR, False))
            except (OSError, struct.error) as e:
                raise StrError(f"{wanted}: {e}") from e
        if out:
            break
    return [movie for movie in out if movie.frames]


def _from_image(image):
    """Movies out of a raw disc track, audio and all."""
    from functions import voice

    if not image or not os.path.isfile(image):
        return []
    out = []
    for wanted in NAMES:
        where = voice.find_file(image, wanted)
        if not where:
            continue
        lba, sectors = where
        try:
            out.append(Movie(wanted, image, lba, sectors, True))
        except (OSError, struct.error) as e:
            raise StrError(f"{wanted}: {e}") from e
    return [movie for movie in out if movie.frames]

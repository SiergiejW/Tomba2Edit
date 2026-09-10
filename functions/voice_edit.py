"""Staging replacement voice audio until an explicit export writes it.

VOICE.XA has no filenames inside it to hand to bin_writer.patch_track -
a clip is only a run of sector positions the overlay's table names - so
edits are kept here as {absolute sector: rebuilt sector bytes} instead,
and written into a copy of the disc image by bin_writer.patch_sectors.
Same "edit in memory, write on Save/Export" shape as the rest of the
app's editors, just keyed by position rather than by name.
"""
from functions import bin_writer, xa


class VoiceEditStore:
    def __init__(self):
        self.image = None
        self.sectors = {}          # absolute lba -> raw 2352 bytes

    def set_image(self, path):
        """Opening a different disc starts over - a staged sector's
        position means nothing on a different image."""
        if path != self.image:
            self.image = path
            self.sectors.clear()

    def stage_clip(self, image_path, indices, samples):
        """Encode `samples` into exactly len(indices) sectors and stage
        them at their absolute positions (already lba + block offsets,
        as functions.voice.clip_sectors / xa.channel_map give them -
        not relative to anything else).

        `samples` must already be the length the caller wants written -
        padded or cut - since only the GUI knows whether the user should
        be asked about that. Returns how many sectors were staged."""
        self.set_image(image_path)
        needed = len(indices) * xa.SAMPLES_PER_SECTOR
        if len(samples) < needed:
            samples = list(samples) + [0] * (needed - len(samples))
        else:
            samples = samples[:needed]
        state = None
        with open(image_path, "rb") as f:
            for n, lba in enumerate(indices):
                original = self.sectors.get(lba)
                if original is None:
                    f.seek(lba * xa.SECTOR)
                    original = f.read(xa.SECTOR)
                    if len(original) != xa.SECTOR:
                        raise ValueError(f"Sector {lba} is past the end "
                                        "of the disc image.")
                chunk = samples[n * xa.SAMPLES_PER_SECTOR:
                                (n + 1) * xa.SAMPLES_PER_SECTOR]
                sector, state = xa.encode_full_sector(original, chunk, state)
                self.sectors[lba] = sector
        return len(indices)

    def count(self):
        return len(self.sectors)

    def clear(self):
        self.sectors.clear()

    def export(self, destination, progress=None):
        if not self.sectors:
            raise ValueError("No voice edits staged.")
        return bin_writer.patch_sectors(self.image, destination,
                                        self.sectors, progress)

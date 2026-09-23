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

    # -- keeping them in a project -------------------------------------
    #
    # Everything else the editor stages ends up inside TOMBA2.DAT or
    # TOMBA2.IMG, so saving those saves the work. Voice does not: a
    # staged clip is raw sectors aimed at positions on the disc image,
    # which no file in the project holds. Without this, re-recording a
    # line was the one edit you could not put down and pick up again.
    #
    # The sectors go out as one blob with an index beside it rather than
    # as JSON: they are 2352 bytes each and a re-recorded scene is
    # hundreds of them, which base64 would inflate by a third for no
    # reason. The disc's digest travels too, because a sector position
    # means nothing on a different image - see functions/source_disc.

    def to_files(self, index_path, blob_path, disc_digest=None):
        """Write the staged sectors out. Returns how many were written."""
        import json
        import os

        order = sorted(self.sectors)
        with open(blob_path, "wb") as f:
            for lba in order:
                f.write(self.sectors[lba])
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump({"sector_size": xa.SECTOR,
                       "disc_digest": disc_digest,
                       "image": os.path.basename(self.image or ""),
                       "sectors": order}, f)
        return len(order)

    def from_files(self, index_path, blob_path, image=None,
                   disc_digest=None):
        """Read staged sectors back in.

        Returns (loaded, rejected_reason). A mismatched disc loads
        nothing and says why: writing a sector aimed at one image into
        another one corrupts whatever happens to live there instead,
        and the result plays as noise rather than failing loudly."""
        import json

        try:
            with open(index_path, encoding="utf-8") as f:
                index = json.load(f)
            with open(blob_path, "rb") as f:
                blob = f.read()
        except (OSError, ValueError) as exc:
            return 0, f"the staged voice couldn't be read ({exc})"

        wanted = index.get("disc_digest")
        if wanted and disc_digest and wanted != disc_digest:
            return 0, ("it was recorded against a different disc image "
                       f"({index.get('image') or 'unknown'})")

        order = index.get("sectors") or []
        size = index.get("sector_size") or xa.SECTOR
        if len(blob) < len(order) * size:
            return 0, "the staged voice is truncated"

        self.image = image or self.image
        for n, lba in enumerate(order):
            self.sectors[int(lba)] = blob[n * size:(n + 1) * size]
        return len(order), None

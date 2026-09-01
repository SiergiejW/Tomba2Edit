"""One section of a disc's names, held for a panel.

Each audio panel owns one of these. It knows which disc is open, which
section of that disc's name file it is looking at, and writes the file
back as soon as something is renamed - there is no Save button for
names, because losing a rename to a forgotten one would be worse than
the occasional extra write.
"""
from functions import labels


class NameStore:
    """The names for one panel: load, read, rename, write back."""

    def __init__(self, section):
        self.section = section
        self.disc = None
        self._all = labels.load(None)

    def load(self, image_path):
        """Pick up the names belonging to the disc being opened."""
        self.disc = labels.disc_id(image_path)
        self._all = labels.load(self.disc)
        return self.disc

    def names(self):
        return self._all.get(self.section, {})

    def get(self, key):
        return self.names().get(key, "")

    def rename(self, key, name):
        """Set or clear one name and write the file. Returns its path.

        The file is re-read first because every panel keeps one of these
        and they all write the same file: holding a copy from open time
        and writing it back would undo whatever another tab renamed in
        the meantime. It also means a file edited by hand is respected
        rather than overwritten."""
        self._all = labels.load(self.disc)
        section = self._all.setdefault(self.section, {})
        if name:
            section[key] = name
        else:
            section.pop(key, None)
        return labels.save(self.disc, self._all)

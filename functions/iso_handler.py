"""
ISO Handler module for Tomba2Edit
Extracts TOMBA2.DAT, TOMBA2.IDX and TOMBA2.IMG from a PlayStation disc image
so they can be browsed/edited the same way as files picked from a folder.

The actual ISO9660 parsing (sector-layout detection, PVD/directory
decoding) lives in functions/iso9660.py, shared with iso_builder.py so both
modules agree on how to read a disc.
"""

import os
import shutil
import tempfile

from functions.iso9660 import ISO9660Reader, ISOFormatError

REQUIRED_FILES = ("TOMBA2.DAT", "TOMBA2.IDX", "TOMBA2.IMG")

# Not required to open the disc - extracted (and later, on export, patched
# back in) when present, but their absence never blocks opening an ISO
# that's otherwise a valid Tomba! 2 disc. SOP.BIN lives in a BIN/
# subfolder, not at the root - find_files() searches subfolders too, so
# no path is needed here.
OPTIONAL_FILES = ("MAIN.EXE", "SOP.BIN")

__all__ = ["ISOHandler", "ISOFormatError", "REQUIRED_FILES", "OPTIONAL_FILES"]


class ISOHandler:
    """Extracts files out of a PlayStation ISO9660 disc image."""

    def __init__(self):
        self.temp_dir = None
        self.extracted_files = {}
        # [{"name", "size"}, ...] for every file in the disc's BIN/
        # folder (area overlays, SOP.BIN, etc.) - metadata only, not
        # extracted to disk, since only SOP.BIN is ever actually read.
        self.bin_overlays = []

    def extract_iso(self, iso_path):
        """Extract TOMBA2.DAT, TOMBA2.IDX, TOMBA2.IMG (and MAIN.EXE, if
        present - see OPTIONAL_FILES) from the disc image at `iso_path`
        into a fresh temp directory, and return {filename: extracted_path}.
        Raises ISOFormatError / FileNotFoundError on failure (only for
        REQUIRED_FILES - a missing optional file is just absent from the
        returned dict); the temp directory is cleaned up automatically if
        it does."""
        self.cleanup()
        self.temp_dir = tempfile.mkdtemp(prefix="tomba2edit_")

        try:
            with open(iso_path, "rb") as f:
                raw = f.read()

            reader = ISO9660Reader(raw)
            wanted = set(REQUIRED_FILES) | set(OPTIONAL_FILES)
            locations = reader.find_files(reader.root_lba, reader.root_size, wanted)

            missing = [name for name in REQUIRED_FILES if name not in locations]
            if missing:
                raise FileNotFoundError(
                    "Couldn't find {} inside this ISO. Make sure it's an "
                    "unmodified Tomba! 2 disc image.".format(", ".join(missing))
                )

            files_found = {}
            for name in list(REQUIRED_FILES) + list(OPTIONAL_FILES):
                if name not in locations:
                    continue
                file_lba, file_size = locations[name]
                data = reader.read_file(file_lba, file_size)
                dest_path = os.path.join(self.temp_dir, name)
                with open(dest_path, "wb") as out:
                    out.write(data)
                files_found[name] = dest_path

            self.extracted_files = files_found

            bin_dir = next(
                (e for e in reader.list_directory(reader.root_lba, reader.root_size)
                 if e.is_dir and e.name.upper() == "BIN"),
                None,
            )
            if bin_dir is not None:
                self.bin_overlays = [
                    {"name": e.name.upper(), "size": e.size}
                    for e in reader.list_directory(bin_dir.lba, bin_dir.size)
                    if not e.is_dir
                ]

            return files_found

        except Exception:
            self.cleanup()
            raise

    def get_temp_dir(self):
        """Get the temporary directory path."""
        return self.temp_dir

    def get_file_path(self, filename):
        """Get the path of an extracted file."""
        return self.extracted_files.get(filename.upper())

    def cleanup(self):
        """Clean up temporary files."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"Warning: Could not clean up temp directory: {e}")
        self.temp_dir = None
        self.extracted_files = {}
        self.bin_overlays = []

"""Naming the files in TOMBA2.DAT.

Nothing on the disc carries a filename; the names come from a
label set scored against the IDX (game/labels.py). This is the
menu side of that: loading a set, renaming a row by hand, and
writing the result back out.
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from formats.archive.idx_parser import (
    apply_labels,
    apply_labels_flat,
    area_index_of,
    content_hashes,
    row_label_data)
from game import labels as labels_module


class LabelsMixin:
    """Naming the files in TOMBA2.DAT.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def load_labels_for_disc(self, idx_path):
        """Pick the names for the disc that has just been opened and put
        them on the tree. Called by idx_parser.parse_idx_file once the
        tree is built.

        A labels file loaded by hand stays in force - someone working on
        their own map doesn't want reopening the ISO to throw it away -
        but it's still scored against the disc, so a set of names that
        doesn't fit says so instead of quietly landing on the wrong
        files."""
        try:
            hashes = content_hashes(self)
            if self.labels_override is not None:
                self.labels = self.labels_override
                score = self.labels.score(hashes)
                source = "loaded"
            else:
                self.labels, score = labels_module.choose(hashes)
                source = "built-in"
        except Exception as e:
            print(f"Could not load labels: {e}")
            self.labels = None
            score, source = 0.0, "built-in"

        named = apply_labels(self)
        apply_labels_flat(self)
        # Relabel, never reload - the BINs tab may be holding a SOP.BIN
        # path from a disc that has since been closed and cleaned up.
        self.bins_viewer.set_descriptions(self.labels.bins if self.labels else None)
        self.voice_panel.set_area_labels(self.labels.bins if self.labels else None)
        self.builtin_labels_action.setEnabled(self.labels_override is not None)

        if self.labels is None:
            self.statusBar().showMessage(
                "No labels file matches this disc - files are listed by "
                f"address only (best match {score:.0%})", 15000)
            return
        fit = "" if score >= 0.999 else f", {score:.0%} of it found on this disc"
        # Rows, not files: the trail lists the same handful of files under
        # nearly every area, so 45 named files fill some 700 rows.
        self.statusBar().showMessage(
            f"Named {named} rows from the {source} labels for "
            f"\"{self.labels.name}\" ({self.labels.named} names){fit}", 15000)

    def rename_row(self, item, name):
        """A row was renamed in the tree. Names live in the labels file,
        not in the tree, so this writes it there and lets apply_labels
        redraw - which is also what makes the name survive switching to
        another file and back.

        A disc with no labels of its own gets an empty set made for it
        on the first rename. That is the way to start naming a build
        nobody has mapped: open it, type names in, File > Export
        Labels."""
        if self.labels is None:
            self.labels = labels_module.LabelSet(
                name=os.path.basename(os.path.dirname(self.dat_file or "")) or "Untitled",
                build="custom")
            self.labels_override = self.labels

        data = row_label_data(item)
        if data:
            stem, filetype, address, _detail, content = data
            size = (item.data(Qt.ItemDataRole.UserRole) or (None,) * 4)[3]
            end = address + size - 1 if isinstance(size, int) and size else 0
            self.labels.rename(content, name, kind=filetype, end=end, start=address)
        else:
            index = area_index_of(item)
            if index is None:
                return
            self.labels.rename_area(index, name)

        self.labels_dirty = True
        apply_labels(self)
        apply_labels_flat(self)
        self._refresh_edit_status()

    def export_labels_dialog(self):
        """Write the names currently on the tree out as a labels file."""
        if self.labels is None:
            QMessageBox.information(
                self, "Nothing to export",
                "No names are loaded or typed in yet. Rename something in the "
                "tree first, or import a labels file.")
            return
        suggested = os.path.join(
            labels_module.labels_dir(),
            f"{self.labels.build or 'custom'}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export labels", suggested, "Labels files (*.json)")
        if not path:
            return
        try:
            labels_module.save(self.labels, path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed", f"Couldn't write it:\n\n{e}")
            return
        self.labels.path = path
        self.labels_dirty = False
        self._refresh_edit_status()
        self.statusBar().showMessage(
            f"Exported {self.labels.named} names ({len(self.labels)} entries) "
            f"to {os.path.basename(path)}", 15000)

    def load_labels_dialog(self):
        """Load a labels file the user points at, and keep it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load labels file", labels_module.labels_dir(),
            "Labels files (*.json);;All files (*)")
        if not path:
            return
        try:
            label_set = labels_module.load(path)
        except labels_module.LabelError as e:
            QMessageBox.critical(self, "Not a labels file",
                                 f"Couldn't read that as a labels file:\n\n{e}")
            return

        self.labels_override = label_set
        self.labels_dirty = False
        if not self.dat_file:
            # Nothing open yet - it'll be applied when a disc is.
            self.statusBar().showMessage(
                f"Labels \"{label_set.name}\" loaded - open a disc to use them", 15000)
            self.builtin_labels_action.setEnabled(True)
            return
        idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
        self.load_labels_for_disc(idx_path)
        if self.labels.score(content_hashes(self)) < labels_module.MATCH_THRESHOLD:
            QMessageBox.warning(
                self, "Labels don't fit this disc",
                f"\"{label_set.name}\" names {len(label_set)} addresses and "
                "hardly any of them are in this disc's IDX. The names have "
                "been applied anyway - they are probably for a different "
                "build, or for one that hasn't been repacked yet.")

    def use_builtin_labels(self):
        """Drop a hand-loaded labels file and go back to whichever
        built-in one matches the open disc."""
        self.labels_override = None
        if not self.dat_file:
            self.labels = None
            self.builtin_labels_action.setEnabled(False)
            return
        self.load_labels_for_disc(
            os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX"))

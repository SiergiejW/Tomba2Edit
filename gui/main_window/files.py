"""Replacing, exporting and staging the bytes of one DAT entry.

Every edit anywhere in the tool arrives here in the end: staged
against its entry, checked against what else points at those
bytes, and held until a repack writes them.
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from gui.widgets.entry_picker import EntryPicker


class FileEditsMixin:
    """Replacing, exporting and staging the bytes of one DAT entry.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    # --- replacing a whole file's bytes ---------------------------------

    def _entry_of(self, item):
        """What a tree row's file is, as a dict the replace/swap code
        can act on, or None for a row that is not one.

        Two kinds, because the disc has two. A file in an AREA's data
        folder is a slot in that chunk's pointer table, and is named by
        (area, file index). A file in its TRAIL folder is named by the
        DAT address it starts at - the trail sits past every chunk and
        is shared between areas, which is where Tomba's own models live.
        Both go to formats/archive/repacker.py, which has a path for each."""
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not data:
            return None
        if data[0] == "trail":
            _tag, address, size, _dat_start = data
            return {"kind": "trail", "trail": address,
                    "address": address, "size": size, "key": ("trail", address)}
        where = item.data(Qt.ItemDataRole.UserRole + 2)
        if not where or not isinstance(data[0], int):
            return None
        _id, dat_start, offset, size = data
        chunk_index, file_index = where
        return {"kind": "sdat", "area": chunk_index, "file_idx": file_index,
                "address": dat_start + offset, "size": size,
                "key": (chunk_index, file_index)}

    def _entry_bytes(self, item):
        """What a tree row's file currently holds - the staged
        replacement if it has one, otherwise what is on the disc."""
        entry = self._entry_of(item)
        if entry is None or not self.dat_file:
            return None
        staged = self.pending_file_edits.get(entry["key"])
        if staged is not None:
            return staged["data"]
        with open(self.dat_file, "rb") as f:
            f.seek(entry["address"])
            return f.read(entry["size"])

    def _stage_file_edit(self, item, data, label):
        """Stage a whole-file replacement, or clear it when the bytes
        are what the disc already has."""
        entry = self._entry_of(item)
        if entry is None:
            return
        with open(self.dat_file, "rb") as f:
            f.seek(entry["address"])
            original = f.read(entry["size"])
        key = entry["key"]
        if data == original:
            self.pending_file_edits.pop(key, None)
        else:
            self.pending_file_edits[key] = dict(entry, data=data, label=label)
        self._colour_address(entry["address"],
                             "edited" if key in self.pending_file_edits else None)
        self._refresh_edit_status()
        self._refresh_open_model_views(entry["address"], data)

    def _refresh_open_model_views(self, address, data):
        """Redraw any 3D view already open on the model at `address`,
        for a staged edit that may have reached it from outside that
        view itself - a swap, an imported replacement, a texture
        migration. A paste or a clear made from inside the SMST tab
        already shows itself the moment it happens; this is what stops
        every OTHER way of changing the same bytes from leaving that
        tab (or the ANMP tab, which can embed the same model) showing a
        model that is no longer what is actually staged."""
        if self.smst_panel.refresh_if_showing(address, data):
            print(f"SMST: refreshed the model at 0x{address:X} with the edit")
        if self.anmp_viewer.reload_model(address):
            print(f"ANMP: reloaded the model at 0x{address:X} with the edit")

    def _confirm_replacement(self, item, data, source):
        """Ask before staging, saying what changes. A different size is
        the thing worth stopping on: the repacker will resize the DAT
        and move every pointer after it, which is fine for the disc, but
        the game may still expect the file it was built with."""
        entry = self._entry_of(item)
        size = entry["size"]
        difference = len(data) - size
        note = ("the same size" if not difference
                else f"{abs(difference)} byte(s) "
                     f"{'larger' if difference > 0 else 'smaller'}")
        warning = ""
        if entry["kind"] == "trail":
            where = f"TRAIL, 0x{entry['address']:X}, {size} bytes"
            shared = self._areas_using_trail(entry["address"])
            if len(shared) > 1:
                warning = (f"\n\nThis is a TRAIL file, and {len(shared)} areas "
                           f"share it ({', '.join(shared[:6])}"
                           f"{', ...' if len(shared) > 6 else ''}) - replacing "
                           f"it changes it in every one of them.")
        else:
            where = (f"AREA_{entry['area']:02X}, slot {entry['file_idx']}, "
                     f"{size} bytes")
        answer = QMessageBox.question(
            self, "Replace this file?",
            f"Replace the bytes of\n\n    {item.text()}\n\n"
            f"({where})\n\nwith {source} - {note}.{warning}\n\n"
            f"Nothing is written to the disc until you export; this is "
            f"staged like a text edit, and exporting rebuilds TOMBA2.DAT "
            f"and TOMBA2.IDX around the new size.")
        return answer == QMessageBox.StandardButton.Yes

    def _areas_using_trail(self, address):
        """Which areas list a trail file. The trail is shared - 13 of
        the retail disc's 53 files are in more than one area's trailer -
        so replacing one reaches every area that uses it."""
        from gui.level.level_scene import trail_files
        idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
        out = []
        try:
            for chunk in range(os.path.getsize(idx_path) // 0x800):
                if any(start == address
                       for start, _size in trail_files(idx_path, chunk)):
                    out.append(f"AREA_{chunk:02X}")
        except OSError:
            pass
        return out

    def import_selected_bytes(self, item=None):
        """Replace a file's bytes from a file on disk."""
        item = item if item is not None else self._selected_tree_item()
        if self._entry_of(item) is None:
            QMessageBox.warning(self, "Nothing to replace",
                                "Pick a file inside an AREA's data folder.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Bytes to put in {item.text()}", "",
            "Binary files (*.bin);;All files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            QMessageBox.critical(self, "Couldn't read that", str(e))
            return
        if self._confirm_replacement(item, data, os.path.basename(path)):
            self._stage_file_edit(item, data, os.path.basename(path))

    def swap_selected_bytes(self, item=None):
        """Replace a file with another entry's bytes.

        What a swap on this disc has to be. An IDX pointer is an offset
        inside its own AREA's chunk, so an entry cannot be pointed at
        another area's file - but its bytes can be copied over, and the
        repacker resizes the chunk around them. Which is how Tomba's
        second suit ends up where his first one was."""
        item = item if item is not None else self._selected_tree_item()
        entry = self._entry_of(item)
        if entry is None:
            QMessageBox.warning(self, "Nothing to replace",
                                "Pick a file inside an AREA's data folder.")
            return
        kind = item.text().rsplit(".", 1)[-1].upper()
        choices = self._entries_of_type(kind, exclude=entry["key"])
        if not choices:
            QMessageBox.information(
                self, "Nothing to swap with",
                f"This disc holds no other {kind} to put here.")
            return
        source = EntryPicker.ask(
            self, "Swap in another file",
            f"Put the bytes of which {kind} into {item.text()}?", choices)
        if source is None:
            return
        chosen = source.text()
        data = self._entry_bytes(source)
        if data is None:
            QMessageBox.critical(self, "Couldn't read that",
                                 "That entry's bytes could not be read.")
            return
        if self._confirm_replacement(item, data, chosen):
            self._stage_file_edit(item, data, chosen)
            self._offer_texture_migration(item, data, source)

    # --- textures after a swap ------------------------------------------

    def _img_idx_problems(self, replacements):
        """Whether the IDX and IMG about to be written agree.

        Either may be coming from `replacements` or from the working
        folder, so both are resolved the same way before checking."""
        from formats.images import img_writer
        cd = os.path.dirname(self.dat_file) if self.dat_file else None
        if not cd:
            return []

        def resolve(name):
            if name in replacements:
                return replacements[name]
            path = os.path.join(cd, name)
            if not os.path.exists(path):
                return None
            with open(path, "rb") as f:
                return f.read()

        idx, img = resolve("TOMBA2.IDX"), resolve("TOMBA2.IMG")
        if idx is None or img is None:
            return []
        return img_writer.check(idx, img)

    def _note_img_written(self):
        """The working TOMBA2.IMG has been rewritten by something other
        than a font page save - see img_dirty."""
        self.img_dirty = True
        self._chunk_vram_cache = {}
        self._area_vram_cache = {}
        self._refresh_edit_status()

    def _entries_of_type(self, kind, exclude=None):
        """[(row name, item), ...] - what a swap can choose from, listed
        the way the Data View lists the disc rather than the way the
        Indexed View does.

        One row per distinct FILE, not one per area that reaches one. An
        area's own SDAT carries its own full copy of a character it
        reuses, so the Indexed View shows the same model twenty-two
        times over and a list built from it is mostly the same file
        again and again. content_item is already keyed by what a file's
        bytes ARE (see formats/archive/idx_parser.build_dat_view), so this is
        the same group-by that view does, filtered to one type."""
        out = []
        for _content, item in self.content_item.items():
            entry = self._entry_of(item)
            if entry is None or entry["key"] == exclude:
                continue
            if item.text().rsplit(".", 1)[-1].upper() != kind:
                continue
            out.append((item.text(), entry["address"], entry["size"], item))
        # First-seen address order, the same as the Data View's - a hash
        # has no meaningful order and the address is what the disc reads
        # in.
        out.sort(key=lambda row: row[1])
        return [(f"{name}  ({size} bytes)", item)
                for name, _address, size, item in out]

    def _selected_tree_item(self):
        """Whatever is picked in whichever view is on screen.

        The toolbar's Export acts on the tab being looked at rather than
        always on the Indexed View - only one of the two is ever
        visible, so that is the whole test."""
        for view in (self.dat_view, self.tree_view):
            model = view.model()
            if model is None or not view.isVisible():
                continue
            selected = view.selectionModel().selectedIndexes()
            if selected:
                return model.itemFromIndex(selected[0])
        return None

    def _revert_file_edit(self, item):
        """Put a staged replacement back to what the disc holds."""
        entry = self._entry_of(item)
        if entry is None:
            return
        self.pending_file_edits.pop(entry["key"], None)
        self._colour_address(entry["address"], None)
        self._refresh_edit_status()

    def _pack_pending_file_edits(self):
        """self.pending_file_edits as the `edits` list repack_files()
        wants. Nothing to pack - the staged bytes are the file."""
        out = []
        for info in self.pending_file_edits.values():
            if info["kind"] == "trail":
                out.append({"trail": info["trail"], "data": info["data"]})
            else:
                out.append({"area": info["area"], "file_idx": info["file_idx"],
                            "data": info["data"]})
        return out

    def export_selected_bytes(self, item=None):
        selected_item = item if item is not None else self._selected_tree_item()
        if selected_item is None:
            QMessageBox.warning(self, "Warning", "No item selected.")
            return

        additional_data = selected_item.data(Qt.ItemDataRole.UserRole)
        chunk_file_info = selected_item.data(Qt.ItemDataRole.UserRole + 2)  # NEW
        if not additional_data:
            QMessageBox.warning(self, "Warning", "Selected item does not contain exportable data.")
            return

        try:
            if additional_data[0] == "trail":
                # TRAIL export
                _, offset, size, _ = additional_data
                if chunk_file_info:
                    chunk_index, trail_index = chunk_file_info
                else:
                    chunk_index, trail_index = (0, 0)

                with open(self.dat_file, "rb") as f:
                    f.seek(offset)
                    data = f.read(size)

                save_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save Trail Bytes",
                    f"AREA_{chunk_index:02X}_TRAIL_{trail_index:02X}_OFFSET_{offset:08X}.bin",
                    "Binary Files (*.bin)"
                )

            elif additional_data[0] in ("vram_compressed", "vram_uncompressed"):
                # VRAM handling (no change)
                # (you can leave this part same as before)
                pass

            else:
                # Normal SDAT export
                id, dat_start, offset, size = additional_data
                if chunk_file_info:
                    chunk_index, file_index = chunk_file_info
                else:
                    chunk_index, file_index = (0, 0)

                with open(self.dat_file, "rb") as f:
                    f.seek(dat_start + offset)
                    data = f.read(size)

                save_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save Exported Bytes",
                    f"AREA_{chunk_index:02X}_FILE_{file_index:02X}_ID_{id:X}_OFFSET_{dat_start + offset:08X}.bin",
                    "Binary Files (*.bin)"
                )

            if save_path:
                with open(save_path, "wb") as out_file:
                    out_file.write(data)
                QMessageBox.information(self, "Success", f"Exported bytes to {save_path}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export bytes: {e}")

"""The translation side: dialogue, letters and the font page.

TXTD and TXT2 edits, their colour in the tree, importing and
exporting whole scripts, and the character table the text is drawn
with.
"""
import json
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from formats.archive.idx_parser import row_label_data
from formats.text import fontpage
from gui.main_window.common import EDITED_TXTD_ITEM_COLOR, EXPORTED_TXTD_ITEM_COLOR


class TextMixin:
    """The translation side: dialogue, letters and the font page.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def on_txtd_content_changed(self, chunk_index, file_index, id_val, dat_start, offset, current_data):
        """Called by TXTDViewer every time an entry's text is edited.
        current_data is the SAME dict object the viewer keeps editing in
        place, so this just needs to remember which file it belongs to -
        no need to copy it defensively here since each edited TXTD only
        ever has one viewer instance touching it at a time in this UI.

        Keyed by DAT address, not (area, file index): the same bytes can
        be reached from several areas' trees (see address_locations in
        idx_parser.parse_idx_file), and editing it from one of them is
        editing the one file underneath all of them. Keying by address
        means a second area's row registers as the SAME pending edit
        rather than a second one repack_files would apply twice."""
        address = dat_start + offset
        state = self.txtd_viewer.pending_state()
        if state == "edited":
            self.pending_txtd_edits[address] = {
                "kind": "txtd", "id": id_val, "dat_start": dat_start, "offset": offset, "data": current_data,
                "locations": self.address_locations.get(address, [(chunk_index, file_index)]),
            }
        else:
            self.pending_txtd_edits.pop(address, None)
        self._set_txtd_tree_item_state(address, state)
        self._refresh_edit_status()

    def on_txt2_content_changed(self, chunk_index, file_index, id_val, dat_start, offset, current_data):
        """Called by TXT2Viewer every time an entry's text is edited. Same
        idea as on_txtd_content_changed() above - the two kinds of pending
        edit share self.pending_txtd_edits (see its docstring), tagged with
        "kind" so _pack_pending_txtd_edits() knows which packer to use for
        each one; every other bookkeeping step (tree coloring, export
        counting) doesn't need to distinguish between them at all."""
        address = dat_start + offset
        state = self.txt2_viewer.pending_state()
        if state == "edited":
            self.pending_txtd_edits[address] = {
                "kind": "txt2", "id": id_val, "dat_start": dat_start, "offset": offset, "data": current_data,
                "locations": self.address_locations.get(address, [(chunk_index, file_index)]),
            }
        else:
            self.pending_txtd_edits.pop(address, None)
        self._set_txtd_tree_item_state(address, state)
        self._refresh_edit_status()

    def on_mainexe_content_changed(self):
        """Called by MainExeViewer every time an entry's text is edited.
        Its own pending edits are tracked entirely inside the viewer
        (self.mainexe_viewer.pending_edits()/has_pending_edits())."""
        self._refresh_edit_status()

    def on_bins_content_changed(self):
        """Same as on_mainexe_content_changed, for SOP.BIN's own pending
        edits (self.bins_viewer.pending_edits()/has_pending_edits())."""
        self._refresh_edit_status()

    def _font_page_folder(self):
        """The CD folder of the disc that is open, or None with a note."""
        if not self.dat_file:
            QMessageBox.information(self, "No disc open",
                                    "Open an ISO or a CD folder first.")
            return None
        return os.path.dirname(self.dat_file)

    def open_font_editor(self):
        """Show the Translation tab, loading the page if it has not been.

        The editor lives in a tab now rather than a window of its own,
        so the menu item brings that tab forward. The codes the disc's
        own text uses are measured from the text files rather than
        assumed, so the editor can say which cells a translation is free
        to take; that measuring happens in the background because it
        takes a while."""
        cd_folder = self._font_page_folder()
        if cd_folder is None:
            return
        self.load_translation_tab(cd_folder)
        self.main_tabs.setCurrentWidget(self.translation_tab)

    def _text_files(self):
        """Every TXTD/TXT1/TXT2 file on the disc, once each.

        Keyed by DAT address: the same file is reached from every area
        that uses it, and translating it once is translating it in all
        of them (see idx_parser's address_locations)."""
        from formats.archive import idx_parser

        found = {}
        for (chunk_index, file_index), item in getattr(
                self, "txtd_item_lookup", {}).items():
            row = idx_parser.row_label_data(item)
            slot = item.data(Qt.ItemDataRole.UserRole)
            if not row or not slot:
                continue
            stem, filetype, address, _detail, _content = row
            if filetype not in ("TXTD", "TXT1", "TXT2") or address in found:
                continue
            id_val, dat_start, offset, size = slot
            found[address] = {
                "kind": filetype, "address": address, "id": id_val,
                "dat_start": dat_start, "offset": offset, "size": size,
                "chunk": chunk_index, "file": file_index,
                "label": f"{chunk_index:02X}/{stem}.{filetype}",
            }
        return [found[a] for a in sorted(found)]

    def _records_for_file(self, entry, only=None):
        """One text file's records, off the viewer's own copy so that
        edits already made go out with the export."""
        from formats.text import translation_io

        if entry["kind"] == "TXTD":
            state = self.txtd_viewer.file_state(
                entry["chunk"], entry["file"], self.dat_file,
                entry["dat_start"], entry["offset"])
            return translation_io.txtd_records(
                entry["address"], state["data"], entry["label"], only)
        state = self.txt2_viewer.file_state(
            entry["chunk"], entry["file"], self.dat_file, entry["dat_start"],
            entry["offset"], entry["size"], entry["id"])
        return translation_io.txt2_records(
            entry["address"], state["data"], entry["label"], only)

    def _write_text_export(self, records, suggested):
        """Ask where, write it, say how much went."""
        from formats.text import translation_io

        if not records:
            QMessageBox.information(
                self, "Nothing to export",
                "There is no text here to export. Open a text file in the "
                "DAT Assets tab first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export text for translating", suggested, self.TEXT_FILTER)
        if not path:
            return
        try:
            translation_io.dump(records, path,
                                build=getattr(self, "build", ""))
        except (OSError, translation_io.TranslationIOError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(
            self, "Text exported",
            f"{len(records)} entr{'y' if len(records) == 1 else 'ies'} "
            f"written to {os.path.basename(path)}.\n\n"
            "Translate the text only - every {$TAG} has to come back "
            "exactly as it went out.")

    def _current_text_viewer(self):
        """(viewer, its file entry) for whichever text file is on
        screen, or (None, None)."""
        for viewer in (self.txtd_viewer, self.txt2_viewer):
            address = viewer.file_address()
            if address is None:
                continue
            for entry in self._text_files():
                if entry["address"] == address:
                    return viewer, entry
        return None, None

    def export_text_entry(self):
        """The one entry that is selected."""
        viewer, entry = self._current_text_viewer()
        if entry is None:
            QMessageBox.information(
                self, "No text file open",
                "Open a TXTD, TXT1 or TXT2 file in the DAT Assets tab first.")
            return
        location = viewer.selected_location()
        if location is None:
            QMessageBox.information(
                self, "No entry selected",
                "Pick an entry in the list on the left first.")
            return
        records = self._records_for_file(entry, only={location})
        self._write_text_export(records, "entry.json")

    def export_text_file(self):
        """Every entry in the text file on screen."""
        _viewer, entry = self._current_text_viewer()
        if entry is None:
            QMessageBox.information(
                self, "No text file open",
                "Open a TXTD, TXT1 or TXT2 file in the DAT Assets tab first.")
            return
        records = self._records_for_file(entry)
        self._write_text_export(records, f"{entry['address']:08X}.json")

    def export_text_all(self):
        """The disc's whole script - every text file in the DAT, plus
        MAIN.EXE's and SOP.BIN's pools where they are readable."""
        from formats.text import translation_io

        if not self.dat_file:
            QMessageBox.information(self, "No disc open",
                                    "Open an ISO or a CD folder first.")
            return
        records = []
        failed = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for entry in self._text_files():
                try:
                    records += self._records_for_file(entry)
                except Exception as exc:
                    failed.append(f"{entry['label']}: {exc}")
            exe = getattr(self.mainexe_viewer, "entries", None)
            if exe:
                records += translation_io.pool_records("mainexe", exe,
                                                       "MAIN.EXE")
            sop = getattr(getattr(self.bins_viewer, "sop_viewer", None),
                          "entries", None)
            if sop:
                records += translation_io.pool_records("sop", sop, "SOP.BIN")
            else:
                # It is only read once someone picks it in the BINs tab,
                # so an export taken before that would quietly be twelve
                # lines short.
                failed.append("SOP.BIN: not open - select it in the BINs "
                              "tab first if you want its story text too")
        finally:
            QApplication.restoreOverrideCursor()
        if failed:
            QMessageBox.warning(
                self, "Some files could not be read",
                "These were left out of the export:\n\n" + "\n".join(failed[:12]))
        self._write_text_export(records, "tomba2-script.json")

    def import_text(self):
        """Read a translated file back in and put it on the disc's text.

        Nothing is written to the disc here - every entry lands as a
        pending edit, the same as typing it would, and goes out with the
        next Save. What is decided here is the alphabet: a translation
        that brings letters this build has never drawn needs codes for
        them before any of it can be packed."""
        from formats.text import translation_io

        if not self.dat_file:
            QMessageBox.information(self, "No disc open",
                                    "Open an ISO or a CD folder first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import translated text", "", self.TEXT_FILTER)
        if not path:
            return
        try:
            meta, records = translation_io.load(path)
        except translation_io.TranslationIOError as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        if not self._confirm_import_build(meta, path):
            return
        grouped, bad_ids = translation_io.group_by_kind(records)
        if not grouped:
            QMessageBox.warning(
                self, "Nothing to import",
                f"{os.path.basename(path)} holds {len(records)} entr"
                f"{'y' if len(records) == 1 else 'ies'}, none of them named "
                "the way this tool names them.")
            return
        if not self._offer_new_characters(grouped):
            return
        report = self._apply_imported(grouped)
        report["bad_ids"] = bad_ids
        self._report_import(os.path.basename(path), report)

    def export_letters(self):
        """Write out which cell each new letter was given.

        The same shape translation.save() keeps beside the disc, but
        somewhere you choose - so a Polish alphabet drawn once can be
        carried to the next disc, or kept in version control next to the
        script it belongs with."""
        import json

        from formats.text import translation

        table = translation.active()
        if not table.chars:
            QMessageBox.information(
                self, "Nothing assigned",
                "No cell has been given a letter yet. Assign some in the "
                "Translation tab first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export letter assignments", "letters.json",
            "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(table.to_json(), f, ensure_ascii=False, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(
            self, "Assignments exported",
            f"{len(table.chars)} letter(s) written to "
            f"{os.path.basename(path)}.")

    def import_letters(self):
        """Take a saved set of letter-to-cell assignments.

        Merging is offered because carrying an alphabet between discs is
        the point: replacing would throw away whatever this disc has
        already been given."""
        import json

        from formats.text import translation

        if not self.dat_file:
            QMessageBox.information(self, "No disc open",
                                    "Open an ISO or a CD folder first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import letter assignments", "",
            "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                incoming = translation.Table.from_json(json.load(f))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(
                self, "Import failed",
                f"{os.path.basename(path)} is not a letter assignment "
                f"file: {exc}")
            return
        if not incoming.chars:
            QMessageBox.warning(self, "Nothing to import",
                                f"{os.path.basename(path)} assigns no cells.")
            return
        table = translation.active()
        mode = QMessageBox.StandardButton.Yes
        if table.chars:
            mode = QMessageBox.question(
                self, "Merge or replace?",
                f"{os.path.basename(path)} assigns {len(incoming.chars)} "
                f"cell(s). This disc already has {len(table.chars)}.\n\n"
                "Yes - merge, letting the file win where they disagree\n"
                "No - replace, dropping what this disc has\n",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes)
            if mode == QMessageBox.StandardButton.Cancel:
                return
        merged = translation.Table(
            incoming.name or table.name,
            dict(table.chars) if mode == QMessageBox.StandardButton.Yes else {},
            incoming.glyph_top if incoming.glyph_top is not None
            else table.glyph_top)
        clashes = []
        for code, char in incoming.chars.items():
            if code in translation.CONTROL_CODES:
                clashes.append(f"{code:#04x} is a control - {char!r} skipped")
                continue
            merged.chars[code] = char
        cd_folder = os.path.dirname(self.dat_file)
        translation.apply(merged)
        try:
            translation.save(cd_folder, merged)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save the table", str(exc))
            return
        if getattr(self, "_translation_loaded", False):
            self.font_page_view.set_source(cd_folder)
        note = (f"{len(merged.chars)} cell(s) now have letters.\n\n"
                "The cells themselves are whatever this disc draws - an "
                "assignment names a cell, it does not carry the glyph. "
                "Draw any that are blank in the Translation tab.")
        if clashes:
            note += "\n\nSkipped:\n    " + "\n    ".join(clashes)
        QMessageBox.information(self, "Assignments imported", note)

    def _confirm_import_build(self, meta, path):
        """Stop an export from one disc being poured into another."""
        theirs = (meta or {}).get("build")
        mine = getattr(self, "build", "")
        if not theirs or not mine or theirs == mine:
            return True
        answer = QMessageBox.question(
            self, "Different build",
            f"{os.path.basename(path)} was exported from a {theirs} disc and "
            f"this one is {mine}.\n\nThe two do not lay their text files out "
            "the same way, so most entries will land somewhere else or "
            "nowhere at all.\n\nImport it anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _import_alphabet_check(self, grouped):
        """(letters the game's own table has no code for, letters the
        MAIN.EXE/SOP pools cannot hold at all).

        Two alphabets, and only one of them can be extended. The DAT's
        text is drawn from the font page, so a new letter is a matter of
        claiming a cell and drawing it. MAIN.EXE's pool and SOP.BIN's
        are Latin-1 bytes the console draws with a font that is not on
        the disc - there is nothing to claim and nothing to draw."""
        from formats.text import txtd_packer
        from formats.executable.mainbin_parser import encode_bytes, MainBinParseError

        game, pool = [], []
        for kind, texts in grouped.items():
            for text in texts.values():
                if kind in ("txtd", "txt2"):
                    for char in txtd_packer.unencodable(text, cells=(kind == "txt2")):
                        if char not in game:
                            game.append(char)
                else:
                    for char in text:
                        if char in pool or char in "\n":
                            continue
                        try:
                            encode_bytes(char)
                        except MainBinParseError:
                            pool.append(char)
        return game, pool

    def _offer_new_characters(self, grouped):
        """Ask about letters the build has never had. False to stop.

        Claiming a code is not drawing a glyph: the cell it points at is
        still blank afterwards, and the Translation tab is where it gets
        a shape. Doing it here is what makes that possible at all - text
        cannot be packed against a letter with no code."""
        from formats.text import dicts
        from formats.text import translation

        game, pool = self._import_alphabet_check(grouped)
        if pool:
            QMessageBox.warning(
                self, "Some letters cannot go in MAIN.EXE or SOP.BIN",
                "These are drawn by the console, out of a font that is not "
                "on the disc, so there is no cell to give them:\n\n"
                f"    {' '.join(pool)}\n\n"
                "Those entries will import, but will not pack until the "
                "letters are gone. Everything else is unaffected.")
        if not game:
            return True
        listed = " ".join(game)
        if dicts.japanese_disc():
            QMessageBox.warning(
                self, "New letters on the Japanese disc",
                f"These are not in this disc's alphabet:\n\n    {listed}\n\n"
                "Assigning cells is not offered here - the Japanese build "
                "draws its text from the console's font, not from a page "
                "this tool can add to. The import will go ahead; those "
                "entries will not pack.")
            return True
        cd_folder = os.path.dirname(self.dat_file)
        answer = QMessageBox.question(
            self, "New letters",
            f"This translation uses {len(game)} letter"
            f"{'' if len(game) == 1 else 's'} the disc has no code for:\n\n"
            f"    {listed}\n\n"
            "Give each one a free cell in the font page now?\n\n"
            "That claims the code so the text can be packed. The cells "
            "stay blank until you draw the glyphs in the Translation tab.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.No:
            return True
        try:
            page = fontpage.read_page(cd_folder)
        except Exception as exc:
            QMessageBox.critical(self, "Could not read the font page", str(exc))
            return False
        picked, unplaced = translation.suggest_codes(
            page, game, top=self.preview_glyph_top())
        table = translation.active()
        try:
            for char, code in picked.items():
                table.claim(code, char)
            translation.apply(table)
            translation.save(cd_folder, table)
        except Exception as exc:
            QMessageBox.critical(self, "Could not claim the cells", str(exc))
            return False
        if getattr(self, "_translation_loaded", False):
            self.font_page_view.set_source(cd_folder)
        taken = ", ".join(f"{char} = {code:#04x}"
                          for char, code in sorted(picked.items()))
        message = f"Claimed {len(picked)} cell(s):\n\n    {taken}\n\n" \
                  "Open the Translation tab to draw them - until you do, " \
                  "they encode correctly but draw nothing."
        if unplaced:
            message += ("\n\nNo room for: " + " ".join(unplaced) +
                        "\nEvery other cell on the page is already spoken "
                        "for. Free some in the Translation tab first.")
        QMessageBox.information(self, "Cells claimed", message)
        return True

    def _apply_imported(self, grouped):
        """Put the imported text on the disc's own copies, as pending
        edits. Returns what happened, for _report_import."""
        from formats.text import translation_io

        report = {"files": 0, "entries": 0, "missed": [], "pinned": 0}
        on_disc = {e["address"] for e in self._text_files()}
        for kind in ("txtd", "txt2"):
            for key in grouped.get(kind, {}):
                if key[0] not in on_disc:
                    report["missed"].append(translation_io.make_id(kind, *key))
        for entry in self._text_files():
            kind = "txtd" if entry["kind"] == "TXTD" else "txt2"
            texts = {k: v for k, v in grouped.get(kind, {}).items()
                     if k[0] == entry["address"]}
            if not texts:
                continue
            try:
                if kind == "txtd":
                    data, changed, edited, missed = self.txtd_viewer.apply_import(
                        entry["chunk"], entry["file"], self.dat_file,
                        entry["dat_start"], entry["offset"], entry["address"],
                        texts)
                else:
                    data, changed, edited, missed = self.txt2_viewer.apply_import(
                        entry["chunk"], entry["file"], self.dat_file,
                        entry["dat_start"], entry["offset"], entry["size"],
                        entry["id"], entry["address"], texts)
            except Exception as exc:
                report["missed"].append(f"{entry['label']}: {exc}")
                continue
            report["missed"] += missed
            if not changed:
                continue
            report["files"] += 1
            report["entries"] += len(changed)
            self._register_imported_edit(kind, entry, data, edited)
        for kind, viewer, name in (
                ("mainexe", self.mainexe_viewer, "MAIN.EXE"),
                ("sop", getattr(self.bins_viewer, "sop_viewer", None),
                 "SOP.BIN")):
            texts = grouped.get(kind)
            if not texts or viewer is None or not getattr(viewer, "entries", None):
                continue
            applied, pinned, missed = viewer.apply_import(texts)
            report["files"] += 1 if applied else 0
            report["entries"] += len(applied)
            report["pinned"] += len(pinned)
            report["missed"] += [f"{name} {m}" for m in missed]
        self._refresh_edit_status()
        return report

    def _register_imported_edit(self, kind, entry, data, edited):
        """Mark one imported file pending, the way typing in it does.

        Not routed through on_txtd_content_changed: that asks the viewer
        for the state of whatever file is on screen, and an import
        touches files nobody has opened."""
        address = entry["address"]
        if edited:
            self.pending_txtd_edits[address] = {
                "kind": kind, "id": entry["id"],
                "dat_start": entry["dat_start"], "offset": entry["offset"],
                "data": data,
                "locations": self.address_locations.get(
                    address, [(entry["chunk"], entry["file"])]),
            }
        else:
            self.pending_txtd_edits.pop(address, None)
        self._set_txtd_tree_item_state(address, "edited" if edited else None)

    def _report_import(self, name, report):
        lines = [f"{report['entries']} entr"
                 f"{'y' if report['entries'] == 1 else 'ies'} changed across "
                 f"{report['files']} file{'' if report['files'] == 1 else 's'}."]
        if report["pinned"]:
            lines.append(f"{report['pinned']} refused - pinned entries have "
                         "no reference the repacker can move.")
        if report["bad_ids"]:
            lines.append(f"{len(report['bad_ids'])} id(s) were not in this "
                         "tool's format and were skipped.")
        if report["missed"]:
            shown = "\n    ".join(report["missed"][:10])
            more = ("" if len(report["missed"]) <= 10
                    else f"\n    ...and {len(report['missed']) - 10} more")
            lines.append(f"{len(report['missed'])} named nothing on this "
                         f"disc:\n    {shown}{more}")
        lines.append("Nothing is on the disc yet - use Save to write it.")
        QMessageBox.information(self, f"Imported {name}", "\n\n".join(lines))

    def preview_glyph_top(self):
        """Which page row this disc's dialogue grid starts at.

        The previews draw a string by looking each character up in the
        table and copying that cell out of the font page, so they need
        to know where the grid begins. Left to default it is 40 - the US
        row - and on a European disc, whose grid starts at 64, every
        cell comes out 24 rows high: the preview draws the symbol and
        system-font rows instead of the letters, which looks like the
        font is simply wrong rather than like an offset."""
        from formats.text import dicts
        return dicts.glyph_top(getattr(self, "build", dicts.DEFAULT_BUILD))

    def _edited_img(self):
        """The working TOMBA2.IMG if anything has written to it, else
        None. See img_dirty."""
        if not self.img_dirty or not getattr(self, "dat_file", None):
            return None
        path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IMG")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def apply_build_table(self, cd_folder):
        """Work out which disc this is and put its character table in
        force, before anything decodes text with it.

        The font page says which layout it is and MAIN.EXE says which
        language - see formats/text/dicts.detect. Done at open rather than
        when the Translation tab is first looked at, because the TXTD
        viewer decodes text long before anyone goes near that tab."""
        from formats.text import fontpage
        from formats.text import dicts
        from formats.text import translation

        try:
            page = fontpage.read_page(cd_folder)
        except Exception as e:
            print(f"Could not read the font page to identify the build: {e}")
            self.build = dicts.DEFAULT_BUILD
            return
        exe = b""
        exe_path = os.path.join(cd_folder, "MAIN.EXE")
        try:
            if os.path.exists(exe_path):
                with open(exe_path, "rb") as f:
                    exe = f.read()
        except Exception:
            exe = b""
        self.build, why = dicts.detect(page, exe)
        translation.use_build(self.build)
        # A project keeps its character assignments beside the extracted
        # CD files. Apply them before TXTD and MAIN.EXE are parsed, as
        # those parsers cache decoded strings for their editors.
        translation.load(cd_folder)
        print(f"Build: {self.build} - {why}")

    def _font_page_saved(self):
        """Reload previews and VRAM views after a font-page save.

        Also marks the IMG as needing to travel with the next export -
        see img_dirty.

        The page is chunk 0 of TOMBA2.IMG, which IS AREA_00's VRAM, so a
        glyph written here changes what the VRAM and CVRAM views draw.
        They read the IMG when a row is picked and hold the image after
        that, so without this the edit is on the disc and invisible
        everywhere but the Translation tab."""
        self.img_dirty = True
        self._refresh_edit_status()
        self._area_vram_cache = {}
        cd_folder = self._font_page_folder()
        if cd_folder:
            self.mainexe_viewer.reload_preview_font(
                cd_folder, self.preview_glyph_top())
        selected = self.tree_view.selectionModel().selectedIndexes()
        if selected:
            # Re-run the selection, which is what loaded them in the
            # first place.
            self.on_tree_selection_changed()

    def load_translation_tab(self, cd_folder=None):
        """Point both halves of the Translation tab at the open disc."""
        if cd_folder is None:
            cd_folder = self._font_page_folder()
        if cd_folder is None:
            return
        self.font_page_view.set_source(cd_folder)
        self._translation_loaded = True

    def export_font_page(self):
        """Write the font/menu page out as an indexed PNG.

        The palette put on the file is one of the page's own CLUTs, so
        it opens looking the way the game draws dialogue rather than as
        a black square. The pixels are the 4-bit indices either way."""
        cd_folder = self._font_page_folder()
        if cd_folder is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export font page", "fontpage.png", "PNG (*.png)")
        if not path:
            return
        try:
            cluts = fontpage.read_cluts(cd_folder)
            # The greyscale ramp the dialogue font uses, where there is one.
            clut = next((pal for _row, _slot, pal in cluts
                         if pal[2][:3] == (255, 255, 255)), None)
            fontpage.export_png(cd_folder, path, clut)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Font page written to {path}", 8000)

    def import_font_page(self):
        """Read an edited page back into TOMBA2.IMG.

        Refused, with nothing written, if the edit no longer fits the
        room the shard was given."""
        cd_folder = self._font_page_folder()
        if cd_folder is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import font page", "", "PNG (*.png)")
        if not path:
            return
        try:
            # The same palette export_font_page writes with, so a file
            # an image editor has renumbered still comes back right.
            cluts = fontpage.read_cluts(cd_folder)
            clut = next((pal for _row, _slot, pal in cluts
                         if pal[2][:3] == (255, 255, 255)), None)
            count = fontpage.import_png(cd_folder, path, clut=clut)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        QMessageBox.information(
            self, "Font page imported",
            f"Rewrote {count} shard(s) in TOMBA2.IMG.\n\n"
            "Reopen the disc to see it in the VRAM view.")
        self.statusBar().showMessage(f"Font page imported from {path}", 8000)

    def _set_twin_edits(self, on):
        """Turn twin-following on or off, and recolour what changes.

        A pending edit's twin is coloured as edited too, so switching
        this has to repaint: the rows it was speaking for stop being
        spoken for."""
        self.twin_edits = on
        for pending in list(self.pending_txtd_edits):
            self._set_txtd_tree_item_state(pending, "edited")
        if not on:
            for pending in list(self.pending_txtd_edits):
                for twin in self.txtd_twins.get(pending, ()):
                    if twin in self.pending_txtd_edits:
                        continue
                    # Back to what is true of it, which is green if an
                    # earlier export really did write it and plain only
                    # if nothing ever has.
                    self._colour_address(
                        twin,
                        "exported" if twin in self.exported_addresses
                        else None)
        self._refresh_edit_status()

    def _twins_of(self, address):
        """Byte-identical copies of this file, if following them is on."""
        if not self.twin_edits:
            return []
        return [twin for twin in self.txtd_twins.get(address, ())
                if twin not in self.pending_txtd_edits]

    def _set_txtd_tree_item_state(self, address, state):
        """Colors every row that points at this DAT address: "edited"
        (orange) while it has pending edits, "exported" (green) once
        those edits have been exported, or None for the tree's normal
        color (never touched) - so it's obvious at a glance which TXTD
        files were changed and whether that change was saved, without
        having to open each one.

        One DAT address can be reached from several areas' rows (see
        address_locations in idx_parser.parse_idx_file); every one of
        them is the same file, so every one of them is recolored, not
        just the row that happened to be open when the edit was made.
        Also recomputes the enclosing NN_DATA and AREA_NN folder colors
        for each, so an edit anywhere shows up all the way up the tree
        in every area it touches."""
        # The twins are shown in the same state, because they are about
        # to be written with the same bytes - see _twins_of.
        for also in [address] + self._twins_of(address):
            self._colour_address(also, state)

    def _colour_address(self, address, state):
        """Colour one address's rows - see _set_txtd_tree_item_state."""
        if state == "exported":
            self.exported_addresses.add(address)
        # Every row this address reaches, of any kind - address_rows
        # holds one per (area, slot) location regardless of filetype,
        # txtd_item_lookup is folded in too in case something still
        # reaches a TXTD row only that way. Deduplicated by identity:
        # a TXTD row is in both, and coloring or aggregating it twice
        # would just be wasted work, not wrong, but the dict below is
        # keyed by id() rather than by (chunk_index, file_index) for a
        # sharper reason - a TRAIL row and an ordinary SDAT row number
        # their slots independently, so the same pair can name two
        # completely different rows, and txtd_file_states has to tell
        # them apart to aggregate an AREA folder's color correctly.
        rows = {id(item): item
                for item in getattr(self, "address_rows", {}).get(address, ())}
        for location in self.address_locations.get(address, []):
            file_item = self.txtd_item_lookup.get(location)
            if file_item is not None:
                rows.setdefault(id(file_item), file_item)

        for file_item in rows.values():
            if state:
                self.txtd_file_states[id(file_item)] = state
            else:
                self.txtd_file_states.pop(id(file_item), None)

            self._apply_tree_item_state_color(file_item, state)

            sdat_item = file_item.parent()
            if sdat_item is not None:
                self._refresh_folder_state_color(sdat_item)
                area_item = sdat_item.parent()
                if area_item is not None:
                    self._refresh_folder_state_color(area_item)

        # The Data View has exactly one row for this address rather than
        # one per location, so it's colored once, directly - no folders
        # to recompute either, since that view has none.
        dat_item = getattr(self, "dat_view_lookup", {}).get(address)
        if dat_item is not None:
            self._apply_tree_item_state_color(dat_item, state)

    @staticmethod
    def _row_name_parts(item):
        """(name without the unsaved-edit mark, its own ".EXT" if this
        row is a file that has one).

        Kept apart so the mark can go right before the extension rather
        than after it. on_tree_selection_changed picks which viewer to
        open by exactly that extension - item_name.endswith('.SMST'),
        item_name.split('.')[-1] and the like - so a mark tacked on the
        end would silently stop an edited row from opening a second
        time; row_label_data's own filetype says where the real
        extension is, so this doesn't have to guess at the text."""
        text = item.text()
        ext = ""
        row = row_label_data(item)
        if row:
            suffix = f".{row[1]}"
            if text.endswith(suffix):
                text, ext = text[:-len(suffix)], suffix
        # The mark itself sits just before the extension, so it has to
        # come off AFTER the extension does - stripping it first (from
        # text that still ends in ".SMST", not "*") would miss it and
        # let a repeated color/recolor stack up "**", "***", ...
        if text.endswith("*"):
            text = text[:-1]
        return text, ext

    @staticmethod
    def _apply_tree_item_state_color(item, state):
        """Color a row (or a folder aggregating several) and, only while
        it is "edited" - orange, unsaved - mark its text with a "*",
        the same mark mainbin_viewer puts on a category folder and
        MainWindow puts on a tab with unsaved edits. "exported" (green,
        already written out) and the plain state both show the row's
        bare name; only pending edits earn the mark, so it means the
        same thing here as it does everywhere else it appears."""
        base, ext = TextMixin._row_name_parts(item)
        if state == "edited":
            item.setForeground(QBrush(QColor(EDITED_TXTD_ITEM_COLOR)))
            item.setText(f"{base}*{ext}")
        elif state == "exported":
            item.setForeground(QBrush(QColor(EXPORTED_TXTD_ITEM_COLOR)))
            item.setText(f"{base}{ext}")
        else:
            item.setData(None, Qt.ItemDataRole.ForegroundRole)
            item.setText(f"{base}{ext}")

    def _refresh_folder_state_color(self, folder_item):
        """Recomputes an NN_DATA or AREA_NN folder's color by aggregating
        the edit/export state of every file anywhere underneath it -
        TXTD, SMST, trail, any of them: orange if any still has pending
        edits, else green if any has been edited-and-exported, else back
        to the tree's normal color."""
        self._apply_tree_item_state_color(folder_item, self._aggregate_txtd_state(folder_item))

    def _aggregate_txtd_state(self, item):
        """"edited" if any file at or below `item` has pending edits,
        else "exported" if any has been edited-and-exported, else None.
        Walks the tree itself (rather than needing a separate index of
        "which files live under this folder"), using each row's own
        identity as the key into txtd_file_states - not the
        (chunk_index, file_index) pair UserRole + 2 carries, which an
        SDAT row and a TRAIL row number independently and so does not
        tell two different rows apart (see _colour_address)."""
        saw_exported = False
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            state = self.txtd_file_states.get(id(child))
            if state is None and child.hasChildren():
                state = self._aggregate_txtd_state(child)

            if state == "edited":
                return "edited"
            if state == "exported":
                saw_exported = True

        return "exported" if saw_exported else None

    def _pack_pending_txtd_edits(self):
        """Turn self.pending_txtd_edits into the `edits` list repack_files()
        expects. Returns None (after showing an error dialog) if any entry
        fails to encode.

        Branches on each pending edit's "kind" tag (see
        on_txtd_content_changed / on_txt2_content_changed) to call the
        right packer - txt2_packer.Txt2PackError subclasses
        txtd_packer.TxtdPackError, so one except clause below catches
        either. This is the single place that packs BOTH file types, and
        is now used by both export_all_files() and export_iso() so the
        logic only exists once."""
        from formats.text import txtd_packer
        from formats.text import txt2_packer
        edits = []
        try:
            for address, info in self.pending_txtd_edits.items():
                if info.get("kind") == "txt2":
                    if info.get("id") == 3:
                        packed_bytes = txt2_packer.pack_txt2_simple(info["data"])
                    else:
                        packed_bytes = txt2_packer.pack_txt2(info["data"])
                else:
                    packed_bytes = txtd_packer.pack_txtd(info["data"])
                # One edit per DAT address, however many areas' rows
                # reach it (see on_txtd_content_changed) - any one of
                # them resolves the same absolute region, so the first
                # is as good as any to hand the repacker.
                chunk_index, file_index = info["locations"][0]
                edits.append({"area": chunk_index, "file_idx": file_index, "data": packed_bytes})
                # And the same bytes into every byte-identical copy, so
                # a purified area does not keep the untranslated text
                # its cursed twin just lost.
                for twin in self._twins_of(address):
                    where = self.address_locations.get(twin)
                    if not where:
                        continue
                    twin_chunk, twin_file = where[0]
                    edits.append({"area": twin_chunk, "file_idx": twin_file,
                                  "data": packed_bytes})
        except txtd_packer.TxtdPackError as e:
            QMessageBox.critical(self, "Text encoding error",
                                  f"Couldn't encode an entry's text:\n\n{e}\n\n"
                                  "Fix that entry and try exporting again.")
            return None
        return edits

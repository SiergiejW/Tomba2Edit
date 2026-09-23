"""A session saved to disk and opened again.

A project is the game's data files plus everything the tool
worked out about them - see formats/archive/project_file.py for
the container.
"""
import json
import os
import shutil
import tempfile
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from formats.archive import project_file
from formats.archive.idx_parser import apply_labels, parse_idx_file
from formats.audio import snd_edit
from formats.executable.mainbin_editor import (
    repack_pool as mainbin_repack_pool)
from formats.executable.sop_editor import repack_pool as sop_repack_pool
from game import labels as labels_module


class ProjectMixin:
    """A session saved to disk and opened again.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def _translation_project_state(self):
        """JSON-safe editor history which is not present in packed files.

        The project snapshot contains the edited bytes, but orange/green rows
        and the asterisks mean *how* those bytes got there.  Preserve that
        workspace state separately so closing the application does not erase
        the translator's progress markers.
        """
        text_files = {}
        for kind, viewer in (("txtd", self.txtd_viewer),
                             ("txt2", self.txt2_viewer)):
            for location, cached in viewer._file_state_cache.items():
                item = self.txtd_item_lookup.get(location)
                data = item.data(Qt.ItemDataRole.UserRole) if item else None
                if not data or not isinstance(data[0], int):
                    continue
                _id, dat_start, offset, _size = data
                address = dat_start + offset
                def locations(name):
                    return [list(value) if isinstance(value, tuple) else value
                            for value in sorted(cached.get(name, ()))]
                record = text_files.setdefault(str(address), {
                    "kind": kind, "edited": [], "exported": []})
                record["edited"] = locations("edited_locations")
                record["exported"] = locations("exported_locations")

        tree_files = {}
        for address, rows in self.address_rows.items():
            states = {self.txtd_file_states.get(id(row)) for row in rows}
            state = "edited" if "edited" in states else (
                "exported" if "exported" in states else None)
            if state:
                tree_files[str(address)] = state

        return {
            "text_files": text_files,
            "tree_files": tree_files,
            "pending_replacements": [
                {"address": info["address"], "label": info.get("label", "")}
                for info in self.pending_file_edits.values()],
            "main_exe": self.mainexe_viewer.project_state(),
            "sop": self.bins_viewer.sop_viewer.project_state(),
            "img_dirty": bool(self.img_dirty),
        }

    def _restore_translation_project_state(self, state):
        """Restore saved colors, stars and pending-work semantics."""
        state = state or {}
        for address_text, record in state.get("text_files", {}).items():
            try:
                address = int(address_text)
            except (TypeError, ValueError):
                continue
            locations = self.address_locations.get(address, ())
            if not locations:
                continue
            chunk_index, file_index = locations[0]
            item = self.txtd_item_lookup.get((chunk_index, file_index))
            data = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not data:
                continue
            id_val, dat_start, offset, size = data
            kind = record.get("kind")
            viewer = self.txt2_viewer if kind == "txt2" else self.txtd_viewer
            if kind == "txt2":
                cached = viewer.file_state(
                    chunk_index, file_index, self.dat_file, dat_start,
                    offset, size, id_val)
                decode = lambda value: int(value)
            else:
                cached = viewer.file_state(
                    chunk_index, file_index, self.dat_file, dat_start, offset)
                decode = lambda value: tuple(int(part) for part in value)
            cached["edited_locations"].update(
                decode(value) for value in record.get("edited", ()))
            cached["exported_locations"].update(
                decode(value) for value in record.get("exported", ()))
            cached["exported_locations"] -= cached["edited_locations"]
            if cached["edited_locations"]:
                self.pending_txtd_edits[address] = {
                    "kind": kind or "txtd", "id": id_val,
                    "dat_start": dat_start, "offset": offset,
                    "data": cached["data"], "locations": list(locations),
                }

        for address_text, visual in state.get("tree_files", {}).items():
            try:
                self._colour_address(int(address_text), visual)
            except (TypeError, ValueError):
                pass

        for saved in state.get("pending_replacements", ()):
            item = self.address_item.get(saved.get("address"))
            entry = self._entry_of(item)
            if entry is None:
                continue
            with open(self.dat_file, "rb") as source:
                source.seek(entry["address"])
                payload = source.read(entry["size"])
            self.pending_file_edits[entry["key"]] = dict(
                entry, data=payload, label=saved.get("label", ""))
            self._colour_address(entry["address"], "edited")

        self.mainexe_viewer.restore_project_state(state.get("main_exe"))
        self.bins_viewer.sop_viewer.restore_project_state(state.get("sop"))
        self.bins_viewer._refresh_sop_item_color()
        self.img_dirty = bool(state.get("img_dirty"))
        self._refresh_edit_status()

    def save_translation_project(self, project_dir=None, announce=True):
        """Snapshot translation work into a reopenable project folder.

        `project_dir` is the folder to write, or None to ask for one.
        Passing it is how saving to a .t2p works: that packs this same
        tree up afterwards rather than duplicating any of it - see
        save_project_file() and formats/archive/project_file.

        BIN/ISO input is extracted to a temporary directory, which is a
        poor place to keep a long-running translation: the character
        table, font page and pending text can otherwise end up in three
        unrelated locations.  A project deliberately contains only the
        files translation needs, not a second 400 MB disc image:

            project/\n
                tomba2project.json\n
                Tomba 2 Game Files/TOMBA2.DAT, TOMBA2.IDX,
                    TOMBA2.IMG, tombadict.json\n
                MAIN.EXE   (when this disc has one)
                BIN/SOP.BIN (when story text is available)

        The project folder can be opened directly through "Open Translation
        Project / Game Folder". Saving back into the currently open project is an
        in-place save: files are staged first, then the editor is rebased
        onto them so a later save cannot apply the same edits twice.
        """
        if not getattr(self, "dat_file", None):
            QMessageBox.information(
                self, "No disc open", "Open a Tomba! 2 disc first.")
            return False

        if project_dir is None:
            project_dir = QFileDialog.getExistingDirectory(
                self, "Choose an empty or existing project folder")
        if not project_dir:
            return False
        game_folder_name = "Tomba 2 Game Files"
        manifest_path = os.path.join(project_dir, "tomba2project.json")
        # Existing version-1 projects keep their CD folder so Save does not
        # silently fork one workspace into two directories.  New projects use
        # a name that says what the folder actually contains.
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as source:
                    old_manifest = json.load(source)
                game_folder_name = old_manifest.get("cd_folder") or game_folder_name
            except (OSError, ValueError, TypeError):
                pass
        cd_dir = os.path.join(project_dir, game_folder_name)
        try:
            os.makedirs(cd_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Project save failed", str(exc))
            return False

        edits = self._pack_pending_txtd_edits()
        if edits is None:
            return False
        edits += self._pack_pending_file_edits()
        source_cd = os.path.dirname(self.dat_file)
        source_idx = os.path.join(source_cd, "TOMBA2.IDX")
        mainexe_edits = self.mainexe_viewer.all_edits()
        sop_path = self.bins_viewer.sop_viewer.sop_path
        sop_edits = self.bins_viewer.all_edits()
        project_state = self._translation_project_state()
        in_place = (os.path.normcase(os.path.abspath(source_cd))
                    == os.path.normcase(os.path.abspath(cd_dir)))

        try:
            from formats.archive.repacker import repack_files
            output_dat = os.path.join(cd_dir, "TOMBA2.DAT")
            output_idx = os.path.join(cd_dir, "TOMBA2.IDX")
            output_img = os.path.join(cd_dir, "TOMBA2.IMG")
            exe_path = self.mainexe_viewer.exe_path
            output_exe = os.path.join(project_dir, "MAIN.EXE") if exe_path else None
            output_sop = (os.path.join(project_dir, "BIN", "SOP.BIN")
                          if sop_path else None)

            # Always write a complete snapshot somewhere separate first.
            # Besides avoiding half-written projects, this makes source ==
            # destination safe on Windows (shutil.copy2 otherwise raises
            # SameFileError for an already-open project).
            with tempfile.TemporaryDirectory(
                    prefix=".tomba2project-", dir=project_dir) as stage:
                stage_cd = os.path.join(stage, game_folder_name)
                os.makedirs(stage_cd)
                stage_dat = os.path.join(stage_cd, "TOMBA2.DAT")
                stage_idx = os.path.join(stage_cd, "TOMBA2.IDX")
                stage_img = os.path.join(stage_cd, "TOMBA2.IMG")

                if edits:
                    repack_files(self.dat_file, source_idx, edits,
                                 stage_dat, stage_idx)
                else:
                    shutil.copy2(self.dat_file, stage_dat)
                    shutil.copy2(source_idx, stage_idx)

                img = self._edited_img()
                if img is None:
                    shutil.copy2(os.path.join(source_cd, "TOMBA2.IMG"),
                                 stage_img)
                else:
                    with open(stage_img, "wb") as f:
                        f.write(img)

                stage_exe = None
                if exe_path:
                    stage_exe = os.path.join(stage, "MAIN.EXE")
                    if mainexe_edits:
                        mainbin_repack_pool(
                            exe_path, self.mainexe_viewer.entries,
                            mainexe_edits, stage_exe)
                    else:
                        shutil.copy2(exe_path, stage_exe)

                stage_sop = None
                if sop_path:
                    stage_bin = os.path.join(stage, "BIN")
                    os.makedirs(stage_bin)
                    stage_sop = os.path.join(stage_bin, "SOP.BIN")
                    if sop_edits:
                        sop_repack_pool(
                            sop_path, self.bins_viewer.sop_viewer.entries,
                            sop_edits, stage_sop)
                    else:
                        shutil.copy2(sop_path, stage_sop)

                from formats.text import translation
                translation.save(stage_cd, translation.active())

                # Everything staged against the DAT or the IMG - a
                # replaced model, a swapped texture, a recoloured
                # sprite, the font page - is already in the files
                # written above, because those are the files. These two
                # are the exceptions: the tree's names live nowhere on
                # the disc, and a staged voice clip is raw sectors aimed
                # at the disc image rather than at anything in here.
                # The music and the sound effects. Held whole like
                # MAIN.EXE rather than as a list of patches, so a
                # project carries its own audio and can be worked on
                # with no disc attached.
                stage_snd = None
                if self.snd_edits.loaded():
                    stage_snd = os.path.join(stage, "TOMBA2.SND")
                    try:
                        with open(stage_snd, "wb") as f:
                            f.write(self.snd_edits.rebuild())
                    except (OSError, snd_edit.SndEditError) as exc:
                        raise RuntimeError(
                            f"The music couldn't be written: {exc}") from exc

                stage_labels = None
                if self.labels is not None:
                    stage_labels = os.path.join(stage, "labels.json")
                    try:
                        labels_module.save(self.labels, stage_labels)
                    except (OSError, ValueError):
                        stage_labels = None

                stage_voice_index = stage_voice_blob = None
                if self.voice_edits.count():
                    voice_dir = os.path.join(stage, "voice")
                    os.makedirs(voice_dir, exist_ok=True)
                    stage_voice_index = os.path.join(voice_dir, "edits.json")
                    stage_voice_blob = os.path.join(voice_dir, "edits.bin")
                    try:
                        self.voice_edits.to_files(
                            stage_voice_index, stage_voice_blob,
                            (self.source_disc or {}).get("digest"))
                    except OSError:
                        stage_voice_index = stage_voice_blob = None

                manifest = {
                    "format": "tomba2edit-translation-project",
                    "version": 3,
                    "cd_folder": game_folder_name,
                    "main_exe": "MAIN.EXE" if exe_path else None,
                    "sop_bin": "BIN/SOP.BIN" if sop_path else None,
                    "snd": "TOMBA2.SND" if stage_snd else None,
                    "labels": "labels.json" if stage_labels else None,
                    "voice_index": ("voice/edits.json"
                                    if stage_voice_index else None),
                    "voice_blob": ("voice/edits.bin"
                                   if stage_voice_blob else None),
                    "source_image": self.current_iso_path,
                    # What the disc is, not only where it was. An
                    # absolute path alone died the moment the project or
                    # the disc moved, and took Build Disc and the spoken
                    # dialogue with it - see disc/source_disc.
                    "source_disc": self.source_disc,
                    "editor_state": project_state,
                }
                stage_manifest = os.path.join(stage, "tomba2project.json")
                with open(stage_manifest, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, ensure_ascii=False, indent=2)

                os.replace(stage_dat, output_dat)
                os.replace(stage_idx, output_idx)
                os.replace(stage_img, output_img)
                os.replace(os.path.join(stage_cd, "tombadict.json"),
                           os.path.join(cd_dir, "tombadict.json"))
                if stage_exe:
                    os.replace(stage_exe, output_exe)
                if stage_sop:
                    os.makedirs(os.path.dirname(output_sop), exist_ok=True)
                    os.replace(stage_sop, output_sop)
                if stage_snd:
                    os.replace(stage_snd,
                               os.path.join(project_dir, "TOMBA2.SND"))
                if stage_labels:
                    os.replace(stage_labels,
                               os.path.join(project_dir, "labels.json"))
                if stage_voice_index:
                    out_voice = os.path.join(project_dir, "voice")
                    os.makedirs(out_voice, exist_ok=True)
                    os.replace(stage_voice_index,
                               os.path.join(out_voice, "edits.json"))
                    os.replace(stage_voice_blob,
                               os.path.join(out_voice, "edits.bin"))
                os.replace(stage_manifest,
                           os.path.join(project_dir, "tomba2project.json"))

        except Exception as exc:
            QMessageBox.critical(self, "Project save failed", str(exc))
            return False

        if in_place:
            # The source files have just been replaced. Drop the old
            # decoded/edit caches and reopen those exact saved files, or a
            # second save would apply the same pending edits a second time.
            self.pending_txtd_edits.clear()
            self.pending_file_edits.clear()
            self.txtd_file_states.clear()
            self.txtd_viewer.clear_cache()
            self.txt2_viewer.clear_cache()
            self.bins_viewer.clear_cache()
            self.dat_file = output_dat
            parse_idx_file(self, cd_dir)
            self._load_mainexe(output_exe)
            self._load_bins(
                ([{"name": "SOP.BIN", "size": os.path.getsize(output_sop)}]
                 if output_sop else []), output_sop)
            self._restore_translation_project_state(project_state)
            self.load_translation_tab(cd_dir)
            self.mainexe_viewer.reload_preview_font(
                cd_dir, self.preview_glyph_top())

        self._project_snapshot_path = project_dir
        if not announce:
            return True
        QMessageBox.information(
            self, "Project saved",
            "Your working copy is now self-contained:\n\n"
            f"    {project_dir}\n\n"
            "To continue later, choose File > Open Project Folder / "
            "Game Folder and select this project folder. "
            "It contains the character "
            "table and font page with the translated text, so no separate "
            "letters.json or temporary extraction is needed.")
        return True

    def _confirm_project_before_close(self, event):
        """Return False when the user cancels a close of temporary work."""
        if (self.current_iso_path and not self._project_snapshot_path
                and not self._project_file_path):
            from formats.text import translation
            has_work = (self.img_dirty or self.pending_txtd_edits
                        or self.pending_file_edits
                        or self.mainexe_viewer.has_pending_edits()
                        or self.bins_viewer.has_pending_edits()
                        or translation.active().chars)
            if has_work:
                answer = QMessageBox.question(
                    self, "Save the project first?",
                    "This disc was opened from an image, so its extracted "
                    "files are temporary. Closing now can lose the font "
                    "page, character assignments, and untranslated edits.\n\n"
                    "Yes - save a project now\n"
                    "No - close and discard the temporary working copy\n"
                    "Cancel - stay in the editor",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes)
                if answer == QMessageBox.StandardButton.Yes:
                    if not self.save_project():
                        return False
                elif answer != QMessageBox.StandardButton.No:
                    return False
        return True

    # ------------------------------------------------------------------
    # A project as one file
    # ------------------------------------------------------------------
    #
    # A .t2p is a zip of the project folder and nothing more, so both
    # halves of this are thin: opening unpacks it into a working
    # directory and opens that as a folder, saving writes the folder and
    # packs it up. Everything between still deals in folders.

    def _restore_project_extras(self, project_root, manifest):
        """Bring back the parts of a project that are not disc files.

        A replaced model or a swapped texture needs nothing here: it is
        inside the TOMBA2.DAT this project just opened. The tree's names
        and any staged voice are the two things with nowhere on the disc
        to live, so they travel as files of their own."""
        relative = manifest.get("snd")
        if relative:
            path = os.path.join(project_root, relative)
            if os.path.isfile(path):
                try:
                    with open(path, "rb") as f:
                        self.snd_edits.set_source(f.read())
                except OSError:
                    pass

        relative = manifest.get("labels")
        if relative:
            path = os.path.join(project_root, relative)
            if os.path.isfile(path):
                try:
                    self.labels = labels_module.load(path)
                    self.labels_override = self.labels
                    apply_labels(self)
                except (OSError, ValueError, labels_module.LabelError):
                    pass

        index = manifest.get("voice_index")
        blob = manifest.get("voice_blob")
        if not (index and blob):
            return
        index_path = os.path.join(project_root, index)
        blob_path = os.path.join(project_root, blob)
        if not (os.path.isfile(index_path) and os.path.isfile(blob_path)):
            return
        loaded, refused = self.voice_edits.from_files(
            index_path, blob_path,
            image=self.current_iso_path,
            disc_digest=(self.source_disc or {}).get("digest"))
        if refused:
            QMessageBox.warning(
                self, "Staged voice not restored",
                "This project has re-recorded dialogue in it, but "
                f"{refused}.\n\nEverything else opened normally. The voice "
                "edits are still in the project file and will come back if "
                "you open it with the disc they were made against.")
        elif loaded:
            self.statusBar().showMessage(
                f"{loaded} staged voice sector(s) restored.", 8000)
        self._refresh_edit_status()

    @classmethod
    def _sweep_stale_work_dirs(cls, older_than_hours=12):
        """Delete unpacked project copies left behind by earlier runs.

        A window cannot always remove its own on the way out, because
        the game's files are still open when it tries (see closeEvent),
        so they accumulate at ~16 MB each. Anything of ours older than
        half a day belongs to a session that is definitely gone; a
        younger one might be another window open right now."""
        root = tempfile.gettempdir()
        cutoff = time.time() - older_than_hours * 3600
        try:
            names = os.listdir(root)
        except OSError:
            return
        for name in names:
            if not name.startswith(cls.WORK_PREFIX):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    def _project_work_dir(self):
        """Where an open .t2p is unpacked to, created on first use.

        One per window rather than one per open, so opening a second
        project does not leave the first one's 32 MB behind; the
        directory is emptied by unpack() each time."""
        if self._project_work is None:
            self._project_work = tempfile.mkdtemp(prefix=self.WORK_PREFIX)
        return self._project_work

    def open_project_file(self, path=None):
        """Open a .t2p, asking for one if none was named."""
        if path is None:
            start = self._theme_settings.value(self.PROJECT_SETTING, "", str)
            path, _ = QFileDialog.getOpenFileName(
                self, "Open a project", start,
                f"{project_file.FILTER};;All files (*)")
        if not path:
            return False
        work = self._project_work_dir()
        try:
            manifest = project_file.unpack(path, work)
        except project_file.ProjectFileError as exc:
            QMessageBox.critical(self, "Couldn't open that project", str(exc))
            return False
        if manifest.get("format") != project_file.FORMAT:
            QMessageBox.critical(
                self, "Couldn't open that project",
                f"{os.path.basename(path)} doesn't identify itself as a "
                "Tomba 2 project.")
            return False
        self._project_file_path = path
        self._theme_settings.setValue(self.PROJECT_SETTING, path)
        self.open_folder_dialog(work)
        # open_folder_dialog names the working directory, which is a
        # temp path and means nothing to anybody. Say what was opened.
        self.folder_info_label.setText(f"Loaded project: {path}")
        return True

    def save_project_file(self, path=None):
        """Save to a .t2p, asking where if it isn't already known."""
        if not getattr(self, "dat_file", None):
            QMessageBox.information(
                self, "No disc open", "Open a Tomba! 2 disc first.")
            return False
        if path is None:
            start = self._project_file_path or self._theme_settings.value(
                self.PROJECT_SETTING, "", str)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save project", start,
                f"{project_file.FILTER};;All files (*)")
            if not path:
                return False
            if not path.lower().endswith(project_file.EXTENSION):
                path += project_file.EXTENSION

        # Written as a folder first, into the working directory, which
        # is also what rebases the editor onto the saved files - the
        # same in-place save a folder project gets. Packing is then just
        # zipping what is already correct.
        work = self._project_work_dir()
        if not self.save_translation_project(work, announce=False):
            return False
        try:
            project_file.pack(work, path)
        except project_file.ProjectFileError as exc:
            QMessageBox.critical(self, "Project save failed", str(exc))
            return False

        self._project_file_path = path
        self._theme_settings.setValue(self.PROJECT_SETTING, path)
        size = os.path.getsize(path) / (1024 * 1024)
        self.folder_info_label.setText(f"Loaded project: {path}")
        self.statusBar().showMessage(
            f"Saved {os.path.basename(path)} ({size:.0f} MB)", 8000)
        return True

    def save_project(self):
        """Save over whatever this project already is."""
        if self._project_file_path:
            return self.save_project_file(self._project_file_path)
        if self._project_snapshot_path:
            return self.save_translation_project(self._project_snapshot_path)
        return self.save_project_file()

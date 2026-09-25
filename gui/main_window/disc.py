"""The disc behind the project: finding it, opening it, writing it back.

The game's files are not the disc. The CD audio, the XA music and
the spoken dialogue only exist in a real image, so the tool keeps
track of one and knows how to go looking for it.
"""
import json
import os
import shutil
import tempfile

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from disc import source_disc
from disc.iso_handler import ISOHandler
from formats.archive.idx_parser import parse_idx_file
from formats.audio import snd_edit, voice
from formats.executable.mainbin_editor import (
    MainBinEditError,
    repack_pool as mainbin_repack_pool)
from formats.executable.sop_editor import (
    SopEditError,
    repack_pool as sop_repack_pool)


class DiscMixin:
    """The disc behind the project: finding it, opening it, writing it back.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def _disc_hints(self):
        """Folders worth searching before troubling anyone for the disc."""
        hints = []

        def add(folder):
            if folder and os.path.isdir(folder) and folder not in hints:
                hints.append(folder)

        add(self._project_snapshot_path)
        if self._project_snapshot_path:
            add(os.path.dirname(self._project_snapshot_path))
        if self.dat_file:
            add(os.path.dirname(self.dat_file))
            add(os.path.dirname(os.path.dirname(self.dat_file)))
        recent = self._theme_settings.value(self.DISC_SETTING, "", str)
        if recent:
            add(os.path.dirname(recent))
        return hints

    def attach_source_disc(self, path, quiet=False):
        """Make `path` the disc this session builds and plays from.

        A cue sheet is accepted and resolved to the data track it names,
        which is what everything downstream actually reads - and which
        is the thing a user is least likely to pick unaided, since the
        two .bin files beside it look interchangeable and are not.

        Returns the track's path, or None after saying what was wrong."""
        try:
            record = source_disc.record(path)
        except source_disc.SourceDiscError as exc:
            if not quiet:
                QMessageBox.critical(self, "That isn't a disc image", str(exc))
            return None

        track = record["path"]
        self.current_iso_path = track
        self.source_disc = record
        self._disc_search_failed = False
        self._theme_settings.setValue(self.DISC_SETTING, track)

        # Everything that plays off the disc is pointed at it here, so
        # attaching it once is enough - the Dialogues tab should never
        # have to be told about a disc the rest of the window already
        # has. A 2048-byte ISO has no usable voice in it whatever we do
        # (see formats/audio/voice), and each panel says so for itself.
        # The music and the sound effects come off the disc once and
        # are held from then on, so they can be edited and saved into a
        # project that the disc is not attached to.
        self._load_snd(track)
        for panel in (self.voice_panel, self.music_panel, self.sfx_panel):
            try:
                panel.set_image(track)
            except Exception:
                pass
        self._refresh_edit_status()
        if not quiet and not source_disc.is_raw_track(record):
            QMessageBox.information(
                self, "Opened, but not a raw track",
                f"{os.path.basename(track)} is a {record['sector_size']}-byte "
                "sector image.\n\nThe text and graphics all work, but the "
                "spoken dialogue and the CD music are not in it: extracting "
                "a disc to 2048-byte sectors throws away 12% of every audio "
                "sector, and nothing can put it back. For those, open the "
                "disc's .cue or its Track 1 .bin instead.")
        return track

    def _load_snd(self, track):
        """Take TOMBA2.SND off the disc, unless a project brought one.

        A project's own copy wins: it is the one with the edits in it,
        and the disc is only ever the place the first copy came from."""
        if self.snd_edits.loaded() and self.snd_edits.count():
            return
        try:
            data = voice.extract_file(track, "TOMBA2.SND")
        except Exception:
            data = None
        if data:
            self.snd_edits.set_source(data)

    def require_source_disc(self, reason, ask=True):
        """The disc's data track - remembered, found, or asked for.

        `reason` finishes "The original disc is needed to ...", so the
        dialog says why it appeared rather than leaving that to be
        guessed at. Returns None if there is no disc to be had."""
        current = getattr(self, "current_iso_path", None)
        if current and os.path.exists(current):
            return current

        # Searching means listing folders and fingerprinting whatever
        # images are in them. That is cheap once and wasteful on every
        # line of dialogue, which is how often the silent caller asks,
        # so a search that found nothing is not repeated until something
        # could plausibly have changed - a disc attached, or a project
        # opened, both of which clear this.
        if not self._disc_search_failed:
            found = source_disc.locate(self.source_disc, self._disc_hints())
            if found:
                return self.attach_source_disc(found, quiet=True)
            self._disc_search_failed = True
        if not ask:
            return None

        wanted = source_disc.describe(self.source_disc)
        known = bool(self.source_disc)
        answer = QMessageBox.question(
            self, "Which disc?",
            f"The original disc image is needed to {reason}.\n\n"
            + (f"This project was made from {wanted}, and it isn't where it "
               "was last seen.\n\n" if known else
               "The game's extracted files don't contain the CD audio, the "
               "spoken dialogue or the disc structure, so the image they "
               "came from is needed too.\n\n")
            + "Find it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return None

        start = next(iter(self._disc_hints()), "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the disc image this project was made from",
            start, source_disc.FILTER)
        if not path:
            return None

        if known and not source_disc.matches(path, self.source_disc):
            proceed = QMessageBox.warning(
                self, "That's a different disc",
                f"{os.path.basename(path)} isn't the image this project was "
                f"made from ({wanted}).\n\nBuilding from the wrong disc "
                "produces something that looks right and is subtly not - a "
                "different region or revision puts the game's files in "
                "different places.\n\nUse it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if proceed != QMessageBox.StandardButton.Yes:
                return None
        return self.attach_source_disc(path)

    @staticmethod
    def _legacy_source_disc(manifest):
        """A pre-fingerprint project's disc, as far as it can be known.

        Version 1 and 2 manifests stored `source_image`: one absolute
        path, no way to tell whether the file at it is still the same
        disc or even the same game. If it is still there it gets
        fingerprinted now and the project is that much more portable
        from the next save on; if it isn't, the name travels anyway, so
        the disc can be recognised beside the project and named in the
        dialog that asks for it."""
        legacy = manifest.get("source_image")
        if not legacy:
            return None
        try:
            return source_disc.record(legacy)
        except source_disc.SourceDiscError:
            return {"name": os.path.basename(legacy), "path": legacy}

    def attach_disc_dialog(self):
        """File > Attach Disc Image - the one place to hand over the
        disc when the session was started from files rather than from
        it. Everything that needs it picks it up at once."""
        start = next(iter(self._disc_hints()), "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach the disc image this project was made from",
            start, source_disc.FILTER)
        if not path:
            return
        track = self.attach_source_disc(path)
        if track:
            self.statusBar().showMessage(
                f"Disc attached: {os.path.basename(track)} - voice, music "
                "and Build Disc can use it now.", 8000)

    def voice_image_path(self):
        """The disc to read voice from, without asking for it again.

        Silent on purpose: this is reached from playback, and a modal
        dialog in the middle of clicking through dialogue would be worse
        than no sound. It still resolves a project's remembered disc, so
        opening a project and pressing play works without the disc ever
        having been opened by hand this session."""
        found = self.require_source_disc("play the spoken dialogue", ask=False)
        if found:
            return found
        return getattr(self.voice_panel, "image", None)

    def export_bin(self):
        """Write the edits into a copy of the disc's data track.

        Unlike Save ISO this keeps the disc playable: the track is
        copied byte for byte and only the sectors of the edited files
        are rewritten, so the Form 2 sectors carrying the XA music and
        voice are never touched. The audio track beside it and the cue
        sheet are copied and written to match."""
        import tempfile

        from formats.archive import bin_writer

        # A project holds the game's files, not the disc they came from,
        # so this asks for the image rather than refusing outright the
        # way it used to - "no track open" was accurate and useless, and
        # left the commonest workflow (open project, build disc) with no
        # way forward at all.
        source = self.require_source_disc(
            "build a playable disc: the CD audio and the streamed music "
            "only survive being patched into a copy of the real track")
        if not source:
            return
        if not source_disc.is_raw_track(source):
            QMessageBox.critical(
                self, "Not a raw disc track",
                f"{os.path.basename(source)} has 2048-byte sectors, so it "
                "carries no CD audio and no XA music to patch around.\n\n"
                "Build from the disc's .cue or its Track 1 .bin instead, or "
                "use Advanced > Save ISO if a silent data-only image is "
                "what you want.")
            return
        mainexe_edits = self.mainexe_viewer.all_edits()
        sop_edits = self.bins_viewer.all_edits()
        # A voice edit only counts here when it was staged against this
        # exact track - one staged against a different disc names
        # sector positions that mean nothing on this one, and writing
        # them in would corrupt whatever happens to sit there instead.
        voice_edits = (self.voice_edits
                       if self.voice_edits.image == source
                       and self.voice_edits.count() else None)
        voice_mismatch = bool(self.voice_edits.count() and not voice_edits)
        if (not self.pending_txtd_edits and not self.pending_file_edits
                and not mainexe_edits and not sop_edits
                and not self.img_dirty and not voice_edits
                # An edited sequence or a swapped sound effect is an
                # edit to TOMBA2.SND, which this writes further down -
                # leaving it out of the count made Build Disc refuse a
                # disc it was perfectly able to build.
                and not self.snd_edits.count()):
            QMessageBox.information(self, "Nothing to save",
                                    "No edits are pending.")
            return
        default = os.path.splitext(os.path.basename(source))[0] + " (edited).bin"
        target, _ = QFileDialog.getSaveFileName(
            self, "Save patched data track", default, "Disc track (*.bin)")
        if not target:
            return

        edits = self._pack_pending_txtd_edits()
        if edits is None:
            return
        edits += self._pack_pending_file_edits()
        replacements = {}
        with tempfile.TemporaryDirectory(prefix="tomba2bin_") as work:
            try:
                if edits:
                    from formats.archive.repacker import repack_files
                    dat = os.path.join(work, "TOMBA2.DAT")
                    idx = os.path.join(work, "TOMBA2.IDX")
                    repack_files(self.dat_file,
                                 os.path.join(os.path.dirname(self.dat_file),
                                              "TOMBA2.IDX"),
                                 edits, dat, idx)
                    replacements["TOMBA2.DAT"] = open(dat, "rb").read()
                    replacements["TOMBA2.IDX"] = open(idx, "rb").read()
                img = self._edited_img()
                if img is not None:
                    replacements["TOMBA2.IMG"] = img
                if mainexe_edits:
                    exe = os.path.join(work, "MAIN.EXE")
                    mainbin_repack_pool(self.mainexe_viewer.exe_path,
                                        self.mainexe_viewer.entries,
                                        mainexe_edits, exe)
                    replacements["MAIN.EXE"] = open(exe, "rb").read()
                if sop_edits:
                    sop = os.path.join(work, "SOP.BIN")
                    sop_repack_pool(self.bins_viewer.sop_viewer.sop_path,
                                    self.bins_viewer.sop_viewer.entries,
                                    sop_edits, sop)
                    replacements["SOP.BIN"] = open(sop, "rb").read()
                # The music and sound effects live in TOMBA2.SND, not
                # in the DAT, so an edited sequence reaches the disc
                # the same way MAIN.EXE's text does - as a whole file.
                if self.snd_edits.count():
                    replacements["TOMBA2.SND"] = self.snd_edits.rebuild()
            except Exception as exc:
                QMessageBox.critical(self, "Save failed",
                                     f"Could not rebuild the files: {exc}")
                return


            # An IDX whose offsets do not match the IMG beside it makes
            # every chunk unreadable, and nothing says so until the game
            # runs - see formats/images/img_writer.check.
            problems = self._img_idx_problems(replacements)
            if problems:
                QMessageBox.critical(
                    self, "Save refused",
                    "TOMBA2.IDX and TOMBA2.IMG do not agree, and shipping "
                    "them together would make the whole disc's artwork "
                    "unreadable:\n\n" + "\n".join(problems[:6]))
                return

            self.statusBar().showMessage("Copying the track...", 0)
            QApplication.processEvents()
            try:
                notes = bin_writer.patch_track(source, target, replacements)
                if voice_edits:
                    bin_writer.write_sectors(target, voice_edits.sectors)
                    notes.append(
                        f"VOICE.XA: {voice_edits.count()} sector(s) patched")
            except Exception as exc:
                QMessageBox.critical(self, "Save failed", str(exc))
                self.statusBar().clearMessage()
                return

        # The track is already written by here, so nothing this does is
        # worth losing it over - and an exception escaping a slot does
        # not raise in PyQt6, it calls qFatal() and takes the whole
        # program with it, pending edits and all.
        try:
            extra = self._copy_audio_track(source, target)
        except Exception as exc:
            extra = (f"The track was written, but its audio track and cue "
                     f"sheet were not: {exc}")
        self.statusBar().clearMessage()
        untouched = (" Every other sector is byte for byte as it was."
                    if voice_edits else
                    "\n\nEvery other sector is byte for byte as it was, so "
                    "the music and voice are untouched.")
        mismatch_note = (
            "\n\nNote: this disc also has voice edits staged, but against "
            "a different data track, so they were NOT included here - "
            "open that disc and Save BIN again to write them."
            if voice_mismatch else "")
        QMessageBox.information(
            self, "Saved",
            "Wrote:\n" + target + "\n\n" + "\n".join(notes) +
            ("\n\n" + extra if extra else "") + untouched + mismatch_note)
        # Left staged rather than cleared, same as the Dialogues tab's
        # own Export patched BIN... - writing to a second destination
        # without redoing the import is a reasonable thing to want, and
        # nothing tracks an "exported" state for a voice edit the way
        # pending_txtd_edits does for a text one.
        for address, info in self.pending_txtd_edits.items():
            self._set_txtd_tree_item_state(address, "exported")
            # Every area's occurrence of this address, not just one -
            # see the matching loop in export_iso() for why.
            for chunk_index, file_index in info["locations"]:
                if info.get("kind") == "txt2":
                    self.txt2_viewer.mark_exported(chunk_index, file_index)
                else:
                    self.txtd_viewer.mark_exported(chunk_index, file_index)
        self.pending_txtd_edits.clear()
        self.pending_file_edits.clear()
        if mainexe_edits:
            self.mainexe_viewer.mark_exported()
        if sop_edits:
            self.bins_viewer.mark_exported()
        self.img_dirty = False
        self._refresh_edit_status()

    def _copy_audio_track(self, source, target):
        """Bring the disc's audio track and a cue sheet along - see
        formats.archive.bin_writer.copy_audio_track, which this now just
        calls (also used by the Dialogues tab's own patched-BIN
        export)."""
        from formats.archive import bin_writer

        return bin_writer.copy_audio_track(source, target)

    def export_all_files(self):
        # all_edits(), not pending_edits() - every export runs against
        # the untouched source exe fresh, so it must reapply every
        # change made since load (edited AND already-exported alike),
        # not just what's newly dirty, or a previous export's changes
        # would silently get dropped from this one.
        mainexe_edits = self.mainexe_viewer.all_edits()
        sop_edits = self.bins_viewer.all_edits()
        snd_edited = self.snd_edits.count()
        if (not self.pending_txtd_edits and not self.pending_file_edits
                and not mainexe_edits and not sop_edits
                and not self.img_dirty and not snd_edited):
            QMessageBox.information(
                self, "Nothing to export",
                "No text, replaced file, MAIN.EXE, SOP.BIN, music or font "
                "page edits are pending.")
            return

        if not getattr(self, 'dat_file', None):
            QMessageBox.critical(self, "Error", "No TOMBA2 ISO is open.")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "Choose output folder for the modified DAT + IDX")
        if not out_dir:
            return

        from formats.archive.repacker import repack_files

        edits = self._pack_pending_txtd_edits()
        if edits is None:
            return
        edits += self._pack_pending_file_edits()

        original_dir = os.path.dirname(self.dat_file)
        original_idx = os.path.join(original_dir, "TOMBA2.IDX")
        output_dat = os.path.join(out_dir, "TOMBA2.DAT")
        output_idx = os.path.join(out_dir, "TOMBA2.IDX")

        try:
            repack_files(self.dat_file, original_idx, edits, output_dat, output_idx)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Failed to rebuild DAT/IDX: {e}")
            return

        output_paths = [output_dat, output_idx]
        img = self._edited_img()
        if img is not None:
            output_img = os.path.join(out_dir, "TOMBA2.IMG")
            with open(output_img, "wb") as f:
                f.write(img)
            output_paths.append(output_img)
        if mainexe_edits:
            output_exe = os.path.join(out_dir, "MAIN.EXE")
            try:
                mainbin_repack_pool(
                    self.mainexe_viewer.exe_path, self.mainexe_viewer.entries,
                    mainexe_edits, output_exe,
                )
            except MainBinEditError as e:
                QMessageBox.critical(self, "Export failed", f"Failed to rebuild MAIN.EXE: {e}")
                return
            output_paths.append(output_exe)

        if snd_edited:
            # The music and the sound effects are a whole file, not a
            # list of patches - the same way Build Disc ships them.
            output_snd = os.path.join(out_dir, "TOMBA2.SND")
            try:
                with open(output_snd, "wb") as f:
                    f.write(self.snd_edits.rebuild())
            except (OSError, snd_edit.SndEditError) as e:
                QMessageBox.critical(self, "Export failed",
                                     f"Failed to rebuild TOMBA2.SND: {e}")
                return
            output_paths.append(output_snd)

        if sop_edits:
            output_sop = os.path.join(out_dir, "SOP.BIN")
            try:
                sop_repack_pool(
                    self.bins_viewer.sop_viewer.sop_path, self.bins_viewer.sop_viewer.entries,
                    sop_edits, output_sop,
                )
            except SopEditError as e:
                QMessageBox.critical(self, "Export failed", f"Failed to rebuild SOP.BIN: {e}")
                return
            output_paths.append(output_sop)

        extras = []
        if mainexe_edits:
            extras.append("MAIN.EXE")
        if sop_edits:
            extras.append("SOP.BIN")
        if snd_edited:
            extras.append("TOMBA2.SND")
        extras_suffix = "".join(f" + {name}" for name in extras)
        QMessageBox.information(
            self, "Export complete",
            "Wrote:\n" + "\n".join(output_paths) + "\n\n"
            f"({len(edits)} disc file(s){extras_suffix} repacked.)\n\n"
            "Back up your original CD files, then copy these over them "
            "to test in-game." + ("" if img is not None else
                                  " TOMBA2.IMG is unchanged and doesn't "
                                  "need copying.")
        )
        for address, info in self.pending_txtd_edits.items():
            self._set_txtd_tree_item_state(address, "exported")
            # Every area's occurrence of this address, not just one -
            # see the matching loop in export_iso() for why.
            for chunk_index, file_index in info["locations"]:
                if info.get("kind") == "txt2":
                    self.txt2_viewer.mark_exported(chunk_index, file_index)
                else:
                    self.txtd_viewer.mark_exported(chunk_index, file_index)
        self.pending_txtd_edits.clear()
        self.pending_file_edits.clear()
        if mainexe_edits:
            self.mainexe_viewer.mark_exported()
        if sop_edits:
            self.bins_viewer.mark_exported()
        self._refresh_edit_status()

    def open_iso_dialog(self, _checked=False, iso_only=False):
        """Extract TOMBA2.DAT/IDX/IMG from a disc image into a temp
        folder and populate the tree view. See open_folder_dialog() for
        opening an already-extracted folder instead.

        `iso_only` is the File > Open ISO route. The toolbar's opener
        asks for a BIN first, because that is the one that carries the
        voice track; an ISO cannot."""
        # A different disc has a different font page, so the Translation
        # tab has to read it again next time it is looked at.
        self._translation_loaded = False
        if iso_only:
            title = "Select a Tomba! 2 ISO"
            filters = "Disc image (*.iso *.img);;All files (*)"
        else:
            title = "Select the disc's data track (Track 1)"
            filters = ("Disc data track (*.bin);;Disc image "
                       "(*.iso *.img);;All files (*)")
        iso_path, _ = QFileDialog.getOpenFileName(self, title, "", filters)
        if not iso_path:
            return

        if (self.pending_txtd_edits or self.pending_file_edits
                or self.mainexe_viewer.has_pending_edits()
                or self.bins_viewer.has_pending_edits()):
            proceed = QMessageBox.question(
                self, "Discard pending edits?",
                f"You have {len(self.pending_txtd_edits)} TXTD/TXT2 edit(s), "
                f"{len(self.mainexe_viewer.pending_edits())} MAIN.EXE edit(s), and "
                f"{len(self.bins_viewer.pending_edits())} SOP.BIN edit(s) that haven't been "
                "exported yet. Opening a new ISO will discard them.\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        # Starting a fresh ISO always throws away whatever was extracted
        # for the previous one.
        self.voice_panel.reset_disc()
        if self.iso_handler:
            self.iso_handler.cleanup()
        self.iso_handler = ISOHandler()
        self.pending_txtd_edits.clear()
        self.pending_file_edits.clear()
        self.txtd_file_states.clear()
        self.txtd_viewer.clear_cache()
        self.txt2_viewer.clear_cache()
        self.mainexe_viewer.clear_cache()
        self.bins_viewer.clear_cache()
        self.current_iso_path = None
        self._project_snapshot_path = None
        self._refresh_edit_status()

        try:
            self.iso_handler.extract_iso(iso_path)
        except Exception as e:
            self.iso_handler.cleanup()
            self.iso_handler = None
            self.folder_info_label.setText("Select a Tomba! 2 ISO file to begin")
            QMessageBox.critical(self, "Error", f"Failed to read ISO:\n\n{e}")
            return

        extracted_dir = self.iso_handler.get_temp_dir()
        self.apply_build_table(extracted_dir)
        try:
            parse_idx_file(self, extracted_dir)
        except Exception as e:
            self.iso_handler.cleanup()
            self.iso_handler = None
            self.dat_file = None
            self.folder_info_label.setText("Select a Tomba! 2 ISO file to begin")
            QMessageBox.critical(self, "Error", f"Failed to parse TOMBA2.IDX from this ISO:\n\n{e}")
            return

        self._load_mainexe(self.iso_handler.extracted_files.get("MAIN.EXE"))
        self._load_bins(self.iso_handler.bin_overlays, self.iso_handler.extracted_files.get("SOP.BIN"))

        self.current_iso_path = iso_path
        self._load_snd(iso_path)
        self.folder_info_label.setText(f"Loaded ISO: {iso_path}")
        # The Dialogues tab reads its audio out of the same track, so
        # opening the disc is enough - it should not have to be opened a
        # second time over there. A 2048-byte ISO simply has no voice in
        # it, and the panel says so itself.
        self.voice_panel.set_image(iso_path)
        self.music_panel.set_image(iso_path)
        self.sfx_panel.set_image(iso_path)
        self.movie_panel.set_source(iso_path)
        self._load_level_editor()
        self._load_img_browser()
        # If Translation is already the tab in front, it has been
        # waiting for a disc rather than for a click.
        self._tab_changed()

    def open_folder_dialog(self, folder=None):
        """Open a project folder or an extracted game-files folder.

        `folder` is the one to open, or None to ask. Passing it is how a
        .t2p opens: it is unpacked to a working directory and that is
        handed here, so nothing below had to learn what an archive is -
        see open_project_file() and formats/archive/project_file."""
        # A different folder may carry a different saved translation.
        self._translation_loaded = False
        if folder is None:
            folder = QFileDialog.getExistingDirectory(
                self,
                "Select a project folder or a Tomba! 2 game-files folder")
        if not folder:
            return

        project_root = None
        manifest = {}
        for possible_root in (folder, os.path.dirname(folder)):
            possible = os.path.join(possible_root, "tomba2project.json")
            if not os.path.isfile(possible):
                continue
            try:
                with open(possible, encoding="utf-8") as source:
                    candidate = json.load(source)
                if candidate.get("format") == "tomba2edit-translation-project":
                    project_root, manifest = possible_root, candidate
                    break
            except (OSError, ValueError, TypeError):
                continue

        required_files = ("TOMBA2.DAT", "TOMBA2.IDX", "TOMBA2.IMG")

        def has_required_files(path):
            return all(os.path.exists(os.path.join(path, name)) for name in required_files)

        candidates = []
        if project_root:
            declared = manifest.get("cd_folder")
            if declared:
                candidates.append(os.path.join(project_root, declared))
            candidates.extend((os.path.join(project_root, "Tomba 2 Game Files"),
                               os.path.join(project_root, "CD")))
        candidates.extend((folder,
                           os.path.join(folder, "Tomba 2 Game Files"),
                           os.path.join(folder, "CD")))
        cd_folder = next((path for path in candidates
                          if has_required_files(path)), None)
        if cd_folder is None:
            QMessageBox.critical(
                self, "Error",
                "Couldn't find TOMBA2.DAT, TOMBA2.IDX and TOMBA2.IMG. "
                "Select the project folder itself, its 'Tomba 2 Game "
                "Files' folder, or an extracted game folder."
            )
            return

        if (self.pending_txtd_edits or self.pending_file_edits
                or self.mainexe_viewer.has_pending_edits()
                or self.bins_viewer.has_pending_edits()):
            proceed = QMessageBox.question(
                self, "Discard pending edits?",
                f"You have {len(self.pending_txtd_edits)} TXTD/TXT2 edit(s), "
                f"{len(self.mainexe_viewer.pending_edits())} MAIN.EXE edit(s), and "
                f"{len(self.bins_viewer.pending_edits())} SOP.BIN edit(s) that haven't been "
                "exported yet. Opening a new folder will discard them.\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        # No extracted-file tree can stand in for the image, so
        # export_iso() still has to refuse until one is attached.
        self.voice_panel.reset_disc()
        if self.iso_handler:
            self.iso_handler.cleanup()
        self.iso_handler = None
        self.current_iso_path = None
        # What the project says its disc was. Kept even when the file
        # itself has gone missing: it is what lets require_source_disc()
        # find it again, or name it when it has to ask. Opening a plain
        # game folder leaves this None, which is honest - nothing there
        # records which disc the files came out of.
        self.source_disc = (manifest.get("source_disc")
                            or self._legacy_source_disc(manifest))
        self._disc_search_failed = False
        self._project_snapshot_path = project_root
        self.pending_txtd_edits.clear()
        self.pending_file_edits.clear()
        self.txtd_file_states.clear()
        self.txtd_viewer.clear_cache()
        self.txt2_viewer.clear_cache()
        self.mainexe_viewer.clear_cache()
        self.bins_viewer.clear_cache()
        self._refresh_edit_status()

        try:
            self.apply_build_table(cd_folder)
            parse_idx_file(self, cd_folder)
        except Exception as e:
            self.dat_file = None
            self.folder_info_label.setText("Select a Tomba! 2 ISO file to begin")
            QMessageBox.critical(self, "Error", f"Failed to parse TOMBA2.IDX from this folder:\n\n{e}")
            return

        # MAIN.EXE decoding consults the active character assignment too.
        # Load the project's table before parsing the executable; doing this
        # afterwards left its editor populated with {$89}-style fallbacks
        # until another manual refresh.
        self.load_translation_tab(cd_folder)

        # MAIN.EXE usually sits alongside the CD folder.  When the user
        # selects CD itself, ``folder`` and ``cd_folder`` are identical,
        # so its parent must be checked explicitly as well.  Otherwise a
        # valid project opens with an empty, apparently unusable MAIN.EXE
        # tab despite project/MAIN.EXE being present.
        mainexe_path = None
        declared_exe = (os.path.join(project_root, manifest.get("main_exe"))
                        if project_root and manifest.get("main_exe") else None)
        exe_candidates = ([declared_exe] if declared_exe else []) + [
            os.path.join(candidate_dir, "MAIN.EXE")
            for candidate_dir in (folder, cd_folder, os.path.dirname(cd_folder))]
        for candidate in exe_candidates:
            if candidate and os.path.exists(candidate):
                mainexe_path = candidate
                break
        self._load_mainexe(mainexe_path)

        # BIN/ sits alongside MAIN.EXE, same two candidate locations.
        bin_dir = None
        bin_roots = ([project_root] if project_root else []) + [folder, cd_folder]
        for candidate_dir in bin_roots:
            candidate = os.path.join(candidate_dir, "BIN")
            if os.path.isdir(candidate):
                bin_dir = candidate
                break
        overlays = []
        sop_path = None
        if bin_dir:
            for name in os.listdir(bin_dir):
                full = os.path.join(bin_dir, name)
                if os.path.isfile(full):
                    overlays.append({"name": name.upper(), "size": os.path.getsize(full)})
                    if name.upper() == "SOP.BIN":
                        sop_path = full
        self._load_bins(overlays, sop_path)
        self._load_level_editor()
        self._load_img_browser()
        # MOVIE sits where MAIN.EXE and BIN do - beside the CD folder,
        # or inside it depending on how the disc was extracted.
        for candidate_dir in (folder, cd_folder):
            self.movie_panel.set_source(candidate_dir)
            if self.movie_panel.movies:
                break

        # Load the saved character table/font page now, even if Translation
        # is not the visible tab, then refresh MAIN.EXE's game-font preview.
        # This removes the old "open project, click around until a tab wakes
        # up" behavior.
        self.load_translation_tab(cd_folder)
        self.mainexe_viewer.reload_preview_font(
            cd_folder, self.preview_glyph_top())
        if project_root:
            self._restore_project_extras(project_root, manifest)
            self._restore_translation_project_state(
                manifest.get("editor_state"))
            self.folder_info_label.setText(
                f"Loaded project: {project_root}")
        else:
            self.folder_info_label.setText(f"Loaded game files: {cd_folder}")
        selected = self.tree_view.selectionModel().selectedIndexes()
        if selected:
            self.on_tree_selection_changed()

    def export_iso(self):
        """Rebuild the currently opened disc as a new .iso, applying any
        pending TXTD edits to TOMBA2.DAT/IDX along the way. Every other
        file and folder on the disc (TOMBA2.IMG, the executable, movies,
        etc.) is carried over from the original image unchanged, so this
        produces a complete, ready-to-play image instead of the two loose
        files 'Export Files' hands back."""
        if not self.current_iso_path or not self.iso_handler or not getattr(self, 'dat_file', None):
            QMessageBox.critical(self, "Error", "No TOMBA2 ISO is open.")
            return

        edits = []
        if self.pending_txtd_edits:
            edits = self._pack_pending_txtd_edits()
            if edits is None:
                return
        edits += self._pack_pending_file_edits()
        # all_edits(), not pending_edits() - see export_all_files()'s
        # own comment on this for why.
        mainexe_edits = self.mainexe_viewer.all_edits()
        sop_edits = self.bins_viewer.all_edits()
        has_any_edits = (bool(edits) or bool(mainexe_edits)
                         or bool(sop_edits) or self.img_dirty)

        default_name = os.path.splitext(os.path.basename(self.current_iso_path))[0]
        default_name += "_edited.iso" if has_any_edits else "_copy.iso"
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Save rebuilt ISO", default_name, "ISO Image (*.iso)"
        )
        if not output_path:
            return

        import shutil
        import tempfile
        from formats.archive.repacker import repack_files
        from disc.iso_builder import build_iso

        replacements = {}
        tmp_repack_dir = None
        try:
            if edits or mainexe_edits or sop_edits:
                tmp_repack_dir = tempfile.mkdtemp(prefix="tomba2edit_repack_")

            if edits:
                original_idx = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
                tmp_dat = os.path.join(tmp_repack_dir, "TOMBA2.DAT")
                tmp_idx = os.path.join(tmp_repack_dir, "TOMBA2.IDX")
                repack_files(self.dat_file, original_idx, edits, tmp_dat, tmp_idx)
                with open(tmp_dat, "rb") as f:
                    replacements["TOMBA2.DAT"] = f.read()
                with open(tmp_idx, "rb") as f:
                    replacements["TOMBA2.IDX"] = f.read()

            if mainexe_edits:
                tmp_exe = os.path.join(tmp_repack_dir, "MAIN.EXE")
                try:
                    mainbin_repack_pool(
                        self.mainexe_viewer.exe_path, self.mainexe_viewer.entries,
                        mainexe_edits, tmp_exe,
                    )
                except MainBinEditError as e:
                    QMessageBox.critical(self, "Export failed", f"Failed to rebuild MAIN.EXE:\n\n{e}")
                    return
                with open(tmp_exe, "rb") as f:
                    replacements["MAIN.EXE"] = f.read()

            if sop_edits:
                tmp_sop = os.path.join(tmp_repack_dir, "SOP.BIN")
                try:
                    sop_repack_pool(
                        self.bins_viewer.sop_viewer.sop_path, self.bins_viewer.sop_viewer.entries,
                        sop_edits, tmp_sop,
                    )
                except SopEditError as e:
                    QMessageBox.critical(self, "Export failed", f"Failed to rebuild SOP.BIN:\n\n{e}")
                    return
                with open(tmp_sop, "rb") as f:
                    replacements["SOP.BIN"] = f.read()

            img = self._edited_img()
            if img is not None:
                replacements["TOMBA2.IMG"] = img
            build_iso(self.current_iso_path, replacements, output_path)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"Failed to rebuild ISO:\n\n{e}")
            return
        finally:
            if tmp_repack_dir:
                shutil.rmtree(tmp_repack_dir, ignore_errors=True)

        extras = []
        if mainexe_edits:
            extras.append("MAIN.EXE")
        if sop_edits:
            extras.append("SOP.BIN")
        extras_suffix = "".join(f" + {name}" for name in extras)
        summary = (
            f"({len(edits)} disc file(s){extras_suffix} repacked into it.)"
            if has_any_edits else
            "(No pending edits - this is an unmodified copy of the opened disc.)"
        )
        QMessageBox.information(
            self, "ISO export complete",
            f"Wrote:\n{output_path}\n\n{summary}\n\n"
            "Note: this is a single-track, data-only ISO. If you opened a "
            "multi-track BIN/CUE with CD audio, that audio isn't part of "
            "this file - keep using the BIN/CUE for in-game music, and use "
            "this ISO to check the edits landed correctly."
        )
        if edits:
            for address, info in self.pending_txtd_edits.items():
                self._set_txtd_tree_item_state(address, "exported")
                # Every area's occurrence of this address needs marking,
                # not just one: whichever the viewer currently has open
                # updates its visible rows, and every other one lands in
                # its state cache for the next time it's opened (see
                # TXTDViewer.mark_exported).
                for chunk_index, file_index in info["locations"]:
                    if info.get("kind") == "txt2":
                        self.txt2_viewer.mark_exported(chunk_index, file_index)
                    else:
                        self.txtd_viewer.mark_exported(chunk_index, file_index)
            self.pending_txtd_edits.clear()
            self.pending_file_edits.clear()
        self.pending_file_edits.clear()
        if mainexe_edits:
            self.mainexe_viewer.mark_exported()
        if sop_edits:
            self.bins_viewer.mark_exported()
        self._refresh_edit_status()

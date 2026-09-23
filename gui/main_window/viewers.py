"""Opening the right viewer for the selected row.

One row can be any of a dozen formats, and each has its own tab,
its own parser and its own idea of what else it needs loaded
first. This is where a selection becomes a view.
"""
import os
import struct

from PIL.ImageQt import ImageQt
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QMessageBox
from formats.collision.scld_parser import find_area_scld_location
from psx.vram_viewer import vram_index_image
from gui.main_window.common import _export_name, _panel_player


class ViewerDispatchMixin:
    """Opening the right viewer for the selected row.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def _load_img_browser(self):
        """Point the IMG tab at the disc that just opened."""
        folder = os.path.dirname(self.dat_file) if self.dat_file else None
        self.img_browser.load(folder)

    def _tab_changed(self, index=None):
        """Fill the Translation tab the first time it is looked at.

        Called both when the tab is switched to and when a disc finishes
        opening, because either can be the moment it first has something
        to show - switching to it before opening a disc used to leave it
        empty for good, since the switch was the only thing listened
        for."""
        if index is None:
            index = self.main_tabs.currentIndex()
        if self.main_tabs.widget(index) is not self.translation_tab:
            return
        if self._translation_loaded or not self.dat_file:
            return
        self.load_translation_tab()

    def _refresh_playback_status(self, *_args):
        """Add "♫" to a Dialogues/Music/SFX tab's name while that tab's
        own player is actually making sound, and take it off again the
        moment it stops or pauses - connected to all three players'
        playbackStateChanged so this runs on every play, pause, stop,
        and track change, whichever tab it happens on."""
        from PyQt6.QtMultimedia import QMediaPlayer

        # Looked up rather than remembered: a tab added in front of
        # these would otherwise put the note on somebody else's.
        for label, panel in self._playback_tabs:
            playing = (_panel_player(panel).playbackState()
                      == QMediaPlayer.PlaybackState.PlayingState)
            self.main_tabs.setTabText(self.main_tabs.indexOf(panel),
                                      f"{label} ♫" if playing else label)

    def _refresh_edit_status(self):
        """Status bar text AND the tabs' own "*" unsaved-marker, both
        reflecting every pending-edit source at once - called after
        each edit AND after a successful export, so neither goes stale
        showing "pending" once everything's just been saved
        (mark_exported() doesn't emit content_changed, so nothing else
        would refresh this)."""
        n_txtd = len(self.pending_txtd_edits)
        n_files = len(self.pending_file_edits)
        n_mainexe = len(self.mainexe_viewer.pending_edits())
        n_sop = len(self.bins_viewer.pending_edits())
        # Sequences and swapped sound effects, both edits to TOMBA2.SND.
        n_seq = len(self.snd_edits.sequences)
        n_sfx = len(self.snd_edits.sounds)

        staged = n_txtd or n_files
        # By widget rather than by index: these were fixed positions, so
        # inserting a tab anywhere before BINs renamed the wrong ones.
        for widget, label, dirty in (
                (self.splitter, "Indexed View (IDX)", staged),
                (self.dat_panel, "Data View (DAT)", staged),
                (self.mainexe_viewer, "MAIN.EXE", n_mainexe),
                (self.bins_viewer, "BINs", n_sop),
                (self.music_panel, "Music", n_seq),
                (self.sfx_panel, "SFX", n_sfx)):
            at = self.main_tabs.indexOf(widget)
            if at >= 0:
                self.main_tabs.setTabText(at, label + ("*" if dirty else ""))

        renamed = " Names have been changed - File > Export Labels to keep them."             if getattr(self, "labels_dirty", False) else ""

        if not (n_txtd or n_files or n_mainexe or n_sop or n_seq or n_sfx):
            self.statusBar().showMessage(
                ("No pending edits." + renamed) if renamed else "No pending edits.")
        else:
            # Built from whatever is actually pending rather than
            # listing every kind with a zero beside it - the line is
            # read at a glance and five zeroes bury the one number
            # that is not.
            parts = []
            for count, one, many in (
                    (n_txtd, "disc file", "disc files"),
                    (n_files, "replaced file", "replaced files"),
                    (n_mainexe, "MAIN.EXE entry", "MAIN.EXE entries"),
                    (n_sop, "SOP.BIN line", "SOP.BIN lines"),
                    (n_seq, "sequence", "sequences"),
                    (n_sfx, "sound effect", "sound effects")):
                if count:
                    parts.append(f"{count} {one if count == 1 else many}")
            listed = parts[0] if len(parts) == 1 else (
                ", ".join(parts[:-1]) + " and " + parts[-1])
            self.statusBar().showMessage(
                f"Pending edits: {listed} - use Build Disc or Save "
                f"Project when ready.{renamed}")

    def on_tree_selection_changed(self):
        try:
            selected_indexes = self.tree_view.selectionModel().selectedIndexes()
            if selected_indexes:
                selected_index = selected_indexes[0]
                selected_item = self.tree_view.model().itemFromIndex(selected_index)
                item_name = selected_item.data(Qt.ItemDataRole.DisplayRole)
                print(f"Selected Item: {item_name}")

                # The IMG chunk itself - the shard table, not the VRAM
                # it decompresses into. See formats/images/img_viewer.py.
                if item_name.endswith('.IMG'):
                    data = selected_item.data(Qt.ItemDataRole.UserRole) or ()
                    if len(data) == 4 and data[0] == "img_chunk":
                        _kind, start, size, img_path = data
                        try:
                            with open(img_path, "rb") as IMG:
                                IMG.seek(start)
                                chunk = IMG.read(size)
                        except OSError as e:
                            QMessageBox.critical(self, "Error",
                                                 f"Couldn't read TOMBA2.IMG:\n\n{e}")
                            return
                        self.widgets_area.setCurrentWidget(self.widgets["IMG"])
                        # Lets the Textured toggle go looking for this
                        # area's own SMST/SPRT/BGMP - see
                        # psx/vram_preview.py. None (an id it
                        # can't reach) is a fine fallback: the toggle
                        # just says so instead of finding anything.
                        area_chunk = self._area_chunk_index(selected_item)
                        if area_chunk is not None and self.dat_file:
                            self.img_viewer.set_area_source(
                                os.path.join(os.path.dirname(self.dat_file),
                                            "TOMBA2.IDX"),
                                self.dat_file, area_chunk)
                        self.img_viewer.load_chunk(chunk, item_name, start)
                    return

                # An area's VRAM: Textured, plain and CVRAM views.
                data = selected_item.data(Qt.ItemDataRole.UserRole) or ()
                if item_name.endswith('.VRAM') and len(data) == 5                         and data[0] == "vram_uncompressed":
                    _kind, chunk_index, img_start, size, img_path = data
                    try:
                        with open(img_path, "rb") as IMG:
                            IMG.seek(img_start)
                            imgdata = IMG.read(size)
                    except OSError as e:
                        QMessageBox.critical(self, "Error",
                                             f"Couldn't read TOMBA2.IMG:\n\n{e}")
                        return
                    self.widgets_area.setCurrentWidget(self.widgets["VRAM"])
                    self.vram_viewer.set_area_source(
                        os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX"),
                        self.dat_file, chunk_index)
                    self.vram_viewer.load_area(imgdata, item_name, img_start)
                    return

                # selection handling code...
                additional_data = selected_item.data(Qt.ItemDataRole.UserRole)
                if additional_data:
                    id = additional_data[0]
                    if isinstance(id, int):
                        dat_start, offset = additional_data[1], additional_data[2]
                        # This SDAT entry's own byte length (idx_parser.py's
                        # `size`) - named distinctly from the unrelated
                        # `chunk_size` (the fixed 0x800 IDX record stride)
                        # used elsewhere in this same method for VRAM.
                        entry_size = additional_data[3] if len(additional_data) > 3 else None
                        print(f"ID: {id:X}, DAT Start: {dat_start:X}, Offset: {offset:X}")
                    elif id == "trail":
                        # A trail file is named by an absolute address
                        # rather than an area-relative offset, and has no
                        # id at all - its type was read out of its bytes
                        # (formats/archive/idx_parser._trail_type). Presented to
                        # the viewers below the same way an SDAT entry is.
                        _, address, entry_size, _ = additional_data
                        dat_start, offset, id = address, 0, None
                        print(f"Trail file at {address:X}, {entry_size} bytes")
                    else:
                        print(f"Special file type: {id}")

                    # Determine file type and get appropriate widget
                    file_type = item_name.split('.')[-1].upper() if '.' in item_name else "DEFAULT"
                    widget = self.widgets.get(file_type, self.widgets["DEFAULT"])
                    self.widgets_area.setCurrentWidget(widget)

                    if widget == self.widgets["TXTD"]:
                        file_path = selected_item.data(Qt.ItemDataRole.UserRole + 1)
                        print(f"File path: {file_path}")
                        if file_path:
                            try:
                                if self.dat_file:
                                    print("Loading TXTD data...")
                                    txtd_chunk_info = selected_item.data(Qt.ItemDataRole.UserRole + 2)
                                    if txtd_chunk_info:
                                        txtd_chunk_index, txtd_file_index = txtd_chunk_info
                                    else:
                                        txtd_chunk_index, txtd_file_index = (0, 0)
                                    self.txtd_viewer.preview.set_source(
                                        os.path.dirname(self.dat_file),
                                        self.preview_glyph_top())
                                    self.txtd_viewer.load_txtd_data(
                                        self.dat_file, dat_start, offset,
                                        chunk_index=txtd_chunk_index, file_index=txtd_file_index, id_val=id
                                    )
                                    self.txtd_viewer.set_voice_source(
                                        self.voice_image_path(),
                                        self.overlay_for_area(txtd_chunk_index),
                                        replace_overlay=True)
                                else:
                                    QMessageBox.critical(self, "Error", "DAT file not loaded.")
                            except Exception as e:
                                print(f"Error loading TXTD file: {e}")
                                QMessageBox.critical(self, "Error", f"Failed to load TXTD file: {e}")

                    elif widget == self.widgets["TXT2"]:  # also handles TXT1 rows
                        file_path = selected_item.data(Qt.ItemDataRole.UserRole + 1)
                        print(f"File path: {file_path}")
                        if file_path:
                            try:
                                if self.dat_file:
                                    print("Loading TXT2 data...")
                                    txt2_chunk_info = selected_item.data(Qt.ItemDataRole.UserRole + 2)
                                    if txt2_chunk_info:
                                        txt2_chunk_index, txt2_file_index = txt2_chunk_info
                                    else:
                                        txt2_chunk_index, txt2_file_index = (0, 0)
                                    # TXT1 is always the dialogue (big)
                                    # font; TXT2 (id 3) is always the
                                    # system (small) one - the same
                                    # widget shows both, so which size
                                    # applies has to be set per load.
                                    self.txt2_viewer.preview.set_big(id != 3)
                                    self.txt2_viewer.preview.set_source(
                                        os.path.dirname(self.dat_file),
                                        self.preview_glyph_top())
                                    self.txt2_viewer.load_txt2_data(
                                        self.dat_file, dat_start, offset,
                                        chunk_index=txt2_chunk_index, file_index=txt2_file_index, id_val=id,
                                        size=entry_size
                                    )
                                else:
                                    QMessageBox.critical(self, "Error", "DAT file not loaded.")
                            except Exception as e:
                                print(f"Error loading TXT2 file: {e}")
                                QMessageBox.critical(self, "Error", f"Failed to load TXT2 file: {e}")

                    elif widget == self.widgets["MDAT"]:
                        try:
                            # Step 1: Try to find AREA_XX from parent or grandparent
                            area_name = None
                            parent = selected_item.parent()
                            if parent:
                                grandparent = parent.parent()
                                if grandparent and grandparent.text().startswith("AREA_"):
                                    area_name = grandparent.text()
                                elif parent.text().startswith("AREA_"):
                                    area_name = parent.text()

                            chunk_index = None
                            if area_name:
                                area_number = area_name.split("_")[1].split()[0]  # e.g., "04" from "AREA_04 (41)"
                                try:
                                    chunk_index = int(area_number, 16)
                                    img_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IMG")
                                    idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")

                                    with open(img_path, "rb") as IMG, open(idx_path, "rb") as IDX:
                                        chunk_size = 0x800
                                        IDX.seek(chunk_index * chunk_size)
                                        img_start, img_end, _, _, _ = struct.unpack("<5I", IDX.read(20))
                                        IMG.seek(img_start)
                                        imgdata = IMG.read(img_end - img_start)

                                        # Step 2: decode VRAM and send to MDATViewer
                                        from psx.vram_viewer import VRAMViewer  # make sure it's available
                                        vram_img_result = self.vram_viewer.process_vram(imgdata)

                                        if isinstance(vram_img_result, tuple):
                                            vram_img, vram_bytes = vram_img_result
                                        else:
                                            vram_img = vram_img_result
                                            vram_bytes = None

                                        qimage = ImageQt(vram_img).copy()

                                        # Remove second call completely, just do once correctly:
                                        if vram_img.mode != "RGBA":
                                            qimage = QImage(vram_img.tobytes(), vram_img.width, vram_img.height,
                                                            QImage.Format.Format_RGB888)
                                        else:
                                            qimage = QImage(vram_img.tobytes(), vram_img.width, vram_img.height,
                                                            QImage.Format.Format_RGBA8888)

                                        self.mdat_viewer.set_vram_image(qimage, vram_bytes)
                                        self._push_clut_choices(
                                            self.mdat_viewer.model_data,
                                            vram_bytes, item_name, chunk_index)

                                except Exception as e:
                                    print(f"❌ Could not load VRAM for AREA_{area_number}: {e}")
                            if self.dat_file:
                                print("Loading MDAT data...")
                                self.mdat_viewer.export_name = _export_name(selected_item)
                                success = self.mdat_viewer.load_mdat_data(self.dat_file, dat_start, offset)
                                if not success:
                                    QMessageBox.critical(self, "Error", "Failed to load MDAT data")
                                self.mdat_panel.populate()

                                # The room's animated textures - palettes
                                # out of the area's overlay, UV frames off
                                # its own texture pages. After the model,
                                # which is what says which of the area's
                                # animations this room actually uses.
                                self.mdat_viewer.load_animations(
                                    self.overlay_for_area(chunk_index)
                                    if chunk_index is not None else None)

                                # The drawmap at the head of this same
                                # entry - never fatal, since the 3D view
                                # stands on its own if it won't parse.
                                self.drwa_viewer.load_drwa_data(
                                    self.dat_file, dat_start, offset, entry_size,
                                    chunk_index=chunk_index)

                                self.mdat_viewer.load_collision_data(None, None, None, None)
                                if chunk_index is not None:
                                    try:
                                        idx_path = os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX")
                                        scld_location = find_area_scld_location(idx_path, chunk_index)
                                        if scld_location:
                                            scld_dat_start, scld_offset, scld_size = scld_location
                                            self.mdat_viewer.load_collision_data(
                                                self.dat_file, scld_dat_start, scld_offset, scld_size)
                                    except Exception as e:
                                        print(f"Could not load collision data for AREA_{chunk_index:02X}: {e}")
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading MDAT file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load MDAT file: {e}")

                    elif widget == self.widgets["SMST"]:
                        # The VRAM has to be in place before the model
                        # is parsed - the CLUTs are cut out of it while
                        # the buffers are built - and it needs AREA_01
                        # merged in, which is where every character
                        # model's texture pages actually live.
                        try:
                            if self.dat_file:
                                print("Loading SMST data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                vram_bytes = self._load_area_vram_bytes(
                                    chunk_index, merge_common=True)
                                self.smst_viewer.set_vram(
                                    vram_bytes,
                                    vram_index_image(vram_bytes) if vram_bytes else None)
                                success = self.smst_viewer.load_smst_data(
                                    self.dat_file, dat_start + offset, entry_size)
                                self.smst_panel.populate_table()
                                # Let a pasted part be staged as an
                                # ordinary whole-file replacement, so it
                                # goes out through the same repack that
                                # can resize a DAT entry. _stage_file_edit
                                # is also what catches up every other
                                # open view of the same model - see
                                # _refresh_open_model_views.
                                self.smst_panel.cd_folder = os.path.dirname(
                                    self.dat_file)
                                self.smst_panel.img_written = (
                                    self._note_img_written)
                                self.smst_panel.stage_edit = (
                                    lambda blob, label, item=selected_item:
                                    self._stage_file_edit(item, blob, label))
                                # So the VRAM view's CLUT list offers the
                                # palettes this model actually samples,
                                # rather than making the user find them.
                                self._push_clut_choices(
                                    self.smst_viewer.model_data,
                                    vram_bytes, item_name, chunk_index)
                                # Find this model's skeleton now, so
                                # exporting it writes a rigged file
                                # rather than a bag of loose parts. It
                                # only sets up the export - the view
                                # itself still shows the packed model.
                                self.smst_viewer.export_name = _export_name(selected_item)
                                self.smst_viewer.export_bones = self._bones_for_model(
                                    self.smst_viewer.model_data, chunk_index,
                                    dat_start + offset)
                                # The area's animated textures - an asset
                                # pack animates out of the same overlay
                                # table its room does, and off the same
                                # texture pages.
                                self.smst_viewer.load_animations(
                                    self.overlay_for_area(chunk_index)
                                    if chunk_index is not None else None)
                                if not success:
                                    QMessageBox.critical(
                                        self, "Error",
                                        "Failed to load SMST data - see the console "
                                        "for what didn't read.")
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading SMST file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load SMST file: {e}")

                    elif widget == self.widgets["ANMP"]:
                        # An animation names no model, so the viewer is
                        # handed every SMST on the disc to choose from
                        # (see ANMPViewer.load_anmp_data).
                        try:
                            if self.dat_file:
                                print("Loading ANMP data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                vram_bytes = self._load_area_vram_bytes(
                                    chunk_index, merge_common=True)
                                self.anmp_viewer.set_approvals(
                                    self._approved_pairings())
                                self.anmp_viewer.set_skeleton_types(
                                    self._skeleton_types())
                                self.anmp_viewer.set_skeleton_sources(
                                    self._skeleton_sources(chunk_index))
                                self.anmp_viewer.export_name = _export_name(selected_item)
                                self.anmp_viewer.load_anmp_data(
                                    self.dat_file, dat_start + offset, entry_size,
                                    candidates=self._smst_candidates(chunk_index),
                                    vram_bytes=vram_bytes,
                                    vram_image=(vram_index_image(vram_bytes)
                                                if vram_bytes else None),
                                    area_membership=self.area_membership,
                                    current_area=chunk_index,
                                    resource_id=id,
                                    overlay_path=self.overlay_for_area(chunk_index),
                                    model_skeletons=self._model_skeletons(chunk_index),
                                    # The main loop waits two video blanks per
                                    # simulation update. Sequence durations are
                                    # update ticks, hence 25 PAL / 30 NTSC.
                                    tick_rate=(25 if self.build in {
                                        "eu-retail", "fr-retail", "de-retail",
                                        "sp-retail", "it-retail"} else 30),
                                    model_vram_provider=(
                                        lambda model_address, area=chunk_index:
                                        self._anmp_model_vram(model_address, area)),
                                    preferred=self._preferred_models(selected_item))
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading ANMP file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load animation: {e}")

                    elif widget == self.widgets["SCLD"]:
                        try:
                            if self.dat_file:
                                print("Loading SCLD data...")
                                area_name = None
                                parent = selected_item.parent()
                                if parent:
                                    grandparent = parent.parent()
                                    if grandparent and grandparent.text().startswith("AREA_"):
                                        area_name = grandparent.text()
                                    elif parent.text().startswith("AREA_"):
                                        area_name = parent.text()
                                chunk_index = None
                                if area_name:
                                    area_number = area_name.split("_")[1].split()[0]
                                    try:
                                        chunk_index = int(area_number, 16)
                                    except ValueError:
                                        chunk_index = None
                                self.scld_viewer.export_name = _export_name(selected_item)
                                success = self.scld_viewer.load_scld_data(
                                    self.dat_file, dat_start, offset, entry_size, chunk_index=chunk_index)
                                if success:
                                    self.scld_panel.populate_table()
                                else:
                                    QMessageBox.critical(self, "Error", "Failed to load SCLD data")
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading SCLD file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load SCLD file: {e}")

                    elif widget == self.widgets["DRWB"]:
                        # Needs the IDX as well as the DAT: the map is
                        # compared against its area's MDATs, and which
                        # of them it belongs to is measured rather than
                        # assumed (see DRWBViewer._match_level).
                        try:
                            if self.dat_file:
                                print("Loading DRWB data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                idx_path = os.path.join(
                                    os.path.dirname(self.dat_file), "TOMBA2.IDX")
                                self.drwb_viewer.load_drwb_data(
                                    self.dat_file, dat_start, offset, entry_size,
                                    chunk_index=chunk_index, idx_path=idx_path)
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading DRWB file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load DRWB file: {e}")

                    elif widget in (self.widgets["SPRT"], self.widgets["BGMP"]):
                        # Both formats are nothing but references into the
                        # area's VRAM, and both still parse and lay out
                        # without it - a missing or unreadable VRAM only
                        # costs the artwork, so it's never fatal here.
                        kind = "SPRT" if widget == self.widgets["SPRT"] else "BGMP"
                        try:
                            if self.dat_file:
                                print(f"Loading {kind} data...")
                                chunk_index = self._area_chunk_index(selected_item)
                                vram_bytes = self._load_area_vram_bytes(chunk_index)
                                self.sprt_viewer.export_name = _export_name(selected_item)
                                # A painted sprite rewrites the IMG, and
                                # an export has to carry it - see img_dirty.
                                self.sprt_viewer.img_written = (
                                    self._note_img_written)
                                # A recoloured piece is a change to the
                                # SPRT blob, staged like any file edit.
                                self.sprt_viewer.stage_edit = (
                                    lambda blob, label, item=selected_item:
                                    self._stage_file_edit(item, blob, label))
                                loader = (self.sprt_viewer.load_sprt_data if kind == "SPRT"
                                          else self.bgmp_viewer.load_bgmp_data)
                                loader(self.dat_file, dat_start, offset, entry_size,
                                       chunk_index=chunk_index, vram_bytes=vram_bytes)
                            else:
                                QMessageBox.critical(self, "Error", "DAT file not loaded.")
                        except Exception as e:
                            print(f"Error loading {kind} file: {e}")
                            QMessageBox.critical(self, "Error", f"Failed to load {kind} file: {e}")

                    else:
                        print(f"No specialized viewer for {file_type} files")
                else:
                    print("No additional data found.")
        except Exception as e:
            print(f"Error in on_tree_selection_changed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to handle selection change: {e}")

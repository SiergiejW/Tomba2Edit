"""IDX character registrations and complete NPC resource imports."""
import copy
import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from formats.archive import project_file, repacker
from formats.archive.idx_parser import parse_idx_file
from game import game_build, labels as labels_module, npc_catalogue, npc_resources
from gui.widgets.import_worker import run_import


def remap_project_state(state, before_idx, after_idx):
    """Move saved edit markers with their original SDAT/TRAIL entries."""
    before, after = repacker.parse_idx_bytes(before_idx), repacker.parse_idx_bytes(after_idx)
    addresses = {}
    for old, new in zip(before, after):
        for (_old_id, offset), (_new_id, moved) in zip(old['sdat_pointers'], new['sdat_pointers']):
            addresses[old['dat_start'] + offset] = new['dat_start'] + moved
        for (start, _end), (moved, _new_end) in zip(old['trail_ranges'], new['trail_ranges']):
            addresses[start] = moved
    result = copy.deepcopy(state)
    for key in ('text_files', 'tree_files'):
        result[key] = {str(addresses.get(int(address), int(address))): value
                       for address, value in result.get(key, {}).items()}
    for record in result.get('pending_replacements', ()):
        record['address'] = addresses.get(record['address'], record['address'])
    return result


class NpcMixin:
    def npc_model_choices(self, chunk):
        """Area-local models already safe to use in generated NPC handlers."""
        overlay = self.overlay_for_area(chunk)
        return npc_resources.registered_models(Path(overlay).read_bytes()) if overlay else []

    def _npc_paths(self, chunk):
        if not self.dat_file:
            raise ValueError('Open a game or project first.')
        exe = getattr(self.mainexe_viewer, 'exe_path', None)
        overlay = self.overlay_for_area(chunk)
        if not exe or not overlay:
            raise ValueError('This character catalogue needs MAIN.EXE and the area overlay.')
        if game_build.use(exe).name != 'us-retail':
            raise ValueError('NPC registration currently requires the US retail game.')
        cd = Path(self.dat_file).parent
        return Path(exe), Path(overlay), Path(self.dat_file), cd / 'TOMBA2.IDX', cd / 'TOMBA2.IMG'

    def _npc_project_root(self):
        cd = Path(self.dat_file).parent
        for root in (cd.parent, cd):
            manifest = root / project_file.MANIFEST
            if manifest.is_file():
                try:
                    if json.loads(manifest.read_text(encoding='utf8')).get('format') == project_file.FORMAT:
                        return root
                except (OSError, ValueError):
                    pass
        return Path(self._project_work_dir())

    def _npc_write_manifest(self, root, state):
        path = Path(root) / project_file.MANIFEST
        manifest = json.loads(path.read_text(encoding='utf8'))
        manifest['editor_state'] = state
        if self.labels is not None:
            manifest['labels'] = manifest.get('labels') or 'labels.json'
            labels_module.save(self.labels, str(Path(root) / manifest['labels']))
        temporary = path.with_name(path.name + '.npc-writing')
        try:
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return manifest

    def _npc_reload_project(self, root, manifest, state, active_chunk=None):
        """Rebase every address cache after already-materialized DAT edits."""
        root = Path(root)
        cd = root / (manifest.get('cd_folder') or '')
        self.level_panel._cancel_load()
        self.pending_txtd_edits.clear()
        self.pending_file_edits.clear()
        self.txtd_file_states.clear()
        for viewer in (self.txtd_viewer, self.txt2_viewer, self.mainexe_viewer, self.bins_viewer):
            viewer.clear_cache()
        for name in ('_model_skeleton_cache', '_overlay_bytes', '_chunk_vram_cache',
                     '_area_vram_cache', '_npc_catalogue_cache'):
            setattr(self, name, {})
        for name in ('_type_names', '_approvals', '_approved_by_animation'):
            setattr(self, name, None)
        # A loaded/edited label set must survive the structural reparse.
        if self.labels is not None:
            self.labels_override = self.labels
        self.dat_file = str(cd / 'TOMBA2.DAT')
        self._project_snapshot_path = str(root)
        parse_idx_file(self, str(cd))
        self._load_mainexe(str(root / manifest['main_exe']) if manifest.get('main_exe') else None)
        overlays = [{'name': Path(relative).name, 'size': (root / relative).stat().st_size}
                    for relative in manifest.get('overlays', ()) if (root / relative).is_file()]
        sop = str(root / manifest['sop_bin']) if manifest.get('sop_bin') else None
        if sop and not any(row['name'].upper() == 'SOP.BIN' for row in overlays):
            overlays.append({'name': 'SOP.BIN', 'size': Path(sop).stat().st_size})
        self._load_bins(overlays, sop)
        self._restore_translation_project_state(state)
        self.load_translation_tab(str(cd))
        self._load_level_editor()
        if active_chunk is not None:
            index = self.level_panel.area_box.findData(active_chunk)
            if index >= 0 and index != self.level_panel.area_box.currentIndex():
                self.level_panel.area_box.setCurrentIndex(index)
        self._load_img_browser()
        self.widgets_area.setCurrentWidget(self.widgets['Folder'])

    def _npc_materialize_session(self):
        """Snapshot text, models, overlays and executable edits before insertion.

        This writes the working project, never the source disc or .t2p. Its
        edit history is rebased by file identity, so a resized translation
        cannot leave later resource markers at old DAT addresses.
        """
        if not self.level_panel.apply_changes():
            return None
        root = self._npc_project_root()
        before_idx = Path(self.dat_file).with_name('TOMBA2.IDX').read_bytes()
        state = self._translation_project_state()
        active = getattr(self.level_panel, 'chunk', None)
        if not self.save_translation_project(str(root), announce=False):
            return None
        manifest = json.loads((root / project_file.MANIFEST).read_text(encoding='utf8'))
        after_idx = (root / (manifest.get('cd_folder') or '') / 'TOMBA2.IDX').read_bytes()
        state = remap_project_state(state, before_idx, after_idx)
        manifest = self._npc_write_manifest(root, state)
        self._npc_reload_project(root, manifest, state, active)
        return root

    def npc_catalogue_recipes(self, chunk, parent=None):
        exe, overlay, dat, idx, _img = self._npc_paths(chunk)
        labels = self.labels

        def task(progress, cancelled):
            progress(f'Reading characters, skeletons and animations in AREA_{chunk:02X}…')
            if cancelled():
                raise ValueError('Character search cancelled.')
            rejected = []
            result = npc_catalogue.catalogue(exe, overlay, dat, idx, chunk,
                                             labels=labels, rejected=rejected)
            if cancelled():
                raise ValueError('Character search cancelled.')
            return result, rejected

        return run_import(task, parent or self, reporting=True)

    def npc_catalogue_all(self, chunks, parent=None):
        """[(chunk, recipes)] over several areas in one cancellable run; an
        area that fails is skipped and reported as (chunk, error)."""
        jobs = [(chunk, *self._npc_paths(chunk)[:4]) for chunk in chunks]
        labels = self.labels

        def task(progress, cancelled):
            found, failed = [], []
            for n, (chunk, exe, overlay, dat, idx) in enumerate(jobs):
                if cancelled():
                    raise ValueError('Character search cancelled.')
                progress(f'AREA_{chunk:02X} ({n + 1} of {len(jobs)}): reading characters…')
                try:
                    found.append((chunk, npc_catalogue.catalogue(
                        exe, overlay, dat, idx, chunk, labels=labels)))
                except Exception as error:
                    failed.append((chunk, str(error)))
            return found, failed

        return run_import(task, parent or self, reporting=True)

    def import_npc_recipe(self, recipe, destination, name=None, state_path=None, parent=None):
        """Prepare and install a complete character, retaining all other edits."""
        parent = parent or self
        exe, overlay, dat, idx, img = self._npc_paths(destination)
        state = self._translation_project_state()
        before_idx = idx.read_bytes()
        active = getattr(self.level_panel, 'chunk', None)
        root = self._npc_project_root()
        while True:
            def task(progress, cancelled):
                progress(f'Preparing {recipe.name} for AREA_{destination:02X}…')
                if cancelled():
                    raise ValueError('NPC import cancelled.')
                prepared = npc_resources.prepare_import(
                    exe, overlay, dat, idx, img, destination, recipe,
                    state_path=state_path, name=name or recipe.name)
                if cancelled():
                    raise ValueError('NPC import cancelled.')
                return prepared
            try:
                prepared = run_import(task, parent, reporting=True)
                break
            except npc_resources.DestinationStateRequired as error:
                QMessageBox.information(parent, 'Destination savestate needed', str(error))
                state_path, _filter = QFileDialog.getOpenFileName(
                    parent, f'Savestate from AREA_{destination:02X}', '',
                    'Emulator savestates (*.sav *.state *.sstate *.000 *.001 *.002);;All files (*)')
                if not state_path:
                    return None
        npc_resources.commit(prepared)
        state = remap_project_state(state, before_idx, idx.read_bytes())
        # IMG offsets changed with texture uploads; every later build must
        # carry this working pair rather than the source disc's original IMG.
        state['img_dirty'] = True
        manifest = self._npc_write_manifest(root, state)
        self._npc_reload_project(root, manifest, state, active)
        self._npc_mark_imported(prepared.model, destination, overlay)
        self._npc_write_manifest(root, self._translation_project_state())
        self.statusBar().showMessage(prepared.report + ' Save Project to keep this registration.', 15000)
        return prepared.model

    def _npc_mark_imported(self, model, chunk, overlay):
        if self.labels is None:
            self.labels = labels_module.LabelSet(name='NPC resources', build='custom')
            self.labels_override = self.labels
        for item in self.address_item.values():
            entry = self._entry_of(item)
            if entry is None or entry.get('area') != chunk:
                continue
            source = item.data(Qt.ItemDataRole.UserRole)
            if source[0] not in (model.file_id, model.anim_file):
                continue
            data = self._entry_bytes(item)
            label = model.name + (' Model' if source[0] == model.file_id else ' Animation')
            self.pending_file_edits[entry['key']] = dict(entry, data=data, label=label)
            self._colour_address(entry['address'], 'edited')
            content = self.address_content.get(entry['address'])
            previous_label = self.labels.get(content) if content else None
            if content and (previous_label is None or not previous_label.name):
                kind = item.text().rsplit('.', 1)[-1]
                self.labels.rename(content, label, kind=kind, start=entry['address'],
                                   end=entry['address'] + entry['size'] - 1)
                self.labels_dirty = True
        from formats.archive.idx_parser import apply_labels, apply_labels_flat
        apply_labels(self)
        apply_labels_flat(self)
        self.level_panel._placement_files[Path(overlay).name] = Path(overlay).read_bytes()
        self._refresh_edit_status()

    def open_npc_catalogue(self, chunk=None, preferred_data=None):
        from gui.widgets.npc_catalogue_dialog import NpcCatalogueDialog
        if chunk is None:
            chunk = getattr(self.level_panel, 'chunk', 4) or 4
        try:
            self._npc_paths(chunk)
            if not 4 <= chunk < 26:
                raise ValueError('Choose a gameplay AREA_04–AREA_19 for the destination NPC catalogue.')
            if self._npc_materialize_session() is None:
                return None
            dialog = NpcCatalogueDialog(self, chunk, preferred_data=preferred_data)
            dialog.exec()
            return dialog.imported_model if dialog.imported_destination == chunk else None
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'NPC catalogue', str(error))
            return None

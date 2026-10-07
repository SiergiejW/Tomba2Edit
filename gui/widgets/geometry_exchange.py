"""Blender export/import actions shared by the MDAT and SMST panels."""
from dataclasses import replace
from pathlib import Path

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from formats.models.obj_exchange import MATERIAL, ExchangeError, export_obj, import_faces, records
from gui.widgets.geometry_import_dialog import GeometryImportDialog


def prepare_import(faces, source, blob, kind, part, *, library=(), growth=0, cell_size=None, install=None,
                   state=None):
    """(ImportResult, prepared textures or None) for the dialog's faces.

    Faces on the target's own materials go in as they are. The rest are
    foreign: `source` (an obj_textures.Source) says where their textures
    come from and `install(packets, vram, part, kept, state)` finds room
    for them in the level - without touching what the kept faces still
    read, and in VRAM the savestate `state` shows unused if one is given."""
    def build(mesh, materials):
        return import_faces(mesh, blob, kind, part, max_growth=growth, material_library=materials,
                            cell_size=cell_size, growth_alignment=2048)
    kept = [f for f in faces if MATERIAL.fullmatch(f.material)]
    foreign = [f for f in faces if not MATERIAL.fullmatch(f.material)]
    if not foreign:
        return build(faces, library), None
    from formats.models import obj_textures
    if install is None:
        raise ExchangeError('Open the model from a disc to install new textures.')
    staged, vram, made, order = obj_textures.build(foreign, source)

    def named(found):
        # A record forgets which Blender object it was, and errors name it.
        return [replace(r, face=replace(r.face, object=foreign[order[n]].object)) for n, r in enumerate(found)]
    early = named(records(staged, 'SMST', 0))
    build(kept + [r.face for r in early], list(library) + early)    # geometry errors before the slow part
    textures = install(staged, vram, part, obj_textures.packets(kept), state)
    textures['library'] = named(textures['library'])
    result = build(kept + [r.face for r in textures['library']], list(library) + textures['library'])
    report = textures['report']
    parts = [f"{made['copied']} materials copied as they are" if made['copied'] else '',
             f"{made['reduced']} reduced to 16 colours" if made['reduced'] else '',
             f"{made['resized']} of them resampled to {made['tile']} texels" if made['resized'] else '',
             f"{made['flat']} plain colours" if made['flat'] else '']
    result.note += (' Textures: ' + ', '.join(p for p in parts if p)
                    + f"; {report['rectangles']} regions and {report['palettes']} palettes installed, "
                    f"{len(report['shrunk'])} shrunk to fit the level's VRAM.")
    return result, textures


def probe_import(faces, blob, kind, part, *, library=(), growth=0, cell_size=None):
    """The ImportResult these faces would give, textures aside: foreign
    faces stand in plain colours. Quick, and enough to know where every
    packet lands - which is what the frame-buffer forecast needs."""
    from formats.models import obj_textures
    kept = [f for f in faces if MATERIAL.fullmatch(f.material)]
    foreign = [f for f in faces if not MATERIAL.fullmatch(f.material)]
    staged = []
    if foreign:
        packets, _vram, _made, order = obj_textures.build(foreign, obj_textures.Source('flat', images={}))
        staged = [replace(r, face=replace(r.face, object=foreign[order[n]].object))
                  for n, r in enumerate(records(packets, 'SMST', 0))]
    return import_faces(kept + [r.face for r in staged], blob, kind, part, max_growth=growth,
                        material_library=list(library) + staged, cell_size=cell_size, growth_alignment=2048)


def install_exchange(panel, kind):
    viewer = panel.viewer

    def selection():
        if viewer.blob is None:
            raise ExchangeError('Open a model from the disc first.')
        part = viewer.highlighted_group if kind == 'SMST' else None
        if kind == 'SMST' and part is None:
            raise ExchangeError('Select the one SMST part to edit in the parts list.')
        return part

    def export():
        try:
            part = selection()
            stem = kind if part is None else f'SMST_part_{part:03d}'
            path, _ = QFileDialog.getSaveFileName(panel, 'Export geometry for Blender', stem + '.obj', 'Wavefront OBJ (*.obj)')
            if not path:
                return
            if not Path(path).suffix:
                path += '.obj'
            export_obj(path, viewer.blob, kind, part, viewer.vram_raw_bytes)
            panel.details.setText(
                'Exported OBJ, MTL and textures. Blender: File > Import > Wavefront (.obj), '
                'scale 1, Forward -Z, Up Y. Export with the same axes and scale, '
                'UVs, Materials and Vertex Colors enabled, Triangulated Mesh disabled. '
                'Keep T2 material names. Then use Import from Blender here.')
        except (ValueError, OSError) as exc:
            QMessageBox.critical(panel, 'Geometry export failed', str(exc))

    def import_():
        try:
            part = selection()
            if panel.stage_edit is None:
                raise ExchangeError('Open this resource from a disc row before importing.')
            path, _ = QFileDialog.getOpenFileName(
                panel, 'Replace MDAT geometry' if part is None else f'Replace only SMST part {part}', '',
                'Blender exports (*.obj *.glb *.gltf);;Wavefront OBJ (*.obj);;glTF 2.0 (*.glb *.gltf)')
            if not path:
                return
            # MainWindow bounds growth by the known area's RAM pool, including
            # sector padding and every other staged edit. Other builds/areas
            # and stand-alone panels cannot enlarge the resource.
            growth = panel.geometry_growth_budget() if getattr(panel, 'geometry_growth_budget', None) else 0
            cell_size = panel.geometry_cell_size() if getattr(panel, 'geometry_cell_size', None) else None
            library = ()
            if viewer.source:
                source, address, size = viewer.source
                with open(source, 'rb') as stream:
                    stream.seek(address)
                    library = records(stream.read(size), kind, part)
            def prepare(faces, mode, source, state=None):
                result, textures = prepare_import(
                    faces, source, viewer.blob, kind, part, library=library, growth=growth,
                    cell_size=cell_size, state=state,
                    install=getattr(panel, 'prepare_geometry_textures', None))
                if forecast and not result.unchanged:
                    result.frames = forecast(result)
                if result.frames:
                    # The game's own rule over recorded frames replaces the density guess.
                    result.note = result.note.replace(' ' + result.warning, '') if result.warning else result.note
                    result.note += ' ' + result.frames.text()
                return result, textures
            forecast = getattr(panel, 'geometry_frame_forecast', None) if kind == 'MDAT' else None

            def probe(faces):
                """Frame-buffer forecast for these faces, before anything slow is done."""
                found = probe_import(faces, viewer.blob, kind, part, library=library, growth=growth,
                                     cell_size=cell_size)
                return None if found.unchanged else forecast(found)
            dialog = GeometryImportDialog(path, [r.face for r in records(viewer.blob, kind, part)],
                                          kind, part, prepare, panel, cell_size=cell_size,
                                          probe=probe if forecast else None)
            if not dialog.exec():
                return
            result, textures = dialog.prepared
            if textures:
                panel.commit_geometry_textures(textures)
                from psx.vram_viewer import vram_index_image
                if kind == 'SMST':
                    viewer.set_vram(textures['vram'], vram_index_image(textures['vram']))
                else:
                    viewer.set_vram_image(vram_index_image(textures['vram']), textures['vram'])
            if kind == 'SMST':
                panel._apply_edit(result.data, result.note, f'Imported part {part}', f'Blender SMST part {part}')
                panel.table.selectRow(part)
                panel.details.setText(f'Imported part {part}. {result.note} Staged; export the disc/files to save.')
            else:
                panel.apply_geometry(result)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(panel, 'Geometry import failed — original retained', str(exc))

    panel.obj_export_action = QAction('Export Blender OBJ', panel)
    panel.obj_import_action = QAction('Import from Blender', panel)
    panel.obj_export_action.setToolTip('Export the whole MDAT' if kind == 'MDAT' else 'Export only the selected SMST part in its local coordinates')
    panel.obj_import_action.setToolTip(('Replace all MDAT geometry; collision stays original' if kind == 'MDAT' else 'Replace only the selected part; all other part bodies stay byte-identical')
                                       + '. Takes an OBJ, or a glTF (.glb), which also carries a foreign model’s textures and vertex colours.')
    panel.obj_export_action.triggered.connect(export)
    panel.obj_import_action.triggered.connect(import_)
    viewer.toolbar.addAction(panel.obj_export_action)
    viewer.toolbar.addAction(panel.obj_import_action)

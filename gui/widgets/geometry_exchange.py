"""Native Blender OBJ actions shared by the MDAT and SMST panels."""
from pathlib import Path

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from formats.models.obj_exchange import ExchangeError, export_obj, import_obj, records


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
                'Keep T2 material names. Then use Import Blender OBJ here.')
        except (ValueError, OSError) as exc:
            QMessageBox.critical(panel, 'Geometry export failed', str(exc))

    def import_():
        try:
            part = selection()
            if panel.stage_edit is None:
                raise ExchangeError('Open this resource from a disc row before importing.')
            path, _ = QFileDialog.getOpenFileName(panel, 'Replace MDAT geometry' if part is None else f'Replace only SMST part {part}', '', 'Wavefront OBJ (*.obj)')
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
            result = import_obj(path, viewer.blob, kind, part, max_growth=growth,
                                material_library=library, cell_size=cell_size)
            if kind == 'SMST':
                panel._apply_edit(result.data, result.note, f'Imported part {part}', f'Blender SMST part {part}')
                panel.table.selectRow(part)
                panel.details.setText(f'Imported part {part}. {result.note} Staged; export the disc/files to save.')
            else:
                panel.apply_geometry(result)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(panel, 'Geometry import failed — original retained', str(exc))

    panel.obj_export_action = QAction('Export Blender OBJ', panel)
    panel.obj_import_action = QAction('Import Blender OBJ', panel)
    panel.obj_export_action.setToolTip('Export the whole MDAT' if kind == 'MDAT' else 'Export only the selected SMST part in its local coordinates')
    panel.obj_import_action.setToolTip('Replace all MDAT geometry; collision stays original' if kind == 'MDAT' else 'Replace only the selected part; all other part bodies stay byte-identical')
    panel.obj_export_action.triggered.connect(export)
    panel.obj_import_action.triggered.connect(import_)
    viewer.toolbar.addAction(panel.obj_export_action)
    viewer.toolbar.addAction(panel.obj_import_action)

"""Choose objects, placement and where the textures come from, before importing.

A model made for this target keeps the target's textures and there is
nothing to choose. A foreign one needs its own, and they are not always
in the file: Blender writes no packed image into an OBJ. So the dialog
says what it found for every material and lets the user pick otherwise
(formats/models/obj_textures.py), rather than importing a grey model.
"""
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from formats.models.obj_exchange import MATERIAL, ExchangeError, read_obj, shades, _key
from formats.models.obj_placement import bounds, fit, place, subdivide
from formats.models.obj_textures import Source, discover, merged, open_source, own_images, untextured

GLTF = ('.glb', '.gltf')
# (key, label, file filter for the ones that need a file)
TEXTURE_MODES = (
    ('auto', 'Automatic (recommended)', None),
    ('images', 'Images from a file I choose…', 'Images inside a model, or named by an MTL (*.glb *.gltf *.mtl)'),
    ('vram', 'Exact copy from the source game’s VRAM dump…', 'Raw VRAM dump (*.vram *.bin *.raw);;All files (*)'),
    ('flat', 'No textures: plain material colours', None),
)


def load(path):
    """(faces with the file's own positions and 0..1 colours, is a glTF)."""
    if Path(path).suffix.lower() in GLTF:
        from formats.models.gltf_import import read_gltf
        return read_gltf(path, colors=tuple), True
    return read_obj(path, scale=1, raw_positions=True, colors=tuple), False


class PlacementPreview(QWidget):
    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target, self.faces, self.selected, self.view = target, [], '', 0
        self.setMinimumSize(440,320)

    def paintEvent(self, event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#18212b'))
        def project(p):
            x,y,z=p
            return (x,-z) if self.view==1 else ((x,-y) if self.view==2 else (x-.65*z,-y+.35*(x+z)))
        points=[project(p) for f in self.faces+self.target for p in f.vertices]
        if not points:return
        lo=[min(p[a] for p in points) for a in (0,1)]
        hi=[max(p[a] for p in points) for a in (0,1)]
        zoom=min((self.width()-32)/max(hi[0]-lo[0],1),(self.height()-55)/max(hi[1]-lo[1],1))
        def point(v):
            x,y=project(v)
            return QPointF((x-(lo[0]+hi[0])/2)*zoom+self.width()/2,
                           (y-(lo[1]+hi[1])/2)*zoom+self.height()/2+10)
        for faces,color,width in [(self.target,'#455361',.5),
                                  ([f for f in self.faces if f.object!=self.selected],'#43bcd0',.8),
                                  ([f for f in self.faces if f.object==self.selected],'#ffbe55',1.7)]:
            path=QPainterPath()
            for face in faces:
                path.moveTo(point(face.vertices[0]))
                for vertex in face.vertices[1:]:path.lineTo(point(vertex))
                path.closeSubpath()
            painter.setPen(QPen(QColor(color),width));painter.drawPath(path)
        painter.setPen(QColor('#e6edf3'))
        painter.drawText(12,22,'Gray: original target   Cyan: import   Orange: selected Blender object')


class GeometryImportDialog(QDialog):
    def __init__(self, path, target, kind, part, prepare, parent=None, cell_size=640, probe=None):
        super().__init__(parent)
        self.setWindowTitle('Import model — choose objects, placement and textures')
        self.resize(1100,820)
        self.path=Path(path);self.target=target;self.prepare=prepare;self.prepared=None
        # probe(faces) -> game.frame_budget.Forecast: what these faces would
        # cost the game's frame buffer, known before anything slow is done.
        self.probe=probe;self.frames=None
        self.kind=kind;self.cell_size=cell_size or 640
        self.faces,self.linear=load(path)
        self.foreign=any(not MATERIAL.fullmatch(f.material) for f in self.faces)
        bounds(self.faces)
        self.objects=QListWidget()
        counts={}
        for f in self.faces:counts[f.object]=counts.get(f.object,0)+1
        for name,count in counts.items():
            row=QListWidgetItem(f'{name or "(unnamed)"} — {count:,} faces')
            row.setData(Qt.ItemDataRole.UserRole,name)
            row.setFlags(row.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked);self.objects.addItem(row)
        self.preview=PlacementPreview(target)
        self.summary=QLabel();self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.scale=self.number(.000001,1000000,100,6)
        self.yaw=self.number(-360,360,0,2)
        self.offset=[self.number(-65536,65536,0,3) for _ in range(3)]
        form=QFormLayout();form.addRow('Scale (game units per file unit)',self.scale);form.addRow('Turn around vertical axis',self.yaw)
        for a,spin in zip('XYZ',self.offset):form.addRow(f'Move {a}',spin)
        fit_button=QPushButton('Fit selected objects to target');fit_button.clicked.connect(self.fit_target)
        reset=QPushButton('Keep exported placement');reset.clicked.connect(self.reset_placement)
        only=QPushButton('Only highlighted object');only.clicked.connect(self.only_highlighted)
        all_button=QPushButton('All objects');all_button.clicked.connect(lambda:self.set_checked(None))
        select=QHBoxLayout();select.addWidget(only);select.addWidget(all_button)
        left=QWidget();left_layout=QVBoxLayout(left)
        left_layout.addWidget(QLabel('Blender object names — click to highlight, tick to import'))
        left_layout.addWidget(self.objects,1);left_layout.addLayout(select);left_layout.addLayout(form)
        left_layout.addWidget(fit_button);left_layout.addWidget(reset)
        left_layout.addWidget(self.material_box())
        views=QComboBox();views.addItems(['3D overview','Top (X/Z)','Front (X/Y)']);views.currentIndexChanged.connect(self.change_view)
        right=QWidget();right_layout=QVBoxLayout(right);right_layout.addWidget(views);right_layout.addWidget(self.preview,1);right_layout.addWidget(self.summary)
        self.frames_hint=QLabel();self.frames_hint.setWordWrap(True)
        self.frames_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if probe:
            check=QPushButton('Check this selection against the game’s frame buffer');check.clicked.connect(self.check_frames)
            self.frames_hint.setText('A level heavier than the game’s frame buffer freezes it. What counts is how much one '
                                     'camera catches, not how many faces there are — check before importing.')
            right_layout.addWidget(check);right_layout.addWidget(self.frames_hint)
        splitter=QSplitter();splitter.addWidget(left);splitter.addWidget(right);splitter.setSizes([460,640])
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Import selected objects')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Replaces the entire MDAT. Collision stays original.' if kind=='MDAT' else f'Replaces SMST part {part} only. Other parts stay unchanged.'))
        layout.addWidget(splitter,1);layout.addWidget(self.buttons)
        self.objects.itemChanged.connect(self.refresh);self.objects.currentRowChanged.connect(self.refresh)
        for spin in [self.scale,self.yaw,*self.offset]:spin.valueChanged.connect(self.refresh)
        if self.foreign:
            lo,hi=bounds(self.faces);tl,th=bounds(target)
            self.yaw.setValue(90 if (hi[0]-lo[0]>hi[2]-lo[2]) != (th[0]-tl[0]>th[2]-tl[2]) else 0)
            self.fit_target()
        self.objects.setCurrentRow(0);self.refresh()

    @staticmethod
    def number(low,high,value,decimals):
        spin=QDoubleSpinBox();spin.setDecimals(decimals);spin.setRange(low,high);spin.setValue(value);return spin

    # --- textures and colours ------------------------------------------

    def material_box(self):
        """The two choices a foreign model needs made, and what was found."""
        box=QGroupBox('Textures and vertex colours');layout=QVBoxLayout(box)
        self.own=Source('images',self.path.name,images={},origin='model')
        self.auto=self.own;self.picked={};self.texture_mode='auto'
        self.textures=QComboBox();self.choose=QPushButton('Choose file…')
        if self.foreign:
            names={f.material for f in self.faces if not MATERIAL.fullmatch(f.material)}
            self.own=Source('images',self.path.name,images=own_images(self.path),origin='model')
            self.auto=discover(self.path,names)
            for key,label,_filter in TEXTURE_MODES:self.textures.addItem(label,key)
        else:
            self.textures.addItem('Keep the target’s textures','keep');self.textures.setEnabled(False)
        self.textures.activated.connect(self.texture_mode_picked)
        self.choose.clicked.connect(lambda:self.choose_file(self.texture_mode))
        self.choose.setVisible(False)
        row=QHBoxLayout();row.addWidget(QLabel('Textures'));row.addWidget(self.textures,1);row.addWidget(self.choose)
        self.texture_hint=QLabel();self.texture_hint.setWordWrap(True)
        self.texture_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Where they go. A savestate is the only evidence that VRAM no file
        # writes is also left alone by the game (game/level_textures.py).
        self.state=None
        self.room=QComboBox()
        self.room.addItem('Only what the replaced model frees (recommended)','freed')
        self.room.addItem('Also VRAM a savestate shows the game leaves empty…','state')
        self.room.setEnabled(self.foreign);self.room.activated.connect(self.room_picked)
        room_row=QHBoxLayout();room_row.addWidget(QLabel('Texture room'));room_row.addWidget(self.room,1)
        self.room_hint=QLabel();self.room_hint.setWordWrap(True)
        self.shading=QComboBox()
        self.shading.addItem('Automatic (recommended)','auto');self.shading.addItem('Ignore: light every face evenly','none')
        self.shading.currentIndexChanged.connect(self.refresh)
        shade_row=QHBoxLayout();shade_row.addWidget(QLabel('Vertex colours'));shade_row.addWidget(self.shading,1)
        self.shading_hint=QLabel();self.shading_hint.setWordWrap(True)
        layout.addLayout(row);layout.addWidget(self.texture_hint)
        layout.addLayout(room_row);layout.addWidget(self.room_hint)
        layout.addLayout(shade_row);layout.addWidget(self.shading_hint)
        return box

    def room_picked(self,index):
        if self.room.itemData(index)=='state':
            path,_=QFileDialog.getOpenFileName(self,'A savestate taken in this level',str(self.path.parent),'All files (*)')
            self.state=path or self.state
        else:self.state=None
        self.room.setCurrentIndex(1 if self.state else 0)
        self.refresh()

    def room_report(self):
        if not self.foreign:return ''
        if self.state:
            return (f'New textures may also use VRAM that no file of this level loads into and that {Path(self.state).name} shows empty. '
                    'Take the savestate in this level; one moment cannot show everything the game ever draws there.')
        return ('New textures go where the replaced model’s were. If they do not all fit at full size you are asked '
                'before anything is shrunk.')

    def texture_mode_picked(self,index):
        mode=self.textures.itemData(index)
        if mode in ('images','vram') and mode not in self.picked and not self.choose_file(mode):
            mode=self.texture_mode       # no file chosen: stay where we were
        self.texture_mode=mode
        self.textures.setCurrentIndex(self.textures.findData(mode))
        self.refresh()

    def choose_file(self,mode):
        """Ask for the file behind `mode`. False if the user backed out."""
        pattern=next(f for key,_label,f in TEXTURE_MODES if key==mode)
        path,_=QFileDialog.getOpenFileName(self,'Take textures from',str(self.path.parent),pattern)
        if not path:return False
        try:
            source=open_source(path,mode)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'That file cannot be used',str(exc));return False
        self.picked[mode]=merged(self.own,source) if mode=='images' else source
        self.refresh();return True

    def texture_source(self):
        """The Source the current choice stands for; None if it still needs a file."""
        if self.texture_mode=='auto':return self.auto
        if self.texture_mode=='flat':return Source('flat',images=self.own.images)
        return self.picked.get(self.texture_mode)

    def foreign_names(self,faces):
        return sorted({f.material for f in faces if not MATERIAL.fullmatch(f.material)})

    def texture_report(self,faces):
        names=self.foreign_names(faces)
        kept=len({f.material for f in faces})-len(names)
        if not names:
            return 'These are the target’s own materials: its installed textures are kept, and unchanged geometry stays byte-identical.'
        wanted=[n for n in names if not untextured(n)]
        source=self.texture_source()
        lead=f'{kept} of the target’s own materials keep its textures. ' if kept else ''
        if source is None:return lead+'Choose the file to take the textures from.'
        if source.kind=='flat':
            return lead+f'{len(names)} materials become plain colours. Nothing of their textures is imported.'
        found=source.textured(wanted)
        where={'model':f'inside {source.label}' if self.linear else f'named by the MTL of {source.label}',
               'beside':f'in {source.label}, found beside this file'}.get(source.origin,f'in {source.label}')
        if wanted and len(found)==len(wanted):
            text=f'Textures for all {len(wanted)} textured materials are {where}.'
            if source.kind=='vram':text=f'All {len(wanted)} textured materials name a page and palette, read exactly from {source.label}.'
            if source.origin=='beside':text+=' Not the right file? Choose another above.'
        elif found:
            missing=[n for n in wanted if n not in found]
            text=(f'Textures for {len(found)} of {len(wanted)} materials are {where}. Without one, and plain-coloured: '
                  +', '.join(missing[:4])+(f' and {len(missing)-4} more.' if len(missing)>4 else '.'))
        elif not wanted:
            text=f'None of these {len(names)} materials has a texture; they import as plain colours.'
        elif self.texture_mode=='auto':
            text=('No textures found. '
                  +('This file has no images in it' if self.linear else 'This OBJ’s MTL names no image files — Blender leaves packed images out of an OBJ —')
                  +' and no .glb or .vram beside it has these material names. Export glTF (.glb) from Blender, which carries the images inside it, or choose a source above.')
        else:
            text=f'{source.label} has no texture for any of these {len(wanted)} materials. Choose another file.'
        return lead+text

    def shading_report(self,faces):
        corners=[c for f in faces for c in f.colors]
        coloured=sum(c is not None for c in corners)
        if self.shading.currentData()=='none':return 'Every face is lit evenly; the file’s colours are not read.'
        if not coloured:
            return ('This file has no vertex colours, so every face is lit evenly. '
                    +('Give the mesh a colour attribute before exporting.' if self.linear else
                      'Blender writes them to an OBJ only with Colors ticked under Geometry in the export window; a glTF (.glb) export keeps them without asking.'))
        some='' if coloured==len(corners) else f' {len(corners)-coloured:,} corners have none and are lit evenly.'
        if self.foreign:return 'Vertex colours found. White is read as unshaded, as other PS1 tools export it.'+some
        return 'Vertex colours are read back as they were exported.'+some

    def shaded(self,faces):
        """Faces with the game's 0-15 colours in place of the file's."""
        if self.shading.currentData()=='none':
            return [replace(f,colors=(None,)*len(f.colors)) for f in faces]
        scales={own:shades(own,self.linear) for own in (True,False)}
        return [replace(f,colors=tuple(c if c is None else scales[bool(MATERIAL.fullmatch(f.material))](c) for c in f.colors))
                for f in faces]

    # --- objects and placement -----------------------------------------

    def chosen(self):
        names={self.objects.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.objects.count()) if self.objects.item(i).checkState()==Qt.CheckState.Checked}
        return [f for f in self.faces if f.object in names]

    def placed(self):
        return place(self.shaded(self.chosen()),self.scale.value(),self.yaw.value(),tuple(s.value() for s in self.offset))

    def refresh(self,*_):
        faces=self.chosen()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(faces))
        row=self.objects.currentItem();self.preview.selected=row.data(Qt.ItemDataRole.UserRole) if row else ''
        self.preview.faces=place(faces,self.scale.value(),self.yaw.value(),tuple(s.value() for s in self.offset),quantize=False)
        self.preview.update()
        self.choose.setVisible(self.texture_mode in ('images','vram'))
        self.texture_hint.setText(self.texture_report(faces));self.shading_hint.setText(self.shading_report(faces))
        self.room_hint.setText(self.room_report());self.room_hint.setVisible(self.foreign)
        if faces:
            lo,hi=bounds(self.preview.faces)
            quads=sum(len(f.vertices)==4 for f in faces)
            self.summary.setText(f'{len({f.object for f in faces})} objects, {len(faces)-quads:,} triangles and {quads:,} quads. Highlighted: {self.preview.selected or "(unnamed)"}.\n'
                + 'Placed size X/Y/Z: '+', '.join(f'{hi[a]-lo[a]:,.1f}' for a in range(3))+' game units.\nFind the same object by name in Blender’s Outliner.')
        else:self.summary.setText('Tick at least one object to import.')

    def change_view(self,index):self.preview.view=index;self.preview.update()

    def fit_target(self):
        try:scale,offset=fit(self.chosen(),self.target,self.yaw.value())
        except ExchangeError:return
        for spin,value in zip([self.scale,*self.offset],[scale,*offset]):
            spin.blockSignals(True);spin.setValue(value);spin.blockSignals(False)
        self.refresh()

    def reset_placement(self):
        for spin,value in zip([self.scale,self.yaw,*self.offset],[100,0,0,0,0]):
            spin.blockSignals(True);spin.setValue(value);spin.blockSignals(False)
        self.refresh()

    def set_checked(self,name):
        self.objects.blockSignals(True)
        for i in range(self.objects.count()):
            row=self.objects.item(i);row.setCheckState(Qt.CheckState.Checked if name is None or row.data(Qt.ItemDataRole.UserRole)==name else Qt.CheckState.Unchecked)
        self.objects.blockSignals(False);self.refresh()

    def only_highlighted(self):
        row=self.objects.currentItem()
        if row:self.set_checked(row.data(Qt.ItemDataRole.UserRole))

    def confirm_untextured(self,faces,source):
        """Importing without a single texture is asked about, not assumed."""
        wanted=[n for n in self.foreign_names(faces) if not untextured(n)]
        if not wanted or source.kind=='flat' or source.textured(wanted):return True
        answer=QMessageBox.question(self,'No textures for this model',
            self.texture_report(faces)+'\n\nImport it in plain colours anyway?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        return answer==QMessageBox.StandardButton.Yes

    def final_faces(self):
        """The chosen faces as they go to the game: placed, and for an MDAT cut to its cells."""
        faces=self.placed()
        if self.kind=='MDAT':
            original={_key(f,False) for f in self.target}
            faces=[part for face in faces for part in
                   ([face] if _key(face,False) in original else subdivide([face],2*self.cell_size))]
        return faces

    def check_frames(self,*_):
        """Forecast the frame buffer for the current selection; False if it could not be made."""
        self.frames=None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.frames=self.probe(self.final_faces())
        except (ValueError,OSError) as exc:
            self.frames_hint.setText(f'Not checked: {exc}');return False
        finally:
            QApplication.restoreOverrideCursor()
        self.frames_hint.setText(self.frames.text() if self.frames else 'Unchanged geometry: nothing to check.')
        return True

    def accept(self):
        try:
            mode=1 if self.foreign else 0
            source=self.texture_source() if self.foreign else None
            if self.foreign and source is None:
                QMessageBox.warning(self,'Textures','Choose the file to take the textures from, or another way to texture the model.');return
            faces=self.final_faces()
            if self.foreign and not self.confirm_untextured(faces,source):return
            # Before the slow part, while the selection can still be changed.
            if self.probe and self.check_frames() and not self.confirm_frames(self.frames):return
            self.buttons.setEnabled(False)
            self.summary.setText('Checking geometry and packing textures. Large models can take a minute…')
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            try:
                self.prepared=self.prepare(faces,mode,source,self.state)
            finally:
                QApplication.restoreOverrideCursor()
                self.buttons.setEnabled(True)
        except (ValueError,OSError) as exc:
            self.refresh()
            QMessageBox.warning(self,'Import needs adjustment',str(exc));return
        if not self.confirm_shrunk():
            self.prepared=None;self.refresh();return
        super().accept()

    def confirm_frames(self,frames):
        """A level heavier than the frame buffer freezes the game: say so now, while it can still be changed."""
        if frames is None or frames.safe:return True
        answer=QMessageBox.question(self,'Too heavy for the game’s frame buffer',
            frames.text()+'\n\nWhat counts is not how many faces the model has but how many one camera catches, on top of '
            'everything else the game draws in that frame. To get under the limit, go back and untick objects, '
            'or scale and move the model so less of it is in view at once.\n\n'
            'Or import it as it is: Build Disc will then offer the frame-buffer patch for MAIN.EXE.\n\nImport as it is?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        return answer==QMessageBox.StandardButton.Yes

    def confirm_shrunk(self):
        """Nothing is written yet: textures that had to shrink are the user's call."""
        textures=self.prepared[1] if isinstance(self.prepared,tuple) and len(self.prepared)==2 else None
        shrunk=textures['report']['shrunk'] if isinstance(textures,dict) else []
        if not shrunk:return True
        worst=min(shrunk,key=lambda s:int(s['scale'].split('/')[0]))
        answer=QMessageBox.question(self,'Not all textures fit at full size',
            f"{len(shrunk)} of {textures['report']['rectangles']} texture regions do not fit this level’s VRAM at full size "
            f"and would be shrunk — the worst from {worst['texels']} to {worst['became']} texels.\n\n"
            'To keep them whole, go back and '
            +('' if self.state else 'either give the import more room (Texture room: a savestate taken in this level) or ')
            +'untick objects you can do without.\n\nImport with the shrunk textures?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        return answer==QMessageBox.StandardButton.Yes

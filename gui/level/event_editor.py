"""Native event pages and an editable, relocatable action stack."""
from copy import deepcopy
from pathlib import Path
from PyQt6.QtCore import Qt,QObject,pyqtSignal,QBuffer,QByteArray,QIODevice,QTimer
from threading import Thread
from PyQt6.QtMultimedia import QAudioOutput,QMediaPlayer
from PyQt6.QtCore import QPointF,QRectF
from PyQt6.QtGui import QColor,QFont,QPainter,QPen,QPolygonF
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,
    QListWidget,QListWidgetItem,QFormLayout,QSpinBox,QPushButton,QMessageBox,QCheckBox,QWidget,QPlainTextEdit,QSplitter,QTabWidget)
from game import event_script as script, game_build

class VoiceDecode(QObject):
    decoded=pyqtSignal(object)
    def __init__(self,image,overlay,masters,master,entry,parent):
        super().__init__(parent);self.args=(image,overlay,masters,master,entry)
        self.worker=None
    def start(self):
        self.worker=Thread(target=self.run,daemon=True);self.worker.start()
    def isRunning(self):return bool(self.worker and self.worker.is_alive())
    def emit_result(self,result):
        try:self.decoded.emit(result)
        except RuntimeError:pass  # The editor may have closed while decoding.
    def run(self):
        try:
            from formats.text.voice_link import VoiceLink
            link=VoiceLink();image,overlay,masters,master,entry=self.args
            error=link.set_image(image)
            if error:raise ValueError(error)
            link.set_overlay(overlay);link.set_masters(masters)
            self.emit_result(link.clip_for(entry,master))
        except Exception as exc:self.emit_result((None,0,str(exc)))

class AnimationDecode(VoiceDecode):
    def __init__(self,scene,instance,command,parent,animations=None):
        QObject.__init__(self,parent);self.args=(scene,instance,deepcopy(command),120,animations);self.worker=None
    def run(self):
        try:
            from game.event_preview import animation_frames
            self.emit_result(animation_frames(*self.args))
        except Exception as exc:self.emit_result(([],str(exc)))

class ResidentVoiceDecode(VoiceDecode):
    def __init__(self,exe,image,voice_id,parent):
        QObject.__init__(self,parent);self.args=(exe,image,voice_id);self.worker=None
    def run(self):
        try:
            from game.event_preview import resident_voice_samples
            self.emit_result(resident_voice_samples(*self.args))
        except Exception as exc:self.emit_result((None,0,str(exc)))


# Row colours by what an action does.
CATEGORY = {**{op:'#5aa9ff' for op in (0,1,2,3,13,14,40,46,47)}, **{op:'#e0b24f' for op in (42,43,44)},
    **{op:'#7fd18b' for op in (28,29,30,55,57)}, **{op:'#f08c4a' for op in (17,31,35,36,37,49,52,54,56,58,59)},
    **{op:'#c08cff' for op in (8,21,22,23,24,25)}, **{op:'#a0a0a0' for op in (4,5,6,7,9,10)}, 62:'#ff6b6b'}
PATH_TAB_OPS = frozenset((5,17,21,22,23,24,31,35,36,37,49))
MODEL_TAB_OPS = frozenset((28,29,30,55,57))
TEXT_TAB_OPS = frozenset((0,1,40,46,47))

class PathPreview(QWidget):
    """Top-down sketch of where the actions move the entity and camera."""
    COLORS = {'move':'#f08c4a','set':'#f0d04a','camera':'#c08cff','camera-tomba':'#c08cff','wait':'#a0a0a0'}
    def __init__(self,parent=None):
        super().__init__(parent);self.steps=[];self.selected=None;self.start=None;self.others=[];self.note=''
        self.setMinimumHeight(240)
    def set(self,steps,selected,start,others,note):
        self.steps,self.selected,self.start,self.others,self.note=steps,selected,start,others,note;self.update()
    def paintEvent(self,event):
        import math
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#1d1f23'))
        if self.start is None:
            p.setPen(QColor('#bbb'));p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'No position for this entity.');return
        points=[self.start]+[s[2] for s in self.steps]+[s[3] for s in self.steps]
        xs=[q[0] for q in points];ds=[q[2] for q in points]
        near=[o for o in self.others if min(xs)-1500<=o[0]<=max(xs)+1500 and min(ds)-1500<=o[2]<=max(ds)+1500]
        xs+=[o[0] for o in near];ds+=[o[2] for o in near]
        span=max(max(xs)-min(xs),max(ds)-min(ds),400)*1.15
        cx,cd=(max(xs)+min(xs))/2,(max(ds)+min(ds))/2
        w,h=self.width(),self.height()-34;scale=min(w,h)/span
        def at(q):return QPointF(w/2+(q[0]-cx)*scale,34+h/2+(q[2]-cd)*scale)
        p.setPen(QPen(QColor('#2c3036'),1))
        grid=10**max(1,len(str(int(span/8)))-1)
        g=int((cx-span/2)//grid)*grid
        while g<cx+span/2:
            x=at((g,0,cd)).x();p.drawLine(QPointF(x,34),QPointF(x,self.height()));g+=grid
        g=int((cd-span/2)//grid)*grid
        while g<cd+span/2:
            y=at((cx,0,g)).y();p.drawLine(QPointF(0,y),QPointF(w,y));g+=grid
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#555b66'))
        for o in near:p.drawEllipse(at(o),3,3)
        total=0
        for row,kind,a,b,ticks in self.steps:
            chosen=row==self.selected
            color=QColor(self.COLORS.get(kind,'#ddd'))
            if not chosen and self.selected is not None:color.setAlpha(150)
            p.setPen(QPen(color,4 if chosen else 2,Qt.PenStyle.DashLine if kind.startswith('camera') else Qt.PenStyle.SolidLine))
            if kind in ('move','set'):
                p.drawLine(at(a),at(b))
                d=at(b)-at(a);length=math.hypot(d.x(),d.y())
                if length>8:
                    u=QPointF(d.x()/length,d.y()/length);n=QPointF(-u.y(),u.x());tip=at(b)
                    p.setBrush(color);p.drawPolygon(QPolygonF([tip,tip-u*10+n*5,tip-u*10-n*5]))
            elif kind=='camera':
                p.drawLine(at(a),at(b));p.setBrush(Qt.BrushStyle.NoBrush);q=at(b);p.drawRect(QRectF(q.x()-5,q.y()-5,10,10))
            if ticks:total+=ticks
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#6fcf78'));p.drawEllipse(at(self.start),6,6)
        p.setPen(QColor('#ddd'))
        p.drawText(QRectF(6,4,w-12,28),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                   f'{self.note}   ● start  ▲ move  ▢ camera focus   known time so far ≈ {total/30:.2f} s ({total} ticks)')


class DialoguePicker(QDialog):
    """Choose a dialogue master and entry from the area's TXTD."""
    def __init__(self,parent,bank,master=None,entry=None):
        super().__init__(parent);self.setWindowTitle('Choose dialogue');self.resize(720,520)
        self.bank=bank;layout=QVBoxLayout(self)
        self.masters=QComboBox()
        for n,m in enumerate(bank['entries']):
            first=next((' '.join(e['text'].split('{$')[0].split()) for e in m['entries'] if e['text'].strip()),'')
            self.masters.addItem(f'Dialogue {n} ({len(m["entries"])} entries) — {first[:50]}',n)
        layout.addWidget(self.masters)
        self.entries=QListWidget();layout.addWidget(self.entries,1)
        self.text=QPlainTextEdit();self.text.setReadOnly(True);self.text.setMaximumHeight(110);layout.addWidget(self.text)
        row=QHBoxLayout();row.addStretch()
        ok=QPushButton('Use this line');ok.clicked.connect(self.accept);cancel=QPushButton('Cancel');cancel.clicked.connect(self.reject)
        row.addWidget(ok);row.addWidget(cancel);layout.addLayout(row)
        self.masters.currentIndexChanged.connect(self.fill)
        self.entries.currentRowChanged.connect(self.show_line)
        self.entries.itemDoubleClicked.connect(lambda *_:self.accept())
        if master is not None and 0<=master<self.masters.count():self.masters.setCurrentIndex(master)
        self.fill(self.masters.currentIndex())
        if entry is not None and 0<=entry<self.entries.count():self.entries.setCurrentRow(entry)
    def fill(self,index):
        self.entries.clear()
        for n,e in enumerate(self.bank['entries'][index]['entries'] if index>=0 else ()):
            self.entries.addItem(f'{n:3d}  '+e['text'].split('{$')[0].replace('\n',' ').strip()[:90])
        self.entries.setCurrentRow(0)
    def show_line(self,row):
        m=self.masters.currentIndex()
        entries=self.bank['entries'][m]['entries'] if m>=0 else []
        self.text.setPlainText(entries[row]['text'] if 0<=row<len(entries) else '')
    def chosen(self):
        if self.masters.currentIndex()<0 or self.entries.currentRow()<0:return None
        return self.masters.currentData(),self.entries.currentRow()


class ViewportPreview(QObject):
    """Plays a move or camera action once in the level viewport, then puts
    everything back. Display only: no record is written, nothing is marked
    as changed, and stop() restores at once (the panel calls it before it
    applies anything)."""
    FPS = 30
    def __init__(self,editor):
        super().__init__(editor)
        self.editor=editor;self.timer=QTimer(self);self.timer.setInterval(16)
        self.timer.timeout.connect(self.step);self.saved=None;self.camera=None;self.clock=None;self.track=None
    def viewer(self):
        return getattr(self.editor.panel,'viewer',None)
    def play(self,nodes,row):
        """Start the selected row's preview; False if it has none here."""
        from game.event_preview import trace
        self.stop()
        viewer=self.viewer();instance=self.editor.instance
        start=self.editor.start_position()
        if viewer is None or start is None:return False
        if not any(i is instance for i in getattr(viewer,'instances',())):return False
        steps=[s for s in trace(nodes,row,start) if s[0]==row]
        if not steps:return False
        _,kind,a,b,ticks=steps[-1]
        import numpy as np
        if kind in ('move','set'):
            seconds=0.6 if kind=='set' else min(6.0,max(0.4,(ticks or np.linalg.norm(np.array(b)-np.array(a))/10)/self.FPS))
            self.saved=(instance,(instance.x,instance.y,instance.z))
            self.track=('move',np.array(a,dtype=float),np.array(b,dtype=float),seconds,0.8)
            self.editor.status.setText(f'Previewing in the viewport: {kind} over {seconds:.2f} s, then back. Nothing is written.')
        elif kind=='camera':
            c=viewer.camera_controls
            self.camera=(c.camera_x,c.camera_y,c.camera_z,c.camera_angle_h,c.camera_angle_v,c.orbit_distance)
            self.track=('camera',np.array(b,dtype=float),0.6,1.2)
            self.editor.status.setText('Previewing the camera focus in the viewport, then back.')
        else:
            return False
        from PyQt6.QtCore import QElapsedTimer
        self.clock=QElapsedTimer();self.clock.start();self.started=False;self.timer.start()
        return True
    def _place(self,point):
        instance=self.saved[0]
        instance.game_position=tuple(float(v) for v in point)
        self.viewer().refresh_instance(instance.index)
    def step(self):
        viewer=self.viewer()
        if viewer is None or self.track is None:self.stop();return
        t=self.clock.elapsed()/1000
        if self.track[0]=='move':
            _,a,b,seconds,hold=self.track
            f=min(1.0,t/seconds) if seconds else 1.0
            self._place(a+(b-a)*f)
            if t>=seconds+hold:self.stop()
        else:
            _,point,seconds,hold=self.track
            c=viewer.camera_controls
            if not self.started:
                from gui.level.level_scene import view_point
                from gui.level.level_viewer import UNIT_SCALE
                centre=tuple(v/UNIT_SCALE for v in view_point(point))
                c.glide_to(centre,1.2,frames=max(1,int(seconds*60)))
                self.started=True
            if t>=seconds+hold:self.stop()
    def stop(self):
        self.timer.stop();viewer=self.viewer()
        if self.saved is not None:
            instance,(x,y,z)=self.saved;self.saved=None
            instance.x,instance.y,instance.z=x,y,z
            if viewer is not None:
                try:viewer.refresh_instance(instance.index)
                except Exception:pass
        if self.camera is not None and viewer is not None:
            c=viewer.camera_controls;c.stop_glide()
            (c.camera_x,c.camera_y,c.camera_z,c.camera_angle_h,c.camera_angle_v,c.orbit_distance)=self.camera
            viewer.update()
        self.camera=None;self.track=None


class EventEditor(QDialog):
    def __init__(self, panel, instance):
        super().__init__(panel)
        self.panel,self.instance=panel,instance
        self.setWindowTitle('Events — '+instance.label)
        self.setModal(False);self.setWindowFlag(Qt.WindowType.Window,True)
        self.resize(1320,840)
        self.exe=Path(panel.exe_path).read_bytes()
        self.overlay=Path(panel.scene.overlay_path).read_bytes()
        handler=script.handler_for(panel.scene,instance)
        self.native_handler=handler
        self.bindings=script.discover(self.exe,self.overlay,handler) if handler else []
        self.originals={}
        self.drafts={}
        self.active=None;self.selected=None
        self.bank=None;self.bank_address=None;self.linked_entry=None;self.voice_job=None
        self.items=None
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio)
        outer=QVBoxLayout(self);splitter=QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)
        left=QWidget();layout=QVBoxLayout(left);splitter.addWidget(left)
        right=QWidget();preview_layout=QVBoxLayout(right);splitter.addWidget(right)
        preview_layout.addWidget(QLabel('Preview'))
        self.preview_tabs=QTabWidget();preview_layout.addWidget(self.preview_tabs,1)
        text_tab=QWidget();text_layout=QVBoxLayout(text_tab)
        self.preview_tabs.addTab(text_tab,'Dialogue / voice')
        self.model_tab=QWidget();self.model_layout=QVBoxLayout(self.model_tab)
        self.preview_model_button=QPushButton('Preview selected entity / action')
        self.preview_model_button.clicked.connect(self.preview_model_action)
        self.model_layout.addWidget(self.preview_model_button)
        self.model_note=QLabel('Model previews run only when requested.');self.model_note.setWordWrap(True)
        self.model_layout.addWidget(self.model_note)
        self.sprite_preview=QLabel();self.sprite_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.model_layout.addWidget(self.sprite_preview)
        self.preview_tabs.addTab(self.model_tab,'Model / animation')
        self.path_preview=PathPreview()
        self.preview_tabs.addTab(self.path_preview,'Scene path')
        code_tab=QWidget();code_layout=QVBoxLayout(code_tab);code_layout.setContentsMargins(0,0,0,0)
        code_bar=QHBoxLayout()
        self.code_edit=QPushButton('Edit code');self.code_edit.setCheckable(True)
        self.code_edit.setToolTip('Edit the instructions shown, in place: a changed line is assembled at its own address.')
        self.code_edit.toggled.connect(self.edit_code)
        self.code_save=QPushButton('Save code');self.code_save.clicked.connect(self.save_code)
        self.code_undo=QPushButton('Undo');self.code_redo=QPushButton('Redo')
        self.code_discard=QPushButton('Discard');self.code_discard.clicked.connect(lambda:self.code_edit.setChecked(False))
        for b in (self.code_edit,self.code_save,self.code_undo,self.code_redo,self.code_discard):code_bar.addWidget(b)
        code_bar.addStretch();code_layout.addLayout(code_bar)
        self.code_preview=QPlainTextEdit();self.code_preview.setReadOnly(True)
        self.code_preview.setFont(QFont('Consolas',9))
        self.code_preview.setUndoRedoEnabled(True)
        self.code_undo.clicked.connect(self.code_preview.undo);self.code_redo.clicked.connect(self.code_preview.redo)
        self.code_preview.undoAvailable.connect(lambda on:self.code_undo.setEnabled(on and self.code_edit.isChecked()))
        self.code_preview.redoAvailable.connect(lambda on:self.code_redo.setEnabled(on and self.code_edit.isChecked()))
        code_layout.addWidget(self.code_preview,1)
        self.code_tab=code_tab
        self.preview_tabs.addTab(code_tab,'Native code')
        for b in (self.code_save,self.code_undo,self.code_redo,self.code_discard):b.setEnabled(False)
        self.model_viewer=None;self.animation_job=None;self.animation_frames=[];self.animation_tick=0
        self.preview_generation=0
        self.animation_timer=QTimer(self);self.animation_timer.setInterval(33)
        self.animation_timer.timeout.connect(self.advance_animation)
        splitter.setSizes([560,760]);splitter.setStretchFactor(1,1)
        hint=QLabel('Event pages are the native scripts this entity’s code loads. Each runs top to bottom; '
                    'rows with ⟶ jump. Quest selection and handler-side actions stay in native code.')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.pages=QComboBox()
        for n,binding in enumerate(self.bindings):
            self.pages.addItem(f'Page {n+1} — script 0x{binding.script:08X} (loaded at 0x{binding.caller:08X})',n)
        layout.addWidget(self.pages)
        self.actions=QListWidget();self.actions.setFont(QFont('Consolas',9));layout.addWidget(self.actions,1)
        play_row=QHBoxLayout()
        self.play_button=QPushButton('▶ Play');self.play_button.setToolTip('Preview the selected action again: moves and camera in the level viewport, text, voice, sound and animation here.')
        self.play_button.clicked.connect(lambda:self.play_selected(manual=True))
        self.autoplay=QCheckBox('Autoplay');self.autoplay.setChecked(True)
        self.autoplay.setToolTip('Preview each action as soon as it is selected.')
        play_row.addWidget(self.play_button);play_row.addWidget(self.autoplay);play_row.addStretch()
        layout.addLayout(play_row)
        self.flow=QLabel();self.flow.setWordWrap(True);layout.addWidget(self.flow)
        self.form_widget=QWidget();self.form=QFormLayout(self.form_widget)
        layout.addWidget(self.form_widget)
        self.raw=QCheckBox('Edit raw parameters of undecoded actions')
        self.raw.toggled.connect(lambda _:self.select_action(self.actions.currentRow()))
        layout.addWidget(self.raw)
        self.fields=[]
        self.text_choices=QComboBox();self.text_choices.currentIndexChanged.connect(self.choose_text)
        text_layout.addWidget(self.text_choices)
        text_buttons=QHBoxLayout()
        self.pick_text=QPushButton('Choose dialogue…');self.pick_text.clicked.connect(self.choose_dialogue)
        self.pick_text.setToolTip('Pick the dialogue master and entry this action shows, from the area’s TXTD.')
        self.goto_txtd=QPushButton('Go to TXTD in IDX');self.goto_txtd.clicked.connect(self.go_to_txtd)
        self.goto_txtd.setToolTip('Open this area’s dialogue file in the Indexed View, on this entry.')
        text_buttons.addWidget(self.pick_text);text_buttons.addWidget(self.goto_txtd)
        text_layout.addLayout(text_buttons)
        self.pick_text.setEnabled(False);self.goto_txtd.setEnabled(False)
        self.text_preview=QPlainTextEdit()
        self.text_preview.setPlaceholderText('Select a conversation or message action to see its text.')
        self.text_preview.textChanged.connect(self.edit_text);text_layout.addWidget(self.text_preview,1)
        self.play_voice=QPushButton('Play linked voice');self.play_voice.clicked.connect(self.decode_voice)
        text_layout.addWidget(self.play_voice);self.play_voice.setEnabled(False)
        self.sound_button=QPushButton('Play action sound');self.sound_button.clicked.connect(self.play_sound)
        text_layout.addWidget(self.sound_button);self.sound_button.setEnabled(False)
        buttons=QHBoxLayout()
        self.types=QComboBox()
        names={0:'Show text… (pick dialogue)',1:'Show text with animation… (pick dialogue)'}
        for opcode in script.INSERTABLE: self.types.addItem(names.get(opcode,script.LABELS[opcode]),opcode)
        self.types.setCurrentIndex(self.types.findData(5))
        self.add=QPushButton('Insert before');self.add.clicked.connect(self.insert)
        self.add_after=QPushButton('Insert after');self.add_after.clicked.connect(self.insert_after)
        buttons.addWidget(self.types,1);buttons.addWidget(self.add);buttons.addWidget(self.add_after);layout.addLayout(buttons)
        edit_row=QHBoxLayout()
        self.up=QPushButton('Move up');self.up.clicked.connect(lambda:self.move(-1))
        self.down=QPushButton('Move down');self.down.clicked.connect(lambda:self.move(1))
        self.remove=QPushButton('Delete');self.remove.clicked.connect(self.delete)
        self.revert=QPushButton('Revert page');self.revert.clicked.connect(self.revert_page)
        self.undo_button=QPushButton('Undo');self.undo_button.clicked.connect(self.undo)
        self.redo_button=QPushButton('Redo');self.redo_button.clicked.connect(self.redo)
        self.undo_button.setToolTip('Undo the last event edit (Ctrl+Z).');self.redo_button.setToolTip('Redo (Ctrl+Y or Ctrl+Shift+Z).')
        for b in (self.up,self.down,self.remove,self.revert,self.undo_button,self.redo_button):edit_row.addWidget(b)
        from PyQt6.QtGui import QKeySequence,QShortcut
        self._undo,self._redo,self._text_session=[],[],None
        for keys,slot in (('Ctrl+Z',self.undo),('Ctrl+Y',self.redo),('Ctrl+Shift+Z',self.redo)):
            shortcut=QShortcut(QKeySequence(keys),self);shortcut.activated.connect(slot)
        layout.addLayout(edit_row)
        self.shared=QCheckBox('Apply shared script binding and dialogue-entry changes')
        self.shared.setToolTip('This edits the selected native call site. Other actors that use it receive the same changes.')
        layout.addWidget(self.shared)
        self.status=QLabel('');self.status.setWordWrap(True);layout.addWidget(self.status)
        bottom=QHBoxLayout();bottom.addStretch()
        self.save=QPushButton('Apply event changes');self.save.clicked.connect(self.apply)
        close=QPushButton('Close');close.clicked.connect(self.reject)
        bottom.addWidget(self.save);bottom.addWidget(close);layout.addLayout(bottom)
        self.pages.currentIndexChanged.connect(self.page_changed)
        self.actions.currentRowChanged.connect(self.row_chosen)
        self.viewport=ViewportPreview(self)
        self.shared.toggled.connect(self.update_enabled)
        self._undo_state()
        self.page_changed(0)

    def page_changed(self,index):
        if self.active is not None: self.store_operands()
        self.selected=None
        self.actions.blockSignals(True)
        self.active=index if 0<=index<len(self.bindings) else None
        self.actions.clear()
        self.actions.blockSignals(False)
        if self.active is None:
            if self.native_handler:
                from game.event_preview import native_code_at
                try:self.code_preview.setPlainText('Native handler — this entity’s behaviour runs here.\n\n'+native_code_at(self.exe,self.overlay,self.native_handler,160))
                except ValueError as exc:self.code_preview.setPlainText(str(exc))
                self.preview_tabs.setCurrentWidget(self.code_tab)
            else:self.code_preview.setPlainText('No native handler is associated with this entity.')
            self.path_preview.set([],None,self.start_position(),self.others(),'No event page')
            self.status.setText('No statically resolved native scripts were found for this entity. '
                'Its behaviour is in its handler code (Native code tab); the Model tab shows its model.')
            self.update_enabled();return
        if index not in self.drafts:
            nodes=script.read_graph(script.reader(self.exe,self.overlay),self.bindings[index].script)
            self.originals[index]=deepcopy(nodes);self.drafts[index]=nodes
        self.populate()

    def start_position(self):
        try:return tuple(float(v) for v in self.instance.game_position)
        except (AttributeError,TypeError):return None

    def others(self):
        scene=getattr(self.panel.scene,'instances',None) or ()
        room=getattr(self.instance,'scene',None)
        out=[]
        for i in scene:
            if i is self.instance or getattr(i,'scene',None)!=room:continue
            try:out.append(tuple(float(v) for v in i.game_position))
            except (AttributeError,TypeError):pass
        return out

    def describe(self,c):
        """The row's summary, with the dialogue line or item name when known."""
        text=script.summary(c)
        try:
            if c.opcode in (0,1) and c.operands[1]>=0:
                self.load_bank()
                entries=self.bank['entries'][c.operands[0]&0xfff]['entries']
                line=entries[c.operands[1]]['text'].split('{$')[0].replace('\n',' ').strip()
                if line:text+=f' — “{line[:48]}{"…" if len(line)>48 else ""}”'
            elif c.opcode==40 and c.operands[0] in (2,3):
                name=self.item_name(c.operands[1])
                if name:text+=f' — {name}'
        except (ValueError,OSError,KeyError,IndexError,TypeError):pass
        return text

    def item_name(self,item):
        if self.items is None:
            try:
                from formats.sprites.pickup_art import item_names
                self.items=item_names(self.panel.exe_path)
            except Exception:self.items={}
        return self.items.get(item)

    def row_texts(self,nodes):
        index={c.address:n for n,c in enumerate(nodes)}
        depth=[0]*len(nodes);incoming={}
        for n,c in enumerate(nodes):
            t=c.branch
            if t in index:
                incoming.setdefault(index[t],[]).append(n)
                for k in range(n+1,index[t]):depth[k]+=1
        out=[]
        for n,c in enumerate(nodes):
            line=f'{n+1:02d}  '+'│   '*depth[n]+'◆ '+self.describe(c)
            t=c.branch
            if t is not None:
                if t in index:line+=f'   ⟶ #{index[t]+1:02d}'+(' if true' if c.opcode in script.CONDITION_OPS else '')
                elif t<script.BASE:line+=f'   ⟶ shared resident script 0x{t:08X}'
            if c.header&script.END:line+='   ■ end'
            if n in incoming:line+='   ◁ '+', '.join(f'#{k+1:02d}' for k in incoming[n])
            out.append(line)
        return out

    def populate(self,selected=0):
        self.actions.blockSignals(True);self.actions.clear()
        nodes=self.drafts.get(self.active,())
        for c,line in zip(nodes,self.row_texts(nodes)):
            item=QListWidgetItem(line)
            item.setForeground(QColor(CATEGORY.get(c.opcode,'#d8d8d8')))
            if c.opcode not in script.LABELS:item.setToolTip('Undecoded native action — its code is in the Native code tab.')
            self.actions.addItem(item)
        self.actions.blockSignals(False)
        selected=max(0,min(selected,len(nodes)-1))
        self.actions.setCurrentRow(selected)
        self.select_action(selected)
        self.update_enabled()

    def store_operands(self):
        if getattr(self,'selected',None) is not None and self.active in self.drafts:
            nodes=self.drafts[self.active]
            if self.selected<len(nodes) and self.fields:
                values=[box.value() for box in self.fields]
                if values!=nodes[self.selected].operands:
                    self.remember()
                    nodes[self.selected].set_operands(values)
                    for n,line in enumerate(self.row_texts(nodes)):
                        item=self.actions.item(n)
                        if item is not None:item.setText(line)

    def select_action(self,row):
        self.animation_timer.stop()
        self.preview_generation+=1
        self.preview_model_button.setEnabled(True)
        self.store_operands()
        self.selected=None
        while self.form.rowCount(): self.form.removeRow(0)
        self.fields=[]
        nodes=self.drafts.get(self.active,())
        if not 0<=row<len(nodes):return
        self.selected=row;c=nodes[row]
        self.sound_button.setEnabled(c.opcode==46)
        self.show_code(c)
        self.preview_model_button.setText('Play on entity model' if c.opcode in (1,30) else 'Show selected entity model')
        tab=(0 if c.opcode in TEXT_TAB_OPS else 1 if c.opcode in MODEL_TAB_OPS else
             2 if c.opcode in PATH_TAB_OPS else 3)
        self.preview_tabs.setCurrentIndex(tab)
        labels=script.FIELDS.get(c.opcode,())
        editable=(c.opcode in script.FIELDS and c.opcode!=62) or self.raw.isChecked()
        for n,value in enumerate(c.operands):
            box=QSpinBox();box.setRange(-32768,32767);box.setValue(value)
            box.setEnabled(editable)
            self.fields.append(box)
            box.editingFinished.connect(self.refresh_preview)
            name=labels[n] if n<len(labels) else (f'Parameter {n+1}' if n<3 else f'Extended parameter {n-2}')
            self.form.addRow(name,box)
        targets=', '.join(('Next' if p is None else 'Jump')+f': 0x{a:X}' for a,p in c.edges)
        extra=' Branch form: this action always takes its jump.' if c.dead_tail else ''
        self.flow.setText(f'{c.label} — native command 0x{c.opcode:02X}, flags 0x{c.header&0xf800:04X}. '+(targets or 'Ends this script.')+extra)
        self.show_text(c)
        self.update_path(row)
        if c.opcode==47:self.play_voice.setEnabled(True)
        if c.opcode in (0,1):
            self.status.setText('Dialogue resolved from the native presentation. A negative entry is chosen by NPC code at runtime; browsing candidates does not change that.')
        elif c.opcode in (13,14,47):
            self.status.setText('Voice and conversation parameters are native selectors; resident voice IDs play from their XA descriptors.')
        elif c.opcode==62:
            self.status.setText('This action calls native code. Its disassembly is in the Native code tab; it is kept verbatim.')
        elif c.opcode in PATH_TAB_OPS:
            self.status.setText('Scene path follows the rows as listed (branches are not chosen). Positions are game units; 30 ticks ≈ 1 s.')
        elif c.opcode not in script.FIELDS:
            self.status.setText('Undecoded parameters are read-only unless “Edit raw parameters” is ticked.')
        else:self.status.setText('Wait timers use game ticks. Animation IDs and movement parameters belong to this actor’s native resources.')
        self.update_enabled()

    # --- editing native code in place ---

    CODE_LINE=r'^\s*(?:0x)?([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s+(.+?)\s*$'

    def _code_lines(self,text):
        import re
        out={}
        for line in text.splitlines():
            m=re.match(self.CODE_LINE,line)
            if m:out[int(m.group(1),16)]=(int(m.group(2),16),' '.join(m.group(3).split()))
        return out

    def edit_code(self,on):
        if on:
            self._code_original=self.code_preview.toPlainText()
            if not self._code_lines(self._code_original):
                self.code_edit.setChecked(False);self.status.setText('There are no instructions here to edit.');return
            self.code_preview.setReadOnly(False);self.code_preview.document().clearUndoRedoStacks()
            self.code_save.setEnabled(True);self.code_discard.setEnabled(True)
            self.status.setText('Editing native code: change an instruction or its word; addresses stay where they are. Save writes the area file (or MAIN.EXE).')
        else:
            original=getattr(self,'_code_original',None)
            self.code_preview.setReadOnly(True)
            if original is not None and self.code_preview.toPlainText()!=original:
                self.code_preview.setPlainText(original)
            self._code_original=None
            for b in (self.code_save,self.code_undo,self.code_redo,self.code_discard):b.setEnabled(False)

    def code_changes(self):
        """{address: new word} from the edited text, or raise ValueError."""
        from psx.mips import assemble,AssemblyError,Instruction
        before=self._code_lines(self._code_original or '')
        after=self._code_lines(self.code_preview.toPlainText())
        changes={}
        for address,(word,text) in after.items():
            if address not in before:
                raise ValueError(f'0x{address:08X} was not in the code shown; only existing instructions can be changed, in place.')
            old_word,old_text=before[address]
            if word==old_word and text==old_text:continue
            if text==old_text:new=word            # the word itself was edited
            else:
                try:new=assemble(text,address)
                except AssemblyError as exc:raise ValueError(f'0x{address:08X}: {exc}') from None
            if new!=old_word:changes[address]=new
        return changes

    def save_code(self):
        import struct
        try:changes=self.code_changes()
        except ValueError as exc:
            QMessageBox.warning(self,'Code not saved',str(exc));return
        if not changes:
            self.status.setText('No instruction changed.');self.code_edit.setChecked(False);return
        resident=[a for a in changes if a<script.BASE]
        from psx.mips import Instruction
        listing='\n'.join(f'0x{a:08X}: {Instruction(a,w).text()}' for a,w in sorted(changes.items())[:12])
        warn='\n\nMAIN.EXE is shared by every area.' if resident else ''
        if QMessageBox.question(self,'Save native code',f'Write {len(changes)} instruction(s)?\n\n{listing}{warn}')!=QMessageBox.StandardButton.Yes:
            return
        panel=self.panel;files={}
        main=struct.unpack_from('<I',self.exe,24)[0]
        overlay=bytearray(self.overlay);exe=bytearray(self.exe)
        for address,word in changes.items():
            if address>=script.BASE:
                at=address-script.BASE
                if at+4>len(overlay):QMessageBox.warning(self,'Code not saved',f'0x{address:08X} is past the area file.');return
                struct.pack_into('<I',overlay,at,word)
            else:
                at=address-main+0x800
                if not 0x800<=at<=len(exe)-4:QMessageBox.warning(self,'Code not saved',f'0x{address:08X} is outside MAIN.EXE.');return
                struct.pack_into('<I',exe,at,word)
        try:
            if bytes(overlay)!=self.overlay:
                path=panel.scene.overlay_path
                if Path(path).read_bytes()!=self.overlay:raise ValueError('The area file changed. Reopen Events before saving code.')
                files[path]=bytes(overlay)
            if bytes(exe)!=self.exe:
                if Path(panel.exe_path).read_bytes()!=self.exe:raise ValueError('MAIN.EXE changed. Reopen Events before saving code.')
                files[panel.exe_path]=bytes(exe)
            panel._write_placement_files(files)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Code not saved',str(exc));return
        for path,data in files.items():panel._placement_files[Path(path).name]=data
        if bytes(overlay)!=self.overlay:panel.scene.overlay_data=bytes(overlay)
        self.overlay,self.exe=bytes(overlay),bytes(exe)
        panel._scene_memory_cache.clear()
        self._code_original=None;self.code_edit.setChecked(False)
        c=self.drafts[self.active][self.selected] if self.active is not None and self.selected is not None else None
        if c is not None:self.show_code(c)
        self.status.setText(f'Wrote {len(changes)} instruction(s).')

    def show_code(self,c):
        if self.code_edit.isChecked():
            if self.code_preview.toPlainText()!=(self._code_original or ''):
                if QMessageBox.question(self,'Unsaved code','Discard the code edit?')!=QMessageBox.StandardButton.Yes:
                    return
            self.code_edit.setChecked(False)
        import struct
        from game.event_preview import native_code,native_code_at
        try:
            text=native_code(self.exe,self.overlay,c)
            if c.opcode==62:
                target=script.code_pointer(c)
                try:text=f'Script calls native code at 0x{target:08X}\n\n'+native_code_at(self.exe,self.overlay,target,160)+'\n\n— dispatcher —\n'+text
                except ValueError as exc:text=f'Native code target 0x{target:08X}: {exc}\n\n'+text
            elif c.opcode==21:
                pointer=script.code_pointer(c)
                try:
                    values=struct.unpack('<6h',script.reader(self.exe,self.overlay)(pointer,12))
                    text=f'Camera transform at 0x{pointer:08X}: {values}\n\n'+text
                except (ValueError,struct.error):pass
            self.code_preview.setPlainText(text)
        except (ValueError,IndexError) as exc:self.code_preview.setPlainText(str(exc))

    def update_path(self,row):
        from game.event_preview import trace
        nodes=self.drafts.get(self.active,())
        start=self.start_position()
        if start is None:self.path_preview.set([],row,None,[],'');return
        steps=trace(nodes,row,start)
        self.path_preview.set(steps,row,start,self.others(),f'Page {self.active+1}, through #{row+1:02d}')

    def refresh_preview(self):
        self.store_operands()
        if self.selected is not None:
            c=self.drafts[self.active][self.selected]
            self.show_text(c);self.update_path(self.selected);self.show_code(c)

    def show_text(self,c):
        self.player.stop();self.linked_entry=None
        self.pick_text.setEnabled(c.opcode in (0,1) and game_build.current().name=='us-retail')
        self.goto_txtd.setEnabled(c.opcode in (0,1))
        self.text_choices.blockSignals(True);self.text_choices.clear();self.text_choices.blockSignals(False)
        self.text_preview.blockSignals(True);self.text_preview.clear();self.text_preview.blockSignals(False)
        self.text_preview.setReadOnly(True);self.play_voice.setEnabled(False)
        if c.opcode==40:
            o=c.operands
            self.text_preview.blockSignals(True)
            if o[0] in (2,3):
                name=self.item_name(o[1])
                self.text_preview.setPlainText(f'Item message for item {o[1]}'+(f': {name}' if name else '')+
                    ('\nWaits until the message closes.' if o[0]&1 else ''))
            else:
                self.text_preview.setPlainText(f'Message resource {o[1]}, group {o[2]} (mode {o[0]}). '
                    'This message table is not decoded yet; the IDs are shown as stored.')
            self.text_preview.blockSignals(False)
            return
        if c.opcode not in (0,1):return
        try:
            self.load_bank()
            master=c.operands[0]&0xfff;entry=c.operands[1]
            if master>=len(self.bank['entries']):raise ValueError('Presentation resource does not resolve to a dialogue master.')
            self.linked_master=master
            entries=self.bank['entries'][master]['entries']
            self.text_choices.blockSignals(True)
            if entry<0:
                self.text_choices.addItem('Runtime-selected dialogue — browse candidates',None)
                for index,value in enumerate(entries):self.text_choices.addItem(f'Candidate {index}: '+value['text'].split('{$')[0][:60],index)
            elif entry<len(entries):self.text_choices.addItem(f'Dialogue entry {entry}',entry)
            else:raise ValueError('The dialogue entry is outside this master.')
            self.text_choices.blockSignals(False);self.choose_text(self.text_choices.currentIndex())
        except (ValueError,OSError,KeyError,IndexError) as exc:
            self.text_choices.blockSignals(False)
            self.text_preview.setPlainText(str(exc))

    def update_enabled(self):
        enabled=self.active is not None and game_build.current().name=='us-retail'
        nodes=self.drafts.get(self.active,())
        c=nodes[self.selected] if enabled and self.selected is not None and self.selected<len(nodes) else None
        self.add.setEnabled(c is not None)
        self.add_after.setEnabled(c is not None and c.fallthrough is not None)
        self.remove.setEnabled(c is not None and c.branch is None and c.fallthrough is not None)
        self.up.setEnabled(c is not None and self.selected>0)
        self.down.setEnabled(c is not None and self.selected<len(nodes)-1)
        self.revert.setEnabled(enabled and self.active in self.originals and nodes!=self.originals[self.active])
        self.save.setEnabled(enabled and self.shared.isChecked())

    def _edit(self,action,row):
        if self.selected is None:return
        self.store_operands()
        self.remember()
        try:action()
        except ValueError as exc:self.status.setText(str(exc));return
        self.selected=None;self.populate(row)

    # --- undo / redo: whole-editor snapshots (pages and dialogue text) ---

    def _snapshot(self):
        return (deepcopy(self.drafts),deepcopy(self.bank) if self.bank is not None else None,
                self.active,self.selected)

    def remember(self):
        self._undo.append(self._snapshot());del self._undo[:-200]
        if not getattr(self,'_stepping',False):self._redo.clear()
        self._undo_state()

    def _undo_state(self):
        if hasattr(self,'undo_button'):
            self.undo_button.setEnabled(bool(self._undo));self.redo_button.setEnabled(bool(self._redo))

    def undo(self):self._step(self._undo,self._redo)
    def redo(self):self._step(self._redo,self._undo)

    def _step(self,source,target):
        self._stepping=True
        try:self.store_operands()
        finally:self._stepping=False
        if not source:return
        target.append(self._snapshot())
        drafts,bank,active,selected=source.pop()
        self.drafts=drafts
        if bank is not None:
            # Keep the same dict: the text editor's pending edits share it.
            if self.bank is None:self.bank=bank
            else:self.bank.clear();self.bank.update(bank)
        self.linked_entry=None;self._text_session=None
        self.selected=None
        if active is not None and active!=self.active:
            self.pages.blockSignals(True);self.pages.setCurrentIndex(active);self.pages.blockSignals(False)
            self.active=active
        self.populate(selected or 0);self._undo_state()

    def _new_operands(self):
        opcode=self.types.currentData()
        return opcode,script.INSERTABLE.get(opcode,(0,0,0))

    def _text_operands(self,opcode,values):
        """A new Show text: the picked line, with the flags and behaviour of
        one this NPC already uses, so it presents the way its others do."""
        template=next((c.operands for nodes in self.drafts.values() for c in nodes if c.opcode==opcode),None)
        picked=self.pick_dialogue()
        if picked is None:return None
        master,entry=picked
        values=list(template or values)
        values[0]=(values[0]&~0xfff)|master;values[1]=entry
        return tuple(values)

    def insert(self):
        row=self.selected;opcode,values=self._new_operands()
        if opcode in (0,1):
            values=self._text_operands(opcode,values)
            if values is None:return
        self._edit(lambda:script.insert_before(self.drafts[self.active],row,opcode,values),row)

    def insert_after(self):
        row=self.selected;opcode,values=self._new_operands()
        if opcode in (0,1):
            values=self._text_operands(opcode,values)
            if values is None:return
        self._edit(lambda:script.insert_after(self.drafts[self.active],row,opcode,values),(row or 0)+1)

    def pick_dialogue(self,master=None,entry=None):
        """(master, entry) chosen from the area's TXTD, or None."""
        try:self.load_bank()
        except (ValueError,OSError,KeyError) as exc:
            QMessageBox.warning(self,'No dialogue',str(exc));return None
        dialog=DialoguePicker(self,self.bank,master,entry)
        return dialog.chosen() if dialog.exec() else None

    def choose_dialogue(self):
        if self.selected is None:return
        self.store_operands();c=self.drafts[self.active][self.selected]
        picked=self.pick_dialogue(c.operands[0]&0xfff,max(c.operands[1],0))
        if picked is None:return
        values=c.operands;values[0]=(values[0]&~0xfff)|picked[0];values[1]=picked[1]
        self.remember();c.set_operands(values);row=self.selected;self.selected=None;self.populate(row)

    def go_to_txtd(self):
        """Show the area's dialogue file in the Indexed View, on this line."""
        window=self.panel.window()
        try:self.load_bank()
        except (ValueError,OSError,KeyError) as exc:
            self.status.setText(str(exc));return
        if not hasattr(window,'reveal_address'):
            self.status.setText('The main window cannot show IDX files here.');return
        scene=self.panel.scene
        locations=getattr(window,'address_locations',{}).get(self.bank_address) or []
        prefer=next((tuple(l) for l in locations if tuple(l)[0]==scene.chunk_index),None)
        if not window.reveal_address(self.bank_address,prefer):
            self.status.setText('This dialogue file has no row in the Indexed View.');return
        c=self.drafts[self.active][self.selected] if self.selected is not None else None
        if c is not None and c.opcode in (0,1):
            master,entry=c.operands[0]&0xfff,max(c.operands[1],0)
            if self.linked_entry is not None and self.text_choices.currentData() is not None:
                entry=self.text_choices.currentData()
            viewer=getattr(window,'txtd_viewer',None)
            if viewer is not None:QTimer.singleShot(0,lambda:viewer.select_entry(master,entry))
        window.raise_();window.activateWindow()

    # --- previews on selection ---

    def row_chosen(self,row):
        self.select_action(row)
        if self.autoplay.isChecked():self.play_selected()

    def play_selected(self,manual=False):
        """Preview the selected action - once."""
        if self.selected is None:return
        self.store_operands()
        nodes=self.drafts[self.active];c=nodes[self.selected]
        if self.viewport.play(nodes,self.selected):return
        if c.opcode in (1,30):self.preview_model_action()
        elif c.opcode==46:self.play_sound()
        elif c.opcode==47 or (c.opcode in (0,1) and self.play_voice.isEnabled()):
            if getattr(self.panel.window(),'current_iso_path',None) or manual:self.decode_voice()

    def stop_preview(self):
        self.viewport.stop()

    def delete(self):
        row=self.selected
        self._edit(lambda:script.delete(self.drafts[self.active],row),row)

    def move(self,delta):
        row=self.selected
        if row is None:return
        top=row if delta>0 else row-1
        self._edit(lambda:script.move_down(self.drafts[self.active],top),row+delta)

    def revert_page(self):
        if self.active in self.originals:
            self.remember()
            self.drafts[self.active]=deepcopy(self.originals[self.active]);self.selected=None;self.populate(0)

    def load_bank(self):
        if self.bank is not None:return
        from formats.text import txtd
        scene=self.panel.scene
        where=scene.by_id.get(13)
        if where is None:raise ValueError('This area has no native dialogue bank in slot 13.')
        self.bank_address=scene.dat_start+where[0]
        window=self.panel.window()
        pending=getattr(window,'pending_txtd_edits',{}).get(self.bank_address)
        self.bank=deepcopy(pending['data']) if pending else txtd.preview(scene.dat_path,self.bank_address,verbose=False)
        self.bank_original=deepcopy(self.bank)

    def choose_text(self,index):
        self.linked_entry=None
        choice=self.text_choices.itemData(index)
        self.text_preview.blockSignals(True)
        if choice is None:
            self.text_preview.clear();self.text_preview.setReadOnly(True);self.play_voice.setEnabled(False)
        else:
            self.linked_entry=self.bank['entries'][self.linked_master]['entries'][choice]
            self.text_preview.setPlainText(self.linked_entry['text']);self.text_preview.setReadOnly(False)
            self.play_voice.setEnabled(self.linked_entry.get('extra',0xffff)!=0xffff)
        self.text_preview.blockSignals(False)

    def edit_text(self):
        if self.linked_entry is not None:
            # One undo step per line edited, not per keystroke.
            if self._text_session is not id(self.linked_entry):
                self.remember();self._text_session=id(self.linked_entry)
            self.linked_entry['text']=self.text_preview.toPlainText()

    def decode_voice(self):
        if self.voice_job and self.voice_job.isRunning():return
        self.store_operands()
        command=self.drafts[self.active][self.selected] if self.selected is not None else None
        resident=command is not None and command.opcode==47
        if self.linked_entry is None and not resident:return
        image=getattr(self.panel.window(),'current_iso_path',None)
        if not image:
            self.status.setText('Attach the source disc to preview this voice line.');return
        self.play_voice.setEnabled(False);self.status.setText('Decoding the linked voice…')
        self.audio.setVolume(1.)
        job=(ResidentVoiceDecode(self.exe,image,command.operands[1],self.panel) if resident else
             VoiceDecode(image,self.panel.scene.overlay_path,self.bank['entries'],self.linked_master,
                        deepcopy(self.linked_entry),self.panel))
        generation=self.preview_generation
        # A bound slot, not a lambda: Qt drops it if this window is gone
        # by the time the decode finishes.
        job.generation=generation
        self.voice_job=job;job.decoded.connect(self._voice_decoded);job.start()

    def _voice_decoded(self,result):
        job=self.voice_job
        self.voice_ready(result,getattr(job,'generation',None))

    def voice_ready(self,result,generation=None):
        if generation is not None and generation!=self.preview_generation:return
        if not self.isVisible():return
        samples,rate,note=result;self.status.setText(note)
        self.play_voice.setEnabled(self.linked_entry is not None)
        if samples is None:return
        from formats.audio.xa import wav_bytes
        self.voice_buffer=QBuffer(self);self.voice_buffer.setData(QByteArray(wav_bytes(samples,rate,1)))
        self.voice_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self.voice_buffer);self.player.play()

    def play_sound(self):
        if self.selected is None:return
        self.store_operands();c=self.drafts[self.active][self.selected]
        try:
            from game.event_preview import sound_samples
            window=self.panel.window();store=getattr(window,'snd_edits',None)
            if store and store.loaded():snd=store.rebuild()
            else:
                from formats.audio.voice import extract_file
                image=getattr(window,'current_iso_path',None)
                if not image:raise ValueError('Attach the source disc to preview sound effects.')
                snd=extract_file(image,'TOMBA2.SND')
            samples,rate,note=sound_samples(self.exe,self.overlay,snd,self.panel.scene.area_index,c.operands[0],c.operands[1])
            # Respect the command volume offset for a preview; the live
            # SPU envelope/mixer remains outside this waveform player.
            self.audio.setVolume(max(0.,min(1.,1+c.operands[2]/127)))
            self.voice_ready((samples,rate,note))
        except Exception as exc:self.status.setText(str(exc))

    def _animation_key_of(self,command):
        return (command.opcode,tuple(command.operands),self.bindings[self.active].animations)

    def preview_model_action(self):
        if self.selected is None:
            self.preview_tabs.setCurrentWidget(self.model_tab)
            self.show_model_parts(None);return
        self.store_operands();command=self.drafts[self.active][self.selected]
        self.preview_tabs.setCurrentWidget(self.model_tab)
        if command.opcode not in (1,30):
            self.show_model_parts(None);return
        key=self._animation_key_of(command)
        cached=getattr(self,'_animation_cache',{}).get(key)
        if cached is not None:
            self.animation_ready(cached);return
        if self.animation_job and self.animation_job.isRunning():
            # Plays as soon as it is ready, if this action is still selected.
            self.model_note.setText('Still initialising the entity’s native animation - it plays when ready.')
            return
        self.preview_model_button.setEnabled(False);self.model_note.setText('Initialising the selected entity’s native animation…')
        job=AnimationDecode(self.panel.scene,self.instance,command,self.panel,self.bindings[self.active].animations)
        job.key=key
        self.animation_job=job;job.decoded.connect(self._animation_decoded);job.start()

    def _animation_decoded(self,result):
        """Cache every result; play it if its action is (still) selected."""
        job=self.animation_job;key=getattr(job,'key',None)
        self.preview_model_button.setEnabled(True)
        frames,note=result
        if frames and key is not None:
            self._animation_cache=getattr(self,'_animation_cache',{});self._animation_cache[key]=result
        nodes=self.drafts.get(self.active,())
        current=nodes[self.selected] if self.selected is not None and self.selected<len(nodes) else None
        if current is not None and current.opcode in (1,30) and self._animation_key_of(current)==key:
            self.animation_ready(result)

    def animation_ready(self,result,generation=None):
        self.preview_model_button.setEnabled(True)
        if not self.isVisible():return
        frames,note=result;self.model_note.setText(note)
        if not frames:return
        self.animation_frames=frames;self.animation_tick=0
        self.show_model_parts(frames[0],frame=True);self.animation_timer.start()

    def advance_animation(self):
        if not self.animation_frames:return
        # Once, like every other preview; Play runs it again.
        if self.animation_tick+1>=len(self.animation_frames):
            self.animation_timer.stop();return
        self.animation_tick+=1
        self.show_model_parts(self.animation_frames[self.animation_tick],frame=False)

    def show_model_parts(self,parts,frame=True):
        from gui.level.level_scene import LevelScene,Instance
        from game.actor_sim import Posed
        from formats.models.smst_viewer import SMSTViewer
        from copy import copy
        import numpy as np
        selected=copy(self.instance)
        selected.follow=None;selected.flip=None;selected.flip_frames=()
        if parts is not None:
            from game.actor_assembly import degrees_to_units
            posed=Posed(self.instance.label,'Native animation preview',parts,[],np.array(self.instance.game_position),degrees_to_units(self.instance.angle))
            selected.assembly=posed;selected.sources=posed.sources
        selected.index=0
        scene=LevelScene();scene.__dict__.update(self.panel.scene.__dict__)
        scene.instances=[selected];scene.__dict__.pop('_built_model',None)
        model=scene.build();model['vertices']=scene.positions(model).tolist()
        if not len(model['vertices']):
            art=getattr(self.instance,'art',None)
            if art is not None and art.frames:
                from gui.level.pickup_sprites import SpriteBank,RESIDENT_SPRT_ID,AREA_SPRT_ID
                from PyQt6.QtGui import QImage,QPixmap
                owner=self.panel.scene
                where=owner.resident.get(RESIDENT_SPRT_ID) if art.resident else (owner.dat_start,owner.by_id.get(AREA_SPRT_ID))
                if where and where[1]:
                    start,(offset,size)=where
                    bank=SpriteBank(owner.dat_path,start,offset,size,self.panel.viewer.vram_raw_bytes)
                    made=bank.image(art.frames[0].frame,art.clut if art.recolored else None)
                    if made:
                        image=made[0].convert('RGBA');raw=image.tobytes()
                        qt=QImage(raw,image.width,image.height,QImage.Format.Format_RGBA8888).copy()
                        self.sprite_preview.setPixmap(QPixmap.fromImage(qt).scaled(256,256,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation))
                        self.model_note.setText('This entity uses native sprite artwork. The first authored frame is shown.');return
            self.model_note.setText('This entity has no resolved model geometry. Its native code remains available.');return
        self.sprite_preview.clear()
        if self.model_viewer is None:
            self.model_viewer=SMSTViewer(self.model_tab);self.model_viewer.spread=False
            self.model_viewer.spread_action.setVisible(False)
            self.model_viewer.set_vram(self.panel.viewer.vram_raw_bytes,self.panel.viewer.vram_qimage)
            self.model_layout.addWidget(self.model_viewer,1)
        self.model_viewer.model_data=model;self.model_viewer.hidden_groups=set()
        self.model_viewer.prepare_buffers()
        if frame:self.model_viewer.frame_model()
        self.model_viewer.update()

    def done(self,result):
        self.viewport.stop()
        self.preview_generation+=1
        self.animation_timer.stop();self.player.stop();super().done(result)

    def apply(self):
        if not self.shared.isChecked() or game_build.current().name!='us-retail':
            self.status.setText('Shared edits must be enabled, and event writing currently requires US retail.');return
        self.store_operands()
        data=self.overlay
        try:
            for page,nodes in self.drafts.items():
                if nodes==self.originals[page]:continue
                data=script.stage(self.exe,data,self.bindings[page],nodes)
            text_changed=self.bank is not None and self.bank!=self.bank_original
            if data==self.overlay and not text_changed:
                self.status.setText('No event changes to apply.');return
            if text_changed:
                window=self.panel.window();scene=self.panel.scene
                if not hasattr(window,'pending_txtd_edits'):raise ValueError('The main text editor is unavailable.')
                where=scene.by_id[13]
                locations=getattr(window,'address_locations',{}).get(self.bank_address)
                if not locations:raise ValueError('The native dialogue file has no verified archive location.')
                current=window.pending_txtd_edits.get(self.bank_address)
                if current and current['data']!=self.bank_original:
                    raise ValueError('The dialogue changed in another editor. Reopen Events before applying.')
            path=self.panel.scene.overlay_path
            if Path(path).read_bytes()!=self.overlay:raise ValueError('The area file changed. Close and reopen Events before applying.')
            if data!=self.overlay:
                self.panel._write_placement_files({path:data})
                self.panel._placement_files[Path(path).name]=data
            self.panel.scene.overlay_data=data
            self.panel._scene_memory_cache.clear()
            if text_changed:
                viewer=getattr(window,'txtd_viewer',None)
                if viewer is not None:
                    viewer.stage_external_data(scene.dat_path,scene.dat_start,where[0],self.bank,locations)
                window.pending_txtd_edits[self.bank_address]={'kind':'txtd','id':13,'dat_start':scene.dat_start,
                    'offset':where[0],'data':self.bank,'locations':locations}
                window._set_txtd_tree_item_state(self.bank_address,'edited');window._refresh_edit_status()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Event changes were not applied',str(exc));return
        self.accept()

"""Native event pages and an editable, relocatable action stack."""
from copy import deepcopy
from pathlib import Path
from PyQt6.QtCore import Qt,QObject,pyqtSignal,QBuffer,QByteArray,QIODevice,QTimer
from threading import Thread
from PyQt6.QtMultimedia import QAudioOutput,QMediaPlayer
from PyQt6.QtCore import QPointF,QRectF
from PyQt6.QtGui import QColor,QFont,QPainter,QPen,QPolygonF
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,
    QListWidget,QListWidgetItem,QFormLayout,QSpinBox,QPushButton,QMessageBox,QCheckBox,QWidget,QPlainTextEdit,QSplitter,QTabWidget,
    QTabBar,QGroupBox,QLineEdit,QFrame,QGridLayout,QStyle,QStyledItemDelegate,QMenu,QInputDialog,
    QTreeWidget,QTreeWidgetItem)
from game import event_script as script, game_build
from gui import theme

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
        except Exception as exc:self.emit_result(([],str(exc),None))

class MusicDecode(VoiceDecode):
    def __init__(self,snd,overlay_name,overlay,cue,parent):
        QObject.__init__(self,parent);self.args=(snd,overlay_name,overlay,cue);self.worker=None
    def run(self):
        try:
            from game.event_preview import music_cue
            self.emit_result(music_cue(*self.args))
        except Exception as exc:self.emit_result((None,str(exc)))


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
    **{op:'#c08cff' for op in (8,21,22,23,24,25)}, **{op:'#a0a0a0' for op in (4,5,6,7,9,10)}, 11:'#4fd1c5', 62:'#ff6b6b'}
# The same, on a light ground: RPG Maker 2003's command colours.
CATEGORY_LIGHT = {**{op:'#000080' for op in (0,1,2,3,13,14,40)}, **{op:'#008080' for op in (11,46,47)},
    **{op:'#0000ff' for op in (42,43,44)}, **{op:'#800080' for op in (28,29,30,55,57)},
    **{op:'#800000' for op in (17,31,35,36,37,49,52,54,56,58,59)},
    **{op:'#806000' for op in (8,21,22,23,24,25)}, **{op:'#c00040' for op in (4,5,6,7,9,10)}, 62:'#c00000'}
PATH_TAB_OPS = frozenset((5,17,21,22,23,24,31,35,36,37,49))
ANIMATION_OPS = frozenset((1,30,57,58))   # an animation on the entity
TOMBA_OPS = frozenset((55,57,58))         # an animation on Tomba
CAMERA_OPS = frozenset((21,22,23,24,25))
TOMBA_PLACE_OPS = frozenset((49,54,56))   # where Tomba stands / faces
MODEL_TAB_OPS = frozenset((28,29,30,55,57,58))
TEXT_TAB_OPS = frozenset((0,1,11,40,46,47))

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


ROW_ROLE = Qt.ItemDataRole.UserRole+1


def row_colour(opcode):
    """A command's text colour under the theme in use."""
    if theme.is_bright():return CATEGORY_LIGHT.get(opcode)
    return CATEGORY.get(opcode,'#d8d8d8')


class RowDelegate(QStyledItemDelegate):
    """Paints a command as RPG Maker does: "@>" then the command in its
    colour, on alternating bands. Number and jump notes stay grey."""
    def _parts(self,index):
        return index.data(ROW_ROLE)
    def paint(self,painter,option,index):
        parts=self._parts(index)
        if not parts:return super().paint(painter,option,index)
        number,indent,text,suffix,colour=parts
        pal=option.palette;r=option.rect
        selected=bool(option.state&QStyle.StateFlag.State_Selected)
        if selected:ground=pal.highlight().color()
        elif index.row()%2:
            ground=QColor('#e4ecf2') if theme.is_grey() else QColor(theme._mix(pal.base().color().name(),pal.text().color().name(),0.06))
        else:ground=pal.base().color()
        painter.save();painter.fillRect(r,ground);painter.setFont(option.font)
        plain=pal.highlightedText().color() if selected else pal.text().color()
        dim=plain if selected else QColor('#808080')
        fm=option.fontMetrics;x=r.left()+3
        for piece,pen in ((f'{number:02d} ',dim),(indent,dim),('@>',plain),
                          (text,plain if selected or not colour else QColor(colour)),
                          ('   '+suffix if suffix else '',dim)):
            if not piece:continue
            painter.setPen(pen)
            room=r.right()-x-4
            if room<=0:break
            shown=fm.elidedText(piece,Qt.TextElideMode.ElideRight,room) if fm.horizontalAdvance(piece)>room else piece
            if shown!=piece and not shown.endswith('…'):shown+='…'
            if shown!=piece:shown=shown[:-1].rstrip()+'...'      # cut by the window: three dots
            painter.drawText(QRectF(x,r.top(),max(0,r.right()-x),r.height()),
                             Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,shown)
            if shown!=piece:break
            x+=fm.horizontalAdvance(piece)
        if option.state&QStyle.StateFlag.State_HasFocus:
            from PyQt6.QtWidgets import QStyleOptionFocusRect
            focus=QStyleOptionFocusRect();focus.rect=r;focus.palette=pal
            focus.backgroundColor=ground;focus.state=option.state
            widget=option.widget
            (widget.style() if widget else self.parent().style()).drawPrimitive(
                QStyle.PrimitiveElement.PE_FrameFocusRect,focus,painter,widget)
        painter.restore()
    def sizeHint(self,option,index):
        parts=self._parts(index)
        size=super().sizeHint(option,index)
        if parts:
            size.setWidth(10)          # rows take the list's width; paint() shortens what does not fit
        size.setHeight(option.fontMetrics.height()+4)
        return size


class EventCommandDialog(QDialog):
    """RPG Maker's Event Command window: a tab per kind of command, each a
    two-column grid of buttons. Clicking one picks it."""
    GROUPS = (('Message',(0,1,40)),('Sound',(46,47,11,'End conversation music')),
              ('Wait',(5,6,7,8,9,10,'Wait: message to close','Wait: quest display to finish')),
              ('Flow / Progress',(12,'If event state…','If area progress…')),('Animation',(30,)),
              ('Move / Turn',('Move / Turn…',)),('Camera',(22,23,24)))
    last_tab = 0
    def __init__(self,parent,choices,title='Event Command'):
        super().__init__(parent);self.setWindowTitle(title);self.choice=None
        layout=QVBoxLayout(self);self.tabs=QTabWidget();layout.addWidget(self.tabs)
        by_key={}
        for label,data in choices:
            by_key.setdefault(label if isinstance(data,tuple) else data,(label,data))
        used=set()
        for name,keys in self.GROUPS:
            picked=[by_key[k] for k in keys if k in by_key]
            used.update(k for k in keys if k in by_key)
            if picked:self._page(name,picked)
        rest=[v for k,v in by_key.items() if k not in used]
        if rest:self._page('Other',rest)
        self.tabs.setCurrentIndex(min(EventCommandDialog.last_tab,self.tabs.count()-1))
        row=QHBoxLayout();row.addStretch()
        cancel=QPushButton('Cancel');cancel.setFixedWidth(88);cancel.clicked.connect(self.reject)
        row.addWidget(cancel);layout.addLayout(row)
    def _page(self,name,picked):
        page=QWidget();grid=QGridLayout(page);grid.setSpacing(0);grid.setContentsMargins(6,6,6,6)
        rows=(len(picked)+1)//2
        for n,(label,data) in enumerate(picked):
            b=QPushButton(label);b.setMinimumSize(180,24)
            b.clicked.connect(lambda _=False,d=data:self.pick(d))
            grid.addWidget(b,n%rows,n//rows)
        grid.setRowStretch(rows,1)
        self.tabs.addTab(page,name)
    def pick(self,data):
        self.choice=data;EventCommandDialog.last_tab=self.tabs.currentIndex();self.accept()


class MoveTurnDialog(QDialog):
    """The Move / Turn action: who moves or turns, then how."""
    def __init__(self,parent,command=None):
        super().__init__(parent);self.setWindowTitle('Move / Turn');self.choice=None
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.who=QComboBox();self.what=QComboBox()
        for who in dict.fromkeys(w for w,_a,_o,_v in script.MOVE_TURN):self.who.addItem(who)
        form.addRow('Who',self.who);form.addRow('What',self.what)
        self.who.currentIndexChanged.connect(self.fill)
        key=script.move_turn_key(command) if command is not None else None
        if key:self.who.setCurrentText(key[0])
        self.fill()
        if key:self.what.setCurrentText(key[1])
        note=QLabel('Only combinations the game has a command for are offered. Positions and angles are set afterwards in the '
                    'command’s parameters.');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();row.addStretch()
        ok=QPushButton('OK');ok.setDefault(True);ok.clicked.connect(self.accept)
        cancel=QPushButton('Cancel');cancel.clicked.connect(self.reject)
        row.addWidget(ok);row.addWidget(cancel);layout.addLayout(row)
    def fill(self):
        self.what.clear()
        for who,what,_op,_values in script.MOVE_TURN:
            if who==self.who.currentText():self.what.addItem(what)
    def accept(self):
        for who,what,op,values in script.MOVE_TURN:
            if who==self.who.currentText() and what==self.what.currentText():self.choice=(op,tuple(values));break
        super().accept()


class PageConditionDialog(QDialog):
    """"Run this page only if ..." - Tomba has an item, an event is in some state,
    a story byte has a value, the NPC is a given variant. All must hold; when they
    do not, the NPC runs the page it ran before (the Else)."""
    def __init__(self,parent,variant=None):
        super().__init__(parent);self.setWindowTitle('Page conditions');self.resize(560,420);self.variant=variant
        layout=QVBoxLayout(self)
        note=QLabel('A copy of this page will run only when ALL of these are true. Otherwise the NPC runs what it ran before '
                    '(the “Else”). Edit the copy and the original separately afterwards.');note.setWordWrap(True);layout.addWidget(note)
        self.list=QListWidget();layout.addWidget(self.list,1)
        row=QHBoxLayout()
        for text,slot in (('Tomba has item…',self.add_item),('Event…',self.add_event),('Story progress…',self.add_progress),
                          ('This NPC is variant…',self.add_variant)):
            b=QPushButton(text);b.clicked.connect(slot);row.addWidget(b)
        layout.addLayout(row)
        row=QHBoxLayout();remove=QPushButton('Remove selected');remove.clicked.connect(self.remove)
        row.addWidget(remove);row.addStretch()
        ok=QPushButton('OK');ok.setDefault(True);ok.clicked.connect(self.accept)
        cancel=QPushButton('Cancel');cancel.clicked.connect(self.reject)
        row.addWidget(ok);row.addWidget(cancel);layout.addLayout(row)
    @property
    def tests(self):
        return [self.list.item(n).data(Qt.ItemDataRole.UserRole) for n in range(self.list.count())]
    def _add(self,test):
        item=QListWidgetItem(script.test_text(test));item.setData(Qt.ItemDataRole.UserRole,test);self.list.addItem(item)
    def remove(self):
        for item in self.list.selectedItems():self.list.takeItem(self.list.row(item))
    def _pick(self,title,label,names):
        entries=[f'{n} - {name}' if name else str(n) for n,name in names]
        chosen,ok=QInputDialog.getItem(self,title,label,entries,0,False)
        return names[entries.index(chosen)][0] if ok else None
    def add_item(self):
        names=script._names.get('items',{})
        item=self._pick('Tomba has item','Item:',[(n,names.get(n,'')) for n in range(256)] if not names else sorted(names.items()))
        if item is None:return
        how,ok=QInputDialog.getItem(self,'Tomba has item','How many?',['at least','at most','exactly','none'],0,False)
        if not ok:return
        if how=='none':self._add(dict(kind='item',index=item,op='eq',value=0));return
        count,ok=QInputDialog.getInt(self,'Tomba has item','Number:',1,0 if how!='at least' else 1,255)
        if ok:self._add(dict(kind='item',index=item,op={'at least':'ge','at most':'le','exactly':'eq'}[how],value=count))
    def add_event(self):
        names=script._names.get('events',{})
        event=self._pick('Event','Event:',sorted(names.items()) if names else [(n,'') for n in range(1,139)])
        if event is None:return
        states={'is not started':('eq',0),'has started':('ne',0),'is cleared':('eq',255),'is not cleared':('ne',255)}
        state,ok=QInputDialog.getItem(self,'Event','The event:',list(states),0,False)
        if ok:self._add(dict(kind='event',index=event,op=states[state][0],value=states[state][1]))
    def add_progress(self):
        index,ok=QInputDialog.getInt(self,'Story progress','Area progress byte number (0-255):',0,0,255)
        if not ok:return
        how,ok=QInputDialog.getItem(self,'Story progress','The byte is:',['equal to','not equal to','at least','at most'],0,False)
        if not ok:return
        value,ok=QInputDialog.getInt(self,'Story progress','Value:',0,0,255)
        if ok:self._add(dict(kind='progress',index=index,op={'equal to':'eq','not equal to':'ne','at least':'ge','at most':'le'}[how],value=value))
    def add_variant(self):
        value,ok=QInputDialog.getInt(self,'NPC variant','The NPC’s scene-record variant:',self.variant if self.variant is not None else 0,0,255)
        if ok:self._add(dict(kind='variant',index=0,op='eq',value=value))


class LogicDialog(QDialog):
    """How an NPC's native code decides what runs, as If / Then: the tests on
    the way to each page (outermost first), merged into a tree, with what the
    page does at the end. Double-click a page to open it."""
    def __init__(self,editor):
        super().__init__(editor);self.setWindowTitle('NPC logic - which page runs when');self.resize(900,640)
        self.choice=None
        layout=QVBoxLayout(self)
        note=QLabel('Read from this NPC’s native code. Each branch is a test the code makes before it starts a page; '
                    'a “?” is a value the editor cannot work out. Pages for other NPCs of the same handler are included '
                    'unless hidden.');note.setWordWrap(True);layout.addWidget(note)
        self.tree=QTreeWidget();self.tree.setHeaderLabels(['If … then …','Rows']);self.tree.setColumnWidth(0,700)
        layout.addWidget(self.tree,1)
        self.tree.itemDoubleClicked.connect(self._open)
        close=QPushButton('Close');close.clicked.connect(self.reject)
        row=QHBoxLayout();row.addStretch();row.addWidget(close);layout.addLayout(row)
        self._build(editor)
        self.tree.expandAll()
    def _gist(self,editor,n,binding):
        try:nodes=script.read_graph(script.reader(editor.exe,editor.overlay),binding.script)
        except ValueError:return f'Page {n+1}',0
        parts=[]
        for c in nodes:
            if c.opcode in (0,1,62,18,19,40,41) or (c.opcode in (5,6,9,10,40) and not parts):
                parts.append(editor.describe(c)[:90])
            if len(parts)>=3:break
        return f'Page {n+1}: '+(' / '.join(parts) if parts else editor.describe(nodes[0])[:90] if nodes else ''),len(nodes)
    def _build(self,editor):
        nodes={}
        for n,binding in enumerate(editor.bindings):
            if binding.table:continue
            if n in getattr(editor,'_other_pages',set()) and not editor.show_others.isChecked():continue
            path=list(reversed(editor._page_conditions(binding)))
            parent=self.tree.invisibleRootItem();key=()
            for test in path:
                key+=(test,)
                if key not in nodes:
                    item=QTreeWidgetItem(parent,['If '+test,''])
                    f=item.font(0);f.setBold(True);item.setFont(0,f);nodes[key]=item
                parent=nodes[key]
            text,count=self._gist(editor,n,binding)
            leaf=QTreeWidgetItem(parent,[('→ ' if path else 'Always → ')+text,str(count)]);leaf.setData(0,Qt.ItemDataRole.UserRole,n)
    def _open(self,item,_column):
        n=item.data(0,Qt.ItemDataRole.UserRole)
        if n is not None:self.choice=n;self.accept()


class DragSpinBox(QSpinBox):
    """A number field you can also drag: press in it and move the mouse up
    (more) or down (less), a step per few pixels - Shift for ten. A click
    without moving edits as usual."""
    PIXELS = 4
    def __init__(self,*args):
        super().__init__(*args);self._drag=None
        self.lineEdit().installEventFilter(self)
        self.setToolTip('Drag up or down to change it (Shift: x10), or type a value.')
    def eventFilter(self,watched,event):
        from PyQt6.QtCore import QEvent
        kind=event.type()
        if kind==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton and self.isEnabled():
            self._drag=(event.globalPosition().y(),self.value(),False)
        elif kind==QEvent.Type.MouseMove and self._drag is not None:
            y,start,moved=self._drag
            delta=int((y-event.globalPosition().y())/self.PIXELS)
            if delta or moved:
                step=10 if event.modifiers()&Qt.KeyboardModifier.ShiftModifier else 1
                self._drag=(y,start,True);self.lineEdit().setCursor(Qt.CursorShape.SizeVerCursor)
                self.setValue(start+delta*step);return True
        elif kind==QEvent.Type.MouseButtonRelease and self._drag is not None:
            moved=self._drag[2];self._drag=None;self.lineEdit().unsetCursor()
            if moved:return True
        return super().eventFilter(watched,event)


class CommandDialog(QDialog):
    """Editing one command: its parameters, then only the panes it needs
    (the line and its voice, the model and its animation, the path, the
    native code). OK keeps the edits, Cancel puts the command back."""
    def __init__(self,editor):
        super().__init__(editor);self.setWindowTitle('Edit Command');self.resize(980,700)
        layout=QVBoxLayout(self)
        self.title=QLabel();self.title.setWordWrap(True)
        font=self.title.font();font.setBold(True);self.title.setFont(font)
        layout.addWidget(self.title)
        self.body=QVBoxLayout();layout.addLayout(self.body,1)
        row=QHBoxLayout();row.addStretch()
        ok=QPushButton('OK');ok.setFixedWidth(88);ok.setDefault(True);ok.clicked.connect(self.accept)
        cancel=QPushButton('Cancel');cancel.setFixedWidth(88);cancel.clicked.connect(self.reject)
        row.addWidget(ok);row.addWidget(cancel);layout.addLayout(row)


class ShowTextDialog(QDialog):
    """RPG Maker's Show Text window: the line itself, edited in place.
    'Choose line…' picks another entry of the area's TXTD."""
    def __init__(self,parent,bank,master,entry,title='Show Text'):
        super().__init__(parent);self.setWindowTitle(title);self.resize(520,220)
        self.bank,self.master,self.entry=bank,master,entry
        layout=QVBoxLayout(self)
        self.where=QLabel();layout.addWidget(self.where)
        self.text=QPlainTextEdit();self.text.setFont(QFont('Consolas',10));layout.addWidget(self.text,1)
        row=QHBoxLayout()
        pick=QPushButton('Choose line…');pick.clicked.connect(self.choose);row.addWidget(pick);row.addStretch()
        ok=QPushButton('OK');ok.setFixedWidth(88);ok.clicked.connect(self.accept)
        cancel=QPushButton('Cancel');cancel.setFixedWidth(88);cancel.clicked.connect(self.reject)
        row.addWidget(ok);row.addWidget(cancel);layout.addLayout(row)
        self.show_entry()
    def show_entry(self):
        self.where.setText(f'Dialogue {self.master}, entry {self.entry} — control codes like {{$END}} stay as written.')
        self.text.setPlainText(self.bank['entries'][self.master]['entries'][self.entry]['text'])
    def choose(self):
        picker=DialoguePicker(self,self.bank,self.master,self.entry)
        if picker.exec() and picker.chosen() is not None:
            self.master,self.entry=picker.chosen();self.show_entry()


class MessagePicker(QDialog):
    """Choose one of the resident System messages (command 40, modes 0/1)."""
    def __init__(self,parent,messages,current=0):
        super().__init__(parent);self.setWindowTitle('Choose system message');self.resize(560,460)
        layout=QVBoxLayout(self);self.list=QListWidget();layout.addWidget(self.list,1)
        for n,text in enumerate(messages):self.list.addItem(f'{n:3d}  '+plain_line(text))
        if 0<=current<len(messages):self.list.setCurrentRow(current)
        self.list.itemDoubleClicked.connect(lambda *_:self.accept())
        row=QHBoxLayout();row.addStretch()
        ok=QPushButton('OK');ok.clicked.connect(self.accept);cancel=QPushButton('Cancel');cancel.clicked.connect(self.reject)
        row.addWidget(ok);row.addWidget(cancel);layout.addLayout(row)


class DialoguePicker(QDialog):
    """Choose a dialogue master and entry from the area's TXTD."""
    def __init__(self,parent,bank,master=None,entry=None):
        super().__init__(parent);self.setWindowTitle('Choose dialogue');self.resize(720,520)
        self.bank=bank;layout=QVBoxLayout(self)
        self.masters=QComboBox()
        for n,m in enumerate(bank['entries']):
            first=next((plain_line(e['text']) for e in m['entries'] if plain_line(e['text'])),'')
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
            self.entries.addItem(f'{n:3d}  '+plain_line(e['text']))
        self.entries.setCurrentRow(0)
    def show_line(self,row):
        m=self.masters.currentIndex()
        entries=self.bank['entries'][m]['entries'] if m>=0 else []
        self.text.setPlainText(entries[row]['text'] if 0<=row<len(entries) else '')
    def chosen(self):
        if self.masters.currentIndex()<0 or self.entries.currentRow()<0:return None
        return self.masters.currentData(),self.entries.currentRow()


class TombaDecode(VoiceDecode):
    def __init__(self,scene,animation,parent):
        QObject.__init__(self,parent);self.args=(scene,animation);self.worker=None
    def run(self):
        try:
            from game.event_preview import tomba_frames
            self.emit_result(tomba_frames(*self.args))
        except Exception as exc:self.emit_result(str(exc))

class FramesDecode(VoiceDecode):
    """Frames for the viewport: the entity's animation N or Zippo's."""
    def __init__(self,scene,instance,animation,who,animations,parent,previous=None,blend=0):
        QObject.__init__(self,parent);self.args=(scene,instance,animation,who,animations,previous,blend);self.worker=None
    def run(self):
        scene,instance,animation,who,animations,previous,blend=self.args
        try:
            from game import event_preview
            if who=='zippo':
                frames,loop,origin,yaw=event_preview.zippo_frames(scene,animation,previous=previous,blend=blend)
            else:
                command=script.Command(0,__import__('struct').pack('<Hhhh',30,0,animation,0))
                frames,_note,loop=event_preview.animation_frames(scene,instance,command,animations=animations,
                                                                 previous=previous,blend=blend)
                origin=yaw=None
            self.emit_result((frames,loop,'',origin,yaw))
        except Exception as exc:self.emit_result(([],None,str(exc),None,None))


class CatalogueDecode(VoiceDecode):
    """The animations an actor's table holds: [(id, ticks, loops)]."""
    def __init__(self,scene,instance,animations,who,parent):
        QObject.__init__(self,parent);self.args=(scene,instance,animations,who);self.worker=None
    def run(self):
        scene,instance,animations,who=self.args
        try:
            from game.event_preview import animation_catalogue
            self.emit_result((who,animation_catalogue(scene,instance,animations,who),''))
        except Exception as exc:self.emit_result((who,[],str(exc)))


class ProgressDecode(VoiceDecode):
    """Which rows of the area set which area-progress byte."""
    def __init__(self,scene,exe,overlay,parent):
        QObject.__init__(self,parent);self.args=(scene,exe,overlay);self.worker=None
    def run(self):
        try:self.emit_result((script.progress_writers(*self.args),''))
        except Exception as exc:self.emit_result(({},str(exc)))


class ConditionsDecode(VoiceDecode):
    """The tests on the way to every page of a handler, worked out together."""
    def __init__(self,exe,overlay,handler,callers,parent):
        QObject.__init__(self,parent);self.args=(exe,overlay,handler,callers);self.worker=None
    def run(self):
        exe,overlay,handler,callers=self.args;out={}
        try:
            flow=script.Flow(exe,overlay,handler);memo={}
            for caller in callers:
                try:out[caller]=script.page_conditions(exe,overlay,handler,caller,flow=flow,memo=memo)
                except (ValueError,IndexError,KeyError) as exc:out[caller]=[f'Not read: {exc}']
        except Exception:pass
        self.emit_result(out)


class CameraDecode(VoiceDecode):
    def __init__(self,scene,nodes,row,tomba,parent):
        QObject.__init__(self,parent);self.args=(scene,deepcopy(nodes),row,tomba);self.worker=None
    def run(self):
        try:
            from game.event_preview import camera_path
            self.emit_result(camera_path(*self.args))
        except Exception as exc:self.emit_result(([],str(exc)))


def plain_line(text):
    """A dialogue entry on one line: control codes ({$...}) and line breaks
    become spaces, so the text after a break is kept, not dropped."""
    import re
    return ' '.join(re.sub(r'\{\$[^}]*\}',' ',text.replace('\n',' ')).split())


def baked_facing(assembly):
    """(pose yaw, facing) in game units for a simulated actor's pose, or None.
    Its parts were read turned the way the game turned it: the root part's
    matrix angle is the actor's +0x56 yaw (checked on A00/A01/A06 actors),
    while the pose itself is anchored at `yaw` (0 for scene-spawned NPCs)."""
    import math
    pieces=getattr(assembly,'pieces',None)
    if not pieces:return None
    m=pieces[0].matrix
    facing=int(round(math.degrees(math.atan2(m[0][2],m[0][0]))*4096/360))&0xfff
    return getattr(assembly,'yaw',0),facing


def yaw_toward(source,target):
    """Game yaw (4096 = 360°) from `source` to `target`, both (x, height,
    depth): ratan2(source depth - target depth, target x - source x), as
    f_RunScriptedActorFaceTombaSequence turns an actor."""
    import math
    return int(round(math.atan2(source[2]-target[2],target[0]-source[0])*4096/(2*math.pi)))&0xfff


import numpy as np
from game import actor_sim


# The stand-in Tomba's model id in the level scene: past any real trail id.
TOMBA_MODEL = actor_sim.TRAIL_ID + 0xFFF0


class ViewportPreview(QObject):
    """Shows actions in the level viewport. Display only: no record is
    written and nothing is marked as changed.

    Single-row mode plays the selected row and puts everything back.
    Simulation (timeline) mode leaves the entity and camera as the events
    up to the selected row leave them; reset() puts back what was there
    before the editor touched anything (on close, and before applying)."""
    FPS = 30
    def __init__(self,editor):
        super().__init__(editor)
        self.editor=editor;self.timer=QTimer(self);self.timer.setInterval(16)
        self.timer.timeout.connect(self.step);self.track=None;self.clock=None
        self.base=None;self.base_game=None;self.timeline=False
        # The stand-in Tomba: drawn as a posed wireframe with the markers.
        self.tomba=None;self.tomba_timer=QTimer(self);self.tomba_timer.setInterval(33)
        self.tomba_timer.timeout.connect(self._tomba_tick)
    def viewer(self):
        return getattr(self.editor.panel,'viewer',None)
    def _shown(self):
        viewer=self.viewer();instance=self.editor.instance
        return viewer is not None and any(i is instance for i in getattr(viewer,'instances',()))
    def _save_base(self):
        """What to put back: the entity and the camera before any preview."""
        if self.base is not None:return
        viewer=self.viewer();instance=self.editor.instance
        c=getattr(viewer,'camera_controls',None)
        try:self.base_game=tuple(float(v) for v in instance.game_position)
        except (AttributeError,TypeError):self.base_game=None
        self.base=((getattr(instance,'x',None),getattr(instance,'y',None),getattr(instance,'z',None),getattr(instance,'angle',None)),
                   None if c is None else (c.camera_x,c.camera_y,c.camera_z,c.camera_angle_h,c.camera_angle_v,c.orbit_distance))
    def _refresh(self):
        viewer=self.viewer();instance=self.editor.instance
        try:viewer.refresh_instance(instance.index)
        except Exception:pass
        if hasattr(viewer,'update'):viewer.update()
    def _place(self,point,yaw=None):
        """Stand the entity at `point` facing game yaw `yaw`. A simulated
        pose already carries the actor's own facing, so the instance turns
        by the difference, not by the whole yaw - else it faced away."""
        instance=self.editor.instance
        instance.game_position=tuple(float(v) for v in point)
        if yaw is not None and hasattr(instance,'angle'):
            found=baked_facing(getattr(instance,'assembly',None))
            if found is not None:
                pose_yaw,facing=found;yaw=pose_yaw+yaw-facing
            instance.angle=(yaw&0xfff)*360/4096
        self._refresh()
    def _aim(self,eye,focus):
        """Camera at `eye` looking at `focus`, both in game axes."""
        import math
        from gui.level.level_scene import view_point
        from gui.level.level_viewer import UNIT_SCALE
        e=[v/UNIT_SCALE for v in view_point(eye)];f=[v/UNIT_SCALE for v in view_point(focus)]
        d=[b-a for a,b in zip(e,f)];length=math.sqrt(sum(v*v for v in d)) or 1e-6
        d=[v/length for v in d]
        c=self.viewer().camera_controls;c.stop_glide()
        c.camera_x,c.camera_y,c.camera_z=-e[0],-e[1],-e[2]
        c.camera_angle_v=math.degrees(math.asin(max(-1.0,min(1.0,-d[1]))))
        c.camera_angle_h=math.degrees(math.atan2(d[0],-d[2]))
        c.orbit_distance=length
        self.viewer().update()
    # --- looping animation on the entity, and Zippo ---

    def _anim_timer(self):
        if getattr(self,'anim_timer',None) is None:
            self.anim_timer=QTimer(self);self.anim_timer.setInterval(33)
            self.anim_timer.timeout.connect(self._anim_tick)
        if not self.anim_timer.isActive():self.anim_timer.start()
        return self.anim_timer

    def show_entity_animation(self,frames,loop):
        """Loop `frames` (actor_sim Parts) on the entity where it stands.
        Its own model and pose come back with stop_entity_animation()."""
        if not self._shown() or not frames:return False
        from game.actor_sim import Posed
        from game.actor_assembly import degrees_to_units
        self._save_base()
        instance=self.editor.instance;viewer=self.viewer()
        if getattr(self,'entity',None) is None:
            self.entity=dict(assembly=instance.assembly,sources=instance.sources)
        origin=np.array(self.base_game if self.base_game is not None else instance.game_position,dtype=float)
        yaw=degrees_to_units(self.base[0][3] or 0) if self.base[0][3] is not None else 0
        posed=Posed(instance.label,'Event simulation',frames[0],[],origin,yaw)
        rebuild=tuple(posed.sources)!=tuple(instance.sources)
        instance.assembly=posed;instance.sources=posed.sources
        self.entity.update(frames=frames,loop=loop,tick=0,posed=posed)
        if rebuild:self._rebuild(viewer)
        else:self._refresh()
        self._anim_timer();return True

    def stop_entity_animation(self):
        e=getattr(self,'entity',None)
        if e is None:return
        self.entity=None
        instance=self.editor.instance;viewer=self.viewer()
        rebuild=tuple(instance.sources)!=tuple(e['sources'])
        instance.assembly,instance.sources=e['assembly'],e['sources']
        if viewer is not None:
            if rebuild:self._rebuild(viewer)
            else:self._refresh()

    def show_zippo(self,position,yaw,frames,loop,origin=(0.0,0.0,0.0),sim_yaw=0):
        """Zippo (area file 16) at `position`, game yaw `yaw`, looping."""
        viewer=self.viewer();scene=getattr(viewer,'scene',None)
        if not self._shown() or position is None or not frames or not hasattr(scene,'build'):return False
        from game.actor_sim import Posed
        from copy import copy
        self._save_base()
        z=getattr(self,'zippo',None)
        # The frames stand Zippo where the emulator did: pose them about that.
        posed=Posed('Zippo','Event simulation',frames[0],[],np.array(origin,dtype=float),sim_yaw)
        if z is None:
            zippo=copy(self.editor.instance)
            zippo.label='Zippo (event simulation)';zippo.placement=None;zippo.pickup=None
            zippo.flip=None;zippo.flip_frames=();zippo.follow=None;zippo.offsets=();zippo.parts=[];zippo.spans=[]
            zippo.assembly=posed;zippo.sources=posed.sources
            zippo.index=len(scene.instances);scene.instances.append(zippo)
            self.zippo=z=dict(instance=zippo)
            zippo.game_position=tuple(float(v) for v in position);zippo.angle=yaw*360/4096
            self._rebuild(viewer)
        zippo=z['instance'];zippo.assembly=posed
        zippo.game_position=tuple(float(v) for v in position);zippo.angle=yaw*360/4096
        z.update(frames=frames,loop=loop,tick=0,posed=posed)
        viewer.refresh_instance(zippo.index)
        self._anim_timer();return True

    def hide_zippo(self):
        z=getattr(self,'zippo',None)
        if z is None:return
        self.zippo=None
        viewer=self.viewer();scene=getattr(viewer,'scene',None)
        if scene is not None and z['instance'] in scene.instances:
            scene.instances.remove(z['instance']);self._rebuild(viewer)

    def _anim_tick(self):
        busy=False
        for which in ('entity','zippo'):
            a=getattr(self,which,None)
            if not a or 'frames' not in a:continue
            frames=a['frames']
            if a['tick']+1<len(frames):a['tick']+=1
            elif a['loop'] is not None:a['tick']=a['loop']
            else:continue
            busy=True
            a['posed'].pieces=list(frames[a['tick']])
            index=self.editor.instance.index if which=='entity' else a['instance'].index
            viewer=self.viewer()
            try:
                if hasattr(viewer,'refresh_moving'):viewer.refresh_moving([index])
                else:viewer.refresh_instance(index)
            except Exception:pass
        if not busy and getattr(self,'anim_timer',None) is not None:self.anim_timer.stop()

    def _restore_camera(self):
        if self.base is None or self.base[1] is None:return
        c=self.viewer().camera_controls;c.stop_glide()
        (c.camera_x,c.camera_y,c.camera_z,c.camera_angle_h,c.camera_angle_v,c.orbit_distance)=self.base[1]
        self.viewer().update()

    # --- timeline ---

    def set_timeline(self,on):
        self.timeline=on
        if not on:self.reset()
    def show_state(self,position,yaw):
        """Timeline: stand the entity where the events so far leave it."""
        if not self._shown() or position is None:return False
        self._save_base()
        self._place(position,yaw)
        return True
    def show_camera(self,path,animate):
        """Run a recorded camera path (or jump to its end)."""
        if getattr(self.viewer(),'camera_controls',None) is None or not path:return False
        self._save_base()
        if not animate or len(path)<2:
            self._aim(*path[-1]);return True
        self.track=('path',path,not self.timeline)
        self.clock=None;self._start()
        return True
    def restore_camera(self):
        """Timeline rows before any camera command: the camera as it was."""
        if self.base is not None:self._restore_camera()

    # --- one row ---

    def play(self,nodes,row,tomba=None):
        """Start the selected row's preview; False if it has none here."""
        from game.event_preview import trace
        if self.timeline:self.stop(keep=True)
        else:self.reset()
        if not self._shown():return False
        start=self.editor.start_position()
        if start is None:return False
        c=nodes[row]
        if c.opcode in (31,58,59):
            entries=self.editor.turn_entries(nodes,row)
            if entries:
                # Whoever turns does so by the command's step each tick, as the game turns them.
                self._save_base();self.track=('turns',entries)
                self.editor.status.setText('Previewing: '+script.summary(c)+' - a step a tick, as in the game.')
                self._start();return True
        steps=[s for s in trace(nodes,row,start) if s[0]==row]
        if not steps:return False
        _,kind,a,b,ticks=steps[-1]
        import numpy as np
        if kind not in ('move','set'):return False
        seconds=0.6 if kind=='set' else min(6.0,max(0.4,(ticks or np.linalg.norm(np.array(b)-np.array(a))/10)/self.FPS))
        self._save_base()
        yaw=self.editor.state_at(nodes,row)[1]
        self.track=('move',np.array(a,dtype=float),np.array(b,dtype=float),seconds,0.8,yaw)
        self.editor.status.setText(f'Previewing in the viewport: {kind} over {seconds:.2f} s'
                                   +('.' if self.timeline else ', then back. Nothing is written.'))
        self._start();return True
    def _start(self):
        from PyQt6.QtCore import QElapsedTimer
        self.clock=QElapsedTimer();self.clock.start();self.timer.start()
    def step(self):
        viewer=self.viewer()
        if viewer is None or self.track is None:self.stop();return
        t=self.clock.elapsed()/1000
        kind=self.track[0]
        if kind=='move':
            _,a,b,seconds,hold,yaw=self.track
            f=min(1.0,t/seconds) if seconds else 1.0
            self._place(a+(b-a)*f,yaw if f>=1.0 else None)
            if t>=seconds+(0 if self.timeline else hold):self.stop()
        elif kind=='turn':
            position,yaw,target=self.track[1:4];per_tick=self.track[4] if len(self.track)>4 else 0x100
            turned=min(int(t*self.FPS)*per_tick,0x1000)
            gap=(target-yaw)&0xfff
            if gap>=0x800:gap-=0x1000
            now=target if abs(gap)<=turned else (yaw+(turned if gap>0 else -turned))&0xfff
            self._place(position,now)
            if now==target and t>=(abs(gap)/per_tick)/self.FPS+(0 if self.timeline else 0.8):self.stop()
        elif kind=='turns':
            tick=int(t*self.FPS);done=True;longest=0
            for who,start,target,step,position in self.track[1]:
                gap=(target-start)&0xfff
                if gap>=0x800:gap-=0x1000
                if step<1 or abs(gap)<=step*tick:now=target
                else:now=(start+(step*tick if gap>0 else -step*tick))&0xfff;done=False
                self._apply_yaw(who,now,position)
                longest=max(longest,0 if step<1 else abs(gap)/step)
            if done and t>=longest/self.FPS+(0 if self.timeline else 0.8):self.stop()
        elif kind=='path':
            _,path,restore=self.track
            n=min(int(t*self.FPS),len(path)-1)
            self._aim(*path[n])
            if n>=len(path)-1 and t>=len(path)/self.FPS+(0.8 if restore else 0):self.stop()
    def _apply_yaw(self,who,yaw,position):
        """Turn the entity, Zippo or Tomba stand-in to game yaw `yaw`."""
        if who=='self':
            if position is not None:self._place(position,yaw)
        elif who=='tomba':
            if self.tomba is not None:self.tomba['yaw']=yaw;self._draw_tomba()
        elif who=='zippo':
            z=getattr(self,'zippo',None);viewer=self.viewer()
            if z is not None and viewer is not None:
                z['instance'].angle=yaw*360/4096;viewer.refresh_instance(z['instance'].index)
    def stop(self,keep=False):
        """End the running preview. Outside the timeline the entity and
        camera go back at once; in it they stay where the events put them."""
        self.timer.stop();self.track=None
        if not self.timeline and not keep:self.reset()
    def reset(self):
        """Put back the entity and camera as they were before any preview."""
        self.timer.stop();self.track=None
        if self.base is None:return
        viewer=self.viewer()
        (x,y,z,angle),_=self.base
        instance=self.editor.instance
        if x is not None:instance.x,instance.y,instance.z=x,y,z
        if angle is not None:instance.angle=angle
        self.stop_entity_animation();self.hide_zippo()
        if getattr(self,'anim_timer',None) is not None:self.anim_timer.stop()
        if viewer is not None:
            try:viewer.refresh_instance(instance.index)
            except Exception:pass
            # The camera is not put back: where the events (or the user)
            # panned it is where it stays.
        self.base=None;self.base_game=None
        self.hide_tomba()

    # --- stand-in Tomba ---

    def show_tomba(self,position,yaw,result,lift=0.0):
        """Tomba, textured, at `position` (game axes) facing `yaw`, playing
        `result` (event_preview.tomba_frames) and looping where it loops.
        He is a temporary instance of the level scene - the area's own
        Tomba model (IDX trail resource 0) - removed again by reset()."""
        viewer=self.viewer();scene=getattr(viewer,'scene',None)
        if not self._shown() or position is None or not result or not hasattr(scene,'build'):return False
        self._save_base()
        same=self.tomba is not None and self.tomba['result'] is result
        tick=self.tomba['tick'] if same else 0
        if self.tomba is None:
            self.tomba=dict(instance=self._add_tomba(scene,result))
            self._rebuild(viewer)
        elif self.tomba['result'] is not result and self.tomba['result']['by_bone']!=result['by_bone']:
            # Another head or hands: different geometry.
            self.tomba['instance'].sources=tuple((TOMBA_MODEL,g) for g in result['by_bone'])
            self._rebuild(viewer)
        self.tomba.update(position=position,yaw=yaw,result=result,tick=tick,lift=lift)
        self._draw_tomba()
        if len(result['frames'])>1:self.tomba_timer.start()
        else:self.tomba_timer.stop()
        return True
    def _register_tomba(self,scene):
        """Tomba's own SMST (System chunk trail resource 0, as tomba_frames
        poses it) under TOMBA_MODEL - not the area's trail 0, which in most
        areas is some NPC or nothing."""
        if scene.models.get(TOMBA_MODEL) is not None:return
        from formats.models.smst_parser import parse_smst
        idx=getattr(scene,'idx_path',str(Path(scene.dat_path).with_name('TOMBA2.IDX')))
        trail=actor_sim.World._trail(idx,1)
        with open(scene.dat_path,'rb') as source:
            source.seek(trail[0]);raw=source.read(trail[1]-trail[0])
        scene.models[TOMBA_MODEL]=parse_smst(raw,address=trail[0])

    def _add_tomba(self,scene,result):
        from copy import copy
        tomba=copy(self.editor.instance)
        class Assembly:
            name='Tomba (event simulation)';note='Event editor stand-in';blends=();riders=();owners=()
            pieces_now=[]
            def pose(self,_state):return self.pieces_now
        self._register_tomba(scene)
        tomba.assembly=Assembly();tomba.sources=tuple((TOMBA_MODEL,g) for g in result['by_bone'])
        tomba.offsets=();tomba.parts=[];tomba.spans=[];tomba.label='Tomba (event simulation)'
        tomba.placement=None;tomba.pickup=None;tomba.flip=None;tomba.flip_frames=();tomba.follow=None
        tomba.index=len(scene.instances)
        scene.instances.append(tomba)
        return tomba
    def _rebuild(self,viewer):
        """New geometry, same view: selection, hidden rows and camera stay."""
        viewer.scene.__dict__.pop('_built_model',None)
        viewer.model_data=viewer.scene.build()
        viewer.prepare_buffers();viewer.rebuild_markers();viewer._build_selection();viewer.update()
    def hide_tomba(self):
        self.tomba_timer.stop()
        if self.tomba is None:return
        tomba=self.tomba['instance'];self.tomba=None
        viewer=self.viewer();scene=getattr(viewer,'scene',None)
        if scene is not None and tomba in scene.instances:
            scene.instances.remove(tomba)
            self._rebuild(viewer)
    def _draw_tomba(self):
        from game.event_preview import tomba_pieces
        t=self.tomba
        t['instance'].assembly.pieces_now=tomba_pieces(t['result'],t['tick'],t['position'],t['yaw'],t['lift'])
        viewer=self.viewer()
        if hasattr(viewer,'refresh_moving'):viewer.refresh_moving([t['instance'].index])
        else:viewer.refresh_instance(t['instance'].index)
    def _tomba_tick(self):
        t=self.tomba
        if t is None:self.tomba_timer.stop();return
        frames=len(t['result']['frames']);loop=t['result']['loop']
        if t['tick']+1<frames:t['tick']+=1
        elif loop is not None:t['tick']=loop
        else:self.tomba_timer.stop();return
        self._draw_tomba()


class EventEditor(QDialog):
    _SLOT_KEPT={}            # (handler, slot, area file) -> loader calls of the routines that slot runs
    _CONDITIONS_KEPT={}      # (handler, area file) -> the tests on the way to each page, kept between openings
    def __init__(self, panel, instance, handler=None):
        super().__init__(panel)
        self.panel,self.instance=panel,instance
        self.setWindowTitle('Event Editor - '+instance.label)
        self.setModal(False);self.setWindowFlag(Qt.WindowType.Window,True)
        self.resize(1320,840)
        self.exe=Path(panel.exe_path).read_bytes()
        self.overlay=Path(panel.scene.overlay_path).read_bytes()
        script.load_names(self.exe,panel.exe_path)
        handler=handler or script.handler_for(panel.scene,instance)
        self.native_handler=handler
        self.bindings=script.discover(self.exe,self.overlay,handler) if handler else []
        self.bindings+=self._conversation_bindings(self.bindings)
        self.originals={}
        self.drafts={}
        self.active=None;self.selected=None
        self.bank=None;self.bank_address=None;self.linked_entry=None;self.voice_job=None
        self.items=None
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio)
        # RPG Maker 2003 event editor layout: Name + page buttons, page
        # tabs, settings on the left, "@>" Contents on the right.
        style=self.style();icon=QStyle.StandardPixmap
        outer=QVBoxLayout(self);outer.setContentsMargins(6,6,6,6);outer.setSpacing(4)
        top=QHBoxLayout();top.setSpacing(6)
        name_box=QGroupBox('Name');name_layout=QVBoxLayout(name_box);name_layout.setContentsMargins(6,2,6,6)
        self.name_field=QLineEdit(instance.label);self.name_field.setReadOnly(True);self.name_field.setFixedWidth(240)
        name_layout.addWidget(self.name_field);top.addWidget(name_box)
        def big(text,pixmap,tip):
            b=QPushButton(text);b.setIcon(style.standardIcon(pixmap));b.setMinimumSize(140,30);b.setToolTip(tip)
            top.addWidget(b,0,Qt.AlignmentFlag.AlignBottom);return b
        self.add=big('Insert Command…',icon.SP_FileDialogNewFolder,'Insert a command before the selected one (Ins).')
        self.add.clicked.connect(lambda:self.insert())
        self.revert=big('Revert Page',icon.SP_BrowserReload,'Put this page back as it was when the editor opened.')
        self.revert.clicked.connect(self.revert_page)
        self.split_all=big('Separate All',icon.SP_ArrowRight,'Separate every text + gesture action on this page that can be separated exactly.')
        self.split_all.clicked.connect(self.split_page)
        self.undo_button=big('Undo',icon.SP_ArrowBack,'Undo the last event edit (Ctrl+Z).');self.undo_button.clicked.connect(self.undo)
        self.redo_button=big('Redo',icon.SP_ArrowForward,'Redo (Ctrl+Y or Ctrl+Shift+Z).');self.redo_button.clicked.connect(self.redo)
        top.addStretch();outer.addLayout(top)
        # Pages: the native scripts this entity's code loads.
        self.pages=QTabBar();self.pages.setExpanding(False);self.pages.setDrawBase(False)
        self.pages.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.pages.customContextMenuRequested.connect(self.page_tab_menu)
        for n,binding in enumerate(self.bindings):self._add_page_tab(n,binding)
        if not self.bindings:
            self.pages.addTab('Handler');self.pages.setTabToolTip(0,'No event script; behaviour is native handler code.')
        # Tabs flush on a raised pane, as a tab widget draws them.
        paged=QVBoxLayout();paged.setSpacing(0);paged.addWidget(self.pages)
        page_frame=QFrame();page_frame.setFrameShape(QFrame.Shape.Panel);page_frame.setFrameShadow(QFrame.Shadow.Raised)
        page_frame.setLineWidth(2);paged.addWidget(page_frame,1);outer.addLayout(paged,1)
        body=QHBoxLayout(page_frame);body.setContentsMargins(6,6,6,6);body.setSpacing(6)
        left_widget=QWidget();left_widget.setFixedWidth(360);left=QVBoxLayout(left_widget);left.setContentsMargins(0,0,0,0)
        body.addWidget(left_widget)
        # Page: where the script lives.
        page_box=QGroupBox('Page');page_form=QFormLayout(page_box)
        self.page_fields={}
        for key,label in (('script','Script'),('caller','Loaded at'),('count','Commands'),('handler','Handler'),('music','Music')):
            field=QLineEdit();field.setReadOnly(True);self.page_fields[key]=field;page_form.addRow(label,field)
        self.talk_filter=QComboBox();self.talk_filter.setToolTip('Show only the pages that speak from one dialogue (a shared handler '
            'serves several NPCs, each with its own dialogue). Pages that show no text stay.')
        self.talk_filter.currentIndexChanged.connect(lambda _=0:self._apply_page_filter())
        page_form.addRow('Talks from',self.talk_filter)
        self.show_others=QCheckBox('Show hidden pages');self.show_others.toggled.connect(self._apply_page_filter)
        self.show_others.setToolTip('A handler shared by several NPCs can start pages meant for the others; they are told apart by '
                                    'the scene record’s variant. Tick to show those pages too.')
        page_form.addRow('',self.show_others)
        self.logic_button=QPushButton('Show as If / Then…');self.logic_button.clicked.connect(self.show_logic)
        self.logic_button.setToolTip('The tests this NPC’s code makes before it starts each page, as a tree.')
        page_form.addRow('',self.logic_button)
        self.music_change=QPushButton('Change music…');self.music_change.clicked.connect(self.change_music)
        self.music_change.setToolTip('Pick the conversation-music cue this NPC’s code starts. Shared by NPCs with the same handler.')
        page_form.addRow('',self.music_change)
        left.addWidget(page_box)
        # RPG Maker's Graphic and Conditions, side by side as there.
        looks=QHBoxLayout();looks.setSpacing(6)
        graphic_box=QGroupBox('Graphic');graphic=QVBoxLayout(graphic_box);graphic.setContentsMargins(4,4,4,4)
        from formats.models.smst_viewer import SMSTViewer
        self.avatar=SMSTViewer(graphic_box);self.avatar.spread=False;self.avatar.spread_action.setVisible(False)
        for extra in (self.avatar.toolbar,self.avatar.stats_label,self.avatar.controls_label):extra.hide()
        self.avatar.setFixedSize(112,112);graphic.addWidget(self.avatar)
        looks.addWidget(graphic_box)
        conditions_box=QGroupBox('Conditions');conditions=QVBoxLayout(conditions_box);conditions.setContentsMargins(4,4,4,4)
        self.conditions=QListWidget();self.conditions.setWordWrap(True)
        self.conditions.setToolTip('What the native handler tests before it starts this page - read from its code. '
                                   '“?” is a value set on another path.')
        conditions.addWidget(self.conditions);looks.addWidget(conditions_box,1)
        left.addLayout(looks)
        # Command: the selected row's parameters and preview.
        command_box=QGroupBox('Command');command=QVBoxLayout(command_box)
        self.flow=QLabel();self.flow.setWordWrap(True);command.addWidget(self.flow)
        self.form_widget=QWidget();self.form=QFormLayout(self.form_widget);self.form.setContentsMargins(0,0,0,0)
        command.addWidget(self.form_widget)
        self.raw=QCheckBox('Edit raw parameters of undecoded actions')
        self.raw.toggled.connect(lambda _:self.select_action(self.actions.currentRow()))
        command.addWidget(self.raw)
        self.fields=[]
        play_row=QHBoxLayout()
        self.play_button=QPushButton('Play');self.play_button.setIcon(style.standardIcon(icon.SP_MediaPlay))
        self.play_button.setToolTip('Preview the selected action again: moves and camera in the level viewport, text, voice, sound and animation here.')
        self.play_button.clicked.connect(lambda:self.play_selected(manual=True))
        self.autoplay=QCheckBox('Autoplay');self.autoplay.setChecked(True)
        self.autoplay.setToolTip('Preview each action as soon as it is selected.')
        self.timeline_box=QCheckBox('Simulation preview')
        self.timeline_box.setToolTip('Timeline: selecting a row leaves the entity and camera as the events up to that row '
                                     'leave them. Closing the editor puts everything back.')
        self.timeline_box.toggled.connect(self.timeline_toggled)
        play_row.addWidget(self.play_button);play_row.addWidget(self.autoplay);play_row.addWidget(self.timeline_box);play_row.addStretch()
        command.addLayout(play_row)
        edit_grid=QGridLayout();edit_grid.setSpacing(4)
        self.add_after=QPushButton('Insert After…');self.add_after.clicked.connect(self.insert_after)
        self.remove=QPushButton('Delete');self.remove.clicked.connect(self.delete)
        self.up=QPushButton('Move Up');self.up.clicked.connect(lambda:self.move(-1))
        self.down=QPushButton('Move Down');self.down.clicked.connect(lambda:self.move(1))
        self.edit_button=QPushButton('Edit…');self.edit_button.clicked.connect(self.edit_row)
        self.edit_button.setToolTip('Edit the command: its parameters and previews (double-click or Space).')
        for n,b in enumerate((self.edit_button,self.add_after,self.remove,self.up,self.down)):edit_grid.addWidget(b,n//2,n%2)
        command.addLayout(edit_grid)
        left.addWidget(command_box);left.addStretch()
        # Contents and the previews that follow the selection.
        right=QSplitter(Qt.Orientation.Vertical);body.addWidget(right,1)
        contents_box=QGroupBox('Contents');contents=QVBoxLayout(contents_box);contents.setContentsMargins(4,4,4,4)
        self.actions=QListWidget()
        self.actions.setFont(QFont('Verdana',9) if theme.is_grey() else QFont('Consolas',9))
        self.actions.setItemDelegate(RowDelegate(self.actions))
        self.actions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.actions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.actions.customContextMenuRequested.connect(self.row_menu)
        self.actions.itemDoubleClicked.connect(lambda *_:self.edit_row())
        contents.addWidget(self.actions);right.addWidget(contents_box)
        preview_box=QGroupBox('Preview');preview_layout=QVBoxLayout(preview_box);preview_layout.setContentsMargins(4,4,4,4)
        self.preview_tabs=QTabWidget();preview_layout.addWidget(self.preview_tabs)
        # Editing happens in a popup (CommandDialog): the parameters and
        # previews live there, made now so the GL views are never re-hosted.
        self.command_dialog=CommandDialog(self)
        self.command_dialog.body.addWidget(preview_box,1)
        self.command_dialog.body.insertWidget(0,self.form_widget)
        self.command_dialog.body.insertWidget(1,self.raw)
        text_tab=QWidget();text_layout=QVBoxLayout(text_tab)
        self.preview_tabs.addTab(text_tab,'Dialogue / voice')
        self.model_tab=QWidget();self.model_layout=QVBoxLayout(self.model_tab)
        self.preview_model_button=QPushButton('Preview selected entity / action')
        self.preview_model_button.clicked.connect(self.preview_model_action)
        self.tomba_button=QPushButton('Play on Tomba');self.tomba_button.clicked.connect(self.preview_tomba_animation)
        self.tomba_button.setToolTip('Tomba’s animation from this command, on Tomba’s model.')
        model_buttons=QHBoxLayout();model_buttons.addWidget(self.preview_model_button,1);model_buttons.addWidget(self.tomba_button,1)
        self.model_layout.addLayout(model_buttons)
        self.model_note=QLabel('Model previews run only when requested.');self.model_note.setWordWrap(True)
        self.model_layout.addWidget(self.model_note)
        self.sprite_preview=QLabel();self.sprite_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.model_layout.addWidget(self.sprite_preview)
        # Made now, not on the first preview: a GL widget added to a window
        # already on screen makes Qt recreate the native window, which drops
        # it back where a new window opens.
        from formats.models.smst_viewer import SMSTViewer
        self.model_viewer=SMSTViewer(self.model_tab);self.model_viewer.spread=False
        self.model_viewer.spread_action.setVisible(False);self._vram_set=False
        self.model_layout.addWidget(self.model_viewer,1)
        self.preview_tabs.addTab(self.model_tab,'Model / animation')
        # Animation on the left, the line and its voice on the right.
        self.preview_tabs.removeTab(self.preview_tabs.indexOf(self.model_tab))
        self.preview_tabs.removeTab(self.preview_tabs.indexOf(text_tab))
        self.both_tab=QSplitter(Qt.Orientation.Horizontal)
        self.both_tab.addWidget(self.model_tab);self.both_tab.addWidget(text_tab);self.both_tab.setSizes([520,460])
        # A page taken out of a tab widget stays hidden; show both in the splitter.
        self.model_tab.show();text_tab.show()
        self.preview_tabs.addTab(self.both_tab,'Model and dialogue')
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
        self.code_undo.clicked.connect(self.code_undo_step);self.code_redo.clicked.connect(self.code_redo_step)
        code_layout.addWidget(self.code_preview,1)
        # Editing: a table where only each instruction's number can change.
        from PyQt6.QtWidgets import QTableWidget,QHeaderView,QAbstractItemView
        self.code_table=QTableWidget(0,3);self.code_table.setHorizontalHeaderLabels(['Address','Instruction','Value'])
        self.code_table.setFont(QFont('Consolas',9));self.code_table.verticalHeader().setVisible(False)
        self.code_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.code_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked|QAbstractItemView.EditTrigger.EditKeyPressed
                                        |QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.code_table.itemChanged.connect(self._code_cell_changed)
        self.code_table.hide();code_layout.addWidget(self.code_table,1)
        self._code_rows=[];self._code_done=[];self._code_undone=[];self._code_replaying=False;self._code_last={}
        self.code_tab=code_tab
        self.preview_tabs.addTab(code_tab,'Native code')
        for b in (self.code_save,self.code_undo,self.code_redo,self.code_discard):b.setEnabled(False)
        self.animation_job=None;self.animation_frames=[];self.animation_tick=0
        self.animation_loop=None;self.animation_kind='actor';self.tomba_job=None;self.camera_job=None;self._tomba_cache={}
        self._field_timer=QTimer(self);self._field_timer.setSingleShot(True);self._field_timer.setInterval(300)
        self._field_timer.timeout.connect(self.refresh_preview)
        self.preview_generation=0
        self.animation_timer=QTimer(self);self.animation_timer.setInterval(33)
        self.animation_timer.timeout.connect(self.advance_animation)
        self.text_choices=QComboBox();self.text_choices.currentIndexChanged.connect(self.choose_text)
        text_layout.addWidget(self.text_choices)
        text_buttons=QHBoxLayout()
        self.pick_text=QPushButton('Choose dialogue…');self.pick_text.clicked.connect(self.choose_for_row)
        self.pick_text.setToolTip('Pick the dialogue master and entry this action shows, from the area’s TXTD.')
        self.goto_txtd=QPushButton('Go to TXTD in IDX');self.goto_txtd.clicked.connect(self.go_to_txtd)
        self.goto_txtd.setToolTip('Open this area’s dialogue file in the Indexed View, on this entry.')
        self.split_text=QPushButton('Separate text and animation')
        self.split_text.setToolTip('Turn this action into Play animation, Show text and Play animation -1 (back to rest) - the same behaviour as separate blocks.')
        self.split_text.clicked.connect(self.split_presentation)
        self.music_button=QPushButton('Play music cue');self.music_button.setIcon(style.standardIcon(icon.SP_MediaPlay))
        self.music_button.clicked.connect(self.play_music)
        for b in (self.pick_text,self.goto_txtd,self.split_text,self.music_button):text_buttons.addWidget(b)
        text_buttons.addStretch();text_layout.addLayout(text_buttons)
        self.pick_text.setEnabled(False);self.goto_txtd.setEnabled(False)
        self.split_text.setEnabled(False);self.music_button.setEnabled(False)
        self.text_preview=QPlainTextEdit()
        self.text_preview.setPlaceholderText('Select a conversation or message action to see its text.')
        self.text_preview.setReadOnly(True)
        self.text_preview.setToolTip('The line this command shows. Edit it here; OK writes it.')
        text_layout.addWidget(self.text_preview,1)
        voice_row=QHBoxLayout()
        self.play_voice=QPushButton('Play linked voice');self.play_voice.clicked.connect(self.decode_voice)
        self.sound_button=QPushButton('Play action sound');self.sound_button.clicked.connect(self.play_sound)
        voice_row.addWidget(self.play_voice);voice_row.addWidget(self.sound_button);voice_row.addStretch()
        text_layout.addLayout(voice_row)
        self.play_voice.setEnabled(False);self.sound_button.setEnabled(False)
        # Event Command choices: what Insert offers, labelled as before.
        names={0:'Show text…',1:'Show text + gesture…',11:'Start conversation music',
               36:'This actor: move to a position',35:'This actor: move along an axis',17:'This actor: set position',
               31:'This actor: turn to an angle'}
        self.command_choices=[(names.get(op,script.LABELS[op]),op) for op in script.INSERTABLE if op not in script.MOVE_TURN_OPS]
        self.command_choices+=[(label,(op,tuple(values))) for label,op,values in script.INSERT_VARIANTS if op not in script.MOVE_TURN_OPS]
        self.command_choices+=[('Move / Turn…','Move / Turn…')]
        self.command_choices+=[('If event state…','If event state…'),('If area progress…','If area progress…')]
        from PyQt6.QtGui import QKeySequence,QShortcut
        self._undo,self._redo,self._text_session=[],[],None
        for keys,slot in (('Ctrl+Z',self.undo),('Ctrl+Y',self.redo),('Ctrl+Shift+Z',self.redo)):
            shortcut=QShortcut(QKeySequence(keys),self);shortcut.activated.connect(slot)
        for keys,slot in (('Ins',lambda:self.insert()),('Del',self.delete_if_allowed),('Space',self.edit_row),
                          ('Ctrl+Up',lambda:self.move_if_allowed(-1)),('Ctrl+Down',lambda:self.move_if_allowed(1))):
            shortcut=QShortcut(QKeySequence(keys),self.actions);shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)
        # Bottom: shared-write switch, status, OK / Cancel / Apply / Help.
        bottom=QHBoxLayout()
        self.shared=QCheckBox('Apply shared script binding and dialogue-entry changes')
        self.shared.setToolTip('This edits the selected native call site. Other actors that use it receive the same changes.')
        left_bottom=QVBoxLayout();left_bottom.addWidget(self.shared)
        self.status=QLabel('');self.status.setWordWrap(True);left_bottom.addWidget(self.status)
        bottom.addLayout(left_bottom,1)
        self.ok_button=QPushButton('OK');self.ok_button.clicked.connect(self.ok)
        close=QPushButton('Cancel');close.clicked.connect(self.reject)
        self.save=QPushButton('Apply');self.save.clicked.connect(lambda:self.apply(close=False))
        help_button=QPushButton('Help');help_button.clicked.connect(self.show_help)
        for b in (self.ok_button,close,self.save,help_button):
            b.setFixedWidth(88);bottom.addWidget(b,0,Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(bottom)
        self.pages.currentChanged.connect(self.page_changed)
        self.actions.currentRowChanged.connect(self.row_chosen)
        self.viewport=ViewportPreview(self)
        self._entity_frames={};self._zippo_frames={};self._camera_key=None;self.frames_jobs=[]
        self.timeline_box.setChecked(True)
        self.shared.toggled.connect(self.update_enabled)
        self._undo_state()
        self._apply_page_filter(initial=True)
        self.page_changed(self.pages.currentIndex() if self.pages.count() else 0)
        QTimer.singleShot(0,self.show_avatar)

    def _conversation_bindings(self,bindings):
        """Pages for the conversation scripts these pages request
        (Request conversation through handler 0 - A00 only)."""
        read=script.reader(self.exe,self.overlay);pages=[]
        for b in bindings:
            try:pages.append(script.read_graph(read,b.script))
            except ValueError:pass
        area=getattr(self.panel.scene,'area_index',None)
        native=[]
        if self.native_handler==script.AREA_SCRIPT_ACTOR:
            native=list(range(script.CONVERSATION_COUNT.get(area,0)))
        elif self.native_handler and area==0:
            try:native=[r for h,r in script.native_requests(self.exe,self.overlay,self.native_handler) if h==0]
            except (ValueError,IndexError):pass
        found=script.conversations(self.exe,self.overlay,area,pages,native)
        if self.native_handler==script.AREA_SCRIPT_ACTOR:return found
        return [b for b in found if self._conversation_is_near(b,read)]

    NEAR_CONVERSATION=4000

    def _conversation_is_near(self,binding,read):
        """Actors sharing one handler (a town fisherman and the one in his
        house) all request the same conversations; one belongs to the actor
        whose spot its camera looks at. Kept when it names no place."""
        import math
        at=getattr(self.instance,'game_position',None)
        if at is None:return True
        try:nodes=script.read_graph(read,binding.script)
        except ValueError:return True
        spots=[(c.operands[3],c.operands[5]) for c in nodes
               if c.opcode==22 and c.extended and c.operands[2]==0]
        if not spots:return True
        return any(math.hypot(x-at[0],z-at[2])<=self.NEAR_CONVERSATION for x,z in spots)

    def _add_page_tab(self,n,binding):
        if binding.table:
            self.pages.addTab(f'C{binding.request}')
            self.pages.setTabToolTip(n,f'Conversation {binding.request}: script 0x{binding.script:08X}, '
                                     f'from the conversation table slot 0x{binding.caller:08X}. '
                                     'Request conversation (handler 0) runs it.')
        elif getattr(binding,'tests',None):
            self.pages.addTab(f'{n+1}★')
            self.pages.setTabToolTip(n,'A copy that runs only when '+' and '.join(script.test_text(t) for t in binding.tests)+
                                     f'; otherwise the page it came from runs. Script 0x{binding.script:08X}, chosen by a stub in front '
                                     f'of the loader call at 0x{binding.caller:08X}.')
        else:
            self.pages.addTab(str(n+1))
            self.pages.setTabToolTip(n,f'Script 0x{binding.script:08X}, loaded at 0x{binding.caller:08X}')

    def conversation_page(self,request):
        return next((n for n,b in enumerate(self.bindings) if b.table and b.request==request),None)

    def native_music_text(self):
        """The conversation music this NPC's native code starts, if it does."""
        if not self.native_handler:return None
        cached=getattr(self,'_music_text',False)
        if cached is not False:return cached
        try:cues=script.native_music(self.exe,self.overlay,self.native_handler)
        except (ValueError,IndexError):cues=[]
        parts=[]
        for cue in cues:
            sound=script.music_sound(cue)
            slot=script.music_slot(sound) if sound is not None else None
            parts.append(f'cue {cue}'+(f' (sound 0x{sound:02X}, SEQ slot {slot})' if slot is not None else ''))
        self._music_text='Started by native code: '+', '.join(parts) if parts else None
        return self._music_text

    def page_changed(self,index):
        if self.active is not None: self.store_operands()
        self.selected=None
        self.actions.blockSignals(True)
        self.active=index if 0<=index<len(self.bindings) else None
        self.actions.clear()
        self.actions.blockSignals(False)
        binding=self.bindings[self.active] if self.active is not None else None
        for key,value in (('script',binding and f'0x{binding.script:08X}'),
                          ('caller',binding and (f'table slot 0x{binding.caller:08X}' if binding.table else f'0x{binding.caller:08X}')),
                          ('count',None),('handler',self.native_handler and f'0x{self.native_handler:08X}'),('music',self.native_music_text())):
            self.page_fields[key].setText(value or '—')
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
        self.fill_conditions(binding)
        if index not in self.drafts:
            nodes=script.read_graph(script.reader(self.exe,self.overlay),self.bindings[index].script)
            self.originals[index]=deepcopy(nodes);self.drafts[index]=nodes
        self.populate()

    def _conditions_ready(self,result):
        self._conditions_job=None
        self.__dict__.setdefault('_conditions',{}).update(result)
        EventEditor._CONDITIONS_KEPT[(self.native_handler,hash(self.overlay))]=dict(self._conditions)
        self._apply_page_filter()
        if self.status.text().startswith('Working out'):self.status.setText('')

    def _page_conditions(self,binding):
        """The tests that lead to a page, read once per page."""
        cache=self.__dict__.setdefault('_conditions',{})
        if binding.caller not in cache:
            try:cache[binding.caller]=script.page_conditions(self.exe,self.overlay,self.native_handler,binding.caller,
                                                                flow=self.__dict__.get('_flow'))
            except (ValueError,IndexError,KeyError) as exc:cache[binding.caller]=[f'Not read: {exc}']
        found=list(cache[binding.caller])
        for tests in getattr(binding,'overridden',()):
            found.insert(0,script.tests_else_text(list(tests)))
        for test in reversed(getattr(binding,'tests',()) or ()):
            found.insert(0,script.test_text(test))
        return found

    def _hide_key(self):
        variant=getattr(self.panel.scene,'event_variants',{}).get(getattr(self.instance,'index',None))
        return f'eventPages/hidden/{self.native_handler or 0:08X}/{"any" if variant is None else variant}'

    def _user_hidden(self):
        """Pages you hid for this kind of NPC (handler + variant), by script address."""
        import json
        from PyQt6.QtCore import QSettings
        try:return set(json.loads(QSettings('Tomba2Edit','Tomba2Edit').value(self._hide_key(),'[]')))
        except Exception:return set()

    def _save_user_hidden(self,values):
        import json
        from PyQt6.QtCore import QSettings
        QSettings('Tomba2Edit','Tomba2Edit').setValue(self._hide_key(),json.dumps(sorted(values)))

    def page_tab_menu(self,pos):
        n=self.pages.tabAt(pos)
        menu=QMenu(self)
        if n>=0 and n<len(self.bindings) and self.pages.isTabVisible(n):
            hide=menu.addAction('Hide this page for this NPC')
            hide.triggered.connect(lambda _=False,i=n:self._hide_page(i))
        if 0<=n<len(self.bindings):
            binding=self.bindings[n]
            if getattr(binding,'tests',None):
                menu.addSeparator()
                gone=menu.addAction('Remove this conditional page (back to the page it came from)')
                gone.triggered.connect(lambda _=False,i=n:self._remove_specific(i))
            elif not binding.table:
                menu.addSeparator()
                make=menu.addAction('Add a copy of this page that runs only if…')
                make.triggered.connect(lambda _=False,i=n:self._make_specific(i))
        mine=self._user_hidden()
        restore=menu.addAction(f'Show the pages I hid again ({len(mine)})');restore.setEnabled(bool(mine))
        restore.triggered.connect(self._restore_pages)
        menu.addAction('Show hidden pages').setCheckable(True)
        menu.actions()[-1].setChecked(self.show_others.isChecked())
        menu.actions()[-1].triggered.connect(lambda on:self.show_others.setChecked(bool(on)))
        menu.exec(self.pages.mapToGlobal(pos))

    def show_logic(self):
        if not self.bindings:return
        dialog=LogicDialog(self)
        if dialog.exec() and dialog.choice is not None:
            n=dialog.choice
            if not self.pages.isTabVisible(n):self.show_others.setChecked(True)
            self.pages.setCurrentIndex(n)

    def _unsaved_page_edits(self):
        return any(nodes!=self.originals.get(page,nodes) for page,nodes in self.drafts.items())

    def _write_overlay(self,data):
        """Write a changed area file through the Level Editor's project files."""
        path=self.panel.scene.overlay_path
        if Path(path).read_bytes()!=self.overlay:raise ValueError('The area file changed. Close and reopen Events first.')
        self.panel._write_placement_files({path:data})
        self.panel._placement_files[Path(path).name]=data
        self.panel.scene.overlay_data=data
        self.panel._scene_memory_cache.clear()

    def _make_specific(self,n):
        """Add a copy of a page that runs only when conditions hold; the page it
        copies stays as the Else."""
        binding=self.bindings[n]
        variant=getattr(self.panel.scene,'event_variants',{}).get(getattr(self.instance,'index',None))
        if not self.shared.isChecked():
            QMessageBox.information(self,'Conditional page','Tick “Apply shared script binding and dialogue-entry changes” first: '
                                    'this changes the NPC’s native code.');return
        if self._unsaved_page_edits():
            QMessageBox.information(self,'Conditional page','Apply or revert your page edits first.');return
        dialog=PageConditionDialog(self,variant)
        if not dialog.exec():return
        tests=dialog.tests
        if not tests:
            QMessageBox.information(self,'Conditional page','Add at least one condition.');return
        try:
            nodes=self.drafts.get(n) or script.read_graph(script.reader(self.exe,self.overlay),binding.script)
            data=script.specialise(self.exe,self.overlay,binding,deepcopy(nodes),tests)
            self._write_overlay(data)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Conditional page',str(exc));return
        self.reload_pages(data)
        mine=next((i for i,b in enumerate(self.bindings) if b.tests and b.caller==binding.caller
                   and script._same_tests(list(b.tests),tests)),None)
        if mine is not None:
            if not self.pages.isTabVisible(mine):self.show_others.setChecked(True)
            self.pages.setCurrentIndex(mine)
        self.status.setText('Added a copy that runs when '+' and '.join(script.test_text(t) for t in tests)+
                            f': tab {mine+1 if mine is not None else "?"} ★. The page it came from is the Else.')

    def _remove_specific(self,n):
        binding=self.bindings[n]
        if self._unsaved_page_edits():
            QMessageBox.information(self,'NPC-specific page','Apply or revert your page edits first.');return
        if not self.shared.isChecked():
            QMessageBox.information(self,'NPC-specific page','Tick “Apply shared script binding and dialogue-entry changes” first.');return
        if QMessageBox.question(self,'Conditional page','Remove this page? The NPC goes back to the page it came from, and the copy is deleted.'
                                )!=QMessageBox.StandardButton.Yes:return
        try:
            data=script.unspecialise(self.exe,self.overlay,binding);self._write_overlay(data)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'NPC-specific page',str(exc));return
        self.reload_pages(data)
        self.status.setText('The conditional page is removed.')

    def _hide_page(self,n):
        """Take a page off this NPC's tabs. Only what the editor shows changes;
        the game still runs the page for whoever its code gives it to."""
        mine=self._user_hidden();mine.add(f'{self.bindings[n].caller:08X}:{self.bindings[n].script:08X}');self._save_user_hidden(mine)
        self._apply_page_filter()
        self.status.setText('Page hidden for this NPC (right-click a tab → “Show the pages I hid again” to bring it back). '
                            'The game itself is unchanged.')

    def _restore_pages(self):
        self._save_user_hidden(set());self._apply_page_filter()
        self.status.setText('The pages you hid are shown again.')

    def _page_masters(self):
        """{page index: the dialogue masters its Show text rows speak from}."""
        if '_masters' not in self.__dict__ or self.__dict__.get('_masters_for') is not self.bindings:
            read=script.reader(self.exe,self.overlay);found={}
            for n,binding in enumerate(self.bindings):
                try:rows=script.read_graph(read,binding.script)
                except ValueError:rows=[]
                found[n]={c.operands[0]&0xfff for c in rows if c.opcode in (0,1) and c.operands[1]>=0 or c.opcode in (0,1)}
            self._masters=found;self._masters_for=self.bindings
        return self._masters

    def _fill_talk_filter(self,masters):
        """The "Talks from" choices (once per set of pages); the master chosen, or None."""
        wanted=sorted({m for found in masters.values() for m in found})
        have=[self.talk_filter.itemData(i) for i in range(1,self.talk_filter.count())]
        if have!=wanted or self.talk_filter.count()==0:
            keep=self.talk_filter.currentData()
            self.talk_filter.blockSignals(True);self.talk_filter.clear();self.talk_filter.addItem('any dialogue',None)
            for m in wanted:
                pages=sum(1 for found in masters.values() if m in found)
                self.talk_filter.addItem(f'dialogue {m} ({pages} page{"s" if pages!=1 else ""})',m)
            i=self.talk_filter.findData(keep);self.talk_filter.setCurrentIndex(max(i,0));self.talk_filter.blockSignals(False)
        return self.talk_filter.currentData()

    def _apply_page_filter(self,*_args,initial=False):
        """Hide the pages a shared handler keeps for other NPCs (their variant
        test fails for this NPC's scene record), unless asked to show them."""
        variant=getattr(self.panel.scene,'event_variants',{}).get(getattr(self.instance,'index',None))
        hidden=set();self._by_hand=set()
        if variant is not None and self.native_handler and self.bindings:
            # Run the handler's own dispatch for this NPC's slot: the routines it hands the NPC to
            # are the ones whose pages are its.
            key=(self.native_handler,variant,hash(self.overlay))
            if key not in EventEditor._SLOT_KEPT:
                try:EventEditor._SLOT_KEPT[key]=script.pages_for_slot(self.exe,self.overlay,self.native_handler,variant)
                except Exception:EventEditor._SLOT_KEPT[key]=None
            own=EventEditor._SLOT_KEPT[key]
            if own:
                hidden|={n for n,b in enumerate(self.bindings) if not b.table and b.caller not in own}
            cache=self.__dict__.setdefault('_conditions',{})
            kept=EventEditor._CONDITIONS_KEPT.get((self.native_handler,hash(self.overlay)))
            if kept:cache.update(kept)
            missing=[b.caller for b in self.bindings if not b.table and b.caller not in cache]
            if missing and not self.__dict__.get('_conditions_job'):
                job=self._conditions_job=ConditionsDecode(self.exe,self.overlay,self.native_handler,sorted(set(missing)),self)
                job.decoded.connect(self._conditions_ready);job.start()
                self.status.setText('Working out which pages belong to this NPC…')
            if not missing:
                for n,binding in enumerate(self.bindings):
                    if binding.table:continue
                    if not script.variant_allows(self._page_conditions(binding),variant):hidden.add(n)
        mine=self._user_hidden()
        for n,binding in enumerate(self.bindings):
            if f'{binding.caller:08X}:{binding.script:08X}' in mine and n not in hidden:hidden.add(n);self._by_hand.add(n)
        self._other_pages=hidden
        show=self.show_others.isChecked()
        masters=self._page_masters()
        chosen=self._fill_talk_filter(masters)
        quiet=set()
        if chosen is not None:
            quiet={n for n,found in masters.items() if found and chosen not in found}
        for n in range(self.pages.count()):self.pages.setTabVisible(n,(show or n not in hidden) and n not in quiet)
        hidden=hidden|quiet
        self.show_others.setEnabled(bool(hidden))
        self.show_others.setText('Show hidden pages'+(f' ({len(hidden)} hidden)' if hidden and not show else ''))
        if hidden:
            self.show_others.setToolTip((f'This NPC’s scene-record variant is {variant}; ' if variant is not None else '')+
                f'{len(hidden)} page(s) are hidden: those a shared handler keeps for another variant, and any you hid '
                '(right-click a tab). Tick to show them all.')
        current=self.pages.currentIndex()
        if current in hidden and not show:
            first=next((n for n in range(self.pages.count()) if n not in hidden),current)
            self.pages.blockSignals(initial)
            self.pages.setCurrentIndex(first);self.pages.blockSignals(False)

    def fill_conditions(self,binding):
        self.conditions.clear()
        if binding is None:return
        if binding.table:
            for line in (f'Conversation {binding.request}: started on Zippo by',
                         'Request conversation (handler 0) on a page of an NPC in this area.'):
                self.conditions.addItem(line)
            return
        found=self._page_conditions(binding)
        if not found:self.conditions.addItem('No test on the way to this page: it runs whenever its code is reached.')
        for line in found:self.conditions.addItem('◆ '+line)

    AVATAR_HEADING = 0.0     # with the facing undone, 0 shows every actor's front

    def show_avatar(self):
        """The entity's model in the Graphic box, turned to face the viewer
        whatever way the game turned it (its baked facing undone)."""
        model=self._entity_model(None,face_front=True)
        if model is None:return
        if not getattr(self,'_avatar_vram',False):
            self.avatar.set_vram(self.panel.viewer.vram_raw_bytes,self.panel.viewer.vram_qimage);self._avatar_vram=True
        self.avatar.model_data=model;self.avatar.hidden_groups=set();self.avatar.prepare_buffers()
        self.avatar.frame_model(heading=self.AVATAR_HEADING);self.avatar.update()

    def start_position(self):
        """Where the entity stands before any preview moved it."""
        base=getattr(getattr(self,'viewport',None),'base_game',None)
        if base is not None:return base
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
                line=plain_line(entries[c.operands[1]]['text'])
                if line:text+=f' — “{line}”'
            elif c.opcode==13 and c.operands[0]&0xff==0:
                page=self.conversation_page(c.operands[1])
                if page is not None:
                    text+=f' — conversation script, tab C{c.operands[1]}'
                    first=self._first_line(self.bindings[page].script)
                    if first:text+=f': “{first}”'
                else:text+=' — conversation script not in this area file (set at run time)'
            elif c.opcode==40 and c.operands[0] in (2,3):
                name=self.item_name(c.operands[1])
                if name:text+=f' — {name}'
            elif c.opcode==62:
                from game.event_preview import native_plain
                said=native_plain(self.exe,self.overlay,script.code_pointer(c))
                if said:text+=' — '+'; '.join(said)
            elif c.opcode==40 and c.operands[0] in (0,1):
                line=self.system_message(c.operands[1])
                if line:text+=f' — “{line}”'
        except (ValueError,OSError,KeyError,IndexError,TypeError):pass
        return text

    def _first_line(self,address):
        """The first dialogue line a script shows, if it shows one."""
        try:
            for c in script.read_graph(script.reader(self.exe,self.overlay),address):
                if c.opcode in (0,1) and c.operands[1]>=0:
                    self.load_bank()
                    return self.bank['entries'][c.operands[0]&0xfff]['entries'][c.operands[1]]['text'] and plain_line(self.bank['entries'][c.operands[0]&0xfff]['entries'][c.operands[1]]['text'])
        except (ValueError,OSError,KeyError,IndexError,TypeError):pass
        return ''

    def system_message(self,number):
        """Resident System message `number` (command 40), plain, or ''."""
        if getattr(self,'_messages',None) is None:
            try:
                from formats.text import system_messages
                scene=self.panel.scene
                idx=getattr(scene,'idx_path',str(Path(scene.dat_path).with_name('TOMBA2.IDX')))
                self._messages=system_messages.load(scene.dat_path,idx)
            except Exception:self._messages=[]
        if not 0<=number<len(self._messages):return ''
        return plain_line(self._messages[number])

    def item_name(self,item):
        if self.items is None:
            try:
                from formats.sprites.pickup_art import item_names
                self.items=item_names(self.panel.exe_path)
            except Exception:self.items={}
        return self.items.get(item)

    def row_parts(self,nodes):
        """(number, indent, command, jump notes, colour) per row."""
        index={c.address:n for n,c in enumerate(nodes)}
        depth=[0]*len(nodes);incoming={}
        for n,c in enumerate(nodes):
            t=c.branch
            if t in index:
                incoming.setdefault(index[t],[]).append(n)
                for k in range(n+1,index[t]):depth[k]+=1
        out=[]
        for n,c in enumerate(nodes):
            notes=[]
            t=c.branch
            text=self.describe(c)
            if t is not None:
                if c.opcode in script.CONDITION_OPS and t in index and index[t]>n:
                    # The rows below run while the test fails: RPG Maker's
                    # "If <condition>:" with the block indented under it.
                    o=c.operands
                    text='If '+script.condition_text(c.opcode,o[0],o[1],o[2],negate=True)+':'
                    notes.append(f'(else skip to #{index[t]+1:02d})')
                elif t in index:notes.append(f'⟶ #{index[t]+1:02d}'+(' if true' if c.opcode in script.CONDITION_OPS else ''))
                elif t<script.BASE:notes.append(f'⟶ shared resident script 0x{t:08X}')
            if c.header&script.END:notes.append('■ end')
            if n in incoming:
                ifs=[k for k in incoming[n] if nodes[k].opcode in script.CONDITION_OPS and k<n]
                if ifs:notes.append('◁ End of If '+', '.join(f'#{k+1:02d}' for k in ifs))
                other=[k for k in incoming[n] if k not in ifs]
                if other:notes.append('◁ '+', '.join(f'#{k+1:02d}' for k in other))
            out.append((n+1,' :  '*depth[n],text,'   '.join(notes),row_colour(c.opcode)))
        return out

    def row_texts(self,nodes):
        return [f'{n:02d} {indent}@>{text}'+(f'   {notes}' if notes else '')
                for n,indent,text,notes,_ in self.row_parts(nodes)]

    def _fill_row(self,item,parts):
        n,indent,text,notes,_=parts
        item.setText(f'{n:02d} {indent}@>{text}'+(f'   {notes}' if notes else ''))
        item.setData(ROW_ROLE,parts)

    def populate(self,selected=0):
        self.actions.blockSignals(True);self.actions.clear()
        nodes=self.drafts.get(self.active,())
        for c,parts in zip(nodes,self.row_parts(nodes)):
            item=QListWidgetItem();self._fill_row(item,parts)
            if c.opcode not in script.LABELS:item.setToolTip('Undecoded native action — its code is in the Native code tab.')
            self.actions.addItem(item)
        self.actions.blockSignals(False)
        self.page_fields['count'].setText(str(len(nodes)))
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
                    for n,parts in enumerate(self.row_parts(nodes)):
                        item=self.actions.item(n)
                        if item is not None:self._fill_row(item,parts)

    # --- plain-language controls over the raw operands ---

    FRIENDLY_WHO={30:(1,1),1:(0x8000,2),57:(0x8000,1),58:(0x8000,1)}   # op: (Zippo bit, animation operand)

    def _friendly_fields(self,c):
        """Who plays it and which animation, as a choice and a list, over the
        flag and animation operands (which stay in step and stay editable
        under "Edit raw parameters")."""
        self._anim_combo=None
        if c.opcode in script.MOVE_TURN_OPS and (c.opcode!=58 or script.move_turn_key(c)):
            key=script.move_turn_key(c)
            button=QPushButton((f'{key[0]}: {key[1]}' if key else 'Move / Turn')+'   — change who / what…')
            button.clicked.connect(self.change_move_turn)
            self.form.insertRow(0,'Move / Turn',button)
        if c.opcode not in self.FRIENDLY_WHO or len(self.fields)<3 or self.raw.isChecked():return
        zbit,which=self.FRIENDLY_WHO[c.opcode];flags=self.fields[0];anim=self.fields[which]
        def signed(v):return v-65536 if v>=32768 else v
        who=QComboBox();who.addItems(['This actor','Zippo'])
        who.setCurrentIndex(1 if flags.value()&zbit else 0)
        def set_who(i):
            v=(flags.value()&0xffff&~zbit)|(zbit if i else 0);flags.setValue(signed(v))
            self._fill_animations(i==1)
        who.currentIndexChanged.connect(set_who)
        self.form.insertRow(0,'Plays on',who)
        row=1
        if c.opcode==30:
            wait=QCheckBox('Wait until it has finished')
            wait.setChecked(bool(flags.value()&2))
            wait.toggled.connect(lambda on:flags.setValue(signed((flags.value()&0xffff&~2)|(2 if on else 0))))
            self.form.insertRow(row,'',wait);row+=1
        combo=QComboBox();combo.setMaxVisibleItems(24);combo.setMinimumWidth(260)
        self._anim_combo=(combo,anim,who)
        self.form.insertRow(row,'Animation',combo)
        def chosen(i):
            value=combo.itemData(i)
            if value is not None and anim.value()!=value:anim.setValue(value)
        combo.activated.connect(chosen)
        anim.valueChanged.connect(lambda _:self._sync_animation_combo())
        for box in (flags,):                       # the raw flag operand is now shown by the choices above
            self.form.setRowVisible(box,False)
        self._fill_animations(who.currentIndex()==1)

    def change_move_turn(self):
        """Change who a move / turn row acts on, or what it does, in place."""
        if self.selected is None:return
        row=self.selected;c=self.drafts[self.active][row]
        dialog=MoveTurnDialog(self,c)
        if not dialog.exec() or dialog.choice is None:return
        opcode,values=dialog.choice
        self.store_operands();self.remember()
        old=c.operands
        # The same kind of turn keeps its numbers; only who / how changes.
        if opcode==c.opcode and opcode in (31,52,58,59):
            same_angle=(old[0]&0x7fff)==0 and (values[0]&0x7fff)==0      # an angle stays an angle
            if opcode==31:values=(values[0],old[1],old[2] if same_angle else 0)
            elif opcode==52:values=(values[0],old[1] if (old[0]&0xF)==0 and (values[0]&0xF)==0 else 0,values[2] if len(values)>2 else 0)
            else:values=(values[0],)+tuple(old[1:3])
        try:script.retype(self.drafts[self.active],row,opcode,values)
        except ValueError as exc:self.status.setText(str(exc));return
        self.selected=None;self.populate(row)
        self.command_dialog.title.setText(f'{row+1:02d}  {self.describe(self.drafts[self.active][row])}')

    def _sync_animation_combo(self):
        pack=getattr(self,'_anim_combo',None)
        if not pack:return
        combo,anim,_who=pack
        try:
            i=combo.findData(anim.value())
            combo.blockSignals(True)
            if i>=0:combo.setCurrentIndex(i)
            else:combo.setEditText(str(anim.value())) if combo.isEditable() else None
            combo.blockSignals(False)
        except RuntimeError:self._anim_combo=None

    def _fill_animations(self,zippo):
        pack=getattr(self,'_anim_combo',None)
        if not pack:return
        combo,anim,_who=pack
        key='zippo' if zippo else 'self'
        cache=getattr(self,'_catalogues',None)
        if cache is None:cache=self._catalogues={}
        if key in cache:
            try:
                combo.blockSignals(True);combo.clear()
                if not cache[key]:combo.addItem('(animations unavailable here)',None)
                for n,ticks,loops in cache[key]:
                    combo.addItem(f'{n} — {ticks} ticks'+(', loops' if loops else ''),n)
                if anim.value()<0:combo.insertItem(0,'−1 — back to rest',-1)
                combo.blockSignals(False);self._sync_animation_combo()
            except RuntimeError:self._anim_combo=None
            return
        combo.clear();combo.addItem('Loading animations…',None)
        job=CatalogueDecode(self.panel.scene,self.instance,self.bindings[self.active].animations if self.active is not None else None,key,self)
        self.frames_jobs.append(job);job.decoded.connect(self._catalogue_ready);job.start()

    def _catalogue_ready(self,result):
        job=self.sender()
        if job in self.frames_jobs:self.frames_jobs.remove(job)
        who,items,error=result
        if not hasattr(self,'_catalogues'):self._catalogues={}
        self._catalogues[who]=items
        pack=getattr(self,'_anim_combo',None)
        if pack:self._fill_animations(pack[2].currentIndex()==1)

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
        self.preview_model_button.setText('Play on entity model' if c.opcode in ANIMATION_OPS else 'Show selected entity model')
        self.tomba_button.setEnabled(c.opcode in TOMBA_OPS)
        tab=(0 if c.opcode in TEXT_TAB_OPS or c.opcode in MODEL_TAB_OPS else
             1 if c.opcode in PATH_TAB_OPS else 2)
        self.preview_tabs.setCurrentIndex(tab)
        labels=script.FIELDS.get(c.opcode,())
        editable=(c.opcode in script.FIELDS and c.opcode!=62) or self.raw.isChecked()
        for n,value in enumerate(c.operands):
            box=DragSpinBox();box.setRange(-32768,32767);box.setValue(value)
            box.setEnabled(editable)
            self.fields.append(box)
            box.valueChanged.connect(lambda _:self._field_timer.start())
            name=labels[n] if n<len(labels) else (f'Parameter {n+1}' if n<3 else f'Extended parameter {n-2}')
            self.form.addRow(name,box)
        self._friendly_fields(c)
        index={x.address:n for n,x in enumerate(nodes)}
        if c.branch in index and game_build.current().name=='us-retail':
            jump=QSpinBox();jump.setRange(1,len(nodes));jump.setValue(index[c.branch]+1)
            jump.setToolTip('The row this command jumps to (for an If: where its block ends).')
            def retarget(box=jump,command=c):
                n=box.value()-1;rows=self.drafts[self.active]
                if rows[n].address==command.branch:return
                self.store_operands();self.remember();script.set_branch_target(command,rows[n].address)
                keep=self.selected;self.selected=None;self.populate(keep)
            jump.editingFinished.connect(retarget)
            self.form.addRow('Jump to row',jump)
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
        elif c.opcode in self.PROGRESS_OPS:
            self.status.setText(self.progress_note(c))
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

    # An instruction's one editable number: an immediate, offset, shift or
    # target - never part of the mnemonic or a register name (a0, s1, t9...).
    CODE_NUMBER=r'(?<![A-Za-z0-9_$.])(-?(?:0x[0-9A-Fa-f]+|\d+))(?![A-Za-z0-9_])'

    def _code_template(self,text):
        """(text with its number as {}, the number) or (None, '')."""
        import re
        mnemonic=text.split(' ',1)[0]
        if mnemonic.startswith('.'):return None,''      # raw data words stay as they are
        m=re.search(self.CODE_NUMBER,text[len(mnemonic):])
        if not m:return None,''
        at=len(mnemonic)+m.start(1);end=len(mnemonic)+m.end(1)
        return text[:at]+'{}'+text[end:],m.group(1)

    def edit_code(self,on):
        from PyQt6.QtWidgets import QTableWidgetItem
        if on:
            lines=self._code_lines(self.code_preview.toPlainText())
            if not lines:
                self.code_edit.setChecked(False);self.status.setText('There are no instructions here to edit.');return
            self._code_rows=[];self._code_done=[];self._code_undone=[];self._code_last={}
            self._code_replaying=True
            table=self.code_table;table.setRowCount(len(lines))
            for row,(address,(word,text)) in enumerate(sorted(lines.items())):
                template,value=self._code_template(text)
                self._code_rows.append((address,word,text,template,value))
                cells=(QTableWidgetItem(f'{address:08X}'),QTableWidgetItem(text if template is None else template.replace('{}','#')),
                       QTableWidgetItem(value))
                for column,item in enumerate(cells):
                    flags=Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsEnabled
                    if column==2 and template is not None:flags|=Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags);table.setItem(row,column,item)
            table.resizeColumnToContents(0);table.resizeColumnToContents(2)
            self._code_replaying=False
            self.code_preview.hide();table.show()
            self.code_save.setEnabled(True);self.code_discard.setEnabled(True);self._code_buttons()
            self.status.setText('Editing native code: only the numbers can change (immediates, offsets, shifts, targets). '
                                'Instructions keep their place; Save assembles and writes them.')
        else:
            self.code_table.hide();self.code_preview.show()
            self._code_rows=[];self._code_done=[];self._code_undone=[]
            for b in (self.code_save,self.code_undo,self.code_redo,self.code_discard):b.setEnabled(False)

    def _code_cell_changed(self,item):
        if self._code_replaying or item.column()!=2:return
        row=item.row();before=self._code_last.get(row,self._code_rows[row][4])
        self._code_done.append((row,before,item.text()));self._code_undone.clear()
        self._code_last[row]=item.text();self._code_buttons()

    def _code_set(self,row,text):
        item=self.code_table.item(row,2)
        self._code_replaying=True;item.setText(text);self._code_last[row]=text;self._code_replaying=False

    def code_undo_step(self):
        if not self._code_done:return
        row,before,after=self._code_done.pop();self._code_set(row,before)
        self._code_undone.append((row,before,after));self._code_buttons()

    def code_redo_step(self):
        if not self._code_undone:return
        row,before,after=self._code_undone.pop();self._code_set(row,after)
        self._code_done.append((row,before,after));self._code_buttons()

    def _code_buttons(self):
        editing=self.code_edit.isChecked()
        self.code_undo.setEnabled(editing and bool(self._code_done))
        self.code_redo.setEnabled(editing and bool(self._code_undone))

    def code_changes(self):
        """{address: new word} from the changed numbers, or raise ValueError."""
        from psx.mips import assemble,AssemblyError
        changes={}
        for row,(address,word,text,template,value) in enumerate(self._code_rows):
            item=self.code_table.item(row,2)
            if template is None or item is None or item.text().strip()==value:continue
            try:new=assemble(template.format(item.text().strip()),address)
            except (AssemblyError,ValueError,IndexError) as exc:
                raise ValueError(f'0x{address:08X} {template.replace("{}","#")}: {exc}') from None
            if new!=word:changes[address]=new
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
        if not self._apply_code(changes):return
        self.code_edit.setChecked(False)
        c=self.drafts[self.active][self.selected] if self.active is not None and self.selected is not None else None
        if c is not None:self.show_code(c)
        self.status.setText(f'Wrote {len(changes)} instruction(s).')

    def _apply_code(self,changes):
        """Write changed instruction words ({address: word}) into the area file
        or MAIN.EXE through the Level Editor's project files. True on success."""
        import struct
        panel=self.panel;files={}
        main=struct.unpack_from('<I',self.exe,24)[0]
        overlay=bytearray(self.overlay);exe=bytearray(self.exe)
        for address,word in changes.items():
            if address>=script.BASE:
                at=address-script.BASE
                if at+4>len(overlay):QMessageBox.warning(self,'Code not saved',f'0x{address:08X} is past the area file.');return False
                struct.pack_into('<I',overlay,at,word)
            else:
                at=address-main+0x800
                if not 0x800<=at<=len(exe)-4:QMessageBox.warning(self,'Code not saved',f'0x{address:08X} is outside MAIN.EXE.');return False
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
            QMessageBox.warning(self,'Code not saved',str(exc));return False
        for path,data in files.items():panel._placement_files[Path(path).name]=data
        if bytes(overlay)!=self.overlay:panel.scene.overlay_data=bytes(overlay)
        self.overlay,self.exe=bytes(overlay),bytes(exe)
        panel._scene_memory_cache.clear()
        return True

    def change_music(self):
        """Pick another conversation-music cue for this NPC: rewrites the cue
        number its native code passes to f_BeginScriptedInteractionAudio."""
        from PyQt6.QtWidgets import QInputDialog
        try:sites=script.native_music_sites(self.exe,self.overlay,self.native_handler) if self.native_handler else []
        except Exception:sites=[]
        if not sites:
            QMessageBox.information(self,'Conversation music','This NPC’s code starts no music with a fixed cue that can be changed here. '
                'To add music, insert “Start conversation music” in the Sound tab; it plays from that row on.');return
        current=sites[0][1]
        names=[]
        for cue in range(1,9):
            sound=script.music_sound(cue);slot=script.music_slot(sound) if sound is not None else None
            names.append(f'{cue}  (sound 0x{sound:02X}, SEQ slot {slot})' if slot is not None else str(cue))
        chosen,ok=QInputDialog.getItem(self,'Conversation music',
            f'Cue played when this NPC starts talking. It is shared by every NPC that uses this handler (0x{self.native_handler:08X}).',
            names,max(0,min(7,current-1)),False)
        if not ok:return
        cue=int(chosen.split()[0])
        if cue==current:return
        import struct
        changes={}
        for address,_old in sites:
            changes[address]=0x24050000|cue          # addiu a1, zero, cue
        if not changes:return
        if QMessageBox.question(self,'Conversation music',f'Change cue {current} to {cue} for every NPC using handler 0x{self.native_handler:08X}? '
                                f'({len(changes)} instruction(s) in the area file.)')!=QMessageBox.StandardButton.Yes:return
        if not self._apply_code(changes):return
        self._music_text=False
        self.page_fields['music'].setText(self.native_music_text() or '—')
        self.status.setText(f'Conversation music cue is now {cue}.')

    def show_code(self,c):
        if self.code_edit.isChecked():
            if self._code_done:
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
        self._refresh_fields()
        c=self.drafts[self.active][self.selected] if self.active is not None and self.selected is not None else None
        if c is None:return
        # The popup's model follows a changed animation number as you drag.
        if c.opcode in ANIMATION_OPS and self.command_dialog.isVisible():self.preview_model_action()

    def _refresh_fields(self):
        self.store_operands()
        if self.selected is not None:
            c=self.drafts[self.active][self.selected]
            self.show_text(c);self.update_path(self.selected);self.show_code(c)
            if self.timeline_box.isChecked():self.apply_timeline(self.selected)
            elif self.autoplay.isChecked() and c.opcode in ANIMATION_OPS|TOMBA_OPS|CAMERA_OPS:self.play_selected()

    def show_text(self,c):
        self.player.stop();self.linked_entry=None
        self.pick_text.setEnabled(c.opcode in (0,1) and game_build.current().name=='us-retail')
        self.goto_txtd.setEnabled(c.opcode in (0,1))
        self.split_text.setEnabled(c.opcode==1 and game_build.current().name=='us-retail')
        self.split_all.setEnabled(game_build.current().name=='us-retail' and any(x.opcode==1 for x in self.drafts.get(self.active,())))
        self.music_button.setEnabled(c.opcode==11 and c.operands[0]!=0)
        self.text_choices.blockSignals(True);self.text_choices.clear();self.text_choices.blockSignals(False)
        self.text_preview.blockSignals(True);self.text_preview.clear();self.text_preview.blockSignals(False)
        self.text_preview.setReadOnly(True);self.play_voice.setEnabled(False)
        if c.opcode==11:
            self.text_preview.blockSignals(True)
            self.text_preview.setPlainText(script.summary(c)+'\n\nStart stops the area track, letterboxes the view and plays this '
                'sequenced cue. End plays sound 0x7F, lifts the letterbox and restarts the area track.')
            self.text_preview.blockSignals(False)
            return
        if c.opcode==40:
            o=c.operands
            self.text_preview.blockSignals(True)
            if o[0]==10:
                self.text_preview.setPlainText(self.open_message_note())
            elif o[0] in (2,3):
                name=self.item_name(o[1])
                self.text_preview.setPlainText(f'Item message for item {o[1]}'+(f': {name}' if name else '')+
                    ('\nWaits until the message closes.' if o[0]&1 else ''))
            elif o[0] in (0,1):
                line=self.system_message(o[1])
                self.text_preview.setPlainText((f'System message {o[1]}: {line}' if line else f'System message {o[1]} (not in the table)')
                    +('\nWaits until the message closes.' if o[0]&1 else '')+'\n\nDouble-click the row to choose another message.')
            else:
                self.text_preview.setPlainText(f'Message mode {o[0]}: payload {o[1]}, {o[2]}.')
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
                for index,value in enumerate(entries):self.text_choices.addItem(f'Candidate {index}: '+plain_line(value['text']),index)
            elif entry<len(entries):self.text_choices.addItem(f'Dialogue entry {entry}',entry)
            else:raise ValueError('The dialogue entry is outside this master.')
            self.text_choices.blockSignals(False);self.choose_text(self.text_choices.currentIndex())
            if entry<0:
                self.text_preview.blockSignals(True);self.text_preview.setPlainText(self.selector_note(entries));self.text_preview.blockSignals(False)
        except (ValueError,OSError,KeyError,IndexError) as exc:
            self.text_choices.blockSignals(False)
            self.text_preview.setPlainText(str(exc))

    def open_message_note(self):
        """What 'wait for the open message to close' waits on: a message
        already on screen, opened before this page's rows run - by the NPC's
        native code or by the page before."""
        head=('This row shows nothing itself. It waits for a message box that is already open to close. That box was '
              'opened before these rows run - by this NPC’s native code, or by an earlier page.\n\n')
        cache=getattr(self,'_messages',None)
        if cache is None:cache=self._messages={}
        if self.native_handler not in cache:
            try:cache[self.native_handler]=script.native_messages(self.exe,self.overlay,self.native_handler) if self.native_handler else []
            except Exception:cache[self.native_handler]=[]
        found=cache[self.native_handler]
        if not found:
            return head+'The NPC’s code opens no message with a fixed number that could be found, so which one it is cannot be named here.'
        lines=[]
        for kind,number in found:
            if kind=='system':
                line=self.system_message(number)
                lines.append(f'• system message {number}'+(f': “{line}”' if line else ''))
            elif kind=='item':
                name=self.item_name(number);lines.append(f'• item message for item {number}'+(f' ({name})' if name else ''))
            else:lines.append(f'• popup message {number}')
        return head+'Messages this NPC’s code can open:\n'+'\n'.join(lines)

    def selector_note(self,entries):
        """What picks a runtime-selected entry: the NPC's own code stores it
        into the actor's dialogue selector (+0x7A); the constants it stores,
        the tests that hold there and the line each is."""
        head=('This line is chosen while the game runs: the NPC’s native code stores the entry number into the actor '
              '(dialogue selector, +0x7A) before the page reads it.\n\n')
        cache=getattr(self,'_selectors',None)
        if cache is None:cache=self._selectors={}
        if self.native_handler not in cache:
            try:cache[self.native_handler]=script.native_selectors(self.exe,self.overlay,self.native_handler) if self.native_handler else []
            except Exception:cache[self.native_handler]=[]
        found=cache[self.native_handler]
        if not found:
            return head+'No constant is stored there that could be found (the number is computed), so the entry cannot be named here.'
        lines=[]
        for value,_at,conditions in found:
            text=plain_line(entries[value]['text']) if 0<=value<len(entries) else '(outside this dialogue)'
            when=('  when '+'; '.join(conditions[:3])) if conditions else ''
            lines.append(f'• entry {value}: “{text}”{when}')
        return head+'Entries it can pick:\n'+'\n'.join(lines)

    PROGRESS_OPS={4:0,6:1,12:1,43:1}      # op: the operand holding the progress index

    def progress_note(self,c):
        """Who else sets the area-progress byte this row uses."""
        index=c.operands[self.PROGRESS_OPS[c.opcode]]
        writers=getattr(self,'_writers',None)
        if writers is None:
            if not getattr(self,'_writers_job',None):
                self._writers_job=job=ProgressDecode(self.panel.scene,self.exe,self.overlay,self)
                job.decoded.connect(self._writers_ready);job.start()
            return f'Area progress[{index}] is one of the game’s story bytes for this area. Looking up which rows set it…'
        rows=writers.get(index,[])
        head=f'Area progress[{index}] is one of the game’s story bytes for this area (it has no name in the game).'
        if not rows:return head+' No script row in this area sets it; native code sets it.'
        return head+' Set by: '+'; '.join(f'{label} page {page} row {row} ({text})' for label,page,row,text in rows[:8])+(' …' if len(rows)>8 else '')

    def _writers_ready(self,result):
        self._writers,_error=result
        self._writers_job=None
        if self.selected is not None and self.active in self.drafts:
            nodes=self.drafts[self.active]
            if self.selected<len(nodes) and nodes[self.selected].opcode in self.PROGRESS_OPS:
                self.status.setText(self.progress_note(nodes[self.selected]))

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

    def _new_operands(self,data):
        if isinstance(data,tuple):return data
        return data,script.INSERTABLE.get(data,(0,0,0))

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

    def insert(self,after=False,data=None):
        """Insert a command before (or after) the selected row: `data`, or
        what is picked in the Event Command window."""
        button=self.add_after if after else self.add
        if self.selected is None or not button.isEnabled():return
        if data is None:
            dialog=EventCommandDialog(self,self.command_choices,'Event Command - insert after' if after else 'Event Command')
            if not dialog.exec() or dialog.choice is None:return
            data=dialog.choice
        if data in ('If event state…','If area progress…'):
            self.insert_if(44 if data=='If event state…' else 43,self.selected);return
        if data=='Move / Turn…':
            dialog=MoveTurnDialog(self)
            if not dialog.exec() or dialog.choice is None:return
            data=dialog.choice
        row=self.selected;opcode,values=self._new_operands(data)
        if opcode in (0,1):
            values=self._text_operands(opcode,values)
            if values is None:return
        if after:self._edit(lambda:script.insert_after(self.drafts[self.active],row,opcode,values),row+1)
        else:self._edit(lambda:script.insert_before(self.drafts[self.active],row,opcode,values),row)

    def insert_after(self):self.insert(after=True)

    def delete_if_allowed(self):
        if self.remove.isEnabled():self.delete()

    def move_if_allowed(self,delta):
        if (self.up if delta<0 else self.down).isEnabled():self.move(delta)

    def edit_row(self):
        """Double-click / Space: edit the command - its dialogue for text,
        else its first parameter."""
        if self.selected is None:return
        c=self.drafts[self.active][self.selected]
        if c.opcode==13 and c.operands[0]&0xff==0:
            page=self.conversation_page(c.operands[1])
            if page is not None:self.pages.setCurrentIndex(page);return
        self.open_command()

    def choose_for_row(self):
        c=self.drafts[self.active][self.selected] if self.selected is not None else None
        if c is not None and c.opcode==40:self.choose_message()
        else:self.choose_dialogue()

    def open_command(self):
        """The command popup: parameters plus the panes this command uses."""
        if self.selected is None:return
        nodes=self.drafts[self.active];row=self.selected;c=nodes[row]
        before=(self._snapshot(),len(self._undo),list(self._redo))
        self.select_action(row)
        tabs=self.preview_tabs
        wanted={0:c.opcode in TEXT_TAB_OPS or c.opcode in MODEL_TAB_OPS or c.opcode in ANIMATION_OPS,
                1:c.opcode in PATH_TAB_OPS,2:True}
        for index,shown in wanted.items():tabs.setTabVisible(index,shown)
        tabs.setCurrentIndex(next(i for i in (0,1,2) if wanted[i]))
        if c.opcode==40 and c.operands[0] in (0,1):self.pick_text.setText('Choose message…');self.pick_text.setEnabled(True)
        else:self.pick_text.setText('Choose dialogue…')
        # The line is edited right here; OK writes it.
        editable=c.opcode in (0,1) and self.linked_entry is not None
        original=self.text_preview.toPlainText() if editable else None
        self.text_preview.setReadOnly(not editable)
        self.command_dialog.title.setText(f'{row+1:02d}  {self.describe(c)}')
        self.command_dialog.setWindowTitle(f'Edit Command - {c.label}')
        if c.opcode in ANIMATION_OPS and self.autoplay.isChecked():self.preview_model_action()
        kept=self.command_dialog.exec()
        self.text_preview.setReadOnly(True)
        if not kept:
            snapshot,undo_length,redo=before
            drafts,bank,active,selected=snapshot
            self.fields=[]
            self.drafts=drafts
            if bank is not None and self.bank is not None:self.bank.clear();self.bank.update(bank)
            del self._undo[undo_length:];self._redo[:]=redo;self._undo_state()
            self.selected=None;self.populate(row);return
        self.store_operands()
        c=self.drafts[self.active][self.selected if self.selected is not None else row]
        if editable and self.text_preview.toPlainText()!=original and self.linked_entry is not None:
            entry=self.text_choices.currentData()
            if entry is not None:self.apply_text(c,self.linked_master,entry,self.text_preview.toPlainText());return
        keep=self.selected if self.selected is not None else row;self.selected=None;self.populate(keep)

    def row_menu(self,pos):
        item=self.actions.itemAt(pos)
        if item is not None:self.actions.setCurrentItem(item)
        menu=QMenu(self)
        for text,keys,slot,enabled in (('Insert…','Ins',lambda:self.insert(),self.add.isEnabled()),
                                       ('Insert After…','',self.insert_after,self.add_after.isEnabled()),
                                       ('Edit','Space',self.edit_row,self.selected is not None),
                                       (None,None,None,None),
                                       ('Delete','Del',self.delete,self.remove.isEnabled()),
                                       ('Move Up','Ctrl+Up',lambda:self.move(-1),self.up.isEnabled()),
                                       ('Move Down','Ctrl+Down',lambda:self.move(1),self.down.isEnabled()),
                                       (None,None,None,None),
                                       ('Play','',lambda:self.play_selected(manual=True),self.selected is not None),
                                       (None,None,None,None),
                                       ('Undo','Ctrl+Z',self.undo,bool(self._undo)),
                                       ('Redo','Ctrl+Y',self.redo,bool(self._redo))):
            if text is None:menu.addSeparator();continue
            action=menu.addAction(text+(f'\t{keys}' if keys else ''));action.setEnabled(enabled)
            action.triggered.connect(lambda _=False,f=slot:f())
        menu.exec(self.actions.viewport().mapToGlobal(pos))

    def show_help(self):
        QMessageBox.information(self,'Event Editor',
            'Event pages are the native scripts this entity’s code loads. Each runs top to bottom; '
            'rows with ⟶ jump, ◁ marks where jumps land, ■ ends the script. Quest selection and '
            'handler-side actions stay in native code.\n\n'
            'Ins: insert a command (Event Command window)\n'
            'Space / double-click: edit the command\n'
            'Del: delete    Ctrl+Up / Ctrl+Down: move\n'
            'Ctrl+Z / Ctrl+Y: undo / redo\n'
            'Right-click a row for the same commands.\n\n'
            'OK applies and closes; Apply writes and keeps the editor open. '
            'Writing needs “Apply shared script binding…” ticked.')

    def edit_text_popup(self,command=None):
        """Show Text: the line in a window of its own, edited in place;
        picking another line there re-points the command."""
        c=command or self.drafts[self.active][self.selected]
        try:self.load_bank()
        except (ValueError,OSError,KeyError) as exc:self.status.setText(str(exc));return False
        master=c.operands[0]&0xfff;entry=c.operands[1]
        if entry<0:
            entry=self.text_choices.currentData() if self.text_choices.currentData() is not None else 0
        dialog=ShowTextDialog(self,self.bank,master,entry)
        if not dialog.exec():return False
        self.apply_text(c,dialog.master,dialog.entry,dialog.text.toPlainText())
        return True

    def apply_text(self,c,master,entry,text):
        """Show Text's OK: the line's text, and the command pointed at it.
        A runtime-selected entry (-1) keeps choosing at run time."""
        self.store_operands();self.remember()
        line=self.bank['entries'][master]['entries'][entry]
        if text!=line['text']:line['text']=text
        values=c.operands
        if (values[0]&0xfff)!=master or (values[1]>=0 and values[1]!=entry):
            values[0]=(values[0]&~0xfff)|master
            if values[1]>=0:values[1]=entry
            c.set_operands(values)
        row=self.selected;self.selected=None;self.populate(row)

    def choose_message(self):
        c=self.drafts[self.active][self.selected]
        self.system_message(0)
        if not self._messages:self.status.setText('The System message table was not found.');return
        dialog=MessagePicker(self,self._messages,c.operands[1])
        if not dialog.exec() or dialog.list.currentRow()<0:return
        self.store_operands();self.remember()
        values=c.operands;values[1]=dialog.list.currentRow();c.set_operands(values)
        row=self.selected;self.selected=None;self.populate(row)

    def insert_if(self,opcode,row):
        """Insert If <test>: the rows from here run while it fails; when it
        holds the script jumps to the row asked for."""
        nodes=self.drafts[self.active]
        target,ok=QInputDialog.getInt(self,'If','When the test holds, jump to row (the If block ends there):',
                                      min(row+2,len(nodes)),1,len(nodes))
        if not ok:return
        values=(0,0,255) if opcode==44 else (0,0,0)
        self._edit(lambda:script.insert_branch(nodes,row,opcode,values,target-1),row)
        self.status.setText('If inserted: set its test in the parameters. Rows up to the jump target are the block.')

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
        if self.timeline_box.isChecked():self.apply_timeline(row)
        elif self.autoplay.isChecked():self.play_selected()

    # --- simulation: where the events so far leave things ---

    def start_yaw(self):
        """The entity's own facing before any preview, in game units."""
        from game.actor_assembly import degrees_to_units
        saved=getattr(getattr(self,'viewport',None),'entity',None)
        found=baked_facing(saved['assembly'] if saved else getattr(self.instance,'assembly',None))
        base=getattr(getattr(self,'viewport',None),'base',None)
        angle=base[0][3] if base is not None else getattr(self.instance,'angle',0)
        if found is not None:
            pose_yaw,facing=found
            return (facing+degrees_to_units(angle or 0)-pose_yaw)&0xfff
        return degrees_to_units(angle or 0)

    TOMBA_DISTANCE = 250    # assumed stand-off when a page gives no position

    def tomba_state(self,nodes,row,yaw_row=None):
        """(position, yaw, animation ID, how found) of Tomba after rows
        0..row: placed by the page's own commands, facing the entity, in
        the last Tomba animation the page started (else idle, 0x02)."""
        position,how=self.tomba_position(nodes,row)
        if position is None:return None,0,2,how
        animation=2
        for c in nodes[:row+1]:
            o=c.operands
            if c.opcode==55 and o[1]>=0:animation=o[1]
            elif c.opcode in (57,58) and o[2]>=0 and not (c.opcode==58 and o[0]&0x7fff==2):animation=o[2]
        entity=self.state_at(nodes,row)[0] or position
        yaw=yaw_toward(position,entity)
        if self.state_at(nodes,row)[0] is not None:
            yaw=self.yaws_at(nodes,row if yaw_row is None else yaw_row)[0]['tomba']
        return position,yaw,animation,how

    def tomba_result(self,animation):
        """tomba_frames for `animation`, cached; None if it cannot be posed."""
        if animation not in self._tomba_cache:
            try:
                from game.event_preview import tomba_frames
                self._tomba_cache[animation]=tomba_frames(self.panel.scene,animation)
            except Exception as exc:
                self._tomba_cache[animation]=None;self._tomba_error=str(exc)
        return self._tomba_cache[animation]

    def show_tomba(self,nodes,row,yaw_row=None):
        position,yaw,animation,how=self.tomba_state(nodes,row,yaw_row)
        result=self.tomba_result(animation)
        if result is None:return ''
        idle=self.tomba_result(2) or result
        self.viewport.show_tomba(position,yaw,result,-idle['feet'])
        return how

    def tomba_position(self,nodes,row):
        """Where Tomba is taken to stand by row `row`: Set Tomba location
        if the page has run one, else the entity plus the page's interaction
        offset (f_UpdateScriptedInteractionApproach keeps the actor at Tomba
        minus that offset). (position, how it was found)."""
        start=self.start_position()
        if start is None:
            # Area scripts stand nowhere: take where the page puts Tomba,
            # else the first point its camera is told to look at.
            for c in nodes[:row+1]:
                o=c.operands
                if c.opcode==54:return (float(o[1]),float(o[2]),float(o[3] if c.extended else 0)),'Tomba placed by Set Tomba location.'
            for c in nodes:
                o=c.operands
                if c.opcode==22 and o[2]==0 and c.extended:return (float(o[3]),float(o[4]),float(o[5])),'Tomba assumed at the camera’s focus.'
            return None,''
        found=None;offset=None
        for c in nodes[:row+1]:
            o=c.operands
            if c.opcode==54:found=[o[1],o[2],o[3] if c.extended else start[2]]
            elif c.opcode==49:           # Move Tomba along X or depth
                found=list(found or self._tomba_default(start)[0])
                found[0 if o[1]==0 else 2]=o[2]
        for c in nodes:
            o=c.operands
            if c.opcode==16:offset=o[:3];break
            if c.opcode==13 and o[2]&4 and c.extended:offset=o[3:6];break
        if found:return self._settled(tuple(float(v) for v in found)),'Tomba placed by Set Tomba location.'
        if offset:return self._settled(tuple(s+o for s,o in zip(start,offset))),'Tomba at the entity plus the page’s interaction / conversation offset.'
        spot,grounded=self._tomba_default(start)
        return spot,(f'No Tomba position on this page: Tomba is assumed about {self.TOMBA_DISTANCE} units from the entity, '
                     +('standing on the nearest collision floor.' if grounded else 'on the side the viewport looks from (no collision floor found near it).'))

    def _ground(self,point,reach=400,rise=320):
        """`point` (game axes) moved onto the nearest collision cell and down
        (or up) to its floor - LevelScene.pickup_position, as a dropped
        chest is - or None when no floor is within `reach` / `rise`."""
        import math
        from gui.level.level_scene import view_point
        scene=self.panel.scene
        if not hasattr(scene,'pickup_position'):return None
        try:view,_plane=scene.pickup_position(view_point(point),snap_ground=True,view=getattr(self.instance,'scene',None))
        except Exception:return None
        ground=(view[2],-view[1],view[0])                     # view -> game axes
        if math.hypot(ground[0]-point[0],ground[2]-point[2])>reach or abs(ground[1]-point[1])>rise:return None
        return ground

    def _settled(self,point):
        """A position the page gives: kept, its height put on a floor right there."""
        ground=self._ground(point,reach=64,rise=160)
        return (point[0],ground[1],point[2]) if ground else point

    def _tomba_default(self,start):
        """No position on the page: Tomba stands TOMBA_DISTANCE from the
        entity, on the first spot round it - from the side the viewport looks
        from - that lands on a collision floor near the entity's height.
        (position, grounded). Fixed once found, so it does not jump."""
        import math
        if getattr(self,'_tomba_spot',None) is not None:return self._tomba_spot
        side=None
        c=getattr(getattr(self.panel,'viewer',None),'camera_controls',None)
        if c is not None:
            from gui.level.level_viewer import UNIT_SCALE
            e=c._eye();eye=(e[2]*UNIT_SCALE,-e[1]*UNIT_SCALE,e[0]*UNIT_SCALE)   # view -> game axes
            dx,dd=eye[0]-start[0],eye[2]-start[2]
            if math.hypot(dx,dd)>1:side=math.atan2(dd,dx)
        if side is None:side=-self.start_yaw()*2*math.pi/4096              # inverse of yaw_toward
        for turn in (0,30,-30,60,-60,90,-90,120,-120,150,-150,180):
            a=side+math.radians(turn)
            for distance in (self.TOMBA_DISTANCE,self.TOMBA_DISTANCE*0.6,self.TOMBA_DISTANCE*1.4):
                ground=self._ground((start[0]+distance*math.cos(a),start[1],start[2]+distance*math.sin(a)),reach=96,rise=200)
                if ground is not None:
                    self._tomba_spot=(ground,True);return self._tomba_spot
        self._tomba_spot=((start[0]+self.TOMBA_DISTANCE*math.cos(side),start[1],start[2]+self.TOMBA_DISTANCE*math.sin(side)),False)
        return self._tomba_spot

    def state_at(self,nodes,row):
        """(position, yaw or None) of the entity after rows 0..row."""
        from game.event_preview import trace
        start=self.start_position()
        if start is None or row<0:return start,None
        position=start;yaw=None;turns=False
        steps={}
        for s in trace(nodes,row,start):
            if s[1] in ('move','set'):steps[s[0]]=s[3]
        for n,c in enumerate(nodes[:row+1]):
            if n in steps:position=tuple(float(v) for v in steps[n])
            o=c.operands
            if c.opcode in (31,58,59):turns=True
        if turns:
            tomba=self.tomba_position(nodes,row)[0]
            yaw=self._yaws(nodes,row,{'self':position,'tomba':tomba,'zippo':None})['self']
        return position,yaw

    def _yaws(self,nodes,row,positions):
        """Where the entity, Zippo and Tomba face after rows 0..row (game
        units): from their usual defaults, turned by the page's own turn rows."""
        entity,tomba,zippo=positions.get('self'),positions.get('tomba'),positions.get('zippo')
        yaws={'self':self.start_yaw(),
              'tomba':yaw_toward(tomba,entity) if tomba and entity else 0,
              'zippo':yaw_toward(zippo,tomba) if zippo and tomba else 0}
        for c in nodes[:row+1]:
            o=c.operands
            if c.opcode in (31,58,59):
                for who,target,_step in script.turn_plan(c,positions):yaws[who]=target
            elif c.opcode in (52,56) and entity and tomba:
                mode=o[0]&0xF if c.opcode==52 else 1
                if mode==0:yaws['tomba']=o[1]&0xfff
                elif mode in (1,2):yaws['tomba']=(yaw_toward(tomba,entity)-(0x800 if mode==2 else 0))&0xfff
        return yaws

    def yaws_at(self,nodes,row):
        """(yaws, positions) of the entity, Zippo and Tomba after rows 0..row."""
        position=self.state_at(nodes,row)[0]
        tomba=self.tomba_position(nodes,row)[0]
        positions={'self':position,'tomba':tomba,'zippo':None}
        if position is not None and tomba is not None:
            first=self._yaws(nodes,row,positions)
            flags=0;offset=None
            for c in nodes[:row+1]:
                if c.opcode==13:
                    flags=c.operands[2]
                    if flags&4 and c.extended:offset=tuple(c.operands[3:6])
            from game.event_preview import zippo_position
            positions['zippo']=zippo_position(tomba,first['tomba'],flags,offset)
        return self._yaws(nodes,row,positions),positions

    def turn_entries(self,nodes,row):
        """[(who, yaw before, yaw after, step, position)] for a turn row."""
        c=nodes[row]
        if c.opcode not in (31,58,59):return []
        after,positions=self.yaws_at(nodes,row)
        before,_=self.yaws_at(nodes,row-1)
        return [(who,before[who],after[who],step,positions.get(who))
                for who,_target,step in script.turn_plan(c,positions) if positions.get(who) is not None]

    def timeline_toggled(self,on):
        self.viewport.set_timeline(on)
        if on and self.selected is not None:self.apply_timeline(self.selected)
        elif not on:self.status.setText('Simulation preview off: the entity and camera are back where they were.')

    def apply_timeline(self,row):
        """Stand everything as of the row before, then play this row."""
        nodes=self.drafts.get(self.active,())
        if not 0<=row<len(nodes):return
        self.store_operands()
        position,yaw=self.state_at(nodes,row-1)
        self.viewport.show_state(position,self.start_yaw() if yaw is None else yaw)
        tomba,_=self.tomba_position(nodes,row)
        if not self.viewport.play(nodes,row,tomba):
            position,yaw=self.state_at(nodes,row)
            self.viewport.show_state(position,self.start_yaw() if yaw is None else yaw)
        turning=nodes[row].opcode in (31,58,59)    # these animate from where the row before left them
        how=self.show_tomba(nodes,row,row-1 if turning else None)
        key=tuple(x.raw for x in nodes[:row+1] if x.opcode in CAMERA_OPS or x.opcode==54)
        if nodes[row].opcode in CAMERA_OPS or (key and key!=self._camera_key):
            self._camera_key=key;self.run_camera(row)
        self.simulate_actors(nodes,row,row-1 if turning else None)
        c=nodes[row]
        if self.autoplay.isChecked():
            if c.opcode in TOMBA_OPS and c.opcode!=57 and c.opcode!=58:self.preview_tomba_animation()
            elif c.opcode in ANIMATION_OPS:self.preview_model_action()
        if not self.status.text().startswith('Previewing'):
            self.status.setText(f'Simulation: as the page stands after row {row+1:02d}. {how} '
                                'Closing the editor puts everything back.')

    # --- who animates what at this point of the page ---

    def is_conversation(self):
        b=self.bindings[self.active] if self.active is not None else None
        return bool(b is not None and b.table)

    def animations_at(self,nodes,row):
        """(entity animation or None for its rest pose, Zippo animation or
        None, Zippo shown?) after rows 0..row. Bit 0x8000 / bit 1 commands
        animate Zippo (src_CurrentScriptActor); on a conversation page the
        script runs on Zippo, so every animation is his."""
        entity=None;zippo=None;talking=self.is_conversation();self._blend={'zippo':4,'self':4}
        for n,c in enumerate(nodes[:row+1]):
            o=c.operands;op=c.opcode
            if op in (9,13):talking=True      # a conversation is under way
            if op in (31,59) and o[0]&0x8000:talking=True
            target=None;anim=None
            if op==30:target,anim=('zippo' if o[0]&1 else 'self'),o[1]
            elif op==1:
                target=('zippo' if o[0]&0x8000 else 'self')
                # The gesture shows while its line is up, then rest (unless 0x4000).
                anim=o[2] if n==row or o[0]&0x4000 else -1
            elif op in (57,58) and not (op==58 and o[0]&0x7fff==2):target,anim=('zippo' if o[0]&0x8000 else 'self'),o[1]
            if target is None:continue
            if self.is_conversation():target='zippo'
            if target=='zippo':talking=True      # a row that moves or speaks for Zippo brings him on
            value=None if anim is None or anim<0 else anim
            self._blend[target]=o[2] if op==30 else 4      # ticks it eases in (op 30 gives them)
            if target=='zippo':zippo=value
            else:entity=value
        return entity,zippo,talking

    def simulate_actors(self,nodes,row,yaw_row=None):
        """Loop the entity's animation of the moment in the viewport, and
        stand Zippo by Tomba while a conversation runs."""
        before=self.animations_at(nodes,row-1) if row>0 else (None,None,False)
        entity,zippo,talking=self.animations_at(nodes,row)
        blend=dict(self._blend)
        # Ease out of the animation on screen - whichever way the timeline was stepped.
        shown=getattr(self,'_on_screen',None) or {}
        came_from={'self':shown.get('self',before[0]),'zippo':shown.get('zippo',before[1])}
        if self.is_conversation():entity=None
        self._on_screen={'self':entity,'zippo':zippo}
        if entity is None:self.viewport.stop_entity_animation()
        else:self._with_frames('self',entity,lambda r:self.viewport.show_entity_animation(r[0],r[1]),
                               previous=came_from['self'],blend=blend['self'])
        if not talking:self.viewport.hide_zippo();return
        tomba,tomba_yaw,_anim,_how=self.tomba_state(nodes,row)
        if tomba is None:self.viewport.hide_zippo();return
        flags=0;offset=None
        for c in nodes[:row+1]:
            if c.opcode==13:
                flags=c.operands[2]
                if flags&4 and c.extended:offset=tuple(c.operands[3:6])
        from game.event_preview import zippo_position
        place=zippo_position(tomba,tomba_yaw,flags,offset)
        facing=yaw_toward(place,tomba)
        if self.state_at(nodes,row)[0] is not None:
            # The same Zippo position the turns were worked out against, so "face each other" lines up.
            yaws,positions=self.yaws_at(nodes,row if yaw_row is None else yaw_row)
            facing=yaws['zippo']
            if positions.get('zippo') is not None:place=positions['zippo']
        self._with_frames('zippo',zippo or 0,lambda r:self.viewport.show_zippo(place,facing,r[0],r[1],r[2],r[3]),
                          previous=came_from['zippo'],blend=blend['zippo'])

    def zippo_animation_of(self,c):
        """The animation a command plays on Zippo, or None if it plays on
        the entity (or none). Conversation pages run on Zippo."""
        o=c.operands;talking=self.is_conversation()
        if c.opcode==30 and (o[0]&1 or talking):return o[1] if o[1]>=0 else None
        if c.opcode==1 and (o[0]&0x8000 or talking):return o[2] if o[2]>=0 else None
        if c.opcode in (57,58) and (o[0]&0x8000 or talking) and not (c.opcode==58 and o[0]&0x7fff==2):
            return o[1] if o[1]>=0 else None
        return None

    def _with_frames(self,who,animation,show,viewport=True,previous=None,blend=4):
        """Run `show((frames, loop))` with the frames, decoding them first
        (in the background, once per animation and the one it follows)."""
        cache=self._zippo_frames if who=='zippo' else self._entity_frames
        key=(animation,previous if previous!=animation else None,blend)
        if key[1] is None:key=(animation,None,0)
        if key in cache:
            if cache[key][0]:show(cache[key])
            return
        animations=self.bindings[self.active].animations if self.active is not None else None
        job=FramesDecode(self.panel.scene,self.instance,animation,who,animations,self,previous=key[1],blend=key[2])
        job.who,job.animation,job.key,job.show,job.row=who,animation,key,show,(self.active,self.selected)
        job.viewport=viewport
        self.frames_jobs.append(job);job.decoded.connect(self._frames_decoded);job.start()

    def _frames_decoded(self,result):
        job=self.sender()
        if job in self.frames_jobs:self.frames_jobs.remove(job)
        if job is None:return
        frames,loop,error,origin,yaw=result
        entry=(frames,loop,origin if origin is not None else (0.0,0.0,0.0),yaw or 0)
        (self._zippo_frames if job.who=='zippo' else self._entity_frames)[job.key]=entry
        if error:self.status.setText(f'{"Zippo" if job.who=="zippo" else "Animation"} not simulated: {error}')
        if frames and job.row==(self.active,self.selected) and (self.timeline_box.isChecked() or not job.viewport):job.show(entry)

    def run_camera(self,row):
        """The game's own camera code over rows 0..row (background)."""
        nodes=self.drafts.get(self.active,())
        tomba,_=self.tomba_position(nodes,row)
        if tomba is None or getattr(self.viewport.viewer(),'camera_controls',None) is None:return
        scene=self.panel.scene
        if not hasattr(scene,'chunk_index'):return
        job=CameraDecode(scene,nodes,row,tomba,self);job.key=(self.active,row,tuple(c.raw for c in nodes[:row+1]))
        job.animate=nodes[row].opcode in CAMERA_OPS
        self.camera_job=job;job.decoded.connect(self._camera_decoded);job.start()

    def _camera_decoded(self,result):
        job=self.camera_job
        if job is None or not self.isVisible():return
        nodes=self.drafts.get(self.active,())
        if job.key!=(self.active,self.selected,tuple(c.raw for c in nodes[:(self.selected or 0)+1])):return
        path,note=result
        if not path:self.status.setText(f'Camera simulation failed: {note}');return
        self.viewport.show_camera(path,job.animate)
        if job.animate:self.status.setText('Previewing the camera: '+note+' '+self.tomba_position(nodes,self.selected)[1])

    def play_selected(self,manual=False):
        """Preview the selected action - once."""
        if self.selected is None:return
        self.store_operands()
        nodes=self.drafts[self.active];c=nodes[self.selected]
        if self.timeline_box.isChecked():self.apply_timeline(self.selected);return
        tomba,_=self.tomba_position(nodes,self.selected)
        if c.opcode in CAMERA_OPS:self.run_camera(self.selected);return
        if self.viewport.play(nodes,self.selected,tomba):
            if c.opcode==58:self.show_tomba(nodes,self.selected);self.preview_model_action()
            return
        if c.opcode in TOMBA_OPS|TOMBA_PLACE_OPS:
            how=self.show_tomba(nodes,self.selected)
            if how:self.status.setText('Tomba in the viewport (pink): '+how)
        if c.opcode==55:self.preview_tomba_animation();return
        if c.opcode==11:
            if c.operands[0]:self.play_music()
            else:self.player.stop();self.status.setText('Conversation music ends here; the area track resumes.')
        elif c.opcode in ANIMATION_OPS:self.preview_model_action()
        elif c.opcode==46:self.play_sound()
        elif c.opcode==47 or (c.opcode in (0,1) and self.play_voice.isEnabled()):
            if getattr(self.panel.window(),'current_iso_path',None) or manual:self.decode_voice()

    def split_presentation(self):
        if self.selected is None:return
        row=self.selected
        self._edit(lambda:script.split_presentation(self.drafts[self.active],row),row)
        if self.drafts[self.active][row].opcode==30:
            self.status.setText('Separated into Play animation, Show text and back-to-rest. In game the text may now appear one tick earlier.')

    def _snd(self):
        """TOMBA2.SND: edited if the SFX tab has it, else beside TOMBA2.DAT
        (an opened game folder), else out of the attached disc."""
        window=self.panel.window();store=getattr(window,'snd_edits',None)
        if store and store.loaded():return store.rebuild()
        beside=Path(self.panel.scene.dat_path).with_name('TOMBA2.SND')
        if beside.is_file():return beside.read_bytes()
        image=getattr(window,'current_iso_path',None)
        if not image:raise ValueError('TOMBA2.SND was not found; attach the source disc to preview audio.')
        from formats.audio.voice import extract_file
        return extract_file(image,'TOMBA2.SND')

    def split_page(self):
        nodes=self.drafts.get(self.active)
        if not nodes:return
        self.store_operands();self.remember()
        done=skipped=0;row=0
        while row<len(nodes):
            if nodes[row].opcode==1:
                try:
                    row+=len(script.split_presentation(nodes,row));done+=1;continue
                except ValueError:skipped+=1
            row+=1
        if not done:self._undo.pop();self._undo_state()
        keep=self.selected or 0;self.selected=None;self.populate(keep)
        self.status.setText(f'Separated {done} text + gesture action(s)'+(f'; {skipped} kept together (branch form, mid-line rest or another actor).' if skipped else '.'))

    def play_music(self):
        if self.selected is None:return
        self.store_operands();c=self.drafts[self.active][self.selected]
        if c.opcode!=11 or not c.operands[0]:return
        if getattr(self,'music_job',None) and self.music_job.isRunning():return
        try:
            snd=self._snd()
        except Exception as exc:
            self.status.setText(str(exc));return
        self.status.setText('Rendering the music cue…');self.music_button.setEnabled(False)
        job=MusicDecode(snd,Path(self.panel.scene.overlay_path).stem,self.overlay,c.operands[1],self)
        job.key=(self.active,self.selected)
        self.music_job=job;job.decoded.connect(self._music_decoded);job.start()

    def _music_decoded(self,result):
        wav,note=result
        self.music_button.setEnabled(True);self.status.setText(note)
        if wav is None or getattr(self.music_job,'key',None)!=(self.active,self.selected):return
        self.audio.setVolume(1.)
        self.voice_buffer=QBuffer(self);self.voice_buffer.setData(QByteArray(wav))
        self.voice_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self.voice_buffer);self.player.play()

    def stop_preview(self):
        self.viewport.reset()

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
            # Display only: the Show Text window (double-click) edits it.
            self.text_preview.setPlainText(self.linked_entry['text']);self.text_preview.setReadOnly(True)
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
            snd=self._snd()
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
            self.preview_tabs.setCurrentWidget(self.both_tab)
            self.show_model_parts(None);return
        self.store_operands();command=self.drafts[self.active][self.selected]
        self.preview_tabs.setCurrentWidget(self.both_tab)
        if command.opcode not in ANIMATION_OPS:
            self.show_model_parts(None);return
        zippo=self.zippo_animation_of(command)
        if zippo is not None:
            # Zippo's animation, on Zippo's own model (area file 16).
            self.model_note.setText(f'Initialising Zippo’s animation {zippo}…')
            note=f'Zippo animation {zippo} (area file 16, conversation table 0x8001B860)'
            self._with_frames('zippo',zippo,lambda r:self.animation_ready((r[0],f'{note}: {len(r[0])} ticks at 30 Hz',r[1])),
                              viewport=False)
            return
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
        frames,note=result[:2]
        if frames and key is not None:
            self._animation_cache=getattr(self,'_animation_cache',{});self._animation_cache[key]=result
        nodes=self.drafts.get(self.active,())
        current=nodes[self.selected] if self.selected is not None and self.selected<len(nodes) else None
        if current is not None and current.opcode in ANIMATION_OPS and self._animation_key_of(current)==key:
            self.animation_ready(result)

    def animation_ready(self,result,generation=None):
        self.preview_model_button.setEnabled(True)
        if not self.isVisible():return
        frames,note,loop=(tuple(result)+(None,))[:3];self.model_note.setText(note)
        if not frames:return
        self.animation_frames=frames;self.animation_tick=0;self.animation_loop=loop;self.animation_kind='actor'
        self.show_model_parts(frames[0],frame=True);self.animation_timer.start()

    def preview_tomba_animation(self):
        """The command's Tomba animation, on Tomba's model."""
        if self.selected is None:return
        self.store_operands();c=self.drafts[self.active][self.selected]
        if c.opcode not in TOMBA_OPS:return
        self.preview_tabs.setCurrentWidget(self.both_tab)
        animation=c.operands[1] if c.opcode==55 else c.operands[2]
        if animation<0:self.model_note.setText('Tomba animation is chosen at runtime.');return
        cached=self._tomba_cache.get(animation)
        if cached is not None:self.tomba_ready(cached);return
        if self.tomba_job and self.tomba_job.isRunning():return
        self.tomba_button.setEnabled(False);self.model_note.setText('Posing Tomba…')
        job=TombaDecode(self.panel.scene,animation,self);job.animation=animation
        self.tomba_job=job;job.decoded.connect(self._tomba_decoded);job.start()

    def _tomba_decoded(self,result):
        job=self.tomba_job;self.tomba_button.setEnabled(True)
        if not self.isVisible():return
        if isinstance(result,str):self.model_note.setText(result);return
        self._tomba_cache[job.animation]=result
        c=self.drafts[self.active][self.selected] if self.selected is not None else None
        if c is not None and c.opcode in TOMBA_OPS and job.animation==(c.operands[1] if c.opcode==55 else c.operands[2]):
            self.tomba_ready(result)

    def tomba_ready(self,result):
        self.animation_timer.stop()
        viewer=self._model_view()
        viewer.model_data=result['model'];viewer.hidden_groups=set(result['hidden'])
        viewer.pose_first_group=0;viewer.pose_spares=dict(result['spares'])
        viewer.pose=None;viewer.prepare_buffers()
        viewer.set_pose(result['frames'][0],result['pivots']);viewer.frame_model()
        self.sprite_preview.clear();self.model_note.setText(result['note'])
        self.animation_frames=result['frames'];self.animation_tick=0;self.animation_loop=result['loop']
        self.animation_kind='tomba';self._tomba_pivots=result['pivots']
        self.animation_timer.start()

    def _model_view(self):
        if not self._vram_set:
            self.model_viewer.set_vram(self.panel.viewer.vram_raw_bytes,self.panel.viewer.vram_qimage)
            self._vram_set=True
        return self.model_viewer

    def advance_animation(self):
        if not self.animation_frames:return
        # Once, like every other preview - unless Autoplay is on and the
        # clip loops, when it plays on from its loop point as in game.
        if self.animation_tick+1>=len(self.animation_frames):
            if self.autoplay.isChecked() and self.animation_loop is not None:self.animation_tick=self.animation_loop
            else:self.animation_timer.stop();return
        else:self.animation_tick+=1
        frame=self.animation_frames[self.animation_tick]
        if self.animation_kind=='tomba':self.model_viewer.set_pose(frame,self._tomba_pivots)
        else:self.show_model_parts(frame,frame=False)

    def _entity_model(self,parts,face_front=False):
        """The entity alone as a model (posed by `parts` if given), or None.
        `face_front` turns it to game yaw 0 whatever way it was facing."""
        from gui.level.level_scene import LevelScene
        from game.actor_sim import Posed
        from copy import copy
        import numpy as np
        selected=copy(self.instance)
        selected.follow=None;selected.flip=None;selected.flip_frames=()
        if parts is not None:
            from game.actor_assembly import degrees_to_units
            posed=Posed(self.instance.label,'Native animation preview',parts,[],np.array(self.instance.game_position),degrees_to_units(self.instance.angle))
            selected.assembly=posed;selected.sources=posed.sources
        if face_front:
            found=baked_facing(getattr(selected,'assembly',None))
            if found is not None:selected.angle=((found[0]-found[1])&0xfff)*360/4096
        selected.index=0
        try:
            scene=LevelScene();scene.__dict__.update(self.panel.scene.__dict__)
            scene.instances=[selected];scene.__dict__.pop('_built_model',None)
            model=scene.build();model['vertices']=scene.positions(model).tolist()
        except Exception:return None
        return model if len(model['vertices']) else None

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
        # Seen from the front: the facing the game baked in is undone, once
        # per clip (from its first frame) so a turning root does not jitter.
        found=baked_facing(getattr(selected,'assembly',None))
        if frame or getattr(self,'_model_face',None) is None:
            self._model_face=None if found is None else (found[0]-found[1])&0xfff
        if self._model_face is not None and found is not None:selected.angle=self._model_face*360/4096
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
        self._model_view()
        if frame:self.animation_kind='actor'
        self.model_viewer.pose=None;self.model_viewer.pose_spares={}
        self.model_viewer.model_data=model;self.model_viewer.hidden_groups=set()
        self.model_viewer.prepare_buffers()
        if frame:self.model_viewer.frame_model(heading=self.AVATAR_HEADING)
        self.model_viewer.update()

    def done(self,result):
        self.viewport.reset()
        self.preview_generation+=1
        self.animation_timer.stop();self.player.stop();super().done(result)

    def has_changes(self):
        self.store_operands()
        return (any(nodes!=self.originals.get(page) for page,nodes in self.drafts.items())
                or (self.bank is not None and self.bank!=getattr(self,'bank_original',self.bank)))

    def ok(self):
        if self.save.isEnabled():self.apply(close=True)
        elif not self.has_changes():self.accept()
        else:self.status.setText('Tick “Apply shared script binding…” to write these edits, or Cancel to drop them.')

    def apply(self,close=True):
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
        if close:self.accept();return
        self.reload_pages(data)

    def reload_pages(self,data):
        """After Apply: read the pages back from what was written, so the
        editor shows (and later writes against) the new area file."""
        self.viewport.stop()
        self.overlay=data
        self.__dict__.pop('_flow',None);self.__dict__.pop('_conditions',None)
        self.__dict__.pop('_selectors',None);self.__dict__.pop('_messages',None)
        if self.bank is not None:self.bank_original=deepcopy(self.bank)
        page=self.active or 0
        try:
            bindings=script.discover(self.exe,self.overlay,self.native_handler) if self.native_handler else []
            bindings+=self._conversation_bindings(bindings)
        except (ValueError,IndexError) as exc:
            self.status.setText(f'Applied, but the pages could not be read back ({exc}). Reopen Events.');return
        self.bindings=bindings;self.drafts.clear();self.originals.clear()
        self._undo.clear();self._redo.clear();self._undo_state()
        self.active=None;self.selected=None
        self.pages.blockSignals(True)
        while self.pages.count():self.pages.removeTab(0)
        for n,binding in enumerate(bindings):self._add_page_tab(n,binding)
        if not bindings:self.pages.addTab('Handler')
        page=min(page,self.pages.count()-1);self.pages.setCurrentIndex(page)
        self.pages.blockSignals(False)
        self._apply_page_filter(initial=True)
        page=self.pages.currentIndex() if self.pages.isTabVisible(page) is False else page
        self.page_changed(page)
        self.status.setText('Applied. The pages show what was written.')
